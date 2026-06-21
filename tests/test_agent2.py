"""
Agent2 / agent2_tools 核心契约测试。

原则：仅保留典型场景与关键边界；业务回归以 eval 评测树 + Judge 为准。
"""

import os
import sys

import pytest

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
    needs_rule_match,
    run_customer_value_analysis,
    search_similar_cases,
)
from backend.tools.rule_matcher import match_rules_from_facts
from schemas import (
    ACTION_DEFEND_PREPARE,
    ACTION_MONETARY_SETTLE,
    ACTION_RULE_EXPLAIN,
    BuyerProfile,
    ChatTurn,
    CustomerValueInput,
    CustomerValueOutput,
    FactOutput,
    MaliciousDetectionOutput,
    MaliciousDetectionInput,
    MaliciousSignal,
    MatchedRule,
    RuleBrief,
    RuleConstraint,
    RuleMatchPlan,
    RuleSearchTerms,
    SectionSelection,
    StrategyInput,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    RULE_RELEVANCE_MUST,
    RULE_CONSTRAINT_RATIO_LIMIT,
    RULE_CONSTRAINT_TIMING,
)


def _with_precomputed(
    input_data: StrategyInput,
    *,
    malicious_detection: MaliciousDetectionOutput | None = None,
    customer_value: CustomerValueOutput | None = None,
) -> StrategyInput:
    """为 recommend() 补齐 Controller 预计算的恶意/价值结果。"""
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


def _mock_strategy_llm(monkeypatch):
    """屏蔽策略 LLM，按证据与规则站位推断 responsibility。"""

    def _ctx_aware_llm(*, disposition, input_data, risk_factors, estimated_win_rate,
                       strategy_stage, action_contract, reasoning_delta_callback=None):
        facts = input_data.facts
        evidence = (facts.evidence_quality or "").strip().lower()
        defect = (facts.defect_type or "").strip()
        red_flags = [str(f).strip() for f in (facts.red_flags or []) if str(f).strip()]
        credential = (facts.credential_trust or "").strip().lower()

        rule_responsibility = "unclear"
        stance_map = {}
        for rule in input_data.matched_rules or []:
            hint = (rule.stance_hint or "").strip().lower()
            stance_map[hint] = stance_map.get(hint, 0) + 1
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
                resp, conf = "unclear", 0.5
        elif evidence == "low" and red_flags:
            resp, conf = "unclear", 0.5
        elif evidence == "medium":
            resp = rule_responsibility if rule_responsibility != "unclear" else "unclear"
            conf = 0.5
        elif rule_responsibility != "unclear":
            resp, conf = rule_responsibility, 0.6

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


