param([int]$Mt5ServerUtcOffsetSeconds = 10800)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $PSScriptRoot 'v3-research-safety.ps1')
$env:TRAINING_ALLOWED = 'false'
$env:V3_MT5_SERVER_UTC_OFFSET_SECONDS = [string]$Mt5ServerUtcOffsetSeconds
$env:PYTHONPATH = Join-Path $projectRoot 'backend'
$workers = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(.exe)?$' -and
    $_.CommandLine -match 'v3_prospective_collector|v34_recertify|v3_outcome_resolver'
})
if ($workers.Count -gt 0) { throw 'WORKER_PRESENT_REQUIRES_OWNERSHIP_AUDIT' }
$taskPython = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'REPOSITORY_PYTHON_MISSING' }
Write-Host ('PYTHON: ' + $taskPython)
& $taskPython -B -m scripts.v34_visualizer reopen
exit $LASTEXITCODE
