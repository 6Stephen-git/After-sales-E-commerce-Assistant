#!/usr/bin/env bash
# Redis 就绪检查：已运行则跳过，否则尝试 Docker / 本机 redis-server 启动
# 用法：./scripts/ensure-redis.sh [python]

set -euo pipefail

PYTHON="${1:-python3}"
REDIS_CONTAINER="ecommerce-assistant-redis"

# ---------- 探测 Redis 是否可 ping ----------
redis_ping() {
  "$PYTHON" -c "import redis; redis.from_url('redis://127.0.0.1:6379/0').ping()" >/dev/null 2>&1
}

if redis_ping; then
  echo "[启动] Redis 已运行：redis://127.0.0.1:6379"
  exit 0
fi

echo "[启动] Redis 未运行，正在尝试启动..."

# ---------- 优先 Docker 拉起官方镜像 ----------
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if docker ps -aq -f "name=^${REDIS_CONTAINER}$" | grep -q .; then
    docker start "$REDIS_CONTAINER" >/dev/null
  else
    docker run -d --name "$REDIS_CONTAINER" -p 6379:6379 redis:7-alpine >/dev/null
  fi
  sleep 2
  if redis_ping; then
    echo "[启动] Redis 已通过 Docker 启动：$REDIS_CONTAINER"
    exit 0
  fi
fi

# ---------- 回退：本机 redis-server（后台守护） ----------
if command -v redis-server >/dev/null 2>&1; then
  redis-server --daemonize yes >/dev/null 2>&1 || true
  sleep 2
  if redis_ping; then
    echo "[启动] Redis 已通过本机 redis-server 启动"
    exit 0
  fi
fi

echo "[启动] Redis 不可用，请安装 Redis 或 Docker，或手动启动 redis-server。" >&2
exit 1
