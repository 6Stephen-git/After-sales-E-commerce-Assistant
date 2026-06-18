"""
Agent 4 工具：卖家情绪分析。

约束：使用 MiMo LLM 结构化输出；调用失败时降级为卖家攻击性关键词匹配。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.tools.llm_client import chat_completion
from backend.tools.text_signals import signal_group


AGENT4_LOG_PREFIX = "[Agent4]"
logger = logging.getLogger(__name__)

_SELLER_EMOTION_SYSTEM_PROMPT = """你是电商售后场景下的卖家情绪督导员。
任务：判断卖家（店主）本条回复是否带有情绪化、攻击性、推卸责任或不专业表达。
注意：分析对象是卖家，不是买家。

输出严格 JSON，不要 markdown：
{
  "sentiment": "negative|neutral|positive",
  "intensity": 0.0到1.0的小数,
  "emotion_note": "用第二人称「您」直接对店主说话，一句话描述其情绪状态、沟通风险与建议（中文，勿用「卖家」第三人称）"
}

判定要点：
- negative：辱骂、威胁、冷嘲热讽、强硬甩锅、明显失去耐心
- neutral：事实陈述、流程说明、无明显情绪
- positive：礼貌、共情、主动担责、专业克制
- intensity 反映情绪激烈程度；轻微不满约 0.3~0.5，明显过激 0.7+"""


def _normalize_label(label: Any) -> str:
    """将模型标签统一映射为 negative/neutral/positive。"""
    text = str(label or "").strip().lower()
    if text in {"negative", "neutral", "positive"}:
        return text
    if "neg" in text or "负" in text:
        return "negative"
    if "pos" in text or "正" in text:
        return "positive"
    return "neutral"


def _clamp_intensity(value: Any) -> float:
    """将强度限制在 0~1 并保留三位小数。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, round(parsed, 3)))


def _parse_emotion_json(raw_text: str) -> dict[str, Any] | None:
    """解析 LLM 情绪 JSON；失败返回 None。"""
    text = (raw_text or "").strip()
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    sentiment = _normalize_label(payload.get("sentiment"))
    intensity = _clamp_intensity(payload.get("intensity"))
    emotion_note = str(payload.get("emotion_note") or "").strip()
    return {
        "label": sentiment,
        "intensity": intensity,
        "emotion_note": emotion_note,
    }


def _normalize_chat_history(chat_history: list[Any] | None) -> list[dict[str, str]]:
    """将 chat_history 规范为 role/content 列表供 LLM 阅读。"""
    normalized: list[dict[str, str]] = []
    for item in chat_history or []:
        if isinstance(item, dict):
            role = str(item.get("role", "")).strip() or "unknown"
            content = str(item.get("content", "")).strip()
            if content:
                normalized.append({"role": role, "content": content})
        elif isinstance(item, str) and item.strip():
            normalized.append({"role": "unknown", "content": item.strip()})
    return normalized[-12:]


def _seller_keyword_fallback(text: str) -> dict[str, Any]:
    """卖家情绪关键词兜底。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return {"label": "neutral", "intensity": 0.0, "emotion_note": ""}

    negative_keywords = signal_group("seller_negative_keywords")
    positive_keywords = signal_group("seller_positive_keywords")
    negative_hits = sum(1 for keyword in negative_keywords if keyword in cleaned)
    positive_hits = sum(1 for keyword in positive_keywords if keyword in cleaned)
    exclamation_hits = cleaned.count("!") + cleaned.count("！")

    if negative_hits > positive_hits:
        intensity = min(1.0, 0.4 + negative_hits * 0.15 + exclamation_hits * 0.05)
        note = "您的措辞偏激烈，存在对立升级风险，建议先冷静再回复买家。"
        return {
            "label": "negative",
            "intensity": round(intensity, 3),
            "emotion_note": note,
        }
    if positive_hits > negative_hits:
        intensity = min(1.0, 0.35 + positive_hits * 0.12)
        return {
            "label": "positive",
            "intensity": round(intensity, 3),
            "emotion_note": "您语气较为克制专业，可继续保持清晰沟通。",
        }
    return {"label": "neutral", "intensity": 0.2, "emotion_note": "您当前情绪整体平稳。"}


def _call_seller_emotion_llm(*, text: str, chat_history: list[Any]) -> dict[str, Any] | None:
    """调用 LLM 分析卖家情绪；失败返回 None。"""
    history = _normalize_chat_history(chat_history)
    user_payload = {
        "seller_message": text,
        "recent_chat_history": history,
    }
    logger.info("%s 开始 LLM 卖家情绪分析", AGENT4_LOG_PREFIX)
    raw = chat_completion(
        messages=[
            {"role": "system", "content": _SELLER_EMOTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ],
        model_env_key="AGENT4_LLM_MODEL",
        temperature=0.3,
    )
    if not raw:
        logger.error("%s LLM 卖家情绪分析失败", AGENT4_LOG_PREFIX)
        return None
    parsed = _parse_emotion_json(raw)
    if parsed is None:
        logger.error("%s LLM 卖家情绪 JSON 解析失败", AGENT4_LOG_PREFIX)
        return None
    logger.info(
        "%s LLM 卖家情绪分析完成 label=%s intensity=%s",
        AGENT4_LOG_PREFIX,
        parsed.get("label"),
        parsed.get("intensity"),
    )
    return parsed


def analyze_seller_emotion(text: str, chat_history: list[Any] | None = None) -> dict[str, Any]:
    """
    分析卖家文本情绪，返回标签、强度与细腻描述。

    返回:
        {"label", "intensity", "emotion_note"}
    """
    cleaned_text = (text or "").strip()
    if not cleaned_text:
        return {"label": "neutral", "intensity": 0.0, "emotion_note": ""}

    llm_result = _call_seller_emotion_llm(text=cleaned_text, chat_history=chat_history or [])
    if llm_result is not None:
        return llm_result

    logger.warning("%s LLM 不可用，使用卖家关键词兜底", AGENT4_LOG_PREFIX)
    return _seller_keyword_fallback(text=cleaned_text)
