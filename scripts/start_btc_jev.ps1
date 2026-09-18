$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.local'
$pidPath = Join-Path $statePath 'btc_jev.pid'
$stopPath = Join-Path $statePath 'btc_jev.stop'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'

New-Item -ItemType Directory -Force -Path $statePath | Out-Null

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Falta .venv. Ejecute scripts\bootstrap.ps1 primero.'
}

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = [int](Get-Content -LiteralPath $pidPath)
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$existingPid" -ErrorAction SilentlyContinue
    if ($existing -and $existing.CommandLine -like '*run_btc_jev.py*') {
        Write-Output "El bot JEV Mandante ya está activo. PID=$existingPid"
        exit 0
    }
}

Remove-Item -LiteralPath $stopPath -ErrorAction SilentlyContinue

$process = Start-Process -FilePath $pythonPath `
    -ArgumentList @('-u', 'scripts\run_btc_jev.py') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $statePath 'btc_jev.stdout.log') `
    -RedirectStandardError (Join-Path $statePath 'btc_jev.stderr.log')

Set-Content -LiteralPath $pidPath -Value $process.Id
Start-Sleep -Seconds 3

if ($process.HasExited) {
    throw "El bot JEV no arrancó. Revise .local\btc_jev.stderr.log"
}

Write-Output "Bot JEV Mandante activo exitosamente. PID=$($process.Id)"
Write-Output "Registro de decisiones: $projectRoot\.local\btc_jev.stdout.log"
Write-Output "Estado en vivo: $projectRoot\reports\btc_jev_live.json"
