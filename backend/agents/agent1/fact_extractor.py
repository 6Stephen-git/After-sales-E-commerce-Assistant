"""
Agent 1：事实还原员。

职责：仅做客观事实归纳，不判责、不调策略；外部能力仅通过 Tools 层封装调用。
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from schemas import (
    CREDENTIAL_TRUST_SUSPECT,
    CREDENTIAL_TRUST_TRUSTED,
    CREDENTIAL_TRUST_UNKNOWN,
    EVIDENCE_HIGH,
    EVIDENCE_LOW,
    EVIDENCE_MEDIUM,
    FactOutput,
    VALID_CREDENTIAL_TRUST,
)

from backend.agents.agent1.dispute_frame import resolve_primary_dispute_frame
from backend.agents.agent1.rule_plan import merge_llm_rule_plan

from backend.tools.agent1_tools import (
    VISUAL_DEFECT_SEVERITY_VALUES,
    VISUAL_GOODS_RECOVERABILITY_VALUES,
    analyze_image,
)
from backend.tools.llm_client import chat_completion
from backend.tools.platform_api import query_logistics
from backend.tools.rule_lexicon import format_category_slug_compact, validate_category_slug
from backend.tools.text_signals import contains_any, signal_group

LOG_PREFIX = "[Agent1]"
logger = logging.getLogger(__name__)

_VISUAL_SEVERITY_RANK = {"minor": 1, "moderate": 2, "severe": 3}
_VISUAL_RECOVERABILITY_LOSS_RANK = {"resalable": 1, "repairable": 2, "unrecoverable": 3}


def _merge_credential_trust_from_visions(vision_results: list[dict[str, Any]]) -> tuple[str, str | None]:
    """
    多图合并举证可信度：任一 suspect 则 suspect；全部 trusted 则 trusted；否则 unknown。

    仅依据视觉模型结构化字段，不再由下游关键词复判。
    """
    trusts: list[str] = []
    notes: list[str] = []
    for item in vision_results:
        if item.get("error"):
            continue
        trust = item.get("credential_trust")
        if isinstance(trust, str) and trust in VALID_CREDENTIAL_TRUST:
            trusts.append(trust)
        note = str(item.get("credential_trust_note") or "").strip()
        if note:
            notes.append(note)
    if any(t == CREDENTIAL_TRUST_SUSPECT for t in trusts):
        return CREDENTIAL_TRUST_SUSPECT, notes[0] if notes else None
    if trusts and all(t == CREDENTIAL_TRUST_TRUSTED for t in trusts):
        return CREDENTIAL_TRUST_TRUSTED, notes[0] if notes else None
    return CREDENTIAL_TRUST_UNKNOWN, None


def _merge_visual_defect_severity(current: str | None, new: str | None) -> str | None:
    """多图合并严重度：取更高等级。"""
    if not new:
        return current
    if not current:
        return new
    if _VISUAL_SEVERITY_RANK[new] > _VISUAL_SEVERITY_RANK[current]:
        return new
    return current


def _merge_visual_goods_recoverability(current: str | None, new: str | None) -> str | None:
    """多图合并可挽回性：取损失更大（越不可挽回）的一侧。"""
    if not new:
        return current
    if not current:
        return new
    if _VISUAL_RECOVERABILITY_LOSS_RANK[new] > _VISUAL_RECOVERABILITY_LOSS_RANK[current]:
        return new
    return current


# ---------- 纠纷材料解析：图片 URL 与可检索文本上下文 ----------
def _collect_image_urls(materials: dict[str, Any]) -> list[str]:
    """
    从 materials 中收集所有图片 URL。

    合并 `image_urls` 与 `evidence_images` 两个常见字段，去空白、去非字符串项。
    顺序：先 image_urls，再 evidence_images，便于与前端传参习惯对齐。

    参数:
        materials: Controller 传入的纠纷材料字典。

    返回:
        非空字符串 URL 列表；无图片时为空列表。
    """
    image_urls = materials.get("image_urls", []) or []
    evidence_images = materials.get("evidence_images", []) or []
    urls: list[str] = []
    for item in image_urls + evidence_images:
        if isinstance(item, str) and item.strip():
            urls.append(item.strip())
    return urls


def _collect_text(materials: dict[str, Any]) -> str:
    """
    从 materials 中拼出用于关键词推断的纯文本上下文。

    来源包括：buyer_text、complaint_text、description，以及 chat_history 里
    每条消息的 content（dict）或整条字符串。

    参数:
        materials: 纠纷材料字典。

    返回:
        各段文本用空格拼接成的一串字符串；可能为空。
    """
    parts: list[str] = []
    for key in ("buyer_text", "complaint_text", "description"):
        value = materials.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    for msg in materials.get("chat_history", []) or []:
        if isinstance(msg, dict):
            text = msg.get("content")
            if isinstance(text, str) and text.strip():
                role = str(msg.get("role", "") or "").strip()
                if role:
                    parts.append(f"{role}: {text.strip()}")
                else:
                    parts.append(text.strip())
        elif isinstance(msg, str) and msg.strip():
            parts.append(msg.strip())
    return " ".join(parts)


RULE_CONTEXT_MATERIAL_KEYS = (
    "platform_service_tags",
    "service_tags",
    "after_sale_timing",
    "time_since_delivery_hours",
    "application_reason",
    "refund_reason",
    "policy_limits",
    "compensation_ratio_cap",
)


def _merge_rule_context_attributes(attributes: dict[str, Any], materials: dict[str, Any]) -> dict[str, Any]:
    """
    将材料中的通用规则判定上下文写入 attributes.rule_context。

    参数:
        attributes: Agent1 已抽取的扩展属性。
        materials: Controller 传入的原始材料，可能包含服务标、售后时效、申请原因等。

    返回:
        合并后的 attributes；不把规则上下文字段散落成 FactOutput 顶层字段。
    """
    merged = dict(attributes or {})
    rule_context = dict(merged.get("rule_context") or {})
    for key in RULE_CONTEXT_MATERIAL_KEYS:
        if key not in materials:
            continue
        value = materials.get(key)
        if value in (None, "", [], {}):
            continue
        rule_context[key] = value
    if rule_context:
        merged["rule_context"] = rule_context
    return merged


# ---------- 通用解析：模型 JSON、布尔、列表与浮点 ----------
def _parse_json_text(raw_text: str) -> dict[str, Any] | None:
    """
    将 LLM 字符串结果解析为 JSON 对象。
    """
    normalized = raw_text.strip()
    if not normalized:
        return None
    if normalized.startswith("```"):
        normalized = normalized.replace("```json", "").replace("```", "").strip()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _parse_bool(value: Any) -> bool | None:
    """
    将 LLM 输出的布尔语义转为 bool。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "是"}:
            return True
        if lowered in {"false", "0", "no", "否"}:
            return False
    return None


