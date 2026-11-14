#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
$root = (Split-Path (Split-Path $PSScriptRoot))
Set-Location -LiteralPath $root
if (-not $env:RUNNER_TEMP) { $env:RUNNER_TEMP = Join-Path $root '.cache' }
function Wait-BackgroundResult([string]$Log) {
  $deadline = [DateTime]::UtcNow.AddSeconds(60)
  do {
    Start-Sleep -Milliseconds 100
    try { $result = Get-Content ($Log + '.result.json') -Raw | ConvertFrom-Json } catch { $result = $null }
    if ([DateTime]::UtcNow -gt $deadline) { throw "Timed out: $Log" }
  } while (-not $result -or $result.status -eq 'starting')
  Get-Content $Log
  Get-Content ($Log + '.err') -ErrorAction SilentlyContinue
  if ($result.exit_code -ne 0) { throw "Background check failed: $Log" }
}
$venv = Join-Path $env:RUNNER_TEMP 'launcher-venv'
$createLog = Join-Path $env:RUNNER_TEMP 'launcher-create.log'
& ./tools/start_background.ps1 -FilePath (Get-Command python).Source -Arguments @('-m','venv',$venv) -LogPath $createLog
Wait-BackgroundResult $createLog
$env:PATH = (Split-Path (Get-Process -Id $PID).Path) + ';' + $env:PATH
$probeLog = Join-Path $env:RUNNER_TEMP 'launcher-probe.log'
& ./tools/start_background.ps1 -FilePath (Join-Path $venv 'Scripts/python.exe') -Arguments @('tests/desktop/check_background_launcher.py') -LogPath $probeLog
Wait-BackgroundResult $probeLog
