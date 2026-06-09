"""批量评测树与 run_batch_eval 链路测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.pipeline.eval_tree import (
    EVAL_TREE_PATH,
    diversity_warnings,
    filter_leaves,
    get_tree_expectations,
    list_lexicon_service_tags,
    load_eval_tree,
    validate_eval_tree,
    validate_service_tag_hints,
)
from eval.pipeline.paths import SCENARIOS_DIR
from datetime import datetime, timezone

from eval.pipeline.judge_models import JudgeRecord, JudgeResult, JudgeScores
from eval.pipeline.run_batch_eval import run_batch_eval, write_batch_summary


def test_load_eval_tree_has_twenty_leaves_with_axis_ratio():
    """首批评测树应为 20 叶，NG/MF/MA = 10/5/5。"""
    leaves = load_eval_tree()
    validate_eval_tree(leaves, tree_path=EVAL_TREE_PATH)

    counts: dict[str, int] = {}
    for leaf in leaves:
        counts[leaf.axis_id] = counts.get(leaf.axis_id, 0) + 1

    assert counts == {"negotiation": 10, "merchant_fault": 5, "malicious": 5}
    assert len({str(leaf.path) for leaf in leaves}) == 20


def test_load_eval_tree_batch2_has_eight_seven_five_ratio():
    """第二批评测树应为 20 叶，NG/MF/MA = 8/7/5，路径与首批不重叠。"""
    batch2_path = SCENARIOS_DIR / "eval_tree_batch2.yaml"
    leaves = load_eval_tree(batch2_path)
    validate_eval_tree(leaves, tree_path=batch2_path)
    expected_total, axis_counts = get_tree_expectations(batch2_path)
    assert expected_total == 20
    assert axis_counts == {
        "negotiation": 8,
        "merchant_fault": 7,
        "malicious": 5,
    }
    batch1_paths = {str(leaf.path) for leaf in load_eval_tree()}
    batch2_paths = {str(leaf.path) for leaf in leaves}
    assert batch1_paths.isdisjoint(batch2_paths)


def test_filter_leaves_only_matches_case_id_fragment():
    """--only 应能按 case_id 片段过滤。"""
    leaves = load_eval_tree()
    filtered = filter_leaves(leaves, only="ng-02")
    assert len(filtered) == 1
    assert filtered[0].case_id == "NG-02_evidence_compensation"


def test_service_tag_hints_resolve_in_lexicon():
    """评测树指定的服务标应能解析到规则库。"""
    leaves = load_eval_tree()
    warnings = validate_service_tag_hints(leaves)
    assert warnings == [], f"未解析服务标：{warnings}"


def test_lexicon_has_many_service_tags():
    """规则库服务标应明显多于坏单/破损等少数几种。"""
    tags = list_lexicon_service_tags()
    assert len(tags) >= 20
    assert "坏单包退" in tags
    assert "果径无忧" in tags
    assert "试饮可退" in tags


def test_run_batch_eval_mock_pipeline(tmp_path: Path):
    """mock 下单叶批量跑批应写出 BATCH_SUMMARY。"""
    tree_path = tmp_path / "tree.yaml"
    md_path = tmp_path / "pilot" / "negotiation" / "NG-99_mock_case.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("# mock\n\n## 背景\n", encoding="utf-8")

    tree_path.write_text(
        """
version: 1
axes:
  - id: negotiation
    label: 妥善协商
    prefix: NG
    dir: pilot/negotiation
    leaves:
      - case_id: NG-99_mock_case
        status: existing
        path: {md}
        leaf: mock
        target_ability: mock
        factors: a, b
        category_hint: 水果
        service_tag_hint: 坏单包退
""".format(md=str(md_path).replace("\\", "/")),
        encoding="utf-8",
    )

    scenario_dir = tmp_path / "scenarios" / "ng-99_mock_case"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "spec.json").write_text(
        json.dumps({"meta": {"case_id": "NG-99_MOCK_CASE"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_json = tmp_path / "reports" / "NG-99_MOCK_CASE.json"
    report_json.parent.mkdir(parents=True)
    report_json.write_text("{}", encoding="utf-8")

    judge_record = JudgeRecord(
        run_id="test-run",
        case_id="NG-99_MOCK_CASE",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        spec_path=str(scenario_dir / "spec.json"),
        report_json_path=str(report_json),
        judge_model_env_key="JUDGE_LLM_MODEL",
        result=JudgeResult.model_validate(
            {
                "case_id": "NG-99_MOCK_CASE",
                "pass": True,
                "overall_score": 90,
                "scores": {"expectation_alignment": 5},
                "judge_summary": "ok",
            }
        ),
    )

    with (
        patch("eval.pipeline.run_batch_eval.validate_eval_tree"),
        patch("eval.pipeline.run_batch_eval.SCENARIO_OUTPUT_DIR", tmp_path / "scenarios"),
        patch("eval.pipeline.run_batch_eval.MANUAL_REPORTS_DIR", tmp_path / "reports"),
        patch("eval.pipeline.run_batch_eval.EVAL_RUNS_DIR", tmp_path / "eval_runs"),
        patch("eval.pipeline.run_batch_eval.generate_and_run_leaf") as mock_gen,
        patch("eval.pipeline.run_batch_eval.judge_leaf", return_value=judge_record),
        patch("eval.pipeline.run_batch_eval.write_summary"),
    ):
        mock_gen.return_value = (scenario_dir, report_json)
        run_dir = run_batch_eval(tree_path=tree_path, run_id="test-run")

    batch_summary = run_dir / "BATCH_SUMMARY.md"
    assert batch_summary.is_file()
    text = batch_summary.read_text(encoding="utf-8")
    assert "NG 妥善协商" in text
    assert "NG-99_MOCK_CASE" in text or "完成 Judge 数：1" in text


def test_write_batch_summary_includes_axis_and_errors(tmp_path: Path):
    """BATCH_SUMMARY 应包含分轴统计与错误列表。"""
    leaves = load_eval_tree()
    subset = [leaf for leaf in leaves if leaf.case_id == "NG-02_evidence_compensation"]
    record = JudgeRecord(
        run_id="r1",
        case_id=subset[0].report_case_id,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        spec_path="spec.json",
        report_json_path="report.json",
        judge_model_env_key="JUDGE_LLM_MODEL",
        result=JudgeResult.model_validate(
            {
                "case_id": subset[0].report_case_id,
                "pass": False,
                "overall_score": 50,
                "hard_failures": ["触犯禁忌"],
                "forbidden_violation_count": 1,
                "scores": {"forbidden_output_safety": 1},
                "judge_summary": "fail",
            }
        ),
    )
    path = write_batch_summary(
        run_dir=tmp_path,
        records=[record],
        leaves=subset,
        errors=[{"case_id": "NG-01", "error": "生成失败"}],
    )
    body = path.read_text(encoding="utf-8")
    assert "硬失败清单" in body
    assert "NG-01" in body
