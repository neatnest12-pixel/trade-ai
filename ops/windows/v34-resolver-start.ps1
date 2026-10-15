param([int]$Mt5ServerUtcOffsetSeconds = 10800)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $PSScriptRoot 'v3-research-safety.ps1')
$env:TRAINING_ALLOWED = 'false'
$env:V3_OUTCOME_RESOLVER_ONCE = 'false'
$env:V3_MT5_SERVER_UTC_OFFSET_SECONDS = [string]$Mt5ServerUtcOffsetSeconds
$env:PYTHONPATH = Join-Path $taskRoot 'backend'
$taskWorkers = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?(.exe)?$' -and
    $_.CommandLine -match 'v34_recertify|v3_outcome_resolver'
})
if ($taskWorkers.Count -gt 0) { throw 'RESOLVER_ALREADY_PRESENT' }
if (Test-Path -LiteralPath (Join-Path $taskRoot 'runtime/v3_outcome_resolver.lock')) {
    throw 'LOCK_REQUIRES_OWNERSHIP_AUDIT'
}
$taskPython = Join-Path $taskRoot 'backend/.venv/Scripts/python.exe'
& $taskPython -B (Join-Path $taskRoot 'backend/scripts/v34_prospective_campaign.py') preflight
if ($LASTEXITCODE -ne 0) { throw 'PREFLIGHT_FAILED_NO_RESOLVER_STARTED' }
$taskStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')
$taskLogs = Join-Path $taskRoot 'runtime/v34_resolver_final_20260929'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskScript = Join-Path $taskRoot 'backend/scripts/v34_recertify.py'
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-B','-u',('"'+$taskScript+'"'),'serve') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs ($taskStamp+'.stdout.log')) -RedirectStandardError (Join-Path $taskLogs ($taskStamp+'.stderr.log'))
[pscustomobject]@{Status='LAUNCHED_NOT_YET_CERTIFIED';LauncherPid=$taskProcess.Id;Once=$env:V3_OUTCOME_RESOLVER_ONCE;FIT_MODEL=0;ORDER_SEND=0} | ConvertTo-Json
