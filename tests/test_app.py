import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
import app as service

URL = 'https://www.youtube.com/watch?v=BaW_jenozKc'


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(service, 'DOWNLOADS', tmp_path)
    service.jobs.clear()
    service.rate_limits.clear()
    service.info_cache.clear()
    with TestClient(service.app) as client:
        yield client
    service.jobs.clear()


@pytest.mark.parametrize('url', [
    'https://youtu.be/BaW_jenozKc?t=2',
    'https://www.youtube.com/watch?v=BaW_jenozKc&list=abc',
    'https://youtube.com/shorts/BaW_jenozKc',
    'https://m.youtube.com/watch?v=BaW_jenozKc',
])
def test_video_urls_are_canonicalized(url):
    assert service.canonical_url(url) == URL


@pytest.mark.parametrize('url', [
    'http://127.0.0.1/secrets', 'file:///etc/passwd',
    'https://youtube.com.evil.example/watch?v=BaW_jenozKc',
    'https://user:password@youtube.com/watch?v=BaW_jenozKc',
    'https://youtube.com:1234/watch?v=BaW_jenozKc',
    'https://youtube.com/playlist?list=anything',
    'https://youtube.com/watch?v=../../secrets',
])
def test_untrusted_urls_rejected(client, url):
    response = client.post('/api/info', json={'url': url})
    assert response.status_code == 422


def test_interface_and_health(client):
    assert client.get('/').status_code == 200
    assert 'Drop your link here.' in client.get('/').text
    assert client.get('/api/health').json()['status'] == 'ok'


def test_info_returns_real_resolutions(client, monkeypatch):
    class FakeDownloader:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            assert not download
            return {'id': 'BaW_jenozKc', 'title': 'Test', 'uploader': 'Creator', 'duration': 10,
                    'formats': [{'height': 720, 'vcodec': 'h264', 'acodec': 'aac'},
                                {'height': 1080, 'vcodec': 'h264', 'acodec': 'none'},
                                {'vcodec': 'none', 'acodec': 'opus'}]}
    monkeypatch.setattr(service.yt_dlp, 'YoutubeDL', FakeDownloader)
    result = client.post('/api/info', json={'url': URL})
    assert result.status_code == 200
    assert result.json()['qualities'] == ['720', '1080']
    assert result.json()['has_audio']


def test_live_and_long_videos_rejected():
    with pytest.raises(ValueError): service.validate_info({'duration': 10, 'is_live': True})
    with pytest.raises(ValueError): service.validate_info({'duration': service.MAX_DURATION + 1})


def test_download_configuration():
    mp3 = service.download_options(service.DownloadRequest(url=URL, format='mp3', quality='192'), Path('test'), lambda _: None)
    assert mp3['postprocessors'][0]['preferredcodec'] == 'mp3'
    assert mp3['postprocessors'][0]['preferredquality'] == '192'
    mp4 = service.download_options(service.DownloadRequest(url=URL, format='mp4', quality='720'), Path('test'), lambda _: None)
    assert '[height<=720]' in mp4['format']
    assert mp4['merge_output_format'] == 'mp4'


def test_invalid_quality_does_not_start_download(client):
    assert client.post('/api/download', json={'url': URL, 'format': 'mp3', 'quality': '1080'}).status_code == 422
    assert client.post('/api/download', json={'url': URL, 'format': 'exe', 'quality': 'best'}).status_code == 422


def test_download_job_and_file_response(client, monkeypatch, tmp_path):
    monkeypatch.setattr(service, 'ffmpeg_location', lambda: '/installed')
    monkeypatch.setattr(service, 'fetch_info', lambda url: {'qualities': ['720'], 'has_audio': True})
    def fake_download(job, payload):
        folder = tmp_path / job.id
        folder.mkdir()
        job.path = folder / 'test.mp3'
        job.path.write_bytes(b'test-audio')
        job.status, job.progress, job.finished = 'ready', 100, time.time()
    monkeypatch.setattr(service, 'run_download', fake_download)
    result = client.post('/api/download', json={'url': URL, 'format': 'mp3', 'quality': '192'})
    assert result.status_code == 202
    job_id = result.json()['id']
    for _ in range(100):
        state = client.get('/api/jobs/' + job_id).json()
        if state['status'] == 'ready': break
        time.sleep(.01)
    assert state['status'] == 'ready'
    response = client.get(state['download_url'])
    assert response.content == b'test-audio'
    assert response.headers['content-type'] == 'audio/mpeg'
    assert 'attachment' in response.headers['content-disposition']


def test_expired_job_is_unavailable_and_deleted(client, tmp_path):
    job_id = uuid4().hex
    folder = tmp_path / job_id
    folder.mkdir()
    service.jobs[job_id] = service.Job(id=job_id, status='ready', finished=time.time() - service.TTL - 1)
    assert client.get('/api/jobs/' + job_id).status_code == 404
    service.cleanup()
    assert not folder.exists()


def test_missing_ffmpeg_is_actionable(client, monkeypatch):
    monkeypatch.setattr(service, 'ffmpeg_location', lambda: None)
    response = client.post('/api/download', json={'url': URL})
    assert response.status_code == 503
    assert 'FFmpeg' in response.json()['detail']


def test_untrusted_browser_origin_blocked(client):
    result = client.post('/api/info', json={'url': URL}, headers={'Origin': 'https://untrusted.example'})
    assert result.status_code == 403


def test_allowed_pages_origin_preflight(client):
    result = client.options('/api/info', headers={'Origin': 'https://norihydev.github.io',
        'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})
    assert result.status_code == 200
    assert result.headers['access-control-allow-origin'] == 'https://norihydev.github.io'


def test_unknown_and_unready_files(client):
    assert client.get('/api/files/unknown').status_code == 404
    job_id = uuid4().hex
    service.jobs[job_id] = service.Job(id=job_id)
    assert client.get('/api/files/' + job_id).status_code == 409


def test_concurrency_limit(client, monkeypatch):
    monkeypatch.setattr(service, 'ffmpeg_location', lambda: '/installed')
    monkeypatch.setattr(service, 'fetch_info', lambda url: {'qualities': ['720'], 'has_audio': True})
    for _ in range(service.MAX_JOBS):
        job_id = uuid4().hex
        service.jobs[job_id] = service.Job(id=job_id)
    assert client.post('/api/download', json={'url': URL}).status_code == 429


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(service, 'fetch_info', lambda url: {'title': 'Test'})
    for _ in range(12): assert client.post('/api/info', json={'url': URL}).status_code == 200
    assert client.post('/api/info', json={'url': URL}).status_code == 429
