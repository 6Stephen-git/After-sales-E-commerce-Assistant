"""
scenario_spec → manual_cases fixture（纯代码组装，不调用 LLM）。
"""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

from scenario_evidence import (
    enrich_materials_logistics,
    evidence_facts_to_facts_override,
    normalize_evidence_quality,
)
from scenario_spec import ScenarioSpec, validate_spec_dict

FIXTURE_LOG_PREFIX = "[SpecToFixture]"
_logger = logging.getLogger(__name__)

VALID_VISUAL_PRESETS = frozenset(
    {"high_evidence", "low_evidence", "medium_evidence", "high_quality_defect", "medium_neutral"}
)
EVIDENCE_LEVEL_TO_PRESET = {
    "high": "high_evidence",
    "low": "low_evidence",
    "medium": "medium_evidence",
}

BUYER_PROFILE_ALIASES = {
    "cumulative_purchases": "purchase_count",
    "historical_dispute_rate": "dispute_rate",
    "reputation_preference": "credit_level",
    "purchase_times": "purchase_count",
    "dispute_times": "dispute_count",
    "仅退款率": "refund_only_rate",
    "refund_only": "refund_only_rate",
}

MALICIOUS_CONTEXT_ALIASES = {
    "refund_only_count": "recent_refund_only_count",
}


def _service_tag_key(text: Any) -> str:
    """服务标标准化键：仅去除展示引号、空白和末尾「服务规范」，用于测试输入归一。"""
    normalized = str(text or "").strip()
    for old, new in (("“", ""), ("”", ""), ("\"", ""), ("'", ""), (" ", ""), ("\u3000", "")):
        normalized = normalized.replace(old, new)
    if normalized.endswith("服务规范"):
        normalized = normalized[: -len("服务规范")]
    return normalized.strip()


def _load_service_tag_map() -> dict[str, str]:
    """读取 lexicon E 通道服务标 → doc_id 映射。"""
    try:
        from backend.tools.rule_lexicon import load_lexicon
    except Exception:  # noqa: BLE001
        return {}
    return dict((load_lexicon().get("lanes") or {}).get("E_service_tag_to_doc_id") or {})


def _build_service_tag_aliases(svc_map: dict[str, str] | None = None) -> dict[str, str]:
    """从规则索引生成服务标别名表（标准名、去引号名 → 唯一 canonical）。"""
    svc_map = svc_map if svc_map is not None else _load_service_tag_map()
    candidates: dict[str, list[str]] = {}
    for canonical in svc_map.keys():
        text = str(canonical or "").strip()
        if not text:
            continue
        keys = {text, _service_tag_key(text)}
        for key in keys:
            if key:
                candidates.setdefault(key, []).append(text)

    aliases: dict[str, str] = {}
    for key, values in candidates.items():
        unique_values = list(dict.fromkeys(values))
        if len(unique_values) == 1:
            aliases[key] = unique_values[0]
    return aliases


