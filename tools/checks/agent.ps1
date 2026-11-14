#Requires -Version 7.0
param([switch]$UI, [switch]$FullPython, [switch]$Build)
$ErrorActionPreference = 'Stop'
$taskRoot = (Split-Path (Split-Path $PSScriptRoot))
. (Join-Path $taskRoot 'tools/hidden_console.ps1')
if ($IsWindows -and -not (Test-JarvisHiddenConsole)) {
    $switches = @($PSBoundParameters.Keys | Where-Object { [bool]$PSBoundParameters[$_] } | ForEach-Object { '-' + $_ })
    exit (Invoke-JarvisHiddenScript -ScriptPath $PSCommandPath -Arguments $switches -WorkingDirectory $taskRoot)
}
. (Join-Path $taskRoot 'tools/use_node.ps1')
Set-Location -LiteralPath $taskRoot
$runLabel = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$reportDir = Join-Path $taskRoot ('.cache/verification/' + $runLabel)
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodePath = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' }
if (-not (Test-Path -LiteralPath $nodePath)) { throw 'Node.js is required for agent checks.' }
if ([version](& $nodePath -p 'process.versions.node') -lt [version]'22.18.0') { throw 'Node.js 22.18+ is required to run TypeScript contracts.' }
$env:PATH = (Split-Path $nodePath) + ';' + $env:PATH
function Check-Stage([string]$Name, [scriptblock]$Action) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $logPath = Join-Path $reportDir ($Name + '.log')
    & $Action *> $logPath
    if ($LASTEXITCODE -ne 0) {
        Get-Content -LiteralPath $logPath -Tail 20 | ForEach-Object { if ($_.Length -gt 400) { $_.Substring(0,400) + "… [see full log]" } else { $_ } }
        throw "$Name failed; see $logPath"
    }
    Write-Output ("{0}: passed ({1:N1}s)" -f $Name, $watch.Elapsed.TotalSeconds)
}
$tests = @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'tests/agent') -Filter 'test_*.mjs' | Sort-Object Name | Select-Object -ExpandProperty FullName)
if ($tests.Count -eq 0) { throw 'No agent regression tests found in tests/agent' }
Check-Stage 'agent-unit' { & $nodePath --test @tests }
Check-Stage 'typescript' { & $nodePath "$taskRoot/apps/desktop/node_modules/typescript/bin/tsc" --noEmit -p "$taskRoot/apps/desktop/tsconfig.json" }
$env:PYTHONPATH = "$taskRoot/.venv/Lib/site-packages;$taskRoot"
$env:JARVIS_QA_ALLOW_CLOUD = '0'
$qaPython = Join-Path $taskRoot '.cache/qa-tools/Scripts/python.exe'
if ($FullPython) { Check-Stage 'python' { & $qaPython -X utf8 -m pytest -q } }
if ($Build) {
    $env:RUSTUP_TOOLCHAIN = 'stable'
    Check-Stage 'desktop-build' { & "$taskRoot/build_desktop.ps1" }
} elseif ($UI) {
    Check-Stage 'frontend-build' {
        Push-Location "$taskRoot/apps/desktop"
        try {
            $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
            if ($pnpm) { & $pnpm.Source run build }
            else { & $nodePath (Join-Path (Split-Path $nodePath) '../node_modules/pnpm/bin/pnpm.cjs') run build }
        } finally { Pop-Location }
    }
}
if ($UI) {
    if ($Build) { Check-Stage 'desktop-ui' { & $qaPython -X utf8 tests/desktop/qa_services.py --bundled-assets } }
    else { Check-Stage 'desktop-ui' { & $qaPython -X utf8 tests/desktop/qa_services.py } }
}
Write-Output "Logs: $reportDir. Default checks make no live model calls; explicit opt-ins may."
