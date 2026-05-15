"""
Agent2（策略参谋员）模块测试
覆盖：3 个典型场景 + 1 个边界场景
"""

import os
import sys


# ---------- 与仓库根对齐的导入路径 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


from backend.agents.agent2.strategist import recommend
import backend.agents.agent2.strategist as strategist_module
from backend.tools.agent2_tools import evaluate_customer_value, match_rules, query_buyer_profile, search_similar_cases
from schemas import (
    BuyerProfile,
    CustomerValueInput,
    FactOutput,
    MatchedRule,
    StrategyInput,
    STRATEGY_COMPENSATE,
    STRATEGY_DEFEND,
    STRATEGY_NEGOTIATE,
)


# ---------- recommend：三典型策略 + 无规则无判例边界 ----------
class TestAgent2Recommend:
    def _mock_customer_value_fields(self, monkeypatch):
        """屏蔽客户价值字段推断的外部依赖，保证单测稳定。"""
        monkeypatch.setattr(
            strategist_module,
            "_llm_infer_customer_value_fields",
            lambda _input: {
                "defect_severity": "moderate",
                "goods_recoverability": "repairable",
                "buyer_cooperation": "neutral",
                "demand_reasonableness": "borderline",
            },
        )

    def test_recommend_compensate_when_high_quality_defect(self, monkeypatch):
        """高质量瑕疵证据，倾向善后策略。"""
        self._mock_customer_value_fields(monkeypatch)
        facts = FactOutput(
            goods_received=True,
            defect_type="破洞",
            evidence_quality="high",
            logistics_normal=True,
        )
        matched_rules = [
            MatchedRule(
                rule_id="R002",
                rule_summary="买家提供清晰瑕疵图片，平台倾向支持退款",
                condition_result="规则条件全部满足；建议策略:compensate",
            )
        ]
        profile = BuyerProfile(buyer_id="buyer_loyal", dispute_rate=0.06, credit_level="high")
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=matched_rules,
            similar_cases=search_similar_cases("破洞退款纠纷", top_k=3),
            order_amount=128.0,
        )

        output = recommend(input_data)
        assert output.strategy == STRATEGY_COMPENSATE, f"期望 compensate，实际 {output.strategy}"
        assert 0.0 <= output.estimated_win_rate <= 1.0
        assert output.policy_ref and "R002" in output.policy_ref

    def test_recommend_defend_when_low_evidence_and_high_risk_buyer(self, monkeypatch):
        """证据不足且买家风险高，倾向抗辩。"""
        self._mock_customer_value_fields(monkeypatch)
        facts = FactOutput(
            goods_received=True,
            defect_type="无瑕疵",
            has_tag_visible=True,
            evidence_quality="low",
            missing_evidence=["商品照片"],
            red_flags=["疑似二次损坏"],
        )
        matched_rules = [
            MatchedRule(
                rule_id="R004",
                rule_summary="证据不足，商家可申诉补证",
                condition_result="规则条件全部满足；建议策略:defend",
            )
        ]
        profile = BuyerProfile(
            buyer_id="buyer_high_risk",
            dispute_rate=0.55,
            return_rate=0.48,
            malicious_flags=2,
        )
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=matched_rules,
            similar_cases=search_similar_cases("吊牌完整但要求退款", top_k=2),
            order_amount=99.0,
        )

        output = recommend(input_data)
        assert output.strategy == STRATEGY_DEFEND, f"期望 defend，实际 {output.strategy}"
        assert output.risk_factors, "期望输出风险因素列表"

    def test_recommend_negotiate_for_medium_evidence_color_diff(self, monkeypatch):
        """色差且证据中等，倾向协商。"""
        self._mock_customer_value_fields(monkeypatch)
        facts = FactOutput(
            goods_received=True,
            defect_type="色差",
            evidence_quality="medium",
            logistics_normal=True,
        )
        matched_rules = [
            MatchedRule(
                rule_id="R007",
                rule_summary="色差类纠纷证据中等，建议协商",
                condition_result="规则条件全部满足；建议策略:negotiate",
            )
        ]
        profile = BuyerProfile(buyer_id="buyer_mid", dispute_rate=0.12, credit_level="medium")
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=matched_rules,
            similar_cases=search_similar_cases("色差纠纷希望退部分款", top_k=3),
            order_amount=168.0,
        )

        output = recommend(input_data)
        assert output.strategy == STRATEGY_NEGOTIATE, f"期望 negotiate，实际 {output.strategy}"
        assert "客户意图：" in output.reasoning
        assert "风险点：" in output.reasoning
        assert "建议动作：" in output.reasoning

    def test_recommend_boundary_with_empty_rules_and_cases(self, monkeypatch):
        """边界场景：无规则无判例时仍应输出合法策略。"""
        self._mock_customer_value_fields(monkeypatch)
        facts = FactOutput(evidence_quality="medium")
        profile = BuyerProfile(buyer_id="buyer_unknown")
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=[],
            similar_cases=[],
            order_amount=0.0,
        )

        output = recommend(input_data)
        assert output.strategy in {STRATEGY_DEFEND, STRATEGY_NEGOTIATE, STRATEGY_COMPENSATE}
        assert 0.0 <= output.confidence <= 1.0
        assert output.customer_value is not None

    def test_recommend_should_use_fallback_reasoning_when_llm_unavailable(self, monkeypatch):
        """LLM不可用时，reasoning 仍应保持三段结构。"""
        self._mock_customer_value_fields(monkeypatch)
        monkeypatch.setattr(strategist_module, "_llm_generate_reasoning", lambda **kwargs: None)
        facts = FactOutput(evidence_quality="medium", missing_evidence=["缺少清晰图片"])
        profile = BuyerProfile(buyer_id="buyer_fallback")
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=[],
            similar_cases=[],
            order_amount=120.0,
        )
        output = recommend(input_data)
        assert "客户意图：" in output.reasoning
        assert "风险点：" in output.reasoning
        assert "建议动作：" in output.reasoning

    def test_recommend_should_accept_llm_reasoning_when_format_valid(self, monkeypatch):
        """LLM输出三段结构时应直接采用。"""
        self._mock_customer_value_fields(monkeypatch)
        monkeypatch.setattr(
            strategist_module,
            "_llm_generate_reasoning",
            lambda **kwargs: "客户意图：质量问题维权。\n风险点：证据链仍需补强。\n建议动作：先补证再提交平台申诉。",
        )
        facts = FactOutput(evidence_quality="high", defect_type="破洞")
        profile = BuyerProfile(buyer_id="buyer_llm")
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=[],
            similar_cases=[],
            order_amount=220.0,
        )
        output = recommend(input_data)
        assert output.reasoning.startswith("客户意图：")


