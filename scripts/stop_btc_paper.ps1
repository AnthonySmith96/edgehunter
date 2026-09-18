$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $root '.local\btc_paper.pid'

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'BTC paper ya está detenido.'
    exit 0
}

$paperPid = [int](Get-Content -LiteralPath $pidPath)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$paperPid" -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidPath
    Write-Output 'BTC paper ya estaba detenido.'
    exit 0
}
if ($process.CommandLine -notlike '*run_btc_paper.py*') {
    throw "El PID $paperPid pertenece a otro proceso; no se detuvo."
}

$allProcesses = @(Get-CimInstance Win32_Process)
$descendants = @()
$frontier = @($paperPid)
while ($frontier.Count -gt 0) {
    $parents = $frontier
    $frontier = @($allProcesses | Where-Object { $_.ParentProcessId -in $parents } |
        Select-Object -ExpandProperty ProcessId)
    $descendants += $frontier
}
foreach ($childPid in ($descendants | Select-Object -Unique | Sort-Object -Descending)) {
    Stop-Process -Id $childPid -ErrorAction SilentlyContinue
}
Stop-Process -Id $paperPid -ErrorAction SilentlyContinue
Wait-Process -Id $paperPid -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidPath
Write-Output 'BTC paper detenido.'
