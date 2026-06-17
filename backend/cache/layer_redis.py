"""
V/T 层 Redis 读写：固定 TTL、读时不续期（与 A/B/C 的 get_json/set_json 分离）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from backend.cache.redis_client import CACHE_LOG_PREFIX, get_redis, is_redis_cache_enabled


logger = logging.getLogger(__name__)


def read_env_ttl_seconds(env_name: str, default: int, *, minimum: int = 60) -> int:
    """
    读取环境变量中的 TTL 秒数；无效时回退 default，并保证不小于 minimum。
    """
    raw = os.getenv(env_name, str(default)).strip()
    try:
        ttl = int(raw)
    except ValueError:
        logger.warning("%s %s 无效：%s，回退 %s", CACHE_LOG_PREFIX, env_name, raw, default)
        return max(minimum, default)
    return max(minimum, ttl)


def get_layer_json(key: str) -> dict[str, Any] | list[Any] | None:
    """
    读取 V/T 层 JSON；未启用缓存、不存在或解析失败时返回 None（不续期 TTL）。
    """
    if not is_redis_cache_enabled():
        return None
    client = get_redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s V/T 层读取失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
        return None


def set_layer_json(key: str, value: dict[str, Any] | list[Any], ttl_seconds: int) -> bool:
    """
    写入 V/T 层 JSON 并设置固定 TTL；写失败返回 False。
    """
    if not is_redis_cache_enabled():
        return False
    client = get_redis()
    if client is None:
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
        client.setex(key, max(60, ttl_seconds), payload)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s V/T 层写入失败 key=%s：%s", CACHE_LOG_PREFIX, key, exc)
        return False
