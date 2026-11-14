#Requires -Version 7.0
param([switch]$PackageOnly, [switch]$CliOnly)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot
. (Join-Path $root 'tools/hidden_console.ps1')
if ($IsWindows -and -not (Test-JarvisHiddenConsole)) {
    $switches = @($PSBoundParameters.Keys | Where-Object { [bool]$PSBoundParameters[$_] } | ForEach-Object { '-' + $_ })
    exit (Invoke-JarvisHiddenScript -ScriptPath $PSCommandPath -Arguments $switches -WorkingDirectory $root)
}
Set-Location -LiteralPath $root
$env:RUSTUP_TOOLCHAIN='stable'
$localCargo=Join-Path $root '.cache/desktop-toolchain/cargo'
if (Test-Path (Join-Path $localCargo 'bin/cargo.exe')) {
    $env:CARGO_HOME=$localCargo
    $env:RUSTUP_HOME=Join-Path $root '.cache/desktop-toolchain/rustup'
    $env:PATH=(Join-Path $localCargo 'bin')+';'+$env:PATH
}
if (-not $PackageOnly) {
    cargo build --release --locked --manifest-path apps/desktop/src-tauri/workbench-core/crates/terax-cli/Cargo.toml --target-dir apps/desktop/src-tauri/workbench-core/target
    if ($LASTEXITCODE -ne 0) {throw 'Embedded workspace CLI build failed.'}
}
$packageArguments = @('integrations/package_companions.py')
if ($CliOnly) { $packageArguments += '--cli-only' }
& (Join-Path $root '.venv/Scripts/python.exe') @packageArguments
if ($LASTEXITCODE -ne 0) {throw 'Companion packaging failed.'}