class TestAgent2Recommend:
    """策略 recommend：三种处置方向 + 契约 + 降级。"""

    def test_recommend_compensate_when_high_quality_defect(self, monkeypatch):
        """高质量瑕疵证据，倾向善后。"""
        _mock_strategy_llm(monkeypatch)
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
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=BuyerProfile(buyer_id="buyer_loyal", dispute_rate=0.06, credit_level="high"),
            matched_rules=matched_rules,
            similar_cases=search_similar_cases("破洞退款纠纷", top_k=3),
            order_amount=128.0,
        )
        output = recommend(_with_precomputed(input_data))
        assert output.disposition == DISPOSITION_COMPENSATE
        assert output.policy_ref and "R002" in output.policy_ref

    def test_recommend_defend_when_low_evidence_and_high_risk_buyer(self, monkeypatch):
        """证据不足且买家风险高，倾向抗辩。"""
        _mock_strategy_llm(monkeypatch)
        facts = FactOutput(
            goods_received=True,
            defect_type="无瑕疵",
            has_tag_visible=True,
            evidence_quality="low",
            missing_evidence=["商品照片"],
            red_flags=["疑似二次损坏"],
        )
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=BuyerProfile(
                buyer_id="buyer_high_risk",
                dispute_rate=0.55,
                return_rate=0.48,
                malicious_flags=2,
            ),
            matched_rules=[
                MatchedRule(
                    rule_id="R004",
                    rule_summary="证据不足，商家可申诉补证",
                    condition_result="规则条件全部满足；建议策略:defend",
                )
            ],
            similar_cases=search_similar_cases("吊牌完整但要求退款", top_k=2),
            order_amount=99.0,
        )
        output = recommend(_with_precomputed(input_data))
        assert output.disposition == DISPOSITION_DEFEND
        assert output.estimated_win_rate is not None
        assert output.risk_factors

    def test_recommend_negotiate_when_medium_evidence_but_missing_key_proof(self, monkeypatch):
        """关键举证未齐时倾向协商，动作为补证阶段。"""
        _mock_strategy_llm(monkeypatch)
        facts = FactOutput(
            goods_received=True,
            defect_type="划痕",
            evidence_quality="medium",
            issue_summary="手机拆开就有划痕，要求退款",
            intent_tags=["质量问题", "退款诉求"],
            missing_evidence=["完整连续开箱视频"],
        )
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=BuyerProfile(buyer_id="buyer_scratch", dispute_rate=0.08),
            matched_rules=[],
            similar_cases=[],
            order_amount=2999.0,
        )
        output = recommend(_with_precomputed(input_data))
        assert output.disposition == DISPOSITION_NEGOTIATE
        assert "补证" in output.strategy_direction_summary or "举证" in output.strategy_direction_summary

    def test_clear_merchant_fault_should_output_remedy_contract(self, monkeypatch):
        """商责明确时进入善后动作契约。"""
        _mock_strategy_llm(monkeypatch)
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

    def test_service_return_frame_strategy_contract(self, monkeypatch):
        """七天无理由框架：不讲金额和解，动作为讲规则/验收。"""
        _mock_strategy_llm(monkeypatch)
        facts = FactOutput(
            issue_summary="买家主张七天无理由应直接退货退款。",
            intent_tags=["七天无理由退货", "退款诉求", "商品完好争议"],
            goods_received=True,
            defect_type="无",
            evidence_quality="medium",
            missing_evidence=[],
            primary_dispute_frame="seven_day_return",
        )
        rule = MatchedRule(
            rule_id="return::intact",
            rule_summary="七天无理由退货以商品完好、不影响二次销售为前提。",
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
                    brief="商品完好且不影响二次销售，退回需验收。",
                    relevance=RULE_RELEVANCE_MUST,
                    stance_hint="neutral",
                )
            ],
            order_amount=2400.0,
            chat_turns=[ChatTurn(role="buyer", content="为什么不能直接退？")],
        )
        output = recommend(_with_precomputed(input_data))
        assert output.action_type == "rule_explain"
        assert output.compensation_policy == "none"
        assert "金额" not in output.strategy_direction_summary

    def test_recommend_should_use_fallback_reasoning_when_llm_unavailable(self, monkeypatch):
        """LLM 不可用时仍输出结构化 reasoning。"""
        _mock_strategy_llm(monkeypatch)
        monkeypatch.setattr(strategist_module, "_llm_generate_strategy", lambda **kwargs: None)
        input_data = StrategyInput(
            facts=FactOutput(evidence_quality="medium", missing_evidence=["缺少清晰图片"]),
            buyer_profile=BuyerProfile(buyer_id="buyer_fallback"),
            matched_rules=[],
            similar_cases=[],
            order_amount=120.0,
        )
        output = recommend(_with_precomputed(input_data))
        assert "客户意图：" in output.reasoning
        assert "建议动作：" in output.reasoning

    def test_recommend_should_surface_precomputed_malicious_layer(self, monkeypatch):
        """透传 Controller 预计算的恶意检测结果。"""
        _mock_strategy_llm(monkeypatch)
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
            disposition_advice="建议优先抗辩并准备平台介入材料。",
        )
        input_data = StrategyInput(
            facts=FactOutput(evidence_quality="medium"),
            buyer_profile=BuyerProfile(buyer_id="buyer_layer"),
            matched_rules=[],
            order_amount=120.0,
            chat_history=["不给补偿我就去投诉平台"],
        )
        output = recommend(_with_precomputed(input_data, malicious_detection=high_malicious))
        assert output.malicious_detection.risk_level == "high"
        assert any(item.startswith("[恶意层]") for item in output.risk_factors)

    def test_recommend_pressure_blackmail_should_de_escalate_to_rule_explain(self, monkeypatch):
        """语义施压场景对外协商降格，非 defend_prepare。"""
        _mock_strategy_llm(monkeypatch)
        high_pressure = MaliciousDetectionOutput(
            risk_score=20,
            risk_level="high",
            triggered_signals=[
                MaliciousSignal(
                    signal_type="review_blackmail",
                    description="差评+12315要挟仅退款",
                    score=20,
                    source="llm_semantic",
                )
            ],
            disposition_advice="建议对外协商降格沟通并守住赔偿边界。",
        )
        facts = FactOutput(
            issue_summary="袖口开线要求仅退款",
            defect_type="无",
            evidence_quality="high",
            missing_evidence=[],
            goods_received=True,
        )
        input_data = StrategyInput(
            facts=facts,
            buyer_profile=BuyerProfile(buyer_id="buyer_ma01", purchase_count=6, dispute_rate=0.17),
            matched_rules=[],
            order_amount=128.0,
            chat_history=["要么差评要么投诉12315，今天必须仅退款"],
        )
        output = recommend(_with_precomputed(input_data, malicious_detection=high_pressure))
        assert output.disposition == DISPOSITION_NEGOTIATE
        assert output.action_type in {ACTION_RULE_EXPLAIN, ACTION_MONETARY_SETTLE}
        assert output.action_type != ACTION_DEFEND_PREPARE


