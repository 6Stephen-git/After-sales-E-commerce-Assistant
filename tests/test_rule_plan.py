"""
Agent1 rule_match_plan 合并逻辑核心测试：品类 C 通道锁定与 slug 门控。

原则：仅保留 slug 解析、上游注入、API 优先与 LLM 跳过门控；细节以 eval 为准。
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.agents.agent1.rule_plan import merge_llm_rule_plan
from backend.tools.rule_lexicon import (
    get_category_doc_id,
    is_category_doc,
    resolve_doc_id_reference,
    should_infer_category_slug,
    validate_category_slug,
)

PHONE_DOC_ID = "特殊品类争议处理_淘宝平台手机类商品争议处理规范_1155_11003755"
BASE_DOC_ID = "争议处理基本规则_淘宝平台争议处理规则_1154_99"


def test_lexicon_slug_and_doc_resolution():
    """slug 校验与 doc_name→doc_id 解析。"""
    assert validate_category_slug("phone") == "phone"
    assert resolve_doc_id_reference("淘宝平台手机类商品争议处理规范") == PHONE_DOC_ID


def test_merge_upstream_slug_locks_category_lane():
    """上游 phone slug 应锁定 C 通道、补全检索词，低置信度也不剔除品类 doc。"""
    plan = merge_llm_rule_plan(
        materials={},
        intent_tags=["质量问题"],
        logistics_normal=True,
        text_context="手机屏幕划痕，没录开箱视频",
        issue_summary="买家主张手机划痕",
        category_slugs=["phone"],
    )
    assert PHONE_DOC_ID in plan.target_doc_ids
    assert BASE_DOC_ID in plan.target_doc_ids
    assert "C" in plan.activated_lanes
    assert "举证" in plan.search_terms.must_terms
    assert "划痕" in plan.search_terms.case_terms
    assert plan.category_confidence >= 0.75


def test_api_slug_always_wins():
    """API 传入 product_category_slug 时必含对应 doc 且置信度为 1。"""
    plan = merge_llm_rule_plan(
        materials={"product_category_slug": "phone"},
        intent_tags=[],
        logistics_normal=True,
    )
    expected = get_category_doc_id("phone")
    assert expected in plan.target_doc_ids
    assert plan.category_confidence == 1.0


def test_category_slug_inference_gate():
    """有品类/聊天上下文才启动语义推断；上游已有视觉 slug 时不再调 LLM。"""
    assert should_infer_category_slug({"category": "宠物"}, "小狗死亡")
    assert not should_infer_category_slug({}, "")


def test_merge_skips_category_llm_when_vision_slug_present(monkeypatch):
    """上游已有视觉 slug 时不应再调品类推断 LLM。"""
    called = {"count": 0}

    def _should_not_infer(*_args, **_kwargs):
        called["count"] += 1
        return None, 0.0

    monkeypatch.setattr(
        "backend.tools.rule_lexicon.infer_category_slug_llm",
        _should_not_infer,
    )
    plan = merge_llm_rule_plan(
        materials={},
        intent_tags=["质量问题"],
        logistics_normal=True,
        text_context="屏幕划痕",
        issue_summary="屏幕划痕",
        category_slugs=["phone"],
    )
    assert called["count"] == 0
    assert any(is_category_doc(doc_id) for doc_id in plan.target_doc_ids)
