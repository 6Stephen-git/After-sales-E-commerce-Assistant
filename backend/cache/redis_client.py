"""
Redis 缓存客户端：连接、固定 TTL JSON 读写与故障冷却恢复。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import redis


CACHE_LOG_PREFIX = "[DisputeCache]"
logger = logging.getLogger(__name__)
_redis_client: redis.Redis | None = None
_redis_retry_after_monotonic: float = 0.0


def is_redis_cache_enabled() -> bool:
    """
    读取 ENABLE_REDIS_CACHE 开关，默认启用。
    """
    raw = os.getenv("ENABLE_REDIS_CACHE", "1").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


def get_agent_cache_version() -> str:
    """
    读取 AGENT_CACHE_VERSION，用于 B/C 层 key 后缀。
    """
    version = os.getenv("AGENT_CACHE_VERSION", "1").strip()
    return version or "1"


def _reconnect_cooldown_seconds() -> float:
    """
    读取 Redis 重连冷却时间，避免 Redis 故障期间每次请求都触发连接探测。
    """
    raw = os.getenv("REDIS_RECONNECT_COOLDOWN_SECONDS", "10").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning("%s REDIS_RECONNECT_COOLDOWN_SECONDS 无效，使用 10 秒", CACHE_LOG_PREFIX)
        return 10.0


def _mark_redis_unavailable(reason: Exception | str) -> None:
    """
    失效当前客户端并进入冷却窗口，使主链路将 Redis 视为安全 miss。
    """
    global _redis_client, _redis_retry_after_monotonic
    _redis_client = None
    cooldown = _reconnect_cooldown_seconds()
    _redis_retry_after_monotonic = time.monotonic() + cooldown
    logger.warning(
        "%s Redis 不可用，缓存已降级为安全 miss；%.1f 秒后自动重连。原因=%s",
        CACHE_LOG_PREFIX,
        cooldown,
        reason,
    )


def get_redis() -> redis.Redis | None:
    """
    获取 Redis 客户端；故障冷却期间返回 None，冷却结束后自动尝试重连。
    """
    global _redis_client
    if not is_redis_cache_enabled():
        return None
    if _redis_client is not None:
        return _redis_client
    if time.monotonic() < _redis_retry_after_monotonic:
        return None
    redis_url = os.getenv("REDIS_CACHE_URL", "redis://localhost:6379/1").strip()
    if not redis_url:
        _mark_redis_unavailable("REDIS_CACHE_URL 为空")
        return None
    connect_timeout = 2.0
    try:
        raw_timeout = os.getenv("REDIS_CONNECT_TIMEOUT_SECONDS", "2").strip()
        connect_timeout = max(0.5, float(raw_timeout))
    except ValueError:
        logger.warning("%s REDIS_CONNECT_TIMEOUT_SECONDS 无效，使用 2 秒", CACHE_LOG_PREFIX)
    try:
        client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=connect_timeout,
            socket_timeout=connect_timeout,
        )
        client.ping()
        _redis_client = client
        logger.info("%s Redis 缓存连接成功：%s", CACHE_LOG_PREFIX, redis_url)
        return _redis_client
    except Exception as exc:  # noqa: BLE001
        _mark_redis_unavailable(exc)
        return None


def reset_redis_client() -> None:
    """
    重置 Redis 客户端单例，供测试隔离使用。
    """
    global _redis_client, _redis_retry_after_monotonic
    _redis_client = None
    _redis_retry_after_monotonic = 0.0


def get_json(key: str) -> dict[str, Any] | list[Any] | None:
    """
    读取 JSON 值；读失败或不存在时返回 None。
    """
    client = get_redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s Redis 读取失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
        _mark_redis_unavailable(exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        logger.warning("%s Redis 缓存 JSON 无法解析 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
        return None


def set_json(key: str, value: dict[str, Any] | list[Any], ttl_seconds: int) -> bool:
    """
    写入 JSON 值并设置固定 TTL；写失败返回 False。
    """
    client = get_redis()
    if client is None:
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        logger.warning("%s Redis 缓存 JSON 序列化失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
        return False
    try:
        client.setex(key, max(1, ttl_seconds), payload)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s Redis 写入失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
        _mark_redis_unavailable(exc)
        return False


def delete(key: str) -> None:
    """
    删除单个 key；失败时仅记录 warn。
    """
    client = get_redis()
    if client is None:
        return
    try:
        client.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s Redis 删除失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
        _mark_redis_unavailable(exc)


def delete_by_pattern(pattern: str) -> int:
    """
    按 pattern SCAN 删除匹配 key，返回删除数量。
    """
    client = get_redis()
    if client is None:
        return 0
    deleted = 0
    try:
        for key in client.scan_iter(match=pattern, count=100):
            client.delete(key)
            deleted += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s Redis 批量删除失败 pattern=%s：%s", CACHE_LOG_PREFIX, pattern, exc)
        _mark_redis_unavailable(exc)
    return deleted
