param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$env:MET_OFFICE_API_KEY = [Environment]::GetEnvironmentVariable('MET_OFFICE_API_KEY', 'User')
if (-not $env:MET_OFFICE_API_KEY) { throw 'MET_OFFICE_API_KEY is not set in the Windows user environment' }
$log = Join-Path $project 'data\live\metoffice_national_bootstrap.log'
& $Python (Join-Path $PSScriptRoot 'collect_met_all.py') *>> $log
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$statePath = Join-Path $project 'data\live\metoffice_national_bootstrap.json'
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.last_result.complete) {
    Unregister-ScheduledTask -TaskName 'LondonAirWatchMetBootstrap' -Confirm:$false
}
