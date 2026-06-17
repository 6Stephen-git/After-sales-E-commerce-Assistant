"""批量评测树与 run_batch_eval 链路测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from eval.pipeline.eval_tree import (
    EVAL_TREE_PATH,
    filter_leaves,
    list_lexicon_service_tags,
    load_eval_tree,
    validate_eval_tree,
    validate_service_tag_hints,
)
from eval.pipeline.judge_models import JudgeRecord, JudgeResult
from eval.pipeline.run_batch_eval import run_batch_eval, write_batch_summary


def test_load_default_eval_tree_is_ma_axis():
    """默认评测树应为 MA 轴 3 叶。"""
    leaves = load_eval_tree()
    validate_eval_tree(leaves, tree_path=EVAL_TREE_PATH)
    assert len(leaves) == 3
    assert {leaf.axis_id for leaf in leaves} == {"malicious"}


def test_filter_leaves_only_matches_case_id_fragment():
    """--only 应能按 case_id 片段过滤。"""
    leaves = load_eval_tree()
    filtered = filter_leaves(leaves, only="ma-01")
    assert len(filtered) == 1
    assert filtered[0].case_id == "MA-01_review_blackmail"


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
    md_path = tmp_path / "functional" / "phase1" / "malicious" / "MA-99_mock_case.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("# mock\n\n## 背景\n", encoding="utf-8")

    tree_path.write_text(
        """
version: 1
leaf_count: 1
axis_counts:
  malicious: 1
axes:
  - id: malicious
    label: 恶意抗辩
    prefix: MA
    dir: functional/phase1/malicious
    leaves:
      - case_id: MA-99_mock_case
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

    scenario_dir = tmp_path / "scenarios" / "phase1" / "ma-99_mock_case"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "spec.json").write_text(
        json.dumps({"meta": {"case_id": "MA-99_MOCK_CASE"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_json = tmp_path / "reports" / "phase1" / "MA-99_MOCK_CASE.json"
    report_json.parent.mkdir(parents=True)
    report_json.write_text("{}", encoding="utf-8")

    judge_record = JudgeRecord(
        run_id="test-run",
        case_id="MA-99_MOCK_CASE",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        spec_path=str(scenario_dir / "spec.json"),
        report_json_path=str(report_json),
        judge_model_env_key="JUDGE_LLM_MODEL",
        result=JudgeResult.model_validate(
            {
                "case_id": "MA-99_MOCK_CASE",
                "pass": True,
                "overall_score": 90,
                "scores": {"expectation_alignment": 5},
                "judge_summary": "ok",
            }
        ),
    )

    run_dir_target = tmp_path / "eval_runs" / "other" / "test-run"

    with (
        patch("eval.pipeline.run_batch_eval.validate_eval_tree"),
        patch(
            "eval.pipeline.run_batch_eval.scenario_output_dir",
            return_value=scenario_dir,
        ),
        patch(
            "eval.pipeline.run_batch_eval.manual_report_json_path",
            return_value=report_json,
        ),
        patch(
            "eval.pipeline.run_batch_eval.eval_run_dir",
            return_value=run_dir_target,
        ),
        patch("eval.pipeline.run_batch_eval.generate_and_run_leaf") as mock_gen,
        patch("eval.pipeline.run_batch_eval.judge_leaf_records", return_value=[judge_record]),
        patch("eval.pipeline.run_batch_eval.write_summary"),
    ):
        mock_gen.return_value = (scenario_dir, report_json)
        run_dir = run_batch_eval(tree_path=tree_path, run_id="test-run")

    batch_summary = run_dir / "BATCH_SUMMARY.md"
    assert batch_summary.is_file()
    text = batch_summary.read_text(encoding="utf-8")
    assert "MA 恶意抗辩" in text
    assert "MA-99_MOCK_CASE" in text or "完成 Judge 数：1" in text


def test_write_batch_summary_includes_axis_and_errors(tmp_path: Path):
    """BATCH_SUMMARY 应包含分轴统计与错误列表。"""
    leaves = load_eval_tree()
    subset = [leaf for leaf in leaves if leaf.case_id == "MA-01_review_blackmail"]
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
        assert_records=[],
        leaves=subset,
        errors=[{"case_id": "MA-02", "error": "生成失败"}],
    )
    body = path.read_text(encoding="utf-8")
    assert "硬断言失败清单" in body
    assert "Judge 硬失败清单" in body
    assert "MA-02" in body
