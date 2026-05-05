"""
分析路由。

职责：接收辅助模式分析请求，调用 AssistedController 并返回 AnalysisReport。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.controllers.assisted_controller import run as assisted_run


API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)
router = APIRouter(tags=["analyze"])


# ---------- 请求体模型：约束 /analyze 入参 ----------
class AnalyzeRequest(BaseModel):
    """
    /analyze 请求参数。
    """

    dispute_id: str = Field(..., description="纠纷编号")
    merchant_id: str = Field(..., description="商家编号")
    messages: list[dict[str, Any]] = Field(default_factory=list, description="纠纷消息列表")
    order_id: str = Field(default="", description="订单号")
    order_amount: float = Field(default=0.0, description="订单金额")
    buyer_id: str = Field(default="", description="买家脱敏标识")
    image_urls: list[str] = Field(default_factory=list, description="举证图片 URL")


# ---------- 请求转换：从消息列表提取 buyer_text ----------
def _extract_buyer_text(messages: list[dict[str, Any]]) -> str:
    """
    提取最近一条买家消息文本，作为 Agent1 的 buyer_text。
    """
    for message in reversed(messages):
        role = str(message.get("role", "")).strip().lower()
        content = str(message.get("content", "")).strip()
        if role in {"buyer", "user"} and content:
            return content
    return ""


# ---------- 核心端点：调用 AssistedController.run ----------
@router.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    """
    执行辅助模式分析并返回结构化报告。
    """
    if not request.dispute_id.strip():
        raise HTTPException(status_code=400, detail="dispute_id 不能为空")
    if not request.merchant_id.strip():
        raise HTTPException(status_code=400, detail="merchant_id 不能为空")

    logger.info("%s 开始处理 /analyze 请求，dispute_id=%s", API_LOG_PREFIX, request.dispute_id)
    try:
        materials = {
            "merchant_id": request.merchant_id.strip(),
            "order_id": request.order_id.strip(),
            "order_amount": request.order_amount,
            "buyer_id": request.buyer_id.strip(),
            "buyer_text": _extract_buyer_text(messages=request.messages),
            "chat_history": request.messages,
            "image_urls": request.image_urls,
        }
        report = assisted_run(dispute_id=request.dispute_id.strip(), new_materials=materials)
        logger.info("%s /analyze 请求处理完成，dispute_id=%s", API_LOG_PREFIX, request.dispute_id)
        return report.model_dump()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("%s /analyze 执行失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"分析失败：{exc}") from exc
