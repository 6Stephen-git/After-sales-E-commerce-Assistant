"""
ResolutionContract 单元测试：方案空间推断与动作物化（无 LLM）。
"""

import os
import sys

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.agents.agent2.resolution_contract import (
    buyer_demands_refund_only,
    finalize_proposed_compensation,
    infer_resolution_contract,
    materialize_action_from_resolution,
)
from schemas import (
    ACTION_EVIDENCE_REQUEST,
    ACTION_MERCHANT_REMEDY,
    COMPENSATION_POLICY_EXPLICIT_AMOUNT,
    ACTION_MONETARY_SETTLE,
    ACTION_RULE_EXPLAIN,
    BuyerProfile,
    ChatTurn,
    FactOutput,
    MaliciousDetectionOutput,
    MaliciousSignal,
    RuleConstraint,
    SETTLEMENT_PARTIAL_COMPENSATE,
    SETTLEMENT_REFUND_ONLY,
    SETTLEMENT_RETURN_REFUND,
    StrategyInput,
    RULE_CONSTRAINT_RATIO_LIMIT,
    RULE_CONSTRAINT_APPLIES,
)


def _base_input(**fact_overrides) -> StrategyInput:
    base_facts = dict(
        issue_summary="挂耳咖啡漏粉，要求仅退款",
        evidence_quality="high",
        decision_readiness="high",
        missing_evidence=[],
        visual_goods_recoverability="resalable",
        attributes={"rule_context": {"compensation_ratio_cap": 0.3}},
    )
    base_facts.update(fact_overrides)
    facts = FactOutput(**base_facts)
    return StrategyInput(
        order_amount=92.0,
        buyer_profile=BuyerProfile(buyer_id="hash-1", purchase_count=32),
        facts=facts,
        chat_turns=[
            ChatTurn(role="buyer", content="老客户了，今天必须92块仅退款"),
            ChatTurn(role="merchant", content="照片和开箱视频我都看到了"),
        ],
        rule_constraints=[
            RuleConstraint(
                constraint_type=RULE_CONSTRAINT_RATIO_LIMIT,
                status=RULE_CONSTRAINT_APPLIES,
                text="赔偿不超过订单金额30%",
            )
        ],
    )


class TestResolutionContract:
    def test_buyer_demands_refund_only(self):
        assert buyer_demands_refund_only(_base_input()) is True

    def test_decision_ready_when_high_readiness_and_no_missing(self):
        malicious = MaliciousDetectionOutput(
            risk_level="medium",
            triggered_signals=[
                MaliciousSignal(signal_type="semantic_pressure", description="差评要挟", score=20)
            ],
        )
        resolution = infer_resolution_contract(
            _base_input(),
            disposition="negotiate",
            merchant_fault=False,
            timing_not_satisfied=False,
            malicious_result=malicious,
            de_escalate_pressure=True,
            evidence_insufficient=False,
        )
        assert resolution.decision_ready is True
        assert SETTLEMENT_REFUND_ONLY in resolution.forbidden_modes
        assert SETTLEMENT_RETURN_REFUND in resolution.offered_modes
        assert SETTLEMENT_PARTIAL_COMPENSATE in resolution.offered_modes

    def test_evidence_insufficient_blocks_offers(self):
        inp = _base_input(missing_evidence=["近照"], decision_readiness="low")
        malicious = MaliciousDetectionOutput(risk_level="low")
        resolution = infer_resolution_contract(
            inp,
            disposition="negotiate",
            merchant_fault=False,
            timing_not_satisfied=False,
            malicious_result=malicious,
            de_escalate_pressure=False,
            evidence_insufficient=True,
        )
        assert resolution.decision_ready is False
        assert resolution.offered_modes == []

    def test_merchant_fault_requires_inspection(self):
        inp = _base_input(
            issue_summary="物流破损裙子撕裂",
            visual_goods_recoverability="unrecoverable",
        )
        malicious = MaliciousDetectionOutput(risk_level="low")
        resolution = infer_resolution_contract(
            inp,
            disposition="compensate",
            merchant_fault=True,
            timing_not_satisfied=False,
            malicious_result=malicious,
            de_escalate_pressure=False,
            evidence_insufficient=False,
        )
        assert resolution.require_inspection_before_refund is True
        assert SETTLEMENT_REFUND_ONLY in resolution.forbidden_modes
        action = materialize_action_from_resolution(
            resolution,
            input_data=inp,
            disposition="compensate",
            merchant_fault=True,
            timing_not_satisfied=False,
            de_escalate_pressure=False,
        )
        assert action["action_type"] == ACTION_MERCHANT_REMEDY

    def test_partial_compensate_materializes_monetary_settle(self):
        malicious = MaliciousDetectionOutput(
            risk_level="medium",
            triggered_signals=[
                MaliciousSignal(signal_type="semantic_pressure", description="要挟", score=15)
            ],
        )
        resolution = infer_resolution_contract(
            _base_input(),
            disposition="negotiate",
            merchant_fault=False,
            timing_not_satisfied=False,
            malicious_result=malicious,
            de_escalate_pressure=True,
            evidence_insufficient=False,
        )
        action = materialize_action_from_resolution(
            resolution,
            input_data=_base_input(),
            disposition="negotiate",
            merchant_fault=False,
            timing_not_satisfied=False,
            de_escalate_pressure=True,
        )
        assert action["action_type"] == ACTION_MONETARY_SETTLE
        assert "部分补偿" in action["next_step"] or "规则内" in action["next_step"]

    def test_evidence_first_materializes_evidence_request(self):
        inp = _base_input(missing_evidence=["外包装照"], decision_readiness="low")
        malicious = MaliciousDetectionOutput(risk_level="low")
        resolution = infer_resolution_contract(
            inp,
            disposition="negotiate",
            merchant_fault=False,
            timing_not_satisfied=False,
            malicious_result=malicious,
            de_escalate_pressure=False,
            evidence_insufficient=True,
        )
        action = materialize_action_from_resolution(
            resolution,
            input_data=inp,
            disposition="negotiate",
            merchant_fault=False,
            timing_not_satisfied=False,
            de_escalate_pressure=False,
        )
        assert action["action_type"] == ACTION_EVIDENCE_REQUEST

    def test_finalize_proposed_compensation_amount(self):
        malicious = MaliciousDetectionOutput(risk_level="low")
        resolution = infer_resolution_contract(
            _base_input(),
            disposition="negotiate",
            merchant_fault=False,
            timing_not_satisfied=True,
            malicious_result=malicious,
            de_escalate_pressure=False,
            evidence_insufficient=False,
        )
        finalized = finalize_proposed_compensation(resolution, order_amount=92.0)
        assert finalized.proposed_compensation_amount == pytest.approx(27.6)

    def test_finalize_merchant_remedy_partial_gets_proposed_amount(self):
        malicious = MaliciousDetectionOutput(risk_level="low")
        resolution = infer_resolution_contract(
            _base_input(visual_goods_recoverability="unrecoverable"),
            disposition="compensate",
            merchant_fault=True,
            timing_not_satisfied=False,
            malicious_result=malicious,
            de_escalate_pressure=False,
            evidence_insufficient=False,
        )
        finalized = finalize_proposed_compensation(resolution, order_amount=5299.0)
        assert finalized.proposed_compensation_amount == pytest.approx(1589.7)
