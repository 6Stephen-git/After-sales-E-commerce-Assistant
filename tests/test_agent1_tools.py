"""
Agent1 工具层核心测试：视觉结果有效性、prompt 与规范化。

原则：合并同类边界断言；细节以 eval 与视觉链路为准。
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.tools.agent1_tools import (
    _build_dashscope_payload,
    _build_vision_analysis_prompt,
    _has_meaningful_vision_content,
    _normalize_vision_dict,
)


def test_has_meaningful_vision_content_validation():
    """新结构字段有效；仅 legacy 字段或空 payload 无效。"""
    assert _has_meaningful_vision_content({"visual_description": "左袖口有明显破损"}) is True
    assert _has_meaningful_vision_content({"findings": ["破损边缘毛糙"]}) is True
    assert _has_meaningful_vision_content({"attributes": {"issue_location": "左袖口"}}) is True
    assert _has_meaningful_vision_content({"defect_type": "破洞"}) is False
    assert _has_meaningful_vision_content({}) is False


def test_vision_prompt_and_payload_contract():
    """诉求锚定 prompt 与百炼 payload 应嵌入买家诉求。"""
    prompt = _build_vision_analysis_prompt("收到的商品有破损，要求退款")
    assert "【买家诉求】" in prompt
    assert "收到的商品有破损" in prompt
    assert "category_slug" in prompt

    payload = _build_dashscope_payload(
        image_ref="https://example.com/a.jpg",
        model="qwen3-vl-flash",
        guidance="包装压扁了，里面的东西碎了",
    )
    text_block = payload["input"]["messages"][0]["content"][1]["text"]
    assert "包装压扁" in text_block
    assert payload["model"] == "qwen3-vl-flash"


def test_normalize_vision_dict_enums_and_slug():
    """损失暴露枚举与 category_slug 应规范化为合法小写值。"""
    normalized = _normalize_vision_dict(
        {
            "visual_description": "熄屏手机可见划痕",
            "visual_defect_severity": "Severe",
            "visual_goods_recoverability": "UNRECOVERABLE",
            "category_slug": "phone",
        }
    )
    assert normalized["visual_defect_severity"] == "severe"
    assert normalized["visual_goods_recoverability"] == "unrecoverable"
    assert normalized.get("category_slug") == "phone"
