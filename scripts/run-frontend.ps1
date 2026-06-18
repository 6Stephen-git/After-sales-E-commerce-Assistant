# 前端 Vite 子进程启动脚本（供 start.ps1 新窗口调用）
$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "EA Frontend :5173"
$FrontendDir = Join-Path (Split-Path -Parent $PSScriptRoot) "frontend"
Set-Location -LiteralPath $FrontendDir

npm run dev
