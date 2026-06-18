"""
纠纷材料存储（A 层）：Redis 主存 + 进程内 fallback，支持增量合并。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from backend.cache.helpers import as_list
from backend.cache.redis_client import CACHE_LOG_PREFIX, delete, delete_by_pattern, get_json, set_json


logger = logging.getLogger(__name__)
_LOCAL_CACHE: dict[str, dict[str, Any]] = {}


def _materials_key(dispute_id: str) -> str:
    """
    生成材料层 Redis key。
    """
    return f"ea:materials:{dispute_id}"


def _dedupe_preserve_order(items: list[Any]) -> list[Any]:
    """
    对列表按出现顺序去重，支持字典与基础类型混合元素。
    """
    deduped: list[Any] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _load_local(dispute_id: str) -> dict[str, Any] | None:
    """
    从进程内 fallback 读取材料。
    """
    cached = _LOCAL_CACHE.get(dispute_id)
    if cached is None:
        return None
    return deepcopy(cached)


def _save_local(dispute_id: str, materials: dict[str, Any]) -> None:
    """
    写入进程内 fallback。
    """
    _LOCAL_CACHE[dispute_id] = deepcopy(materials)


def _load_redis(dispute_id: str) -> dict[str, Any] | None:
    """
    从 Redis 读取材料；失败时返回 None。
    """
    payload = get_json(_materials_key(dispute_id))
    if isinstance(payload, dict):
        return payload
    return None


def _save_redis(dispute_id: str, materials: dict[str, Any]) -> None:
    """
    写入 Redis 材料层；失败时仅 warn。
    """
    if not set_json(_materials_key(dispute_id), materials):
        logger.warning("%s 材料层 Redis 写入失败：dispute_id=%s", CACHE_LOG_PREFIX, dispute_id)


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


def load_materials(dispute_id: str) -> dict[str, Any] | None:
    """
    读取已缓存的纠纷材料（Redis 优先，进程内 fallback）。
    """
    cached = _load_redis(dispute_id)
    if cached is None:
        cached = _load_local(dispute_id)
    return deepcopy(cached) if cached else None


def merge_materials(dispute_id: str, new_materials: dict[str, Any]) -> dict[str, Any]:
    """
    合并纠纷材料：Redis 优先，进程内 fallback；返回合并结果的副本。
    """
    if bool(new_materials.get("reset_context")):
        merged_materials = deepcopy(new_materials)
        for meta_key in ("reset_context", "materials_snapshot"):
            merged_materials.pop(meta_key, None)
        _save_local(dispute_id, merged_materials)
        _save_redis(dispute_id, merged_materials)
        return deepcopy(merged_materials)

    snapshot = bool(new_materials.get("materials_snapshot", True))

    cached_materials = _load_redis(dispute_id)
    if cached_materials is None:
        cached_materials = _load_local(dispute_id)

    if cached_materials is None:
        merged_materials = deepcopy(new_materials)
    else:
        merged_materials = _merge_dicts(cached_materials, new_materials, snapshot=snapshot)

    for meta_key in ("reset_context", "materials_snapshot"):
        merged_materials.pop(meta_key, None)

    _save_local(dispute_id, merged_materials)
    _save_redis(dispute_id, merged_materials)
    return deepcopy(merged_materials)


def clear_materials_cache(dispute_id: str | None = None) -> None:
    """
    清理材料层缓存（本地 + Redis）。
    """
    if dispute_id is None:
        _LOCAL_CACHE.clear()
        delete_by_pattern("ea:materials:*")
        return

    _LOCAL_CACHE.pop(dispute_id, None)
    delete(_materials_key(dispute_id))


def clear_dispute_cache(dispute_id: str) -> None:
    """
    清理指定纠纷的全部缓存层（材料 + B/C 层 pattern）。
    """
    clear_materials_cache(dispute_id)
    delete_by_pattern(f"ea:facts:{dispute_id}:*")
    delete_by_pattern(f"ea:report:{dispute_id}:*")
    logger.info("%s 已清理纠纷缓存：dispute_id=%s", CACHE_LOG_PREFIX, dispute_id)


def clear_all_cache() -> None:
    """
    清理全部纠纷缓存，供测试或手工重置。
    """
    _LOCAL_CACHE.clear()
    delete_by_pattern("ea:materials:*")
    delete_by_pattern("ea:facts:*")
    delete_by_pattern("ea:report:*")
