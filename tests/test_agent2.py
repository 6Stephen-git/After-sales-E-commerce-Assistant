"""
Agent2 / agent2_tools 核心契约测试。

原则：保留典型场景 + 边界；单次 bug 修复验证后不单开用例堆积。
业务回归以 eval.pipeline.scenario_gen + Judge 为准。
"""

import os
import sys

import pytest


# ---------- 与仓库根对齐的导入路径 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

TEST_DB_PATH = os.path.join(ROOT_DIR, "tests", "tmp_agent2.sqlite3")
if os.path.exists(TEST_DB_PATH):
    try:
        os.remove(TEST_DB_PATH)
    except OSError:
        pass
os.environ["DB_URL"] = f"sqlite+pysqlite:///{TEST_DB_PATH.replace(os.sep, '/')}"

from backend.db import init_db

init_db()


from backend.agents.agent2.strategist import recommend
import backend.agents.agent2.strategist as strategist_module
import backend.tools.agent2_tools as agent2_tools_module
from backend.tools.agent2_tools import (
    detect_malicious_behavior,
    evaluate_customer_value,
    query_buyer_profile,
    run_customer_value_analysis,
    search_similar_cases,
)
from backend.tools.rule_matcher import match_rules_from_facts
from schemas import (
    BuyerProfile,
    ChatTurn,
    CustomerValueInput,
    CustomerValueOutput,
    FactOutput,
    MaliciousDetectionOutput,
    MaliciousSignal,
    MaliciousDetectionInput,
    MatchedRule,
    RuleConstraint,
    RuleBrief,
    StrategyInput,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    RULE_CONSTRAINT_APPLIES,
    RULE_CONSTRAINT_EVIDENCE,
    RULE_CONSTRAINT_OTHER,
    RULE_CONSTRAINT_MISSING_FACT,
    RULE_CONSTRAINT_RATIO_LIMIT,
    RULE_CONSTRAINT_TIMING,
    RULE_RELEVANCE_MUST,
)


# ---------- 测试辅助：与 Controller 一致注入 precomputed 层 ----------
def _with_precomputed(
    input_data: StrategyInput,
    *,
    malicious_detection: MaliciousDetectionOutput | None = None,
    customer_value: CustomerValueOutput | None = None,
) -> StrategyInput:
    """
    为 recommend() 补齐 Controller 预计算的恶意/价值结果。
    """
    malicious_input = MaliciousDetectionInput(
        buyer_profile=input_data.buyer_profile,
        facts=input_data.facts,
        order_amount=input_data.order_amount,
        chat_history=input_data.chat_history or [],
        emotion_note=input_data.emotion_note,
    )
    resolved_malicious = (
        malicious_detection
        if malicious_detection is not None
        else detect_malicious_behavior(malicious_input)
    )
    resolved_value = (
        customer_value
        if customer_value is not None
        else run_customer_value_analysis(input_data)
    )
    return input_data.model_copy(
        update={
            "precomputed_malicious_detection": resolved_malicious,
            "precomputed_customer_value": resolved_value,
        }
    )


