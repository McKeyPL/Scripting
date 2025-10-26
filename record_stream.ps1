# ============================================
# Script for recording live stream HLS/m3u8
# using FFmpeg with automatic reconnect and retry
# All recorded files will be saved with timestamp in current directory
# ============================================

# Config
$streamUrl = "<m3u8 link>"
$outputPrefix = "<NameOfEvent>"
$retryDelaySeconds = 60
$waitAfterSuccessSeconds = 18000  # 5 h
$logFile = "${outputPrefix}_recording_log.txt"
Start-Transcript -Path $logFile -Append

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Automatic Recording" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Stream URL: $streamUrl" -ForegroundColor Yellow
Write-Host "File: ${outputPrefix}_[timestamp].ts" -ForegroundColor Yellow
Write-Host "Log save to: $logFile" -ForegroundColor Yellow
Write-Host ""
Write-Host "Waiting for stream..." -ForegroundColor Green
Write-Host "Press Ctrl+C for break" -ForegroundColor Red
Write-Host ""

# Main Recording loop
$attemptCount = 0
while ($true) {
    $attemptCount++
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outputFile = "${outputPrefix}_${timestamp}.ts"
    
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Try #${attemptCount}: Start Recording..." -ForegroundColor Cyan
    
    # Run FFmpeg with reconnect
    ffmpeg `
        -reconnect 1 `
        -reconnect_streamed 1 `
        -reconnect_delay_max 10 `
        -reconnect_on_http_error 4xx,5xx `
        -timeout 10000000 `
        -i "$streamUrl" `
        -c copy `
        -f mpegts  `
        -movflags +faststart `
        "$outputFile"
    
    $exitCode = $LASTEXITCODE
    
    # check ffmpeg exit code
    if ($exitCode -eq 0) {
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Record Finish!" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "File: $outputFile" -ForegroundColor Yellow
        
        # Filesize
        if (Test-Path $outputFile) {
            $fileSize = (Get-Item $outputFile).Length / 1MB
            Write-Host "File Size: $([math]::Round($fileSize, 2)) MB" -ForegroundColor Yellow
        }

        # Calculate wait time dynamically
        $waitHours = [math]::Floor($waitAfterSuccessSeconds / 3600)
        $waitMinutes = [math]::Floor(($waitAfterSuccessSeconds % 3600) / 60)
        $waitSeconds = $waitAfterSuccessSeconds % 60
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Waiting for $waitHours hours, $waitMinutes minutes, and $waitSeconds seconds before restarting..." -ForegroundColor Magenta

        Start-Sleep -Seconds $waitAfterSuccessSeconds
        continue
    }
    else {
        Write-Host ""
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] FFmpeg end with error: $exitCode" -ForegroundColor Red
        
        # check for error
        if (Test-Path $outputFile) {
            $fileSize = (Get-Item $outputFile).Length / 1MB
            if ($fileSize -gt 0) {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Partial file: $([math]::Round($fileSize, 2)) MB" -ForegroundColor Yellow
            } else {
                # Delete 0
                Remove-Item $outputFile -ErrorAction SilentlyContinue
            }
        }
        
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Trying for ${retryDelaySeconds} sec..." -ForegroundColor Magenta
        Write-Host ""
        
        # wait for stream
        Start-Sleep -Seconds $retryDelaySeconds
    }
}

Stop-Transcript

Write-Host ""
Write-Host "Press THE button..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

if (-not (Get-Command "ffmpeg" -ErrorAction SilentlyContinue)) {
    Write-Host "FFmpeg is not installed or not in PATH. Please install it." -ForegroundColor Red
    exit 1
}
