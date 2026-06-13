"""多步快照 fixture 结构契约测试。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "eval" / "content" / "scenarios" / "pilot" / "fixtures"


def _load_fixture(name: str) -> dict:
    path = FIXTURE_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_ng20_multistep_fixture_has_three_steps() -> None:
    """NG-20 应含三步且 Step2 起 missing_evidence 为空。"""
    payload = _load_fixture("ng-20_evidence_arc_partial.fixture.json")
    case = payload["cases"][0]
    steps = case["steps"]
    assert len(steps) == 3
    assert case["meta"]["case_id"] == "NG-20_EVIDENCE_ARC_PARTIAL"
    assert steps[0]["facts_override"]["missing_evidence"]
    assert steps[1]["facts_override"]["missing_evidence"] == []
    assert steps[2]["facts_override"]["decision_readiness"] == "high"


def test_mf20_multistep_fixture_has_three_steps() -> None:
    """MF-20 三步均应为证据闭环态。"""
    payload = _load_fixture("mf-20_evidence_arc_remedy.fixture.json")
    case = payload["cases"][0]
    steps = case["steps"]
    assert len(steps) == 3
    assert case["meta"]["case_id"] == "MF-20_EVIDENCE_ARC_REMEDY"
    for step in steps:
        assert step["facts_override"]["missing_evidence"] == []
        assert step["facts_override"]["decision_readiness"] == "high"
