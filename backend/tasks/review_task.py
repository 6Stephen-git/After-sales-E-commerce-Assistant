"""
Agent 5 异步任务封装。

职责：接收 ReviewInput 字典，调用 Agent5 复盘并写入判例库。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.agents.agent5 import review
from backend.tasks.celery_app import celery_app
from backend.tools.agent5_tools import save_case_to_db
from schemas import ReviewInput


AGENT5_LOG_PREFIX = "[Agent5]"
logger = logging.getLogger(__name__)


# ---------- 异步任务入口：反序列化输入并持久化经验卡片 ----------
@celery_app.task(name="agent5.async_review")
def async_review(review_input_dict: dict[str, Any], merchant_id: str) -> bool:
    """
    异步执行 Agent5 复盘并写入判例库。

    参数:
        review_input_dict: ReviewInput 的字典结构。
        merchant_id: 当前商家标识。

    返回:
        bool: 全流程成功返回 True，否则 False。
    """
    logger.info("%s 异步复盘任务开始，merchant_id=%s", AGENT5_LOG_PREFIX, merchant_id)

    try:
        review_input = ReviewInput.model_validate(review_input_dict)
        review_output = review(input=review_input)
        saved = save_case_to_db(review=review_output, merchant_id=merchant_id)
        logger.info("%s 异步复盘任务结束，写入结果=%s", AGENT5_LOG_PREFIX, saved)
        return saved
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 异步复盘任务失败：%s", AGENT5_LOG_PREFIX, exc)
        return False
