param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Preflight", "Once", "Continuous")]
    [string]$Mode,

    [int]$Mt5ServerUtcOffsetSeconds = 10800
)

$env:TRAINING_ALLOWED = 'false'
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot 'v3-research-safety.ps1')
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
Write-Host ('PYTHON: ' + $python)
$collector = Join-Path $repoRoot "backend\scripts\v3_prospective_collector.py"
$preflight = Join-Path $repoRoot "backend\scripts\v3_4_utc_preflight.py"
$lock = Join-Path $repoRoot "runtime\v3_prospective_collector.lock"

if (-not (Test-Path -LiteralPath $python)) {
    throw "CERTIFIED_MT5_PYTHON_NOT_FOUND"
}

$liveCollectors = @(
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match "^python(.exe)?$" -and
        $_.CommandLine -match "v3_prospective_collector\.py"
    }
)
if ($liveCollectors.Count -ne 0) {
    throw "COLLECTOR_ALREADY_RUNNING:$($liveCollectors.Count)"
}
if (Test-Path -LiteralPath $lock) {
    throw "COLLECTOR_LOCK_PRESENT_REQUIRES_SEPARATE_AUDIT"
}

$env:PYTHONPATH = Join-Path $repoRoot "backend"
$env:APP_ENV = "paper"
$env:MT5_READ_ONLY = "true"
$env:TRADING_EXECUTION_ENABLED = "false"
$env:DEMO_EXECUTION_ENABLED = "false"
$env:REAL_EXECUTION_ENABLED = "false"
$env:V3_COLLECTOR_CATCH_UP = "false"
$env:V3_MT5_SERVER_UTC_OFFSET_SECONDS = [string]$Mt5ServerUtcOffsetSeconds

foreach ($name in "V3_FRED_API_KEY", "V3_BLS_API_KEY", "V3_BEA_API_KEY") {
    $value = [Environment]::GetEnvironmentVariable($name, "User")
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "USER_ENV_CREDENTIAL_MISSING:$name"
    }
    Set-Item -LiteralPath "Env:$name" -Value $value
}

& $python $preflight
if ($LASTEXITCODE -ne 0) {
    throw "V3_4_PREFLIGHT_FAILED"
}
if ($Mode -eq "Preflight") {
    exit 0
}

$env:V3_COLLECTOR_ONCE = if ($Mode -eq "Once") { "true" } else { "false" }
if ($Mode -eq "Once") {
    & $python $collector
    exit $LASTEXITCODE
}

$process = Start-Process `
    -FilePath $python `
    -ArgumentList @($collector) `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -PassThru

[pscustomobject]@{
    Status = "STARTED"
    ProcessId = $process.Id
    Mode = $Mode
    PolicyVersion = "v3.4-complete-fx-intelligence-r1"
    PolicyHash = "c056fac9dca4cf331dc94c52ea3e46dc7e6257e9fd0009506fbf808ce795ee58"
    Mt5ServerUtcOffsetSeconds = $Mt5ServerUtcOffsetSeconds
} | ConvertTo-Json
