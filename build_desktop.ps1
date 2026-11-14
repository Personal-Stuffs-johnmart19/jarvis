#Requires -Version 7.0
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'tools/hidden_console.ps1')
if ($IsWindows -and -not (Test-JarvisHiddenConsole)) {
    $switches = @($PSBoundParameters.Keys | Where-Object { [bool]$PSBoundParameters[$_] } | ForEach-Object { '-' + $_ })
    exit (Invoke-JarvisHiddenScript -ScriptPath $PSCommandPath -Arguments $switches -WorkingDirectory $PSScriptRoot)
}
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'tools/use_node.ps1')
$localCargo = Join-Path $PSScriptRoot '.cache/desktop-toolchain/cargo'
if (Test-Path (Join-Path $localCargo 'bin/cargo.exe')) {
    $env:CARGO_HOME = $localCargo
    $env:RUSTUP_HOME = Join-Path $PSScriptRoot '.cache/desktop-toolchain/rustup'
    $env:PATH = (Join-Path $localCargo 'bin') + ';' + $env:PATH
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Для сборки установите Rust MSVC и Visual Studio C++ Build Tools. Обычный запуск START.cmd их не требует.' }
$pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
$nodeCommand = Get-Command node -ErrorAction Stop
$bundledPnpm = Join-Path (Split-Path $nodeCommand.Source) '../node_modules/pnpm/bin/pnpm.cjs'
function Invoke-Pnpm {
    param([string[]]$Arguments)
    if ($pnpmCommand) { & $pnpmCommand.Source @Arguments }
    elseif (Test-Path $bundledPnpm) { & $nodeCommand.Source $bundledPnpm @Arguments }
    else { throw 'Установите pnpm 11 для сборки frontend.' }
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка pnpm' }
}
Push-Location apps/desktop
try {
    Invoke-Pnpm -Arguments @('install','--frozen-lockfile','--ignore-scripts')
    Invoke-Pnpm -Arguments @('run','build')
    if ($CheckOnly) { cargo check --manifest-path src-tauri/Cargo.toml --locked }
    else { cargo build --release --manifest-path src-tauri/Cargo.toml --locked }
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка Rust' }
    if (-not $CheckOnly) {
        $destination = Join-Path $PSScriptRoot 'desktop-bin'
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        # Версионное имя позволяет обновиться, не закрывая работающий Jarvis.
        $source = 'src-tauri/target/release/jarvis-desktop.exe'
        $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        $filename = "jarvis-desktop-$hash.exe"
        $binary = Join-Path $destination $filename
        if (-not (Test-Path -LiteralPath $binary)) {
            Copy-Item -LiteralPath $source -Destination $binary
        }
        if ((Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) { throw 'Контрольная сумма сборки не совпала.' }
        $manifest = Join-Path $destination 'manifest.json'
        $pending = Join-Path $destination 'manifest.pending.json'
        @{ version='0.1.0'; platform='windows-x86_64'; file=$filename; sha256=$hash } | ConvertTo-Json | Set-Content -LiteralPath $pending -Encoding UTF8
        if (Test-Path -LiteralPath $manifest) {
            $backup = Join-Path $destination 'manifest.previous.json'
            [System.IO.File]::Replace($pending, $manifest, $backup)
            Remove-Item -LiteralPath $backup
        }
        else { [System.IO.File]::Move($pending, $manifest) }
    }
} finally { Pop-Location }
