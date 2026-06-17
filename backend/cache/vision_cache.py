"""
视觉分析缓存（V 层）：按图片引用 + 模型 + guidance 缓存 analyze_image 成功结果。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from backend.cache.layer_redis import get_layer_json, read_env_ttl_seconds, set_layer_json
from backend.cache.redis_client import CACHE_LOG_PREFIX, delete_by_pattern


logger = logging.getLogger(__name__)
_VISION_TTL_ENV = "VISION_CACHE_TTL_SECONDS"
_VISION_TTL_DEFAULT = 604800


def _fp(text: str) -> str:
    """
    对文本做 SHA256，取前 16 位 hex，用于缩短 Redis key。
    """
    normalized = str(text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _vision_key(image_ref: str, model: str, guidance: str) -> str:
    """
    生成 V 层 Redis key。
    """
    guidance_fp = _fp(guidance) if guidance.strip() else "_"
    return f"ea:vision:{_fp(image_ref)}:{model}:{guidance_fp}"


def _is_cacheable_result(payload: dict[str, Any]) -> bool:
    """
    仅缓存无 error 字段的成功视觉分析结果。
    """
    return isinstance(payload, dict) and "error" not in payload


def get_cached_vision(image_ref: str, model: str, guidance: str = "") -> dict[str, Any] | None:
    """
    读取 V 层缓存；miss 或结构非 dict 时返回 None。
    """
    key = _vision_key(image_ref, model, guidance)
    payload = get_layer_json(key)
    if not isinstance(payload, dict) or not _is_cacheable_result(payload):
        return None
    logger.info("%s V 层命中：model=%s image_fp=%s", CACHE_LOG_PREFIX, model, _fp(image_ref))
    return payload


def save_vision(image_ref: str, model: str, guidance: str, result: dict[str, Any]) -> None:
    """
    写入 V 层缓存；非成功结果或写入失败时静默跳过。
    """
    if not _is_cacheable_result(result):
        return
    key = _vision_key(image_ref, model, guidance)
    ttl = read_env_ttl_seconds(_VISION_TTL_ENV, _VISION_TTL_DEFAULT)
    if set_layer_json(key, result, ttl):
        logger.info("%s V 层写入成功：model=%s image_fp=%s", CACHE_LOG_PREFIX, model, _fp(image_ref))


def clear_vision_cache() -> int:
    """
    清理全部 V 层 key，供测试或手工重置；返回删除数量。
    """
    return delete_by_pattern("ea:vision:*")
