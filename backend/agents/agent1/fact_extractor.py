"""
Agent 1：事实还原员。

职责：仅做客观事实归纳，不判责、不调策略；外部能力仅通过 Tools 层封装调用。
"""

from __future__ import annotations

import json
from typing import Any

from schemas import EVIDENCE_HIGH, EVIDENCE_LOW, EVIDENCE_MEDIUM, FactOutput

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
                parts.append(text.strip())
        elif isinstance(msg, str) and msg.strip():
            parts.append(msg.strip())
    return " ".join(parts)


# ---------- 事实推断辅助：收货判断、证据档位、置信度 ----------
def _infer_goods_received(text_context: str, logistics_signed: bool | None) -> bool | None:
    """
    推断买家是否已收到货（三态：是 / 否 / 未知）。

    优先级：文本中出现明确「未收到」类表述 → False；「已收到」类 → True；
    若文本无明确结论且提供了物流签收标志，则用 logistics_signed 兜底；
    两者皆无时返回 None。

    参数:
        text_context: 由 _collect_text 得到的合并文本。
        logistics_signed: 物流是否已签收；无物流数据时为 None。

    返回:
        True / False / None（无法从现有材料判断）。
    """
    if not text_context and logistics_signed is None:
        return None

    if any(k in text_context for k in ["没收到", "未收到", "没有收到", "未签收"]):
        return False
    if any(k in text_context for k in ["收到了", "已收到", "签收了", "拿到了"]):
        return True
    if logistics_signed is not None:
        return logistics_signed
    return None


def _derive_evidence_quality(
    has_text: bool,
    has_images: bool,
    has_logistics: bool,
    red_flag_count: int,
) -> str:
    """
    根据材料完整度与疑点数量给出证据质量枚举（high / medium / low）。

    规则简述：文本、图片、物流三项计分；材料过少则低；有两项为中等；
    若已出现疑点（red_flags）则证据质量不高于 medium。

    参数:
        has_text: 是否存在可用文本。
        has_images: 是否存在至少一张图片 URL。
        has_logistics: 是否成功具备物流查询结果（有订单号且查到对象即 True）。
        red_flag_count: 当前已收集的疑点条数。

    返回:
        EVIDENCE_HIGH、EVIDENCE_MEDIUM 或 EVIDENCE_LOW 之一。
    """
    score = int(has_text) + int(has_images) + int(has_logistics)
    if score <= 1:
        return EVIDENCE_LOW
    if score == 2:
        return EVIDENCE_MEDIUM
    if red_flag_count >= 1:
        return EVIDENCE_MEDIUM
    return EVIDENCE_HIGH


def _derive_confidence(evidence_quality: str, red_flag_count: int) -> float:
    """
    在证据质量基础上按疑点数量下调置信度，并裁剪到 [0, 1]。

    参数:
        evidence_quality: high / medium / low。
        red_flag_count: 疑点条数，每条扣固定步长。

    返回:
        0～1 之间的浮点数。
    """
    base = {
        EVIDENCE_LOW: 0.38,
        EVIDENCE_MEDIUM: 0.66,
        EVIDENCE_HIGH: 0.86,
    }[evidence_quality]
    return max(0.0, min(1.0, base - red_flag_count * 0.12))


