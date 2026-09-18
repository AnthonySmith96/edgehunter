$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.local'
$pidPath = Join-Path $statePath 'btc_challenger.pid'
$stopPath = Join-Path $statePath 'btc_challenger.stop'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$manifestPath = Join-Path $projectRoot 'config\btc_challenger_v5.json'
New-Item -ItemType Directory -Force -Path $statePath | Out-Null
if (-not (Test-Path -LiteralPath $manifestPath)) {
    & $pythonPath (Join-Path $projectRoot 'scripts\run_btc_challenger.py') register
    if ($LASTEXITCODE -ne 0) { throw 'No fue posible registrar el modelo congelado.' }
}
if (Test-Path -LiteralPath $pidPath) {
    $challengerPid = [int](Get-Content -LiteralPath $pidPath)
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$challengerPid" -ErrorAction SilentlyContinue
    if ($existing -and $existing.CommandLine -like '*run_btc_challenger.py*') {
        Write-Output "Paper aprendido ya activo. PID=$challengerPid"
        exit 0
    }
}
Remove-Item -LiteralPath $stopPath -ErrorAction SilentlyContinue
$process = Start-Process -FilePath $pythonPath `
    -ArgumentList @('scripts\run_btc_challenger.py', 'run', '--forever', '--interval', '1') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $statePath 'btc_challenger.stdout.log') `
    -RedirectStandardError (Join-Path $statePath 'btc_challenger.stderr.log')
Set-Content -LiteralPath $pidPath -Value $process.Id
Start-Sleep -Seconds 3
if ($process.HasExited) {
    throw "El paper no arrancó. Revise .local\btc_challenger.stderr.log"
}
Write-Output "Paper aprendido activo. PID=$($process.Id); capital ficticio 100 USD, máximo 10 por apuesta."
Write-Output "Estado: $projectRoot\reports\btc_challenger_live.json"
