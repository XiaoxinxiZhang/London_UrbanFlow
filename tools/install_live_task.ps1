# Register a single-user five-minute collector task. Run from PowerShell on Windows.
param(
    [string]$Python = $env:AIRWATCH_PYTHON,
    [string]$TaskName = 'LondonAirWatchLiveCollector'
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$script = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'collect_live.py')).Path
if (-not $Python) {
    $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($candidate) { $Python = $candidate.Source }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Python 3.10+ not found. Pass -Python C:\path\to\python.exe or set AIRWATCH_PYTHON.'
}
& $Python -c 'import sys; assert sys.version_info >= (3,10)' 2>$null
if ($LASTEXITCODE -ne 0) { throw 'The selected Python executable must be version 3.10 or newer.' }

$backgroundPython = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $backgroundPython -PathType Leaf)) { $backgroundPython = $Python }
$action = New-ScheduledTaskAction -Execute $backgroundPython -Argument ('"{0}" run-once' -f $script) -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description 'Collect London AirWatch source data every 5 minutes' -Force | Out-Null
Write-Host "Registered $TaskName for $user"
Write-Host "Python: $Python"
Write-Host "Project: $project"
Write-Host 'The task runs while this Windows user is signed in; missed intervals are not invented.'
