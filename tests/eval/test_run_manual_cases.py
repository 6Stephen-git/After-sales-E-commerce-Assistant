"""run_manual_cases 的无图事实覆盖与测试阈值行为测试。"""

from eval.pipeline.run_manual_cases import (
    _build_fact_output_with_overlay,
    _parse_buyer_profile,
    _patch_facts_override,
    _patch_test_overrides,
)
import backend.agents.agent1 as agent1_module
import backend.tools.agent2_tools as agent2_tools_module
from schemas import FactOutput, RuleMatchPlan


def test_replace_mode_recomputes_decision_readiness() -> None:
    """replace 模式注入高证据事实后，可决策度应重算而非默认 low。"""
    facts = _build_fact_output_with_overlay(
        {"buyer_text": "仅退款"},
        visual_preset="high_evidence",
        facts_overlay={
            "visual_observations": ["外包装完好", "轻微线头"],
            "visual_defect_severity": "minor",
            "defect_type": "开线",
            "missing_evidence": [],
            "evidence_quality": "high",
        },
        agent1_mode="replace",
        original_extract=lambda _m: FactOutput(issue_summary="x"),
    )
    assert facts.evidence_quality == "high"
    assert facts.decision_readiness in {"medium", "high"}


def test_patch_facts_override_patches_dispute_batch() -> None:
    """dispute_batch 须与 agent1 同步 patch，否则评测注入不生效。"""
    import backend.pipeline.dispute_batch as dispute_batch_module

    case = {
        "test_overrides": {"visual_preset": "high_evidence", "agent1_mode": "replace"},
        "facts_override": {"evidence_quality": "high", "missing_evidence": []},
    }
    with _patch_facts_override(case_id="T-PATCH", case=case):
        assert dispute_batch_module.extract is agent1_module.extract


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


def test_patch_test_overrides_patches_dispute_batch_detect() -> None:
    """恶意检测 patch 须同步 dispute_batch，否则 test_overrides 不生效。"""
    import backend.pipeline.dispute_batch as dispute_batch_module

    original = agent2_tools_module.detect_malicious_behavior
    with _patch_test_overrides(
        case_id="T-DETECT",
        test_overrides={"malicious_hard_rules": {"refund_only_count_threshold": 2}},
        malicious_context={"recent_refund_only_count": 4},
    ):
        assert agent2_tools_module.detect_malicious_behavior is not original
        assert dispute_batch_module.detect_malicious_behavior is agent2_tools_module.detect_malicious_behavior


def test_channel_threshold_overrides_order_value_score_threshold() -> None:
    """情景 channel_threshold 兼容键应映射为双维评分触发阈值，不再用累计金额单独开老客通道。"""
    original = agent2_tools_module.ORDER_VALUE_SCORE_THRESHOLD
    with _patch_test_overrides(
        case_id="CASE-CV",
        test_overrides={"channel_threshold": 40},
        malicious_context=None,
    ):
        assert agent2_tools_module.ORDER_VALUE_SCORE_THRESHOLD == 40
    assert agent2_tools_module.ORDER_VALUE_SCORE_THRESHOLD == original
