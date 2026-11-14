#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$FilePath,
    [string[]]$Arguments = @(),
    [Parameter(Mandatory)][string]$LogPath,
    [string]$WorkingDirectory = (Get-Location).Path,
    [switch]$Worker,
    [string]$EncodedArguments
)
$ErrorActionPreference = 'Stop'
$LogPath = [IO.Path]::GetFullPath($LogPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($LogPath)) | Out-Null
$info = [Diagnostics.ProcessStartInfo]::new()
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.WorkingDirectory = $WorkingDirectory
if (-not $Worker) {
    # A hidden console is inherited by CLI grandchildren; no-window flags do not propagate.
    $info.FileName = (Get-Process -Id $PID).Path
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject @($Arguments) -Compress)))
    foreach ($item in @('-NoProfile','-NonInteractive','-File',$PSCommandPath,'-Worker','-FilePath',$FilePath,'-LogPath',$LogPath,'-WorkingDirectory',$WorkingDirectory,'-EncodedArguments',$encoded)) { $info.ArgumentList.Add($item) }
    @{status='starting'; exit_code=$null} | ConvertTo-Json -Compress | Set-Content -LiteralPath ($LogPath + '.result.json')
    . (Join-Path $PSScriptRoot 'hidden_console.ps1')
    try {
        $workerId = if ($IsWindows) {
            Start-JarvisHiddenConsole -FilePath $info.FileName -Arguments @($info.ArgumentList) -WorkingDirectory $WorkingDirectory
        } else { ([Diagnostics.Process]::Start($info)).Id }
    }
    catch {
        @{status='failed';exit_code=1;error=$_.Exception.Message} | ConvertTo-Json -Compress | Set-Content -LiteralPath ($LogPath + '.result.json')
        throw
    }
    return $workerId
}
$Arguments = @(ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($EncodedArguments))))
$info.CreateNoWindow = $false # Inherit the worker's already-hidden console.
$info.FileName = $FilePath
foreach ($item in $Arguments) { $info.ArgumentList.Add($item) }
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$stdout = $null
$stderr = $null
try {
    $stdout = [IO.FileStream]::new($LogPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read, 1)
    $stderr = [IO.FileStream]::new($LogPath + '.err', [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read, 1)
    $process = [Diagnostics.Process]::Start($info)
    $outCopy = $process.StandardOutput.BaseStream.CopyToAsync($stdout)
    $errCopy = $process.StandardError.BaseStream.CopyToAsync($stderr)
    $process.WaitForExit()
    [Threading.Tasks.Task]::WaitAll(@($outCopy,$errCopy))
    $stdout.Flush()
    $stderr.Flush()
    @{status='completed';exit_code=$process.ExitCode; process_id=$process.Id} | ConvertTo-Json -Compress | Set-Content -LiteralPath ($LogPath + '.result.json')
    exit $process.ExitCode
} catch {
    @{status='failed';exit_code=1;error=$_.Exception.Message} | ConvertTo-Json -Compress | Set-Content -LiteralPath ($LogPath + '.result.json')
    exit 1
} finally {
    if ($stdout) { $stdout.Dispose() }
    if ($stderr) { $stderr.Dispose() }
}
