"""
纠纷复盘控制器。

职责：组装 ReviewInput、触发 Celery 或仅关闭纠纷。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.cache import clear_dispute_cache, get_cached_report, load_materials
from backend.defaults import default_merchant_id
from backend.tasks.review_task import async_review
from schemas import ReviewInput


REVIEW_LOG_PREFIX = "[ReviewController]"
logger = logging.getLogger(__name__)
_VALID_OUTCOMES = {"胜", "败", "和解", "升级"}


def _normalize_outcome(raw: str) -> str:
    """校验并标准化纠纷结果。"""
    text = str(raw or "").strip()
    if text not in _VALID_OUTCOMES:
        raise ValueError(f"final_outcome 必须是：{'/'.join(sorted(_VALID_OUTCOMES))}")
    return text


def _build_full_timeline(*, merchant_id: str, dispute_id: str, materials: dict[str, Any]) -> dict[str, Any]:
    """从材料缓存与分析报告缓存组装复盘轨迹。"""
    timeline: dict[str, Any] = {
        "dispute_id": dispute_id,
        "chat_history": materials.get("chat_history", []),
        "order_amount": materials.get("order_amount"),
        "buyer_id": materials.get("buyer_id"),
        "order_id": materials.get("order_id"),
    }
    report = get_cached_report(merchant_id, dispute_id, materials)
    if report is not None:
        timeline["facts"] = report.facts.model_dump()
        timeline["strategy"] = report.strategy.model_dump()
        timeline["strategy_output"] = report.strategy.model_dump()
        timeline["scripts"] = report.scripts.model_dump()
        timeline["matched_rules"] = [item.model_dump() for item in report.matched_rules]
    return timeline


def submit_review(
    *,
    dispute_id: str,
    merchant_id: str,
    final_outcome: str,
    outcome_note: str,
    ai_strategy_adopted: bool,
    save_to_db: bool,
) -> dict[str, Any]:
    """
    结束纠纷：可选触发 Agent5 复盘入库。

    返回:
        status 响应字典。
    """
    normalized_dispute_id = str(dispute_id or "").strip()
    normalized_merchant_id = str(merchant_id or "").strip() or default_merchant_id()
    outcome = _normalize_outcome(final_outcome)

    if not normalized_dispute_id:
        raise ValueError("dispute_id 不能为空")

    if not save_to_db:
        clear_dispute_cache(normalized_merchant_id, normalized_dispute_id)
        logger.info("%s 纠纷已关闭（未入库）dispute_id=%s", REVIEW_LOG_PREFIX, normalized_dispute_id)
        return {"status": "closed", "saved": False, "dispute_id": normalized_dispute_id}

    materials = load_materials(normalized_merchant_id, normalized_dispute_id)
    if not materials:
        raise ValueError("未找到纠纷缓存，请先完成一次 AI 分析后再结束纠纷")

    full_timeline = _build_full_timeline(
        merchant_id=normalized_merchant_id,
        dispute_id=normalized_dispute_id,
        materials=materials,
    )
    review_input = ReviewInput(
        dispute_id=normalized_dispute_id,
        full_timeline=full_timeline,
        final_outcome=outcome,
        ai_strategy_adopted=ai_strategy_adopted,
        outcome_note=str(outcome_note or "").strip(),
    )

    async_review.delay(review_input.model_dump(), normalized_merchant_id)
    clear_dispute_cache(normalized_merchant_id, normalized_dispute_id)
    logger.info("%s 复盘任务已入队 dispute_id=%s", REVIEW_LOG_PREFIX, normalized_dispute_id)
    return {"status": "accepted", "saved": True, "dispute_id": normalized_dispute_id}
