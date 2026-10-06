"""Exercise real yt-dlp downloads and FFmpeg conversion using generated media."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import threading
from uuid import uuid4

import pytest
import app as service


@pytest.mark.parametrize('format,quality', [('mp4', 'best'), ('mp3', '192')])
def test_real_download_and_conversion(tmp_path, monkeypatch, format, quality):
    location = service.ffmpeg_location()
    if not location:
        pytest.skip('FFmpeg is needed for real conversion checks')
    suffix = '.exe' if service.os.name == 'nt' else ''
    ffmpeg = str(Path(location) / ('ffmpeg' + suffix))
    ffprobe = str(Path(location) / ('ffprobe' + suffix))
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
        '-i', 'color=c=purple:s=320x180:r=24:d=2', '-f', 'lavfi',
        '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac',
        '-shortest', str(source / 'fixture.mp4')], check=True, capture_output=True)

    class SilentHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SilentHandler, directory=str(source)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = service.yt_dlp.YoutubeDL.extract_info
    fixture_url = f'http://127.0.0.1:{server.server_port}/fixture.mp4'

    def fixture_extract(self, url, *args, **kwargs):
        if url.startswith('https://www.youtube.com/'):
            url = fixture_url
        info = original(self, url, *args, **kwargs)
        # A direct media fixture has no webpage duration; our generated clip is two seconds.
        info['duration'] = 2
        return info

    monkeypatch.setattr(service.yt_dlp.YoutubeDL, 'extract_info', fixture_extract)
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    monkeypatch.setattr(service, 'DOWNLOADS', downloads)
    job = service.Job(id=uuid4().hex)
    try:
        service.run_download(job, service.DownloadRequest(
            url='https://youtube.com/watch?v=BaW_jenozKc', format=format, quality=quality))
        assert job.status == 'ready', job.message
        assert job.path.suffix == '.' + format
        assert job.path.stat().st_size > 1000
        probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries',
            'stream=codec_name', '-of', 'csv=p=0', str(job.path)], capture_output=True, text=True, check=True)
        assert ('mp3' if format == 'mp3' else 'h264') in probe.stdout
        if format == 'mp4': assert 'aac' in probe.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
