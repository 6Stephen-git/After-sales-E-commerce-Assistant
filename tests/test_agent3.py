"""
Agent3（话术生成员）模块测试
覆盖：3 个典型场景 + 1 个边界场景
"""

import os
import sys


# ---------- 与仓库根对齐的导入路径 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


from backend.agents.agent3.script_generator import generate
from backend.tools.agent3_tools import get_script_template
from schemas import (
    SCRIPT_COMPENSATE,
    SCRIPT_DEFENSE,
    SCRIPT_NEGOTIATE,
    FactOutput,
    ScriptInput,
    StrategyOutput,
    STRATEGY_COMPENSATE,
    STRATEGY_DEFEND,
    STRATEGY_NEGOTIATE,
)


# ---------- generate：三策略推荐 + 缺字段边界 ----------
class TestAgent3Generate:
    def test_generate_should_recommend_defense_version(self):
        """抗辩策略场景：推荐抗辩版并生成三版话术。"""
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                strategy=STRATEGY_DEFEND,
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

    def test_generate_should_recommend_negotiate_version(self):
        """协商策略场景：推荐协商版并保持真人口吻。"""
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                strategy=STRATEGY_NEGOTIATE,
                reasoning="色差争议证据中等，协商可减少升级概率",
                estimated_win_rate=0.67,
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
        assert "咱们可以先按88.50元协商处理" in output.negotiate_version
        assert "您看这样安排是否可以" in output.negotiate_version
        assert output.usage_tip and "协商版" in output.usage_tip

    def test_generate_should_recommend_compensate_version(self):
        """认赔策略场景：推荐认赔版并包含事实描述。"""
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                strategy=STRATEGY_COMPENSATE,
                reasoning="高质量瑕疵证据已形成闭环，认赔更稳妥",
                estimated_win_rate=0.8,
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
        assert "156.00元" in output.compensate_version
        assert output.usage_tip and "认赔版" in output.usage_tip

    def test_generate_boundary_should_still_output_non_empty_scripts(self):
        """边界场景：字段缺失时依然生成可用三版话术。"""
        input_data = ScriptInput(
            strategy_output=StrategyOutput(
                strategy=STRATEGY_NEGOTIATE,
                reasoning="",
                estimated_win_rate=0.5,
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


# ---------- 模板工具：三键模板均含占位符 ----------
class TestAgent3Tools:
    def test_get_script_template_returns_content_for_all_types(self):
        """工具应返回三种策略模板。"""
        assert "{{order_id}}" in get_script_template("defend")
        assert "{{order_id}}" in get_script_template("negotiate")
        assert "{{order_id}}" in get_script_template("compensate")
