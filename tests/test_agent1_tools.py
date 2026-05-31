"""
Agent1 工具层测试：视觉结果有效性校验。
"""

import os
import sys

# ---------- 与仓库根对齐的导入路径（单测可直接 pytest 运行） ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.tools.agent1_tools import (
    _build_dashscope_payload,
    _build_vision_analysis_prompt,
    _has_meaningful_vision_content,
    _normalize_vision_dict,
)


# ---------- 新模式：仅有 visual_description 也应视为有效 ----------
def test_has_meaningful_vision_content_should_accept_visual_description():
    payload = {"visual_description": "左袖口有明显破损"}
    assert _has_meaningful_vision_content(payload) is True


# ---------- 新模式：仅有 findings/attributes 也应视为有效 ----------
def test_has_meaningful_vision_content_should_accept_findings_or_attributes():
    payload_findings = {"findings": ["破损边缘毛糙", "位置在袖口"]}
    payload_attributes = {"attributes": {"issue_location": "左袖口"}}
    assert _has_meaningful_vision_content(payload_findings) is True
    assert _has_meaningful_vision_content(payload_attributes) is True


# ---------- 非新结构字段：不应通过 ----------
def test_has_meaningful_vision_content_should_reject_legacy_only_fields():
    payload = {"defect_type": "破洞"}
    assert _has_meaningful_vision_content(payload) is False


# ---------- 空结果：没有任何可用字段应判为无效 ----------
def test_has_meaningful_vision_content_should_reject_empty_payload():
    assert _has_meaningful_vision_content({}) is False


# ---------- 诉求锚定 prompt：应嵌入买家诉求并含可选 category_slug 枚举 ----------
def test_build_vision_analysis_prompt_should_anchor_on_buyer_claim():
    prompt = _build_vision_analysis_prompt("收到的商品有破损，要求退款")
    assert "【买家诉求】" in prompt
    assert "收到的商品有破损" in prompt
    assert "以诉求为唯一锚点" in prompt
    assert "category_slug" in prompt
    assert "phone" in prompt or "food" in prompt


# ---------- 百炼 payload：guidance 应进入 multimodal 文本块 ----------
def test_build_dashscope_payload_should_embed_buyer_claim():
    payload = _build_dashscope_payload(
        image_ref="https://example.com/a.jpg",
        model="qwen3-vl-flash",
        guidance="包装压扁了，里面的东西碎了",
    )
    text_block = payload["input"]["messages"][0]["content"][1]["text"]
    assert "包装压扁" in text_block
    assert payload["model"] == "qwen3-vl-flash"


# ---------- 视觉损失暴露枚举：规范化为合法小写值 ----------
def test_normalize_vision_dict_should_coerce_loss_exposure_enums():
    normalized = _normalize_vision_dict(
        {
            "visual_description": "底座开裂",
            "visual_defect_severity": "Severe",
            "visual_goods_recoverability": "UNRECOVERABLE",
        }
    )
    assert normalized["visual_defect_severity"] == "severe"
    assert normalized["visual_goods_recoverability"] == "unrecoverable"


def test_normalize_vision_dict_should_coerce_valid_category_slug():
    normalized = _normalize_vision_dict(
        {
            "visual_description": "熄屏手机可见划痕",
            "category_slug": "phone",
        }
    )
    assert normalized.get("category_slug") == "phone"
