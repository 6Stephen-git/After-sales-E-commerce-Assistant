"""

全链路共享的文本信号配置：从 data/text_signals.json 加载，避免业务代码散落词表。

"""



from __future__ import annotations



import json

import logging

import os

from functools import lru_cache

from pathlib import Path

from typing import Any



from schemas import CREDENTIAL_TRUST_SUSPECT, CREDENTIAL_TRUST_UNKNOWN



LOG_PREFIX = "[TextSignals]"

logger = logging.getLogger(__name__)



ROOT_DIR = Path(__file__).resolve().parents[2]

DEFAULT_SIGNALS_PATH = ROOT_DIR / "data" / "text_signals.json"





@lru_cache(maxsize=1)

def load_text_signals() -> dict[str, Any]:

    """

    加载文本信号配置；文件缺失时返回空 dict 并记录错误。

    """

    path = Path(os.getenv("TEXT_SIGNALS_PATH", str(DEFAULT_SIGNALS_PATH)))

    try:

        payload = json.loads(path.read_text(encoding="utf-8"))

    except FileNotFoundError:

        logger.error("%s 配置文件不存在：%s", LOG_PREFIX, path)

        return {}

    except Exception as exc:  # noqa: BLE001

        logger.error("%s 读取配置失败：%s", LOG_PREFIX, exc)

        return {}

    return payload if isinstance(payload, dict) else {}





def signal_group(name: str) -> tuple[str, ...]:

    """

    读取命名词表组，统一为 tuple 供 contains_any 使用。

    """

    raw = load_text_signals().get(name)

    if not isinstance(raw, list):

        return ()

    return tuple(str(item).strip() for item in raw if str(item).strip())





def constraint_type_markers(constraint_key: str) -> tuple[str, ...]:

    """

    读取规则约束类型归类用词表组。

    """

    raw = load_text_signals().get("constraint_type_markers")

    if not isinstance(raw, dict):

        return ()

    items = raw.get(constraint_key)

    if not isinstance(items, list):

        return ()

    return tuple(str(item).strip() for item in items if str(item).strip())





def contains_any(text: str, phrases: tuple[str, ...]) -> bool:

    """

    判断 text 是否包含 phrases 中任一子串。

    """

    if not text or not phrases:

        return False

    return any(phrase in text for phrase in phrases)





def read_credential_trust(facts: Any) -> str:

    """读取 FactOutput 或 dict 上的 credential_trust，缺省为 unknown。"""

    if hasattr(facts, "credential_trust"):

        raw = getattr(facts, "credential_trust", None)

    elif isinstance(facts, dict):

        raw = facts.get("credential_trust")

    else:

        raw = None

    text = str(raw or "").strip().lower()

    return text if text else CREDENTIAL_TRUST_UNKNOWN





def facts_has_deceptive_credential_clues(facts: Any) -> bool:

    """

    是否应视为举证来源可疑：仅当 Agent1 视觉链路写入 credential_trust=suspect。



    下游不再用 red_flags / visual_observations 关键词复判网图。

    """

    return read_credential_trust(facts) == CREDENTIAL_TRUST_SUSPECT





def visual_observations_suspicious(facts: Any) -> bool:

    """恶意语义层门控：举证可疑时保留 LLM 通道（通常已有硬规则命中）。"""

    return facts_has_deceptive_credential_clues(facts)


def _read_facts_attributes(facts: Any) -> dict[str, Any]:
    """读取 FactOutput 或 dict 上的 attributes 字段。"""
    if hasattr(facts, "attributes"):
        raw = getattr(facts, "attributes", None)
    elif isinstance(facts, dict):
        raw = facts.get("attributes")
    else:
        raw = None
    return raw if isinstance(raw, dict) else {}


def _read_visual_observations_list(facts: Any) -> list[str]:
    """读取 visual_observations 并过滤空白项。"""
    if hasattr(facts, "visual_observations"):
        raw = getattr(facts, "visual_observations", None)
    elif isinstance(facts, dict):
        raw = facts.get("visual_observations")
    else:
        raw = None
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def pick_balanced_visual_observations(facts: Any, *, max_items: int) -> list[str]:
    """
    为下游 LLM 选取视觉观察：多图时轮询每图至少一条，避免截断后只剩第一张图结论。

    参数:
        facts: FactOutput 或等价 dict。
        max_items: 最多返回条数。

    返回:
        带图序前缀的观察短句列表；无 per-image 摘要时回退 visual_observations 截断。
    """
    limit = max(1, int(max_items))
    attrs = _read_facts_attributes(facts)
    summaries = attrs.get("image_vision_summaries")
    if not isinstance(summaries, list) or len(summaries) <= 1:
        return _read_visual_observations_list(facts)[:limit]

    per_image_lines: list[list[str]] = []
    for item in summaries:
        if not isinstance(item, dict) or item.get("error"):
            per_image_lines.append([])
            continue
        index = item.get("index", len(per_image_lines) + 1)
        lines: list[str] = []
        desc = str(item.get("visual_description") or "").strip()
        if desc:
            lines.append(f"图{index}：{desc}")
        defect = str(item.get("defect_type") or "").strip()
        if defect and defect not in {"无", "暂无", "无瑕疵", "无质量问题", "无明显瑕疵", "没有瑕疵"}:
            lines.append(f"图{index}瑕疵：{defect}")
        trust = str(item.get("credential_trust") or "").strip().lower()
        if trust == CREDENTIAL_TRUST_SUSPECT:
            note = str(item.get("credential_trust_note") or "").strip()
            lines.append(note or f"图{index}举证来源可疑")
        for flag in item.get("visual_red_flags") or []:
            text = str(flag).strip()
            if text:
                lines.append(f"图{index}疑点：{text}")
        per_image_lines.append(lines)

    if not any(per_image_lines):
        return _read_visual_observations_list(facts)[:limit]

    picked: list[str] = []
    round_idx = 0
    while len(picked) < limit:
        added = False
        for lines in per_image_lines:
            if round_idx >= len(lines):
                continue
            line = lines[round_idx]
            if line not in picked:
                picked.append(line)
                added = True
            if len(picked) >= limit:
                break
        if not added:
            break
        round_idx += 1
    return picked

