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

# ---------- Redis：Celery 队列与纠纷缓存依赖 ----------
& (Join-Path $PSScriptRoot "ensure-redis.ps1") -Python $Python
if ($LASTEXITCODE -ne 0) {
    exit 1
}

Write-Host "[start] backend  http://127.0.0.1:${BackendPort}" -ForegroundColor Green
Write-Host "[start] frontend http://127.0.0.1:${FrontendPort}" -ForegroundColor Green
Write-Host "[start] celery   analysis.run + agent5.async_review (Redis queue)" -ForegroundColor Green
Write-Host "[start] opening 3 windows: EA Backend | EA Celery | EA Frontend" -ForegroundColor Gray
Write-Host "[start] close each service window to stop" -ForegroundColor Gray

# ---------- 各服务独立窗口（-File 避免中文路径在 -Command 里转义失败） ----------
$PsArgs = @("-NoExit", "-ExecutionPolicy", "Bypass")
Start-Process -FilePath "powershell.exe" -ArgumentList ($PsArgs + "-File", (Join-Path $PSScriptRoot "run-backend.ps1"))
Start-Process -FilePath "powershell.exe" -ArgumentList ($PsArgs + "-File", (Join-Path $PSScriptRoot "run-celery.ps1"))
Start-Process -FilePath "powershell.exe" -ArgumentList ($PsArgs + "-File", (Join-Path $PSScriptRoot "run-frontend.ps1"))
