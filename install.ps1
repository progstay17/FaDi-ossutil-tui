<#
.SYNOPSIS
  Installs OSS Control Center (fadi-oss-tui) for the current Windows user.

.DESCRIPTION
  Creates a private Python virtual environment, installs this project into it and creates a launcher.
  Nothing is installed system-wide and no administrator rights are needed.

.PARAMETER InstallDir
  Where the app lives. Default: %LOCALAPPDATA%\FaDi-OSS-TUI (your login, queue and logs live here too).

.PARAMETER InstallOssutil
  Also download ossutil 2.4.0 from Alibaba Cloud and verify its SHA-256 before using it.

.PARAMETER AddToPath
  Add <InstallDir>\bin to your user PATH so "fadi-oss-tui" works in any terminal.

.PARAMETER NoShortcut
  Do not create the Start Menu shortcut.

.PARAMETER Python
  Path to a specific python.exe (3.11 or newer). By default the installer looks for one.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'FaDi-OSS-TUI'),
    [switch]$InstallOssutil,
    [switch]$AddToPath,
    [switch]$NoShortcut,
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$MinPython = [version]'3.11'
$OssutilVersion = '2.4.0'
$OssutilUrl = "https://gosspublic.alicdn.com/ossutil/v2/$OssutilVersion/ossutil-$OssutilVersion-windows-amd64.zip"
$OssutilSha256 = 'dd68cffb62d88e59ff3d7fde6055ac7b8e2197e2adce7e540bcd6c706a2c4a49'
$Venv = Join-Path $InstallDir 'venv'
$BinDir = Join-Path $InstallDir 'bin'
$Launcher = Join-Path $BinDir 'fadi-oss-tui.cmd'
$Marker = Join-Path $InstallDir '.fadi-oss-tui-install'
$ShortcutName = 'OSS Control Center.lnk'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Invoke-Native([scriptblock]$Block, [string]$Failure) {
    # Run a native command, show its output indented, and fail loudly on a non-zero exit code.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Block 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ } | ForEach-Object { Write-Host "    $_" }
    }
    finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -ne 0) { throw $Failure }
}

function Get-PythonVersion([string]$Exe, [string[]]$ExtraArgs) {
    try {
        $out = & $Exe @ExtraArgs -c "import sys; print('{}.{}'.format(*sys.version_info[:2]))" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$out" -match '^\d+\.\d+$') { return [version]"$out" }
    }
    catch { }
    return $null
}

