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