# ---------- recommend：单链路处置方向 + 胜率/置信度 ----------
class TestAgent2Recommend:
    def _mock_strategy_llm(self, monkeypatch):
        """屏蔽策略 LLM 外部依赖，用上下文推断 responsibility。"""

        def _ctx_aware_llm(*, disposition, input_data, risk_factors, estimated_win_rate,
                           strategy_stage, action_contract, reasoning_delta_callback=None):
            """根据输入上下文推断合理 responsibility，模拟 LLM 决策。"""
            facts = input_data.facts
            evidence = (facts.evidence_quality or "").strip().lower()
            defect = (facts.defect_type or "").strip()
            red_flags = [str(f).strip() for f in (facts.red_flags or []) if str(f).strip()]
            credential = (facts.credential_trust or "").strip().lower()
            # 规则站位
            rule_responsibility = "unclear"
            stance_map = {}
            for rule in (input_data.matched_rules or []):
                h = (rule.stance_hint or "").strip().lower()
                stance_map[h] = stance_map.get(h, 0) + 1
            if stance_map.get("buyer", 0) > stance_map.get("merchant", 0):
                rule_responsibility = "merchant_fault"
            elif stance_map.get("merchant", 0) > stance_map.get("buyer", 0):
                rule_responsibility = "buyer_fault"

            resp = "unclear"
            conf = 0.4
            if evidence == "high" and defect not in {"", "无", "无瑕疵"}:
                if credential != "suspect":
                    resp = "merchant_fault"
                    conf = 0.8
                else:
                    resp = "unclear"
                    conf = 0.5
            elif evidence == "low" and red_flags:
                resp = "unclear"
                conf = 0.5
            elif evidence == "medium":
                resp = rule_responsibility if rule_responsibility != "unclear" else "unclear"
                conf = 0.5
            elif rule_responsibility != "unclear":
                resp = rule_responsibility
                conf = 0.6

            return {
                "responsibility": resp,
                "responsibility_confidence": conf,
                "responsibility_rationale": f"基于证据质量({evidence})和缺陷类型({defect})推断责任。",
                "customer_intent_analysis": "买家意图分析。",
                "strategy_direction_summary": "建议当前动作。",
                "strategy_direction_rationale": "推理理由。",
                "platform_rule_basis": [],
                "risk_factors": [],
                "dialogue_context": {
                    "dialogue_mode": "continue",
                    "blocked_evidence_requests": [],
                    "actionable_evidence_requests": [],
                    "fallback_script": "我这边还在核对材料，核实完马上回您。",
                },
            }

        monkeypatch.setattr(strategist_module, "_llm_generate_strategy", _ctx_aware_llm)

    def test_recommend_compensate_when_high_quality_defect(self, monkeypatch):
        """高质量瑕疵证据，倾向善后策略。"""
        self._mock_strategy_llm(monkeypatch)
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

        output = recommend(_with_precomputed(input_data))
        assert output.disposition == DISPOSITION_COMPENSATE, f"期望 compensate，实际 {output.disposition}"
        assert output.estimated_win_rate is None
        assert 0.6 <= output.confidence <= 0.95
        assert output.policy_ref and "R002" in output.policy_ref
        assert output.platform_rule_basis
        assert "退款" in output.platform_rule_basis[0]

    def test_recommend_defend_when_low_evidence_and_high_risk_buyer(self, monkeypatch):
        """证据不足且买家风险高，倾向抗辩。"""
        self._mock_strategy_llm(monkeypatch)
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

        output = recommend(_with_precomputed(input_data))
        assert output.disposition == DISPOSITION_DEFEND, f"期望 defend，实际 {output.disposition}"
        assert output.estimated_win_rate is not None
        assert 0.05 <= output.estimated_win_rate <= 0.95
        assert output.risk_factors, "期望输出风险因素列表"

    def test_recommend_negotiate_when_medium_evidence_but_missing_key_proof(self, monkeypatch):
        """有图有文但关键举证未齐（如划痕缺开箱视频）：处置仍为协商，当下动作由补证阶段约束。"""
        self._mock_strategy_llm(monkeypatch)
        facts = FactOutput(
            goods_received=True,
            defect_type="划痕",
            evidence_quality="medium",
            issue_summary="手机拆开就有划痕，要求退款",
            intent_tags=["质量问题", "退款诉求"],
            missing_evidence=["完整连续开箱视频"],
            visual_observations=["屏幕反光较强，暂无法从现有图片确认明显划痕"],
            evidence_items=[{"type": "text"}, {"type": "image", "url": "http://example.com/p1.jpg"}],
        )
        profile = BuyerProfile(buyer_id="buyer_scratch", dispute_rate=0.08)
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=[],
            similar_cases=[],
            order_amount=2999.0,
        )
        output = recommend(_with_precomputed(input_data))
        assert output.disposition == DISPOSITION_NEGOTIATE
        assert any("策略阶段" in item for item in output.risk_factors)
        assert "补证" in output.strategy_direction_summary or "举证" in output.strategy_direction_summary

    def test_service_return_frame_strategy_contract(self, monkeypatch):
        """服务退货框架（只读 primary_dispute_frame）：不进质量补证阶段，动作为讲规则/验收。"""
        self._mock_strategy_llm(monkeypatch)

        missing_proof_facts = FactOutput(
            issue_summary="买家主张适用无理由退货；争议焦点为批量试穿后是否仍满足商品完好。",
            intent_tags=["退款诉求", "商品完好争议", "批量试穿"],
            goods_received=True,
            defect_type="无",
            evidence_quality="medium",
            missing_evidence=["商品完好验收照片"],
            primary_dispute_frame="seven_day_return",
        )
        missing_proof_input = StrategyInput(
            facts=missing_proof_facts,
            buyer_profile=BuyerProfile(buyer_id="buyer_new", purchase_count=1, dispute_rate=0),
            order_amount=2400.0,
            chat_history=["不喜欢颜色，麻烦按无理由退货处理。"],
            chat_turns=[ChatTurn(role="buyer", content="不喜欢颜色，麻烦按无理由退货处理。")],
        )
        missing_proof_out = recommend(_with_precomputed(missing_proof_input))
        assert missing_proof_out.strategy_stage != "evidence_first"

        facts = FactOutput(
            issue_summary="买家主张七天无理由应直接退货退款，商家关注30件批量试穿后是否仍完好。",
            intent_tags=["七天无理由退货", "退款诉求", "商品完好争议", "批量试穿"],
            goods_received=True,
            defect_type="无",
            evidence_quality="medium",
            missing_evidence=[],
            primary_dispute_frame="seven_day_return",
        )
        rule = MatchedRule(
            rule_id="return::intact",
            rule_summary="七天无理由退货以商品完好、不影响二次销售为前提，退回后商家可按规则验收。",
            condition_result="命中服务规则边界",
            relevance=RULE_RELEVANCE_MUST,
            stance_hint="neutral",
        )
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=BuyerProfile(buyer_id="buyer_7day", purchase_count=1),
            matched_rules=[rule],
            rule_briefs=[
                RuleBrief(
                    article_ref="七天无理由规则",
                    brief="七天无理由退货成立前提是商品完好且不影响二次销售，退回商品需验收。",
                    relevance=RULE_RELEVANCE_MUST,
                    stance_hint="neutral",
                )
            ],
            order_amount=2400.0,
            chat_turns=[ChatTurn(role="buyer", content="我买了七天无理由，为什么不能直接退？")],
        )
        buyer_stance_rule = MatchedRule(
            rule_id="apparel::quality",
            rule_summary="买家举证有效证明商品存在质量问题时，平台倾向支持退货退款。",
            condition_result="建议策略:compensate",
            relevance=RULE_RELEVANCE_MUST,
            stance_hint="buyer",
        )
        buyer_stance_input = input_data.model_copy(
            update={
                "matched_rules": [rule, buyer_stance_rule],
                "facts": facts.model_copy(update={"evidence_quality": "high"}),
            }
        )
        output = recommend(_with_precomputed(input_data))
        buyer_stance_out = recommend(_with_precomputed(buyer_stance_input))

        assert output.action_type == "rule_explain"
        assert output.compensation_policy == "none"
        assert buyer_stance_out.action_type == "rule_explain"
        assert buyer_stance_out.compensation_policy == "none"
        assert any("完好" in item for item in output.rule_constraints)
        assert any("验收" in item for item in output.rule_constraints)
        constraints_text = "；".join(output.rule_constraints)
        assert "影响二次销售" in constraints_text
        assert "使用痕迹" in constraints_text
        assert "不予退款" in constraints_text or "无法退款" in constraints_text
        assert "运费" in constraints_text
        assert "金额" not in output.strategy_direction_summary
        assert "补偿" not in output.strategy_direction_summary
        assert "验收" in output.strategy_direction_summary or "规则" in output.strategy_direction_summary
        assert "金额" not in output.next_step

    def test_clear_merchant_fault_should_output_remedy_contract(self, monkeypatch):
        """商责明确时应进入善后动作契约，允许明确处理方案。"""
        self._mock_strategy_llm(monkeypatch)
        facts = FactOutput(
            issue_summary="买家收到商品破损，已提供清晰图片，要求退款。",
            intent_tags=["质量问题", "退款诉求"],
            goods_received=True,
            defect_type="破损",
            evidence_quality="high",
            missing_evidence=[],
        )
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=BuyerProfile(buyer_id="buyer_fault"),
            matched_rules=[
                MatchedRule(
                    rule_id="quality::refund",
                    rule_summary="买家举证有效证明商品存在质量问题时，平台倾向支持退货退款。",
                    condition_result="规则条件全部满足；建议策略:compensate",
                    relevance=RULE_RELEVANCE_MUST,
                    stance_hint="buyer",
                )
            ],
            order_amount=128.0,
        )

        output = recommend(_with_precomputed(input_data))

        assert output.action_type == "merchant_remedy"
        assert output.compensation_policy == "explicit_amount"
        assert "退" in output.next_step or "补" in output.next_step

    def test_recommend_negotiate_for_medium_evidence_color_diff(self, monkeypatch):
        """色差且证据中等，倾向协商。"""
        self._mock_strategy_llm(monkeypatch)
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

        output = recommend(_with_precomputed(input_data))
        assert output.disposition == DISPOSITION_NEGOTIATE, f"期望 negotiate，实际 {output.disposition}"
        assert output.estimated_win_rate is None
        assert 0.1 <= output.confidence <= 0.95
        assert "客户意图：" in output.reasoning
        assert "风险点：" in output.reasoning
        assert "建议动作：" in output.reasoning
        assert "推理理由：" in output.reasoning
        assert output.strategy_direction_summary

    def test_recommend_negotiate_when_medium_risk_conflicts_with_merchant_fault(self, monkeypatch):
        """中风险恶意与商责明确信号冲突时，应协商且降低置信度。"""
        self._mock_strategy_llm(monkeypatch)
        facts = FactOutput(goods_received=True, defect_type="破洞", evidence_quality="high")
        matched_rules = [
            MatchedRule(
                rule_id="R002",
                rule_summary="买家提供清晰瑕疵图片，平台倾向支持买家退款",
                condition_result="规则条件全部满足；建议策略:compensate",
            )
        ]
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=BuyerProfile(buyer_id="buyer_conflict"),
            matched_rules=matched_rules,
            similar_cases=[],
            order_amount=128.0,
        )
        medium_malicious = MaliciousDetectionOutput(
            risk_score=42,
            risk_level="medium",
            triggered_signals=[],
            hard_rule_summary="存在中风险信号。",
            disposition_advice="建议谨慎协商并加强举证要求，控制补偿上限。",
        )
        output = recommend(_with_precomputed(input_data, malicious_detection=medium_malicious))
        assert output.disposition == DISPOSITION_NEGOTIATE
        assert output.estimated_win_rate is None
        assert output.confidence <= 0.65
        assert any("信号一致性" in item for item in output.risk_factors)
        assert "信号一致性" in output.reasoning or "冲突" in output.reasoning

    def test_recommend_boundary_with_empty_rules_and_cases(self, monkeypatch):
        """边界场景：无规则无判例时仍应输出合法策略。"""
        self._mock_strategy_llm(monkeypatch)
        facts = FactOutput(evidence_quality="medium")
        profile = BuyerProfile(buyer_id="buyer_unknown")
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=[],
            similar_cases=[],
            order_amount=0.0,
        )

        output = recommend(_with_precomputed(input_data))
        assert output.disposition in {DISPOSITION_DEFEND, DISPOSITION_NEGOTIATE, DISPOSITION_COMPENSATE}
        assert 0.0 <= output.confidence <= 1.0
        assert output.customer_value is not None

    def test_confidence_rule_dim_should_be_higher_when_rule_match_skipped(self):
        """简单案跳过条文匹配时，无命中规则维应按 0.30 计而非 0.08。"""
        from backend.agents.agent2.strategist import _estimate_confidence

        facts = FactOutput(evidence_quality="high")
        malicious = MaliciousDetectionOutput(risk_level="low")
        base = StrategyInput(
            facts=facts,
            buyer_profile=BuyerProfile(buyer_id="b1"),
            matched_rules=[],
            rule_briefs=[],
            order_amount=50.0,
        )
        skipped = base.model_copy(update={"rule_match_skipped": True})
        conf_default = _estimate_confidence(base, malicious_result=malicious, rule_stance="neutral", rule_count=0)
        conf_skipped = _estimate_confidence(skipped, malicious_result=malicious, rule_stance="neutral", rule_count=0)
        assert conf_skipped == conf_default + 0.22

    def test_recommend_should_use_fallback_reasoning_when_llm_unavailable(self, monkeypatch):
        """LLM不可用时，reasoning 仍应保持三段结构。"""
        self._mock_strategy_llm(monkeypatch)
        monkeypatch.setattr(strategist_module, "_llm_generate_strategy", lambda **kwargs: None)
        facts = FactOutput(evidence_quality="medium", missing_evidence=["缺少清晰图片"])
        profile = BuyerProfile(buyer_id="buyer_fallback")
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=[],
            similar_cases=[],
            order_amount=120.0,
        )
        output = recommend(_with_precomputed(input_data))
        assert "客户意图：" in output.reasoning
        assert "风险点：" in output.reasoning
        assert "建议动作：" in output.reasoning
        assert "推理理由：" in output.reasoning
        assert output.customer_intent_analysis
        assert "证据不足但诉求明确" in output.customer_intent_analysis
        assert output.strategy_direction_rationale

    def test_recommend_should_accept_llm_reasoning_when_format_valid(self, monkeypatch):
        """LLM输出三段结构时应直接采用。"""
        self._mock_strategy_llm(monkeypatch)
        monkeypatch.setattr(
            strategist_module,
            "_llm_generate_strategy",
            lambda **kwargs: {
                "customer_intent_analysis": "质量问题维权。",
                "strategy_direction_summary": "先要求买家补充完整开箱视频，暂不承诺退款。",
                "strategy_direction_rationale": "现有举证不足以认定责任；补证有利于按规则抗辩并控制损失。",
                "platform_rule_basis": ["买家应提供初步凭证"],
                "risk_factors": ["证据链仍需补强"],
                "dialogue_context": {
                    "dialogue_mode": "cold_start",
                    "blocked_evidence_requests": [],
                    "actionable_evidence_requests": ["开箱视频"],
                    "fallback_script": "麻烦补一下开箱视频，我核对后马上处理。",
                },
            },
        )
        facts = FactOutput(
            evidence_quality="high",
            defect_type="破洞",
            missing_evidence=["开箱视频"],
        )
        profile = BuyerProfile(buyer_id="buyer_llm")
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=profile,
            matched_rules=[],
            similar_cases=[],
            order_amount=220.0,
        )
        output = recommend(_with_precomputed(input_data))
        assert output.reasoning.startswith("客户意图：")
        assert "质量问题维权" in output.customer_intent_analysis
        assert "开箱视频" in output.strategy_direction_summary
        assert output.dialogue_context is not None
        assert "开箱视频" in output.dialogue_context.actionable_evidence_requests

    def test_normalize_dialogue_context_clears_generic_requests_when_evidence_complete(self):
        """举证已齐且非补证动作时，不得保留快递单等泛化补证请求。"""
        from backend.agents.agent2.strategist import _normalize_dialogue_context

        input_data = StrategyInput(
            facts=FactOutput(evidence_quality="high", missing_evidence=[]),
            buyer_profile=BuyerProfile(buyer_id="buyer_complete"),
            order_amount=268.0,
        )
        ctx = _normalize_dialogue_context(
            {"actionable_evidence_requests": ["快递单照片", "商品使用环境说明"]},
            input_data,
            action_type="defend_prepare",
        )
        assert ctx.actionable_evidence_requests == []

    def test_platform_rule_basis_should_come_from_matched_rules_not_llm(self, monkeypatch):
        """平台规则依据固定来自条文匹配，忽略策略 LLM 自造要点。"""
        self._mock_strategy_llm(monkeypatch)
        monkeypatch.setattr(
            strategist_module,
            "_llm_generate_strategy",
            lambda **kwargs: {
                "customer_intent_analysis": "屏幕划痕维权。",
                "strategy_direction_summary": "先核对举证再协商。",
                "strategy_direction_rationale": "需结合专项规范处理。",
                "platform_rule_basis": ["平台可能基于综合信息如大数据判断支持退货退款"],
                "risk_factors": [],
                "dialogue_context": {
                    "dialogue_mode": "continue",
                    "blocked_evidence_requests": [],
                    "actionable_evidence_requests": [],
                    "fallback_script": "我这边还在核对。",
                },
            },
        )
        phone_rule = MatchedRule(
            rule_id="phone::quality",
            rule_summary="手机存在质量问题且买家举证有效时，卖家不得以划痕为由拒绝退货退款",
            condition_result="LLM判定",
            relevance=RULE_RELEVANCE_MUST,
            doc_id="phone",
        )
        input_data = StrategyInput(
            facts=FactOutput(issue_summary="屏幕划痕", evidence_quality="high"),
            buyer_profile=BuyerProfile(buyer_id="buyer_phone"),
            matched_rules=[phone_rule],
            order_amount=399.0,
        )
        output = recommend(_with_precomputed(input_data))
        assert "大数据" not in " ".join(output.platform_rule_basis)
        assert any("划痕" in line for line in output.platform_rule_basis)

    def test_platform_rule_basis_only_statute_summaries_not_constraints(self):
        """平台规则依据仅收录 matched_rules 法条摘要，不混入 rule_constraints 程序句。"""
        evidence_template = (
            "处理退款或补偿前，应先核验买家举证是否满足规则要求，"
            "并固定商品照片、视频、快递单和聊天记录"
        )
        statute_line = "生鲜类商品存在腐烂、变质等情形的，买家需在签收商品之时起48小时内拍照并联系卖家协商"
        input_data = StrategyInput(
            facts=FactOutput(defect_type="腐败变质", evidence_quality="高"),
            buyer_profile=BuyerProfile(buyer_id="buyer_fresh"),
            matched_rules=[
                MatchedRule(
                    rule_id="fresh::第三条",
                    rule_summary=statute_line,
                    condition_result="LLM判定",
                    relevance=RULE_RELEVANCE_MUST,
                    doc_id="fresh",
                    article_no="第三条",
                )
            ],
            rule_constraints=[
                RuleConstraint(
                    constraint_type=RULE_CONSTRAINT_EVIDENCE,
                    text=evidence_template,
                    status=RULE_CONSTRAINT_APPLIES,
                    source_rule_id="fresh::第三条",
                ),
                RuleConstraint(
                    constraint_type=RULE_CONSTRAINT_OTHER,
                    text="举证要求",
                    status=RULE_CONSTRAINT_APPLIES,
                    source_rule_id="fresh::第三条",
                ),
            ],
        )
        basis = strategist_module._build_platform_rule_basis(input_data)
        assert basis == [statute_line]
        assert evidence_template not in " ".join(basis)
        assert "举证要求" not in basis

    def test_recommend_should_surface_precomputed_malicious_layer(self, monkeypatch):
        """分层编排后，应透传 Controller 预计算的恶意检测并写入风险提示。"""
        self._mock_strategy_llm(monkeypatch)
        high_malicious = MaliciousDetectionOutput(
            risk_score=72,
            risk_level="high",
            triggered_signals=[
                MaliciousSignal(
                    signal_type="review_blackmail",
                    description="条件交换式投诉威胁",
                    score=12,
                    source="llm_semantic",
                )
            ],
            hard_rule_summary="硬规则层未命中异常项。",
            disposition_advice="建议优先抗辩并准备平台介入材料，固定完整证据链后再沟通。",
        )
        input_data = StrategyInput(
            facts=FactOutput(evidence_quality="medium"),
            buyer_profile=BuyerProfile(buyer_id="buyer_layer"),
            matched_rules=[],
            similar_cases=[],
            order_amount=120.0,
            chat_history=["不给补偿我就去投诉平台"],
        )
        output = recommend(_with_precomputed(input_data, malicious_detection=high_malicious))
        assert output.malicious_detection is not None
        assert output.malicious_detection.risk_level == "high"
        assert any(item.startswith("[恶意层]") for item in output.risk_factors)


