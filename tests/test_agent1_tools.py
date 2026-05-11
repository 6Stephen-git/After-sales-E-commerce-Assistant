"""
Agent1 工具层测试：视觉结果有效性校验。
"""

import os
import sys

# ---------- 与仓库根对齐的导入路径（单测可直接 pytest 运行） ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.tools.agent1_tools import _has_meaningful_vision_content


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
