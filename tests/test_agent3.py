"""
Agent3（话术生成员）核心契约测试。

原则：仅保留 disposition→response_mode、dialogue_context 注入、fallback 与补偿门禁；
业务回归以 eval 评测树 + Judge 为准。
"""

import os
import sys

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.agents.agent3.script_generator import generate
import backend.agents.agent3.script_generator as script_generator_module
from schemas import (
    ACTION_MERCHANT_REMEDY,
    ChatTurn,
    COMPENSATION_POLICY_EXPLICIT_AMOUNT,
    CustomerValueOutput,
    DialogueContext,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    EVIDENCE_HIGH,
    FactOutput,
    MaliciousDetectionOutput,
    RESPONSE_MODE_MALICIOUS_RISK,
    RESPONSE_MODE_MERCHANT_FAULT,
    RESPONSE_MODE_NEUTRAL_NEGOTIATE,
    STRATEGY_STAGE_COMPENSATE_CLOSE,
    STRATEGY_STAGE_DEFEND_PLATFORM,
    STRATEGY_STAGE_EVIDENCE_FIRST,
    STRATEGY_STAGE_NEGOTIATE_SETTLE,
    ScriptInput,
    StrategyOutput,
)


def _mock_script(text: str):
    """构造固定话术 mock。"""

    def _inner(_payload):
        return text

    return _inner


def _minimal_dialogue_context() -> DialogueContext:
    """最小 dialogue_context。"""
    return DialogueContext(
        dialogue_mode="cold_start",
        blocked_evidence_requests=[],
        actionable_evidence_requests=[],
        fallback_script="我这边还在核对，核实完马上回您。",
    )


def _phone_scratch_dialogue_context() -> DialogueContext:
    """手机划痕场景 dialogue_context。"""
    return DialogueContext(
        dialogue_mode="continue",
        blocked_evidence_requests=["开箱视频", "完整开箱录像"],
        actionable_evidence_requests=["划痕位置近景照片或短视频", "未使用状态展示"],
        fallback_script="照片我看了，有些反光看不太清。方便再拍一段划痕位置的近景吗？我收到马上继续查。",
    )


