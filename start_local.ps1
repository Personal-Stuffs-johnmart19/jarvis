$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $localPython)) { throw 'Run setup_local.ps1 first.' }
    & $localPython -X utf8 (Join-Path $PSScriptRoot 'start_local.py') @args
    $localExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $localExitCode