class TestAgent2Tools:
    """规则匹配、价值评估、恶意检测、条文门控。"""

    def test_match_rules_should_score_articles_from_plan(self, monkeypatch):
        """基于 rule_match_plan 执行条文匹配。"""
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
                        "content": "买家主张花屏、闪屏等情形应举证。",
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
        facts = FactOutput(
            goods_received=True,
            defect_type="划痕",
            evidence_quality="low",
            rule_match_plan=RuleMatchPlan(
                target_doc_ids=[base_doc_id, phone_doc_id],
                section_selections=[
                    SectionSelection(doc_id=base_doc_id, section_keys=[], confidence=0.9, reason=""),
                    SectionSelection(doc_id=phone_doc_id, section_keys=[], confidence=0.9, reason=""),
                ],
                search_terms=RuleSearchTerms(
                    must_terms=["商品质量问题", "初步凭证"],
                    should_terms=["表面不一致"],
                    case_terms=["划痕"],
                    exclude_terms=[],
                ),
            ),
        )
        result = match_rules_from_facts(facts)
        assert result.display_rules
        assert any(r.relevance == "must" for r in result.matched_rules)

    def test_match_rules_empty_without_plan(self):
        """无 target_doc_ids 时不匹配。"""
        facts = FactOutput(goods_received=True, defect_type="破洞", evidence_quality="high")
        assert match_rules_from_facts(facts).display_rules == []

    def test_display_rules_prioritize_category_over_base(self):
        """代表条优先展示品类专项规则。"""
        from backend.tools.rule_matcher import _pick_display_rules

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
                rule_summary="生鲜腐烂需在签收48小时内举证",
                condition_result="",
                relevance=RULE_RELEVANCE_MUST,
                doc_id=fresh_doc,
            ),
        ]
        picked = _pick_display_rules(pool)
        assert picked[0].doc_id == fresh_doc

    def test_rule_constraints_should_capture_timing_and_ratio_limit(self):
        """规则匹配产出时效与补偿比例结构化约束。"""
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
            item for item in constraints
            if item.constraint_type == RULE_CONSTRAINT_TIMING and item.status == "violated"
        ]
        assert timing_violated
        assert any(
            item.constraint_type == RULE_CONSTRAINT_RATIO_LIMIT and "30%" in item.text
            for item in constraints
        )

    def test_needs_rule_match_gate(self):
        """简单案跳过条文匹配；通道/恶意/价值/多诉求触发。"""
        base_facts = FactOutput(issue_summary="屏幕划痕", intent_tags=["质量问题"], evidence_quality="high")
        base_malicious = MaliciousDetectionOutput(risk_level="low")
        base_value = CustomerValueOutput(channel="none")
        assert not needs_rule_match(base_facts, base_malicious, base_value)

        category_facts = FactOutput(
            issue_summary="批量试穿后要求退货",
            intent_tags=["七天无理由退货"],
            rule_match_plan=RuleMatchPlan(
                activated_lanes=["A", "C"],
                target_doc_ids=["特殊品类争议处理_淘宝平台服饰类商品争议处理规范_1155_11003625"],
            ),
        )
        assert needs_rule_match(category_facts, base_malicious, base_value)

        assert needs_rule_match(
            base_facts,
            MaliciousDetectionOutput(risk_level="medium"),
            base_value,
        )
        multi_intent = FactOutput(issue_summary="既要退款又投诉物流", intent_tags=["质量问题", "物流异常"])
        assert needs_rule_match(multi_intent, base_malicious, base_value)

    def test_evaluate_customer_value_should_trigger_long_term_channel(self):
        """高长期价值触发老客优待通道。"""
        result = evaluate_customer_value(
            CustomerValueInput(
                buyer_profile=BuyerProfile(
                    buyer_id="buyer_loyal",
                    purchase_count=20,
                    dispute_rate=0.02,
                    avg_order_value=180.0,
                    positive_review_count=6,
                ),
                order_amount=120.0,
                defect_severity="minor",
                goods_recoverability="resalable",
                has_visual_loss_exposure=True,
            )
        )
        assert result.long_term_triggered is True
        assert result.channel == "long_term"

    def test_detect_malicious_deceptive_credential_should_be_high(self, monkeypatch):
        """举证可疑时命中虚假举证硬规则。"""
        monkeypatch.delenv("AGENT2_LLM_MODEL_MALICIOUS", raising=False)
        result = detect_malicious_behavior(
            MaliciousDetectionInput(
                buyer_profile=BuyerProfile(buyer_id="buyer_new", purchase_count=0, return_rate=0.0),
                facts=FactOutput(
                    evidence_quality="medium",
                    credential_trust="suspect",
                    credential_trust_note="图片角落可见批发图水印",
                    red_flags=["图文来源可疑"],
                ),
                order_amount=199.0,
                chat_history=[],
            )
        )
        assert any(item.signal_type == "deceptive_credential" for item in result.triggered_signals)
        assert result.risk_level == "high"

    def test_detect_malicious_behavior_should_merge_semantic_signals(self, monkeypatch):
        """语义层结构化信号参与综合评分。"""
        monkeypatch.setenv("AGENT2_LLM_MODEL_MALICIOUS", "mock-model")
        monkeypatch.setattr(
            agent2_tools_module,
            "chat_completion",
            lambda **kwargs: (
                '[{"signal_type":"review_blackmail","description":"差评勒索","score":20,"source":"llm_semantic"}]'
            ),
        )
        result = detect_malicious_behavior(
            MaliciousDetectionInput(
                buyer_profile=BuyerProfile(buyer_id="buyer_semantic", return_rate=0.05),
                facts=FactOutput(evidence_quality="medium", red_flags=[]),
                order_amount=99.0,
                chat_history=["不给赔偿我就差评并投诉你们店"],
            )
        )
        assert result.risk_level == "high"
        assert any(item.signal_type == "review_blackmail" for item in result.triggered_signals)
