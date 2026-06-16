# ============================================
# Script for recording live stream HLS/m3u8
# using FFmpeg with automatic reconnect and retry
# All recorded files will be saved with timestamp in current directory
# ============================================

# Config
$streamUrl                = "<m3u8 link>"
$outputPrefix             = "<NameOfEvent>"
$retryDelaySeconds        = 60
$waitAfterSuccessSeconds  = 18000   # 5 h
$minExpectedDurationSec   = 28800   # 8 h — recordings shorter than this are treated as premature endings
$logFile                  = "${outputPrefix}_recording_log.txt"
$userAgent                = "VLC/3.0.21 LibVLC/3.0.21"

# FFmpeg check MUST be first, before any loop
if (-not (Get-Command "ffmpeg" -ErrorAction SilentlyContinue)) {
    Write-Host "FFmpeg is not installed or not in PATH. Please install it." -ForegroundColor Red
    exit 1
}

# ffprobe check (used for duration measurement)
$ffprobeAvailable = [bool](Get-Command "ffprobe" -ErrorAction SilentlyContinue)
if (-not $ffprobeAvailable) {
    Write-Host "[WARN] ffprobe not found — short-recording fallback will use wall-clock time instead." -ForegroundColor Yellow
}

Start-Transcript -Path $logFile -Append

try {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Automatic Recording" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Stream URL: $streamUrl" -ForegroundColor Yellow
    Write-Host "File: ${outputPrefix}_[timestamp].ts" -ForegroundColor Yellow
    Write-Host "Log save to: $logFile" -ForegroundColor Yellow
    Write-Host "Min expected duration: $($minExpectedDurationSec / 3600) h" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Waiting for stream..." -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop" -ForegroundColor Red
    Write-Host ""

    # Main Recording loop
    $attemptCount = 0
    while ($true) {
        $attemptCount++
        $timestamp  = Get-Date -Format "yyyyMMdd_HHmmss"
        $outputFile = "${outputPrefix}_${timestamp}.ts"

        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Try #${attemptCount}: Start Recording..." -ForegroundColor Cyan

        # Wall-clock timer (fallback when ffprobe unavailable)
        $recordStart = [System.Diagnostics.Stopwatch]::StartNew()

        # Run FFmpeg with reconnect
        ffmpeg `
            -nostdin `
            -rw_timeout 15000000 `
            -reconnect 1 `
            -reconnect_streamed 1 `
            -reconnect_at_eof 0 `
            -reconnect_on_network_error 1 `
            -reconnect_delay_max 10 `
            -multiple_requests 1 `
            -user_agent "$userAgent" `
            -i "$streamUrl" `
            -map 0 `
            -c copy `
            -f mpegts `
            "$outputFile"

        $exitCode = $LASTEXITCODE
        $recordStart.Stop()
        $wallDurationSec = [math]::Floor($recordStart.Elapsed.TotalSeconds)

        # ── Determine actual recorded duration ──────────────────────────────────
        $recordedDurationSec = $wallDurationSec   # default: wall-clock

        if ($ffprobeAvailable -and (Test-Path $outputFile)) {
            $probe = ffprobe -v 0 -show_entries format=duration -of csv=p=0 "$outputFile" 2>$null
            if ($probe -match '^\d+(\.\d+)?$') {
                $recordedDurationSec = [math]::Floor([double]$probe)
            }
        }

        $recHours   = [math]::Floor($recordedDurationSec / 3600)
        $recMinutes = [math]::Floor(($recordedDurationSec % 3600) / 60)
        $recSeconds = $recordedDurationSec % 60

        # ── Handle clean FFmpeg exit (code 0) ───────────────────────────────────
        if ($exitCode -eq 0) {
            Write-Host ""
            Write-Host "========================================" -ForegroundColor Green
            Write-Host "  FFmpeg finished cleanly" -ForegroundColor Green
            Write-Host "========================================" -ForegroundColor Green
            Write-Host ""
            Write-Host "File: $outputFile" -ForegroundColor Yellow

            if (Test-Path $outputFile) {
                $fileSizeBytes = (Get-Item $outputFile).Length
                $fileSize = $fileSizeBytes / 1MB
                Write-Host "File Size:    $([math]::Round($fileSize, 2)) MB" -ForegroundColor Yellow

                if ($fileSizeBytes -eq 0) {
                    Remove-Item $outputFile -ErrorAction SilentlyContinue
                    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Empty file removed." -ForegroundColor DarkGray
                }
            }

            Write-Host "Duration:     ${recHours}h ${recMinutes}m ${recSeconds}s" -ForegroundColor Yellow
            Write-Host ""

            # ── FALLBACK: recording was too short → stream ended prematurely ────
            if ($recordedDurationSec -lt $minExpectedDurationSec) {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] WARNING: Recording was shorter than $($minExpectedDurationSec/3600)h!" -ForegroundColor Yellow
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Premature stream end detected — restarting immediately..." -ForegroundColor Magenta
                Write-Host ""
                # No wait — go straight back to the top of the loop to retry
                continue
            }

            # ── Normal successful finish ─────────────────────────────────────────
            Write-Host "========================================" -ForegroundColor Green
            Write-Host "  Record Complete!" -ForegroundColor Green
            Write-Host "========================================" -ForegroundColor Green

            $waitHours   = [math]::Floor($waitAfterSuccessSeconds / 3600)
            $waitMinutes = [math]::Floor(($waitAfterSuccessSeconds % 3600) / 60)
            $waitSeconds = $waitAfterSuccessSeconds % 60
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Waiting ${waitHours}h ${waitMinutes}m ${waitSeconds}s before restarting..." -ForegroundColor Magenta

            Start-Sleep -Seconds $waitAfterSuccessSeconds
            continue
        }

        # ── Handle FFmpeg error exit ─────────────────────────────────────────────
        Write-Host ""
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] FFmpeg ended with error: $exitCode" -ForegroundColor Red
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Wall-clock duration: ${recHours}h ${recMinutes}m ${recSeconds}s" -ForegroundColor DarkGray

        if (Test-Path $outputFile) {
            $fileSize = (Get-Item $outputFile).Length / 1MB
            if ($fileSize -gt 0) {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Partial file saved: $([math]::Round($fileSize, 2)) MB" -ForegroundColor Yellow
            }
            else {
                Remove-Item $outputFile -ErrorAction SilentlyContinue
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Empty file removed." -ForegroundColor DarkGray
            }
        }

        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Retrying in ${retryDelaySeconds}s..." -ForegroundColor Magenta
        Write-Host ""
        Start-Sleep -Seconds $retryDelaySeconds
    }
}
finally {
    Stop-Transcript
}
