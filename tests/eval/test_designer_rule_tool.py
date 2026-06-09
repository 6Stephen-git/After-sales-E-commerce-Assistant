"""Scenario Designer 规则查阅工具单测。"""

from __future__ import annotations

from backend.tools.rule_lexicon import resolve_service_tag_doc_id
from backend.tools.rule_text_reader import extract_rule_design_brief, read_service_rule_text
from eval.pipeline.designer_tools import execute_designer_tool


def test_resolve_bad_order_refund_tag() -> None:
    """坏单包退应解析到对应 doc_id。"""
    resolved = resolve_service_tag_doc_id("坏单包退")
    assert resolved is not None
    _canonical, doc_id = resolved
    assert "坏单包退" in doc_id


def test_read_service_rule_contains_scope_and_refund_tiers() -> None:
    """原文应含适用品类与退款比例，而非 lexicon 截断摘录。"""
    text = read_service_rule_text("坏单包退")
    assert "错误：" not in text
    assert "水产肉类/新鲜蔬果/熟食" in text
    assert "百分之十" in text or "10%" in text


def test_execute_designer_tool_read_service_rule() -> None:
    """工具分发应返回规则原文。"""
    result = execute_designer_tool(
        "read_service_rule",
        '{"service_tag": "坏单包退"}',
    )
    assert "水产肉类" in result


def test_design_brief_omits_refund_tiers() -> None:
    """设计摘要只含适用范围，不含退款比例。"""
    full = read_service_rule_text("坏单包退")
    brief = extract_rule_design_brief(full)
    assert "水产肉类" in brief or "新鲜蔬果" in brief
    assert "百分之十" not in brief
    assert "10%" not in brief


def test_design_brief_for_damage_compensation_tag() -> None:
    """破损包赔应解析出一级类目列表。"""
    full = read_service_rule_text("破损包赔")
    assert "错误：" not in full
    brief = extract_rule_design_brief(full)
    assert "住宅家具" in brief
    assert "家装主材" in brief
    assert "60%" not in brief


def test_parse_service_constraint_tags() -> None:
    """CLI 服务标字符串应正确拆分。"""
    from eval.pipeline.designer_tools import parse_service_constraint_tags

    assert parse_service_constraint_tags("坏单包退") == ["坏单包退"]
    assert parse_service_constraint_tags("坏单包退, 七天无理由") == ["坏单包退", "七天无理由"]


def test_read_unknown_service_tag_returns_error() -> None:
    """未知服务标应返回中文错误。"""
    result = read_service_rule_text("不存在的服务标_xyz")
    assert result.startswith("错误：")
