# =============================================================================
#  start-recorder.ps1
#  Starter script for yt-live-recorder.py on Windows Server
#  Run as Administrator in PowerShell
# =============================================================================

# ── CONFIGURATION - EDIT BEFORE RUNNING ──────────────────────────────────────

$ChannelUrl    = "https://www.youtube.com/@McKeyPL/streams"   # YouTube channel URL
$OutputDir     = "E:\Storage"                           # Recording output folder
$LogFile       = "E:\Storage\recorder.log"             # Log file
$CookiesBrowser = "firefox"                                # firefox | chrome | edge | chromium
# $CookiesFile  = "X:\cookies.txt"                         # Uncomment if you use a cookies file instead of a browser
$Interval      = 60                                        # Live check interval [seconds]
$Format        = "bestvideo+bestaudio/best"                # yt-dlp format
$MergeFormat   = "mkv"                                     # mkv | mp4 | ts
$Retries       = 5                                         # Number of retries for network errors
$ScriptPath    = "$PSScriptRoot\yt-live-recorder.py"      # Path to the Python script

# =============================================================================

$ErrorActionPreference = "Stop"

function Write-Header {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  yt-live-recorder | Starter"                                 -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Check-Command {
    param($Name, $Cmd, $Fix)
    Write-Host -NoNewline "  Checking $Name... "
    try {
        $ver = & $Cmd 2>&1 | Select-Object -First 1
        Write-Host "OK  ($ver)" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "ERROR" -ForegroundColor Red
        Write-Host "    Instalation: $Fix" -ForegroundColor Yellow
        return $false
    }
}

function Check-Requirements {
    Write-Host "Checking the required tools:" -ForegroundColor White
    Write-Host ""

    $ok = $true
    if (-not (Check-Command "Python"  { python --version }  "winget install Python.Python.3.12 lub https://www.python.org")) { $ok = $false }
    if (-not (Check-Command "yt-dlp"  { yt-dlp --version }  "pip install -U yt-dlp")) { $ok = $false }
    if (-not (Check-Command "Deno"    { deno --version }     "winget install DenoLand.Deno")) { $ok = $false }
    if (-not (Check-Command "ffmpeg"  { ffmpeg -version }    "winget install Gyan.FFmpeg")) { $ok = $false }

    Write-Host ""
    if (-not $ok) {
        Write-Host "ERROR: The required tools are missing. Please install them and restart." -ForegroundColor Red
        Write-Host ""
        Write-Host "Quick installation of everything via Winget (as Administrator):" -ForegroundColor Yellow
        Write-Host "  winget install Python.Python.3.12 DenoLand.Deno Gyan.FFmpeg" -ForegroundColor Yellow
        Write-Host "  pip install -U yt-dlp" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }

    Write-Host "  All tools are available." -ForegroundColor Green
    Write-Host ""
}

function Update-YtDlp {
    Write-Host "Update yt-dlp to the latest version..." -ForegroundColor White
    try {
        pip install -U yt-dlp --quiet
        Write-Host "  yt-dlp has been updated." -ForegroundColor Green
    } catch {
        Write-Host "  I was unable to update yt-dlp (I'll continue using the existing version)." -ForegroundColor Yellow
    }
    Write-Host ""
}

function Prepare-OutputDir {
    if (-not (Test-Path $OutputDir)) {
        Write-Host "Tworzenie katalogu: $OutputDir" -ForegroundColor White
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
        Write-Host "  Created." -ForegroundColor Green
        Write-Host ""
    }
}

function Build-Arguments {
    $args_list = @(
        $ScriptPath,
        "--channel-url", $ChannelUrl,
        "--output-dir",  $OutputDir,
        "--log-file",    $LogFile,
        "--interval",    $Interval,
        "--format",      $Format,
        "--merge-format", $MergeFormat,
        "--retries",     $Retries
    )

    if ($null -ne (Get-Variable -Name CookiesFile -ErrorAction SilentlyContinue) -and $CookiesFile) {
        $args_list += "--cookies-file"
        $args_list += $CookiesFile
    } else {
        $args_list += "--cookies-browser"
        $args_list += $CookiesBrowser
    }

    return $args_list
}

function Show-Config {
    Write-Host "Configuration:" -ForegroundColor White
    Write-Host "  Channel     : $ChannelUrl"
    Write-Host "  Output     : $OutputDir"
    Write-Host "  Log        : $LogFile"
    Write-Host "  Interwal   : ${Interval}s"
    Write-Host "  Format     : $Format -> .$MergeFormat"
    if ($null -ne (Get-Variable -Name CookiesFile -ErrorAction SilentlyContinue) -and $CookiesFile) {
        Write-Host "  Cookies    : file $CookiesFile"
    } else {
        Write-Host "  Cookies    : browser $CookiesBrowser"
    }
    Write-Host ""
}

# ── MAIN ─────────────────────────────────────────────────────────────────────

Write-Header
Check-Requirements
Update-YtDlp
Prepare-OutputDir
Show-Config

Write-Host "Runing..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C for break." -ForegroundColor Yellow
Write-Host ""

$py_args = Build-Arguments
python @py_args
