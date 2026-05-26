#!/usr/bin/env bash
# 商家应诉助手 — 本地开发一键启动（Linux / macOS / Git Bash）
# 用法：./scripts/start.sh

set -euo pipefail

# ---------- 路径与端口 ----------
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$ROOT/frontend"
BACKEND_PORT=8000
FRONTEND_PORT=5173

cd "$ROOT"

# ---------- Python：优先使用项目 .venv ----------
PYTHON="python3"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "[启动] 未找到 Python，请先安装 Python 3。" >&2
  exit 1
fi

# ---------- 环境变量检查 ----------
if [[ ! -f "$ROOT/.env" ]]; then
  echo "[启动] 未找到 .env，请复制 .env.example 为 .env 并填写配置。" >&2
  exit 1
fi

# ---------- 前端依赖：缺 node_modules 时自动安装 ----------
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "[启动] 正在安装前端依赖..."
  (cd "$FRONTEND_DIR" && npm install)
fi

echo "[启动] 后端 http://127.0.0.1:${BACKEND_PORT}  |  前端 http://127.0.0.1:${FRONTEND_PORT}"
echo "[启动] 按 Ctrl+C 停止全部服务。"

# ---------- 退出时回收子进程 ----------
PIDS=()
cleanup() {
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

# ---------- 同终端后台启动后端与前端 ----------
(cd "$ROOT" && "$PYTHON" -m uvicorn backend.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload) &
PIDS+=($!)

(cd "$FRONTEND_DIR" && npm run dev) &
PIDS+=($!)

wait
