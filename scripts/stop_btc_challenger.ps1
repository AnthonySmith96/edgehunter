$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force -Path $statePath | Out-Null
Set-Content -LiteralPath (Join-Path $statePath 'btc_challenger.stop') -Value 'STOP_REQUESTED'
Write-Output 'Parada solicitada. El paper terminará al cerrar el ciclo actual; revise operational_status=STOPPED.'
