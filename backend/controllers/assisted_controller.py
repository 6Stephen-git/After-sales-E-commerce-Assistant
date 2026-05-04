"""
辅助模式控制器：负责串联 Agent1 -> Agent2 -> Agent3，并维护纠纷上下文缓存。

说明：材料缓存为进程内字典，MVP 阶段足够；多实例部署时需改为 Redis 等共享存储。
"""

from __future__ import annotations

# ---------- 标准库与类型 ----------
import logging
from copy import deepcopy
from typing import Any

from backend.agents.agent1 import extract
from backend.agents.agent2 import recommend
from backend.agents.agent3 import generate
from backend.tools.agent2_tools import match_rules, query_buyer_profile, search_similar_cases
from schemas import AnalysisReport, ScriptInput, StrategyInput

# ---------- 日志前缀与纠纷材料缓存（按 dispute_id） ----------
ASSISTED_LOG_PREFIX = "[AssistedController]"
logger = logging.getLogger(__name__)
_CACHE: dict[str, dict[str, Any]] = {}


# ---------- 材料合并：列表规范化与去重追加 ----------
def _to_list(value: Any) -> list[Any]:
    """
    将任意值安全转为列表。
    """
    if isinstance(value, list):
        return value
    return []


def _dedupe_preserve_order(items: list[Any]) -> list[Any]:
    """
    对列表按出现顺序去重，支持字典与基础类型混合元素。
    """
    deduped: list[Any] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _merge_materials(dispute_id: str, new_materials: dict[str, Any]) -> dict[str, Any]:
    """
    合并纠纷材料：首次全量写入，后续对 chat_history/image_urls 增量去重追加，其他字段覆盖。
    """
    cached_materials = _CACHE.get(dispute_id)
    if cached_materials is None:
        merged_materials = deepcopy(new_materials)
        _CACHE[dispute_id] = merged_materials
        return deepcopy(merged_materials)

    merged_materials = deepcopy(cached_materials)
    for key, value in new_materials.items():
        if key in {"chat_history", "image_urls"}:
            old_items = _to_list(merged_materials.get(key))
            new_items = _to_list(value)
            merged_materials[key] = _dedupe_preserve_order(old_items + new_items)
        else:
            merged_materials[key] = deepcopy(value)

    _CACHE[dispute_id] = merged_materials
    return deepcopy(merged_materials)


# ---------- 判例检索输入：从材料中抽取可读纠纷描述 ----------
def _build_dispute_desc(merged_materials: dict[str, Any]) -> str:
    """
    按约定拼接 dispute_desc：buyer_text + chat_history.content。
    """
    parts: list[str] = []
    buyer_text = merged_materials.get("buyer_text")
    if isinstance(buyer_text, str) and buyer_text.strip():
        parts.append(buyer_text.strip())

    for message in _to_list(merged_materials.get("chat_history")):
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        elif isinstance(message, str) and message.strip():
            parts.append(message.strip())

    return " ".join(parts)


# ---------- 金额等标量：容错转换，避免策略/话术链路因脏数据中断 ----------
def _safe_order_amount(raw_value: Any) -> float:
    """
    将 order_amount 转为非负浮点，转换失败时回退 0.0。
    """
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return 0.0


def clear_cache(dispute_id: str | None = None) -> None:
    """
    清理辅助模式缓存，用于集成测试或手工重置。
    """
    if dispute_id is None:
        _CACHE.clear()
        return
    _CACHE.pop(dispute_id, None)


# ---------- 对外主流程：校验 → 合并 → Agent1/2/3 → 组装 AnalysisReport ----------
def run(dispute_id: str, new_materials: dict[str, Any]) -> AnalysisReport:
    """
    运行辅助模式完整链路并返回 AnalysisReport。
    """
    if not isinstance(dispute_id, str) or not dispute_id.strip():
        raise ValueError(f"{ASSISTED_LOG_PREFIX} dispute_id 不能为空")
    if not isinstance(new_materials, dict):
        raise ValueError(f"{ASSISTED_LOG_PREFIX} new_materials 必须是 dict")

    normalized_dispute_id = dispute_id.strip()
    logger.info("%s 开始处理纠纷：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)

    # 1) 合并本次传入与历史缓存，得到 Agent1 所需的完整 materials
    try:
        merged_materials = _merge_materials(normalized_dispute_id, new_materials)
        logger.info("%s 材料合并完成：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)
    except Exception as exc:  # noqa: BLE001
        message = f"材料合并失败：{exc}"
        logger.error("%s %s", ASSISTED_LOG_PREFIX, message)
        raise RuntimeError(f"{ASSISTED_LOG_PREFIX} {message}") from exc

    # 2) 事实还原（仅 Tools 层对外部能力封装）
    try:
        facts = extract(materials=merged_materials)
        logger.info("%s Agent1 完成：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)
    except Exception as exc:  # noqa: BLE001
        message = f"Agent1 执行失败：{exc}"
        logger.error("%s %s", ASSISTED_LOG_PREFIX, message)
        raise RuntimeError(f"{ASSISTED_LOG_PREFIX} {message}") from exc

    # 3) 策略参谋：工具层拉规则/画像/判例，再调用无状态 recommend
    try:
        buyer_id = str(merged_materials.get("buyer_id", "") or "")
        dispute_desc = _build_dispute_desc(merged_materials)
        matched_rules = match_rules(facts=facts)
        buyer_profile = query_buyer_profile(buyer_id=buyer_id)
        similar_cases = search_similar_cases(dispute_desc=dispute_desc, top_k=3)
        strategy_input = StrategyInput(
            facts=facts,
            buyer_profile=buyer_profile,
            matched_rules=matched_rules,
            similar_cases=similar_cases,
            order_amount=_safe_order_amount(merged_materials.get("order_amount", 0.0)),
        )
        strategy_output = recommend(input_data=strategy_input)
        logger.info("%s Agent2 完成：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)
    except Exception as exc:  # noqa: BLE001
        message = f"Agent2 执行失败：{exc}"
        logger.error("%s %s", ASSISTED_LOG_PREFIX, message)
        raise RuntimeError(f"{ASSISTED_LOG_PREFIX} {message}") from exc

    # 4) 话术生成：模板变量来自 facts + 订单字段，策略来自上一步输出
    try:
        script_input = ScriptInput(
            strategy_output=strategy_output,
            facts=facts,
            order_id=str(merged_materials.get("order_id", "") or ""),
            order_amount=_safe_order_amount(merged_materials.get("order_amount", 0.0)),
            emotion_note=merged_materials.get("emotion_note"),
        )
        scripts = generate(input_data=script_input)
        logger.info("%s Agent3 完成：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)
    except Exception as exc:  # noqa: BLE001
        message = f"Agent3 执行失败：{exc}"
        logger.error("%s %s", ASSISTED_LOG_PREFIX, message)
        raise RuntimeError(f"{ASSISTED_LOG_PREFIX} {message}") from exc

    # 5) 聚合为前端/API 使用的单对象（情绪预警由后续 Agent4 接入）
    report = AnalysisReport(
        dispute_id=normalized_dispute_id,
        facts=facts,
        strategy=strategy_output,
        scripts=scripts,
        emotion_alert=None,
    )
    logger.info("%s 报告组装完成：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)
    return report

