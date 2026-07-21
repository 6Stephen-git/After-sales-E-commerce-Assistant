"""
纠纷缓存包：对外统一导出材料层与结果层接口。
"""

from backend.cache.fingerprint import (
    compute_fp_agent1,
    compute_fp_report,
    hash_cache_identifier,
    merchant_cache_scope,
)
from backend.cache.materials_store import (
    clear_all_cache,
    clear_dispute_cache,
    clear_materials_cache,
    load_materials,
    merge_materials,
)
from backend.cache.redis_client import is_redis_cache_enabled, reset_redis_client
from backend.cache.result_cache import get_cached_facts, get_cached_report, save_facts, save_report

__all__ = [
    "clear_all_cache",
    "clear_dispute_cache",
    "clear_materials_cache",
    "compute_fp_agent1",
    "compute_fp_report",
    "get_cached_facts",
    "get_cached_report",
    "is_redis_cache_enabled",
    "hash_cache_identifier",
    "load_materials",
    "merge_materials",
    "merchant_cache_scope",
    "reset_redis_client",
    "save_facts",
    "save_report",
]
