"""
工具查询缓存（T 层）：买家画像、相似判例、物流状态的 Redis 缓存。
"""

from __future__ import annotations

import hashlib
import logging

from backend.cache.layer_redis import get_layer_json, read_env_ttl_seconds, set_layer_json
from backend.cache.redis_client import CACHE_LOG_PREFIX, delete_by_pattern
from schemas import BuyerProfile, LogisticsInfo, SimilarCase


logger = logging.getLogger(__name__)

_PROFILE_TTL_ENV = "PROFILE_CACHE_TTL_SECONDS"
_PROFILE_TTL_DEFAULT = 86400
_CASES_TTL_ENV = "CASES_CACHE_TTL_SECONDS"
_CASES_TTL_DEFAULT = 86400
_LOGISTICS_TTL_ENV = "LOGISTICS_CACHE_TTL_SECONDS"
_LOGISTICS_TTL_DEFAULT = 900


def _fp(text: str) -> str:
    """
    对文本做 SHA256，取前 16 位 hex，用于缩短 Redis key。
    """
    normalized = str(text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def clear_tool_caches() -> None:
    """
    清理全部 T 层与 V 层工具 key（不含 A/B/C 纠纷缓存）。
    """
    delete_by_pattern("ea:vision:*")
    delete_by_pattern("ea:profile:*")
    delete_by_pattern("ea:cases:*")
    delete_by_pattern("ea:logistics:*")


# ---------- 买家画像 ----------


def get_cached_profile(merchant_id: str, buyer_id: str) -> BuyerProfile | None:
    """
    读取买家画像缓存；merchant_id 或 buyer_id 为空、miss 或反序列化失败时返回 None。
    """
    normalized_merchant = merchant_id.strip()
    normalized_buyer = buyer_id.strip()
    if not normalized_merchant or not normalized_buyer:
        return None
    key = f"ea:profile:{normalized_merchant}:{normalized_buyer}"
    payload = get_layer_json(key)
    if not isinstance(payload, dict):
        return None
    try:
        profile = BuyerProfile.model_validate(payload)
        logger.info(
            "%s T 层画像命中：merchant_id=%s buyer_id=%s",
            CACHE_LOG_PREFIX,
            normalized_merchant,
            normalized_buyer,
        )
        return profile
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s T 层画像反序列化失败：merchant_id=%s buyer_id=%s 原因=%s",
            CACHE_LOG_PREFIX,
            normalized_merchant,
            normalized_buyer,
            exc,
        )
        return None


def save_profile(merchant_id: str, buyer_id: str, profile: BuyerProfile) -> None:
    """
    写入买家画像缓存；标识为空或写入失败时静默跳过。
    """
    normalized_merchant = merchant_id.strip()
    normalized_buyer = buyer_id.strip()
    if not normalized_merchant or not normalized_buyer:
        return
    key = f"ea:profile:{normalized_merchant}:{normalized_buyer}"
    ttl = read_env_ttl_seconds(_PROFILE_TTL_ENV, _PROFILE_TTL_DEFAULT)
    if set_layer_json(key, profile.model_dump(), ttl):
        logger.info(
            "%s T 层画像写入成功：merchant_id=%s buyer_id=%s",
            CACHE_LOG_PREFIX,
            normalized_merchant,
            normalized_buyer,
        )


# ---------- 相似判例 ----------


def get_cached_cases(dispute_desc: str, top_k: int) -> list[SimilarCase] | None:
    """
    读取判例检索缓存；描述为空、miss 或反序列化失败时返回 None。
    """
    normalized_desc = dispute_desc.strip()
    if not normalized_desc or top_k <= 0:
        return None
    key = f"ea:cases:{_fp(normalized_desc)}:{top_k}"
    payload = get_layer_json(key)
    if not isinstance(payload, list):
        return None
    try:
        cases = [SimilarCase.model_validate(item) for item in payload if isinstance(item, dict)]
        logger.info("%s T 层判例命中：desc_fp=%s top_k=%s", CACHE_LOG_PREFIX, _fp(normalized_desc), top_k)
        return cases
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s T 层判例反序列化失败：desc_fp=%s top_k=%s 原因=%s",
            CACHE_LOG_PREFIX,
            _fp(normalized_desc),
            top_k,
            exc,
        )
        return None


def save_cases(dispute_desc: str, top_k: int, cases: list[SimilarCase]) -> None:
    """
    写入判例检索缓存；描述为空或写入失败时静默跳过。
    """
    normalized_desc = dispute_desc.strip()
    if not normalized_desc or top_k <= 0:
        return
    key = f"ea:cases:{_fp(normalized_desc)}:{top_k}"
    ttl = read_env_ttl_seconds(_CASES_TTL_ENV, _CASES_TTL_DEFAULT)
    payload = [case.model_dump() for case in cases]
    if set_layer_json(key, payload, ttl):
        logger.info("%s T 层判例写入成功：desc_fp=%s top_k=%s", CACHE_LOG_PREFIX, _fp(normalized_desc), top_k)


# ---------- 物流 ----------


def get_cached_logistics(order_id: str) -> LogisticsInfo | None:
    """
    读取物流缓存；order_id 为空、miss 或反序列化失败时返回 None。
    """
    normalized_order = order_id.strip()
    if not normalized_order:
        return None
    key = f"ea:logistics:{normalized_order}"
    payload = get_layer_json(key)
    if not isinstance(payload, dict):
        return None
    try:
        logistics = LogisticsInfo.model_validate(payload)
        logger.info("%s T 层物流命中：order_id=%s", CACHE_LOG_PREFIX, normalized_order)
        return logistics
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s T 层物流反序列化失败：order_id=%s 原因=%s",
            CACHE_LOG_PREFIX,
            normalized_order,
            exc,
        )
        return None


def save_logistics(order_id: str, logistics: LogisticsInfo) -> None:
    """
    写入物流缓存；order_id 为空或写入失败时静默跳过。
    """
    normalized_order = order_id.strip()
    if not normalized_order:
        return
    key = f"ea:logistics:{normalized_order}"
    ttl = read_env_ttl_seconds(_LOGISTICS_TTL_ENV, _LOGISTICS_TTL_DEFAULT)
    if set_layer_json(key, logistics.model_dump(), ttl):
        logger.info("%s T 层物流写入成功：order_id=%s", CACHE_LOG_PREFIX, normalized_order)
