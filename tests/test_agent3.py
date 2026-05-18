"""
Agent3（话术生成员）模块测试
覆盖：3 个典型场景 + 1 个边界场景 + LLM 不可用回退 + 短句压缩

说明：场景 1-3 隔离 LLM 依赖，使用确定性 mock 输出，避免真实调用导致断言不稳定。
     模板兜底测试显式置空 LLM 返回，验证模板路径。
"""

import os
import sys


# ---------- 与仓库根对齐的导入路径 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


from backend.agents.agent3.script_generator import generate
import backend.agents.agent3.script_generator as script_generator_module
from backend.tools.agent3_tools import get_script_template
from schemas import (
    SCRIPT_COMPENSATE,
    SCRIPT_DEFENSE,
    SCRIPT_NEGOTIATE,
    FactOutput,
    ScriptInput,
    StrategyOutput,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
)


# ---------- 确定性 LLM 话术 mock：隔离外部不稳定依赖 ----------
def _mock_llm_scripts_for_disposition(disposition: str, fact_summary: str, order_id: str, offer_amount: str):
    """
    根据 disposition 返回包含 order_id 与关键事实的确定性话术 JSON，
    保证断言可稳定命中，不依赖真实 LLM 输出。
    """
    templates = {
        DISPOSITION_DEFEND: (
            f"您好，订单{order_id}已核实，争议点：{fact_summary}。"
            f"金额{offer_amount}元，我们建议先走平台复核流程，补齐证据链后再提交抗辩材料。"
        ),
        DISPOSITION_NEGOTIATE: (
            f"您好，订单{order_id}问题已收到，{fact_summary}。"
            f"金额{offer_amount}元，我们可以提供退款或换货方案，您看哪个方式更方便？"
        ),
        DISPOSITION_COMPENSATE: (
            f"您好，确认订单{order_id}存在{fact_summary}，非常抱歉。"
            f"金额{offer_amount}元，我们可按流程为您办理补偿退款。"
        ),
    }
    return {
        "defense_version": templates[DISPOSITION_DEFEND],
        "negotiate_version": templates[DISPOSITION_NEGOTIATE],
        "compensate_version": templates[DISPOSITION_COMPENSATE],
    }


def _make_mock_llm_generate(disposition: str):
    """
    构造绑定 disposition 的 _llm_generate_scripts 替身，
    从 variables 中提取 order_id/offer_amount/fact_summary 生成确定性输出。
    """
    def _mock(*, input_data, variables, tone_profile, fast_path=False):
        return _mock_llm_scripts_for_disposition(
            disposition=disposition,
            fact_summary=variables.get("fact_summary", ""),
            order_id=variables.get("order_id", ""),
            offer_amount=variables.get("offer_amount", "0.00"),
        )
    return _mock


