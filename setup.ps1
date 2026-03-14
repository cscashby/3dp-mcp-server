# 3DP MCP Server — Setup Script for Windows 11
# Run with: powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir = Join-Path $ScriptDir ".venv"

Write-Host "=== 3DP MCP Server Setup ===" -ForegroundColor Cyan
Write-Host ""

# --- Detect Python 3.11+ ---
$Python = $null

# Candidates: py launcher, python3, python
$candidates = @()

# Try the py launcher first (standard on Windows)
if (Get-Command "py" -ErrorAction SilentlyContinue) {
    $candidates += "py -3"
}
if (Get-Command "python3" -ErrorAction SilentlyContinue) {
    $candidates += "python3"
}
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $candidates += "python"
}

foreach ($cmd in $candidates) {
    try {
        $versionInfo = & ([scriptblock]::Create("$cmd -c `"import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')`"")) 2>$null
        $parts = $versionInfo.Split('.')
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        if ($major -ge 3 -and $minor -ge 11) {
            $Python = $cmd
            Write-Host "[OK] Found $cmd ($versionInfo)" -ForegroundColor Green
            break
        }
    } catch {
        continue
    }
}

if (-not $Python) {
    Write-Host "[ERROR] Python 3.11+ is required but not found." -ForegroundColor Red
    Write-Host "Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "Make sure to check 'Add Python to PATH' during installation." -ForegroundColor Yellow
    exit 1
}

# --- Create virtual environment ---
Write-Host ""
Write-Host "Creating virtual environment..."
Invoke-Expression "$Python -m venv `"$VenvDir`""

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] Virtual environment creation failed. $VenvPython not found." -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Virtual environment created at .venv" -ForegroundColor Green

# --- Install dependencies ---
Write-Host ""
Write-Host "Installing dependencies (this may take a few minutes for build123d)..."
& $VenvPip install --upgrade pip -q
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to upgrade pip." -ForegroundColor Red
    exit 1
}

& $VenvPip install "build123d>=0.7" "mcp[cli]>=1.0" "bd_warehouse" "qrcode>=7.0" -q
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to install dependencies." -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Dependencies installed" -ForegroundColor Green

# --- Verify build123d ---
Write-Host ""
Write-Host "Verifying build123d..."
& $VenvPython -c "from build123d import Box; b = Box(10,10,10); print(f'[OK] build123d works - test cube volume: {b.volume:.1f} mm3')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] build123d verification failed." -ForegroundColor Red
    exit 1
}

# --- Verify MCP ---
Write-Host "Verifying MCP..."
& $VenvPython -c "from mcp.server.fastmcp import FastMCP; print('[OK] MCP server framework works')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] MCP verification failed." -ForegroundColor Red
    exit 1
}

# --- Create outputs directory ---
$OutputsDir = Join-Path $ScriptDir "outputs"
if (-not (Test-Path $OutputsDir)) {
    New-Item -ItemType Directory -Path $OutputsDir | Out-Null
}
Write-Host "[OK] outputs/ directory ready" -ForegroundColor Green

# --- Register MCP server with Claude Code ---
Write-Host ""
Write-Host "Registering MCP server with Claude Code..."
$ServerScript = Join-Path $ScriptDir "server.py"

if (Get-Command "claude" -ErrorAction SilentlyContinue) {
    claude mcp add 3dp-mcp-server "$VenvPython" "$ServerScript" -s user
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] MCP server registered with Claude Code" -ForegroundColor Green
    } else {
        Write-Host "[WARN] claude mcp add returned an error. You may need to register manually." -ForegroundColor Yellow
    }
} else {
    Write-Host "[SKIP] 'claude' CLI not found in PATH." -ForegroundColor Yellow
    Write-Host "  Run this manually after installing Claude Code:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  claude mcp add 3dp-mcp-server `"$VenvPython`" `"$ServerScript`" -s user" -ForegroundColor White
}

# --- Done ---
Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "To verify, start Claude Code and ask:"
Write-Host '  "What MCP servers are available?"'
Write-Host ""
Write-Host "Then try:"
Write-Host '  "Create a 50x40x10mm box with 2mm fillets on all edges"'
Write-Host ""
Write-Host "STL files will be saved to: $OutputsDir"
