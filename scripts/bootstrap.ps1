param([switch]$Demo)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment failed' }
}
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath '.venv\Scripts\uv.exe')) {
    & $projectPython -m pip install --disable-pip-version-check uv==0.8.22
    if ($LASTEXITCODE -ne 0) { throw 'uv installation failed' }
}
& '.\.venv\Scripts\uv.exe' sync --frozen --extra dev --native-tls -q
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed' }
& $projectPython scripts\pocketbase_setup.py
if ($LASTEXITCODE -ne 0) { throw 'PocketBase installation failed' }
& $projectPython -m edgehunter doctor --redact
if ($LASTEXITCODE -ne 0) { throw 'Doctor failed' }
if ($Demo) {
    & $projectPython -m edgehunter demo
} else {
    & $projectPython -m edgehunter start --mode paper
}
if ($LASTEXITCODE -ne 0) { throw 'EdgeHunter start failed' }
