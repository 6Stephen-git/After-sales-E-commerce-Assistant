"""
纠纷复盘路由。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.controllers.review_controller import submit_review
from backend.defaults import default_dispute_id, default_merchant_id


API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)
router = APIRouter(tags=["review"])


class ReviewRequest(BaseModel):
    """POST /review 请求体。"""

    dispute_id: str = Field(default="", description="纠纷编号")
    merchant_id: str = Field(default="", description="商家编号")
    final_outcome: str = Field(..., description="胜/败/和解/升级")
    outcome_note: str = Field(default="", description="结果补充说明")
    ai_strategy_adopted: bool = Field(default=False, description="是否采纳 AI 建议")
    save_to_db: bool = Field(default=True, description="是否写入判例库并触发复盘")


@router.post("/review")
def close_dispute_review(request: ReviewRequest):
    """结束纠纷并可选触发 Agent5 复盘。"""
    dispute_id = (request.dispute_id or "").strip() or default_dispute_id()
    merchant_id = (request.merchant_id or "").strip() or default_merchant_id()
    logger.info("%s /review dispute_id=%s save_to_db=%s", API_LOG_PREFIX, dispute_id, request.save_to_db)

    try:
        result = submit_review(
            dispute_id=dispute_id,
            merchant_id=merchant_id,
            final_outcome=request.final_outcome,
            outcome_note=request.outcome_note,
            ai_strategy_adopted=request.ai_strategy_adopted,
            save_to_db=request.save_to_db,
        )
        status_code = 202 if result.get("saved") else 200
        return JSONResponse(status_code=status_code, content=result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("%s /review 失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"复盘提交失败：{exc}") from exc
