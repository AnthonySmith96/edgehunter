$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force -Path $statePath | Out-Null
Set-Content -LiteralPath (Join-Path $statePath 'btc_jev.stop') -Value 'STOP_REQUESTED'
Write-Output 'Parada de JEV solicitada. El proceso finalizara al cerrar el ciclo actual.'
