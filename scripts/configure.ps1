param([switch]$NoOpen)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$examplePath = Join-Path $projectRoot '.env.example'
$envPath = Join-Path $projectRoot '.env'

if (-not (Test-Path -LiteralPath $examplePath)) {
    throw 'No se encontro .env.example.'
}
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    Write-Output 'Se creo .env desde la plantilla segura.'
} else {
    Write-Output '.env ya existe; no se modifico.'
}

Write-Output 'Pegue TYPESAFE_API_KEY y cambie TYPESAFE_BUDGET_USD=0 solo si autoriza gasto real de API.'
Write-Output 'Despues ejecute INICIAR_EDGEHUNTER.bat o INICIAR_BTC_JEV.bat.'
if (-not $NoOpen) {
    Start-Process -FilePath 'notepad.exe' -ArgumentList @($envPath)
}