class TestAgent3Generate:
    @pytest.mark.parametrize(
        "disposition,stage,response_mode,script_text,extra_strategy",
        [
            (
                DISPOSITION_DEFEND,
                STRATEGY_STAGE_DEFEND_PLATFORM,
                RESPONSE_MODE_NEUTRAL_NEGOTIATE,
                "这单我核过了，细节还需要您补一下凭证。",
                {},
            ),
            (
                DISPOSITION_NEGOTIATE,
                STRATEGY_STAGE_NEGOTIATE_SETTLE,
                RESPONSE_MODE_NEUTRAL_NEGOTIATE,
                "咱们可以商量着解决。",
                {},
            ),
            (
                DISPOSITION_COMPENSATE,
                STRATEGY_STAGE_COMPENSATE_CLOSE,
                RESPONSE_MODE_MERCHANT_FAULT,
                "这次确实是我们这边的问题，给您安排退款。",
                {
                    "action_type": ACTION_MERCHANT_REMEDY,
                    "compensation_policy": COMPENSATION_POLICY_EXPLICIT_AMOUNT,
                },
            ),
        ],
    )
    def test_generate_maps_disposition_to_response_mode(
        self, monkeypatch, disposition, stage, response_mode, script_text, extra_strategy
    ):
        """各 disposition 应映射到正确 response_mode。"""
        monkeypatch.setattr(script_generator_module, "generate_buyer_script", _mock_script(script_text))
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=disposition,
                strategy_stage=stage,
                confidence=0.75,
                dialogue_context=_minimal_dialogue_context(),
                **extra_strategy,
            ),
            facts=FactOutput(issue_summary="买家反馈商品问题", evidence_quality=EVIDENCE_HIGH),
            order_id="ORDER-A3-MODE",
            order_amount=129.0,
        )
        output = generate(input_data)
        assert output.response_mode == response_mode
        assert output.script == script_text

    def test_generate_should_pass_dialogue_context_to_script_llm(self, monkeypatch):
        """Agent2 dialogue_context 应注入话术生成 payload。"""
        captured: dict = {}

        def _capture_script(payload):
            captured.update(payload)
            return "续写话术内容"

        monkeypatch.setattr(script_generator_module, "generate_buyer_script", _capture_script)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_EVIDENCE_FIRST,
                dialogue_context=_phone_scratch_dialogue_context(),
            ),
            facts=FactOutput(
                issue_summary="买家反馈手机屏幕有划痕",
                missing_evidence=["缺少开箱视频"],
            ),
            chat_history=[
                ChatTurn(role="merchant", content="麻烦提供开箱视频"),
                ChatTurn(role="buyer", content="拆的时候没拍"),
            ],
            order_id="O1",
        )
        output = generate(input_data)
        assert output.script == "续写话术内容"
        ctx = captured.get("dialogue_context") or {}
        assert ctx.get("dialogue_mode") == "continue"
        assert "开箱视频" in ctx.get("blocked_evidence_requests", [""])[0]

    def test_generate_should_use_fallback_when_llm_fails(self, monkeypatch):
        """话术 LLM 失败时走 dialogue_context.fallback；字段缺失仍输出非空话术。"""
        monkeypatch.setattr(script_generator_module, "generate_buyer_script", lambda *_a, **_k: None)

        with_ctx = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_EVIDENCE_FIRST,
                dialogue_context=_phone_scratch_dialogue_context(),
            ),
            facts=FactOutput(issue_summary="屏幕划痕"),
            chat_history=[ChatTurn(role="buyer", content="有划痕")],
            order_id="O2",
        )
        output_ctx = generate(with_ctx)
        assert "反光" in output_ctx.script
        assert "开箱" not in output_ctx.script

        minimal = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_NEGOTIATE_SETTLE,
            ),
            facts=FactOutput(),
            order_id="",
        )
        output_min = generate(minimal)
        assert output_min.script.strip() != ""
        assert "退款" not in output_min.script
        assert "补偿" not in output_min.script

    def test_generate_watermark_fallback_should_not_describe_defect_severity(self, monkeypatch):
        """网图/水印举证：fallback 只提疑点与补证，不复述货损程度。"""
        watermark_ctx = DialogueContext(
            dialogue_mode="cold_start",
            blocked_evidence_requests=[],
            actionable_evidence_requests=["近景视频", "开箱连续录像"],
            fallback_script="图片右下好像有网络水印，麻烦拍段近景或开箱连续录像方便核实。",
        )
        monkeypatch.setattr(script_generator_module, "generate_buyer_script", lambda *_a, **_k: None)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_EVIDENCE_FIRST,
                dialogue_context=watermark_ctx,
            ),
            facts=FactOutput(
                issue_summary="买家称香蕉腐烂要求退款",
                missing_evidence=["近景视频", "开箱连续录像"],
                visual_observations=["图片右下角可见门户网站水印"],
            ),
            order_id="O-banana",
        )
        output = generate(input_data)
        assert "水印" in output.script
        assert "烂" not in output.script
        assert "明显" not in output.script

    def test_generate_compensation_payload_contract(self, monkeypatch):
        """规则解释不报金额；金额和解须报具体数额并注入客户价值。"""
        captured: dict = {}

        def _capture_script(payload):
            captured.update(payload)
            return "话术占位"

        monkeypatch.setattr(script_generator_module, "generate_buyer_script", _capture_script)

        rule_input = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_NEGOTIATE_SETTLE,
                action_type="rule_explain",
                compensation_policy="none",
                rule_constraints=["七天无理由成立前提是商品完好"],
                customer_value=CustomerValueOutput(
                    channel="long_term",
                    compensation_uplift="可在规则内略增5%补偿弹性",
                ),
                dialogue_context=_minimal_dialogue_context(),
            ),
            facts=FactOutput(issue_summary="买家主张七天无理由应直接退货"),
            order_id="ORDER-A3-RULE",
        )
        generate(rule_input)
        assert captured.get("action_type") == "rule_explain"
        assert captured.get("must_state_compensation_amount") is False
        assert "compensation_uplift" not in captured

        captured.clear()
        monetary_input = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_NEGOTIATE_SETTLE,
                action_type="monetary_settle",
                compensation_policy="explicit_amount",
                customer_value=CustomerValueOutput(
                    channel="long_term",
                    long_term_triggered=True,
                    tone_suggestion="语气可稍暖，体现重视老客",
                    compensation_uplift="可在规则内略增5%补偿弹性",
                ),
                dialogue_context=_minimal_dialogue_context(),
            ),
            facts=FactOutput(issue_summary="买家认为实物与描述略有差异"),
            order_id="ORDER-A3-AMT",
            order_amount=320.0,
        )
        generate(monetary_input)
        assert captured.get("must_state_compensation_amount") is True
        assert captured.get("customer_value_channel") == "long_term"
        assert captured.get("compensation_uplift") == "可在规则内略增5%补偿弹性"

    def test_generate_high_malicious_should_use_malicious_risk_mode(self, monkeypatch):
        """高恶意风险：应对思想为依据应对。"""
        monkeypatch.setattr(
            script_generator_module,
            "generate_buyer_script",
            _mock_script("麻烦补一下凭证，我核对后马上处理。"),
        )
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_NEGOTIATE_SETTLE,
                malicious_detection=MaliciousDetectionOutput(risk_level="high", risk_score=85),
                dialogue_context=_minimal_dialogue_context(),
            ),
            facts=FactOutput(issue_summary="买家要求仅退款不退货"),
            order_id="ORDER-A3-020",
        )
        output = generate(input_data)
        assert output.response_mode == RESPONSE_MODE_MALICIOUS_RISK
