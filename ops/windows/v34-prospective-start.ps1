param([int]$Mt5ServerUtcOffsetSeconds = 10800)
$env:TRAINING_ALLOWED = 'false'
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $PSScriptRoot 'v3-research-safety.ps1')
$env:PYTHONPATH = Join-Path $repoRoot 'backend'
$env:V3_MT5_SERVER_UTC_OFFSET_SECONDS = [string]$Mt5ServerUtcOffsetSeconds
$env:V3_COLLECTOR_CATCH_UP = 'false'
$env:V3_COLLECTOR_ONCE = 'false'
$env:V3_OUTCOME_RESOLVER_ONCE = 'false'
$taskPython = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
Write-Host ('PYTHON: ' + $taskPython)
$taskCampaign = Join-Path $repoRoot 'runtime\v34_prospective_validation_r1'
$taskExisting = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(.exe)?$' -and
    $_.CommandLine -match 'v3_prospective_collector|v34_recertify|v3_outcome_resolver'
})
if ($taskExisting.Count -gt 0) { throw 'WORKER_ALREADY_PRESENT' }
foreach ($taskLock in @('v3_prospective_collector.lock','v3_outcome_resolver.lock')) {
    if (Test-Path -LiteralPath (Join-Path $repoRoot ('runtime\'+$taskLock))) { throw 'LOCK_REQUIRES_OWNERSHIP_AUDIT' }
}
# Initialization is idempotent and refuses a changed manifest/contract. It never moves the cutoff.
& $taskPython -B (Join-Path $repoRoot 'backend\scripts\v34_prospective_campaign.py') init
if ($LASTEXITCODE -ne 0) { throw 'CAMPAIGN_INTEGRITY_FAILED' }
& $taskPython -B (Join-Path $repoRoot 'backend\scripts\v34_prospective_campaign.py') preflight
if ($LASTEXITCODE -ne 0) { throw 'MT5_PREFLIGHT_FAILED_NO_SERVICES_STARTED' }
foreach ($taskKey in @('V3_FRED_API_KEY','V3_BLS_API_KEY','V3_BEA_API_KEY')) {
    $taskValue = [Environment]::GetEnvironmentVariable($taskKey,'User')
    if (-not [string]::IsNullOrWhiteSpace($taskValue)) { Set-Item -LiteralPath ('Env:'+$taskKey) -Value $taskValue }
}
$taskCollectorScript = Join-Path $repoRoot 'backend\scripts\v3_prospective_collector.py'
$taskResolverScript = Join-Path $repoRoot 'backend\scripts\v34_recertify.py'
# Mandatory once smoke before continuous launch; never bypass the fresh preflight.
$taskBefore = (Get-Content (Join-Path $repoRoot 'runtime\v3_prospective_collector_state.json') -Raw | ConvertFrom-Json).observations_count
$env:V3_COLLECTOR_ONCE = 'true'
& $taskPython -B $taskCollectorScript
if ($LASTEXITCODE -ne 0) { throw 'ONCE_SMOKE_FAILED' }
$taskSmoke = Get-Content (Join-Path $repoRoot 'runtime\v3_prospective_collector_state.json') -Raw | ConvertFrom-Json
if ($taskSmoke.observations_count -le $taskBefore) { throw 'ONCE_SMOKE_NO_NEW_OBSERVATION_WAIT_NEXT_COMPLETED_M1' }
if ($taskSmoke.status -ne 'STOPPED' -or $taskSmoke.last_error) { throw 'ONCE_SMOKE_STATE_FAILED' }
$env:V3_COLLECTOR_ONCE = 'false'
$taskStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')
$taskCollector = Start-Process -FilePath $taskPython -ArgumentList @('-B',('-u "'+$taskCollectorScript+'"')) -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskCampaign ('collector.'+$taskStamp+'.stdout.log')) -RedirectStandardError (Join-Path $taskCampaign ('collector.'+$taskStamp+'.stderr.log'))
$taskResolver = Start-Process -FilePath $taskPython -ArgumentList @('-B',('-u "'+$taskResolverScript+'"'),'serve') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskCampaign ('resolver.'+$taskStamp+'.stdout.log')) -RedirectStandardError (Join-Path $taskCampaign ('resolver.'+$taskStamp+'.stderr.log'))
[pscustomobject]@{ Status='LAUNCHED_NOT_YET_HEALTH_CERTIFIED'; CollectorLauncherPid=$taskCollector.Id; ResolverLauncherPid=$taskResolver.Id; FIT_MODEL=0; ORDER_SEND=0 } | ConvertTo-Json