function Find-Python {
    if ($Python) {
        $v = Get-PythonVersion $Python @()
        if (-not $v -or $v -lt $MinPython) { throw "The python given with -Python is missing or older than $MinPython : $Python" }
        return @{ Exe = $Python; Args = @() }
    }
    $candidates = @(
        @{ Exe = 'py'; Args = @('-3') },
        @{ Exe = 'python'; Args = @() },
        @{ Exe = 'python3'; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $v = Get-PythonVersion $c.Exe $c.Args
        if ($v -and $v -ge $MinPython) { return $c }
    }
    return $null
}

function Install-Ossutil {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'The ossutil download supports 64-bit Windows only.' }
    $zip = Join-Path ([IO.Path]::GetTempPath()) "ossutil-$OssutilVersion.zip"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('ossutil-' + [guid]::NewGuid().ToString('N'))
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $OssutilUrl -OutFile $zip -UseBasicParsing
        $hash = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLower()
        if ($hash -ne $OssutilSha256) {
            throw "ossutil download failed the SHA-256 check (got $hash). It was NOT installed."
        }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $found = Get-ChildItem -Path $tmp -Recurse -Filter ossutil.exe | Select-Object -First 1
        if (-not $found) { throw 'ossutil.exe was not found inside the downloaded archive.' }
        $target = Join-Path $InstallDir 'ossutil'
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        $dest = Join-Path $target 'ossutil.exe'
        Copy-Item -Path $found.FullName -Destination $dest -Force
        return $dest
    }
    finally {
        Remove-Item -Path $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function New-StartMenuShortcut {
    $path = Join-Path ([Environment]::GetFolderPath('Programs')) $ShortcutName
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $wt = Get-Command wt.exe -ErrorAction SilentlyContinue
    if ($wt) {
        # Windows Terminal draws the interface best.
        $lnk.TargetPath = $wt.Source
        $lnk.Arguments = 'new-tab --title "OSS Control Center" "' + $Launcher + '"'
    }
    else {
        $lnk.TargetPath = $Launcher
    }
    $lnk.WorkingDirectory = $env:USERPROFILE
    $lnk.Description = 'OSS Control Center'
    $lnk.Save()
    return $path
}

try {
    if (-not (Test-Path (Join-Path $RepoRoot 'pyproject.toml'))) {
        throw 'Run install.ps1 from the repository folder (pyproject.toml was not found next to it).'
    }

    Write-Step 'Looking for Python 3.11 or newer'
    $py = Find-Python
    if (-not $py) {
        throw ("Python $MinPython or newer was not found.`n" +
            "    Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'),`n" +
            "    or run: winget install Python.Python.3.12`n" +
            "    then run the installer again.")
    }
    Write-Host ("    using: " + $py.Exe + ' ' + ($py.Args -join ' '))

    New-Item -ItemType Directory -Force -Path $InstallDir, $BinDir | Out-Null

    Write-Step "Creating the private environment in $Venv"
    $venvPy = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path $venvPy)) {
        Invoke-Native { & $py.Exe @($py.Args) -m venv $Venv } 'Could not create the virtual environment.'
    }

    Write-Step 'Installing OSS Control Center (this downloads Textual the first time)'
    Invoke-Native { & $venvPy -m pip install --disable-pip-version-check --quiet --upgrade pip } 'pip could not be upgraded.'
    Invoke-Native { & $venvPy -m pip uninstall --yes --disable-pip-version-check fadi-oss-tui } 'pip uninstall failed.'
    Invoke-Native { & $venvPy -m pip install --disable-pip-version-check --quiet --upgrade $RepoRoot } 'The installation failed.'

    Write-Step 'Creating the launcher'
    $text = "@echo off`r`n" + '"%~dp0..\venv\Scripts\fadi-oss-tui.exe" %*' + "`r`n" + 'exit /b %errorlevel%' + "`r`n"
    [IO.File]::WriteAllText($Launcher, $text, [Text.Encoding]::ASCII)
    [IO.File]::WriteAllText($Marker, "installed by install.ps1 on $(Get-Date -Format 's')`r`n", [Text.Encoding]::ASCII)

    $ossutilPath = ''
    if ($InstallOssutil) {
        Write-Step "Downloading ossutil $OssutilVersion and checking its SHA-256"
        $ossutilPath = Install-Ossutil
        Invoke-Native { & $ossutilPath version } 'The downloaded ossutil does not run.'
    }

    if ($AddToPath) {
        Write-Step 'Adding the launcher folder to your user PATH'
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if ($null -eq $userPath) { $userPath = '' }
        if (($userPath -split ';') -notcontains $BinDir) {
            [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $BinDir).TrimStart(';'), 'User')
        }
    }

    $shortcut = ''
    if (-not $NoShortcut) {
        Write-Step 'Creating the Start Menu shortcut'
        $shortcut = New-StartMenuShortcut
    }

    Write-Step 'Checking the installation'
    Invoke-Native { & $Launcher --help } 'The launcher did not start.'
    $version = & $venvPy -c 'import ossutil_tui; print(ossutil_tui.__version__)'

    Write-Host ''
    Write-Host "OSS Control Center $version is installed." -ForegroundColor Green
    Write-Host "  Start it:      Start Menu -> 'OSS Control Center'   or run:  $Launcher"
    if ($AddToPath) { Write-Host '  From a new terminal window you can simply type:  fadi-oss-tui' }
    Write-Host "  Your data:     $InstallDir  (login, queue, logs, checkpoints)"
    if ($ossutilPath) { Write-Host "  ossutil:       $ossutilPath  (the login screen fills this in for you)" }
    else { Write-Host '  ossutil:       not installed by this script. Re-run with -InstallOssutil, or enter the path of your own ossutil.exe at login.' }
    Write-Host '  Tip:           Windows Terminal gives the best display.'
}
catch {
    Write-Host ''
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}