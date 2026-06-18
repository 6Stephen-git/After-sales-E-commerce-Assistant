"""
卖家情绪监控控制器。

职责：校验入参并调用 Agent4 monitor。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.agents.agent4 import monitor
from schemas import EmotionOutput


EMOTION_LOG_PREFIX = "[EmotionController]"
logger = logging.getLogger(__name__)


def run_emotion_monitor(
    *,
    text: str,
    chat_history: list[dict[str, Any]],
    alert_threshold: float = 0.8,
) -> EmotionOutput:
    """
    执行卖家情绪监控。

    参数:
        text: 卖家本条消息。
        chat_history: 近期对话。
        alert_threshold: 预警阈值。

    返回:
        EmotionOutput。
    """
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        raise ValueError("text 不能为空")

    logger.info("%s 开始卖家情绪监控 text_len=%s history_len=%s", EMOTION_LOG_PREFIX, len(cleaned_text), len(chat_history))
    output = monitor(
        text=cleaned_text,
        context={
            "chat_history": chat_history,
            "alert_threshold": alert_threshold,
        },
    )
    logger.info(
        "%s 卖家情绪监控完成 alert=%s sentiment=%s intensity=%s",
        EMOTION_LOG_PREFIX,
        output.alert_triggered,
        output.sentiment,
        output.intensity,
    )
    return output