# ---------- 工具层：规则命中、画像默认、判例 top_k 截断 ----------
class TestAgent2Tools:
    def test_match_rules_should_hit_known_rule(self):
        """规则匹配应至少命中一条已知规则。"""
        facts = FactOutput(
            goods_received=True,
            defect_type="破洞",
            evidence_quality="high",
            logistics_normal=True,
        )
        matched = match_rules(facts)
        assert matched, "期望至少命中一条规则"
        assert any(rule.rule_id == "R002" for rule in matched), "期望命中 R002"

    def test_query_buyer_profile_should_return_default_when_unknown(self):
        """未知买家 ID 返回默认画像。"""
        profile = query_buyer_profile("unknown_buyer")
        assert profile.buyer_id == "unknown_buyer"
        assert profile.purchase_count >= 0

    def test_search_similar_cases_top_k(self):
        """判例检索应按 top_k 截断结果。"""
        cases = search_similar_cases("物流异常退款", top_k=2)
        assert len(cases) == 2
        assert cases[0].similarity >= cases[1].similarity

    def test_evaluate_customer_value_should_trigger_long_term_channel(self):
        """客户长期价值高时应触发长期优待通道。"""
        profile = BuyerProfile(
            buyer_id="buyer_loyal",
            purchase_count=20,
            dispute_rate=0.02,
            avg_order_value=180.0,
            positive_review_count=6,
        )
        input_data = CustomerValueInput(
            buyer_profile=profile,
            order_amount=120.0,
            defect_severity="minor",
            goods_recoverability="resalable",
            buyer_cooperation="good",
            demand_reasonableness="reasonable",
        )
        result = evaluate_customer_value(input_data)
        assert result.long_term_triggered is True
        assert result.channel == "long_term"
        assert result.compensation_uplift == "+10%~20%"

    def test_evaluate_customer_value_should_score_recoverability_as_higher_when_worse(self):
        """商品越不可挽回，本单得分应越高。"""
        profile = BuyerProfile(buyer_id="buyer_case")
        low_loss = evaluate_customer_value(
            CustomerValueInput(
                buyer_profile=profile,
                order_amount=260.0,
                defect_severity="moderate",
                goods_recoverability="resalable",
                buyer_cooperation="neutral",
                demand_reasonableness="borderline",
            )
        )
        high_loss = evaluate_customer_value(
            CustomerValueInput(
                buyer_profile=profile,
                order_amount=260.0,
                defect_severity="moderate",
                goods_recoverability="unrecoverable",
                buyer_cooperation="neutral",
                demand_reasonableness="borderline",
            )
        )
        assert high_loss.order_score > low_loss.order_score
