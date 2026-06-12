"""
Agent 2：策略参谋员。

职责：在既定输入（事实、规则命中、画像、判例）上做加权决策；本模块不读库、不调 HTTP。
"""

from __future__ import annotations

from typing import Any, Callable, List
import json
import logging
import re
import time

from schemas import (
    ACTION_DEFEND_PREPARE,
    ACTION_EVIDENCE_REQUEST,
    ACTION_MERCHANT_REMEDY,
    ACTION_MONETARY_SETTLE,
    ACTION_RETURN_INSPECTION,
    ACTION_RULE_EXPLAIN,
    COMPENSATION_POLICY_EXPLICIT_AMOUNT,
    COMPENSATION_POLICY_FORBID,
    COMPENSATION_POLICY_NONE,
    COMPENSATION_POLICY_SOFT_NO_AMOUNT,
    DialogueContext,
    MaliciousDetectionOutput,
    CustomerValueOutput,
    RULE_CONSTRAINT_APPLIES,
    RULE_CONSTRAINT_EVIDENCE,
    RULE_CONSTRAINT_MISSING_FACT,
    RULE_CONSTRAINT_RATIO_LIMIT,
    RULE_CONSTRAINT_TIMING,
    RULE_CONSTRAINT_VIOLATED,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    DISPUTE_FRAME_SEVEN_DAY_RETURN,
    DISPUTE_FRAME_UNKNOWN,
    FRAMES_NO_MONETARY_SETTLE,
    STRATEGY_STAGE_EVIDENCE_FIRST,
    StrategyInput,
    StrategyOutput,
)
from backend.agents.agent2.evidence_readiness import (
    assess_evidence_readiness,
    build_evidence_risk_notes,
    buyer_facing_missing_evidence,
    compose_evidence_stage_next_step,
    derive_blocked_evidence_requests,
    filter_actionable_evidence_requests,
)
from backend.tools.llm_client import chat_completion
from backend.tools.text_signals import contains_any, signal_group


AGENT2_LOG_PREFIX = "[Agent2]"
logger = logging.getLogger(__name__)

# ---------- 策略 LLM payload 截断与模型分级 ----------
STRATEGY_RECENT_TURNS_MAX = 8
STRATEGY_VISUAL_OBS_MAX = 3
STRATEGY_RED_FLAGS_MAX = 4
STRATEGY_MODEL_ENV = "AGENT2_LLM_MODEL_STRATEGY"

# ---------- 策略参谋核心目标（全链路提示词共用） ----------
_MERCHANT_INTEREST_GOAL = (
    "目标：在道德、平台规则与法律边界内，帮助商家分阶段争取最优结果（本单损益 + 客户长期价值 + 口碑与升级风险）。"
    "规则或高风险恶意要求补证时优先固定证据链；其余缺证写入风险说明并推进协商、善后或合理拒赔，禁止无依据跳过举证直接承诺退款。"
)


