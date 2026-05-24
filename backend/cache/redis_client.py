"""
Redis 缓存客户端：连接、JSON 读写、TTL 续期与异常降级。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis


CACHE_LOG_PREFIX = "[DisputeCache]"
logger = logging.getLogger(__name__)
_redis_client: redis.Redis | None = None
_redis_connect_failed: bool = False


def is_redis_cache_enabled() -> bool:
    """
    读取 ENABLE_REDIS_CACHE 开关，默认启用。
    """
    raw = os.getenv("ENABLE_REDIS_CACHE", "1").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


def get_cache_ttl_seconds() -> int:
    """
    读取 DISPUTE_CACHE_TTL_SECONDS，默认 86400 秒。
    """
    raw = os.getenv("DISPUTE_CACHE_TTL_SECONDS", "86400").strip()
    try:
        ttl = int(raw)
    except ValueError:
        logger.warning("%s DISPUTE_CACHE_TTL_SECONDS 无效：%s，回退 86400", CACHE_LOG_PREFIX, raw)
        return 86400
    return max(60, ttl)


def get_agent_cache_version() -> str:
    """
    读取 AGENT_CACHE_VERSION，用于 B/C 层 key 后缀。
    """
    version = os.getenv("AGENT_CACHE_VERSION", "1").strip()
    return version or "1"


def get_redis() -> redis.Redis | None:
    """
    获取 Redis 客户端；未启用缓存或连接失败时返回 None（失败只尝试一次，避免重复打日志）。
    """
    global _redis_client, _redis_connect_failed
    if not is_redis_cache_enabled():
        return None
    if _redis_client is not None:
        return _redis_client
    if _redis_connect_failed:
        return None
    redis_url = os.getenv("REDIS_CACHE_URL", "redis://localhost:6379/1").strip()
    if not redis_url:
        logger.warning("%s REDIS_CACHE_URL 为空，跳过 Redis 缓存", CACHE_LOG_PREFIX)
        _redis_connect_failed = True
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
        _redis_connect_failed = True
        logger.warning(
            "%s Redis 连接失败，已降级为进程内材料缓存（B/C 层不写入 Redis）：%s。"
            "请启动 Redis 或于 .env 设置 ENABLE_REDIS_CACHE=0",
            CACHE_LOG_PREFIX,
            exc,
        )
        return None


def reset_redis_client() -> None:
    """
    重置 Redis 客户端单例，供测试隔离使用。
    """
    global _redis_client, _redis_connect_failed
    _redis_client = None
    _redis_connect_failed = False


def get_json(key: str) -> dict[str, Any] | list[Any] | None:
    """
    读取 JSON 值；读失败或不存在时返回 None。
    """
    client = get_redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if raw is None:
            return None
        refresh_ttl(key)
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s Redis 读取失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
        return None


def set_json(key: str, value: dict[str, Any] | list[Any]) -> bool:
    """
    写入 JSON 值并设置 TTL；写失败返回 False。
    """
    client = get_redis()
    if client is None:
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
        client.setex(key, get_cache_ttl_seconds(), payload)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s Redis 写入失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
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
    return deleted


def refresh_ttl(key: str) -> None:
    """
    命中缓存时续期 TTL（滑动过期）。
    """
    client = get_redis()
    if client is None:
        return
    try:
        client.expire(key, get_cache_ttl_seconds())
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s Redis TTL 续期失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