# ---------- generate：单链路处置方向 + 胜率/置信度 ----------
class TestAgent3Generate:
    def test_generate_should_recommend_defense_version(self, monkeypatch):
        """抗辩策略场景：推荐抗辩版并生成三版话术。"""
        monkeypatch.setattr(script_generator_module, "_llm_generate_scripts", _make_mock_llm_generate(DISPOSITION_DEFEND))
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_DEFEND,
                reasoning="证据链存在疑点，优先要求补证并坚持事实边界",
                estimated_win_rate=0.63,
                confidence=0.72,
            ),
            facts=FactOutput(
                goods_received=True,
                defect_type="无瑕疵",
                defect_location="衣领",
                logistics_normal=True,
                red_flags=["疑似二次损坏"],
            ),
            order_id="ORDER-A3-001",
            order_amount=129.0,
        )

        output = generate(input_data)
        assert output.recommended_version == SCRIPT_DEFENSE
        assert output.defense_version and output.negotiate_version and output.compensate_version
        assert "ORDER-A3-001" in output.defense_version
        assert "无瑕疵" in output.defense_version
        assert output.usage_tip and "抗辩版" in output.usage_tip

    def test_generate_should_recommend_negotiate_version(self, monkeypatch):
        """协商策略场景：推荐协商版并保持真人口吻。"""
        monkeypatch.setattr(script_generator_module, "_llm_generate_scripts", _make_mock_llm_generate(DISPOSITION_NEGOTIATE))
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                reasoning="色差争议证据中等，协商可减少升级概率",
                estimated_win_rate=None,
                confidence=0.7,
            ),
            facts=FactOutput(
                goods_received=True,
                defect_type="色差",
                defect_location="前胸",
                logistics_normal=True,
            ),
            order_id="ORDER-A3-002",
            order_amount=88.5,
            emotion_note="买家语气急，希望尽快解决",
        )

        output = generate(input_data)
        assert output.recommended_version == SCRIPT_NEGOTIATE
        assert "ORDER-A3-002" in output.negotiate_version
        assert output.negotiate_version.strip() != ""
        assert output.usage_tip and "协商版" in output.usage_tip

    def test_generate_should_recommend_compensate_version(self, monkeypatch):
        """善后策略场景：推荐善后版并包含事实描述。"""
        monkeypatch.setattr(script_generator_module, "_llm_generate_scripts", _make_mock_llm_generate(DISPOSITION_COMPENSATE))
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_COMPENSATE,
                reasoning="高质量瑕疵证据已形成闭环，主动善后更稳妥",
                estimated_win_rate=None,
                confidence=0.81,
            ),
            facts=FactOutput(
                goods_received=True,
                defect_type="破洞",
                defect_location="袖口",
                logistics_normal=True,
            ),
            order_id="ORDER-A3-003",
            order_amount=156.0,
        )

        output = generate(input_data)
        assert output.recommended_version == SCRIPT_COMPENSATE
        assert "破洞" in output.compensate_version
        assert "156.00" in output.compensate_version
        assert output.usage_tip and "善后版" in output.usage_tip

    def test_generate_boundary_should_still_output_non_empty_scripts(self, monkeypatch):
        """边界场景：字段缺失时依然生成可用三版话术。"""
        monkeypatch.setattr(script_generator_module, "_llm_generate_scripts", lambda **kwargs: None)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                disposition=DISPOSITION_NEGOTIATE,
                reasoning="",
                estimated_win_rate=None,
                confidence=0.4,
            ),
            facts=FactOutput(),
            order_id="",
            order_amount=0.0,
            emotion_note=None,
        )

        output = generate(input_data)
        assert output.recommended_version in {SCRIPT_DEFENSE, SCRIPT_NEGOTIATE, SCRIPT_COMPENSATE}
        assert output.defense_version.strip() != ""
        assert output.negotiate_version.strip() != ""
        assert output.compensate_version.strip() != ""
        assert output.usage_tip is not None

    def test_generate_should_fallback_to_template_when_llm_unavailable(self, monkeypatch):
        """LLM 不可用时继续走模板兜底。"""
        monkeypatch.setattr(script_generator_module, "_llm_generate_scripts", lambda **kwargs: None)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(disposition=DISPOSITION_NEGOTIATE, reasoning="协商优先"),
            facts=FactOutput(defect_type="色差", defect_location="衣领"),
            order_id="ORDER-A3-010",
            order_amount=66.0,
        )
        output = generate(input_data)
        assert "ORDER-A3-010" in output.negotiate_version
        assert output.negotiate_version.strip() != ""

    def test_generate_should_compress_script_in_short_mode(self, monkeypatch):
        """情绪备注触发短句模式时，话术长度应受控。"""
        monkeypatch.setattr(script_generator_module, "_llm_generate_scripts", lambda **kwargs: None)
        input_data = ScriptInput(
            strategy_output=StrategyOutput(disposition=DISPOSITION_NEGOTIATE, reasoning="协商优先"),
            facts=FactOutput(defect_type="色差", missing_evidence=["缺少近景图"]),
            order_id="ORDER-A3-011",
            order_amount=88.0,
            emotion_note="买家一直催，要求马上处理",
        )
        output = generate(input_data)
        assert len(output.negotiate_version) <= 40


# ---------- 模板工具：三键模板均含占位符 ----------
class TestAgent3Tools:
    def test_get_script_template_returns_content_for_all_types(self):
        """工具应返回三种策略模板。"""
        assert "{{order_id}}" in get_script_template("defend")
        assert "{{order_id}}" in get_script_template("negotiate")
        assert "{{order_id}}" in get_script_template("compensate")
