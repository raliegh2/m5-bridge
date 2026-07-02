param(
    [string]$InstallDir = "$env:USERPROFILE\mt5-ai-bridge"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found in PATH."
    }
}

Require-Command "git"
Require-Command "python"

$RepoUrl = "https://github.com/raliegh2/m5-bridge.git"
$Branch = "v10-swing-precision"

if (-not (Test-Path $InstallDir)) {
    Write-Host "Creating $InstallDir ..."
    git clone $RepoUrl $InstallDir
}

if (-not (Test-Path (Join-Path $InstallDir ".git"))) {
    throw "$InstallDir exists but is not a Git repository."
}

Set-Location $InstallDir

Write-Host "Fetching the V10 swing-precision branch ..."
git fetch origin
git checkout -B $Branch "origin/$Branch"

$VenvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating Python virtual environment ..."
    python -m venv .venv
}

Write-Host "Installing dependencies ..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

Write-Host "Installing the V10 local application route ..."
& $VenvPython tools\apply_strategy_engine_v10_precision.py

$EnvPath = Join-Path $InstallDir ".env"
if (-not (Test-Path $EnvPath)) {
    Copy-Item ".env.example" ".env"
}

$envText = Get-Content $EnvPath -Raw

function Set-EnvValue {
    param(
        [string]$Text,
        [string]$Name,
        [string]$Value
    )
    $pattern = "(?m)^" + [regex]::Escape($Name) + "=.*$"
    if ([regex]::IsMatch($Text, $pattern)) {
        return [regex]::Replace($Text, $pattern, "$Name=$Value")
    }
    return $Text.TrimEnd() + "`r`n$Name=$Value`r`n"
}

$envText = Set-EnvValue $envText "STRATEGY" "gbpusd_v10_precision"
$envText = Set-EnvValue $envText "MODE" "READ_ONLY"
$envText = Set-EnvValue $envText "SYMBOL" "GBPUSD"
$envText = Set-EnvValue $envText "V9_EVAL_CACHE_SECONDS" "5"
$envText = Set-EnvValue $envText "PORTFOLIO_V2_STATE_PATH" "state/portfolio_v10_precision_state.json"

Set-Content -Path $EnvPath -Value $envText -Encoding UTF8
New-Item -ItemType Directory -Force -Path "state" | Out-Null

Write-Host ""
Write-Host "Local installation completed." -ForegroundColor Green
Write-Host "Folder: $InstallDir"
Write-Host "Branch: $Branch"
Write-Host ""
Write-Host "Next:"
Write-Host "1. Open $EnvPath and enter your MT5 demo login, password and server."
Write-Host "2. Keep MODE=READ_ONLY for the first run."
Write-Host "3. Open MetaTrader 5 and log into the same demo account."
Write-Host "4. Run Run-V10SwingPrecision.ps1."
