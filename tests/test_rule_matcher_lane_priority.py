"""规则匹配通道优先级测试。"""

from backend.tools import rule_matcher
from schemas import MatchedRule, RULE_RELEVANCE_MUST, RULE_RELEVANCE_SHOULD, RuleSearchTerms


def _article(no: int) -> dict:
    """构造测试条文。"""
    return {
        "article_no": f"第{no}条",
        "article_title": f"测试条文{no}",
        "content": "举证 退款 物流 服务",
        "chapter": "",
    }


def test_prepare_llm_candidates_keeps_all_category_and_service_articles(monkeypatch) -> None:
    """C/E 贴案通道不应被普通字面 cap 截断。"""
    lane_map = {"category_doc": "C", "service_doc": "E", "base_doc": "A"}

    def _fake_doc(doc_id: str) -> dict:
        return {
            "lane": lane_map.get(doc_id, ""),
            "doc_name": doc_id,
            "default_section_keys": [],
        }

    monkeypatch.setattr(rule_matcher, "get_doc_by_id", _fake_doc)
    docs = {
        "category_doc": {"doc_id": "category_doc", "articles": [_article(i) for i in range(12)]},
        "service_doc": {"doc_id": "service_doc", "articles": [_article(i) for i in range(12, 24)]},
        "base_doc": {"doc_id": "base_doc", "articles": [_article(i) for i in range(24, 40)]},
    }

    candidates = rule_matcher.prepare_llm_candidates(
        documents=docs,
        section_map={},
        terms=RuleSearchTerms(must_terms=["举证"], should_terms=[], case_terms=[]),
    )

    lane_counts = {}
    for item in candidates:
        lane_counts[item["lane"]] = lane_counts.get(item["lane"], 0) + 1

    assert lane_counts["C"] == 12
    assert lane_counts["E"] == 12
    assert lane_counts["A"] == rule_matcher.NON_CATEGORY_LLM_CAP_PER_DOC


def test_display_rules_orders_category_and_service_before_base(monkeypatch) -> None:
    """展示规则应优先 C/E，通用 A 放后面。"""
    lane_map = {"base": "A", "service": "E", "category": "C"}
    monkeypatch.setattr(rule_matcher, "get_doc_by_id", lambda doc_id: {"lane": lane_map.get(doc_id, "")})

    pool = [
        MatchedRule(
            rule_id="base::1",
            rule_summary="通用举证",
            condition_result="",
            relevance=RULE_RELEVANCE_MUST,
            doc_id="base",
        ),
        MatchedRule(
            rule_id="service::1",
            rule_summary="服务保障",
            condition_result="",
            relevance=RULE_RELEVANCE_SHOULD,
            doc_id="service",
        ),
        MatchedRule(
            rule_id="category::1",
            rule_summary="品类规范",
            condition_result="",
            relevance=RULE_RELEVANCE_SHOULD,
            doc_id="category",
        ),
    ]

    display = rule_matcher._resolve_display_rules(
        pool,
        facts=None,
        llm_display_ids=["base::1", "service::1", "category::1"],
    )

    assert [rule.rule_id for rule in display] == ["service::1", "category::1", "base::1"]