# ---------- 工具层：规则命中、画像默认、判例 top_k 截断 ----------
class TestAgent2Tools:
    def test_match_rules_should_score_articles_from_plan(self, monkeypatch):
        """规则匹配应基于 LLM 在候选条文中结构化选型（失败时回退字面检索）。"""
        base_doc_id = "争议处理基本规则_淘宝平台争议处理规则_1154_99"
        phone_doc_id = "特殊品类争议处理_淘宝平台手机类商品争议处理规范_1155_11003755"
        mock_docs = {
            base_doc_id: {
                "doc_id": base_doc_id,
                "articles": [
                    {
                        "article_no": "第六十五条",
                        "article_title": "买家主张商品存在质量问题系肉眼可识别的，应提供初步凭证予以证明。",
                        "content": "买家未提供初步凭证的，交易支持打款。",
                        "chapter": "",
                    }
                ],
            },
            phone_doc_id: {
                "doc_id": phone_doc_id,
                "articles": [
                    {
                        "article_no": "第四条",
                        "article_title": "商品质量问题",
                        "content": "买家主张收到的商品存在包括但不限于以下情形的：花屏、闪屏。举证要求。",
                        "chapter": "",
                    }
                ],
            },
        }
        monkeypatch.setattr(
            "backend.tools.rule_matcher._load_documents",
            lambda doc_ids: {k: v for k, v in mock_docs.items() if k in doc_ids},
        )

        def _fake_llm_match(facts, candidates):
            from backend.tools.rule_matcher import _score_article
            from backend.tools.rule_matcher_llm import RuleMatchLLMResult

            scored = []
            for item in candidates:
                doc_id = item["doc_id"]
                rule = _score_article(doc_id, {"doc_id": doc_id}, item, facts.rule_match_plan.search_terms)
                if rule is not None:
                    scored.append(rule)
            return RuleMatchLLMResult(matched_rules=scored, display_rule_ids=None)

        monkeypatch.setattr("backend.tools.rule_matcher_llm.llm_match_articles", _fake_llm_match)
        from schemas import RuleMatchPlan, RuleSearchTerms, SectionSelection

        facts = FactOutput(
            goods_received=True,
            defect_type="划痕",
            evidence_quality="low",
            logistics_normal=True,
            rule_match_plan=RuleMatchPlan(
                target_doc_ids=[base_doc_id, phone_doc_id],
                section_selections=[
                    SectionSelection(doc_id=base_doc_id, section_keys=[], confidence=0.9, reason=""),
                    SectionSelection(doc_id=phone_doc_id, section_keys=[], confidence=0.9, reason=""),
                ],
                search_terms=RuleSearchTerms(
                    must_terms=["商品质量问题", "初步凭证", "肉眼可识别"],
                    should_terms=["表面不一致"],
                    case_terms=["划痕"],
                    exclude_terms=[],
                ),
            ),
        )
        result = match_rules_from_facts(facts)
        assert result.display_rules, "期望有前端代表条"
        ids = " ".join(r.rule_id for r in result.matched_rules)
        assert "第六十五条" in ids or "第四条" in ids
        assert any(r.relevance == "must" for r in result.matched_rules)
        display = result.display_rules[0]
        assert "第六十五条" not in display.rule_summary
        assert "第四条" not in display.rule_summary
        summaries = " ".join(r.rule_summary for r in result.display_rules)
        assert "初步凭证" in summaries or "花屏" in summaries or "质量问题" in summaries

    def test_format_merchant_rule_text_strips_article_no(self):
        """商家展示文案应去除条号。"""
        from backend.tools.rule_matcher import _format_merchant_rule_text

        text = _format_merchant_rule_text(
            {
                "article_no": "第六十五条",
                "article_title": "第六十五条 买家主张商品存在质量问题系肉眼可识别的，应提供初步凭证予以证明。",
                "content": "买家未提供初步凭证的，交易支持打款。",
                "chapter": "",
            }
        )
        assert "第六十五条" not in text
        assert "初步凭证" in text

    def test_display_rules_prioritize_category_over_base(self):
        """前端代表条应优先展示品类专项规则，而非基本规则刷屏。"""
        from backend.tools.rule_matcher import _pick_display_rules
        from schemas import MatchedRule, RULE_RELEVANCE_MUST, RULE_RELEVANCE_SHOULD

        base_doc = "争议处理基本规则_淘宝平台争议处理规则_1154_99"
        fresh_doc = "特殊品类争议处理_淘宝平台生鲜类商品争议处理规范_1155_11003608"
        pool = [
            MatchedRule(
                rule_id=f"{base_doc}::第{i}条",
                rule_summary=f"基本规则通用要点{i}",
                condition_result="",
                relevance=RULE_RELEVANCE_MUST,
                doc_id=base_doc,
            )
            for i in range(1, 6)
        ] + [
            MatchedRule(
                rule_id=f"{fresh_doc}::第七条",
                rule_summary="生鲜腐烂需在签收48小时内举证并提供拆包视频",
                condition_result="",
                relevance=RULE_RELEVANCE_MUST,
                doc_id=fresh_doc,
            ),
            MatchedRule(
                rule_id=f"{fresh_doc}::第八条",
                rule_summary="买家主张生鲜变质且举证有效的，支持退货退款",
                condition_result="",
                relevance=RULE_RELEVANCE_SHOULD,
                doc_id=fresh_doc,
            ),
        ]
        picked = _pick_display_rules(pool)
        assert picked[0].doc_id == fresh_doc
        assert any("48小时" in r.rule_summary or "变质" in r.rule_summary for r in picked)
        assert sum(1 for r in picked if r.doc_id == base_doc) <= 1

    def test_briefs_follow_category_first_order(self):
        """rule_briefs 顺序应与品类优先策略一致。"""
        from backend.tools.rule_matcher import _sort_pool_category_first
        from schemas import MatchedRule, RULE_RELEVANCE_MUST, RULE_RELEVANCE_SHOULD

        base_doc = "争议处理基本规则_淘宝平台争议处理规则_1154_99"
        fresh_doc = "特殊品类争议处理_淘宝平台生鲜类商品争议处理规范_1155_11003608"
        pool = [
            MatchedRule(
                rule_id=f"{base_doc}::第{i}条",
                rule_summary=f"基本规则{i}",
                condition_result="",
                relevance=RULE_RELEVANCE_MUST,
                doc_id=base_doc,
            )
            for i in range(1, 8)
        ] + [
            MatchedRule(
                rule_id=f"{fresh_doc}::第七条",
                rule_summary="生鲜48小时举证",
                condition_result="",
                relevance=RULE_RELEVANCE_MUST,
                doc_id=fresh_doc,
            ),
        ]
        ordered = _sort_pool_category_first(pool)
        assert ordered[0].doc_id == fresh_doc
        assert sum(1 for r in ordered if r.doc_id == base_doc) <= 5

    def test_match_rules_empty_without_plan(self):
        """无 target_doc_ids 时不应匹配。"""
        facts = FactOutput(goods_received=True, defect_type="破洞", evidence_quality="high")
        assert match_rules_from_facts(facts).display_rules == []

    def test_classify_relevance_should_drop_generic_case_only_hits(self):
        """字面降级：仅命中泛化 case 词时应判 weak 并丢弃。"""
        from backend.tools.rule_matcher import _classify_relevance, RULE_RELEVANCE_WEAK

        relevance = _classify_relevance(
            must_hits=0,
            should_hits=0,
            case_hits=1,
            title="某条",
            hit_terms=["case:举证"],
        )
        assert relevance == RULE_RELEVANCE_WEAK

    def test_resolve_display_should_use_llm_display_ids(self):
        """条文匹配同批返回 display_rule_ids 时直接用于展示。"""
        from backend.tools.rule_matcher import _resolve_display_rules
        from schemas import MatchedRule, RULE_RELEVANCE_MUST, FactOutput

        pool = [
            MatchedRule(
                rule_id="base::第六十五条",
                rule_summary="初步凭证要点",
                condition_result="",
                relevance=RULE_RELEVANCE_MUST,
                doc_id="base",
            ),
            MatchedRule(
                rule_id="fresh::第七条",
                rule_summary="生鲜48小时举证",
                condition_result="",
                relevance=RULE_RELEVANCE_MUST,
                doc_id="fresh",
            ),
        ]
        facts = FactOutput(issue_summary="香蕉腐烂")
        display = _resolve_display_rules(
            pool,
            facts=facts,
            llm_display_ids=["fresh::第七条"],
        )
        assert len(display) == 1
        assert display[0].rule_id == "fresh::第七条"

    def test_resolve_display_should_return_empty_when_llm_says_none(self):
        """LLM 显式返回空 display_rule_ids 时不应启发式凑条。"""
        from backend.tools.rule_matcher import _resolve_display_rules
        from schemas import MatchedRule, RULE_RELEVANCE_MUST, FactOutput

        pool = [
            MatchedRule(
                rule_id="phone::quality",
                rule_summary="手机质量问题",
                condition_result="",
                relevance=RULE_RELEVANCE_MUST,
                doc_id="phone",
            ),
        ]
        facts = FactOutput(issue_summary="无关诉求")
        display = _resolve_display_rules(pool, facts=facts, llm_display_ids=[])
        assert display == []

    def test_query_buyer_profile_should_return_default_when_unknown(self):
        """未知买家 ID 返回默认画像。"""
        profile = query_buyer_profile("unknown_buyer")
        assert profile.buyer_id == "unknown_buyer"
        assert profile.purchase_count >= 0

    def test_search_similar_cases_top_k(self):
        """判例检索占位：未接 DB 前恒为空，仅校验 top_k 参数。"""
        assert search_similar_cases("物流异常退款", top_k=2) == []
        with pytest.raises(ValueError, match="top_k"):
            search_similar_cases("物流异常退款", top_k=0)

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
            has_visual_loss_exposure=True,
        )
        result = evaluate_customer_value(input_data)
        assert result.long_term_triggered is True
        assert result.channel == "long_term"
        assert result.compensation_uplift == "+10%~20%"

    def test_customer_value_long_term_requires_score_not_spend_alone(self, monkeypatch):
        """累计消费高但长期价值分不足时，不得仅凭金额触发老客通道。"""
        monkeypatch.setattr(agent2_tools_module, "ORDER_VALUE_SCORE_THRESHOLD", 60)
        profile = BuyerProfile(
            buyer_id="buyer_ltv",
            purchase_count=2,
            avg_order_value=80.0,
            dispute_rate=0.5,
            return_rate=0.4,
            positive_review_count=0,
        )
        result = evaluate_customer_value(CustomerValueInput(buyer_profile=profile, order_amount=50))

        assert result.long_term_score < 60
        assert result.long_term_triggered is False
        assert result.channel != "long_term"

    def test_evaluate_customer_value_should_score_recoverability_as_higher_when_worse(self):
        """商品越不可挽回，本单得分应越高。"""
        profile = BuyerProfile(buyer_id="buyer_case")
        low_loss = evaluate_customer_value(
            CustomerValueInput(
                buyer_profile=profile,
                order_amount=260.0,
                defect_severity="moderate",
                goods_recoverability="resalable",
                has_visual_loss_exposure=True,
            )
        )
        high_loss = evaluate_customer_value(
            CustomerValueInput(
                buyer_profile=profile,
                order_amount=260.0,
                defect_severity="moderate",
                goods_recoverability="unrecoverable",
                has_visual_loss_exposure=True,
            )
        )
        assert high_loss.order_score > low_loss.order_score

    def test_evaluate_customer_value_should_not_trigger_order_without_visual_loss(self):
        """无视觉损失暴露且金额一般时不触发本单通道。"""
        result = evaluate_customer_value(
            CustomerValueInput(
                buyer_profile=BuyerProfile(buyer_id="b1"),
                order_amount=260.0,
                has_visual_loss_exposure=False,
            )
        )
        assert result.order_triggered is False
        assert result.channel == "none"

    def test_evaluate_customer_value_should_trigger_order_on_high_amount_without_visual(self):
        """无图但本单金额够高仍可触发本单通道。"""
        result = evaluate_customer_value(
            CustomerValueInput(
                buyer_profile=BuyerProfile(buyer_id="b2"),
                order_amount=520.0,
                has_visual_loss_exposure=False,
            )
        )
        assert result.order_triggered is True
        assert result.channel == "order"

    def test_run_customer_value_should_read_visual_fields_from_facts(self):
        """客户价值应从 FactOutput 视觉枚举读取损失暴露。"""
        from backend.tools.agent2_tools import run_customer_value_analysis

        output = run_customer_value_analysis(
            StrategyInput(
                facts=FactOutput(
                    visual_defect_severity="severe",
                    visual_goods_recoverability="unrecoverable",
                    evidence_quality="high",
                ),
                buyer_profile=BuyerProfile(buyer_id="b3"),
                order_amount=280.0,
            )
        )
        assert output.order_score >= 60
        assert output.channel == "order"

    def test_run_customer_value_should_block_order_channel_on_red_flags(self):
        """有 red_flags 时不触发本单优待通道。"""
        from backend.tools.agent2_tools import run_customer_value_analysis

        output = run_customer_value_analysis(
            StrategyInput(
                facts=FactOutput(
                    visual_defect_severity="severe",
                    visual_goods_recoverability="unrecoverable",
                    red_flags=["图文来源可疑"],
                    evidence_quality="high",
                ),
                buyer_profile=BuyerProfile(buyer_id="b4"),
                order_amount=600.0,
            )
        )
        assert output.order_triggered is False
        assert output.channel == "none"
        """硬规则命中时应输出风险分与等级。"""
        profile = BuyerProfile(
            buyer_id="buyer_risk",
            purchase_count=8,
            dispute_rate=0.62,
            return_rate=0.55,
            malicious_flags=3,
        )
        input_data = MaliciousDetectionInput(
            buyer_profile=profile,
            facts=FactOutput(evidence_quality="low", red_flags=["凭证异常"], logistics_normal=False),
            order_amount=299.0,
            order_address="广东省深圳市南山区",
            recent_refund_only_count=4,
            return_rate_category_avg=0.16,
            freight_insurance_used=True,
            swap_flag_count=2,
            related_account_count=4,
            chat_history=[],
        )
        result = detect_malicious_behavior(input_data)
        assert result.risk_score >= 60
        assert result.risk_level == "high"
        assert len(result.triggered_signals) >= 4

    def test_detect_malicious_deceptive_credential_should_be_high(self, monkeypatch):
        """本单举证存在网图/非实拍欺骗线索时，应直接命中恶意举证硬规则。"""
        monkeypatch.delenv("AGENT2_LLM_MODEL_MALICIOUS", raising=False)
        input_data = MaliciousDetectionInput(
            buyer_profile=BuyerProfile(buyer_id="buyer_new", purchase_count=0, return_rate=0.0),
            facts=FactOutput(
                evidence_quality="medium",
                issue_summary="电热水壶底座开裂",
                credential_trust="suspect",
                credential_trust_note="图片角落可见1688.com批发图水印",
                red_flags=["图文来源可疑"],
                visual_observations=["图片角落可见1688.com批发图水印"],
            ),
            order_amount=199.0,
            chat_history=[],
        )
        result = detect_malicious_behavior(input_data)
        assert any(item.signal_type == "deceptive_credential" for item in result.triggered_signals)
        assert result.risk_score >= 20
        assert result.risk_level == "high"

    def test_detect_malicious_ai_generated_credential_should_be_high(self, monkeypatch):
        """事实层明确 AI 生图/伪造举证时，应按明显欺骗类恶意处理。"""
        monkeypatch.delenv("AGENT2_LLM_MODEL_MALICIOUS", raising=False)
        input_data = MaliciousDetectionInput(
            buyer_profile=BuyerProfile(buyer_id="buyer_ai", purchase_count=0, return_rate=0.0),
            facts=FactOutput(
                evidence_quality="medium",
                issue_summary="买家称商品外壳破裂",
                credential_trust="suspect",
                credential_trust_note="举证图疑似AI生成",
                red_flags=["举证图疑似AI生成，纹理和阴影不符合实拍"],
                visual_observations=["图片存在AI生图痕迹，破损边缘形态不自然"],
            ),
            order_amount=299.0,
            chat_history=[],
        )
        result = detect_malicious_behavior(input_data)
        assert any(item.signal_type == "deceptive_credential" for item in result.triggered_signals)
        assert result.risk_level == "high"

    def test_deceptive_credential_hard_rule_follows_credential_trust(self, monkeypatch):
        """虚假举证硬规则只认 Agent1 credential_trust，不认自然语言水印描述。"""
        monkeypatch.delenv("AGENT2_LLM_MODEL_MALICIOUS", raising=False)
        suspect = detect_malicious_behavior(
            MaliciousDetectionInput(
                buyer_profile=BuyerProfile(buyer_id="buyer_wm", purchase_count=0, return_rate=0.0),
                facts=FactOutput(
                    evidence_quality="medium",
                    credential_trust="suspect",
                    visual_observations=["图片角落可见批发图水印"],
                ),
                order_amount=199.0,
                chat_history=[],
            )
        )
        assert any(item.signal_type == "deceptive_credential" for item in suspect.triggered_signals)

        clean = detect_malicious_behavior(
            MaliciousDetectionInput(
                buyer_profile=BuyerProfile(buyer_id="buyer_wm2", purchase_count=0, return_rate=0.0),
                facts=FactOutput(
                    evidence_quality="medium",
                    credential_trust="unknown",
                    visual_observations=["图片角落可见批发图水印"],
                ),
                order_amount=199.0,
                chat_history=[],
            )
        )
        assert not any(item.signal_type == "deceptive_credential" for item in clean.triggered_signals)

    def test_detect_malicious_behavior_should_merge_semantic_signals(self, monkeypatch):
        """语义层返回结构化信号时应参与综合评分。"""
        monkeypatch.setenv("AGENT2_LLM_MODEL_MALICIOUS", "mock-model")
        monkeypatch.setattr(
            agent2_tools_module,
            "chat_completion",
            lambda **kwargs: (
                '[{"signal_type":"review_blackmail","description":"出现差评勒索语义","score":20,"source":"llm_semantic"}]'
            ),
        )
        input_data = MaliciousDetectionInput(
            buyer_profile=BuyerProfile(buyer_id="buyer_semantic", return_rate=0.05),
            facts=FactOutput(evidence_quality="medium", red_flags=[]),
            order_amount=99.0,
            chat_history=["不给赔偿我就差评并投诉你们店"],
        )
        result = detect_malicious_behavior(input_data)
        assert result.risk_score >= 20
        assert result.risk_level == "high"
        assert any(item.signal_type == "review_blackmail" for item in result.triggered_signals)

    def test_parse_llm_json_array_tolerates_trailing_explanation(self):
        """恶意语义 JSON 数组后附带说明时不应 Extra data 失败。"""
        payload = (
            '[{"signal_type":"review_blackmail","description":"要挟","score":20,"source":"llm_semantic"}]'
            "\n以上为识别结果。"
        )
        parsed = agent2_tools_module._parse_llm_json_array(payload)
        assert len(parsed) == 1
        assert parsed[0]["signal_type"] == "review_blackmail"

    def test_detect_malicious_semantic_ignores_unauthorized_signal_type(self, monkeypatch):
        """语义层输出未授权 signal_type 时应忽略。"""
        monkeypatch.setenv("AGENT2_LLM_MODEL_MALICIOUS", "mock-model")
        monkeypatch.setattr(
            agent2_tools_module,
            "chat_completion",
            lambda **kwargs: (
                '[{"signal_type":"legacy_fake_credential_web_image",'
                '"description":"举证图带门户网站水印","score":30,"source":"llm_semantic"}]'
            ),
        )
        input_data = MaliciousDetectionInput(
            buyer_profile=BuyerProfile(buyer_id="buyer_no_anchor", return_rate=0.05),
            facts=FactOutput(
                evidence_quality="medium",
                issue_summary="手机有划痕要求退款",
                red_flags=[],
                visual_observations=["屏幕反光，未见明显划痕"],
            ),
            order_amount=1999.0,
            chat_history=["给我退款"],
        )
        result = detect_malicious_behavior(input_data)
        assert result.triggered_signals == []

    def test_detect_malicious_behavior_should_reject_abuse_refund_without_chat_markers(self, monkeypatch):
        """语义层复述 few-shot 套利话术但聊天无对应表述时，应丢弃以防误报。"""
        monkeypatch.setenv("AGENT2_LLM_MODEL_MALICIOUS", "mock-model")
        monkeypatch.setattr(
            agent2_tools_module,
            "chat_completion",
            lambda **kwargs: (
                '[{"signal_type":"abuse_refund_intent_chat",'
                '"description":"聊天暗示高频退款并提及运费险套利，存在滥用售后意图","score":5,"source":"llm_semantic"}]'
            ),
        )
        input_data = MaliciousDetectionInput(
            buyer_profile=BuyerProfile(buyer_id="buyer_banana", return_rate=0.05),
            facts=FactOutput(evidence_quality="high", issue_summary="香蕉腐烂", defect_type="变质"),
            order_amount=29.9,
            chat_history=["收到的香蕉都烂了，要求退款"],
        )
        result = detect_malicious_behavior(input_data)
        assert result.risk_score == 0
        assert not any(item.signal_type == "abuse_refund_intent_chat" for item in result.triggered_signals)

    def test_detect_malicious_behavior_should_not_mark_emotional_complaint_as_blackmail(self, monkeypatch):
        """仅情绪激动+提及投诉但无条件交换，不应计为勒索恶意分。"""
        monkeypatch.setenv("AGENT2_LLM_MODEL_MALICIOUS", "mock-model")
        monkeypatch.setattr(
            agent2_tools_module,
            "chat_completion",
            lambda **kwargs: (
                '[{"signal_type":"review_blackmail","description":"出现投诉表述","score":10,"source":"llm_semantic"}]'
            ),
        )
        input_data = MaliciousDetectionInput(
            buyer_profile=BuyerProfile(buyer_id="buyer_emotional", return_rate=0.05),
            facts=FactOutput(evidence_quality="high", red_flags=[]),
            order_amount=129.0,
            chat_history=["这次体验很差，我会投诉平台，希望你们尽快给处理方案"],
        )
        result = detect_malicious_behavior(input_data)
        assert result.risk_score == 0
        assert not any(item.signal_type == "review_blackmail" for item in result.triggered_signals)


