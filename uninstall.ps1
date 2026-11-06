<#
.SYNOPSIS
  Removes OSS Control Center that was installed by install.ps1.

.DESCRIPTION
  Removes the program (virtual environment, launcher, downloaded ossutil, shortcut, PATH entry).
  Your saved login, upload queue, logs and checkpoints are KEPT unless you pass -RemoveData.
  Only files created by the installer are touched; the install folder is never deleted recursively.

.PARAMETER InstallDir
  The folder that was used when installing. Default: %LOCALAPPDATA%\FaDi-OSS-TUI

.PARAMETER RemoveData
  Also delete the saved login, the upload queue, logs and checkpoints.
  Paused uploads can NOT be resumed afterwards.

.PARAMETER Force
  Do not ask for confirmation before removing data.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'FaDi-OSS-TUI'),
    [switch]$RemoveData,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Marker = Join-Path $InstallDir '.fadi-oss-tui-install'
$BinDir = Join-Path $InstallDir 'bin'
$Launcher = Join-Path $BinDir 'fadi-oss-tui.cmd'
$ShortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'OSS Control Center.lnk'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Remove-Owned([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-Host "    removed $Path"
    }
}

try {
    if (-not (Test-Path -LiteralPath $Marker)) {
        throw "No installation marker found in $InstallDir. Nothing was removed (wrong folder? pass -InstallDir)."
    }

    if ($RemoveData -and -not $Force) {
        Write-Host 'This will also delete your saved login, upload queue, logs and checkpoints.' -ForegroundColor Yellow
        Write-Host 'Paused uploads cannot be resumed afterwards.' -ForegroundColor Yellow
        $answer = Read-Host "Type DELETE to continue"
        if ($answer -cne 'DELETE') { throw 'Cancelled. Nothing was removed.' }
    }

    Write-Step 'Removing the program'
    try {
        Remove-Owned (Join-Path $InstallDir 'venv')
    }
    catch {
        throw "Could not remove the program files. Close OSS Control Center first, then try again. ($($_.Exception.Message))"
    }
    Remove-Owned $BinDir
    Remove-Owned (Join-Path $InstallDir 'ossutil')

    if (Test-Path -LiteralPath $ShortcutPath) {
        $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($ShortcutPath)
        if ((($lnk.TargetPath + ' ' + $lnk.Arguments).IndexOf($Launcher, [StringComparison]::OrdinalIgnoreCase)) -ge 0) {
            Remove-Owned $ShortcutPath
        }
        else {
            Write-Host '    left the Start Menu shortcut alone (it belongs to another installation)'
        }
    }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath -and (($userPath -split ';') -contains $BinDir)) {
        $kept = ($userPath -split ';') | Where-Object { $_ -and ($_ -ne $BinDir) }
        [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
        Write-Host '    removed the launcher folder from your user PATH'
    }

    if ($RemoveData) {
        Write-Step 'Removing your data'
        foreach ($name in 'profile.json', 'queue.json', 'ossutil.empty.cfg', 'logs', 'checkpoints') {
            Remove-Owned (Join-Path $InstallDir $name)
        }
    }
    else {
        Write-Host '    kept your login, queue, logs and checkpoints (use -RemoveData to delete them)'
    }

    # Keep the marker while data remains, so "-RemoveData" still works in a later run.
    $left = @(Get-ChildItem -LiteralPath $InstallDir -Force | Where-Object { $_.FullName -ne $Marker })
    if ($left.Count -eq 0) {
        Remove-Owned $Marker
        Remove-Item -LiteralPath $InstallDir -Force
    }

    Write-Host ''
    Write-Host 'OSS Control Center was removed.' -ForegroundColor Green
}
catch {
    Write-Host ''
    Write-Host "Uninstall stopped: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}