def _safe_float(value: Any, default: float) -> float:
    """
    将任意值转换为 [0,1] 浮点，失败时回退默认值。
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _extend_unique(target_list: list[str], new_items: Any) -> None:
    """
    将字符串列表去重追加到目标列表。
    """
    if not isinstance(new_items, list):
        return
    for item in new_items:
        text = str(item or "").strip()
        if text and text not in target_list:
            target_list.append(text)


# ---------- 证据评估：按材料覆盖度给档位与置信度 ----------
def _derive_evidence_quality(has_text: bool, has_images: bool, has_logistics: bool, red_flag_count: int) -> str:
    """
    根据文本/图片/物流覆盖度给证据档位，并对疑点做降档处理。
    """
    score = int(has_text) + int(has_images) + int(has_logistics)
    if score <= 1:
        return EVIDENCE_LOW
    if score == 2 or red_flag_count >= 1:
        return EVIDENCE_MEDIUM
    return EVIDENCE_HIGH


def _derive_decision_readiness(
    *,
    evidence_quality: str,
    credential_trust: str,
    missing_evidence: list[str],
    visual_observations: list[str],
    red_flags: list[str],
    defect_type: str | None,
    visual_defect_severity: str | None,
) -> tuple[str, str]:
    """
    综合判断事实是否足以支撑终局决策（退款/补偿/拒赔）。

    与 evidence_quality（覆盖度）的区别：
    - evidence_quality 只看「文本+图+物流」三类材料有没有
    - decision_readiness 还看图是否可信、视觉结论是否可用、缺陷严重度是否明确

    参数:
        evidence_quality: 材料覆盖度档位。
        credential_trust: 举证图片可信度。
        missing_evidence: 缺失的证据项列表。
        visual_observations: 视觉观察结论列表。
        red_flags: 疑点列表。
        defect_type: 瑕疵类型。
        visual_defect_severity: 视觉缺陷严重度。

    返回:
        (readiness_level, note) 元组。
    """
    if evidence_quality == EVIDENCE_LOW:
        return EVIDENCE_LOW, "材料覆盖不足，关键信息缺失"

    score = 0
    reasons: list[str] = []

    if visual_observations:
        score += 1
    else:
        reasons.append("无可用视觉结论")

    if credential_trust == CREDENTIAL_TRUST_TRUSTED:
        score += 1
    elif credential_trust == CREDENTIAL_TRUST_SUSPECT:
        score -= 1
        reasons.append("举证图可信度存疑")

    if not missing_evidence:
        score += 1
    elif len(missing_evidence) >= 2:
        score -= 1
        reasons.append(f"缺{len(missing_evidence)}项关键证据")

    if len(red_flags) >= 2:
        score -= 1
        reasons.append(f"有{len(red_flags)}个疑点待核实")

    normalized_defect = str(defect_type or "").strip()
    has_real_defect = normalized_defect and normalized_defect not in {"无", "暂无", "无瑕疵", "无质量问题", "无明显瑕疵", "没有瑕疵"}
    if has_real_defect:
        if visual_defect_severity in {"moderate", "severe"}:
            score += 1
        elif visual_defect_severity is None:
            reasons.append("有瑕疵主张但无视觉严重度判定")

    if score >= 2:
        level = EVIDENCE_HIGH
        note = "；".join(reasons) if reasons else "证据链基本完整，可支撑决策"
    elif score >= 0:
        level = EVIDENCE_MEDIUM
        note = "；".join(reasons) if reasons else "部分维度待补充"
    else:
        level = EVIDENCE_LOW
        note = "；".join(reasons) if reasons else "关键证据不足"

    return level, note


def _derive_confidence(evidence_quality: str, llm_confidence: float, red_flag_count: int) -> float:
    """
    融合档位与 LLM 置信度得到最终置信度。
    """
    quality_base = {
        EVIDENCE_LOW: 0.35,
        EVIDENCE_MEDIUM: 0.62,
        EVIDENCE_HIGH: 0.82,
    }[evidence_quality]
    fused = quality_base * 0.55 + llm_confidence * 0.45
    return max(0.0, min(1.0, fused - red_flag_count * 0.08))


# ---------- 诉求提取：先从聊天记录抽核心诉求，再指导视觉分析 ----------
def _llm_extract_issue(
    text_context: str,
    logistics_signed: bool | None,
    materials: dict[str, Any],
) -> dict[str, Any] | None:
    """
    调用 LLM 提取核心诉求与事实字段；规则导航由 merge_llm_rule_plan 确定性生成。
    """
    if not text_context:
        return None
    api_slug = str(materials.get("product_category_slug", "") or "").strip()
    slug_line = ""
    if not api_slug:
        compact_slugs = format_category_slug_compact()
        if compact_slugs:
            slug_line = (
                f"- category_slug: 字符串或 null，可选枚举 [{compact_slugs}]；无法判断填 null\n"
            )

    system_prompt = (
        "你是售后事实提取助手。只抽取客观事实，不做责任归因；疑点仅描述「观察到的矛盾或待核实点」，不对买家做道德定性。"
        "请输出 JSON。"
    )
    user_prompt = (
        "根据聊天记录提取核心诉求并返回 JSON，不要输出其他内容。\n"
        "注意：用户诉求不等于退款/退货。只要用户反馈问题（如商品瑕疵、物流异常、描述不符等）就属于有效诉求。\n"
        "若聊天中存在多个未解决诉求，应在 intent_tags / issue_summary 中体现主要矛盾。\n"
        "字段要求：\n"
        "- issue_summary: 字符串，一句话总结买家核心诉求\n"
        "- intent_tags: 字符串数组，如 质量问题/物流异常/退款诉求\n"
        "- goods_received: 布尔或null\n"
        "- confidence: 0~1 浮点\n"
        "- missing_evidence: 字符串数组，列仍缺的关键举证（如近景视频、开箱连续录像等），跨品类通用\n"
        "- red_flags: 字符串数组。**每条独立、简短、可展示给商家**；用于「疑点列表」，覆盖但不限于：\n"
        "  · 图文/陈述与客观材料可能不一致（如声称霉变但图片更像其他状态、时间线对不上）\n"
        "  · 证据来源或真实性待核实（如第三方水印、非本单背景、关键信息被遮挡）\n"
        "  · 物流、签收、商品状态等与其他字段或常识存在矛盾\n"
        "  · 诉求与已提供证据能支撑的结论相比过度或不清\n"
        "  无则填 []；禁止把整段聊天粘进单条 red_flags；禁止单一条目硬编码某一品类示例句。\n"
        f"{slug_line}"
        "聊天记录：\n"
        f"{text_context}\n"
        f"物流签收状态：{logistics_signed}\n"
        f"product_category_slug(API)={api_slug or '无'}\n"
        f"platform_service_tags={materials.get('platform_service_tags', [])}\n"
    )
    llm_text = chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model_env_key="AGENT1_LLM_MODEL",
        temperature=0.1,
    )
    if not llm_text:
        return None
    return _parse_json_text(llm_text)


# ---------- 视觉指引：事实 LLM 完成后，用提炼诉求锚定多模态分析 ----------
def _build_vision_guidance(
    materials: dict[str, Any],
    issue_result: dict[str, Any] | None,
    *,
    buyer_text: str,
    text_context: str,
) -> str:
    """
    拼装供视觉模型使用的买家诉求文本；优先 issue_summary，其次原始材料。
    """
    parts: list[str] = []
    if isinstance(issue_result, dict):
        summary = str(issue_result.get("issue_summary", "") or "").strip()
        if summary:
            parts.append(summary)
        tags = [str(item).strip() for item in (issue_result.get("intent_tags") or []) if str(item).strip()]
        if tags:
            parts.append(f"诉求标签：{'、'.join(tags)}")
    if not parts and buyer_text.strip():
        parts.append(buyer_text.strip())
    if not parts:
        for key in ("complaint_text", "description"):
            value = materials.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    if not parts and text_context.strip():
        parts.append(text_context.strip()[:240])
    return "\n".join(dict.fromkeys(parts))


# ---------- 语义兜底：模型不可用时用最小关键词保证收货判断可用 ----------
def _fallback_infer_goods_received(text_context: str) -> bool | None:
    """
    在 LLM 不可用时，从文本中做最小化收货判断。
    """
    if not text_context:
        return None
    if contains_any(text_context, signal_group("goods_received_negative")):
        return False
    if contains_any(text_context, signal_group("goods_received_positive")):
        return True
    return None


def _collect_category_slugs(
    issue_result: dict[str, Any] | None,
    vision_category_slug: str | None = None,
) -> list[str]:
    """
    合并品类 slug：文本事实 LLM 优先，视觉 slug 次之（API slug 由 merge 单独注入）。
    """
    slugs: list[str] = []
    if isinstance(issue_result, dict):
        text_slug = validate_category_slug(str(issue_result.get("category_slug", "") or "").strip() or None)
        if text_slug:
            slugs.append(text_slug)
    if vision_category_slug:
        validated = validate_category_slug(vision_category_slug)
        if validated and validated not in slugs:
            slugs.append(validated)
    return slugs


def _analyze_single_image(image_url: str, guidance: str) -> dict[str, Any]:
    """
    包装单图视觉分析，供线程池并发调用。
    """
    return analyze_image(image_url=image_url, guidance=guidance)


# ---------- 主入口：物流 + 多模态 + 规则化疑点，输出 FactOutput ----------
def extract(materials: dict[str, Any]) -> FactOutput:
    """
    从纠纷材料中提取结构化事实，输出严格符合 schemas.FactOutput。

    流程概要：校验输入 → 诉求提取 → 按诉求引导多模态分析 → 汇总事实与证据结构。

    参数:
        materials: 须为 dict；建议包含 order_id、image_urls/evidence_images、
            buyer_text 或 chat_history 等；具体键由 Controller 与前端约定。

    返回:
        FactOutput 实例。

    异常:
        ValueError: materials 非 dict 时抛出，前缀为 [Agent1]。
    """
    if not isinstance(materials, dict):
        raise ValueError(f"{LOG_PREFIX} 输入 materials 必须是 dict")

    order_id = str(materials.get("order_id", "") or "").strip()
    buyer_text = str(materials.get("buyer_text", "") or "").strip()
    image_urls = _collect_image_urls(materials=materials)
    text_context = _collect_text(materials=materials)

    missing_evidence: list[str] = []
    red_flags: list[str] = []
    uncertainty_reasons: list[str] = []

    if not order_id:
        missing_evidence.append("缺少订单号，无法查询物流状态")
    if not image_urls:
        missing_evidence.append("缺少举证图片")
    if not text_context:
        missing_evidence.append("缺少买家文字描述或聊天记录")

    logistics_info = query_logistics(order_id=order_id) if order_id else None
    logistics_normal = None if logistics_info is None else (not logistics_info.is_abnormal)

    issue_result: dict[str, Any] | None = None
    vision_results: list[dict[str, Any]] = []
    batch_start = time.perf_counter()

    # ---------- Batch0：先事实 LLM，再按诉求并行调视觉 LLM ----------
    if text_context:
        issue_result = _llm_extract_issue(
            text_context=text_context,
            logistics_signed=(None if logistics_info is None else logistics_info.is_signed),
            materials=materials,
        )

    image_cap = image_urls[:3]
    if image_cap:
        vision_guidance = _build_vision_guidance(
            materials=materials,
            issue_result=issue_result,
            buyer_text=buyer_text,
            text_context=text_context,
        )
        max_workers = max(1, min(3, len(image_cap)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            image_futures = [
                executor.submit(_analyze_single_image, image_url=url, guidance=vision_guidance)
                for url in image_cap
            ]
            vision_results = [future.result() for future in image_futures]

    logger.info(
        "%s Batch0 串行完成 elapsed_ms=%s images=%s has_text=%s",
        LOG_PREFIX,
        int((time.perf_counter() - batch_start) * 1000),
        len(image_cap),
        bool(text_context),
    )

    issue_summary = None
    intent_tags: list[str] = []
    goods_received = None
    llm_confidence = 0.45
    if isinstance(issue_result, dict):
        issue_summary = str(issue_result.get("issue_summary", "")).strip() or None
        _extend_unique(intent_tags, issue_result.get("intent_tags"))
        llm_goods_received = _parse_bool(issue_result.get("goods_received"))
        if llm_goods_received is not None:
            goods_received = llm_goods_received
        llm_confidence = _safe_float(issue_result.get("confidence"), default=0.45)
        _extend_unique(missing_evidence, issue_result.get("missing_evidence"))
        _extend_unique(red_flags, issue_result.get("red_flags"))
    else:
        uncertainty_reasons.append("核心诉求提取失败，已回退基础摘要")

    if not issue_summary:
        # ---------- 诉求兜底：优先使用最近买家消息，避免混入过往历史语义 ----------
        issue_summary = buyer_text or (text_context[:80] if text_context else "买家诉求待补充")

    if goods_received is None:
        goods_received = _fallback_infer_goods_received(text_context=text_context)
    if goods_received is None and logistics_info is not None:
        goods_received = logistics_info.is_signed

    visual_observations: list[str] = []
    attributes: dict[str, Any] = {}
    defect_type = None
    defect_location = None
    defect_edge = None
    has_tag_visible = None
    photo_background = None
    wear_signs = None
    visual_defect_severity: str | None = None
    visual_goods_recoverability: str | None = None
    visual_category_slug: str | None = None
    evidence_items: list[dict[str, Any]] = []

    if text_context:
        evidence_items.append({"type": "text", "content": text_context})
    if logistics_info is not None:
        evidence_items.append(
            {
                "type": "logistics",
                "order_id": order_id,
                "is_signed": logistics_info.is_signed,
                "is_abnormal": logistics_info.is_abnormal,
                "stagnant_days": logistics_info.stagnant_days,
            }
        )

    for image_url, image_result in zip(image_cap, vision_results):
        evidence_items.append({"type": "image", "url": image_url})
        if image_result.get("error"):
            error_text = str(image_result["error"])
            uncertainty_reasons.append(error_text)
            red_flags.append("图片分析失败，视觉证据暂不可靠")
            continue

        visual_description = str(image_result.get("visual_description", "")).strip()
        if visual_description:
            visual_observations.append(visual_description)
        _extend_unique(visual_observations, image_result.get("findings"))
        _extend_unique(red_flags, image_result.get("visual_red_flags"))

        raw_attrs = image_result.get("attributes")
        if isinstance(raw_attrs, dict):
            for key, value in raw_attrs.items():
                key_text = str(key or "").strip()
                if key_text and value is not None and key_text not in attributes:
                    attributes[key_text] = value

        if defect_type is None:
            defect_type = image_result.get("defect_type")
        if defect_location is None:
            defect_location = image_result.get("defect_location")
        if defect_edge is None:
            defect_edge = image_result.get("edge_condition")
        if has_tag_visible is None:
            has_tag_visible = image_result.get("has_tag")
        if photo_background is None:
            photo_background = image_result.get("background")
        if wear_signs is None:
            wear_signs = image_result.get("wear_signs")

        candidate_severity = image_result.get("visual_defect_severity")
        if isinstance(candidate_severity, str) and candidate_severity in VISUAL_DEFECT_SEVERITY_VALUES:
            visual_defect_severity = _merge_visual_defect_severity(visual_defect_severity, candidate_severity)
        candidate_recoverability = image_result.get("visual_goods_recoverability")
        if isinstance(candidate_recoverability, str) and candidate_recoverability in VISUAL_GOODS_RECOVERABILITY_VALUES:
            visual_goods_recoverability = _merge_visual_goods_recoverability(
                visual_goods_recoverability,
                candidate_recoverability,
            )
        if visual_category_slug is None:
            candidate_slug = image_result.get("category_slug")
            if isinstance(candidate_slug, str) and candidate_slug.strip():
                visual_category_slug = validate_category_slug(candidate_slug)

    if logistics_info and goods_received is False and logistics_info.is_signed:
        red_flags.append("买家称未收到货，但物流显示已签收")
    if logistics_info and logistics_info.is_abnormal:
        red_flags.append(f"物流异常：停滞 {logistics_info.stagnant_days} 天")
    if not visual_observations and image_urls:
        missing_evidence.append("缺少可用视觉分析结论，建议补充更清晰图片或视频")

    credential_trust, credential_trust_note = _merge_credential_trust_from_visions(vision_results)
    if not image_urls:
        credential_trust = CREDENTIAL_TRUST_UNKNOWN
        credential_trust_note = None

    evidence_quality = _derive_evidence_quality(
        has_text=bool(text_context),
        has_images=bool(image_urls),
        has_logistics=bool(logistics_info),
        red_flag_count=len(red_flags),
    )
    confidence = _derive_confidence(
        evidence_quality=evidence_quality,
        llm_confidence=llm_confidence,
        red_flag_count=len(red_flags),
    )

    # 可决策度：综合覆盖度、视觉结论、举证可信度、缺证、缺陷严重度
    decision_readiness, decision_readiness_note = _derive_decision_readiness(
        evidence_quality=evidence_quality,
        credential_trust=credential_trust,
        missing_evidence=missing_evidence,
        visual_observations=visual_observations,
        red_flags=red_flags,
        defect_type=defect_type,
        visual_defect_severity=visual_defect_severity,
    )

    uncertain_fields = []
    for field_name, value in {
        "goods_received": goods_received,
        "defect_type": defect_type,
        "defect_location": defect_location,
        "defect_edge": defect_edge,
        "has_tag_visible": has_tag_visible,
        "photo_background": photo_background,
        "wear_signs": wear_signs,
        "visual_defect_severity": visual_defect_severity,
        "visual_goods_recoverability": visual_goods_recoverability,
        "logistics_normal": logistics_normal,
    }.items():
        if value is None:
            uncertain_fields.append(field_name)
    if uncertain_fields:
        uncertainty_reasons.append(f"以下字段暂无法确定：{', '.join(uncertain_fields)}")

    uncertainty_note = "；".join(dict.fromkeys(uncertainty_reasons)) if uncertainty_reasons else None

    category_slugs = _collect_category_slugs(issue_result, vision_category_slug=visual_category_slug)
    if visual_category_slug and visual_category_slug in category_slugs:
        logger.info("%s 视觉识别品类 slug=%s", LOG_PREFIX, visual_category_slug)
    rule_match_plan = merge_llm_rule_plan(
        materials=materials,
        intent_tags=intent_tags,
        logistics_normal=logistics_normal,
        text_context=text_context,
        issue_summary=issue_summary,
        category_slugs=category_slugs,
        defect_type=defect_type,
    )
    if not rule_match_plan.target_doc_ids:
        logger.warning("%s rule_match_plan 无 target_doc_ids，规则匹配将跳过", LOG_PREFIX)

    attributes = _merge_rule_context_attributes(attributes, materials)
    primary_dispute_frame = resolve_primary_dispute_frame(
        materials=materials,
        intent_tags=intent_tags,
        defect_type=defect_type,
        logistics_normal=logistics_normal,
    )

    return FactOutput(
        issue_summary=issue_summary,
        intent_tags=list(dict.fromkeys(intent_tags)),
        visual_observations=list(dict.fromkeys(visual_observations)),
        visual_defect_severity=visual_defect_severity,
        visual_goods_recoverability=visual_goods_recoverability,
        attributes=attributes,
        evidence_items=evidence_items,
        goods_received=goods_received,
        defect_type=defect_type,
        defect_location=defect_location,
        defect_edge=defect_edge,
        has_tag_visible=has_tag_visible,
        photo_background=photo_background,
        wear_signs=wear_signs,
        logistics_normal=logistics_normal,
        missing_evidence=missing_evidence,
        red_flags=red_flags,
        credential_trust=credential_trust,
        credential_trust_note=credential_trust_note,
        primary_dispute_frame=primary_dispute_frame,
        evidence_quality=evidence_quality,
        decision_readiness=decision_readiness,
        decision_readiness_note=decision_readiness_note,
        confidence=confidence,
        uncertainty_note=uncertainty_note,
        rule_match_plan=rule_match_plan,
    )
