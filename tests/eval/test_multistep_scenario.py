"""三期多步情景解析与 fixture 防泄题。"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.pipeline.multistep_scenario import is_multistep_narrative, parse_multistep_narrative
from eval.pipeline.scenario_gen import load_narrative, source_key_from_path
from eval.pipeline.scenario_spec import validate_spec_dict
from eval.pipeline.spec_to_fixture import fixture_from_spec_dict, validate_fixture_no_answer_leak

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "eval" / "content" / "scenarios"
MS01 = SCENARIOS_DIR / "functional" / "phase3" / "multistep" / "MS-01_plant_live_evidence_arc.md"


def test_ms01_is_multistep() -> None:
    narrative = MS01.read_text(encoding="utf-8")
    assert is_multistep_narrative(narrative)


def test_ms01_parse_three_steps() -> None:
    narrative = MS01.read_text(encoding="utf-8")
    source_key = source_key_from_path(MS01)
    spec_dict = parse_multistep_narrative(narrative, source_key=source_key)
    spec = validate_spec_dict(spec_dict)
    assert spec.meta.case_id == "MS-01_PLANT_LIVE_EVIDENCE_ARC"
    assert len(spec.steps) == 3
    assert spec.steps[0]["materials"]["order_amount"] == 58.0
    assert spec.test_overrides.get("compensation_ratio_cap") is None  # 归一在 spec_to_fixture


def test_ms01_fixture_no_answer_leak() -> None:
    narrative = MS01.read_text(encoding="utf-8")
    spec_dict = parse_multistep_narrative(narrative, source_key=source_key_from_path(MS01))
    fixture = fixture_from_spec_dict(spec_dict)
    case = fixture["cases"][0]
    validate_fixture_no_answer_leak(case)
    assert "steps" in case
    assert "expectation" not in case
    for step in case["steps"]:
        assert "expectation" not in step
        chat_blob = " ".join(t["content"] for t in step["materials"]["chat_history"])
        assert "expected_report" not in chat_blob
        assert "17.4" not in chat_blob


def test_compensation_cap_from_other_notes() -> None:
    narrative = MS01.read_text(encoding="utf-8")
    spec_dict = parse_multistep_narrative(narrative, source_key=source_key_from_path(MS01))
    case = fixture_from_spec_dict(spec_dict)["cases"][0]
    assert case["test_overrides"]["compensation_ratio_cap"] == pytest.approx(0.3)
