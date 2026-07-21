"""
分析结果缓存（B/C 层）：Agent1 事实与整报告读写。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.cache.fingerprint import (
    compute_fp_agent1,
    compute_fp_report,
    hash_cache_identifier,
    merchant_cache_scope,
)
from backend.cache.layer_redis import read_env_ttl_seconds
from backend.cache.redis_client import (
    CACHE_LOG_PREFIX,
    get_agent_cache_version,
    get_json,
    set_json,
)
from schemas import AnalysisReport, FactOutput


logger = logging.getLogger(__name__)
_FACTS_TTL_ENV = "FACTS_CACHE_TTL_SECONDS"
_FACTS_TTL_DEFAULT = 86400
_REPORT_TTL_ENV = "REPORT_CACHE_TTL_SECONDS"
_REPORT_TTL_DEFAULT = 86400


def _facts_key(merchant_id: str, dispute_id: str, fp_agent1: str) -> str:
    """
    生成 Agent1 事实层 Redis key，按商家隔离且不暴露原始纠纷标识。
    """
    version = get_agent_cache_version()
    return f"ea:v2:b:{merchant_cache_scope(merchant_id)}:{hash_cache_identifier(dispute_id)}:{fp_agent1}:v{version}"


def _report_key(merchant_id: str, dispute_id: str, fp_report: str) -> str:
    """
    生成整报告层 Redis key，按商家隔离且不暴露原始纠纷标识。
    """
    version = get_agent_cache_version()
    return f"ea:v2:c:{merchant_cache_scope(merchant_id)}:{hash_cache_identifier(dispute_id)}:{fp_report}:v{version}"


def get_cached_facts(merchant_id: str, dispute_id: str, materials: dict[str, Any]) -> FactOutput | None:
    """
    读取 B 层缓存；未启用 Redis 或 miss 时返回 None。
    """
    fp_agent1 = compute_fp_agent1(materials)
    payload = get_json(_facts_key(merchant_id, dispute_id, fp_agent1))
    if not isinstance(payload, dict):
        return None
    try:
        facts = FactOutput.model_validate(payload)
        logger.info(
            "%s B 层命中：dispute_id=%s fp_agent1=%s",
            CACHE_LOG_PREFIX,
            dispute_id,
            fp_agent1,
        )
        return facts
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s B 层反序列化失败：dispute_id=%s fp_agent1=%s 原因=%s",
            CACHE_LOG_PREFIX,
            dispute_id,
            fp_agent1,
            exc,
        )
        return None


def save_facts(merchant_id: str, dispute_id: str, materials: dict[str, Any], facts: FactOutput) -> None:
    """
    写入 B 层缓存；失败时仅 warn。
    """
    fp_agent1 = compute_fp_agent1(materials)
    ttl = read_env_ttl_seconds(_FACTS_TTL_ENV, _FACTS_TTL_DEFAULT)
    if set_json(_facts_key(merchant_id, dispute_id, fp_agent1), facts.model_dump(), ttl):
        logger.info(
            "%s B 层写入成功：dispute_id=%s fp_agent1=%s",
            CACHE_LOG_PREFIX,
            dispute_id,
            fp_agent1,
        )


def get_cached_report(merchant_id: str, dispute_id: str, materials: dict[str, Any]) -> AnalysisReport | None:
    """
    读取 C 层缓存；未启用 Redis 或 miss 时返回 None。
    """
    fp_report = compute_fp_report(materials)
    payload = get_json(_report_key(merchant_id, dispute_id, fp_report))
    if not isinstance(payload, dict):
        return None
    try:
        report = AnalysisReport.model_validate(payload)
        logger.info(
            "%s C 层命中：dispute_id=%s fp_report=%s",
            CACHE_LOG_PREFIX,
            dispute_id,
            fp_report,
        )
        return report
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s C 层反序列化失败：dispute_id=%s fp_report=%s 原因=%s",
            CACHE_LOG_PREFIX,
            dispute_id,
            fp_report,
            exc,
        )
        return None


def save_report(merchant_id: str, dispute_id: str, materials: dict[str, Any], report: AnalysisReport) -> None:
    """
    写入 C 层缓存；失败时仅 warn。
    """
    fp_report = compute_fp_report(materials)
    ttl = read_env_ttl_seconds(_REPORT_TTL_ENV, _REPORT_TTL_DEFAULT)
    if set_json(_report_key(merchant_id, dispute_id, fp_report), report.model_dump(), ttl):
        logger.info(
            "%s C 层写入成功：dispute_id=%s fp_report=%s",
            CACHE_LOG_PREFIX,
            dispute_id,
            fp_report,
        )
