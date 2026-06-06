"""情景源文件 → 稳定 case_id 与输出目录键。"""

from pathlib import Path

from eval.pipeline.scenario_gen import (
    apply_source_identity,
    case_id_from_source_key,
    source_key_from_path,
    write_outputs,
)


def test_case_id_from_source_key() -> None:
    assert case_id_from_source_key("case1") == "CASE-CASE1"
    assert case_id_from_source_key("case2") == "CASE-CASE2"


def test_apply_source_identity_overrides_llm_case_id() -> None:
    spec = {
        "meta": {"case_id": "SCENARIO-001", "title": "x"},
        "human_review": {"scenario_restated": "t", "fixture_focus": "f", "checks_before_run": ["c"]},
        "expectation": {},
        "taxonomy": {"evidence_level": "medium"},
        "materials": {"order_amount": 1},
        "buyer_profile": {},
        "evidence_facts": {"issue_summary": "s"},
    }
    updated = apply_source_identity(spec, input_path=Path("eval/content/scenarios/case/case1.md"))
    assert updated["meta"]["case_id"] == "CASE-CASE1"
    assert updated["meta"]["source_key"] == "case1"


def test_write_outputs_uses_source_key_for_directory(tmp_path) -> None:
    spec_dict = apply_source_identity(
        {
            "meta": {"case_id": "SCENARIO-999", "title": "t", "source_key": "case1"},
            "human_review": {"scenario_restated": "t", "fixture_focus": "f", "checks_before_run": ["c"]},
            "expectation": {},
            "taxonomy": {"evidence_level": "medium"},
            "materials": {"order_amount": 1},
            "buyer_profile": {},
            "evidence_facts": {"issue_summary": "s"},
        },
        source_key="case1",
    )
    paths = write_outputs(spec_dict, out_dir=tmp_path / "case1", source_key="case1")
    assert paths["spec"].parent.name == "case1"
    fixture = paths["fixture"].read_text(encoding="utf-8")
    assert "CASE-CASE1" in fixture
