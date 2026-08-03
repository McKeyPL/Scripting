# =============================================================================
#  start-kick-recorder.ps1
#  Starter script for kick-live-recorder.py on Windows Server or pwsh on Linux.
#  Run as Administrator in PowerShell
# =============================================================================

# ── CONFIGURATION - EDIT BEFORE RUNNING ──────────────────────────────────────

$Channels   = "ctsg,jellysketch,perrydotto,ninuschk"                             # Kick channel names (comma-separated)
$OutputDir  = "E:\KickRecordings"                       # Root recording directory
$LogFile    = "E:\KickRecordings\recorder.log"          # Log file path
$Quality    = "best"                                     # best | 1080p60 | 720p60 | 480p | worst
$Interval   = 60                                         # Live check interval [seconds]
$Retries    = 5                                          # Stream open retry attempts
$ScriptPath = "$PSScriptRoot\kick-live-recorder.py"      # Path to Python script

# =============================================================================

$ErrorActionPreference = "Stop"

function Write-Header {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  kick-live-recorder | Starter"                              -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Check-Command {
    param($Name, [scriptblock]$Cmd, $Fix)
    Write-Host -NoNewline "  Checking $Name... "
    try {
        $ver = & $Cmd 2>&1 | Select-Object -First 1
        if ($LASTEXITCODE -ne 0) {
            throw "$Name exited with code $LASTEXITCODE"
        }
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
    if (-not (Check-Command "Python"           { python --version }                                                        "winget install Python.Python.3.12  or  https://www.python.org")) { $ok = $false }
    if (-not (Check-Command "streamlink"       { streamlink --version }                                                    "python -m pip install -U streamlink"))                           { $ok = $false }
    if (-not (Check-Command "ffmpeg"           { ffmpeg -version }                                                         "winget install Gyan.FFmpeg"))                                    { $ok = $false }
    if (-not (Check-Command "requests"         { python -c "import requests; print(requests.__version__)" }                 "python -m pip install -U requests"))                             { $ok = $false }
    if (-not (Check-Command "websocket-client" { python -c "import websocket; print(websocket.__version__)" }               "python -m pip install -U websocket-client"))                     { $ok = $false }

    Write-Host ""
    if (-not $ok) {
        Write-Host "ERROR: Required tools are missing. Install them and restart." -ForegroundColor Red
        Write-Host ""
        Write-Host "Quick install via Winget + pip (as Administrator):" -ForegroundColor Yellow
        Write-Host "  winget install Python.Python.3.12 Gyan.FFmpeg" -ForegroundColor Yellow
        Write-Host '  python -m pip install -U "streamlink>=7.6.0" requests websocket-client' -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }

    Write-Host "  All tools available." -ForegroundColor Green
    Write-Host ""
}

function Update-PythonPackages {
    Write-Host "Updating Kick recorder Python packages..." -ForegroundColor White
    try {
        python -m pip install -U "streamlink>=7.6.0" requests websocket-client --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "pip exited with code $LASTEXITCODE"
        }
        Write-Host "  streamlink, requests and websocket-client updated." -ForegroundColor Green
    } catch {
        Write-Host "  Could not update packages (using installed versions)." -ForegroundColor Yellow
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
    return @(
        $ScriptPath,
        "--channels",   $Channels,
        "--output-dir", $OutputDir,
        "--log-file",   $LogFile,
        "--quality",    $Quality,
        "--interval",   $Interval,
        "--retries",    $Retries
    )
}

function Show-Config {
    Write-Host "Configuration:" -ForegroundColor White
    Write-Host "  Channels : $Channels"
    Write-Host "  Output   : $OutputDir"
    Write-Host "  Log      : $LogFile"
    Write-Host "  Quality  : $Quality"
    Write-Host "  Interval : ${Interval}s"
    Write-Host "  Retries  : $Retries"
    Write-Host ""
    Write-Host "File structure:" -ForegroundColor DarkGray
    Write-Host "  {OutputDir}\{channel}\YYYYMMDD_HHMMSS_{channel}_{title}.mkv"              -ForegroundColor DarkGray
    Write-Host "  {OutputDir}\{channel}\YYYYMMDD_HHMMSS_{channel}_{title}_chat.srt"         -ForegroundColor DarkGray
    Write-Host "  {OutputDir}\{channel}\YYYYMMDD_HHMMSS_{channel}_{title}_chat_colored.srt" -ForegroundColor DarkGray
    Write-Host "  {OutputDir}\{channel}\YYYYMMDD_HHMMSS_{channel}_{title}_meta.txt"         -ForegroundColor DarkGray
    Write-Host ""
}

# ── MAIN ─────────────────────────────────────────────────────────────────────

Write-Header
Check-Requirements
Update-PythonPackages
Prepare-OutputDir
Show-Config

Write-Host "Running..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop all recordings gracefully." -ForegroundColor Yellow
Write-Host ""

$py_args = Build-Arguments
python @py_args
