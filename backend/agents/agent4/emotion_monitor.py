"""
Agent 4：情绪监控员。

职责：分析消息情绪并生成细腻情绪描述，为后续话术语气提供依据。
"""

from __future__ import annotations

from typing import Any

from schemas import EmotionOutput

from backend.tools.agent4_tools import analyze_sentiment


AGENT4_LOG_PREFIX = "[Agent4]"
DEFAULT_ALERT_THRESHOLD = 0.8


# ---------- 上下文解读：提取历史文本并识别是否存在负面轨迹 ----------
def _extract_history_text(context: dict[str, Any]) -> str:
    """
    将 context 中可用历史消息拼接为纯文本。

    参数:
        context: 上下文快照，可能包含 chat_history。

    返回:
        历史文本拼接结果，可能为空字符串。
    """
    if not isinstance(context, dict):
        return ""

    parts: list[str] = []
    history = context.get("chat_history", []) or []
    for item in history:
        if isinstance(item, dict):
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        elif isinstance(item, str) and item.strip():
            parts.append(item.strip())
    return " ".join(parts)


def _has_negative_trace(history_text: str) -> bool:
    """
    判断历史中是否出现明显负面情绪轨迹。

    参数:
        history_text: 历史文本。

    返回:
        若命中负面关键词返回 True，否则 False。
    """
    if not history_text:
        return False

    keywords = ["投诉", "失望", "不满意", "生气", "差评", "退货", "退款", "愤怒", "问题"]
    return any(keyword in history_text for keyword in keywords)


# ---------- 情绪注释生成：根据标签、强度与历史轨迹输出细腻描述 ----------
def _build_emotion_note(sentiment: str, intensity: float, context: dict[str, Any]) -> str:
    """
    生成细腻情绪描述 emotion_note。

    参数:
        sentiment: 基础情绪标签。
        intensity: 情绪强度，0~1。
        context: 对话上下文快照。

    返回:
        中文自然语言描述。
    """
    history_text = _extract_history_text(context=context)
    has_negative_trace = _has_negative_trace(history_text=history_text)
    stage = str(context.get("dispute_stage", "")).strip() if isinstance(context, dict) else ""

    if sentiment == "negative":
        if intensity >= 0.8:
            return "买家情绪激动，表达明显不满，当前存在升级投诉或要求平台介入的风险，建议先稳态安抚并快速给出明确处理路径。"
        if intensity >= 0.5:
            return "买家表达不满，但仍处于可协商区间，核心诉求是尽快获得清晰解释和可执行方案。"
        return "买家有轻度抵触情绪，整体措辞较克制，建议用确认问题与补充证据的方式降低对立感。"

    if sentiment == "positive":
        if intensity < 0.7 and has_negative_trace:
            return "买家表面上已接受当前沟通节奏，但仍带有勉强情绪，潜在期待是结果尽快落地且不要反复拉扯。"
        if intensity >= 0.7:
            return "买家情绪明显好转，接受度较高，可顺势推进方案确认并锁定后续执行节点。"
        return "买家态度较为缓和，具备继续协商空间，建议保持礼貌并明确下一步动作。"

    if stage:
        return f"买家情绪整体平稳，当前处于{stage}阶段，沟通重点应放在事实确认和预期对齐。"
    return "买家情绪平稳，对话以事实描述为主，建议持续保持清晰、简洁的沟通节奏。"


def _normalize_sentiment(raw_sentiment: Any) -> str:
    """
    兜底标准化情绪标签，避免异常值污染输出。

    参数:
        raw_sentiment: 工具层返回的情绪标签。

    返回:
        标准情绪标签。
    """
    text = str(raw_sentiment or "").strip().lower()
    if text in {"negative", "neutral", "positive"}:
        return text
    return "neutral"


# ---------- 主入口：组合基础情绪、细腻描述与预警结论 ----------
def monitor(text: str, context: dict) -> EmotionOutput:
    """
    执行情绪监控，输出 EmotionOutput。

    参数:
        text: 待分析文本（商家输入或买家消息）。
        context: 纠纷上下文快照，支持 alert_threshold、chat_history、dispute_stage。

    返回:
        EmotionOutput。
    """
    if not isinstance(context, dict):
        raise ValueError(f"{AGENT4_LOG_PREFIX} context 必须是 dict")

    sentiment_result = analyze_sentiment(text=text)
    sentiment = _normalize_sentiment(raw_sentiment=sentiment_result.get("label"))
    intensity = float(sentiment_result.get("intensity", 0.0))
    intensity = max(0.0, min(1.0, round(intensity, 3)))

    alert_threshold = float(context.get("alert_threshold", DEFAULT_ALERT_THRESHOLD))
    alert_triggered = sentiment == "negative" and intensity > alert_threshold

    if alert_triggered:
        alert_message = "检测到高强度负面情绪，请优先安抚并提供明确解决时点。"
        alert_reason = f"负面情绪强度 {intensity} 超过阈值 {round(alert_threshold, 3)}"
    else:
        alert_message = ""
        alert_reason = ""

    emotion_note = _build_emotion_note(
        sentiment=sentiment,
        intensity=intensity,
        context=context,
    )

    return EmotionOutput(
        alert_triggered=alert_triggered,
        alert_message=alert_message,
        alert_reason=alert_reason,
        sentiment=sentiment,
        intensity=intensity,
        emotion_note=emotion_note,
    )
