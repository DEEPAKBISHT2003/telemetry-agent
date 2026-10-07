# AI Telemetry Agent Uninstaller for Windows PowerShell
# Phase 2.6 - Global Antigravity Lifecycle Hook Uninstallation

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path "$ScriptDir\..").Path

# Find available Python interpreter
$PythonExe = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
} elseif (Test-Path "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe") {
    $PythonExe = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
} else {
    Write-Error "Python interpreter not found. Please ensure Python is installed."
    exit 1
}

# Run uninstall-hook CLI command
Push-Location $ProjectRoot
try {
    & $PythonExe -m src.main uninstall-hook $args
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    exit $exitCode
}
