param([switch]$NoOpen)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$candidates = @()
if ($env:AIRWATCH_PYTHON) { $candidates += $env:AIRWATCH_PYTHON }
$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($launcher) { $candidates += $launcher.Source }
$installRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
if (Test-Path -LiteralPath $installRoot) {
    $candidates += @(Get-ChildItem -LiteralPath $installRoot -Directory | Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' })
}
$pathPython = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pathPython) { $candidates += $pathPython.Source }
$selectedPython = $null
foreach ($candidate in $candidates | Select-Object -Unique) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
    try {
        $executable = & $candidate -c 'import sys; assert sys.version_info >= (3,10); print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $executable) {
            $selectedPython = [string]@($executable)[-1]
            break
        }
    } catch { continue }
}
if (-not $selectedPython) { throw 'Python 3.10+ not found. Set AIRWATCH_PYTHON to your python.exe path.' }
Set-Location -LiteralPath $project
$serverArgs = @((Join-Path $PSScriptRoot 'server.py'))
if (-not $NoOpen) { $serverArgs += '--open' }
& $selectedPython @serverArgs
exit $LASTEXITCODE
