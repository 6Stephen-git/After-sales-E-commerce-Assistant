"""
Judge 评分模型与 JSON Schema 一致性测试。
"""

import json
import os
import sys
from pathlib import Path

# ---------- 与仓库根对齐的导入路径（单测可直接 pytest 运行） ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from eval.pipeline.judge_models import JudgeResult, JudgeScores, derive_overall_score


def test_judge_score_fields_match_schema_required_fields():
    """JudgeScores 字段应与 judge_result.schema.json 的必填评分字段一致。"""
    schema_path = Path(ROOT_DIR) / "eval" / "content" / "prompts" / "judge_result.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required_scores = set(schema["properties"]["scores"]["required"])
    assert required_scores == set(JudgeScores.model_fields)
    assert "report_readability" not in required_scores


def test_judge_result_accepts_new_scores():
    """JudgeResult 应接受本轮新增的规则边界、恶意风险、客户价值和话术可靠性评分。"""
    payload = {
        "case_id": "CASE-JUDGE",
        "pass": True,
        "overall_score": 88,
        "hard_failures": [],
        "forbidden_violation_count": 0,
        "scores": {name: 5 for name in JudgeScores.model_fields},
        "fail_reasons": [],
        "warnings": [],
        "suggested_fix_area": "",
        "judge_summary": "通过",
    }
    result = JudgeResult.model_validate(payload)
    assert result.pass_ is True
    assert result.scores.rule_boundary_ability == 5
    assert result.scores.script_reliability == 5


def test_derive_overall_score_from_ma04_like_dimensions():
    """分项 4~5 时不应把均值 4.5 误当 overall_score=45。"""
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


def test_forbidden_output_safety_one_does_not_auto_fail_without_hard_failures():
    """禁忌分项打 1 分不单独 fail，由总分反映；无 hard_failures 时其余项高仍可 pass。"""
    scores = {name: 5 for name in JudgeScores.model_fields}
    scores["forbidden_output_safety"] = 1
    result = JudgeResult.model_validate(
        {
            "case_id": "CASE-FORBIDDEN-SCORE",
            "pass": False,
            "overall_score": 20,
            "hard_failures": [],
            "forbidden_violation_count": 1,
            "scores": scores,
            "fail_reasons": [],
            "warnings": [],
            "suggested_fix_area": "",
            "judge_summary": "触犯禁忌但仅降分项",
        }
    )
    assert result.scores.forbidden_output_safety == 1
    assert result.overall_score >= 80
    assert result.pass_ is True


def test_hard_failures_string_coerced_to_list():
    """Judge LLM 将 hard_failures 写成字符串时不应导致校验失败。"""
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


def test_two_forbidden_violations_auto_fail():
    """触犯 2 条及以上禁忌必须 fail。"""
    scores = {name: 5 for name in JudgeScores.model_fields}
    result = JudgeResult.model_validate(
        {
            "case_id": "CASE-FORBIDDEN-TWO",
            "pass": True,
            "overall_score": 95,
            "hard_failures": [],
            "forbidden_violation_count": 2,
            "scores": scores,
            "fail_reasons": [],
            "warnings": [],
            "suggested_fix_area": "",
            "judge_summary": "触犯两项禁忌",
        }
    )
    assert result.scores.forbidden_output_safety == 1
    assert result.pass_ is False
    assert result.hard_failures
