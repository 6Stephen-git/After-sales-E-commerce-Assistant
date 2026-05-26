"""
Agent1 规则导航计划：解析 LLM 输出、API 标注入、C 通道锁定与 section 校验。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.tools.rule_lexicon import (
    CE_CONFIDENCE_THRESHOLD,
    collect_lexicon_search_hints,
    expand_doc_ids_by_lanes,
    format_category_slug_catalog_lines,
    get_category_doc_id,
    get_doc_by_id,
    infer_category_slug_llm,
    infer_lanes_from_intent,
    is_category_doc,
    list_section_candidates_for_doc,
    resolve_doc_id_reference,
    resolve_doc_ids_from_materials,
    resolve_hint_section_keys,
    validate_category_slug,
    validate_section_keys,
)
from schemas import RuleMatchPlan, RuleSearchTerms, SectionSelection

LOG_PREFIX = "[Agent1]"
logger = logging.getLogger(__name__)

LANE_C = "C"
LANE_E = "E"

# 买家口语不应作为 must_terms 单独检索规则正文
COLLOQUIAL_MUST_BLOCKLIST = frozenset(
    {"开箱视频", "没录", "未录", "视频", "聊天记录", "截图"}
)


def merge_llm_rule_plan(
    raw_plan: dict[str, Any] | None,
    materials: dict[str, Any],
    intent_tags: list[str],
    logistics_normal: bool | None,
    text_context: str = "",
    issue_summary: str | None = None,
    category_slugs: list[str] | None = None,
) -> RuleMatchPlan:
    """
    将 LLM 输出的 rule_match_plan 与 materials API 标合并，并执行门控校验。
    """
    plan = _parse_raw_plan(raw_plan)
    buyer_text = _merge_buyer_text(text_context, issue_summary, materials)
    api_doc_ids, api_lanes = resolve_doc_ids_from_materials(materials)
    normalized_slugs = _normalize_category_slugs(category_slugs, materials, raw_plan)

    if not normalized_slugs and not str(materials.get("product_category_slug", "") or "").strip():
        inferred_slug, inferred_conf = infer_category_slug_llm(buyer_text, materials)
        if inferred_slug:
            normalized_slugs.append(inferred_slug)
            plan.category_confidence = max(plan.category_confidence, inferred_conf)
            logger.info(
                "%s 上游未提供 category_slug，品类 LLM 兜底 slug=%s confidence=%.2f",
                LOG_PREFIX,
                inferred_slug,
                inferred_conf,
            )

    plan = _sanitize_target_doc_ids(plan)

    if api_doc_ids:
        plan.target_doc_ids = list(dict.fromkeys(api_doc_ids + plan.target_doc_ids))
        plan.activated_lanes = list(dict.fromkeys(api_lanes + plan.activated_lanes))
        if materials.get("product_category_slug"):
            plan.category_confidence = 1.0
        if materials.get("platform_service_tags"):
            plan.service_confidence = 1.0

    inferred = infer_lanes_from_intent(intent_tags, logistics_normal)
    if inferred:
        plan.activated_lanes = list(dict.fromkeys(plan.activated_lanes + inferred))
        plan.target_doc_ids = list(
            dict.fromkeys(plan.target_doc_ids + expand_doc_ids_by_lanes(inferred))
        )

    plan = _ensure_category_lane(plan, materials, buyer_text, normalized_slugs)
    plan = _apply_ce_confidence_gate(plan, materials, buyer_text, normalized_slugs)
    plan = _sanitize_sections(plan)
    plan = _ensure_base_doc(plan)
    plan = _ensure_doc_default_sections(plan, intent_tags)
    plan = _enrich_search_terms(plan, buyer_text, intent_tags)
    if not plan.search_terms.must_terms:
        plan.search_terms = _default_search_terms(intent_tags, plan)
    return plan


def _format_section_prompt_line(sec: dict[str, Any]) -> str:
    """将单节索引信息格式化为 prompt 一行。"""
    parts = [
        sec.get("section_key", ""),
        sec.get("label", ""),
        f"facets={sec.get('facet_tags', [])}",
    ]
    rule_terms = sec.get("rule_terms_top") or []
    if rule_terms:
        parts.append(f"规则词:{','.join(rule_terms)}")
    alias_hint = str(sec.get("alias_hint", "") or "").strip()
    if alias_hint:
        parts.append(f"口语映射:{alias_hint}")
    excerpt = str(sec.get("excerpt_snippet", "") or "").strip()
    if excerpt:
        parts.append(f"摘录:{excerpt}")
    return " | ".join(str(p) for p in parts if p)


def build_rule_navigation_prompt_block(
    materials: dict[str, Any],
    intent_tags: list[str],
    text_context: str = "",
) -> str:
    """生成写入 Agent1 user prompt 的规则导航说明块。"""
    doc_ids, lanes = resolve_doc_ids_from_materials(materials)
    api_slug = str(materials.get("product_category_slug", "") or "").strip()
    if api_slug:
        cat_doc = get_category_doc_id(api_slug)
        if cat_doc and cat_doc not in doc_ids:
            doc_ids.append(cat_doc)
        if "C" not in lanes:
            lanes.append("C")

    buyer_text = _merge_buyer_text(text_context, None, materials)
    slug_catalog = format_category_slug_catalog_lines()

    lines = [
        "rule_match_plan 字段要求（与事实同次 JSON 输出）：",
        "- activated_lanes: 数组，如 A,C,G",
        "- target_doc_ids: 从下列候选 doc 中选择",
        "- section_selections: 每项含 doc_id、section_keys（必须来自候选表）、confidence(0~1)、reason",
        "- search_terms: {must_terms, should_terms, case_terms, exclude_terms}；"
        "must 须用规则正文用语；case_terms 可填买家原话；"
        "系统会据已选节的 rule_terms/case_aliases 自动补全 must/should，LLM 可少填 must",
        f"- category_confidence / service_confidence: 无 API 品类/服务标时填推断置信度；"
        f"一旦激活 C 通道或选中品类 doc，后续必检索该品类规范（勿因置信度低自行放弃 C）",
        f"- 服务保障 E 通道：无 API 服务标且 service_confidence 低于 {CE_CONFIDENCE_THRESHOLD} 时可不选 E doc",
        "- category_slug: 字符串或 null，从下列 slug 枚举中选择最匹配特殊品类（无图场景必填；有图时可与视觉 slug 一致）",
        "",
        "可选 slug 列表：",
        slug_catalog,
        "",
        "候选 doc 与节（仅可从中勾选 section_keys；★=与当前诉求 facet 更相关）：",
    ]
    for doc_id in doc_ids[:6]:
        doc = get_doc_by_id(doc_id)
        if not doc:
            continue
        lines.append(f"doc_id={doc_id} name={doc.get('doc_name','')}")
        for sec in list_section_candidates_for_doc(doc_id, buyer_text=buyer_text, intent_tags=intent_tags)[:10]:
            prefix = "★ " if sec.get("facet_match") else "  "
            lines.append(f"{prefix}- {_format_section_prompt_line(sec)}")
    if api_slug:
        lines.append(f"API 品类 slug={api_slug}（应激活 C 并勾选对应品类 doc）")
    elif not doc_ids:
        lines.append("（暂无 API 品类/服务标；若聊天可判断品类请填 category_slug 并激活 C）")
    lines.append(f"当前 intent_tags={intent_tags}")
    return "\n".join(lines)


def _parse_raw_plan(raw: dict[str, Any] | None) -> RuleMatchPlan:
    """解析 LLM 返回的 rule_match_plan 字典。"""
    if not isinstance(raw, dict):
        return RuleMatchPlan()
    terms_raw = raw.get("search_terms") if isinstance(raw.get("search_terms"), dict) else {}
    terms = RuleSearchTerms(
        must_terms=_as_str_list(terms_raw.get("must_terms")),
        should_terms=_as_str_list(terms_raw.get("should_terms")),
        case_terms=_as_str_list(terms_raw.get("case_terms")),
        exclude_terms=_as_str_list(terms_raw.get("exclude_terms")),
    )
    selections: list[SectionSelection] = []
    for item in raw.get("section_selections") or []:
        if not isinstance(item, dict):
            continue
        selections.append(
            SectionSelection(
                doc_id=str(item.get("doc_id", "")).strip(),
                section_keys=_as_str_list(item.get("section_keys")),
                confidence=_clamp_float(item.get("confidence"), 0.0),
                reason=str(item.get("reason", "")).strip()[:200],
            )
        )
    return RuleMatchPlan(
        activated_lanes=_as_str_list(raw.get("activated_lanes")),
        target_doc_ids=_as_str_list(raw.get("target_doc_ids")),
        section_selections=selections,
        search_terms=terms,
        category_confidence=_clamp_float(raw.get("category_confidence"), 0.0),
        service_confidence=_clamp_float(raw.get("service_confidence"), 0.0),
    )


def _apply_ce_confidence_gate(
    plan: RuleMatchPlan,
    materials: dict[str, Any],
    buyer_text: str,
    category_slugs: list[str],
) -> RuleMatchPlan:
    """
    E 通道：推断置信度 < 0.75 时移除专项 doc。
    C 通道：一旦触发则必保留，不再因 category_confidence 剔除。
    """
    has_api_category = bool(str(materials.get("product_category_slug", "") or "").strip())
    has_api_service = bool(materials.get("platform_service_tags"))

    doc_ids = list(plan.target_doc_ids)
    lanes = list(plan.activated_lanes)
    selections = list(plan.section_selections)

    category_locked = _is_category_lane_locked(plan, materials, category_slugs)
    if not has_api_category and not category_locked and plan.category_confidence < CE_CONFIDENCE_THRESHOLD:
        doc_ids, lanes, selections = _strip_lane_docs(doc_ids, lanes, selections, LANE_C)

    if not has_api_service and plan.service_confidence < CE_CONFIDENCE_THRESHOLD:
        doc_ids, lanes, selections = _strip_lane_docs(doc_ids, lanes, selections, LANE_E)

    plan.target_doc_ids = doc_ids
    plan.activated_lanes = lanes
    plan.section_selections = selections
    return plan


def _is_category_lane_locked(
    plan: RuleMatchPlan,
    materials: dict[str, Any],
    category_slugs: list[str],
) -> bool:
    """判定特殊品类 C 通道是否已触发（触发后不得剔除品类 doc）。"""
    if str(materials.get("product_category_slug", "") or "").strip():
        return True
    if LANE_C in plan.activated_lanes:
        return True
    if any(is_category_doc(doc_id) for doc_id in plan.target_doc_ids):
        return True
    if category_slugs:
        return True
    return False


def _normalize_category_slugs(
    category_slugs: list[str] | None,
    materials: dict[str, Any],
    raw_plan: dict[str, Any] | None = None,
) -> list[str]:
    """合并 API slug、上游 LLM/视觉 slug 与 rule_match_plan 内嵌 slug，去重保序。"""
    slugs: list[str] = []
    api_slug = validate_category_slug(str(materials.get("product_category_slug", "") or "").strip() or None)
    if api_slug:
        slugs.append(api_slug)
    for item in category_slugs or []:
        validated = validate_category_slug(item)
        if validated and validated not in slugs:
            slugs.append(validated)
    if isinstance(raw_plan, dict):
        nested_slug = validate_category_slug(raw_plan.get("category_slug"))
        if nested_slug and nested_slug not in slugs:
            slugs.append(nested_slug)
    return slugs


def _sanitize_target_doc_ids(plan: RuleMatchPlan) -> RuleMatchPlan:
    """将 target_doc_ids 中的 doc_name/非法引用解析为 canonical doc_id，丢弃无法解析项。"""
    resolved: list[str] = []
    for ref in plan.target_doc_ids:
        doc_id = resolve_doc_id_reference(ref)
        if doc_id and doc_id not in resolved:
            resolved.append(doc_id)
    if len(resolved) != len(plan.target_doc_ids):
        logger.info(
            "%s target_doc_ids 已规范化：%s -> %s",
            LOG_PREFIX,
            plan.target_doc_ids,
            resolved,
        )
    plan.target_doc_ids = resolved
    return plan


def _ensure_category_lane(
    plan: RuleMatchPlan,
    materials: dict[str, Any],
    buyer_text: str,
    category_slugs: list[str],
) -> RuleMatchPlan:
    """API 标、LLM 选择或上游 slug 命中品类时，强制加入 C 通道与品类 doc。"""
    slugs = _normalize_category_slugs(category_slugs, materials)
    if not slugs:
        if _is_category_lane_locked(plan, materials, slugs):
            for existing in plan.target_doc_ids:
                if is_category_doc(existing):
                    doc_id = existing
                    slug = ""
                    slug_confidence = max(plan.category_confidence, CE_CONFIDENCE_THRESHOLD)
                    return _append_category_doc(plan, doc_id, slug, slug_confidence, materials)
        return plan

    for slug in slugs:
        doc_id = get_category_doc_id(slug)
        if not doc_id:
            continue
        slug_confidence = 1.0 if str(materials.get("product_category_slug", "") or "").strip() == slug else max(
            plan.category_confidence,
            CE_CONFIDENCE_THRESHOLD,
        )
        plan = _append_category_doc(plan, doc_id, slug, slug_confidence, materials)
    return plan


def _append_category_doc(
    plan: RuleMatchPlan,
    doc_id: str,
    slug: str,
    slug_confidence: float,
    materials: dict[str, Any],
) -> RuleMatchPlan:
    """将单个品类 doc 写入 plan 并补默认节。"""
    if doc_id not in plan.target_doc_ids:
        plan.target_doc_ids.append(doc_id)
        logger.info("%s 强制加入品类规范 doc_id=%s slug=%s", LOG_PREFIX, doc_id, slug or "-")
    if LANE_C not in plan.activated_lanes:
        plan.activated_lanes.append(LANE_C)

    if not str(materials.get("product_category_slug", "") or "").strip():
        plan.category_confidence = max(plan.category_confidence, slug_confidence, CE_CONFIDENCE_THRESHOLD)

    has_section = any(sel.doc_id == doc_id for sel in plan.section_selections)
    if not has_section:
        doc = get_doc_by_id(doc_id)
        default_keys = (doc or {}).get("default_section_keys") or []
        if default_keys:
            plan.section_selections.append(
                SectionSelection(
                    doc_id=doc_id,
                    section_keys=list(default_keys),
                    confidence=max(plan.category_confidence, CE_CONFIDENCE_THRESHOLD),
                    reason="品类通道触发，默认质量争议节",
                )
            )
    return plan


def _ensure_doc_default_sections(plan: RuleMatchPlan, intent_tags: list[str]) -> RuleMatchPlan:
    """为尚未选节的 doc 写入 lexicon default_section_keys，便于检索词补全与节过滤。"""
    existing = {sel.doc_id for sel in plan.section_selections}
    for doc_id in plan.target_doc_ids:
        if doc_id in existing:
            continue
        doc = get_doc_by_id(doc_id)
        if not doc:
            continue
        keys = resolve_hint_section_keys(doc, None, intent_tags)
        if not keys:
            continue
        plan.section_selections.append(
            SectionSelection(
                doc_id=doc_id,
                section_keys=keys,
                confidence=0.7,
                reason="未显式选节，按索引 default/facet 推断",
            )
        )
    return plan


def _enrich_search_terms(
    plan: RuleMatchPlan,
    buyer_text: str,
    intent_tags: list[str],
) -> RuleMatchPlan:
    """用 lexicon 规则词补全全部激活 doc 的检索词，并将口语从 must 挪到 case。"""
    terms = plan.search_terms
    sanitized_must: list[str] = []
    moved_case: list[str] = []
    for term in terms.must_terms:
        if term in COLLOQUIAL_MUST_BLOCKLIST:
            moved_case.append(term)
        else:
            sanitized_must.append(term)
    terms.must_terms = sanitized_must
    for term in moved_case:
        if term not in terms.case_terms:
            terms.case_terms.append(term)

    for doc_id in plan.target_doc_ids:
        section_keys = next(
            (sel.section_keys for sel in plan.section_selections if sel.doc_id == doc_id),
            None,
        )
        must_add, should_add, case_add = collect_lexicon_search_hints(
            doc_id=doc_id,
            section_keys=section_keys,
            buyer_text=buyer_text,
            intent_tags=intent_tags,
        )
        for bucket, additions in (
            (terms.must_terms, must_add),
            (terms.should_terms, should_add),
            (terms.case_terms, case_add),
        ):
            for item in additions:
                if item not in bucket:
                    bucket.append(item)
    plan.search_terms = terms
    return plan


def _merge_buyer_text(
    text_context: str,
    issue_summary: str | None,
    materials: dict[str, Any],
) -> str:
    """合并用于品类推断与检索词扩展的买家文本。"""
    parts: list[str] = []
    if text_context.strip():
        parts.append(text_context.strip())
    if issue_summary and issue_summary.strip():
        parts.append(issue_summary.strip())
    for key in ("buyer_text", "complaint_text", "description"):
        value = materials.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " ".join(dict.fromkeys(parts))


def _strip_lane_docs(
    doc_ids: list[str],
    lanes: list[str],
    selections: list[SectionSelection],
    lane: str,
) -> tuple[list[str], list[str], list[SectionSelection]]:
    """移除某通道下的全部 doc 与 section 选择。"""
    if lane in lanes:
        lanes = [x for x in lanes if x != lane]
    removed: set[str] = set()
    for doc_id in doc_ids:
        doc = get_doc_by_id(doc_id)
        if doc and doc.get("lane") == lane:
            removed.add(doc_id)
    doc_ids = [d for d in doc_ids if d not in removed]
    selections = [s for s in selections if s.doc_id not in removed]
    return doc_ids, lanes, selections


def _sanitize_sections(plan: RuleMatchPlan) -> RuleMatchPlan:
    """校验 section_keys 属于 lexicon；section 的 doc_id 同步规范化。"""
    cleaned: list[SectionSelection] = []
    for sel in plan.section_selections:
        if not sel.doc_id:
            continue
        doc_id = resolve_doc_id_reference(sel.doc_id)
        if not doc_id:
            continue
        keys = validate_section_keys(doc_id, sel.section_keys)
        if keys:
            cleaned.append(
                SectionSelection(
                    doc_id=doc_id,
                    section_keys=keys,
                    confidence=sel.confidence,
                    reason=sel.reason,
                )
            )
    plan.section_selections = cleaned
    return plan


def _ensure_base_doc(plan: RuleMatchPlan) -> RuleMatchPlan:
    """保证争议处理基本规则 doc 在列表中。"""
    from backend.tools.rule_lexicon import load_lexicon

    lanes = load_lexicon().get("lanes") or {}
    base = (lanes.get("A_base") or [None])[0]
    if base and base not in plan.target_doc_ids:
        plan.target_doc_ids.insert(0, base)
    if "A" not in plan.activated_lanes:
        plan.activated_lanes.insert(0, "A")
    return plan


def _default_search_terms(intent_tags: list[str], plan: RuleMatchPlan | None = None) -> RuleSearchTerms:
    """LLM 与 lexicon 均未产出 must 时的最后兜底。"""
    must = ["举证", "初步凭证", "商品质量问题"]
    should = ["表面不一致", "签收", "确认收货"]
    case: list[str] = []
    text = " ".join(intent_tags)
    if "物流" in text:
        must.extend(["发货", "物流"])
    if "质量" in text:
        case.append("瑕疵")
    if plan and plan.target_doc_ids:
        for doc_id in plan.target_doc_ids[:2]:
            hints = collect_lexicon_search_hints(doc_id, None, "", intent_tags)
            for item in hints[0]:
                if item not in must:
                    must.append(item)
    return RuleSearchTerms(must_terms=must, should_terms=should, case_terms=case, exclude_terms=[])


def _as_str_list(value: Any) -> list[str]:
    """将值转为非空字符串列表。"""
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _clamp_float(value: Any, default: float) -> float:
    """解析 0~1 浮点。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, num))