def _polish_merchant_facing_text(text: str) -> str:
    """去除条号/章节等内部引用，使文案更适合商家阅读。"""
    if not text:
        return text
    cleaned = str(text)
    for marker in ("规则解读：", "规则解读:", "规则解读", "本规范于", "生效时间", "最新修订"):
        idx = cleaned.find(marker)
        if idx >= 0:
            cleaned = cleaned[:idx].strip()
            break
    cleaned = re.sub(r"第[一二三四五六七八九十百千零\d]+条", "", cleaned)
    cleaned = re.sub(r"第[一二三四五六七八九十]+节[^，。；\n]*", "", cleaned)
    cleaned = re.sub(r"[（(]篇首[）)]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" 。；，,")
    return cleaned.strip()


def _build_platform_rule_basis(input_data: StrategyInput) -> List[str]:
    """
    平台规则依据：仅展示条文匹配结果中的法条摘要（matched_rules.rule_summary）。

    不写入 rule_constraints（举证模板、LLM strategy_constraints 短句等执行约束），避免与法条混排。
    无命中展示条时返回空列表，不回退 brief 或策略 LLM 自造要点。
    """
    lines: List[str] = []
    seen: set[str] = set()
    for rule in input_data.matched_rules or []:
        text = _polish_merchant_facing_text(str(getattr(rule, "rule_summary", "") or ""))
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
    return lines


def _build_rule_constraints(input_data: StrategyInput) -> List[str]:
    """
    将命中的规则摘要提炼为 Agent3 必须遵守的边界。

    这里不生成固定话术，只把已命中的规则要点作为约束传下去。
    """
    constraints: List[str] = []
    seen: set[str] = set()
    for item in input_data.rule_constraints or []:
        text = _polish_merchant_facing_text(str(item.text or "").strip())
        if text and text not in seen:
            seen.add(text)
            constraints.append(text)
    for text in _derive_return_rule_risk_constraints(input_data, constraints):
        if text and text not in seen:
            seen.add(text)
            constraints.append(text)
    return constraints


def _has_structured_constraint(
    input_data: StrategyInput,
    *,
    constraint_type: str | None = None,
    statuses: set[str] | None = None,
) -> bool:
    """判断是否存在指定类型/状态的结构化规则约束。"""
    for item in input_data.rule_constraints or []:
        if constraint_type and item.constraint_type != constraint_type:
            continue
        if statuses and item.status not in statuses:
            continue
        return True
    return False


def _structured_constraint_texts(
    input_data: StrategyInput,
    *,
    constraint_type: str | None = None,
    statuses: set[str] | None = None,
    limit: int = 3,
) -> List[str]:
    """提取结构化约束文本，供 next_step 与 prompt 使用。"""
    texts: List[str] = []
    for item in input_data.rule_constraints or []:
        if constraint_type and item.constraint_type != constraint_type:
            continue
        if statuses and item.status not in statuses:
            continue
        text = _polish_merchant_facing_text(str(item.text or "").strip())
        if text and text not in texts:
            texts.append(text)
        if len(texts) >= limit:
            break
    return texts


def _primary_timing_constraint_text(input_data: StrategyInput) -> str:
    """优先取已违反的时效约束，其次取待核验约束，避免策略动作重复罗列多个时效。"""
    violated = _structured_constraint_texts(
        input_data,
        constraint_type=RULE_CONSTRAINT_TIMING,
        statuses={RULE_CONSTRAINT_VIOLATED},
        limit=1,
    )
    if violated:
        return violated[0]
    missing = _structured_constraint_texts(
        input_data,
        constraint_type=RULE_CONSTRAINT_TIMING,
        statuses={RULE_CONSTRAINT_MISSING_FACT},
        limit=1,
    )
    return missing[0] if missing else ""


def _primary_dispute_frame(input_data: StrategyInput) -> str:
    """读取 Agent1 已判定的主争议框架，下游禁止再解析服务标或 intent 文案。"""
    frame = str(input_data.facts.primary_dispute_frame or "").strip()
    return frame if frame else DISPUTE_FRAME_UNKNOWN


def _derive_return_rule_risk_constraints(input_data: StrategyInput, base_constraints: List[str]) -> List[str]:
    """
    服务退货/完好争议框架下，将规则上位表述转为可执行话术约束（仅读 primary_dispute_frame）。
    """
    facts = input_data.facts
    if _primary_dispute_frame(input_data) != DISPUTE_FRAME_SEVEN_DAY_RETURN:
        return []
    intent_blob = " ".join(str(tag) for tag in (facts.intent_tags or []))
    constraint_blob = " ".join(str(item) for item in base_constraints if item)
    combined = f"{constraint_blob} {intent_blob}"
    if not contains_any(combined, signal_group("intact_return_markers")):
        return []
    return [
        "退货退款成立前提是商品完好且不影响二次销售，商家收到退货后需严格验收",
        "如退回商品存在使用痕迹、污损异味、吊牌包装异常或其他影响二次销售情形，可按规则不予退款",
        "退货运费及验收不通过后的寄回运费与商品风险，应提前向买家说明并按平台规则或订单约定处理",
    ]


def _analyze_rule_stance(input_data: StrategyInput) -> tuple[List[str], str, int]:
    """
    解析规则命中站位，输出规则引用、站位方向与命中数。
    """
    merchant_support = 0
    buyer_support = 0
    policy_refs: List[str] = []
    briefs = input_data.rule_briefs or []
    rules_for_stance = briefs if briefs else input_data.matched_rules
    for rule in rules_for_stance:
        if hasattr(rule, "stance_hint"):
            stance = str(getattr(rule, "stance_hint", "") or "").lower()
            summary = str(getattr(rule, "brief", "") or getattr(rule, "rule_summary", ""))
            ref = str(getattr(rule, "article_ref", "") or getattr(rule, "rule_id", ""))
        else:
            stance = str(getattr(rule, "stance_hint", "") or "").lower()
            summary = str(getattr(rule, "rule_summary", ""))
            ref = str(getattr(rule, "rule_id", ""))
        policy_refs.append(ref)
        combined_text = f"{summary} {getattr(rule, 'condition_result', '')}".lower()
        if stance == "merchant" or "支持打款" in combined_text:
            merchant_support += 1
        elif stance == "buyer" or "支持买家" in combined_text:
            buyer_support += 1
        elif "建议策略:defend" in combined_text or "抗辩" in combined_text:
            merchant_support += 1
        elif "建议策略:compensate" in combined_text or ("退款" in combined_text and "支持买家" in combined_text):
            buyer_support += 1
    hit_count = len(rules_for_stance)
    if merchant_support > buyer_support:
        return policy_refs, "merchant", hit_count
    if buyer_support > merchant_support:
        return policy_refs, "buyer", hit_count
    return policy_refs, "neutral", hit_count


def _value_risks_from_result(customer_value: CustomerValueOutput) -> List[str]:
    """
    根据客户价值通道生成策略层风险提示条目。
    """
    layer_risks: List[str] = []
    if customer_value.channel == "long_term":
        layer_risks.append("[客户价值层] 触发长期优待通道，可在规则内兼顾关系维护，但不得跳过事实核验")
    elif customer_value.channel == "order":
        layer_risks.append("[客户价值层] 触发本单重点处理，需快速响应并优先核实事实、控制损失")
    return layer_risks


def _merchant_fault_signal(input_data: StrategyInput, rule_stance: str) -> bool:
    """
    规则站位偏买家且已有足够视觉/高质量证据时，视为商责压力明确。

    中等证据但已有视觉观察结论时与高质量同等触发善后通道，避免仅有照片仍卡在纯补证。
    """
    if rule_stance != "buyer":
        return False
    facts = input_data.facts
    evidence_quality = (facts.evidence_quality or "").strip().lower()
    if evidence_quality == "high":
        return True
    return evidence_quality == "medium" and bool(facts.visual_observations or [])


def _has_defect_claim(defect_type: str | None) -> bool:
    """
    判断事实层是否真的声明了质量/瑕疵问题。

    Agent1/测试用例会用「无」「暂无」「无质量问题」等自然语言表示
    没有瑕疵；这些都不应触发质量瑕疵补证阶段。
    """
    normalized = str(defect_type or "").strip()
    if not normalized:
        return False
    negative_values = {"无", "暂无", "无瑕疵", "无质量问题", "无明显瑕疵", "没有瑕疵"}
    return normalized not in negative_values


def _infer_strategy_stage(
    input_data: StrategyInput,
    *,
    disposition: str,
    merchant_fault_signal: bool,
    blocks_decision: bool,
) -> str:
    """
    推断当前应处的策略阶段，供 LLM 生成「当下这一步」而非终局方案。

    返回:
        evidence_first | negotiate_settle | compensate_close | defend_platform
    """
    if blocks_decision:
        return "evidence_first"
    if disposition == DISPOSITION_COMPENSATE and merchant_fault_signal:
        return "compensate_close"
    if disposition == DISPOSITION_DEFEND:
        return "defend_platform"
    return "negotiate_settle"


def _compose_evidence_first_next_step(input_data: StrategyInput) -> str:
    """生成举证优先阶段的下一步（排除已拒证与不可执行项）。"""
    return compose_evidence_stage_next_step(
        facts=input_data.facts,
        chat_turns=input_data.chat_turns or [],
    )


def _infer_action_contract(
    input_data: StrategyInput,
    *,
    disposition: str,
    strategy_stage: str,
    rule_stance: str,
    merchant_fault_signal: bool,
    rule_constraints: List[str],
    malicious_result: MaliciousDetectionOutput,
) -> dict[str, Any]:
    """
    将粗粒度处置方向细化为 Agent3 可执行的当前动作契约。
    """
    facts = input_data.facts
    intent_blob = " ".join(str(tag) for tag in (facts.intent_tags or []))
    constraints_blob = " ".join(str(item) for item in rule_constraints if item)
    return_service_frame = _primary_dispute_frame(input_data) == DISPUTE_FRAME_SEVEN_DAY_RETURN
    return_flow_context = return_service_frame or contains_any(
        constraints_blob, ("退货", "寄回", "验收")
    )

    timing_not_satisfied = _has_structured_constraint(
        input_data,
        constraint_type=RULE_CONSTRAINT_TIMING,
        statuses={RULE_CONSTRAINT_VIOLATED, RULE_CONSTRAINT_MISSING_FACT},
    )
    evidence_constraint = _has_structured_constraint(
        input_data,
        constraint_type=RULE_CONSTRAINT_EVIDENCE,
        statuses={RULE_CONSTRAINT_APPLIES, RULE_CONSTRAINT_MISSING_FACT},
    )
    ratio_limit_texts = _structured_constraint_texts(
        input_data,
        constraint_type=RULE_CONSTRAINT_RATIO_LIMIT,
        statuses={RULE_CONSTRAINT_APPLIES},
        limit=2,
    )

    if timing_not_satisfied:
        action_type = ACTION_RULE_EXPLAIN
        compensation_policy = COMPENSATION_POLICY_NONE
        timing_text = _primary_timing_constraint_text(input_data)
        next_step = timing_text or "先向买家说明规则时效与处理边界，再核验保存方式和举证材料"
    elif strategy_stage == STRATEGY_STAGE_EVIDENCE_FIRST:
        action_type = ACTION_EVIDENCE_REQUEST
        compensation_policy = COMPENSATION_POLICY_FORBID
        next_step = _compose_evidence_first_next_step(input_data)
    elif disposition == DISPOSITION_COMPENSATE and merchant_fault_signal:
        action_type = ACTION_MERCHANT_REMEDY
        compensation_policy = COMPENSATION_POLICY_EXPLICIT_AMOUNT
        next_step = "商家给出明确的退款、退货、换货、补发或补偿处理方案，并请买家确认"
    elif (
        _primary_dispute_frame(input_data) in FRAMES_NO_MONETARY_SETTLE
        and not merchant_fault_signal
    ):
        action_type = ACTION_RULE_EXPLAIN
        compensation_policy = COMPENSATION_POLICY_NONE
        if contains_any(f"{constraints_blob} {intent_blob}", signal_group("intact_return_markers")):
            next_step = "引导买家按退货流程寄回，商家收到后严格验收，并提前告知验收不通过和运费风险"
        else:
            next_step = "先向买家说明规则边界和退货验收流程，再根据买家反馈推进寄回与验收"
    elif disposition == DISPOSITION_DEFEND or (malicious_result.risk_level or "").lower() == "high":
        action_type = ACTION_DEFEND_PREPARE
        compensation_policy = COMPENSATION_POLICY_NONE
        next_step = "按规则说明当前不满足直接退款或补偿条件，并整理聊天、订单、物流和举证材料以备平台介入"
    elif rule_constraints and (
        rule_stance in {"neutral", "merchant"} or evidence_constraint
    ):
        if return_flow_context:
            action_type = ACTION_RULE_EXPLAIN
            if return_service_frame and contains_any(
                f"{constraints_blob} {intent_blob}",
                signal_group("intact_return_markers"),
            ):
                next_step = "引导买家按退货流程寄回，商家收到后严格验收，并提前告知验收不通过和运费风险"
            else:
                next_step = "引导买家按退货流程寄回，商家收到后按规则验收并根据结果处理"
        else:
            action_type = ACTION_RULE_EXPLAIN
            next_step = "先向买家说明规则边界和处理流程，再根据买家反馈进入补证、验收或协商"
        compensation_policy = COMPENSATION_POLICY_NONE
    elif disposition == DISPOSITION_NEGOTIATE:
        if _primary_dispute_frame(input_data) in FRAMES_NO_MONETARY_SETTLE and not merchant_fault_signal:
            action_type = ACTION_RULE_EXPLAIN
            compensation_policy = COMPENSATION_POLICY_NONE
            next_step = "先向买家说明规则边界和退货验收流程，再根据买家反馈推进寄回与验收"
        else:
            action_type = ACTION_MONETARY_SETTLE
            compensation_policy = COMPENSATION_POLICY_EXPLICIT_AMOUNT
            next_step = "在规则允许范围内给出明确金额或具体方案，并征求买家是否接受"
    else:
        action_type = ACTION_RETURN_INSPECTION
        compensation_policy = COMPENSATION_POLICY_SOFT_NO_AMOUNT
        next_step = "按退回验收流程推进，结果确认前不承诺最终退款或补偿"

    resolved_constraints = list(rule_constraints)
    for ratio_text in ratio_limit_texts:
        if ratio_text not in resolved_constraints:
            resolved_constraints.append(ratio_text)
    if action_type in {ACTION_RULE_EXPLAIN, ACTION_RETURN_INSPECTION}:
        no_promise = "未达到规则前提或完成验收前，不承诺退款或补偿"
        if no_promise not in resolved_constraints:
            resolved_constraints.append(no_promise)
    if action_type == ACTION_EVIDENCE_REQUEST:
        no_promise = "举证未闭环前，不承诺退款、补偿、优惠券或换新"
        if no_promise not in resolved_constraints:
            resolved_constraints.append(no_promise)

    return {
        "action_type": action_type,
        "compensation_policy": compensation_policy,
        "rule_constraints": resolved_constraints,
        "next_step": next_step,
    }


def _determine_disposition(
    input_data: StrategyInput,
    *,
    malicious_result,
    customer_value,
    rule_stance: str,
) -> str:
    """
    单链路处置方向：高风险恶意→抗辩；规则+高证据明确商责→善后；其余→协商（含举证未闭环）。

    举证未闭环不改变 disposition，由 strategy_stage=evidence_first 约束「当下先补证」。
    """
    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    merchant_fault_signal = _merchant_fault_signal(input_data, rule_stance)

    if malicious_result.risk_level == "high":
        return DISPOSITION_DEFEND
    if malicious_result.risk_level == "medium" and merchant_fault_signal:
        return DISPOSITION_NEGOTIATE
    if malicious_result.risk_level == "medium" and evidence_quality == "low" and not merchant_fault_signal:
        return DISPOSITION_DEFEND
    if merchant_fault_signal and malicious_result.risk_level == "low":
        return DISPOSITION_COMPENSATE
    if customer_value.channel in {"long_term", "order"} and malicious_result.risk_level == "low":
        return DISPOSITION_NEGOTIATE
    return DISPOSITION_NEGOTIATE


def _normalize_win_rate(raw_rate: float) -> float:
    """
    胜率裁剪到 [0.05, 0.95]，避免极值。
    """
    return max(0.05, min(0.95, round(raw_rate, 3)))


def _count_supportive_cases(input_data: StrategyInput) -> int:
    """
    统计支持抗辩方向的相似判例数（similarity >= 0.6）。
    """
    count = 0
    for case in input_data.similar_cases:
        if case.similarity < 0.6:
            continue
        outcome_text = case.outcome.lower()
        action_text = case.merchant_action.lower()
        if "支持商家" in outcome_text or "抗辩" in action_text:
            count += 1
    return count


def _estimate_win_rate(
    input_data: StrategyInput,
    *,
    disposition: str,
    malicious_result,
    rule_stance: str,
    rule_count: int,
) -> float | None:
    """
    仅在抗辩方向计算胜率（平台支持商家概率）。
    """
    if disposition != DISPOSITION_DEFEND:
        return None

    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    missing_count = len(input_data.facts.missing_evidence)
    merchant_fault_signal = _merchant_fault_signal(input_data, rule_stance)

    # 规则轴
    if rule_stance == "merchant" and rule_count >= 2:
        rule_score = 0.40
    elif rule_stance == "merchant" and rule_count == 1:
        rule_score = 0.28
    else:
        rule_score = 0.16

    # 证据轴
    if evidence_quality == "high" and missing_count == 0:
        evidence_score = 0.25
    elif evidence_quality == "medium" or (evidence_quality == "high" and missing_count <= 1):
        evidence_score = 0.16
    else:
        evidence_score = 0.07

    # 强信号轴
    if malicious_result.risk_level == "high":
        signal_score = 0.20
    elif malicious_result.risk_level == "medium" and evidence_quality == "low":
        signal_score = 0.14
    elif malicious_result.risk_level == "medium":
        signal_score = 0.10
    else:
        signal_score = 0.06
    if merchant_fault_signal:
        signal_score = max(0.0, signal_score - 0.08)

    # 判例轴
    supportive_count = _count_supportive_cases(input_data)
    if supportive_count >= 2:
        case_score = 0.15
    elif supportive_count == 1:
        case_score = 0.09
    else:
        case_score = 0.04

    return _normalize_win_rate(rule_score + evidence_score + signal_score + case_score)


def _estimate_confidence(
    input_data: StrategyInput,
    *,
    malicious_result,
    rule_stance: str,
    rule_count: int,
) -> float:
    """
    置信度：规则确定性 + 证据充分性 + 信号一致性。
    简单案跳过条文匹配（rule_match_skipped）且无命中时，规则维按 0.30 计。
    """
    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    missing_count = len(input_data.facts.missing_evidence)
    merchant_fault_signal = _merchant_fault_signal(input_data, rule_stance)

    # 维度一：规则确定性
    if rule_count >= 1 and rule_stance in {"merchant", "buyer"}:
        dim_rule = 0.40
    elif rule_count >= 1:
        dim_rule = 0.20
    elif input_data.rule_match_skipped:
        dim_rule = 0.30
    else:
        dim_rule = 0.08

    # 维度二：证据充分性
    if evidence_quality == "high" and missing_count == 0:
        dim_evidence = 0.35
    elif evidence_quality == "medium" or (evidence_quality == "high" and missing_count <= 1):
        dim_evidence = 0.20
    else:
        dim_evidence = 0.07

    # 维度三：信号一致性
    if malicious_result.risk_level in {"high", "medium"} and merchant_fault_signal:
        dim_consistency = 0.05
    elif rule_stance == "merchant" and _count_supportive_cases(input_data) == 0 and input_data.similar_cases:
        dim_consistency = 0.13
    else:
        dim_consistency = 0.25

    confidence = dim_rule + dim_evidence + dim_consistency
    if malicious_result.risk_level in {"high", "medium"} and merchant_fault_signal:
        confidence = min(confidence, 0.65)
    return max(0.10, min(0.95, round(confidence, 3)))


def _has_major_signal_conflict(input_data: StrategyInput, *, malicious_result, rule_stance: str) -> bool:
    """
    判断是否存在恶意风险与商责明确信号的主要矛盾。
    """
    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    merchant_fault_signal = _merchant_fault_signal(input_data, rule_stance)
    return malicious_result.risk_level in {"high", "medium"} and merchant_fault_signal


def _infer_customer_intent(input_data: StrategyInput) -> str:
    """
    基于事实与画像推断买家主诉意图标签。
    """
    facts = input_data.facts
    if facts.goods_received is False:
        return "物流履约争议"
    if facts.defect_type and facts.defect_type != "无瑕疵":
        return "质量问题维权"
    if facts.missing_evidence:
        return "证据不足但诉求明确"
    return "常规售后沟通"


def _compose_action_direction_summary(action_contract: dict[str, Any], fallback: str) -> str:
    """
    用动作契约修正兜底策略方向，避免 rule_explain 被泛化成金额协商。
    """
    action_type = str(action_contract.get("action_type") or "").strip()
    next_step = str(action_contract.get("next_step") or "").strip()
    if action_type == ACTION_RULE_EXPLAIN:
        return next_step or "先向买家说明规则前提和处理流程，后续按买家反馈进入补证、验收或协商。"
    if action_type == ACTION_RETURN_INSPECTION:
        return next_step or "先按退回验收流程推进，确认结果前不承诺最终退款或补偿。"
    if action_type == ACTION_EVIDENCE_REQUEST:
        return next_step or "先要求买家补证并补齐关键举证，材料闭环前不承诺退款或补偿。"
    if action_type == ACTION_DEFEND_PREPARE:
        return next_step or "按规则说明当前不支持直接退款或补偿，同步整理证据准备平台介入。"
    if action_type == ACTION_MERCHANT_REMEDY:
        return next_step or "商责已基本明确，给出一条可执行的善后处理方案并请买家确认。"
    if action_type == ACTION_MONETARY_SETTLE:
        return fallback
    return fallback


def _build_strategy_json_system_prompt() -> str:
    """
    构造策略 JSON LLM 的 system 提示，统一注入商户利益最大化与分阶段决策约束。
    """
    return (
        "你是资深电商客服策略参谋。"
        f"{_MERCHANT_INTEREST_GOAL}"
        "请输出单个 JSON 对象，字段如下：\n"
        "{\n"
        '  "customer_intent_analysis": "面向商家的客户意图分析",\n'
        '  "strategy_direction_summary": "当前这一步的单一路径动作（1～2句）",\n'
        '  "strategy_direction_rationale": "2～4句：结合本案事实与已匹配规则要点，向商家说明为何采取当前动作；口语化概括，禁止粘贴条文原文、条号或长段规则摘录",\n'
        '  "risk_factors": ["风险点1"],\n'
        '  "dialogue_context": {\n'
        '    "dialogue_mode": "continue|cold_start",\n'
        '    "blocked_evidence_requests": ["买家已拒举证项"],\n'
        '    "actionable_evidence_requests": ["仍可请求的替代举证"],\n'
        "  }\n"
        "}\n"
        "系统会在输入中提供 action_type、compensation_policy、rule_constraints、next_step；"
        "structured_rule_constraints 是已校验的规则条件约束，优先级高于 rule_briefs 摘要；"
        "你的策略说明必须与这些动作契约一致，不得把 rule_explain / return_inspection / evidence_request 写成金额和解。"
        "strategy_direction_summary 只能写面向商家的当前处理方向。"
        "strategy_direction_rationale 是案情分析+规则要点的推理说明（给商家看）。"
        "只可概括规则如何约束本案（如时效、举证、验收），禁止复述 rule_briefs/rule_constraints/platform_rule_basis 原文。"
        "若 evidence_blocks_decision=true（strategy_stage=evidence_first）：strategy_direction_summary 只能要求补证、固定己方证据，禁止先给退款/补偿方案。"
        "若 evidence_blocks_decision=false：非阻断缺证已在 risk_factors，不得再主导为纯补证策略。"
        "若 compensation_policy=none/forbid/soft_no_amount：不得建议报具体补偿金额。"
        "若 dialogue_context.blocked_evidence_requests 非空：不得再要求其中任何一项。"
        "若 recent_turns 非空：须承接对话，禁止重复商家已提且买家已拒的举证要求。"
        "平台规则依据由系统从 rule_briefs 另行注入，勿在 JSON 中输出 platform_rule_basis。"
        "若 rule_briefs 为空：禁止引用或编造具体平台条款，策略仅基于 facts、对话与画像。"
    )


def _parse_strategy_json(raw_text: str) -> dict[str, Any] | None:
    """
    解析策略 LLM 输出的 JSON 对象。
    """
    normalized = (raw_text or "").strip()
    if normalized.startswith("```"):
        normalized = normalized.replace("```json", "").replace("```", "").strip()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_dialogue_context(raw: Any, input_data: StrategyInput) -> DialogueContext:
    """
    校验并规范化 dialogue_context；缺字段时用事实字段兜底。
    """
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("dialogue_mode") or "").strip()
    if mode not in {"continue", "cold_start"}:
        mode = "continue" if (input_data.chat_turns or []) else "cold_start"

    def _as_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    chat_blocked = derive_blocked_evidence_requests(input_data.chat_turns or [])
    blocked = list(
        dict.fromkeys(_as_list(data.get("blocked_evidence_requests")) + chat_blocked)
    )
    llm_actionable = _as_list(data.get("actionable_evidence_requests"))
    actionable = filter_actionable_evidence_requests(
        llm_actionable or buyer_facing_missing_evidence(input_data.facts),
        blocked,
    )

    fallback = str(data.get("fallback_script") or "").strip()
    if not fallback:
        turns = input_data.chat_turns or []
        fallback = "我这边还在核对材料，核实完马上回您。" if turns else "您好，我这边还在核对材料，核实完马上回您。"

    return DialogueContext(
        dialogue_mode=mode,
        blocked_evidence_requests=blocked,
        actionable_evidence_requests=actionable,
        fallback_script=fallback,
    )


