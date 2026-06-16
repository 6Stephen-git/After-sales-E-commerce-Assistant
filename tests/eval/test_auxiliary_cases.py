"""辅助判例库加载单测。"""

from __future__ import annotations

from eval.pipeline.auxiliary_cases import (
    apply_auxiliary_cases,
    load_auxiliary_case,
    resolve_similar_cases_from_narrative,
)


def test_load_case_mal_01() -> None:
    """JSON 直读 SimilarCase 字段。"""
    item = load_auxiliary_case("CASE-MAL-01")
    assert item["case_id"] == "CASE-MAL-01"
    assert item["similarity"] == 0.88
    assert item["merchant_action"]
    assert item["outcome"]
    assert item["lesson"]


def test_reference_none_returns_empty_list() -> None:
    """参考为「无」→ 空列表。"""
    narrative = "## 参考\n\n无\n\n## 期望与禁忌\n"
    assert resolve_similar_cases_from_narrative(narrative) == []


def test_resolve_case_from_reference() -> None:
    """参考段 CASE-* → 加载对应 JSON。"""
    narrative = "## 参考\n\n- CASE-VAL-01\n"
    cases = resolve_similar_cases_from_narrative(narrative)
    assert cases is not None
    assert cases[0]["case_id"] == "CASE-VAL-01"


def test_apply_overrides_llm_hallucination() -> None:
    """有 CASE 引用时覆盖 LLM 自拟判例。"""
    narrative = "## 参考\n\n- CASE-MF-01\n"
    spec = {"similar_cases": [{"case_id": "HALLUCINATED"}]}
    updated = apply_auxiliary_cases(spec, narrative)
    assert updated["similar_cases"][0]["case_id"] == "CASE-MF-01"
