# Drop — YouTube Video Downloader

A Bootstrap website for saving YouTube videos as **MP4** or extracting audio as **MP3**, with selectable quality, video previews, and live download progress.

**[Source](https://github.com/NorihyDev/youtube-video-downloader)**

## Run locally

The website runs on a **local server on your computer**, using Python, yt-dlp, and FFmpeg. Start the server below and open **http://127.0.0.1:8000** for the complete experience. The public GitHub Pages site has been unpublished, and automatic website publishing has been removed.

### Windows

Install [Python 3.11+](https://www.python.org/downloads/) and [Node.js 22+](https://nodejs.org/), then run these commands in PowerShell:

```powershell
git clone https://github.com/NorihyDev/youtube-video-downloader.git
cd youtube-video-downloader
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Setup creates a virtual environment, installs Python packages, and downloads the Windows [FFmpeg essentials build](https://www.gyan.dev/ffmpeg/builds/) if needed. The server listens only on `127.0.0.1`. Keep its terminal open; press Ctrl+C to stop it.

### macOS / Linux

Install Python 3.11+, Node.js 22+, and FFmpeg with your package manager, then:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
```

### Docker

```sh
docker build -t drop .
docker run --rm -p 127.0.0.1:8000:8000 drop
```

## Features

- Responsive Bootstrap 5.3 interface with a cream and purple design.
- MP4 video with audio, using resolutions actually available in the source video.
- MP3 audio at 128, 192, 256, or 320 kbps. Higher bitrates do not improve source fidelity.
- Real title, creator, duration, and thumbnail previews.
- Background downloads, conversion progress, and a save-file link.
- Configurable connection address, stored in the visitor’s browser.
- Automatic temporary-file cleanup and request/concurrency limits.
- URL validation limited to individual YouTube videos and Shorts.
- GitHub Actions for backend tests.

## Configuration

Windows `scripts/start.ps1` reads an optional `.env` file. Copy `.env.example` and edit as needed. On other platforms, export the variables before launching the server.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CORS_ORIGINS` | GitHub Pages and localhost origins | Comma-separated allowed browser origins |
| `FFMPEG_LOCATION` | Auto-detected | Directory containing ffmpeg and ffprobe |
| `MAX_DURATION_SECONDS` | `7200` | Maximum video duration |
| `MAX_FILE_BYTES` | `1073741824` | Maximum output size (1 GiB) |
| `MAX_CONCURRENT_JOBS` | `2` | Maximum simultaneous downloads |
| `JOB_TTL_SECONDS` | `1800` | Completed files expire after 30 minutes |

Run **one server worker**: job state is in memory. Jobs cannot resume after a restart. Files stay on the download server until they expire or it restarts. A selected video resolution is a ceiling; yt-dlp chooses the best matching stream at or below it.

For a future hosted server, the included `Dockerfile` and `render.yaml` provide a starting point. Hosting is optional and is not currently configured. YouTube may reject requests from data-center IP addresses. Configure `public/config.js` with the deployed server origin if you host the backend; visitor connection settings take precedence.

## Checks

```sh
pip install -r requirements-dev.txt
python -m pytest -q
node --check public/app.js
```

Backend tests cover URL validation, quality selection, metadata, job completion, file delivery, expiration, concurrency, request limits, and browser origins. Conversion tests generate a short clip and exercise real yt-dlp downloads and FFmpeg processing; they skip if FFmpeg is absent. YouTube requests are mocked in automated tests.

## Limitations

Individual public videos only. No playlists, live streams, private or DRM-protected content. Availability depends on YouTube and yt-dlp; refresh yt-dlp when upstream extraction changes:

```sh
python -m pip install --upgrade "yt-dlp[default]"
```

Only download content you own or have permission to save. Drop is not affiliated with YouTube. Bootstrap is distributed under its MIT license; yt-dlp and FFmpeg retain their respective licenses.
