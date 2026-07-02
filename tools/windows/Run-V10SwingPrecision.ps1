param(
    [string]$InstallDir = "$env:USERPROFILE\mt5-ai-bridge",
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$VenvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
$EnvPath = Join-Path $InstallDir ".env"

if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment not found. Run Setup-V10SwingPrecision.ps1 first."
}
if (-not (Test-Path $EnvPath)) {
    throw ".env not found. Run Setup-V10SwingPrecision.ps1 first."
}

Set-Location $InstallDir

$envText = Get-Content $EnvPath -Raw
$modeMatch = [regex]::Match($envText, "(?m)^MODE=(.+)$")
$mode = if ($modeMatch.Success) { $modeMatch.Groups[1].Value.Trim() } else { "UNKNOWN" }

Write-Host "Install folder: $InstallDir"
Write-Host "Mode: $mode"

if ($mode -eq "AUTO") {
    $answer = Read-Host "AUTO can place orders on the connected account. Type RUN AUTO to continue"
    if ($answer -ne "RUN AUTO") {
        Write-Host "Cancelled."
        exit 1
    }
}

if (-not $SkipPreflight) {
    Write-Host "Running safe MT5 preflight ..."
    & $VenvPython preflight.py
    if ($LASTEXITCODE -ne 0) {
        throw "Preflight failed."
    }
}

Write-Host ""
Write-Host "Starting V10 swing-precision locally. Press Ctrl+C to stop." -ForegroundColor Green
& $VenvPython bridge.py
