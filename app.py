"""Drop: a single-process, local-first YouTube downloader."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
import yt_dlp

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / 'downloads'
DOWNLOADS.mkdir(exist_ok=True)
MAX_DURATION = int(os.getenv('MAX_DURATION_SECONDS', '7200'))
MAX_BYTES = int(os.getenv('MAX_FILE_BYTES', '1073741824'))
MAX_JOBS = int(os.getenv('MAX_CONCURRENT_JOBS', '2'))
TTL = int(os.getenv('JOB_TTL_SECONDS', '1800'))
ALLOWED_ORIGINS = set(filter(None, os.getenv('CORS_ORIGINS',
    'https://norihydev.github.io,http://localhost:8000,http://127.0.0.1:8000').split(',')))
VIDEO_QUALITIES = {'best', '144', '240', '360', '480', '720', '1080', '1440', '2160', '4320'}
AUDIO_QUALITIES = {'128', '192', '256', '320'}
lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=MAX_JOBS)
info_slots = threading.BoundedSemaphore(3)
info_cache: dict[str, tuple[float, dict]] = {}
rate_limits: dict[str, list[float]] = {}
log = logging.getLogger('drop')


def canonical_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
        raise ValueError('Paste a valid YouTube video link.')
    if parsed.port not in {None, 80, 443}:
        raise ValueError('Use a standard YouTube video link.')
    host = (parsed.hostname or '').lower()
    parts = parsed.path.strip('/').split('/')
    video_id = None
    if host == 'youtu.be' and len(parts) == 1:
        video_id = parts[0]
    elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
        if parsed.path == '/watch':
            video_id = parse_qs(parsed.query).get('v', [None])[0]
        elif len(parts) == 2 and parts[0] in {'shorts', 'embed', 'live'}:
            video_id = parts[1]
    if not video_id or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('Use a YouTube video, Shorts, or youtu.be link. Playlists are not supported.')
    return 'https://www.youtube.com/watch?v=' + video_id


class VideoRequest(BaseModel):
    url: str = Field(max_length=2048)

    @field_validator('url')
    @classmethod
    def validate_url(cls, value: str) -> str:
        return canonical_url(value)


class DownloadRequest(VideoRequest):
    format: str = 'mp4'
    quality: str = 'best'

    @field_validator('format')
    @classmethod
    def validate_format(cls, value: str) -> str:
        if value not in {'mp4', 'mp3'}:
            raise ValueError('Choose MP4 or MP3.')
        return value


@dataclass
class Job:
    id: str
    status: str = 'queued'
    progress: float = 0
    message: str = 'Your download is queued.'
    title: str = ''
    path: Path | None = None
    finished: float | None = None
    created: float = field(default_factory=time.time)


jobs: dict[str, Job] = {}


def ffmpeg_location() -> str | None:
    configured = os.getenv('FFMPEG_LOCATION')
    if configured:
        location = Path(configured)
        exe = location / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg') if location.is_dir() else location
        return str(location) if exe.is_file() else None
    system = shutil.which('ffmpeg')
    if system:
        return str(Path(system).parent)
    portable = next((ROOT / '.tools' / 'ffmpeg').glob('*/bin/ffmpeg.exe'), None)
    return str(portable.parent) if portable else None


def options() -> dict:
    return {'quiet': True, 'no_warnings': True, 'noplaylist': True,
            'socket_timeout': 25, 'retries': 2, 'extractor_retries': 2,
            'js_runtimes': {'node': {}}, 'cachedir': False,
            'ffmpeg_location': ffmpeg_location()}


def public_error(error: Exception) -> str:
    text = str(error).lower()
    if 'sign in' in text or 'bot' in text:
        return 'YouTube is asking this server to sign in. Try another video or update yt-dlp.'
    if 'private' in text or 'unavailable' in text or 'removed' in text:
        return 'This video is private, unavailable, or restricted.'
    if 'too long' in text or 'duration limit' in text:
        return f'This video exceeds the {MAX_DURATION // 60}-minute limit.'
    if 'size limit' in text or 'larger than' in text:
        return 'This file exceeds the server size limit. Choose a lower quality.'
    if 'live' in text:
        return 'Live streams cannot be downloaded. Try a finished video.'
    if 'requested format' in text:
        return 'That quality is unavailable. Try another quality.'
    return 'The video could not be processed. Try another link or update yt-dlp.'


def validate_info(info: dict) -> None:
    if info.get('is_live') or info.get('live_status') in {'is_live', 'is_upcoming'}:
        raise ValueError('Live videos are not supported')
    if not info.get('duration') or info['duration'] > MAX_DURATION:
        raise ValueError('Video exceeds duration limit')


def fetch_info(url: str) -> dict:
    with lock:
        cached = info_cache.get(url)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
    if not info_slots.acquire(blocking=False):
        raise HTTPException(429, 'The server is busy. Try again in a moment.')
    try:
        with yt_dlp.YoutubeDL(options()) as downloader:
            info = downloader.extract_info(url, download=False)
        validate_info(info)
        heights = sorted({int(f['height']) for f in info.get('formats', [])
                          if f.get('height') and f.get('vcodec') != 'none'})
        result = {'url': url, 'title': info.get('title', 'YouTube video'),
                  'author': info.get('uploader', 'YouTube'), 'duration': info.get('duration'),
                  'thumbnail': f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg",
                  'qualities': [str(h) for h in heights if str(h) in VIDEO_QUALITIES],
                  'has_audio': any(f.get('acodec') not in {None, 'none'} for f in info.get('formats', []))}
        with lock:
            if len(info_cache) >= 100:
                info_cache.pop(next(iter(info_cache)))
            info_cache[url] = (time.time(), result)
        return result
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(422, public_error(error)) from None
    finally:
        info_slots.release()


def download_options(payload: DownloadRequest, folder: Path, hook) -> dict:
    opts = {**options(), 'outtmpl': str(folder / '%(title).120B [%(id)s].%(ext)s'),
            'restrictfilenames': True, 'max_filesize': MAX_BYTES, 'progress_hooks': [hook],
            'postprocessor_hooks': [hook], 'overwrites': True,
            'match_filter': lambda info, **_: 'Video exceeds duration limit' if
                (info.get('duration') or 0) > MAX_DURATION else
                ('Live videos are not supported' if info.get('is_live') else None)}
    if payload.format == 'mp3':
        opts.update(format='bestaudio/best', postprocessors=[{
            'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': payload.quality}])
    else:
        cap = '' if payload.quality == 'best' else f'[height<={payload.quality}]'
        opts.update(format=f'bv[ext=mp4]{cap}+ba[ext=m4a]/b[ext=mp4]{cap}/bv{cap}+ba/b{cap}',
                    merge_output_format='mp4', postprocessors=[{'key': 'FFmpegVideoConvertor',
                                                               'preferedformat': 'mp4'}])
    return opts


def run_download(job: Job, payload: DownloadRequest) -> None:
    folder = DOWNLOADS / job.id
    folder.mkdir()

    def hook(data: dict):
        with lock:
            if data.get('downloaded_bytes', 0) > MAX_BYTES:
                raise ValueError('Download exceeded size limit')
            if data.get('status') == 'downloading':
                total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
                job.progress = min(89, 89 * data.get('downloaded_bytes', 0) / total) if total else 0
                job.status, job.message = 'downloading', 'Downloading your media…'
            elif data.get('status') in {'finished', 'started', 'processing'}:
                job.status, job.progress, job.message = 'converting', 92, 'Preparing your ' + payload.format.upper() + ' file…'

    try:
        with lock:
            job.status, job.message = 'downloading', 'Connecting to YouTube…'
        with yt_dlp.YoutubeDL(download_options(payload, folder, hook)) as downloader:
            info = downloader.extract_info(payload.url, download=True)
        validate_info(info)
        outputs = list(folder.glob('*.' + payload.format))
        if not outputs:
            raise ValueError('No converted file was produced')
        output = outputs[0]
        if output.stat().st_size > MAX_BYTES:
            raise ValueError('Converted file exceeds size limit')
        with lock:
            job.path, job.title = output, info.get('title', 'Your download')
            job.status, job.progress, job.message = 'ready', 100, 'Your file is ready.'
    except Exception as error:
        log.warning('Download %s failed: %s', job.id, type(error).__name__)
        shutil.rmtree(folder, ignore_errors=True)
        with lock:
            job.status, job.message = 'error', public_error(error)
    finally:
        with lock:
            job.finished = time.time()


def cleanup() -> None:
    with lock:
        expired = [key for key, job in jobs.items() if job.finished and time.time() - job.finished > TTL]
        for key in expired:
            shutil.rmtree(DOWNLOADS / key, ignore_errors=True)
            del jobs[key]


@asynccontextmanager
async def lifespan(app):
    # Previous process jobs cannot be resumed. Remove only this app's UUID folders.
    for folder in DOWNLOADS.iterdir():
        if folder.is_dir() and re.fullmatch(r'[a-f0-9]{32}', folder.name):
            shutil.rmtree(folder, ignore_errors=True)

    async def janitor():
        while True:
            await asyncio.sleep(60)
            await run_in_threadpool(cleanup)

    task = asyncio.create_task(janitor())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title='Drop downloader', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=sorted(ALLOWED_ORIGINS),
                   allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])


@app.middleware('http')
async def browser_protection(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method == 'POST' and origin and origin not in ALLOWED_ORIGINS:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'This browser origin is not allowed.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


def rate_limit(request: Request) -> None:
    address = request.client.host if request.client else 'unknown'
    now = time.time()
    with lock:
        # Bound memory when the app is exposed on a public server.
        for ip in list(rate_limits):
            if not rate_limits[ip] or now - rate_limits[ip][-1] > 60:
                del rate_limits[ip]
        stamps = [stamp for stamp in rate_limits.get(address, []) if now - stamp < 60]
        if len(stamps) >= 12:
            raise HTTPException(429, 'Too many requests. Wait a minute and try again.')
        stamps.append(now)
        rate_limits[address] = stamps


@app.get('/api/health')
def health():
    return {'status': 'ok', 'ffmpeg': bool(ffmpeg_location()), 'max_duration': MAX_DURATION,
            'max_file_bytes': MAX_BYTES, 'job_ttl': TTL}


@app.post('/api/info')
async def video_info(payload: VideoRequest, request: Request):
    rate_limit(request)
    return await run_in_threadpool(fetch_info, payload.url)


@app.post('/api/download', status_code=202)
async def start_download(payload: DownloadRequest, request: Request):
    rate_limit(request)
    allowed = VIDEO_QUALITIES if payload.format == 'mp4' else AUDIO_QUALITIES
    if payload.quality not in allowed:
        raise HTTPException(422, 'Choose an available quality for this format.')
    if not ffmpeg_location():
        raise HTTPException(503, 'FFmpeg is missing. Run scripts/setup.ps1 on the download server.')
    info = await run_in_threadpool(fetch_info, payload.url)
    if payload.format == 'mp4' and payload.quality != 'best' and payload.quality not in info['qualities']:
        raise HTTPException(422, 'That quality is not available for this video.')
    if payload.format == 'mp3' and not info['has_audio']:
        raise HTTPException(422, 'This video has no downloadable audio.')
    cleanup()
    with lock:
        if sum(job.finished is None for job in jobs.values()) >= MAX_JOBS:
            raise HTTPException(429, 'The download server is busy. Try again when a download finishes.')
        if len(jobs) >= 100:
            raise HTTPException(429, 'The server has reached its file limit. Try again later.')
        job = Job(id=uuid4().hex)
        jobs[job.id] = job
        executor.submit(run_download, job, payload)
    return {'id': job.id}


def get_job(job_id: str) -> Job:
    with lock:
        job = jobs.get(job_id)
        if not job or (job.finished and time.time() - job.finished > TTL):
            raise HTTPException(404, 'This download has expired. Please create a new one.')
        return job


@app.get('/api/jobs/{job_id}')
def job_status(job_id: str):
    job = get_job(job_id)
    with lock:
        return {'id': job.id, 'status': job.status, 'progress': round(job.progress, 1),
                'message': job.message, 'title': job.title,
                'download_url': f'/api/files/{job.id}' if job.status == 'ready' else None}


@app.get('/api/files/{job_id}')
def download_file(job_id: str):
    job = get_job(job_id)
    if job.status != 'ready' or not job.path or not job.path.is_file():
        raise HTTPException(409, 'Your file is not ready yet.')
    media_type = 'audio/mpeg' if job.path.suffix == '.mp3' else 'video/mp4'
    return FileResponse(job.path, media_type=media_type, filename=job.path.name)


app.mount('/', StaticFiles(directory=ROOT / 'public', html=True), name='website')
