"""
Agent 5 工具集：复盘经验卡片写入。

约束：写入时必须携带 merchant_id，用于商家数据隔离。
"""

from __future__ import annotations

import logging
from typing import Any

from schemas import ReviewOutput


AGENT5_LOG_PREFIX = "[Agent5]"
logger = logging.getLogger(__name__)


# ---------- 工具入参检查：保证商家隔离字段和数据结构完整 ----------
def _validate_review_input(review: ReviewOutput, merchant_id: str) -> None:
    """
    校验保存判例所需的关键输入。

    参数:
        review: Agent5 产出的经验卡片。
        merchant_id: 当前商家标识。
    """
    if not merchant_id or not str(merchant_id).strip():
        raise ValueError("merchant_id 不能为空")

    if not isinstance(review, ReviewOutput):
        raise TypeError("review 必须是 ReviewOutput 类型")


# ---------- 对外工具：保存经验卡片（当前为可观测 Mock 写入） ----------
def save_case_to_db(review: ReviewOutput, merchant_id: str) -> bool:
    """
    保存经验卡片到判例库（MVP 阶段使用 Mock 写入）。

    参数:
        review: 复盘分析师输出的经验卡片。
        merchant_id: 当前商家标识，用于数据隔离。

    返回:
        bool: 写入成功返回 True，失败返回 False。
    """
    logger.info("%s 开始写入经验卡片，merchant_id=%s", AGENT5_LOG_PREFIX, merchant_id)

    try:
        _validate_review_input(review=review, merchant_id=merchant_id)

        # MVP 阶段先做可观测 Mock，后续接入真实 MySQL 时保持函数签名不变。
        payload: dict[str, Any] = review.model_dump()
        logger.info(
            "%s Mock 写入完成，merchant_id=%s case_type=%s outcome=%s tags=%s",
            AGENT5_LOG_PREFIX,
            merchant_id,
            payload.get("case_type", ""),
            payload.get("outcome", ""),
            payload.get("tags", []),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 经验卡片写入失败：%s", AGENT5_LOG_PREFIX, exc)
        return False
