"""
纠纷材料存储（A 层）：Redis 主存 + 进程内 fallback，支持增量合并。
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from typing import Any

from backend.cache.fingerprint import hash_cache_identifier, merchant_cache_scope
from backend.cache.helpers import as_list
from backend.cache.layer_redis import read_env_ttl_seconds
from backend.cache.redis_client import CACHE_LOG_PREFIX, delete, delete_by_pattern, get_json, get_redis, set_json


logger = logging.getLogger(__name__)
_MATERIALS_TTL_ENV = "MATERIALS_CACHE_TTL_SECONDS"
_MATERIALS_TTL_DEFAULT = 86400
_LOCAL_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def _materials_key(merchant_id: str, dispute_id: str) -> str:
    """
    生成材料层 Redis key，按商家隔离且不暴露原始业务标识。
    """
    return f"ea:v2:a:{merchant_cache_scope(merchant_id)}:{hash_cache_identifier(dispute_id)}"


def _local_key(merchant_id: str, dispute_id: str) -> tuple[str, str]:
    """
    生成进程内降级缓存键，保持与 Redis 相同的商家、纠纷隔离维度。
    """
    return merchant_id, dispute_id


def _dedupe_preserve_order(items: list[Any]) -> list[Any]:
    """
    对列表按出现顺序去重，支持字典与基础类型混合元素。
    """
    deduped: list[Any] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _load_local(merchant_id: str, dispute_id: str) -> dict[str, Any] | None:
    """
    从进程内 fallback 读取材料。
    """
    cached = _LOCAL_CACHE.get(_local_key(merchant_id, dispute_id))
    if cached is None:
        return None
    expires_at, materials = cached
    if time.monotonic() >= expires_at:
        _LOCAL_CACHE.pop(_local_key(merchant_id, dispute_id), None)
        return None
    return deepcopy(materials)


def _save_local(merchant_id: str, dispute_id: str, materials: dict[str, Any]) -> None:
    """
    写入进程内 fallback。
    """
    ttl = read_env_ttl_seconds(_MATERIALS_TTL_ENV, _MATERIALS_TTL_DEFAULT)
    _LOCAL_CACHE[_local_key(merchant_id, dispute_id)] = (time.monotonic() + ttl, deepcopy(materials))


def _load_redis(merchant_id: str, dispute_id: str) -> dict[str, Any] | None:
    """
    从 Redis 读取材料；失败时返回 None。
    """
    payload = get_json(_materials_key(merchant_id, dispute_id))
    if isinstance(payload, dict):
        return payload
    return None


def _save_redis(merchant_id: str, dispute_id: str, materials: dict[str, Any]) -> bool:
    """
    写入 Redis 材料层；失败时返回 False，由调用方写入进程内降级缓存。
    """
    ttl = read_env_ttl_seconds(_MATERIALS_TTL_ENV, _MATERIALS_TTL_DEFAULT)
    if set_json(_materials_key(merchant_id, dispute_id), materials, ttl):
        return True
    logger.warning("%s 材料层 Redis 写入失败，已降级进程内缓存：dispute_id=%s", CACHE_LOG_PREFIX, dispute_id)
    return False


def _load_materials(merchant_id: str, dispute_id: str) -> dict[str, Any] | None:
    """
    Redis 可用时仅信任 Redis；不可用时才读取带 TTL 的进程内降级材料。
    """
    if get_redis() is None:
        return _load_local(merchant_id, dispute_id)
    cached = _load_redis(merchant_id, dispute_id)
    if cached is not None or get_redis() is not None:
        return cached
    return _load_local(merchant_id, dispute_id)


def _save_materials(merchant_id: str, dispute_id: str, materials: dict[str, Any]) -> None:
    """
    优先写 Redis；写入失败时才短暂保留进程内材料，恢复 Redis 后清除本地副本。
    """
    if _save_redis(merchant_id, dispute_id, materials):
        _LOCAL_CACHE.pop(_local_key(merchant_id, dispute_id), None)
        return
    _save_local(merchant_id, dispute_id, materials)


def _merge_dicts(
    cached_materials: dict[str, Any],
    new_materials: dict[str, Any],
    *,
    snapshot: bool,
) -> dict[str, Any]:
    """
    将新材料合并进已有材料：snapshot 模式下 chat/image 整包覆盖，否则增量去重追加。
    """
    merged_materials = deepcopy(cached_materials)
    skip_keys = {"reset_context", "materials_snapshot"}
    for key, value in new_materials.items():
        if key in skip_keys:
            continue
        if key in {"chat_history", "image_urls"}:
            if snapshot:
                merged_materials[key] = deepcopy(as_list(value))
            else:
                old_items = as_list(merged_materials.get(key))
                new_items = as_list(value)
                merged_materials[key] = _dedupe_preserve_order(old_items + new_items)
        else:
            merged_materials[key] = deepcopy(value)
    return merged_materials


def load_materials(merchant_id: str, dispute_id: str) -> dict[str, Any] | None:
    """
    读取已缓存的纠纷材料（Redis 可用时只读 Redis，故障时进程内 fallback）。
    """
    cached = _load_materials(merchant_id, dispute_id)
    return deepcopy(cached) if cached else None


def merge_materials(merchant_id: str, dispute_id: str, new_materials: dict[str, Any]) -> dict[str, Any]:
    """
    合并纠纷材料：Redis 可用时以 Redis 为准，故障时使用进程内降级副本。
    """
    if bool(new_materials.get("reset_context")):
        merged_materials = deepcopy(new_materials)
        for meta_key in ("reset_context", "materials_snapshot"):
            merged_materials.pop(meta_key, None)
        _save_materials(merchant_id, dispute_id, merged_materials)
        return deepcopy(merged_materials)

    snapshot = bool(new_materials.get("materials_snapshot", True))

    cached_materials = _load_materials(merchant_id, dispute_id)

    if cached_materials is None:
        merged_materials = deepcopy(new_materials)
    else:
        merged_materials = _merge_dicts(cached_materials, new_materials, snapshot=snapshot)

    for meta_key in ("reset_context", "materials_snapshot"):
        merged_materials.pop(meta_key, None)

    _save_materials(merchant_id, dispute_id, merged_materials)
    return deepcopy(merged_materials)


def clear_materials_cache(merchant_id: str | None = None, dispute_id: str | None = None) -> None:
    """
    清理材料层缓存（本地 + Redis）。
    """
    if merchant_id is None and dispute_id is None:
        _LOCAL_CACHE.clear()
        delete_by_pattern("ea:v2:a:*")
        return

    if not merchant_id or not dispute_id:
        raise ValueError("清理指定材料缓存时 merchant_id 与 dispute_id 必须同时提供")
    _LOCAL_CACHE.pop(_local_key(merchant_id, dispute_id), None)
    delete(_materials_key(merchant_id, dispute_id))


def clear_dispute_cache(merchant_id: str, dispute_id: str) -> None:
    """
    清理指定纠纷的全部缓存层（材料 + B/C 层 pattern）。
    """
    scope = merchant_cache_scope(merchant_id)
    dispute_scope = hash_cache_identifier(dispute_id)
    clear_materials_cache(merchant_id, dispute_id)
    delete_by_pattern(f"ea:v2:b:{scope}:{dispute_scope}:*")
    delete_by_pattern(f"ea:v2:c:{scope}:{dispute_scope}:*")
    logger.info("%s 已清理纠纷缓存：dispute_id=%s", CACHE_LOG_PREFIX, dispute_id)


def clear_all_cache() -> None:
    """
    清理全部纠纷缓存，供测试或手工重置。
    """
    _LOCAL_CACHE.clear()
    delete_by_pattern("ea:v2:a:*")
    delete_by_pattern("ea:v2:b:*")
    delete_by_pattern("ea:v2:c:*")