class TestMaliciousSemanticOptimization:
    def test_malicious_semantic_llm_gate(self, monkeypatch):
        """语义 LLM 门控：有实质聊天才调；低材料与仅 red_flags 不调。"""
        monkeypatch.setenv("AGENT2_LLM_MODEL_MALICIOUS", "mock-model")
        called = {"count": 0}

        def _mock_call(**_kwargs):
            called["count"] += 1
            return "[]"

        monkeypatch.setattr(agent2_tools_module, "chat_completion", _mock_call)

        detect_malicious_behavior(
            MaliciousDetectionInput(
                buyer_profile=BuyerProfile(buyer_id="buyer_chat", purchase_count=0, return_rate=0.0),
                facts=FactOutput(evidence_quality="high", issue_summary="商品破损"),
                order_amount=88.0,
                chat_history=["商品收到就裂了，你们必须今天内给我处理退款，不然我天天来问"],
            )
        )
        assert called["count"] == 1

        called["count"] = 0
        low_material = detect_malicious_behavior(
            MaliciousDetectionInput(
                buyer_profile=BuyerProfile(buyer_id="buyer_plain", return_rate=0.05),
                facts=FactOutput(evidence_quality="high", issue_summary="香蕉褐变", defect_type="变质"),
                order_amount=29.9,
                chat_history=[],
            )
        )
        assert called["count"] == 0
        assert low_material.risk_score == 0

        called["count"] = 0
        detect_malicious_behavior(
            MaliciousDetectionInput(
                buyer_profile=BuyerProfile(buyer_id="buyer_flag", return_rate=0.05),
                facts=FactOutput(evidence_quality="high", red_flags=["图片带 sohu 水印"]),
                order_amount=29.9,
                chat_history=[],
            )
        )
        assert called["count"] == 0

    def test_malicious_facts_summary_omits_heavy_fields(self):
        from backend.tools.agent2_tools import _build_malicious_facts_summary, _build_malicious_semantic_messages

        facts = FactOutput(
            issue_summary="屏幕划痕",
            red_flags=["疑点"],
            evidence_items=[{"type": "text", "content": "x" * 200}],
        )
        summary = _build_malicious_facts_summary(facts)
        assert "evidence_items" not in summary
        assert summary["issue_summary"] == "屏幕划痕"
        messages = _build_malicious_semantic_messages(
            input_data=MaliciousDetectionInput(
                buyer_profile=BuyerProfile(buyer_id="b", return_rate=0.1),
                facts=facts,
                chat_history=["a"] * 10,
            ),
            hard_rule_summary="硬规则层未命中异常项。",
        )
        user_msg = messages[-1]["content"]
        assert "evidence_items" not in user_msg
        assert messages.count({"role": "user", "content": messages[1]["content"]}) == 1
        assert len([m for m in messages if m["role"] == "assistant"]) == 2


