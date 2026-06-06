"""LLM-as-Judge 跑批评测链路测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.pipeline.judge_models import JudgeResult
from eval.pipeline.judge_cases import (
    build_report_markdown_summary,
    judge_case,
    main,
)


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _scenario_output(tmp_path: Path) -> Path:
    scenario_dir = tmp_path / "output" / "scenarios" / "scenario-001"
    _write_json(
        scenario_dir / "spec.json",
        {
            "meta": {"case_id": "SCENARIO-001", "title": "七天无理由完好争议"},
            "scenario_narrative": "买家批量试穿 30 件后要求七天无理由退货。",
            "expectation": {
                "intent_summary": "先解释七天无理由的商品完好前提，再协商处理。",
                "acceptable_dispositions": ["negotiate"],
                "forbidden_outputs": ["未解释完好前提就直接给金额补偿"],
            },
            "taxonomy": {"primary_axis": "rule", "evidence_level": "medium"},
        },
    )
    return scenario_dir


def _report_files(tmp_path: Path) -> tuple[Path, Path]:
    report_json = _write_json(
        tmp_path / "output" / "manual_reports" / "SCENARIO-001.json",
        {
            "dispute_id": "DISPUTE-SCENARIO-001",
            "facts": {
                "issue_summary": "七天无理由与商品完好争议",
                "defect_type": "无瑕疵",
                "evidence_quality": "medium",
            },
            "strategy": {
                "disposition": "negotiate",
                "action_type": "rule_explain",
                "strategy_direction_summary": "解释商品完好规则后引导寄回验收。",
                "platform_rule_basis": ["七天无理由退货要求商品完好。"],
            },
            "scripts": {
                "script": "七天无理由需要商品保持完好，我们收到后会按规则验收。",
                "response_mode": "neutral_negotiate",
            },
        },
    )
    report_md = report_json.with_suffix(".md")
    report_md.write_text(
        "\n".join(
            [
                "# 七天无理由完好争议",
                "## 核心结论区",
                "策略方向：解释规则后协商。",
                "## 关键依据区",
                "平台规则依据：商品应当完好。",
                "## 话术区",
                "推荐话术：七天无理由需要商品保持完好。",
                "## 结构化附录",
                "完整 JSON 见同目录。",
            ]
        ),
        encoding="utf-8",
    )
    return report_json, report_md


def _judge_payload(*, passed: bool = True) -> dict:
    return {
        "case_id": "SCENARIO-001",
        "pass": passed,
        "overall_score": 88 if passed else 72,
        "hard_failures": [] if passed else ["命中禁忌：未解释完好前提就直接给金额补偿"],
        "scores": {
            "expectation_alignment": 5 if passed else 2,
            "forbidden_output_safety": 5 if passed else 1,
            "rule_understanding": 4,
            "evidence_handling": 4,
            "merchant_interest": 4,
            "buyer_communication": 4,
            "script_safety": 5 if passed else 3,
            "report_readability": 4,
        },
        "fail_reasons": [] if passed else ["话术提前抛出金额补偿。"],
        "warnings": ["话术可更温和"],
        "suggested_fix_area": "agent3_script",
        "judge_summary": "整体符合预期。" if passed else "触犯禁忌，需要调整。",
    }


def test_judge_result_forces_fail_when_hard_failures_present() -> None:
    result = JudgeResult.model_validate(
        {
            **_judge_payload(passed=True),
            "pass": True,
            "hard_failures": ["承诺必退"],
        }
    )

    assert result.pass_ is False


def test_build_report_markdown_summary_keeps_only_review_sections() -> None:
    markdown = "\n".join(
        [
            "# title",
            "## 核心结论区",
            "核心内容",
            "## 关键依据区",
            "依据内容",
            "## 话术区",
            "话术内容",
            "## 参考信息区",
            "不应进入 judge 摘要",
        ]
    )

    summary = build_report_markdown_summary(markdown)

    assert "核心内容" in summary
    assert "依据内容" in summary
    assert "话术内容" in summary
    assert "不应进入 judge 摘要" not in summary


def test_judge_case_writes_jsonl_record_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario_dir = _scenario_output(tmp_path)
    report_json, _ = _report_files(tmp_path)
    eval_root = tmp_path / "eval_runs"

    monkeypatch.setattr("eval.pipeline.judge_cases.call_llm_json", lambda **_kwargs: _judge_payload(passed=True))

    record = judge_case(
        scenario_output=scenario_dir,
        report_json_path=report_json,
        eval_root=eval_root,
        run_id="RUN-001",
    )

    record_path = eval_root / "RUN-001" / "records.jsonl"
    summary_path = eval_root / "RUN-001" / "SUMMARY.md"
    assert record.result.pass_ is True
    assert record_path.is_file()
    assert summary_path.is_file()

    rows = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]
    summary_text = summary_path.read_text(encoding="utf-8")
    assert rows[0]["case_id"] == "SCENARIO-001"
    assert rows[0]["result"]["pass"] is True
    assert "通过率" in summary_text
    assert "策略选择：`negotiate` / `rule_explain`" in summary_text
    assert "推荐话术：七天无理由需要商品保持完好，我们收到后会按规则验收。" in summary_text
    assert "`expectation_alignment`：5/5" in summary_text
    assert "`script_safety`：5/5" in summary_text


def test_main_returns_nonzero_only_when_fail_on_judge_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario_dir = _scenario_output(tmp_path)
    report_json, _ = _report_files(tmp_path)
    eval_root = tmp_path / "eval_runs"
    monkeypatch.setattr("eval.pipeline.judge_cases.call_llm_json", lambda **_kwargs: _judge_payload(passed=False))

    ok_exit = main(
        [
            "--scenario-output",
            str(scenario_dir),
            "--report",
            str(report_json),
            "--eval-root",
            str(eval_root),
            "--run-id",
            "RUN-FAIL",
        ]
    )
    fail_exit = main(
        [
            "--scenario-output",
            str(scenario_dir),
            "--report",
            str(report_json),
            "--eval-root",
            str(eval_root),
            "--run-id",
            "RUN-FAIL-GATED",
            "--fail-on-judge-fail",
        ]
    )

    assert ok_exit == 0
    assert fail_exit == 1
