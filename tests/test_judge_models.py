"""
Judge 评分模型与 JSON Schema 一致性核心测试。

原则：schema 对齐、总分推导、禁忌 fail 规则与 hard_failures 容错各保留一条路径。
"""

import json
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from eval.pipeline.judge_models import JudgeResult, JudgeScores


def test_judge_score_fields_match_schema_required_fields():
    """JudgeScores 字段应与 judge_result.schema.json 的必填评分字段一致。"""
    schema_path = Path(ROOT_DIR) / "eval" / "content" / "prompts" / "judge_result.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required_scores = set(schema["properties"]["scores"]["required"])
    assert required_scores == set(JudgeScores.model_fields)
    assert "report_readability" not in required_scores


def test_derive_overall_score_and_new_score_fields():
    """分项 4~5 时不应误算低分；新增评分维度可校验。"""
    scores = JudgeScores(
        expectation_alignment=5,
        forbidden_output_safety=5,
        rule_understanding=4,
        rule_boundary_ability=5,
        evidence_handling=4,
        malicious_risk_recognition=4,
        customer_value_tradeoff=4,
        merchant_interest=5,
        buyer_communication=4,
        script_safety=5,
        script_reliability=5,
    )
    result = JudgeResult.model_validate(
        {
            "case_id": "MA-04_PROFESSIONAL_CLAIM_PATTERN",
            "pass": False,
            "overall_score": 45,
            "hard_failures": [],
            "forbidden_violation_count": 0,
            "scores": scores.model_dump(),
            "fail_reasons": [],
            "warnings": [],
            "suggested_fix_area": "",
            "judge_summary": "误算总分",
        }
    )
    assert result.overall_score >= 85
    assert result.pass_ is True
    assert result.scores.rule_boundary_ability == 5
    assert result.scores.script_reliability == 5


def test_forbidden_violation_fail_policy():
    """单项禁忌打 1 分不单独 fail；触犯 2 条及以上必须 fail。"""
    scores_one = {name: 5 for name in JudgeScores.model_fields}
    scores_one["forbidden_output_safety"] = 1
    mild = JudgeResult.model_validate(
        {
            "case_id": "CASE-FORBIDDEN-SCORE",
            "pass": False,
            "overall_score": 20,
            "hard_failures": [],
            "forbidden_violation_count": 1,
            "scores": scores_one,
            "fail_reasons": [],
            "warnings": [],
            "suggested_fix_area": "",
            "judge_summary": "触犯禁忌但仅降分项",
        }
    )
    assert mild.scores.forbidden_output_safety == 1
    assert mild.overall_score >= 80
    assert mild.pass_ is True

    scores_two = {name: 5 for name in JudgeScores.model_fields}
    severe = JudgeResult.model_validate(
        {
            "case_id": "CASE-FORBIDDEN-TWO",
            "pass": True,
            "overall_score": 95,
            "hard_failures": [],
            "forbidden_violation_count": 2,
            "scores": scores_two,
            "fail_reasons": [],
            "warnings": [],
            "suggested_fix_area": "",
            "judge_summary": "触犯两项禁忌",
        }
    )
    assert severe.scores.forbidden_output_safety == 1
    assert severe.pass_ is False
    assert severe.hard_failures


def test_hard_failures_string_coerced_to_list():
    """Judge LLM 将 hard_failures 写成字符串时应强制 fail。"""
    scores = {name: 4 for name in JudgeScores.model_fields}
    result = JudgeResult.model_validate(
        {
            "case_id": "CASE-HARD-STR",
            "pass": True,
            "overall_score": 90,
            "hard_failures": "报告事实与情景冲突",
            "forbidden_violation_count": 0,
            "scores": scores,
            "fail_reasons": [],
            "warnings": [],
            "suggested_fix_area": "",
            "judge_summary": "硬失败字符串",
        }
    )
    assert result.hard_failures == ["报告事实与情景冲突"]
    assert result.pass_ is False
