# Builds dist\WaferGuard\WaferGuard.exe and dist\WaferGuard-Setup-1.0.0.exe (installer).
# Requirements: Python 3.11/3.12 x64, Node.js 20, Inno Setup 6 (iscc on PATH or default install path).
# Run from a PowerShell prompt in the repository root:  .\scripts\build_desktop_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m pip install -r requirements-desktop.txt
Push-Location frontend; npm install --no-audit --no-fund; npx vite build; Pop-Location
if (-not (Test-Path "models\wafer-ensemble\manifest.json")) { throw "Train or copy a model into models\wafer-ensemble first." }
pyinstaller packaging\waferguard.spec --noconfirm --distpath dist --workpath build
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc packaging\waferguard.iss
Write-Host "Built dist\WaferGuard-Setup-1.0.0.exe"
