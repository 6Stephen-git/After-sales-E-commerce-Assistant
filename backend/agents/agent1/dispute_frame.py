"""
争议主框架判定：全链路单点产出 primary_dispute_frame，下游只读不再猜。
"""

from __future__ import annotations

import re
from typing import Any

from backend.tools.rule_lexicon import load_lexicon_config
from schemas import (
    DISPUTE_FRAME_DESCRIPTION_MISMATCH,
    DISPUTE_FRAME_LOGISTICS,
    DISPUTE_FRAME_QUALITY_DEFECT,
    DISPUTE_FRAME_SEVEN_DAY_RETURN,
    DISPUTE_FRAME_UNKNOWN,
)

# facet（rule_lexicon_config）→ 主框架枚举
_FACET_TO_DISPUTE_FRAME: dict[str, str] = {
    "seven_day_return": DISPUTE_FRAME_SEVEN_DAY_RETURN,
    "logistics": DISPUTE_FRAME_LOGISTICS,
    "receipt": DISPUTE_FRAME_LOGISTICS,
    "shipping": DISPUTE_FRAME_LOGISTICS,
    "description_mismatch": DISPUTE_FRAME_DESCRIPTION_MISMATCH,
    "surface_inconsistency": DISPUTE_FRAME_DESCRIPTION_MISMATCH,
    "quality_claim": DISPUTE_FRAME_QUALITY_DEFECT,
}

_NO_DEFECT_EXACT = frozenset(
    {
        "无",
        "暂无",
        "无瑕疵",
        "无质量问题",
        "无明显瑕疵",
        "没有瑕疵",
        "无客观瑕疵",
        "no_defect",
        "none",
    }
)
_NO_DEFECT_PREFIXES = ("无瑕疵", "无质量问题", "无明显瑕疵", "没有瑕疵", "无客观瑕疵")


def is_no_defect_claim(defect_type: str | None) -> bool:
    """
    是否主张无质量瑕疵（全品类口语：含括号补充说明、英文枚举）。

    供 Agent1 框架判定与 RuleMatcher 举证门控复用。
    """
    normalized = str(defect_type or "").strip().lower()
    if not normalized:
        return True
    main = re.split(r"[（(]", normalized, maxsplit=1)[0].strip()
    if main in _NO_DEFECT_EXACT:
        return True
    return any(main.startswith(prefix) for prefix in _NO_DEFECT_PREFIXES)


def _collect_facets(labels: list[Any]) -> set[str]:
    """从 intent/服务标标签经 lexicon intent_to_facets 归一为 facet 集合。"""
    intent_map = load_lexicon_config().get("intent_to_facets") or {}
    facets: set[str] = set()
    for raw in labels or []:
        text = str(raw or "").strip()
        if not text:
            continue
        for facet in intent_map.get(text, []):
            facet_text = str(facet or "").strip()
            if facet_text:
                facets.add(facet_text)
    return facets


def resolve_primary_dispute_frame(
    *,
    materials: dict[str, Any],
    intent_tags: list[str],
    defect_type: str | None,
    logistics_normal: bool | None,
) -> str:
    """
    判定本单主争议框架；仅在此处结合服务标、intent facet 与事实字段，下游只读枚举。
    """
    service_tags = materials.get("platform_service_tags") or []
    service_facets = _collect_facets(service_tags if isinstance(service_tags, list) else [])
    all_facets = service_facets | _collect_facets(intent_tags or [])

    if logistics_normal is False or "logistics" in all_facets or "receipt" in all_facets:
        return DISPUTE_FRAME_LOGISTICS
    if "seven_day_return" in service_facets:
        return DISPUTE_FRAME_SEVEN_DAY_RETURN
    if "seven_day_return" in all_facets and is_no_defect_claim(defect_type):
        return DISPUTE_FRAME_SEVEN_DAY_RETURN
    if "description_mismatch" in all_facets or "surface_inconsistency" in all_facets:
        return DISPUTE_FRAME_DESCRIPTION_MISMATCH
    if not is_no_defect_claim(defect_type):
        return DISPUTE_FRAME_QUALITY_DEFECT
    if "quality_claim" in all_facets:
        return DISPUTE_FRAME_QUALITY_DEFECT
    for facet in all_facets:
        mapped = _FACET_TO_DISPUTE_FRAME.get(facet)
        if mapped:
            return mapped
    return DISPUTE_FRAME_UNKNOWN


def attach_primary_dispute_frame(facts: Any, materials: dict[str, Any]) -> Any:
    """
    按 materials 与 facts 字段写入 primary_dispute_frame（测试 replace 模式与生产 extract 共用）。
    """
    frame = resolve_primary_dispute_frame(
        materials=materials,
        intent_tags=list(getattr(facts, "intent_tags", None) or []),
        defect_type=getattr(facts, "defect_type", None),
        logistics_normal=getattr(facts, "logistics_normal", None),
    )
    if getattr(facts, "primary_dispute_frame", None) == frame:
        return facts
    return facts.model_copy(update={"primary_dispute_frame": frame})

