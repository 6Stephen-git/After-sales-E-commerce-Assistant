"""
Agent 4 工具：情绪分析。

约束：优先使用本地 BERT 模型；模型不可用时自动降级为关键词匹配。
"""

from __future__ import annotations

import os
from threading import Lock
from typing import Any


AGENT4_LOG_PREFIX = "[Agent4]"

# ---------- 模型单例状态：仅首次尝试加载，后续调用复用 ----------
_MODEL_LOCK = Lock()
_MODEL_INITIALIZED = False
_SENTIMENT_PIPELINE = None


# ---------- 关键词词表：本地模型未就绪时的兜底规则 ----------
NEGATIVE_KEYWORDS = [
    "差",
    "烂",
    "失望",
    "生气",
    "愤怒",
    "投诉",
    "退货",
    "退款",
    "骗子",
    "欺骗",
    "气死",
    "垃圾",
    "不一样",
    "不符",
    "有问题",
    "不对",
]

POSITIVE_KEYWORDS = [
    "满意",
    "谢谢",
    "可以",
    "接受",
    "理解",
    "辛苦",
    "好评",
    "解决了",
    "没事",
    "行吧",
    "好吧",
    "算了",
]


def _normalize_label(label: Any) -> str:
    """
    将模型标签统一映射为 negative/neutral/positive。

    参数:
        label: 模型原始标签，可能为中文、英文或带前缀字符串。

    返回:
        统一标签字符串。
    """
    text = str(label or "").strip().lower()
    if not text:
        return "neutral"

    if text in {"negative", "neg"}:
        return "negative"
    if text in {"positive", "pos"}:
        return "positive"
    if text in {"neutral", "neu"}:
        return "neutral"

    if "neg" in text or "负" in text:
        return "negative"
    if "pos" in text or "正" in text:
        return "positive"
    if "neu" in text or "中性" in text:
        return "neutral"
    return "neutral"


def _keyword_fallback(text: str) -> dict[str, Any]:
    """
    关键词兜底情绪分析，返回统一结构。

    参数:
        text: 待分析文本。

    返回:
        dict，包含 label 与 intensity。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return {"label": "neutral", "intensity": 0.0}

    negative_hits = sum(1 for keyword in NEGATIVE_KEYWORDS if keyword in cleaned)
    positive_hits = sum(1 for keyword in POSITIVE_KEYWORDS if keyword in cleaned)
    exclamation_hits = cleaned.count("!") + cleaned.count("！")

    if negative_hits > positive_hits:
        intensity = min(1.0, 0.35 + negative_hits * 0.2 + exclamation_hits * 0.05)
        return {"label": "negative", "intensity": round(intensity, 3)}
    if positive_hits > negative_hits:
        intensity = min(1.0, 0.3 + positive_hits * 0.18)
        return {"label": "positive", "intensity": round(intensity, 3)}
    return {"label": "neutral", "intensity": 0.2}


def _init_model_if_needed() -> None:
    """
    按需初始化本地情绪模型，仅执行一次。

    说明:
        - 读环境变量 SENTIMENT_MODEL_PATH。
        - 加载失败不抛异常，保持可降级状态。
    """
    global _MODEL_INITIALIZED, _SENTIMENT_PIPELINE

    if _MODEL_INITIALIZED:
        return

    with _MODEL_LOCK:
        if _MODEL_INITIALIZED:
            return

        model_path = os.getenv("SENTIMENT_MODEL_PATH", "").strip()
        if not model_path or not os.path.exists(model_path):
            print(f"{AGENT4_LOG_PREFIX} 情绪模型未加载，降级为关键词匹配")
            _MODEL_INITIALIZED = True
            return

        try:
            from transformers import pipeline  # type: ignore

            print(f"{AGENT4_LOG_PREFIX} 开始加载本地情绪模型：{model_path}")
            _SENTIMENT_PIPELINE = pipeline(
                "sentiment-analysis",
                model=model_path,
                tokenizer=model_path,
            )
            print(f"{AGENT4_LOG_PREFIX} 本地情绪模型加载完成")
        except Exception as exc:  # noqa: BLE001
            print(f"{AGENT4_LOG_PREFIX} 情绪模型加载失败，降级为关键词匹配：{exc}")
            _SENTIMENT_PIPELINE = None
        finally:
            _MODEL_INITIALIZED = True


def analyze_sentiment(text: str) -> dict[str, Any]:
    """
    分析文本情绪，返回基础标签和强度。

    流程:
        1) 初始化并复用本地模型（若可用）；
        2) 模型推理失败或不可用时，自动降级到关键词匹配。

    参数:
        text: 待分析文本。

    返回:
        {"label": "negative|neutral|positive", "intensity": 0~1}
    """
    _init_model_if_needed()
    cleaned_text = (text or "").strip()
    if not cleaned_text:
        return {"label": "neutral", "intensity": 0.0}

    if _SENTIMENT_PIPELINE is None:
        return _keyword_fallback(text=cleaned_text)

    try:
        print(f"{AGENT4_LOG_PREFIX} 开始执行本地情绪推理")
        result = _SENTIMENT_PIPELINE(cleaned_text)
        if isinstance(result, list) and result:
            top = result[0]
            label = _normalize_label(top.get("label"))
            score = float(top.get("score", 0.0))
            intensity = max(0.0, min(1.0, round(score, 3)))
            print(f"{AGENT4_LOG_PREFIX} 本地情绪推理完成，label={label}, intensity={intensity}")
            return {"label": label, "intensity": intensity}
    except Exception as exc:  # noqa: BLE001
        print(f"{AGENT4_LOG_PREFIX} 本地情绪推理失败，改用关键词匹配：{exc}")

    return _keyword_fallback(text=cleaned_text)
