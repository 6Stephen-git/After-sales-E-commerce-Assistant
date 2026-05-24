"""
Agent1 rule_match_plan 合并逻辑测试：特殊品类 C 通道锁定与 LLM 品类推断。
"""

import json
import os
import sys
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.agents.agent1.rule_plan import build_rule_navigation_prompt_block, merge_llm_rule_plan
from backend.tools.rule_lexicon import get_category_doc_id, infer_category_slug_from_text


PHONE_DOC_ID = "特殊品类争议处理_淘宝平台手机类商品争议处理规范_1155_11003755"
BASE_DOC_ID = "争议处理基本规则_淘宝平台争议处理规则_1154_99"


def _mock_category_llm(messages, model_env_key, temperature=0.0, **kwargs):
    """
    按买家描述段落返回品类 slug JSON，避免 slug 列表中的 doc 名称干扰。
    """
    user_text = messages[-1]["content"] if messages else ""
    buyer_part = user_text.split("买家描述：")[-1] if "买家描述：" in user_text else user_text
    if "香蕉" in buyer_part:
        return json.dumps({"category_slug": "fresh", "confidence": 0.9}, ensure_ascii=False)
    if "手机" in buyer_part or ("划痕" in buyer_part and "屏幕" in buyer_part):
        return json.dumps({"category_slug": "phone", "confidence": 0.92}, ensure_ascii=False)
    if "衣服" in buyer_part or "破洞" in buyer_part or "袖子" in buyer_part:
        return json.dumps({"category_slug": "apparel", "confidence": 0.9}, ensure_ascii=False)
    return json.dumps({"category_slug": None, "confidence": 0.0}, ensure_ascii=False)


class TestCategoryLaneLock:
    @patch("backend.tools.llm_client.chat_completion", side_effect=_mock_category_llm)
    def test_infer_phone_slug_from_chat(self, _mock_llm):
        """聊天含手机关键词时 LLM 应推断 phone slug。"""
        slug = infer_category_slug_from_text("买家说手机屏幕有划痕，要求退款")
        assert slug == "phone"

    @patch("backend.tools.llm_client.chat_completion", side_effect=_mock_category_llm)
    def test_infer_fresh_slug_from_banana_spoilage(self, _mock_llm):
        """香蕉腐烂应推断 fresh 并触发生鲜规范。"""
        slug = infer_category_slug_from_text("香蕉拆开来都坏了，你们怎么搞这样的东西")
        assert slug == "fresh"

    @patch("backend.tools.llm_client.chat_completion", side_effect=_mock_category_llm)
    def test_low_confidence_still_keeps_category_doc_when_c_lane_active(self, _mock_llm):
        """LLM 已选品类 doc 时，低置信度不得剔除 C 通道。"""
        plan = merge_llm_rule_plan(
            raw_plan={
                "activated_lanes": ["A", "C"],
                "target_doc_ids": [PHONE_DOC_ID],
                "section_selections": [],
                "search_terms": {"must_terms": ["开箱视频"], "case_terms": []},
                "category_confidence": 0.4,
                "service_confidence": 0.0,
            },
            materials={},
            intent_tags=["质量问题"],
            logistics_normal=True,
            text_context="手机屏幕划痕，没录开箱视频",
            issue_summary="买家主张手机划痕",
        )
        assert PHONE_DOC_ID in plan.target_doc_ids
        assert "C" in plan.activated_lanes
        assert "举证" in plan.search_terms.must_terms
        assert "商品质量问题" in plan.search_terms.must_terms
        assert "开箱视频" in plan.search_terms.case_terms

    @patch("backend.tools.llm_client.chat_completion", side_effect=_mock_category_llm)
    def test_text_inference_injects_phone_doc_without_api_slug(self, _mock_llm):
        """无 API 标时，LLM 推断手机品类应强制加入品类规范。"""
        plan = merge_llm_rule_plan(
            raw_plan={
                "activated_lanes": ["A"],
                "target_doc_ids": [],
                "section_selections": [],
                "search_terms": {"must_terms": [], "case_terms": []},
                "category_confidence": 0.2,
            },
            materials={},
            intent_tags=["质量问题"],
            logistics_normal=True,
            text_context="这个手机收到就有划痕",
            issue_summary="手机划痕退款",
        )
        assert PHONE_DOC_ID in plan.target_doc_ids
        assert plan.category_confidence >= 0.75
        assert any(sel.doc_id == PHONE_DOC_ID for sel in plan.section_selections)

    def test_api_slug_always_wins(self):
        """API 传入 product_category_slug 时必含对应 doc。"""
        plan = merge_llm_rule_plan(
            raw_plan={"target_doc_ids": [], "category_confidence": 0.0},
            materials={"product_category_slug": "phone"},
            intent_tags=[],
            logistics_normal=True,
        )
        expected = get_category_doc_id("phone")
        assert expected
        assert expected in plan.target_doc_ids
        assert plan.category_confidence == 1.0


class TestLexiconSearchEnrichment:
    @patch("backend.tools.llm_client.chat_completion", side_effect=_mock_category_llm)
    def test_base_doc_gets_rule_terms_from_lexicon(self, _mock_llm):
        """A 通道基本规则也应从 lexicon rule_terms 补全检索词。"""
        plan = merge_llm_rule_plan(
            raw_plan={
                "target_doc_ids": [BASE_DOC_ID],
                "section_selections": [],
                "search_terms": {"must_terms": [], "case_terms": []},
            },
            materials={},
            intent_tags=["质量问题"],
            logistics_normal=True,
            text_context="商品有划痕，要求退款",
        )
        assert BASE_DOC_ID in plan.target_doc_ids
        assert "举证" in plan.search_terms.must_terms
        assert "划痕" in plan.search_terms.case_terms
        assert "商品质量问题" in plan.search_terms.must_terms

    def test_prompt_includes_rule_terms_and_alias_hint(self):
        """prompt 候选节应展示 rule_terms 与摘录。"""
        block = build_rule_navigation_prompt_block(
            materials={"product_category_slug": "phone"},
            intent_tags=["质量问题"],
            text_context="手机屏幕划痕",
        )
        assert "规则词:" in block
        assert "摘录:" in block
        assert PHONE_DOC_ID in block
