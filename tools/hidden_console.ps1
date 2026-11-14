#Requires -Version 7.0
# Start a console worker hidden at creation so nested CLI tools inherit it.
function Initialize-JarvisHiddenConsole {
    if (-not ('JarvisHiddenConsole' -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class JarvisHiddenConsole {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct StartupInfo {
        public int cb; public string reserved; public string desktop; public string title;
        public int x,y,xSize,ySize,xChars,yChars,fill,flags;
        public short show,reservedSize; public IntPtr reservedData,input,output,error;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct ProcessInfo { public IntPtr process,thread; public int pid,tid; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool CreateProcessW(string application, StringBuilder command,
        IntPtr processSecurity, IntPtr threadSecurity, bool inheritHandles, uint flags,
        IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr handle);
}
"@
    }
}
function Start-JarvisHiddenConsole {
    param([string]$FilePath,[string[]]$Arguments,[string]$WorkingDirectory)
    Initialize-JarvisHiddenConsole
    $quoted = foreach ($argument in @($FilePath) + $Arguments) {
        $value = [regex]::Replace($argument, '(\\*)"', {param($m) ($m.Groups[1].Value * 2) + '\"'})
        $value = [regex]::Replace($value, '(\\+)$', {param($m) $m.Value * 2})
        '"' + $value + '"'
    }
    $startup = [JarvisHiddenConsole+StartupInfo]::new()
    $startup.cb = [Runtime.InteropServices.Marshal]::SizeOf($startup)
    $startup.flags = 1 # STARTF_USESHOWWINDOW
    $startup.show = 0 # SW_HIDE, applied before a console is displayed
    $process = [JarvisHiddenConsole+ProcessInfo]::new()
    $command = [Text.StringBuilder]::new(($quoted -join ' '))
    if (-not [JarvisHiddenConsole]::CreateProcessW($FilePath,$command,[IntPtr]::Zero,[IntPtr]::Zero,$false,0x10,[IntPtr]::Zero,$WorkingDirectory,[ref]$startup,[ref]$process)) {
        throw [ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error())
    }
    try { return $process.pid }
    finally {
        [void][JarvisHiddenConsole]::CloseHandle($process.thread)
        [void][JarvisHiddenConsole]::CloseHandle($process.process)
    }
}

function Test-JarvisHiddenConsole {
    Initialize-JarvisHiddenConsole
    $handle = [JarvisHiddenConsole]::GetConsoleWindow()
    return $handle -ne [IntPtr]::Zero -and -not [JarvisHiddenConsole]::IsWindowVisible($handle)
}

function Invoke-JarvisHiddenScript {
    param([string]$ScriptPath,[string[]]$Arguments,[string]$WorkingDirectory)
    $log = Join-Path $WorkingDirectory ('.cache/verification/hidden-' + [IO.Path]::GetFileNameWithoutExtension($ScriptPath) + '-' + [Guid]::NewGuid().ToString('N') + '.log')
    $worker = & (Join-Path $PSScriptRoot 'start_background.ps1') -FilePath (Get-Process -Id $PID).Path -Arguments (@('-NoProfile','-NonInteractive','-File',$ScriptPath) + $Arguments) -LogPath $log -WorkingDirectory $WorkingDirectory
    do {
        Start-Sleep -Milliseconds 100
        try { $result = Get-Content -LiteralPath ($log + '.result.json') -Raw | ConvertFrom-Json }
        catch { $result = $null }
        if ((-not $result -or $result.status -eq 'starting') -and -not (Get-Process -Id $worker -ErrorAction SilentlyContinue)) {
            # A reader may have overlapped the final short write; reread after exit.
            Start-Sleep -Milliseconds 100
            try { $result = Get-Content -LiteralPath ($log + '.result.json') -Raw | ConvertFrom-Json } catch { $result = $null }
            if (-not $result -or $result.status -eq 'starting') { throw "Background worker exited without a final result: $log" }
        }
    } while (-not $result -or $result.status -eq 'starting')
    if ($result.status -notin @('completed','failed') -or $null -eq $result.exit_code) {
        throw "Invalid background result: $log"
    }
    Write-Host "Background log: $log"
    if ($result.exit_code -ne 0) {
        Get-Content -LiteralPath $log -Tail 15 | ForEach-Object { Write-Host $_ }
        Get-Content -LiteralPath ($log + '.err') -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    }
    return [int]$result.exit_code
}
