"""eval/output 路径布局测试。"""

from __future__ import annotations

from eval.pipeline.output_layout import (
    case_id_phase,
    eval_run_dir,
    manual_reports_bucket,
    run_id_phase,
    scenario_output_dir,
    slug_phase,
)


def test_slug_phase_mapping():
    assert slug_phase("ma-01_review_blackmail") == "phase1"
    assert slug_phase("mix-06_high_order_emotional_negotiate") == "phase2"
    assert slug_phase("ms-04_jade_auth_evidence_arc") == "phase3"


def test_case_id_phase_and_buckets():
    assert case_id_phase("MA-01_REVIEW_BLACKMAIL") == "phase1"
    assert case_id_phase("MS-01_PLANT__step01_x") == "phase3"
    assert case_id_phase("PHASE3_MS_RUN_REPORT") == "_meta"
    assert manual_reports_bucket("MIX-04_MIX-06_RUN_REPORT").name == "_meta"


def test_eval_run_dir_phase():
    assert run_id_phase("phase3-multistep-r1") == "phase3"
    assert run_id_phase("phase2-mixed-r5") == "phase2"
    assert eval_run_dir("phase3-ms-batch2-judge").parts[-2:] == ("phase3", "phase3-ms-batch2-judge")
