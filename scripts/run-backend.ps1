# 后端 Uvicorn 子进程启动脚本（供 start.ps1 新窗口调用）
$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "EA Backend :8000"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$Python = "python"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
}

& $Python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
