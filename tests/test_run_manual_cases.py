"""run_manual_cases 的无图事实覆盖与测试阈值行为测试。"""

from run_manual_cases import _build_fact_output_with_overlay, _parse_buyer_profile, _patch_test_overrides
import backend.tools.agent2_tools as agent2_tools_module
from schemas import FactOutput, RuleMatchPlan


def test_replace_mode_preserves_rule_match_plan() -> None:
    """事实字段可完全覆盖，但规则导航不能因 replace 模式丢失。"""

    def _extract(_materials: dict) -> FactOutput:
        return FactOutput(
            issue_summary="LLM 文本抽取结果",
            uncertainty_note="不应混入覆盖事实",
            rule_match_plan=RuleMatchPlan(target_doc_ids=["服务保障_七天无理由"]),
        )

    facts = _build_fact_output_with_overlay(
        {"buyer_text": "七天无理由退货"},
        visual_preset=None,
        facts_overlay={
            "issue_summary": "买家主张七天无理由退货，争议点是商品完好。",
            "defect_type": "无瑕疵",
        },
        agent1_mode="replace",
        original_extract=_extract,
    )

    assert facts.issue_summary == "买家主张七天无理由退货，争议点是商品完好。"
    assert facts.uncertainty_note is None
    assert facts.rule_match_plan.target_doc_ids == ["服务保障_七天无理由"]


def test_customer_lifetime_value_overrides_avg_order_value() -> None:
    """情景写明累计消费时，测试画像应按累计消费反推客单价。"""
    profile = _parse_buyer_profile(
        {"buyer_id": "buyer-1", "purchase_count": 5, "avg_order_value": 30},
        {"customer_lifetime_value": 150},
    )

    assert profile.avg_order_value == 30


def test_channel_threshold_overrides_customer_value_threshold() -> None:
    """情景 channel_threshold 应映射为累计消费触发阈值。"""
    original = agent2_tools_module.LONG_TERM_VALUE_AMOUNT_THRESHOLD
    with _patch_test_overrides(
        case_id="CASE-CV",
        test_overrides={"channel_threshold": 40},
        malicious_context=None,
    ):
        assert agent2_tools_module.LONG_TERM_VALUE_AMOUNT_THRESHOLD == 40
    assert agent2_tools_module.LONG_TERM_VALUE_AMOUNT_THRESHOLD == original