class TestStrategyPromptPayload:
    def test_build_strategy_prompt_payload_should_omit_heavy_fields(self):
        from backend.agents.agent2.strategist import _build_strategy_prompt_payload
        from schemas import ChatTurn, CustomerValueOutput, RuleBrief, RuleMatchPlan, RuleSearchTerms

        long_chat = "x" * 500
        input_data = StrategyInput(
            facts=FactOutput(
                issue_summary="香蕉褐变",
                intent_tags=["质量问题"],
                visual_observations=["表皮黑斑"],
                evidence_items=[{"type": "text", "content": long_chat}],
                red_flags=["水印"],
                rule_match_plan=RuleMatchPlan(
                    target_doc_ids=["doc_a"],
                    search_terms=RuleSearchTerms(must_terms=["举证"]),
                ),
            ),
            buyer_profile=BuyerProfile(buyer_id="hash_id", purchase_count=3),
            matched_rules=[
                MatchedRule(
                    rule_id="doc::1",
                    rule_summary="规则摘要",
                    condition_result="条件",
                )
            ],
            rule_briefs=[RuleBrief(article_ref="第七条", brief="食品质量要点", relevance="must")],
            similar_cases=[],
            chat_turns=[ChatTurn(role="buyer", content=f"turn{i}") for i in range(12)],
        )
        payload = _build_strategy_prompt_payload(
            disposition=DISPOSITION_NEGOTIATE,
            strategy_stage="negotiate_settle",
            compensation_policy="negotiate_soft",
            evidence_incomplete=False,
            input_data=input_data,
            risk_factors=[],
            estimated_win_rate=0.6,
        )
        facts = payload["facts"]
        assert "evidence_items" not in facts
        assert "rule_match_plan" not in facts
        assert "matched_rules" not in payload
        assert "similar_cases" not in payload
        assert len(payload["recent_turns"]) == 8
        assert payload["buyer_profile"].get("buyer_id") is None
        assert payload["rule_briefs"][0]["brief"] == "食品质量要点"
        assert "article_ref" not in payload["rule_briefs"][0]

    def test_needs_rule_match_gate(self):
        """简单案跳过条文匹配；C/E 通道、恶意/价值/多诉求标签触发。"""
        from backend.tools.agent2_tools import needs_rule_match
        from schemas import CustomerValueOutput, FactOutput, MaliciousDetectionOutput, RuleMatchPlan

        base_facts = FactOutput(issue_summary="屏幕划痕", intent_tags=["质量问题"], evidence_quality="high")
        base_malicious = MaliciousDetectionOutput(risk_level="low")
        base_value = CustomerValueOutput(channel="none")
        assert not needs_rule_match(base_facts, base_malicious, base_value)

        category_facts = FactOutput(
            issue_summary="批量试穿后要求退货",
            intent_tags=["七天无理由退货"],
            evidence_quality="medium",
            rule_match_plan=RuleMatchPlan(
                activated_lanes=["A", "C"],
                target_doc_ids=["特殊品类争议处理_淘宝平台服饰类商品争议处理规范_1155_11003625"],
            ),
        )
        assert needs_rule_match(category_facts, base_malicious, base_value)

        service_facts = FactOutput(
            issue_summary="七天无理由完好争议",
            intent_tags=["七天无理由退货"],
            evidence_quality="medium",
            rule_match_plan=RuleMatchPlan(
                activated_lanes=["A", "E"],
                target_doc_ids=["服务保障_淘宝网七天无理由退货规范_1150_5507"],
            ),
        )
        assert needs_rule_match(service_facts, base_malicious, base_value)

        assert needs_rule_match(
            base_facts,
            MaliciousDetectionOutput(risk_level="medium"),
            base_value,
        )
        assert needs_rule_match(
            base_facts,
            base_malicious,
            CustomerValueOutput(channel="order"),
        )
        multi_intent = FactOutput(issue_summary="既要退款又投诉物流", intent_tags=["质量问题", "物流异常"])
        assert needs_rule_match(multi_intent, base_malicious, base_value)

        missing_evidence = FactOutput(
            issue_summary="要求退款",
            intent_tags=["质量问题"],
            missing_evidence=["开箱视频"],
            evidence_quality="low",
        )
        assert not needs_rule_match(missing_evidence, base_malicious, base_value)


