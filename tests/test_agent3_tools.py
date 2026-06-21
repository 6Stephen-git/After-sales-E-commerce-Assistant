"""
Agent3 话术工具核心测试：施压画像、举证卡点与补偿质量门禁。

原则：每类风格/质量规则保留一条代表用例；细节以 eval Judge 为准。
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.tools import agent2_tools, agent3_tools
from schemas import MaliciousDetectionOutput, MaliciousSignal


def test_semantic_pressure_profile_detection():
    """仅语义施压信号可降格；含硬规则信号时不应按施压处理。"""
    semantic_only = MaliciousDetectionOutput(
        risk_score=20,
        risk_level="high",
        triggered_signals=[
            MaliciousSignal(
                signal_type="review_blackmail",
                description="要挟",
                score=20,
                source="llm_semantic",
            )
        ],
    )
    with_hard_rule = MaliciousDetectionOutput(
        risk_score=40,
        risk_level="high",
        triggered_signals=[
            MaliciousSignal(
                signal_type="deceptive_credential",
                description="假证",
                score=20,
                source="hard_rule",
            )
        ],
    )
    assert agent2_tools.is_semantic_pressure_profile(semantic_only)
    assert not agent2_tools.is_semantic_pressure_profile(with_hard_rule)


def test_evidence_complete_stall_vs_request_allowed():
    """举证已齐时禁止「核对材料后再回复」；补证阶段仍可引导补充材料。"""
    stall_script = "我这边正在核对所有材料，马上回复您具体情况。"
    stall_payload = {
        "action_type": "defend_prepare",
        "malicious_risk_level": "high",
        "missing_evidence": [],
        "evidence_complete": True,
    }
    assert agent3_tools._has_evidence_complete_stall(stall_script, stall_payload)
    issues = agent3_tools._collect_style_issues(stall_script, stall_payload)
    assert issues and "举证已齐" in issues[0]

    request_script = "麻烦您再拍一下袖口近照，我收到马上核对。"
    request_payload = {
        "action_type": "evidence_request",
        "strategy_stage": "evidence_first",
        "missing_evidence": ["袖口近照"],
        "evidence_complete": False,
    }
    assert not agent3_tools._has_evidence_complete_stall(request_script, request_payload)


def test_de_escalate_style_guards():
    """施压降格：首句平台介入、踢皮球、非终局条文曝光应触发风格校验。"""
    platform_script = "这边材料不齐，我们会申请平台介入处理。"
    platform_payload = {
        "action_type": "rule_explain",
        "malicious_risk_level": "high",
        "buyer_service_posture": "de_escalate_within_bounds",
    }
    assert agent3_tools._has_platform_confrontation_opener(platform_script)
    platform_issues = agent3_tools._collect_style_issues(platform_script, platform_payload)
    assert platform_issues and "平台介入" in platform_issues[0]

    assert agent3_tools._contains_forbidden_phrase("我帮您进一步核对，看能不能处理。")

    rule_script = "根据坏单包退规则，签收后48小时内申请才行。"
    rule_payload = {"action_type": "rule_explain", "malicious_risk_level": "high"}
    assert agent3_tools._has_premature_rule_exposure(rule_script, rule_payload)

    defend_script = "根据平台规则，当前不满足直接退款条件。"
    defend_payload = {"action_type": "defend_prepare", "malicious_risk_level": "high"}
    assert not agent3_tools._has_premature_rule_exposure(defend_script, defend_payload)


def test_compensation_quality_guards():
    """契约报价须写出具体数额；部分补偿场景禁止向买家说比例。"""
    amount_script = "部分补偿在38块以内咱们灵活谈，您看行吗？"
    amount_issues = agent3_tools._collect_quality_issues(
        amount_script,
        {"proposed_compensation_amount": 38.4},
    )
    assert any("契约报价" in item for item in amount_issues)

    ratio_script = "三是部分补偿，补偿金额不超过订单总额的30%，您看行吗？"
    ratio_issues = agent3_tools._collect_quality_issues(
        ratio_script,
        {
            "resolution_contract": {"offered_modes": ["partial_compensate"]},
            "proposed_compensation_amount": 1589.7,
        },
    )
    assert any("比例" in item for item in ratio_issues)