def _resolve_one_service_tag(raw: str, svc_map: dict[str, str], aliases: dict[str, str]) -> str | None:
    """
    将单条服务标解析为 lexicon canonical 名；支持精确匹配与中文核心名子串唯一命中。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if text in svc_map:
        return text

    keyed = _service_tag_key(text)
    if keyed in aliases:
        return aliases[keyed]
    if keyed in svc_map:
        return keyed

    fuzzy: list[str] = []
    keyed_lower = keyed.lower()
    for canonical in svc_map.keys():
        canon_key = _service_tag_key(canonical)
        if not canon_key:
            continue
        if keyed_lower == canon_key.lower():
            return canonical
        if len(canon_key) >= 3 and (canon_key in keyed or keyed in canon_key):
            fuzzy.append(canonical)

    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        fuzzy.sort(key=lambda item: len(_service_tag_key(item)), reverse=True)
        return fuzzy[0]
    return None


def _infer_service_tags_from_context(context_blob: str, svc_map: dict[str, str]) -> list[str]:
    """从标题/标签/诉求等文本中推断 lexicon 已登记的服务标（中文核心名子串匹配）。"""
    compact = _service_tag_key(context_blob)
    if not compact or not svc_map:
        return []

    hits: list[tuple[int, str]] = []
    for canonical in svc_map.keys():
        core = _service_tag_key(canonical)
        if len(core) < 3:
            continue
        if core in compact:
            hits.append((len(core), canonical))
    if not hits:
        return []

    hits.sort(key=lambda item: item[0], reverse=True)
    selected: list[str] = []
    selected_cores: list[str] = []
    for _score, canonical in hits:
        core = _service_tag_key(canonical)
        if any(existing in core or core in existing for existing in selected_cores):
            continue
        selected.append(canonical)
        selected_cores.append(core)
    return selected


def _normalize_service_tags(
    tags: Any,
    *,
    meta_tags: list[str] | None = None,
    title: str = "",
    issue_summary: str = "",
    defect_type: str = "",
) -> list[str]:
    """
    将 scenario 服务标归一为 lexicon 标准名。

    优先解析 materials 字段；其次 meta.tags；仍无命中时从标题/诉求/瑕疵类型补全。
    自造 slug 无法命中时不原样透传，避免 E 通道 silent miss。
    """
    svc_map = _load_service_tag_map()
    aliases = _build_service_tag_aliases(svc_map)
    normalized: list[str] = []
    raw_items = tags if isinstance(tags, list) else []

    def _append(canonical: str | None) -> None:
        if canonical and canonical not in normalized:
            normalized.append(canonical)

    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        canonical = _resolve_one_service_tag(text, svc_map, aliases)
        if canonical:
            _append(canonical)
        else:
            _logger.warning(
                "%s 无法解析 platform_service_tag=%s，将尝试从 meta.tags 与情景摘要补全",
                FIXTURE_LOG_PREFIX,
                text,
            )

    for item in meta_tags or []:
        _append(_resolve_one_service_tag(str(item or "").strip(), svc_map, aliases))

    if not normalized:
        focused_blob = " ".join(
            part for part in (title, issue_summary, defect_type) if str(part or "").strip()
        )
        if focused_blob.strip():
            inferred = _infer_service_tags_from_context(focused_blob, svc_map)
            for canonical in inferred:
                _append(canonical)
            if inferred:
                _logger.info(
                    "%s 已从情景摘要补全服务标：%s",
                    FIXTURE_LOG_PREFIX,
                    inferred,
                )

    return normalized


def _normalize_buyer_profile(raw: dict[str, Any], *, case_id: str, materials: dict[str, Any]) -> dict[str, Any]:
    """纠偏生成器常见字段别名与缺失 buyer_id。"""
    profile = copy.deepcopy(raw or {})
    for old_key, new_key in BUYER_PROFILE_ALIASES.items():
        if old_key in profile and new_key not in profile:
            profile[new_key] = profile.pop(old_key)
    for rate_key in ("return_rate", "refund_only_rate", "dispute_rate"):
        if rate_key in profile and profile[rate_key] is not None:
            try:
                profile[rate_key] = max(0.0, min(1.0, float(profile[rate_key])))
            except (TypeError, ValueError):
                profile.pop(rate_key, None)

    resolved_buyer_id = str(
        profile.get("buyer_id") or materials.get("buyer_id") or f"buyer_{case_id.replace('-', '_').lower()}"
    ).strip()
    profile["buyer_id"] = resolved_buyer_id
    return profile


def _normalize_chat_history(chat_history: Any) -> list[dict[str, str]]:
    """将 text 字段归一为 content。"""
    if not isinstance(chat_history, list):
        return []
    normalized: list[dict[str, str]] = []
    for turn in chat_history:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "buyer").strip()
        if role == "user":
            role = "buyer"
        content = turn.get("content")
        if content is None:
            content = turn.get("text", "")
        normalized.append({"role": role, "content": str(content)})
    return normalized


def _service_tag_inputs_from_spec(spec: ScenarioSpec) -> dict[str, Any]:
    """提取服务标归一化所需的结构化字段。"""
    ef = spec.evidence_facts
    return {
        "meta_tags": list(spec.meta.tags or []),
        "title": spec.meta.title or "",
        "issue_summary": ef.issue_summary or "",
        "defect_type": ef.defect_type or "",
    }


def _normalize_materials(
    materials: dict[str, Any],
    *,
    case_id: str,
    buyer_id: str,
    service_tag_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """强制无图测试默认，补齐 dispute_id；仅校验 slug 字段，中文品类留给跑批语义推断。"""
    out = copy.deepcopy(materials or {})
    out["image_urls"] = []
    out.setdefault("evidence_images", [])
    from backend.tools.rule_lexicon import validate_category_slug

    for key in ("product_category_slug", "category_slug"):
        raw_slug = str(out.get(key) or "").strip()
        if not raw_slug:
            continue
        validated = validate_category_slug(raw_slug)
        if validated:
            out["product_category_slug"] = validated
            break
        if key == "product_category_slug":
            out.pop("product_category_slug", None)
    svc_inputs = service_tag_inputs or {}
    out["platform_service_tags"] = _normalize_service_tags(
        out.get("platform_service_tags"),
        meta_tags=svc_inputs.get("meta_tags"),
        title=str(svc_inputs.get("title") or ""),
        issue_summary=str(svc_inputs.get("issue_summary") or ""),
        defect_type=str(svc_inputs.get("defect_type") or ""),
    )
    out["chat_history"] = _normalize_chat_history(out.get("chat_history"))
    out.setdefault("dispute_id", f"DISPUTE-{case_id}")
    out.setdefault("buyer_id", buyer_id)
    out.setdefault("reset_context", True)
    out.setdefault("materials_snapshot", True)
    return out


def _normalize_malicious_context(raw: dict[str, Any]) -> dict[str, Any]:
    """恶意上下文字段名纠偏。"""
    ctx = copy.deepcopy(raw or {})
    for old_key, new_key in MALICIOUS_CONTEXT_ALIASES.items():
        if old_key in ctx and new_key not in ctx:
            ctx[new_key] = ctx.pop(old_key)
    return ctx


def _parse_amount_from_text(value: Any) -> float | None:
    """从「累计消费150元」「门槛100」等文案中提取首个数字。"""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _normalize_test_overrides_keys(overrides: dict[str, Any]) -> dict[str, Any]:
    """
    将情景 LLM 常见别名归一为 run_manual_cases 识别的 test_overrides 键。

    跑批只认：channel_threshold、order_value_amount_only_threshold、
    order_value_score_threshold、customer_lifetime_value、malicious_hard_rules 等。
    """
    normalized = dict(overrides)

    if "channel_threshold" not in normalized:
        for alias in (
            "channel_threshold",
            "old_customer_value",
            "lifetime_value_threshold",
            "long_term_value_threshold",
            "cumulative_spend_threshold",
            "老客价值",
        ):
            if alias in normalized:
                amount = _parse_amount_from_text(normalized[alias])
                if amount is not None:
                    normalized["channel_threshold"] = amount
                    break

    if "order_value_amount_only_threshold" not in normalized:
        for alias in (
            "order_value_amount_only_threshold",
            "order_amount_threshold",
            "order_channel_threshold",
            "本单金额通道门槛",
            "本单金额通道",
        ):
            if alias in normalized:
                amount = _parse_amount_from_text(normalized[alias])
                if amount is not None:
                    normalized["order_value_amount_only_threshold"] = amount
                    break

    if normalized.get("customer_lifetime_value") in (None, ""):
        for alias in ("customer_lifetime_value", "old_customer_value", "lifetime_value", "累计消费"):
            if alias in normalized:
                amount = _parse_amount_from_text(normalized[alias])
                if amount is not None:
                    normalized["customer_lifetime_value"] = amount
                    break

    if "order_value_score_threshold" in normalized:
        amount = _parse_amount_from_text(normalized["order_value_score_threshold"])
        if amount is not None:
            normalized["order_value_score_threshold"] = int(amount)

    return normalized


def _merge_test_overrides(spec: ScenarioSpec) -> dict[str, Any]:
    """合并 test_overrides；visual_preset 仅表证据强弱，不写瑕疵类型。"""
    overrides = _normalize_test_overrides_keys(copy.deepcopy(spec.test_overrides or {}))
    preset = overrides.get("visual_preset")
    if not preset:
        overrides["visual_preset"] = EVIDENCE_LEVEL_TO_PRESET.get(
            spec.taxonomy.evidence_level, "medium_evidence"
        )
    elif preset not in VALID_VISUAL_PRESETS:
        overrides["visual_preset"] = "medium_evidence"
    overrides.setdefault("agent1_mode", "replace" if spec.facts_override else "merge_text")
    return overrides


def _merge_facts_override(spec: ScenarioSpec) -> dict[str, Any]:
    """情景「事实证据」→ facts_override；证据强弱可与 taxonomy 对齐。"""
    facts = evidence_facts_to_facts_override(spec)
    level = spec.taxonomy.evidence_level
    facts.setdefault("evidence_quality", normalize_evidence_quality(facts.get("evidence_quality") or level))
    if facts.get("goods_received") is None:
        facts.setdefault("goods_received", True)
    return facts


def spec_to_case_dict(spec: ScenarioSpec) -> dict[str, Any]:
    """单条 scenario_spec → manual_cases 单 case 字典。"""
    case_id = spec.meta.case_id.strip()
    if not case_id:
        raise ValueError(f"{FIXTURE_LOG_PREFIX} meta.case_id 不能为空")

    materials_src = spec.materials or {}
    service_tag_inputs = _service_tag_inputs_from_spec(spec)
    profile = _normalize_buyer_profile(spec.buyer_profile, case_id=case_id, materials=materials_src)
    buyer_id = str(profile["buyer_id"])

    case: dict[str, Any] = {
        "meta": {
            "case_id": case_id,
            "title": spec.meta.title or case_id,
            "tags": list(spec.meta.tags or []),
            **({"source_key": spec.meta.source_key} if getattr(spec.meta, "source_key", None) else {}),
        },
        "buyer_profile": profile,
        "similar_cases": copy.deepcopy(spec.similar_cases or []),
        "test_overrides": _merge_test_overrides(spec),
    }

    malicious = _normalize_malicious_context(spec.malicious_context)
    if malicious:
        case["malicious_context"] = malicious

    case["facts_override"] = _merge_facts_override(spec)

    steps = spec.steps or []
    if steps:
        normalized_steps: list[dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_materials = _normalize_materials(
                step.get("materials") or {},
                case_id=case_id,
                buyer_id=buyer_id,
                service_tag_inputs=service_tag_inputs,
            )
            normalized_steps.append(
                {
                    "label": str(step.get("label") or "步骤"),
                    "materials": step_materials,
                    **({"facts_override": step["facts_override"]} if step.get("facts_override") else {}),
                }
            )
        case["steps"] = normalized_steps
    else:
        materials_norm = _normalize_materials(
            materials_src,
            case_id=case_id,
            buyer_id=buyer_id,
            service_tag_inputs=service_tag_inputs,
        )
        case["materials"] = enrich_materials_logistics(materials_norm, spec)

    return case


def spec_to_fixture_payload(spec: ScenarioSpec, *, version: str = "1.0") -> dict[str, Any]:
    """包装为 manual_cases 顶层结构。"""
    return {"version": version, "cases": [spec_to_case_dict(spec)]}


def merge_specs_to_payload(specs: list[ScenarioSpec], *, version: str = "1.0") -> dict[str, Any]:
    """多条 spec 合并为一个 fixture 文件。"""
    return {"version": version, "cases": [spec_to_case_dict(s) for s in specs]}


def fixture_from_spec_dict(payload: dict[str, Any], *, version: str = "1.0") -> dict[str, Any]:
    """dict → 校验 → fixture payload。"""
    spec = validate_spec_dict(payload)
    return spec_to_fixture_payload(spec, version=version)


def write_fixture(payload: dict[str, Any], path: Path) -> Path:
    """落盘 fixture JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_spec(path: Path) -> ScenarioSpec:
    """从 JSON 文件加载 spec。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{FIXTURE_LOG_PREFIX} spec 文件根节点必须是对象：{path}")
    return validate_spec_dict(data)