class TestRuleMatcherInfra:
    def test_facts_overlay_should_preserve_rule_context(self):
        """facts_override 未知规则字段应进入 attributes.rule_context。"""
        from eval.pipeline.run_manual_cases import _normalize_facts_overlay_for_model

        normalized = _normalize_facts_overlay_for_model(
            {
                "issue_summary": "葡萄霉变",
                "is_received": True,
                "time_since_delivery_hours": 49,
                "compensation_ratio_cap": 0.3,
            }
        )
        facts = FactOutput.model_validate(normalized)
        assert facts.goods_received is True
        assert facts.attributes["rule_context"]["time_since_delivery_hours"] == 49
        assert facts.attributes["rule_context"]["compensation_ratio_cap"] == 0.3

    def test_rule_constraints_should_capture_timing_and_ratio_limit(self):
        """规则匹配应把时效与补偿比例转成结构化约束。"""
        from backend.tools.rule_matcher import _build_rule_constraints

        rule = MatchedRule(
            rule_id="fresh::第三条",
            rule_summary="生鲜类商品存在腐烂、变质等情形的，买家需在签收商品之时起48小时内拍照并联系卖家协商。",
            condition_result="",
            relevance=RULE_RELEVANCE_MUST,
            doc_id="fresh",
            article_no="第三条",
        )
        facts = FactOutput(
            evidence_quality="high",
            attributes={
                "rule_context": {
                    "time_since_delivery_hours": 49,
                    "compensation_ratio_cap": 0.3,
                }
            },
        )
        constraints = _build_rule_constraints([rule], facts)
        timing_violated = [
            item
            for item in constraints
            if item.constraint_type == RULE_CONSTRAINT_TIMING and item.status == "violated"
        ]
        assert timing_violated
        assert "48" in timing_violated[0].text
        assert "已超过" in timing_violated[0].text
        assert "核验并说明是否超过" not in timing_violated[0].text
        assert any(
            item.constraint_type == RULE_CONSTRAINT_RATIO_LIMIT and "30%" in item.text
            for item in constraints
        )

    def test_category_label_without_slug_should_not_hard_match_fresh(self):
        """中文品类标签不再硬匹配 slug，须走语义推断或留空。"""
        from backend.tools.rule_lexicon import infer_category_slug_from_materials

        assert infer_category_slug_from_materials({"category": "水果"}) is None
        assert infer_category_slug_from_materials({"product_category_slug": "fresh"}) == "fresh"

    def test_strategy_should_prioritize_timing_constraint(self, monkeypatch):
        """时效未满足时，策略动作应先解释规则边界而非金额和解。"""
        monkeypatch.setattr(strategist_module, "_llm_generate_strategy", lambda **_kwargs: None)
        input_data = StrategyInput(
            facts=FactOutput(issue_summary="葡萄霉变，申请仅退款", evidence_quality="high"),
            buyer_profile=BuyerProfile(buyer_id="buyer_timing", purchase_count=5),
            matched_rules=[],
            rule_briefs=[],
            rule_constraints=[
                RuleConstraint(
                    constraint_type=RULE_CONSTRAINT_TIMING,
                    status=RULE_CONSTRAINT_MISSING_FACT,
                    text="本案售后发生在签收后约48小时，需先核验并说明是否超过规则要求的48小时内申请/举证时效",
                    source_rule_id="fresh::第三条",
                )
            ],
            order_amount=50,
        )
        output = recommend(
            _with_precomputed(
                input_data,
                malicious_detection=MaliciousDetectionOutput(risk_level="medium", risk_score=20),
                customer_value=CustomerValueOutput(channel="none"),
            )
        )
        assert output.action_type == "rule_explain"
        assert output.compensation_policy == "none"
        assert "48小时" in output.next_step

    def test_script_payload_should_include_compensation_cap(self):
        """话术 payload 应根据规则上下文计算最大补偿金额。"""
        from backend.agents.agent3.script_generator import _build_script_payload
        from schemas import ScriptInput, StrategyOutput

        strategy = StrategyOutput(
            disposition=DISPOSITION_NEGOTIATE,
            rule_constraints=["补偿不得超过本单金额30%"],
            structured_rule_constraints=[
                RuleConstraint(
                    constraint_type=RULE_CONSTRAINT_RATIO_LIMIT,
                    status=RULE_CONSTRAINT_APPLIES,
                    text="补偿或让步金额不得超过本单金额的30%",
                    source_rule_id="policy_limits",
                )
            ],
        )
        input_data = ScriptInput(
            strategy_output=strategy,
            facts=FactOutput(attributes={"rule_context": {"compensation_ratio_cap": 0.3}}),
            order_id="order-1",
            order_amount=50,
        )
        payload = _build_script_payload(
            input_data,
            compensation_policy="explicit_amount",
            dialogue_context=strategy.dialogue_context or strategist_module.DialogueContext(),
            must_state_compensation_amount=True,
        )
        assert payload["max_compensation_amount"] == 15

    def test_load_documents_should_batch_fetch_uncached_doc_ids(self, monkeypatch):
        """未命中缓存的 doc 应一次批量查询 MySQL。"""
        from backend.tools import rule_matcher as rule_matcher_module

        rule_matcher_module.clear_rule_document_cache()
        batch_calls: list[list[str]] = []

        def _fake_batch_fetch(doc_ids: list[str]) -> dict[str, dict | None]:
            batch_calls.append(list(doc_ids))
            return {doc_id: {"doc_id": doc_id, "articles": []} for doc_id in doc_ids}

        monkeypatch.setattr(rule_matcher_module, "_fetch_rule_documents_from_db", _fake_batch_fetch)
        loaded = rule_matcher_module._load_documents(["doc_a", "doc_b"])
        assert len(batch_calls) == 1
        assert set(batch_calls[0]) == {"doc_a", "doc_b"}
        assert set(loaded.keys()) == {"doc_a", "doc_b"}

        rule_matcher_module._load_documents(["doc_a"])
        assert len(batch_calls) == 1

    def test_llm_match_articles_should_use_rule_model_env(self, monkeypatch):
        """条文匹配 LLM 应优先读取 AGENT2_LLM_MODEL_RULE。"""
        from backend.tools.rule_matcher_llm import llm_match_articles

        monkeypatch.setenv("AGENT2_LLM_MODEL_RULE", "rule-fast-model")
        captured: dict[str, str] = {}

        def _fake_chat(**kwargs):
            captured["model_env_key"] = kwargs.get("model_env_key", "")
            captured["fallback_model_env_key"] = kwargs.get("fallback_model_env_key", "")
            return '{"matched_articles":[],"display_rule_ids":[]}'

        monkeypatch.setattr("backend.tools.rule_matcher_llm.chat_completion", _fake_chat)
        llm_match_articles(
            facts=FactOutput(issue_summary="屏幕划痕"),
            candidates=[{"doc_id": "phone", "article_no": "第四条", "content": "质量问题", "article_title": "质量"}],
        )
        assert captured["model_env_key"] == "AGENT2_LLM_MODEL_RULE"
        assert captured["fallback_model_env_key"] == "AGENT2_LLM_MODEL"

    def test_match_rules_should_skip_llm_when_candidates_at_most_five(self, monkeypatch):
        """候选≤5 时不调条文 LLM，走字面降级。"""
        from backend.tools.rule_matcher import match_rules_from_facts

        called = {"count": 0}

        def _should_not_call(*_args, **_kwargs):
            called["count"] += 1
            return None

        monkeypatch.setattr("backend.tools.rule_matcher_llm.llm_match_articles", _should_not_call)
        mock_doc = {
            "doc_id": "fresh",
            "articles": [
                {
                    "article_no": "第七条",
                    "article_title": "商品质量问题",
                    "content": "买家主张商品质量问题应提供初步凭证。",
                    "chapter": "",
                }
            ],
        }
        monkeypatch.setattr(
            "backend.tools.rule_matcher._load_documents",
            lambda _doc_ids: {"fresh": mock_doc},
        )
        from schemas import RuleMatchPlan, RuleSearchTerms, SectionSelection

        facts = FactOutput(
            issue_summary="香蕉腐烂",
            intent_tags=["质量问题"],
            rule_match_plan=RuleMatchPlan(
                target_doc_ids=["fresh"],
                section_selections=[
                    SectionSelection(doc_id="fresh", section_keys=[], confidence=0.9, reason=""),
                ],
                search_terms=RuleSearchTerms(
                    must_terms=["商品质量问题", "初步凭证"],
                    should_terms=[],
                    case_terms=["腐烂"],
                ),
            ),
        )
        result = match_rules_from_facts(facts)
        assert called["count"] == 0
        assert len(result.matched_rules) >= 1
