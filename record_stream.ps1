# ============================================
# Script for recording live stream HLS/m3u8
# ============================================

# Config
$streamUrl = "<m3u8 link>"
$outputPrefix = "<NameOfEvent>"
$retryDelaySeconds = 60
$logFile = "${outputPrefix}_recording_log.txt"
Start-Transcript -Path $logFile -Append

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Automatyczne nagrywanie live streamu" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Stream URL: $streamUrl" -ForegroundColor Yellow
Write-Host "Plik wyjściowy: ${outputPrefix}_[timestamp].ts" -ForegroundColor Yellow
Write-Host "Log zapisywany do: $logFile" -ForegroundColor Yellow
Write-Host ""
Write-Host "Oczekiwanie na dostępność streamu..." -ForegroundColor Green
Write-Host "Naciśnij Ctrl+C aby przerwać" -ForegroundColor Red
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
        
        break
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
Write-Host "Pres THE button..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
