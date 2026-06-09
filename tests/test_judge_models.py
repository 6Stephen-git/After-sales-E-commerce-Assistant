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

from eval.pipeline.judge_models import JudgeResult, JudgeScores


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
