$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & (Join-Path $PSScriptRoot 'setup.ps1')
}
if (Test-Path -LiteralPath '.env') {
    Get-Content -LiteralPath '.env' | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"').Trim("'"), 'Process')
        }
    }
}
& '.\.venv\Scripts\python.exe' -m uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
