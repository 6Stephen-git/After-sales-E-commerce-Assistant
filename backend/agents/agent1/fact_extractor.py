"""
Agent 1：事实还原员。

职责：仅做客观事实归纳，不判责、不调策略；外部能力仅通过 Tools 层封装调用。
"""

from __future__ import annotations

import json
from typing import Any

from schemas import EVIDENCE_HIGH, EVIDENCE_LOW, EVIDENCE_MEDIUM, FactOutput, RuleMatchPlan

from backend.agents.agent1.rule_plan import build_rule_navigation_prompt_block, merge_llm_rule_plan

from backend.tools.agent1_tools import analyze_image
from backend.tools.llm_client import chat_completion
from backend.tools.platform_api import query_logistics

LOG_PREFIX = "[Agent1]"


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
    调用 LLM 提取核心诉求、标签、收货判断、补证建议与 rule_match_plan。
    """
    if not text_context:
        return None
    intent_hint: list[str] = []
    for kw, tag in (
        ("物流", "物流异常"),
        ("退款", "退款诉求"),
        ("质量", "质量问题"),
        ("瑕疵", "质量问题"),
        ("破损", "质量问题"),
    ):
        if kw in text_context and tag not in intent_hint:
            intent_hint.append(tag)
    nav_block = build_rule_navigation_prompt_block(
        materials=materials,
        intent_tags=intent_hint,
        text_context=text_context,
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
        "- rule_match_plan: 对象，含 activated_lanes、target_doc_ids、section_selections、search_terms、"
        "category_confidence、service_confidence（规则导航，见下方候选表）\n"
        f"{nav_block}\n"
        "聊天记录：\n"
        f"{text_context}\n"
        f"物流签收状态：{logistics_signed}\n"
        f"product_category_slug={materials.get('product_category_slug', '')}\n"
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


# ---------- 语义兜底：模型不可用时用最小关键词保证收货判断可用 ----------
def _fallback_infer_goods_received(text_context: str) -> bool | None:
    """
    在 LLM 不可用时，从文本中做最小化收货判断。
    """
    if not text_context:
        return None
    if any(keyword in text_context for keyword in ["没收到", "未收到", "没有收到", "未签收"]):
        return False
    if any(keyword in text_context for keyword in ["收到了", "已收到", "签收了", "拿到了"]):
        return True
    return None


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
    issue_result = _llm_extract_issue(
        text_context=text_context,
        logistics_signed=(None if logistics_info is None else logistics_info.is_signed),
        materials=materials,
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

    for image_url in image_urls[:3]:
        image_result = analyze_image(image_url=image_url, guidance=issue_summary)
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

    if logistics_info and goods_received is False and logistics_info.is_signed:
        red_flags.append("买家称未收到货，但物流显示已签收")
    if logistics_info and logistics_info.is_abnormal:
        red_flags.append(f"物流异常：停滞 {logistics_info.stagnant_days} 天")
    if not visual_observations and image_urls:
        missing_evidence.append("缺少可用视觉分析结论，建议补充更清晰图片或视频")

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

    uncertain_fields = []
    for field_name, value in {
        "goods_received": goods_received,
        "defect_type": defect_type,
        "defect_location": defect_location,
        "defect_edge": defect_edge,
        "has_tag_visible": has_tag_visible,
        "photo_background": photo_background,
        "wear_signs": wear_signs,
        "logistics_normal": logistics_normal,
    }.items():
        if value is None:
            uncertain_fields.append(field_name)
    if uncertain_fields:
        uncertainty_reasons.append(f"以下字段暂无法确定：{', '.join(uncertain_fields)}")

    uncertainty_note = "；".join(dict.fromkeys(uncertainty_reasons)) if uncertainty_reasons else None

    raw_plan = issue_result.get("rule_match_plan") if isinstance(issue_result, dict) else None
    rule_match_plan = merge_llm_rule_plan(
        raw_plan=raw_plan if isinstance(raw_plan, dict) else None,
        materials=materials,
        intent_tags=intent_tags,
        logistics_normal=logistics_normal,
        text_context=text_context,
        issue_summary=issue_summary,
    )

    return FactOutput(
        issue_summary=issue_summary,
        intent_tags=list(dict.fromkeys(intent_tags)),
        visual_observations=list(dict.fromkeys(visual_observations)),
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
        evidence_quality=evidence_quality,
        confidence=confidence,
        uncertainty_note=uncertainty_note,
        rule_match_plan=rule_match_plan,
    )