def _compose_reasoning_from_fields(
    *,
    customer_intent_analysis: str,
    risk_factors: List[str],
    strategy_direction_summary: str,
    strategy_direction_rationale: str,
) -> str:
    """
    将 JSON 字段拼接为 reasoning 文本，供流式展示与日志兼容。
    """
    risk_text = "；".join(risk_factors[:3]) if risk_factors else "当前未识别到高风险项"
    return (
        f"客户意图：{customer_intent_analysis}。\n"
        f"风险点：{risk_text}。\n"
        f"建议动作：{strategy_direction_summary}\n"
        f"推理理由：{strategy_direction_rationale}"
    )


def _build_strategy_facts_summary(input_data: StrategyInput) -> dict[str, Any]:
    """
    构造策略 LLM 用 facts 摘要：去掉 evidence_items、rule_match_plan 与无用兼容字段。
    """
    facts = input_data.facts
    summary: dict[str, Any] = {
        "issue_summary": facts.issue_summary,
        "intent_tags": list(facts.intent_tags or []),
        "visual_observations": list((facts.visual_observations or [])[:STRATEGY_VISUAL_OBS_MAX]),
        "defect_type": facts.defect_type,
        "defect_location": facts.defect_location,
        "goods_received": facts.goods_received,
        "logistics_normal": facts.logistics_normal,
        "missing_evidence": list(facts.missing_evidence or []),
        "red_flags": list((facts.red_flags or [])[:STRATEGY_RED_FLAGS_MAX]),
        "evidence_quality": facts.evidence_quality,
        "credential_trust": facts.credential_trust,
    }
    if facts.credential_trust_note:
        summary["credential_trust_note"] = str(facts.credential_trust_note).strip()
    if facts.attributes:
        summary["attributes"] = facts.attributes
    if facts.uncertainty_note and str(facts.uncertainty_note).strip():
        summary["uncertainty_note"] = str(facts.uncertainty_note).strip()
    return summary


