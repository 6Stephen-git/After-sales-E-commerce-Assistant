# Redis 就绪检查：已运行则跳过，否则尝试 Docker / 本机 redis-server 启动
# 用法：.\scripts\ensure-redis.ps1 [-Python python.exe]

param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$RedisContainer = "ecommerce-assistant-redis"

# ---------- 探测 Redis 是否可 ping ----------
function Test-RedisPing {
    & $Python -c "import redis; redis.from_url('redis://127.0.0.1:6379/0').ping()" 2>$null
    return $LASTEXITCODE -eq 0
}

if (Test-RedisPing) {
    Write-Host "[start] redis   redis://127.0.0.1:6379 (already running)" -ForegroundColor Green
    exit 0
}

Write-Host "[start] redis not running, trying to start..." -ForegroundColor Cyan

# ---------- 优先 Docker 拉起官方镜像 ----------
if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $existing = (& docker ps -aq -f "name=^${RedisContainer}$" 2>$null | Out-String).Trim()
        if ($existing) {
            docker start $RedisContainer 2>$null | Out-Null
        } else {
            docker run -d --name $RedisContainer -p 6379:6379 redis:7-alpine 2>$null | Out-Null
        }
        if ($LASTEXITCODE -eq 0) {
            Start-Sleep -Seconds 2
            if (Test-RedisPing) {
                Write-Host "[start] redis   redis://127.0.0.1:6379 (docker: $RedisContainer)" -ForegroundColor Green
                exit 0
            }
        }
    }
}

# ---------- 回退：本机 redis-server ----------
if (Get-Command redis-server -ErrorAction SilentlyContinue) {
    Start-Process -FilePath "redis-server" -WindowStyle Hidden
    Start-Sleep -Seconds 2
    if (Test-RedisPing) {
        Write-Host "[start] redis   redis://127.0.0.1:6379 (local redis-server)" -ForegroundColor Green
        exit 0
    }
}

Write-Host "[start] redis unavailable. Install Redis or Docker, or start redis-server manually." -ForegroundColor Red
exit 1
