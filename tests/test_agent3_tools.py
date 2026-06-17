"""Agent3 话术工具：风格与金额质量校验。"""

from backend.tools import agent2_tools, agent3_tools
from schemas import MaliciousDetectionOutput, MaliciousSignal


def test_is_semantic_pressure_profile_only_blackmail():
    """仅 review_blackmail 语义信号时应判定为施压画像。"""
    profile = MaliciousDetectionOutput(
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
    assert agent2_tools.is_semantic_pressure_profile(profile)


def test_is_semantic_pressure_profile_rejects_hard_rule():
    """含硬规则信号时不应按施压降格处理。"""
    profile = MaliciousDetectionOutput(
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
    assert not agent2_tools.is_semantic_pressure_profile(profile)


def test_evidence_complete_stall_blocks_material_check_phrase():
    """举证已齐时「核对材料后再回复」应触发风格校验。"""
    script = "我这边正在核对所有材料，马上回复您具体情况。"
    payload = {
        "action_type": "defend_prepare",
        "malicious_risk_level": "high",
        "missing_evidence": [],
        "evidence_complete": True,
    }
    assert agent3_tools._has_evidence_complete_stall(script, payload)
    issues = agent3_tools._collect_style_issues(script, payload)
    assert issues and "举证已齐" in issues[0]


def test_evidence_request_allows_material_mention():
    """补证阶段仍可引导补充材料。"""
    script = "麻烦您再拍一下袖口近照，我收到马上核对。"
    payload = {
        "action_type": "evidence_request",
        "strategy_stage": "evidence_first",
        "missing_evidence": ["袖口近照"],
        "evidence_complete": False,
    }
    assert not agent3_tools._has_evidence_complete_stall(script, payload)


def test_de_escalate_blocks_platform_confrontation_opener():
    """施压降格场景首句提平台介入应触发风格校验。"""
    script = "这边材料不齐，我们会申请平台介入处理。"
    payload = {
        "action_type": "rule_explain",
        "malicious_risk_level": "high",
        "buyer_service_posture": "de_escalate_within_bounds",
    }
    assert agent3_tools._has_platform_confrontation_opener(script)
    issues = agent3_tools._collect_style_issues(script, payload)
    assert issues and "平台介入" in issues[0]


def test_hedging_phrase_triggers_forbidden():
    """踢皮球式「能不能处理」应被拦截。"""
    assert agent3_tools._contains_forbidden_phrase("我帮您进一步核对，看能不能处理。")


def test_premature_rule_exposure_on_rule_explain():
    """非终局抗辩时引用条文时效应触发风格校验。"""
    script = "根据坏单包退规则，签收后48小时内申请才行。"
    payload = {"action_type": "rule_explain", "malicious_risk_level": "high"}
    assert agent3_tools._has_premature_rule_exposure(script, payload)


def test_explicit_rule_allowed_on_defend_prepare_high_risk():
    """终局抗辩阶段允许较直接说明规则边界。"""
    script = "根据平台规则，当前不满足直接退款条件。"
    payload = {"action_type": "defend_prepare", "malicious_risk_level": "high"}
    assert not agent3_tools._has_premature_rule_exposure(script, payload)


def test_proposed_compensation_amount_must_match_contract():
    """契约已给出确定报价时，话术须写出该数额，不能只亮上限。"""
    script = "部分补偿在38块以内咱们灵活谈，您看行吗？"
    payload = {"proposed_compensation_amount": 38.4}
    issues = agent3_tools._collect_quality_issues(script, payload)
    assert any("契约报价" in item for item in issues)


def test_buyer_facing_ratio_exposure_blocked():
    """部分补偿场景面向买家禁止说比例。"""
    script = "三是部分补偿，补偿金额不超过订单总额的30%，您看行吗？"
    payload = {
        "resolution_contract": {"offered_modes": ["partial_compensate"]},
        "proposed_compensation_amount": 1589.7,
    }
    issues = agent3_tools._collect_quality_issues(script, payload)
    assert any("比例" in item for item in issues)