def _build_strategy_buyer_profile_summary(input_data: StrategyInput) -> dict[str, Any]:
    """
    构造策略 LLM 用买家画像摘要：去掉无策略意义的 buyer_id。
    """
    profile = input_data.buyer_profile.model_dump()
    profile.pop("buyer_id", None)
    return profile


def _build_strategy_rule_briefs_payload(input_data: StrategyInput) -> list[dict[str, Any]]:
    """
    仅传策略 LLM 写推理所需的 brief 字段（展示用 platform_rule_basis 由条文匹配结果单独注入）。
    """
    briefs: list[dict[str, Any]] = []
    for item in input_data.rule_briefs or []:
        briefs.append(
            {
                "brief": item.brief,
                "relevance": item.relevance,
                "stance_hint": item.stance_hint,
                "strategy_constraints": list(item.strategy_constraints or []),
            }
        )
    return briefs


def _build_strategy_prompt_payload(
    *,
    disposition: str,
    strategy_stage: str,
    compensation_policy: str,
    action_type: str = "",
    rule_constraints: List[str] | None = None,
    next_step: str = "",
    evidence_blocks_decision: bool,
    merchant_fault_signal: bool,
    input_data: StrategyInput,
    risk_factors: List[str],
    estimated_win_rate: float | None,
) -> dict[str, Any]:
    """
    组装精简后的策略 LLM user payload（Grill #1-C）。
    """
    payload: dict[str, Any] = {
        "disposition": disposition,
        "strategy_stage": strategy_stage,
        "action_type": action_type,
        "compensation_policy": compensation_policy,
        "rule_constraints": list(rule_constraints or []),
        "structured_rule_constraints": [
            item.model_dump() for item in (input_data.rule_constraints or [])
        ],
        "next_step": next_step,
        "evidence_blocks_decision": evidence_blocks_decision,
        "merchant_fault_clear": merchant_fault_signal,
        "facts": _build_strategy_facts_summary(input_data),
        "buyer_profile": _build_strategy_buyer_profile_summary(input_data),
        "rule_briefs": _build_strategy_rule_briefs_payload(input_data),
        "risk_factors": risk_factors,
        "order_amount": input_data.order_amount,
        "estimated_win_rate": estimated_win_rate,
    }
    if input_data.similar_cases:
        payload["similar_cases"] = [item.model_dump() for item in input_data.similar_cases]
    turns = input_data.chat_turns or []
    if turns:
        payload["recent_turns"] = [t.model_dump() for t in turns[-STRATEGY_RECENT_TURNS_MAX:]]
    chat_blocked = derive_blocked_evidence_requests(turns)
    actionable = filter_actionable_evidence_requests(
        buyer_facing_missing_evidence(input_data.facts),
        chat_blocked,
    )
    if chat_blocked:
        payload["blocked_evidence_requests"] = chat_blocked
    if actionable:
        payload["actionable_evidence_requests"] = actionable
    return payload


