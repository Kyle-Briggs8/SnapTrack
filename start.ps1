# SnapTrack Startup Script
# This script sets the Google Cloud credentials and starts the Flask app

$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\Users\kyle\Downloads\snaptrack-482706-0d453712c512.json"

# Set Gemini API key if you have one (optional - app will fall back to Vision API if not set)
# Uncomment and add your Gemini API key below:
# $env:GEMINI_API_KEY = "your-gemini-api-key-here"

Write-Host "Starting SnapTrack..." -ForegroundColor Green
Write-Host "Vision API credentials: $env:GOOGLE_APPLICATION_CREDENTIALS" -ForegroundColor Cyan

if ($env:GEMINI_API_KEY) {
    Write-Host "Gemini API key: Set (detailed descriptions enabled)" -ForegroundColor Green
} else {
    Write-Host "Gemini API key: Not set (using Vision API only)" -ForegroundColor Yellow
    Write-Host "  Get your key at: https://ai.google.dev/" -ForegroundColor Gray
}

# Verify credentials file exists
if (-not (Test-Path $env:GOOGLE_APPLICATION_CREDENTIALS)) {
    Write-Host "ERROR: Credentials file not found at: $env:GOOGLE_APPLICATION_CREDENTIALS" -ForegroundColor Red
    exit 1
}

Write-Host "Credentials file found. Starting Flask app..." -ForegroundColor Green
Write-Host "Open http://localhost:5000 in your browser" -ForegroundColor Yellow
Write-Host ""

python app.py

