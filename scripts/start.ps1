# E-commerce Assistant dev launcher (Windows PowerShell)
# Usage: .\scripts\start.ps1

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---------- paths and ports ----------
$Root = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $Root "frontend"
$BackendPort = 8000
$FrontendPort = 5173

Set-Location -LiteralPath $Root

# ---------- python: prefer project .venv ----------
$Python = "python"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
}

# ---------- env file check ----------
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Write-Host "[start] missing .env, copy .env.example to .env first" -ForegroundColor Yellow
    exit 1
}

# ---------- frontend deps ----------
$NodeModules = Join-Path $FrontendDir "node_modules"
if (-not (Test-Path -LiteralPath $NodeModules)) {
    Write-Host "[start] running npm install..." -ForegroundColor Cyan
    Push-Location -LiteralPath $FrontendDir
    try {
        & npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[start] npm install failed" -ForegroundColor Red
            exit 1
        }
    } finally {
        Pop-Location
    }
}

Write-Host "[start] backend  http://127.0.0.1:${BackendPort}" -ForegroundColor Green
Write-Host "[start] frontend http://127.0.0.1:${FrontendPort}" -ForegroundColor Green
Write-Host "[start] close each service window to stop" -ForegroundColor Gray

# ---------- launch backend in new window ----------
$BackendArgs = @(
    "-NoExit",
    "-Command",
    "& '$Python' -m uvicorn backend.main:app --host 127.0.0.1 --port $BackendPort --reload"
)
Start-Process -FilePath "powershell.exe" -ArgumentList $BackendArgs -WorkingDirectory $Root

# ---------- launch frontend in new window ----------
$FrontendArgs = @(
    "-NoExit",
    "-Command",
    "npm run dev"
)
Start-Process -FilePath "powershell.exe" -ArgumentList $FrontendArgs -WorkingDirectory $FrontendDir