def _llm_generate_strategy(
    *,
    disposition: str,
    input_data: StrategyInput,
    risk_factors: List[str],
    estimated_win_rate: float | None,
    strategy_stage: str,
    action_contract: dict[str, Any],
    merchant_fault_signal: bool,
    evidence_blocks_decision: bool,
    reasoning_delta_callback: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """
    输出结构化策略 JSON（固定使用 AGENT2_LLM_MODEL_STRATEGY）。
    """
    prompt_payload = _build_strategy_prompt_payload(
        disposition=disposition,
        strategy_stage=strategy_stage,
        action_type=str(action_contract.get("action_type") or ""),
        compensation_policy=str(action_contract.get("compensation_policy") or ""),
        rule_constraints=list(action_contract.get("rule_constraints") or []),
        next_step=str(action_contract.get("next_step") or ""),
        evidence_blocks_decision=evidence_blocks_decision,
        merchant_fault_signal=merchant_fault_signal,
        input_data=input_data,
        risk_factors=risk_factors,
        estimated_win_rate=estimated_win_rate,
    )
    user_content = f"请基于以下输入生成策略 JSON：\n{json.dumps(prompt_payload, ensure_ascii=False)}"
    call_start = time.perf_counter()
    llm_text = chat_completion(
        messages=[
            {"role": "system", "content": _build_strategy_json_system_prompt()},
            {"role": "user", "content": user_content},
        ],
        model_env_key=STRATEGY_MODEL_ENV,
        temperature=0.3,
        stream_delta_callback=reasoning_delta_callback,
    )
    elapsed_ms = int((time.perf_counter() - call_start) * 1000)
    logger.info(
        "%s 策略 LLM 完成 elapsed_ms=%s prompt_chars=%s",
        AGENT2_LOG_PREFIX,
        elapsed_ms,
        len(user_content),
    )
    if not llm_text:
        return None
    return _parse_strategy_json(llm_text)


# ---------- 主入口：汇总得分并生成 StrategyOutput ----------
def recommend(
    input_data: StrategyInput,
    reasoning_delta_callback: Callable[[str], None] | None = None,
) -> StrategyOutput:
    """
    综合规则、恶意、价值与判例，输出单链路处置方向与说明。

    纯函数：不读环境变量、不访问网络与数据库；所有外部数据须由调用方
    通过 StrategyInput 注入。

    参数:
        input_data: 含 facts、buyer_profile、matched_rules、similar_cases、order_amount。
        reasoning_delta_callback: 策略 JSON 流式回调（用于前端增量展示）。

    返回:
        StrategyOutput，含 disposition、estimated_win_rate、customer_intent_analysis、
        reasoning、risk_factors、dialogue_context 等。
    """
    if input_data.precomputed_malicious_detection is None:
        raise ValueError(f"{AGENT2_LOG_PREFIX} precomputed_malicious_detection 必须由调用方注入")
    if input_data.precomputed_customer_value is None:
        raise ValueError(f"{AGENT2_LOG_PREFIX} precomputed_customer_value 必须由调用方注入")

    risk_factors: List[str] = []

    # 第一层：规则层（纯计算，无外部依赖）
    policy_refs, rule_stance, rule_count = _analyze_rule_stance(input_data)

    malicious_result = input_data.precomputed_malicious_detection
    customer_value = input_data.precomputed_customer_value
    value_risks = _value_risks_from_result(customer_value)

    if (malicious_result.risk_level or "").strip().lower() in {"medium", "high"}:
        advice = str(malicious_result.disposition_advice or "").strip()
        if advice:
            risk_factors.append(f"[恶意层] {advice}")
    risk_factors.extend(value_risks)

    has_signal_conflict = _has_major_signal_conflict(
        input_data,
        malicious_result=malicious_result,
        rule_stance=rule_stance,
    )
    if has_signal_conflict:
        risk_factors.append("[信号一致性] 恶意风险与商责明确信号同时存在，需说明冲突并按优先级谨慎处理")

    merchant_fault_signal = _merchant_fault_signal(input_data, rule_stance)

    disposition = _determine_disposition(
        input_data,
        malicious_result=malicious_result,
        customer_value=customer_value,
        rule_stance=rule_stance,
    )
    evidence_readiness = assess_evidence_readiness(
        facts=input_data.facts,
        rule_constraints=input_data.rule_constraints or [],
        malicious_result=malicious_result,
    )
    strategy_stage = _infer_strategy_stage(
        input_data,
        disposition=disposition,
        merchant_fault_signal=merchant_fault_signal,
        blocks_decision=evidence_readiness.blocks_decision,
    )
    if evidence_readiness.blocks_decision:
        for reason in evidence_readiness.blocking_reasons:
            risk_factors.append(f"[策略阶段] {reason}")
    else:
        risk_factors.extend(build_evidence_risk_notes(input_data.facts))

    rule_constraints = _build_rule_constraints(input_data)
    action_contract = _infer_action_contract(
        input_data,
        disposition=disposition,
        strategy_stage=strategy_stage,
        rule_stance=rule_stance,
        merchant_fault_signal=merchant_fault_signal,
        rule_constraints=rule_constraints,
        malicious_result=malicious_result,
    )

    estimated_win_rate = _estimate_win_rate(
        input_data,
        disposition=disposition,
        malicious_result=malicious_result,
        rule_stance=rule_stance,
        rule_count=rule_count,
    )
    confidence = _estimate_confidence(
        input_data,
        malicious_result=malicious_result,
        rule_stance=rule_stance,
        rule_count=rule_count,
    )

    strategy_json = _llm_generate_strategy(
        disposition=disposition,
        input_data=input_data,
        risk_factors=risk_factors,
        estimated_win_rate=estimated_win_rate,
        strategy_stage=strategy_stage,
        action_contract=action_contract,
        merchant_fault_signal=merchant_fault_signal,
        evidence_blocks_decision=evidence_readiness.blocks_decision,
        reasoning_delta_callback=reasoning_delta_callback,
    )

    contract_next_step = str(action_contract.get("next_step") or "").strip()
    intent_fallback = f"主诉意图归类为「{_infer_customer_intent(input_data)}」"
    rationale_fallback = contract_next_step or "综合本案事实与动作契约推进。"

    if strategy_json:
        llm_risks = strategy_json.get("risk_factors")
        if isinstance(llm_risks, list):
            for item in llm_risks:
                text = str(item or "").strip()
                if text:
                    risk_factors.append(text)
        customer_intent_analysis = _polish_merchant_facing_text(
            str(strategy_json.get("customer_intent_analysis") or "").strip()
        ) or intent_fallback
        strategy_direction_summary = _compose_action_direction_summary(
            action_contract,
            _polish_merchant_facing_text(
                str(strategy_json.get("strategy_direction_summary") or "").strip()
            )
            or contract_next_step,
        )
        strategy_direction_rationale = _polish_merchant_facing_text(
            str(strategy_json.get("strategy_direction_rationale") or "").strip()
        ) or rationale_fallback
        dialogue_context = _normalize_dialogue_context(strategy_json.get("dialogue_context"), input_data)
    else:
        customer_intent_analysis = intent_fallback
        strategy_direction_summary = _compose_action_direction_summary(action_contract, contract_next_step)
        strategy_direction_rationale = rationale_fallback
        dialogue_context = _normalize_dialogue_context({}, input_data)

    platform_rule_basis = _build_platform_rule_basis(input_data)
    reasoning = _compose_reasoning_from_fields(
        customer_intent_analysis=customer_intent_analysis,
        risk_factors=list(dict.fromkeys(risk_factors)),
        strategy_direction_summary=strategy_direction_summary,
        strategy_direction_rationale=strategy_direction_rationale,
    )

    policy_ref = ",".join(policy_refs[:3]) if policy_refs else None
    dedup_risks = list(dict.fromkeys(risk_factors))

    return StrategyOutput(
        disposition=disposition,
        estimated_win_rate=estimated_win_rate,
        policy_ref=policy_ref,
        customer_intent_analysis=customer_intent_analysis,
        strategy_direction_summary=strategy_direction_summary,
        strategy_direction_rationale=strategy_direction_rationale,
        platform_rule_basis=platform_rule_basis,
        reasoning=reasoning,
        risk_factors=dedup_risks,
        confidence=confidence,
        strategy_stage=strategy_stage,
        action_type=str(action_contract.get("action_type") or ""),
        compensation_policy=str(action_contract.get("compensation_policy") or ""),
        rule_constraints=list(action_contract.get("rule_constraints") or []),
        structured_rule_constraints=list(input_data.rule_constraints or []),
        next_step=str(action_contract.get("next_step") or ""),
        customer_value=customer_value,
        malicious_detection=malicious_result,
        dialogue_context=dialogue_context,
    )