# ---------- LLM辅助：结构化解析与守门合并（模糊不判、缺证必问） ----------
def _parse_json_text(raw_text: str) -> dict[str, Any] | None:
    """
    将 LLM 字符串结果解析为 JSON 对象。

    参数:
        raw_text: LLM 返回文本，可能含 markdown 代码块。

    返回:
        解析成功返回 dict，失败返回 None。
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


def _llm_extract_facts(text_context: str, image_result: dict[str, Any], logistics_normal: bool | None) -> dict[str, Any] | None:
    """
    调用 LLM 做二次事实审阅，重点识别“是否必须补证”。

    参数:
        text_context: 买家文本与聊天上下文。
        image_result: 多模态图片分析原始结果。
        logistics_normal: 物流是否正常。

    返回:
        约定字段字典，失败返回 None。
    """
    if not text_context and not image_result:
        return None

    system_prompt = (
        "你是电商纠纷事实守门员。只做客观判断，不做责任归属。"
        "若证据模糊、冲突或不足，必须要求补证，严禁臆断。"
    )
    user_prompt = (
        "请根据买家文本、图片分析结果和物流状态输出JSON，不要输出其他文本。\n"
        "字段要求：\n"
        "- defect_type/defect_location/defect_edge/photo_background/wear_signs: 字符串，可缺省。\n"
        "- has_tag_visible/goods_received: 布尔或null。\n"
        "- need_clarify: 布尔，是否必须补证。\n"
        "- confidence: 0到1浮点。\n"
        "- missing_evidence/red_flags/clarify_requests/uncertainty_reasons: 字符串数组。\n"
        "买家文本:\n"
        f"{text_context or '（空）'}\n"
        "图片分析结果:\n"
        f"{json.dumps(image_result, ensure_ascii=False)}\n"
        "物流是否正常:\n"
        f"{logistics_normal}\n"
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


# ---------- 主入口：物流 + 多模态 + 规则化疑点，输出 FactOutput ----------
def extract(materials: dict[str, Any]) -> FactOutput:
    """
    从纠纷材料中提取结构化事实，输出严格符合 schemas.FactOutput。

    流程概要：校验输入 → 汇总缺失证据 → 查物流 → 分析首张举证图（若存在）
    → 根据文本与物流打红点 → 计算证据质量与置信度 → 对仍为 None 的字段写 uncertainty_note。

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
    goods_received = _infer_goods_received(
        text_context=text_context,
        logistics_signed=(None if logistics_info is None else logistics_info.is_signed),
    )

    # 当前实现仅取首张图调用多模态；多图合并策略可由 Controller 传入预处理结果后再扩展。
    image_result: dict[str, Any] = {}
    if image_urls:
        image_result = analyze_image(image_url=image_urls[0])
        if image_result.get("error"):
            uncertainty_reasons.append(str(image_result["error"]))
            red_flags.append("图片分析失败，视觉证据暂不可靠")

    defect_type = image_result.get("defect_type")
    defect_location = image_result.get("defect_location")
    defect_edge = image_result.get("edge_condition")
    has_tag_visible = image_result.get("has_tag")
    photo_background = image_result.get("background")
    wear_signs = image_result.get("wear_signs")

    # 多模态返回“无法判断”时强制走不确定路径，避免错误事实带偏后续策略。
    if isinstance(defect_type, str) and defect_type.strip() in {"无法判断", "不确定", "未知"}:
        defect_type = None
        missing_evidence.append("图片不清晰，请补拍瑕疵部位近景和全景各一张")
        uncertainty_reasons.append("当前图片无法稳定识别瑕疵类型")

    if logistics_info and goods_received is False and logistics_info.is_signed:
        red_flags.append("买家称未收到货，但物流显示已签收")

    if logistics_info and logistics_info.is_abnormal:
        red_flags.append(f"物流异常：停滞 {logistics_info.stagnant_days} 天")

    if has_tag_visible is False and isinstance(wear_signs, str) and wear_signs:
        red_flags.append("图片吊牌不可见，且存在使用痕迹描述")

    # 通过 LLM 二次审阅事实，但只有“高置信且无需补证”时才允许覆盖字段。
    llm_result = _llm_extract_facts(
        text_context=text_context,
        image_result=image_result,
        logistics_normal=logistics_normal,
    )
    if isinstance(llm_result, dict):
        llm_confidence_raw = llm_result.get("confidence")
        try:
            llm_confidence = float(llm_confidence_raw)
        except (TypeError, ValueError):
            llm_confidence = 0.0
        parsed_need_clarify = _parse_bool(llm_result.get("need_clarify"))
        need_clarify = parsed_need_clarify is True
        can_override = llm_confidence >= 0.7 and not need_clarify

        _extend_unique(missing_evidence, llm_result.get("missing_evidence"))
        _extend_unique(red_flags, llm_result.get("red_flags"))
        _extend_unique(uncertainty_reasons, llm_result.get("uncertainty_reasons"))
        clarify_requests: list[str] = []
        _extend_unique(clarify_requests, llm_result.get("clarify_requests"))
        if clarify_requests:
            _extend_unique(missing_evidence, clarify_requests)

        if can_override:
            llm_goods_received = _parse_bool(llm_result.get("goods_received"))
            llm_has_tag_visible = _parse_bool(llm_result.get("has_tag_visible"))
            llm_defect_type = str(llm_result.get("defect_type", "")).strip() or None
            llm_defect_location = str(llm_result.get("defect_location", "")).strip() or None
            llm_defect_edge = str(llm_result.get("defect_edge", "")).strip() or None
            llm_photo_background = str(llm_result.get("photo_background", "")).strip() or None
            llm_wear_signs = str(llm_result.get("wear_signs", "")).strip() or None

            if goods_received is None and llm_goods_received is not None:
                goods_received = llm_goods_received
            if defect_type is None and llm_defect_type not in {None, "无法判断"}:
                defect_type = llm_defect_type
            if defect_location is None and llm_defect_location is not None:
                defect_location = llm_defect_location
            if defect_edge is None and llm_defect_edge not in {None, "无法判断"}:
                defect_edge = llm_defect_edge
            if has_tag_visible is None and llm_has_tag_visible is not None:
                has_tag_visible = llm_has_tag_visible
            if photo_background is None and llm_photo_background is not None:
                photo_background = llm_photo_background
            if wear_signs is None and llm_wear_signs is not None:
                wear_signs = llm_wear_signs
        else:
            uncertainty_reasons.append("证据存在不确定性，已进入补证优先流程")

    evidence_quality = _derive_evidence_quality(
        has_text=bool(text_context),
        has_images=bool(image_urls),
        has_logistics=bool(logistics_info),
        red_flag_count=len(red_flags),
    )
    confidence = _derive_confidence(
        evidence_quality=evidence_quality,
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

    return FactOutput(
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
    )
