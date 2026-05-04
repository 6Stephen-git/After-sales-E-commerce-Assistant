"""
Agent 1：事实还原员。

职责：仅做客观事实归纳，不判责、不调策略；外部能力仅通过 Tools 层封装调用。
"""

from __future__ import annotations

from typing import Any

from schemas import EVIDENCE_HIGH, EVIDENCE_LOW, EVIDENCE_MEDIUM, FactOutput

from backend.tools.agent1_tools import analyze_image
from backend.tools.platform_api import query_logistics

LOG_PREFIX = "[Agent1]"


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

    if logistics_info and goods_received is False and logistics_info.is_signed:
        red_flags.append("买家称未收到货，但物流显示已签收")

    if logistics_info and logistics_info.is_abnormal:
        red_flags.append(f"物流异常：停滞 {logistics_info.stagnant_days} 天")

    if has_tag_visible is False and isinstance(wear_signs, str) and wear_signs:
        red_flags.append("图片吊牌不可见，且存在使用痕迹描述")

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
