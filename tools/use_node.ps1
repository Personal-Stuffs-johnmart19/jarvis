# Prefer the project-pinned standalone Node installation; leave managed Codex files intact.
$jarvisDevProject = Split-Path $PSScriptRoot -Parent
$jarvisDevVersion = (Get-Content -LiteralPath (Join-Path $jarvisDevProject '.node-version') -Raw).Trim()
if ($jarvisDevVersion -notmatch '^\d+\.\d+\.\d+$') {throw 'Invalid project Node version'}
if ($env:LOCALAPPDATA) {
    $jarvisDevNode = Join-Path $env:LOCALAPPDATA "Programs/nodejs/node-v$jarvisDevVersion-win-x64"
    $jarvisDevPnpm = Join-Path $env:LOCALAPPDATA 'Programs/jarvis-dev-tools'
    if (Test-Path -LiteralPath (Join-Path $jarvisDevNode 'node.exe')) {
        $env:PATH = "$jarvisDevNode;$jarvisDevPnpm;" + $env:PATH
    }
}

# Avoid the analyzer's detached self-update process during development/CI.
$env:CODACY_DISABLE_UPDATE_CHECK = '1'
