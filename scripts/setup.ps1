$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or later first.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
$portableFfmpeg = Get-ChildItem -Path '.tools\ffmpeg\*\bin\ffmpeg.exe' -ErrorAction SilentlyContinue
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and -not $portableFfmpeg -and -not $env:FFMPEG_LOCATION) {
    New-Item -ItemType Directory -Force -Path '.tools' | Out-Null
    Write-Output 'Downloading the FFmpeg essentials build for Windows...'
    Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '.tools\ffmpeg.zip'
    Expand-Archive -LiteralPath '.tools\ffmpeg.zip' -DestinationPath '.tools\ffmpeg' -Force
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Warning 'Install Node.js 22 or later for YouTube JavaScript challenge support: https://nodejs.org/'
}
Write-Output 'Ready. Run .\scripts\start.ps1 and open http://127.0.0.1:8000'
