$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$state = Join-Path $root '.local'
$pidPath = Join-Path $state 'btc_paper.pid'
$stdout = Join-Path $state 'btc_paper.stdout.log'
$stderr = Join-Path $state 'btc_paper.stderr.log'
$python = Join-Path $root '.venv\Scripts\python.exe'

New-Item -ItemType Directory -Force $state | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = [int](Get-Content -LiteralPath $pidPath)
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$existingPid" -ErrorAction SilentlyContinue
    if ($existing -and $existing.CommandLine -like '*run_btc_paper.py*') {
        Write-Output "BTC paper ya está activo. PID=$existingPid"
        exit 0
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Falta .venv. Ejecute scripts\bootstrap.ps1 primero.'
}

$process = Start-Process -FilePath $python `
    -ArgumentList @('scripts\run_btc_paper.py', '--forever', '--interval', '5') `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id
Start-Sleep -Seconds 2
if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
    throw "BTC paper no arrancó. Revise $stderr"
}
Write-Output "BTC paper activo continuamente. PID=$($process.Id)"
Write-Output "Estado: $root\reports\btc_paper_live.json"
