"""
卖家情绪监控路由。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.controllers.emotion_controller import run_emotion_monitor


API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)
router = APIRouter(tags=["emotion"])


class EmotionMonitorRequest(BaseModel):
    """POST /emotion/monitor 请求体。"""

    dispute_id: str = Field(default="", description="纠纷编号")
    merchant_id: str = Field(default="", description="商家编号")
    text: str = Field(..., description="卖家本条消息")
    chat_history: list[dict[str, Any]] = Field(default_factory=list, description="近期对话")
    alert_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="预警阈值")


@router.post("/emotion/monitor")
def emotion_monitor(request: EmotionMonitorRequest) -> dict[str, Any]:
    """卖家发消息后实时情绪监控。"""
    logger.info("%s /emotion/monitor dispute_id=%s", API_LOG_PREFIX, request.dispute_id)
    try:
        output = run_emotion_monitor(
            text=request.text,
            chat_history=request.chat_history,
            alert_threshold=request.alert_threshold,
        )
        return output.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("%s /emotion/monitor 失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"情绪监控失败：{exc}") from exc
