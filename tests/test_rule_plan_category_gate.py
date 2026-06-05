"""
品类 slug 门控：无效 product_category_slug 不应阻断品类 LLM 兜底。
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.agents.agent1.rule_plan import merge_llm_rule_plan
from backend.tools.rule_lexicon import is_category_doc


def test_invalid_fruit_slug_allows_category_llm_infer(monkeypatch) -> None:
    """无效 slug fruit 时仍应调用品类语义推断，而非静默跳过 C 通道。"""
    calls: list[str] = []

    def _fake_infer(_text: str, _materials: dict) -> tuple[str | None, float]:
        calls.append("infer")
        return "fresh", 0.9

    monkeypatch.setattr(
        "backend.tools.rule_lexicon.infer_category_slug_llm",
        _fake_infer,
    )
    plan = merge_llm_rule_plan(
        materials={"product_category_slug": "fruit"},
        intent_tags=["质量问题"],
        logistics_normal=True,
        text_context="葡萄腐烂要求退款",
        issue_summary="水果腐烂",
    )
    assert calls == ["infer"]
    assert any(is_category_doc(doc_id) for doc_id in plan.target_doc_ids)
