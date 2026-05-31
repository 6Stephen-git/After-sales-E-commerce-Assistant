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
    get_category_doc_id,
    get_doc_by_id,
    infer_category_slug_llm,
    infer_lanes_from_intent,
    is_category_doc,
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

# 质量/描述类争点关键词：无 slug 时才条件触发品类推断 LLM
_CATEGORY_DISPUTE_KEYWORDS = (
    "质量",
    "瑕疵",
    "破损",
    "描述不符",
    "不一致",
    "腐烂",
    "变质",
    "划痕",
    "假货",
    "缺件",
    "少件",
    "损坏",
    "功能异常",
    "发霉",
    "过期",
)


def merge_llm_rule_plan(
    materials: dict[str, Any],
    intent_tags: list[str],
    logistics_normal: bool | None,
    text_context: str = "",
    issue_summary: str | None = None,
    category_slugs: list[str] | None = None,
) -> RuleMatchPlan:
    """
    由 API 标、品类 slug、intent 与 lexicon 确定性生成 rule_match_plan。
    """
    plan = RuleMatchPlan()
    buyer_text = _merge_buyer_text(text_context, issue_summary, materials)
    api_doc_ids, api_lanes = resolve_doc_ids_from_materials(materials)
    normalized_slugs = _normalize_category_slugs(category_slugs, materials)

    if (
        not normalized_slugs
        and not str(materials.get("product_category_slug", "") or "").strip()
        and _intent_suggests_category_dispute(intent_tags, buyer_text)
    ):
        inferred_slug, inferred_conf = infer_category_slug_llm(buyer_text, materials)
        if inferred_slug:
            normalized_slugs.append(inferred_slug)
            plan.category_confidence = max(plan.category_confidence, inferred_conf)
            logger.info(
                "%s 质量类争点且无 slug，品类 LLM 兜底 slug=%s confidence=%.2f",
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
) -> list[str]:
    """合并 API slug 与上游事实 LLM category_slug，去重保序。"""
    slugs: list[str] = []
    api_slug = validate_category_slug(str(materials.get("product_category_slug", "") or "").strip() or None)
    if api_slug:
        slugs.append(api_slug)
    for item in category_slugs or []:
        validated = validate_category_slug(item)
        if validated and validated not in slugs:
            slugs.append(validated)
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


def _intent_suggests_category_dispute(intent_tags: list[str], buyer_text: str) -> bool:
    """判断是否为可能需 C 通道专项规范的质量/描述类争点。"""
    blob = f"{' '.join(intent_tags)} {buyer_text}"
    return any(keyword in blob for keyword in _CATEGORY_DISPUTE_KEYWORDS)


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
