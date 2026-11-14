param(
    [string]$Python = '',
    [switch]$NonInteractive,
    [switch]$Launch
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Push-Location $PSScriptRoot
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Jarvis requires 64-bit Windows.' }
    New-Item -ItemType Directory -Force -Path '.cache','data' | Out-Null

    function Test-Python312([string]$Candidate) {
        if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) { return $false }
        try {
            & $Candidate -c "import sys,struct; sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 1)" 2>$null | Out-Null
            return $LASTEXITCODE -eq 0
        } catch { return $false }
    }

    $venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (-not (Test-Python312 $venvPython)) {
        $candidates = @()
        if ($Python) {
            $resolved = Get-Command $Python -ErrorAction SilentlyContinue
            $candidates += $(if ($resolved) { $resolved.Source } else { $Python })
        }
        $candidates += (Join-Path $PSScriptRoot '.runtime/Python312/python.exe')
        $candidates += (Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe')
        $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($systemPython) { $candidates += $systemPython.Source }
        $basePython = $null
        foreach ($candidate in $candidates) {
            if (Test-Python312 $candidate) { $basePython = $candidate; break }
        }
        if (-not $basePython) {
            Write-Host '[1/5] Downloading official Python 3.12.10 x64...'
            $installer = Join-Path $PSScriptRoot '.cache/python-3.12.10-amd64.exe'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $installer
            $signature = Get-AuthenticodeSignature -LiteralPath $installer
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                throw 'Python installer signature could not be verified. Installation stopped.'
            }
            $runtimeFolder = Join-Path $PSScriptRoot '.runtime/Python312'
            $installerArgs = '/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Include_pip=1 Include_tcltk=0 Include_doc=0 Shortcuts=0 TargetDir="' + $runtimeFolder + '"'
            $process = Start-Process -FilePath $installer -ArgumentList $installerArgs -Wait -PassThru -WindowStyle Hidden
            if ($process.ExitCode -notin @(0,3010)) { throw "Python installation failed: $($process.ExitCode)" }
            $basePython = Join-Path $runtimeFolder 'python.exe'
            if (-not (Test-Python312 $basePython)) { throw 'Python 3.12 is unavailable after installation.' }
        }
        & $basePython -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }

    $pipReady = $false
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $venvPython -m pip --version 2>$null | Out-Null
        $pipReady = $LASTEXITCODE -eq 0
    } finally { $ErrorActionPreference = $previousErrorPreference }
    if (-not $pipReady) {
        & $venvPython -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'Could not repair pip in the Python environment.' }
    }
    Write-Host '[2/5] Installing pinned package versions...'
    & $venvPython -m pip install --disable-pip-version-check -r requirements/full.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check the network connection.' }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Package compatibility checks failed.' }

    # A fresh Windows installation may lack the Microsoft C++ Runtime required by torch/ONNX.
    $nativeCheck = 'import torch, onnxruntime, cv2, ctranslate2; from piper import PiperVoice'
    function Test-NativePackages {
        $previousErrorPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $venvPython -X utf8 -c $nativeCheck *> '.cache/native-check.log'
            return $LASTEXITCODE -eq 0
        } finally { $ErrorActionPreference = $previousErrorPreference }
    }
    if (-not (Test-NativePackages)) {
        if (-not (Select-String -LiteralPath '.cache/native-check.log' -Pattern 'DLL|WinError 126|WinError 1114' -Quiet)) {
            throw 'Could not load native libraries. See .cache/native-check.log.'
        }
        Write-Host 'Installing Microsoft Visual C++ Runtime. Windows may request UAC approval.'
        $vcInstaller = Join-Path $PSScriptRoot '.cache/vc_redist.x64.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vcInstaller
        $vcSignature = Get-AuthenticodeSignature -LiteralPath $vcInstaller
        if ($vcSignature.Status -ne 'Valid' -or $vcSignature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
            throw 'Microsoft installer signature could not be verified.'
        }
        $vcProcess = Start-Process -FilePath $vcInstaller -ArgumentList '/install /quiet /norestart' -Verb RunAs -Wait -PassThru -WindowStyle Hidden
        if ($vcProcess.ExitCode -notin @(0,1638,3010)) { throw 'Could not install Microsoft C++ Runtime.' }
        if (-not (Test-NativePackages)) { throw 'Native libraries are still unavailable. See .cache/native-check.log.' }
    }

    & $venvPython -X utf8 -m tools.setup.capabilities
    if ($LASTEXITCODE -ne 0) { throw 'Tool and model checks failed.' }

    Write-Host '[3/5] Setting up local Russian speech recognition...'
    & $venvPython -X utf8 -m tools.setup.local_model
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the Russian speech model.' }
    & $venvPython -X utf8 -c "from local_mode.silero_voice import ensure_model; ensure_model(); print('Silero ready.')"
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Silero. Check the network connection.' }

    Write-Host 'Setting up Supertonic 3 and portable Vulkan audio.cpp (about 520 MB download)...'
    & $venvPython -X utf8 -c "from local_mode.audio_cpp_install import ensure_assets; ensure_assets(); print('Supertonic Vulkan ready.')"
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Supertonic is not installed yet. Silero CPU is available; selecting Supertonic will retry its download.' }

    & $venvPython -X utf8 -c "from local_mode.piper_voice import ensure_model; ensure_model(); print('Russian TTS ready.')"
    if ($LASTEXITCODE -ne 0) { throw 'Could not set up Russian speech synthesis.' }

    & $venvPython -X utf8 -c "from local_mode.whisper_voice import ensure_model; ensure_model(); print('Russian Whisper ready.')"
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Russian Whisper.' }

    & $venvPython -X utf8 -m tools.checks.offline_voice
    if ($LASTEXITCODE -ne 0) { throw 'Local Russian command recognition checks failed.' }

    Write-Host '[4/5] Setting up the dedicated browser profile...'
    $chromePaths = @(
        (Join-Path $env:ProgramFiles 'Google/Chrome/Application/chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google/Chrome/Application/chrome.exe')
    )
    $edgePaths = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft/Edge/Application/msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft/Edge/Application/msedge.exe')
    )
    $preferences = [pscustomobject]@{}
    if (Test-Path -LiteralPath 'data/preferences.json') {
        $preferences = Get-Content -Raw -LiteralPath 'data/preferences.json' | ConvertFrom-Json
    }
    $channel = 'chromium'
    foreach ($browserPath in $edgePaths) { if (Test-Path -LiteralPath $browserPath) { $channel = 'msedge'; break } }
    foreach ($browserPath in $chromePaths) { if (Test-Path -LiteralPath $browserPath) { $channel = 'chrome'; break } }
    $savedChannel = $preferences.browser_channel
    if ($savedChannel -eq 'chromium') { $channel = 'chromium' }
    elseif ($savedChannel -eq 'chrome' -and ($chromePaths | Where-Object { Test-Path -LiteralPath $_ })) { $channel = 'chrome' }
    elseif ($savedChannel -eq 'msedge' -and ($edgePaths | Where-Object { Test-Path -LiteralPath $_ })) { $channel = 'msedge' }
    if ($channel -eq 'chromium') {
        & $venvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the browser.' }
    }
    $preferences | Add-Member -NotePropertyName browser_channel -NotePropertyValue $channel -Force
    $preferences | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath 'data/preferences.json'

    Write-Host '[5/5] Saving the API key in the Windows DPAPI credential store...'
    & $venvPython -X utf8 -c "from local_mode.credentials import load_key; print('Gemini key configured.' if load_key() else 'No key configured.')"
    if ($LASTEXITCODE -ne 0) { throw 'Could not import the API key.' }
    & $venvPython -X utf8 -c 'from local_mode.gemini import has_key; import sys; sys.exit(0 if has_key() else 1)'
    if ($LASTEXITCODE -ne 0 -and -not $NonInteractive) {
        & $venvPython -X utf8 start_local.py --set-key
        if ($LASTEXITCODE -ne 0) { throw 'Could not configure the API key.' }
    }
    & $venvPython -X utf8 start_local.py --doctor
    if ($LASTEXITCODE -ne 0) { throw 'Environment checks failed.' }
    & $venvPython -X utf8 -c 'from local_mode.setup_state import record; record()'
    if ($LASTEXITCODE -ne 0) { throw 'Could not save the environment verification result.' }
    Set-Content -Encoding ascii -LiteralPath 'data/setup-complete.txt' -Value 'unified-v4'
    if (-not $NonInteractive) {
        $desktopFolder = [Environment]::GetFolderPath('DesktopDirectory')
        if ($desktopFolder -and (Test-Path -LiteralPath $desktopFolder)) {
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut((Join-Path $desktopFolder 'Jarvis.lnk'))
            $shortcut.TargetPath = Join-Path $PSScriptRoot '.venv/Scripts/pythonw.exe'
            $shortcut.Arguments = '-X utf8 "' + (Join-Path $PSScriptRoot 'launch_local.py') + '"'
            $shortcut.WorkingDirectory = $PSScriptRoot
            $shortcut.Description = 'Jarvis voice assistant and Gemini'
            $shortcut.IconLocation = $env:SystemRoot + '\System32\shell32.dll,44'
            $shortcut.Save()
        }
    }
    Write-Host 'READY. Run START.cmd. F8 enables Russian voice input in Jarvis.'
    if ($Launch) {
        $pythonw = Join-Path $PSScriptRoot '.venv/Scripts/pythonw.exe'
        Start-Process -FilePath $pythonw -ArgumentList @('-X','utf8','launch_local.py') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    }
} finally { Pop-Location }
