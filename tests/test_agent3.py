"""
Agent3（话术生成员）模块测试
覆盖：应对思想推导、dialogue_context 消费、话术生成失败走 fallback、边界场景
"""

import os
import sys


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


from backend.agents.agent3.script_generator import generate
import backend.agents.agent3.script_generator as script_generator_module
from schemas import (
    ChatTurn,
    CustomerValueOutput,
    DialogueContext,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    EVIDENCE_HIGH,
    FactOutput,
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


def _phone_scratch_dialogue_context() -> DialogueContext:
    """模拟 Agent2 输出的 dialogue_context（手机划痕 + 已拒开箱）。"""
    return DialogueContext(
        dialogue_mode="continue",
        blocked_evidence_requests=["开箱视频", "完整开箱录像"],
        actionable_evidence_requests=["划痕位置近景照片或短视频", "未使用状态展示"],
        fallback_script="照片我看了，有些反光看不太清。方便再拍一段划痕位置的近景吗？我收到马上继续查。",
    )


def _minimal_dialogue_context() -> DialogueContext:
    """最小 dialogue_context mock。"""
    return DialogueContext(
        dialogue_mode="cold_start",
        blocked_evidence_requests=[],
        actionable_evidence_requests=[],
        fallback_script="我这边还在核对，核实完马上回您。",
    )


class TestAgent3Generate:
    def test_generate_defend_should_use_malicious_risk_mode(self, monkeypatch):
        """抗辩策略：应对思想为依据应对。"""
        script_text = "这单我核过了，细节还需要您补一下凭证，我这边按规则整理材料。"
        monkeypatch.setattr(script_generator_module, "generate_buyer_script", _mock_script(script_text))
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_DEFEND,
                strategy_stage=STRATEGY_STAGE_DEFEND_PLATFORM,
                confidence=0.72,
                dialogue_context=_minimal_dialogue_context(),
            ),
            facts=FactOutput(
                issue_summary="买家反馈商品无法正常使用",
                defect_type="功能异常",
            ),
            order_id="ORDER-A3-001",
            order_amount=129.0,
        )

        output = generate(input_data)
        assert output.response_mode == RESPONSE_MODE_MALICIOUS_RISK
        assert output.script == script_text

    def test_generate_compensate_close_should_use_merchant_fault_mode(self, monkeypatch):
        """善后收尾阶段：应对思想为主动担责。"""
        script_text = "这次确实是我们这边的问题，给您安排退款，钱会原路返回。"
        monkeypatch.setattr(script_generator_module, "generate_buyer_script", _mock_script(script_text))
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_COMPENSATE,
                strategy_stage=STRATEGY_STAGE_COMPENSATE_CLOSE,
                confidence=0.81,
                dialogue_context=_minimal_dialogue_context(),
            ),
            facts=FactOutput(
                issue_summary="买家收到的商品存在明显破损",
                defect_type="破损",
                evidence_quality=EVIDENCE_HIGH,
            ),
            order_id="ORDER-A3-003",
            order_amount=156.0,
        )

        output = generate(input_data)
        assert output.response_mode == RESPONSE_MODE_MERCHANT_FAULT
        assert "退款" in output.script

    def test_generate_negotiate_should_use_neutral_mode(self, monkeypatch):
        """协商策略：应对思想为协商沟通。"""
        monkeypatch.setattr(
            script_generator_module,
            "generate_buyer_script",
            _mock_script("咱们可以商量着解决。"),
        )
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_NEGOTIATE_SETTLE,
                confidence=0.7,
                dialogue_context=_minimal_dialogue_context(),
            ),
            facts=FactOutput(issue_summary="买家认为实物与页面展示不一致"),
            order_id="ORDER-A3-002",
            order_amount=88.5,
        )

        output = generate(input_data)
        assert output.response_mode == RESPONSE_MODE_NEUTRAL_NEGOTIATE

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

    def test_generate_should_use_fallback_script_when_script_llm_fails(self, monkeypatch):
        """话术 LLM 失败时使用 dialogue_context.fallback_script。"""
        monkeypatch.setattr(script_generator_module, "generate_buyer_script", lambda *_a, **_k: None)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_EVIDENCE_FIRST,
                dialogue_context=_phone_scratch_dialogue_context(),
            ),
            facts=FactOutput(issue_summary="屏幕划痕"),
            chat_history=[ChatTurn(role="buyer", content="有划痕")],
            order_id="O2",
        )

        output = generate(input_data)
        assert "反光" in output.script
        assert "开箱" not in output.script
        assert not output.script.startswith("您好")

    def test_generate_watermark_evidence_should_not_describe_defect_severity(self, monkeypatch):
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

    def test_generate_should_pass_customer_value_to_script_llm(self, monkeypatch):
        """客户价值字段应注入话术 LLM payload，供语气与补偿弹性调节。"""
        captured: dict = {}

        def _capture_script(payload):
            captured.update(payload)
            return "老客协商话术"

        monkeypatch.setattr(script_generator_module, "generate_buyer_script", _capture_script)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_NEGOTIATE_SETTLE,
                customer_value=CustomerValueOutput(
                    channel="long_term",
                    long_term_triggered=True,
                    tone_suggestion="语气可稍暖，体现重视老客",
                    compensation_uplift="可在规则内略增5%补偿弹性",
                ),
                dialogue_context=_minimal_dialogue_context(),
            ),
            facts=FactOutput(issue_summary="买家认为实物与描述略有差异"),
            order_id="ORDER-A3-CV",
            order_amount=320.0,
        )

        output = generate(input_data)
        assert output.script == "老客协商话术"
        assert captured.get("customer_value_channel") == "long_term"
        assert captured.get("compensation_uplift") == "可在规则内略增5%补偿弹性"
        assert "老客" in captured.get("tone_hint", "")

    def test_generate_evidence_first_minimal_fallback_should_not_offer_compensation(self, monkeypatch):
        """全 LLM 失败：最小兜底不提补偿。"""
        monkeypatch.setattr(script_generator_module, "generate_buyer_script", lambda *_a, **_k: None)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_EVIDENCE_FIRST,
            ),
            facts=FactOutput(missing_evidence=["缺少近景图"]),
            order_id="ORDER-A3-011",
        )

        output = generate(input_data)
        assert output.script.strip() != ""
        assert "退款" not in output.script
        assert "补偿" not in output.script

    def test_generate_boundary_should_still_output_non_empty_script(self, monkeypatch):
        """边界场景：字段缺失时仍输出可用话术。"""
        monkeypatch.setattr(script_generator_module, "generate_buyer_script", lambda *_a, **_k: None)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                strategy_stage=STRATEGY_STAGE_NEGOTIATE_SETTLE,
            ),
            facts=FactOutput(),
            order_id="",
        )

        output = generate(input_data)
        assert output.script.strip() != ""

    def test_generate_high_malicious_should_use_malicious_risk_mode(self, monkeypatch):
        """高恶意风险：应对思想为依据应对。"""
        from schemas import MaliciousDetectionOutput

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
