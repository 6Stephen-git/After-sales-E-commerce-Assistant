"""
情景 spec 转 fixture 的评测安全与字段兼容测试。
"""

import os
import sys

# ---------- 与仓库根对齐的导入路径（单测可直接 pytest 运行） ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pytest

from eval.pipeline.scenario_spec import ScenarioSpec
from eval.pipeline.spec_to_fixture import (
    spec_to_fixture_payload,
    validate_fixture_no_answer_leak,
)


def _base_spec(**updates):
    """构造最小 scenario_spec，便于测试 fixture 组装逻辑。"""
    payload = {
        "meta": {"case_id": "CASE-TEST", "title": "测试情景"},
        "human_review": {
            "scenario_restated": "仅供审阅",
            "fixture_focus": "仅供审阅",
            "checks_before_run": ["确认不泄题"],
        },
        "expectation": {
            "intent_summary": "仅供 Judge",
            "acceptable_dispositions": ["negotiate"],
            "forbidden_outputs": ["禁止直接退款"],
        },
        "taxonomy": {
            "primary_axis": "conflict",
            "evidence_level": "medium",
            "buyer_risk": "medium",
            "order_amount_band": "medium",
            "complexity": "conflict",
        },
        "materials": {
            "order_amount": 100,
            "chat_history": [{"role": "buyer", "content": "商品到货后有争议"}],
            "platform_service_tags": [],
        },
        "buyer_profile": {"buyer_id": "buyer_1", "purchase_count": 1},
        "evidence_facts": {
            "issue_summary": "买家围绕服务承诺提出售后",
            "dispute_issue_type": "服务承诺边界",
            "evidence_quality": "medium",
            "logistics": {"goods_received": True, "logistics_normal": True},
        },
    }
    payload.update(updates)
    return ScenarioSpec.model_validate(payload)


def test_dispute_issue_type_maps_to_fact_defect_type():
    """新字段争议问题类型应兼容映射到底层 FactOutput.defect_type。"""
    fixture = spec_to_fixture_payload(_base_spec())
    case = fixture["cases"][0]
    assert case["facts_override"]["defect_type"] == "服务承诺边界"


def test_fixture_payload_does_not_leak_expectation_fields():
    """expectation 与 human_review 只留在 spec，不应进入被测 agent fixture。"""
    fixture = spec_to_fixture_payload(_base_spec())
    serialized = str(fixture)
    assert "expectation" not in serialized
    assert "human_review" not in serialized
    assert "禁止直接退款" not in serialized


def test_fixture_leak_check_rejects_answer_markers():
    """泄题检查应拦截答案字段或中文答案提示。"""
    payload = {
        "version": "1.0",
        "cases": [
            {
                "meta": {"case_id": "CASE-LEAK"},
                "materials": {"chat_history": [{"role": "buyer", "content": "期望策略：直接退款"}]},
            }
        ],
    }
    with pytest.raises(ValueError, match="评测答案泄露"):
        validate_fixture_no_answer_leak(payload)
