"""
Agent 4：卖家情绪监控员。

职责：分析卖家消息情绪，在过激时触发预警（独立 API，不参与 Agent1→2→3 主链路）。
"""

from __future__ import annotations

from typing import Any

from schemas import EmotionOutput

from backend.tools.agent4_tools import analyze_seller_emotion


AGENT4_LOG_PREFIX = "[Agent4]"
DEFAULT_ALERT_THRESHOLD = 0.8
DEFAULT_EARLY_WARN_THRESHOLD = 0.5


def _normalize_sentiment(raw_sentiment: Any) -> str:
    """兜底标准化情绪标签。"""
    text = str(raw_sentiment or "").strip().lower()
    if text in {"negative", "neutral", "positive"}:
        return text
    return "neutral"


def _fallback_emotion_note(sentiment: str, intensity: float) -> str:
    """LLM 未返回 emotion_note 时的简化描述（第二人称对店主）。"""
    if sentiment == "negative":
        if intensity >= 0.8:
            return "您当前情绪明显过激，容易激化纠纷，建议先冷静措辞再发送。"
        return "您语气偏硬，建议放慢节奏、先确认事实再表态。"
    if sentiment == "positive":
        return "您沟通较为专业克制，可继续保持清晰友好的表达。"
    return "您当前情绪整体平稳。"


# ---------- 主入口：卖家情绪监控与预警 ----------
def monitor(text: str, context: dict) -> EmotionOutput:
    """
    执行卖家情绪监控，输出 EmotionOutput。

    参数:
        text: 待分析卖家消息。
        context: 含 chat_history、alert_threshold。

    返回:
        EmotionOutput。
    """
    if not isinstance(context, dict):
        raise ValueError(f"{AGENT4_LOG_PREFIX} context 必须是 dict")

    chat_history = context.get("chat_history", []) or []
    sentiment_result = analyze_seller_emotion(text=text, chat_history=chat_history)
    sentiment = _normalize_sentiment(raw_sentiment=sentiment_result.get("label"))
    intensity = float(sentiment_result.get("intensity", 0.0))
    intensity = max(0.0, min(1.0, round(intensity, 3)))

    alert_threshold = float(context.get("alert_threshold", DEFAULT_ALERT_THRESHOLD))
    early_warn_threshold = float(
        context.get("early_warn_threshold", DEFAULT_EARLY_WARN_THRESHOLD)
    )
    alert_triggered = sentiment == "negative" and intensity >= alert_threshold
    early_warn_triggered = (
        sentiment == "negative"
        and intensity >= early_warn_threshold
        and intensity < alert_threshold
    )

    if alert_triggered:
        alert_message = "您当前情绪明显过激，建议先冷静措辞。"
        alert_reason = f"您的负面情绪强度 {intensity} 超过阈值 {round(alert_threshold, 3)}"
    else:
        alert_message = ""
        alert_reason = ""

    if early_warn_triggered:
        early_warn_message = "您这条语气偏硬，下一条发送前会再帮您把关。"
    else:
        early_warn_message = ""

    emotion_note = str(sentiment_result.get("emotion_note") or "").strip()
    if not emotion_note:
        emotion_note = _fallback_emotion_note(sentiment=sentiment, intensity=intensity)

    return EmotionOutput(
        alert_triggered=alert_triggered,
        alert_message=alert_message,
        alert_reason=alert_reason,
        early_warn_triggered=early_warn_triggered,
        early_warn_message=early_warn_message,
        sentiment=sentiment,
        intensity=intensity,
        emotion_note=emotion_note,
    )
