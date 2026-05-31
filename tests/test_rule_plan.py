"""
Agent1 rule_match_plan 合并逻辑测试：特殊品类 C 通道锁定与上游 slug 消费。
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.agents.agent1.rule_plan import (
    _intent_suggests_category_dispute,
    merge_llm_rule_plan,
)
from backend.tools.rule_lexicon import get_category_doc_id, is_category_doc, resolve_doc_id_reference, validate_category_slug


PHONE_DOC_ID = "特殊品类争议处理_淘宝平台手机类商品争议处理规范_1155_11003755"
BASE_DOC_ID = "争议处理基本规则_淘宝平台争议处理规则_1154_99"


class TestCategoryLaneLock:
    def test_validate_phone_slug(self):
        """phone slug 应通过 lexicon 校验。"""
        assert validate_category_slug("phone") == "phone"

    def test_validate_fresh_slug(self):
        """fresh slug 应通过 lexicon 校验。"""
        assert validate_category_slug("fresh") == "fresh"

    def test_resolve_doc_name_to_canonical_id(self):
        """LLM 误填 doc_name 时应解析为完整 doc_id。"""
        resolved = resolve_doc_id_reference("淘宝平台手机类商品争议处理规范")
        assert resolved == PHONE_DOC_ID

    def test_sanitize_doc_name_in_merge(self):
        """上游 slug 应强制加入 canonical 品类 doc_id。"""
        plan = merge_llm_rule_plan(
            materials={},
            intent_tags=["质量问题"],
            logistics_normal=True,
            text_context="手机屏幕有划痕",
            category_slugs=["phone"],
        )
        assert PHONE_DOC_ID in plan.target_doc_ids
        assert "划痕" in plan.search_terms.case_terms

    def test_low_confidence_still_keeps_category_doc_when_c_lane_active(self):
        """已选品类 doc 时，低置信度不得剔除 C 通道。"""
        plan = merge_llm_rule_plan(
            materials={},
            intent_tags=["质量问题"],
            logistics_normal=True,
            text_context="手机屏幕划痕，没录开箱视频",
            issue_summary="买家主张手机划痕",
            category_slugs=["phone"],
        )
        assert PHONE_DOC_ID in plan.target_doc_ids
        assert "C" in plan.activated_lanes
        assert "举证" in plan.search_terms.must_terms
        assert "商品质量问题" in plan.search_terms.must_terms
        assert "划痕" in plan.search_terms.case_terms

    def test_upstream_slug_injects_phone_doc_without_api_slug(self):
        """无 API 标时，上游 slug 应强制加入品类规范。"""
        plan = merge_llm_rule_plan(
            materials={},
            intent_tags=["质量问题"],
            logistics_normal=True,
            text_context="这个手机收到就有划痕",
            issue_summary="手机划痕退款",
            category_slugs=["phone"],
        )
        assert PHONE_DOC_ID in plan.target_doc_ids
        assert plan.category_confidence >= 0.75
        assert any(sel.doc_id == PHONE_DOC_ID for sel in plan.section_selections)

    def test_api_slug_always_wins(self):
        """API 传入 product_category_slug 时必含对应 doc。"""
        plan = merge_llm_rule_plan(
            materials={"product_category_slug": "phone"},
            intent_tags=[],
            logistics_normal=True,
        )
        expected = get_category_doc_id("phone")
        assert expected
        assert expected in plan.target_doc_ids
        assert plan.category_confidence == 1.0


class TestLexiconSearchEnrichment:
    def test_base_doc_gets_rule_terms_from_lexicon(self):
        """A 通道基本规则也应从 lexicon rule_terms 补全检索词。"""
        plan = merge_llm_rule_plan(
            materials={},
            intent_tags=["质量问题"],
            logistics_normal=True,
            text_context="商品有划痕，要求退款",
            category_slugs=["phone"],
        )
        assert BASE_DOC_ID in plan.target_doc_ids
        assert "举证" in plan.search_terms.must_terms
        assert "划痕" in plan.search_terms.case_terms
        assert "商品质量问题" in plan.search_terms.must_terms


class TestCategorySlugInferenceGate:
    def test_quality_intent_triggers_category_dispute_gate(self):
        """质量类争点应通过品类争点门控。"""
        assert _intent_suggests_category_dispute(["质量问题"], "商品有划痕")

    def test_logistics_intent_skips_category_dispute_gate(self):
        """纯物流争点不应触发品类争点门控。"""
        assert not _intent_suggests_category_dispute(["物流异常"], "快递一直不到")

    def test_merge_should_skip_category_llm_when_vision_slug_present(self, monkeypatch):
        """上游已有视觉 slug 时不应再调品类推断 LLM。"""
        called = {"count": 0}

        def _should_not_infer(*_args, **_kwargs):
            called["count"] += 1
            return None, 0.0

        monkeypatch.setattr(
            "backend.agents.agent1.rule_plan.infer_category_slug_llm",
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
