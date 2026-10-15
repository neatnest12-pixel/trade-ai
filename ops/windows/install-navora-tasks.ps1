# Run from an elevated PowerShell after creating a production .env file.
# Creates two independent automatic-start tasks: API and forward-paper worker.
param([string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path)
$python = Join-Path $ProjectRoot ".venv312\Scripts\python.exe"
if (!(Test-Path $python)) { throw "Python 3.12 environment not found: $python" }
$backend = Join-Path $ProjectRoot "backend"
$common = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 365)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$api = New-ScheduledTaskAction -Execute $python -Argument "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log" -WorkingDirectory $backend
$worker = New-ScheduledTaskAction -Execute $python -Argument "scripts\forward_paper.py" -WorkingDirectory $backend
Register-ScheduledTask -TaskName "NavoraQuantApi" -Action $api -Settings $common -Principal $principal -Description "Navora private paper-mode API" -Force | Out-Null
Register-ScheduledTask -TaskName "NavoraQuantPaperWorker" -Action $worker -Settings $common -Principal $principal -Description "Navora single forward-paper worker" -Force | Out-Null
Start-ScheduledTask -TaskName "NavoraQuantApi"
Start-ScheduledTask -TaskName "NavoraQuantPaperWorker"