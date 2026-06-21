# =============================================================================
#  start-twitch-recorder.ps1
#  Starter script for twitch-live-recorder.py on Windows Server or pwsh on linux.
#  Run as Administrator in PowerShell
# =============================================================================

# ── CONFIGURATION - EDIT BEFORE RUNNING ──────────────────────────────────────

$Channels    = "ctsg,jellysketch,perrydotto,ninuschk"                         # Twitch channel names (comma-separated)
$OutputDir   = "E:\TwitchRecordings"                  # Root recording directory
$LogFile     = "E:\TwitchRecordings\recorder.log"     # Log file path
$Quality     = "best"                                  # best | 1080p60 | 720p60 | 480p | worst
$Interval    = 60                                      # Live check interval [seconds]
$Retries     = 5                                       # Stream open retry attempts
$DisableAds  = $true                                   # Skip Twitch ads (streamlink plugin)
$ScriptPath  = "$PSScriptRoot\twitch-live-recorder.py" # Path to Python script

# =============================================================================

$ErrorActionPreference = "Stop"

function Write-Header {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  twitch-live-recorder | Starter"                             -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Check-Command {
    param($Name, [scriptblock]$Cmd, $Fix)
    Write-Host -NoNewline "  Checking $Name... "
    try {
        $ver = & $Cmd 2>&1 | Select-Object -First 1
        Write-Host "OK  ($ver)" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "ERROR" -ForegroundColor Red
        Write-Host "    Install: $Fix" -ForegroundColor Yellow
        return $false
    }
}

function Check-Requirements {
    Write-Host "Checking required tools:" -ForegroundColor White
    Write-Host ""

    $ok = $true
    if (-not (Check-Command "Python"     { python --version }     "winget install Python.Python.3.12  lub  https://www.python.org")) { $ok = $false }
    if (-not (Check-Command "streamlink" { streamlink --version } "pip install -U streamlink"))                                      { $ok = $false }
    if (-not (Check-Command "ffmpeg"     { ffmpeg -version }      "winget install Gyan.FFmpeg"))                                     { $ok = $false }

    Write-Host ""
    if (-not $ok) {
        Write-Host "ERROR: Required tools are missing. Install them and restart." -ForegroundColor Red
        Write-Host ""
        Write-Host "Quick install via Winget + pip (as Administrator):" -ForegroundColor Yellow
        Write-Host "  winget install Python.Python.3.12 Gyan.FFmpeg" -ForegroundColor Yellow
        Write-Host "  pip install -U streamlink" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }

    Write-Host "  All tools available." -ForegroundColor Green
    Write-Host ""
}

function Update-Streamlink {
    Write-Host "Updating streamlink to the latest version..." -ForegroundColor White
    try {
        pip install -U streamlink --quiet
        Write-Host "  streamlink updated." -ForegroundColor Green
    } catch {
        Write-Host "  Could not update streamlink (using existing version)." -ForegroundColor Yellow
    }
    Write-Host ""
}

function Prepare-OutputDir {
    if (-not (Test-Path $OutputDir)) {
        Write-Host "Creating directory: $OutputDir" -ForegroundColor White
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
        Write-Host "  Created." -ForegroundColor Green
        Write-Host ""
    }
}

function Build-Arguments {
    $args_list = @(
        $ScriptPath,
        "--channels",   $Channels,
        "--output-dir", $OutputDir,
        "--log-file",   $LogFile,
        "--quality",    $Quality,
        "--interval",   $Interval,
        "--retries",    $Retries
    )

    if ($DisableAds) {
        $args_list += "--disable-ads"
    }

    return $args_list
}

function Show-Config {
    Write-Host "Configuration:" -ForegroundColor White
    Write-Host "  Channels  : $Channels"
    Write-Host "  Output    : $OutputDir"
    Write-Host "  Log       : $LogFile"
    Write-Host "  Quality   : $Quality"
    Write-Host "  Interval  : ${Interval}s"
    Write-Host "  Retries   : $Retries"
    Write-Host "  Disable ads: $DisableAds"
    Write-Host ""
    Write-Host "File structure:" -ForegroundColor DarkGray
    Write-Host "  {OutputDir}\{channel}\YYYYMMDD_HHMMSS_{channel}_{title}.mkv"    -ForegroundColor DarkGray
    Write-Host "  {OutputDir}\{channel}\YYYYMMDD_HHMMSS_{channel}_{title}_chat.srt" -ForegroundColor DarkGray
    Write-Host "  {OutputDir}\{channel}\YYYYMMDD_HHMMSS_{channel}_{title}_meta.txt" -ForegroundColor DarkGray
    Write-Host ""
}

# ── MAIN ─────────────────────────────────────────────────────────────────────

Write-Header
Check-Requirements
Update-Streamlink
Prepare-OutputDir
Show-Config

Write-Host "Running..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop all recordings gracefully." -ForegroundColor Yellow
Write-Host ""

$py_args = Build-Arguments
python @py_args
