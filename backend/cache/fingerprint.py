"""
纠纷材料指纹：为 B/C 层缓存生成稳定 hash。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


# ---------- Agent1 指纹字段（事实还原相关） ----------
FP_AGENT1_FIELDS = (
    "chat_history",
    "image_urls",
    "evidence_images",
    "buyer_text",
    "complaint_text",
    "description",
    "product_category_slug",
    "platform_service_tags",
    "order_id",
)

# ---------- 整报告指纹额外字段（策略/话术相关） ----------
FP_REPORT_EXTRA_FIELDS = (
    "order_amount",
    "buyer_id",
    "merchant_id",
    "emotion_note",
)


def _to_list(value: Any) -> list[Any]:
    """
    将任意值安全转为列表。
    """
    if isinstance(value, list):
        return value
    return []


def _normalize_chat_history(chat_history: list[Any]) -> list[dict[str, str]]:
    """
    归一化聊天历史：保留 role 与 content，忽略无关字段波动。
    """
    normalized: list[dict[str, str]] = []
    for message in chat_history:
        if isinstance(message, dict):
            role = str(message.get("role", "") or "").strip().lower()
            content = str(message.get("content", "") or "").strip()
            normalized.append({"role": role, "content": content})
        elif isinstance(message, str) and message.strip():
            normalized.append({"role": "", "content": message.strip()})
    return normalized


def _normalize_string_list(items: list[Any]) -> list[str]:
    """
    归一化字符串列表：去空白、排序以保证稳定 hash。
    """
    normalized = [str(item).strip() for item in items if str(item).strip()]
    return sorted(normalized)


def _normalize_image_urls(materials: dict[str, Any]) -> list[str]:
    """
    合并 image_urls 与 evidence_images 并归一化。
    """
    urls = _to_list(materials.get("image_urls")) + _to_list(materials.get("evidence_images"))
    return _normalize_string_list(urls)


def _extract_field_values(materials: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """
    按固定字段顺序提取并归一化材料片段，供 hash 使用。
    """
    payload: dict[str, Any] = {}
    for field in fields:
        if field == "chat_history":
            payload[field] = _normalize_chat_history(_to_list(materials.get(field)))
        elif field in {"image_urls", "evidence_images"}:
            if field == "image_urls":
                payload["image_urls"] = _normalize_image_urls(materials)
        elif field == "platform_service_tags":
            payload[field] = _normalize_string_list(_to_list(materials.get(field)))
        elif field == "order_amount":
            try:
                payload[field] = round(max(0.0, float(materials.get(field, 0.0))), 2)
            except (TypeError, ValueError):
                payload[field] = 0.0
        else:
            payload[field] = str(materials.get(field, "") or "").strip()
    return payload


def _hash_payload(payload: dict[str, Any]) -> str:
    """
    对归一化 payload 做 SHA256，取前 16 位 hex。
    """
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def compute_fp_agent1(materials: dict[str, Any]) -> str:
    """
    计算 Agent1 层材料指纹。
    """
    agent1_fields = [field for field in FP_AGENT1_FIELDS if field != "evidence_images"]
    payload = _extract_field_values(materials, tuple(agent1_fields))
    return _hash_payload(payload)


def compute_fp_report(materials: dict[str, Any]) -> str:
    """
    计算整报告层材料指纹（含 fp_agent1 字段 + 策略相关字段）。
    """
    all_fields = tuple(
        field
        for field in FP_AGENT1_FIELDS + FP_REPORT_EXTRA_FIELDS
        if field != "evidence_images"
    )
    payload = _extract_field_values(materials, all_fields)
    return _hash_payload(payload)
