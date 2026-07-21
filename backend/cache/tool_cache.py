"""
工具查询缓存（T 层）：买家画像、相似判例、物流状态的 Redis 缓存。
"""

from __future__ import annotations

import hashlib
import logging

from backend.cache.fingerprint import hash_cache_identifier, merchant_cache_scope
from backend.cache.layer_redis import get_layer_json, read_env_ttl_seconds, set_layer_json
from backend.cache.redis_client import CACHE_LOG_PREFIX, delete_by_pattern
from schemas import BuyerProfile, LogisticsInfo, SimilarCase


logger = logging.getLogger(__name__)

_PROFILE_TTL_ENV = "PROFILE_CACHE_TTL_SECONDS"
_PROFILE_TTL_DEFAULT = 21600
_CASES_TTL_ENV = "CASES_CACHE_TTL_SECONDS"
_CASES_TTL_DEFAULT = 21600
_CASES_EMPTY_TTL_ENV = "CASES_EMPTY_CACHE_TTL_SECONDS"
_CASES_EMPTY_TTL_DEFAULT = 60
_LOGISTICS_TTL_ENV = "LOGISTICS_CACHE_TTL_SECONDS"
_LOGISTICS_TTL_DEFAULT = 900


def _fp(text: str) -> str:
    """
    对文本做 SHA256，取前 16 位 hex，用于缩短 Redis key。
    """
    normalized = str(text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _profile_key(merchant_id: str, buyer_id: str) -> str:
    """
    生成买家画像缓存键，分别哈希租户与买家标识以避免明文暴露。
    """
    return f"ea:v2:t:profile:{merchant_cache_scope(merchant_id)}:{hash_cache_identifier(buyer_id)}"


def _cases_key(merchant_id: str, dispute_desc: str, top_k: int) -> str:
    """
    生成相似判例缓存键，检索文本和商家标识均只以哈希形式出现。
    """
    return f"ea:v2:t:cases:{merchant_cache_scope(merchant_id)}:{hash_cache_identifier(dispute_desc)}:{top_k}"


def _logistics_key(merchant_id: str, order_id: str) -> str:
    """
    生成物流缓存键，避免 Redis key 暴露订单编号。
    """
    return f"ea:v2:t:logistics:{merchant_cache_scope(merchant_id)}:{hash_cache_identifier(order_id)}"


def clear_tool_caches() -> None:
    """
    清理全部 T 层与 V 层工具 key（不含 A/B/C 纠纷缓存）。
    """
    delete_by_pattern("ea:v2:v:*")
    delete_by_pattern("ea:v2:t:*")


# ---------- 买家画像 ----------


def get_cached_profile(merchant_id: str, buyer_id: str) -> BuyerProfile | None:
    """
    读取买家画像缓存；merchant_id 或 buyer_id 为空、miss 或反序列化失败时返回 None。
    """
    normalized_merchant = merchant_id.strip()
    normalized_buyer = buyer_id.strip()
    if not normalized_merchant or not normalized_buyer:
        return None
    key = _profile_key(normalized_merchant, normalized_buyer)
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
    key = _profile_key(normalized_merchant, normalized_buyer)
    ttl = read_env_ttl_seconds(_PROFILE_TTL_ENV, _PROFILE_TTL_DEFAULT)
    if set_layer_json(key, profile.model_dump(), ttl):
        logger.info(
            "%s T 层画像写入成功：merchant_id=%s buyer_id=%s",
            CACHE_LOG_PREFIX,
            normalized_merchant,
            normalized_buyer,
        )


# ---------- 相似判例 ----------


def get_cached_cases(merchant_id: str, dispute_desc: str, top_k: int) -> list[SimilarCase] | None:
    """
    读取判例检索缓存；商家、描述为空、miss 或反序列化失败时返回 None。
    """
    normalized_merchant = merchant_id.strip()
    normalized_desc = dispute_desc.strip()
    if not normalized_merchant or not normalized_desc or top_k <= 0:
        return None
    key = _cases_key(normalized_merchant, normalized_desc, top_k)
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


def save_cases(merchant_id: str, dispute_desc: str, top_k: int, cases: list[SimilarCase]) -> None:
    """
    写入判例检索缓存；空结果使用短 TTL，查询异常不得调用本函数。
    """
    normalized_merchant = merchant_id.strip()
    normalized_desc = dispute_desc.strip()
    if not normalized_merchant or not normalized_desc or top_k <= 0:
        return
    key = _cases_key(normalized_merchant, normalized_desc, top_k)
    ttl_env = _CASES_EMPTY_TTL_ENV if not cases else _CASES_TTL_ENV
    ttl_default = _CASES_EMPTY_TTL_DEFAULT if not cases else _CASES_TTL_DEFAULT
    ttl = read_env_ttl_seconds(ttl_env, ttl_default)
    payload = [case.model_dump() for case in cases]
    if set_layer_json(key, payload, ttl):
        logger.info("%s T 层判例写入成功：desc_fp=%s top_k=%s", CACHE_LOG_PREFIX, _fp(normalized_desc), top_k)


# ---------- 物流 ----------


def get_cached_logistics(merchant_id: str, order_id: str) -> LogisticsInfo | None:
    """
    读取物流缓存；merchant_id/order_id 为空、miss 或反序列化失败时返回 None。
    """
    normalized_merchant = merchant_id.strip()
    normalized_order = order_id.strip()
    if not normalized_merchant or not normalized_order:
        return None
    key = _logistics_key(normalized_merchant, normalized_order)
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


def save_logistics(merchant_id: str, order_id: str, logistics: LogisticsInfo) -> None:
    """
    写入物流缓存；仅真实平台成功结果可调用，避免缓存异常占位状态。
    """
    normalized_merchant = merchant_id.strip()
    normalized_order = order_id.strip()
    if not normalized_merchant or not normalized_order:
        return
    key = _logistics_key(normalized_merchant, normalized_order)
    ttl = read_env_ttl_seconds(_LOGISTICS_TTL_ENV, _LOGISTICS_TTL_DEFAULT)
    if set_layer_json(key, logistics.model_dump(), ttl):
        logger.info("%s T 层物流写入成功：order_id=%s", CACHE_LOG_PREFIX, normalized_order)
