# Friday AI Assistant — Windows installer (PowerShell)
# Usage: .\install.ps1
# Run from PowerShell as the current user (no admin required)

$ErrorActionPreference = "Stop"

Write-Host "◆ Installing Friday AI Assistant..." -ForegroundColor Cyan
Write-Host ""

# Check Python
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  Found: $pythonVersion"
} catch {
    Write-Host "✗ Python is required but not installed." -ForegroundColor Red
    Write-Host "  Install from https://python.org (make sure to check 'Add to PATH')"
    exit 1
}

# Check pip
try {
    $pipVersion = pip --version 2>&1
} catch {
    Write-Host "✗ pip is required but not installed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[1/4] Installing Python dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Failed to install dependencies" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "[2/4] Creating .env file..." -ForegroundColor Yellow
if (-not (Test-Path .env)) {
    if (Test-Path .env.example) {
        Copy-Item .env.example .env
        Write-Host "  Created .env from .env.example"
    } else {
        "GLM_API_KEY=" | Out-File .env -Encoding ascii
        Write-Host "  Created .env"
    }
    # Generate a secure random FRIDAY_API_TOKEN
    $tokenBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($tokenBytes)
    $token = [System.Convert]::ToBase64String($tokenBytes) -replace '[+/=]', ''
    "FRIDAY_API_TOKEN=$token" | Add-Content .env
    Write-Host "  Secure auth token generated ($($token.Length) chars)." -ForegroundColor Green
    Write-Host "  Keep your .env file private — it contains your Friday credentials." -ForegroundColor Yellow
} else {
    Write-Host "  .env already exists — leaving it alone"
}
Write-Host ""

Write-Host "[3/4] Installing Friday console script..." -ForegroundColor Yellow
pip install -e .
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (console script install failed — you can still run 'python -m cli')"
}
Write-Host ""

Write-Host "[4/4] Asking for GLM key (free at open.bigmodel.cn)..." -ForegroundColor Yellow
$key = Read-Host "Enter your free Z.ai GLM key (press Enter to skip and edit .env later)"
if ($key -and $key -ne "") {
    # Replace the GLM_API_KEY line in .env
    $envContent = Get-Content .env
    $envContent = $envContent | ForEach-Object {
        if ($_ -match "^GLM_API_KEY=") {
            "GLM_API_KEY=$key"
        } else {
            $_
        }
    }
    # If no GLM_API_KEY line exists, append
    if (-not ($envContent -match "^GLM_API_KEY=")) {
        $envContent += "GLM_API_KEY=$key"
    }
    $envContent | Out-File .env -Encoding ascii
    Write-Host "  ✓ GLM_API_KEY saved to .env"
} else {
    Write-Host "  (skipped — edit .env later to add GLM_API_KEY)"
}
Write-Host ""

Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  ✓ Friday installed successfully!" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Run:  friday"
Write-Host "  2. Or start the web dashboard:"
Write-Host "     uvicorn api.main:app --port 8000"
Write-Host "  3. Then open http://localhost:8000"
Write-Host ""
Write-Host "Get a free GLM key at: https://open.bigmodel.cn"
Write-Host ""
