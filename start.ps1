# Sets up (if needed) and starts Clanky Cat.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    if (-not (Test-Path $venvPython)) {
        Write-Error "Failed to create .venv - is Python installed and on PATH?"
        exit 1
    }
}

Write-Host "Checking dependencies..."
& $venvPython -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Error "Dependency install failed."
    exit 1
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Created .env from .env.example." -ForegroundColor Yellow
    Write-Host "Fill in DISCORD_TOKEN, CHANNEL_ID (and Spotify credentials), then run this script again." -ForegroundColor Yellow
    exit 1
}

Write-Host "Starting Clanky Cat (Ctrl+C to stop)..."
& $venvPython -m clanky_cat
exit $LASTEXITCODE
