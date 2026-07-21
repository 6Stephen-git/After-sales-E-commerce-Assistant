# Celery Worker 子进程启动脚本（供 start.ps1 新窗口调用）
$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "EA Celery"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$Python = "python"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
}

& $Python -m celery -A backend.tasks.celery_app worker --loglevel=info -P solo
