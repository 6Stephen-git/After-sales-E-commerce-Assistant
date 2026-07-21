"""
缓存层 Redis 读写：固定 TTL、读时不续期。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from backend.cache.redis_client import CACHE_LOG_PREFIX, get_json, set_json


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
    return get_json(key)


def set_layer_json(key: str, value: dict[str, Any] | list[Any], ttl_seconds: int) -> bool:
    """
    写入 V/T 层 JSON 并设置固定 TTL；写失败返回 False。
    """
    return set_json(key, value, max(60, ttl_seconds))
