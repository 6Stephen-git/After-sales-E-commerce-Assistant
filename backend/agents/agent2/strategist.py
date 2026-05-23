"""
Agent 2：策略参谋员。

职责：在既定输入（事实、规则命中、画像、判例）上做加权决策；本模块不读库、不调 HTTP。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List
import json
import logging
import os
import re

from schemas import (
    MaliciousDetectionInput,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    StrategyInput,
    StrategyOutput,
)
from backend.tools.agent2_tools import detect_malicious_behavior, run_customer_value_analysis
from backend.tools.llm_client import chat_completion


AGENT2_LOG_PREFIX = "[Agent2]"
logger = logging.getLogger(__name__)

# ---------- 策略参谋核心目标（全链路提示词共用） ----------
_MERCHANT_INTEREST_GOAL = (
    "目标：在道德、平台规则与法律边界内，帮助商家分阶段争取最优结果（本单损益 + 客户长期价值 + 口碑与升级风险）。"
    "售后不是一步结案：先完善举证与事实闭环，再视证据与规则选择协商、善后或合理拒赔；禁止跳过举证直接给退款/换新方案。"
)


def _polish_merchant_facing_text(text: str) -> str:
    """去除条号/章节等内部引用，使文案更适合商家阅读。"""
    if not text:
        return text
    cleaned = re.sub(r"第[一二三四五六七八九十百千零\d]+条", "", text)
    cleaned = re.sub(r"第[一二三四五六七八九十]+节[^，。；\n]*", "", cleaned)
    cleaned = re.sub(r"[（(]篇首[）)]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" 。；，,")
    return cleaned.strip()


def _build_platform_rule_basis(input_data: StrategyInput) -> List[str]:
    """从 rule_briefs（品类优先）提取面向商家的规则要点列表。"""
    lines: List[str] = []
    seen: set[str] = set()
    for brief in input_data.rule_briefs or []:
        text = _polish_merchant_facing_text(str(getattr(brief, "brief", "") or ""))
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
    if not lines:
        for rule in input_data.matched_rules or []:
            text = _polish_merchant_facing_text(str(getattr(rule, "rule_summary", "") or ""))
            if text and text not in seen:
                seen.add(text)
                lines.append(text)
    return lines[:5]


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


def _apply_malicious_layer(input_data: StrategyInput) -> tuple:
    """
    第二层：恶意行为风险过滤层，输出标准备注。

    返回 (malicious_result, new_risk_factors) 元组，不修改外部 risk_factors，
    保证并发安全。
    """
    malicious_input = MaliciousDetectionInput(
        buyer_profile=input_data.buyer_profile,
        facts=input_data.facts,
        order_amount=input_data.order_amount,
        chat_history=input_data.chat_history,
        emotion_note=input_data.emotion_note,
    )
    malicious_result = detect_malicious_behavior(malicious_input)
    layer_risks: List[str] = []
    if malicious_result.risk_level == "high":
        layer_risks.append("[恶意层] 高风险恶意：优先抗辩并准备平台介入，先固定证据链后再沟通")
    elif malicious_result.risk_level == "medium":
        layer_risks.append("[恶意层] 中风险恶意：谨慎协商；若证据质量 low，则先走抗辩补证路径")
    return malicious_result, layer_risks


def _apply_customer_value_layer(input_data: StrategyInput) -> tuple:
    """
    第三层：客户价值评估层（双维价值 + 触发通道）。

    完整工具链见 backend.tools.agent2_tools.run_customer_value_analysis，供智能模式复用。

    返回 (customer_value, new_risk_factors) 元组，不修改外部 risk_factors，
    保证并发安全。
    """
    logger.info("%s 开始客户价值完整分析（工具链）", AGENT2_LOG_PREFIX)
    customer_value = run_customer_value_analysis(input_data)
    layer_risks: List[str] = []
    if customer_value.channel == "long_term":
        layer_risks.append("[客户价值层] 触发长期优待通道，优先协商维护关系")
    elif customer_value.channel == "order":
        layer_risks.append("[客户价值层] 触发本单重点处理，建议快速协商收敛纠纷")
    return customer_value, layer_risks


def _is_evidence_insufficient_for_decision(input_data: StrategyInput) -> bool:
    """
    判断当前材料是否尚不足以支撑退款/补偿/拒赔等终局决策（全品类，基于事实字段而非品类词表）。

    参数:
        input_data: 策略输入。

    返回:
        True 表示应先进入「补证/固定证据」阶段。
    """
    facts = input_data.facts
    if facts.missing_evidence:
        return True

    doubt_markers = (
        "待核实",
        "不足以",
        "无法确认",
        "存疑",
        "暂不可靠",
        "不清晰",
        "看不清",
        "难以辨认",
        "无法认定",
        "尚不能",
        "待补充",
    )
    corpus_parts: List[str] = []
    if facts.uncertainty_note:
        corpus_parts.append(str(facts.uncertainty_note))
    for item in facts.red_flags or []:
        corpus_parts.append(str(item))
    for item in facts.visual_observations or []:
        corpus_parts.append(str(item))
    corpus = " ".join(corpus_parts)
    if any(marker in corpus for marker in doubt_markers):
        return True

    summary = (facts.issue_summary or "") + " " + " ".join(str(t) for t in (facts.intent_tags or []))
    has_settlement_demand = any(keyword in summary for keyword in ("退款", "赔偿", "赔付", "退换", "仅退"))
    defect_claimed = bool(facts.defect_type and facts.defect_type != "无瑕疵") or any(
        keyword in summary for keyword in ("质量", "瑕疵", "破损", "划痕", "损坏", "故障")
    )
    evidence_quality = (facts.evidence_quality or "").strip().lower()
    if has_settlement_demand and defect_claimed and evidence_quality != "high":
        return True

    has_image_evidence = any(
        isinstance(item, dict) and str(item.get("type", "")).strip().lower() == "image"
        for item in (facts.evidence_items or [])
    )
    if defect_claimed and has_image_evidence and not facts.visual_observations:
        return True
    return False


def _infer_strategy_stage(
    input_data: StrategyInput,
    *,
    disposition: str,
    merchant_fault_signal: bool,
) -> str:
    """
    推断当前应处的策略阶段，供 LLM 生成「当下这一步」而非终局方案。

    返回:
        evidence_first | negotiate_settle | compensate_close | defend_platform
    """
    if _is_evidence_insufficient_for_decision(input_data):
        return "evidence_first"
    if disposition == DISPOSITION_COMPENSATE and merchant_fault_signal:
        return "compensate_close"
    if disposition == DISPOSITION_DEFEND:
        return "defend_platform"
    return "negotiate_settle"


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
    merchant_fault_signal = rule_stance == "buyer" and evidence_quality == "high"

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
    merchant_fault_signal = rule_stance == "buyer" and evidence_quality == "high"

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
    """
    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    missing_count = len(input_data.facts.missing_evidence)
    merchant_fault_signal = rule_stance == "buyer" and evidence_quality == "high"

    # 维度一：规则确定性
    if rule_count >= 1 and rule_stance in {"merchant", "buyer"}:
        dim_rule = 0.40
    elif rule_count >= 1:
        dim_rule = 0.20
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
    merchant_fault_signal = rule_stance == "buyer" and evidence_quality == "high"
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


def _extract_customer_intent_from_reasoning(reasoning: str) -> str | None:
    """
    从四段式 reasoning 中截取「客户意图：」与「风险点：」之间的正文，供核心结论区单独展示。

    参数:
        reasoning: Agent2 生成的完整策略说明。

    返回:
        截取到的意图段落；格式不符合时返回 None。
    """
    if not reasoning or "客户意图：" not in reasoning or "风险点：" not in reasoning:
        return None
    start = reasoning.index("客户意图：") + len("客户意图：")
    risk_idx = reasoning.find("风险点：", start)
    if risk_idx < 0:
        return None
    body = reasoning[start:risk_idx].strip().lstrip("。").strip()
    return body or None


def _extract_strategy_direction_from_reasoning(reasoning: str) -> str | None:
    """
    从四段式 reasoning 中截取「建议动作：」至「推理理由：」之间的正文，作为核心结论区的策略方向。

    参数:
        reasoning: 完整策略说明。

    返回:
        单一路径建议动作；格式不符合时返回 None。
    """
    if not reasoning or "建议动作：" not in reasoning:
        return None
    start = reasoning.index("建议动作：") + len("建议动作：")
    rationale_idx = reasoning.find("推理理由：", start)
    end = rationale_idx if rationale_idx >= 0 else len(reasoning)
    body = reasoning[start:end].strip().rstrip("。").strip()
    return body or None


def _extract_strategy_rationale_from_reasoning(reasoning: str) -> str | None:
    """
    从四段式 reasoning 中截取「推理理由：」起至文末，供核心结论区「推理理由」展示。

    参数:
        reasoning: 完整策略说明。

    返回:
        推理理由段落；格式不符合时返回 None。
    """
    if not reasoning or "推理理由：" not in reasoning:
        return None
    start = reasoning.index("推理理由：") + len("推理理由：")
    body = reasoning[start:].strip()
    return body or None


def _compose_strategy_direction_summary(*, disposition: str, input_data: StrategyInput) -> str:
    """
    当无法从 reasoning 解析建议动作时，按处置方向与事实缺口生成单一路径局部策略（全品类通用）。

    参数:
        disposition: 处置枚举。
        input_data: 策略输入（含事实）。

    返回:
        1～2 句策略方向。
    """
    missing = bool(input_data.facts.missing_evidence)
    weak_evidence = (input_data.facts.evidence_quality or "").lower() in {"low", "medium", "弱", "中"}
    if disposition == DISPOSITION_DEFEND:
        if missing or weak_evidence:
            return "暂不承诺退款或补偿，引导买家按规则补充核心举证，同步固定己方证据链。"
        return "在规则允许范围内提交抗辩或平台复核，沟通上只陈述事实与举证要求，不做超规则口头承诺。"
    if disposition == DISPOSITION_COMPENSATE:
        return "核实责任后给出一条可执行的退换或补偿方案并留痕，同步说明处理时效以收敛纠纷。"
    if missing or weak_evidence:
        return "先要求买家补齐关键举证并说明规则依据，在证据到位前不主动给出退款或大额补偿口径。"
    return "给出一条双方可接受的协商口径（如部分退款或换货），并明确需买家确认或补证后再执行。"



def _compose_strategy_direction_rationale(
    *,
    disposition: str,
    input_data: StrategyInput,
    risk_factors: List[str],
    estimated_win_rate: float | None,
) -> str:
    """
    当无法从 reasoning 解析推理理由时，用事实、规则与风险拼出 2～4 句简述。

    参数:
        disposition: 处置枚举。
        input_data: 策略输入。
        risk_factors: 风险提示列表。
        estimated_win_rate: 抗辩胜率（可选）。

    返回:
        面向商家的推理理由文案。
    """
    parts: List[str] = []
    summary = (input_data.facts.issue_summary or "").strip()
    if summary:
        parts.append(f"买家主要在说：{summary}")
    missing = input_data.facts.missing_evidence or []
    if missing:
        parts.append(f"现在还缺{ '、'.join(str(item) for item in missing[:2]) }，直接认责或退款风险比较大")
    elif (input_data.facts.evidence_quality or "").lower() in {"low", "medium", "弱", "中"}:
        parts.append("现有材料还不足以把责任说清楚，先补证更稳妥")
    briefs = input_data.rule_briefs or []
    if briefs:
        hint = _polish_merchant_facing_text(briefs[0].brief)
        if hint:
            parts.append(f"平台相关规则倾向于：{hint}")
    if risk_factors:
        parts.append(f"另外要注意：{'；'.join(risk_factors[:2])}")
    if disposition == DISPOSITION_DEFEND and estimated_win_rate is not None:
        parts.append(f"按目前情况看，抗辩成功率大约 {estimated_win_rate:.0%}，先把证据和口径站稳更划算")
    elif disposition == DISPOSITION_COMPENSATE:
        parts.append("责任已经比较清楚，及时处理可以少扯皮、少升级投诉")
    elif disposition == DISPOSITION_NEGOTIATE:
        parts.append("证据差不多够了，在规则允许范围内谈一步，往往比硬扛更省成本")
    if not parts:
        parts.append("综合买家说法、现有证据和平台规则，先按上面这一步走比较稳")
    return "。".join(parts[:4]) + "。"


def _compose_customer_intent_analysis(input_data: StrategyInput) -> str:
    """
    当无法从 reasoning 解析意图段时，用规则标签与诉求摘要拼出客户意图分析文案。

    参数:
        input_data: 策略输入（含事实）。

    返回:
        面向商家展示的单段中文说明。
    """
    label = _infer_customer_intent(input_data)
    parts: List[str] = [f"主诉意图归类为「{label}」"]
    summary = (input_data.facts.issue_summary or "").strip()
    if summary:
        parts.append(f"诉求摘要：{summary}")
    tags = [t for t in (input_data.facts.intent_tags or []) if str(t).strip()]
    if tags:
        parts.append("诉求标签：" + "、".join(str(t).strip() for t in tags))
    return "；".join(parts)


def _build_fallback_reasoning(
    *,
    disposition: str,
    input_data: StrategyInput,
    risk_factors: List[str],
    estimated_win_rate: float | None,
) -> str:
    """
    当 LLM 不可用时，输出结构化四段 reasoning。
    """
    intent = _infer_customer_intent(input_data)
    risk_text = "；".join(risk_factors[:3]) if risk_factors else "当前未识别到高风险项"
    action = _compose_strategy_direction_summary(disposition=disposition, input_data=input_data)
    rationale = _compose_strategy_direction_rationale(
        disposition=disposition,
        input_data=input_data,
        risk_factors=risk_factors,
        estimated_win_rate=estimated_win_rate,
    )
    return (
        f"客户意图：{intent}。\n"
        f"风险点：{risk_text}。\n"
        f"建议动作：{action}\n"
        f"推理理由：{rationale}"
    )


def _build_strategy_reasoning_system_prompt() -> str:
    """
    构造策略说明 LLM 的 system 提示，统一注入商户利益最大化与分阶段决策约束。
    """
    return (
        "你是资深电商客服策略参谋。"
        f"{_MERCHANT_INTEREST_GOAL}"
        "输出四段中文，每段分别以“客户意图：”“风险点：”“建议动作：”“推理理由：”开头。"
        "建议动作：只写「当前这一步」的单一路径（1～2句）。"
        "若 strategy_stage=evidence_first：只能要求补证、固定己方证据、说明规则依据，禁止先给退款/部分退款/换新/优惠券等终局方案。"
        "若 strategy_stage=negotiate_settle：可给一条协商口径，须注明以证据与规则为前提。"
        "禁止罗列多套备选、禁止“例如/或者/可同时”式展开。"
        "推理理由：2～4句，用商家能直接看懂的口语向店长解释「为什么建议这一步」；"
        "可说「买家这边」「您这边」「平台一般会」；禁止写第几条、第几章、条号、法规腔和 AI 套话；"
        "可概括平台规则倾向，但不要逐条引用条文编号。"
    )


def _llm_generate_reasoning(
    *,
    disposition: str,
    input_data: StrategyInput,
    risk_factors: List[str],
    estimated_win_rate: float | None,
    strategy_stage: str,
    fast_path: bool = False,
    reasoning_delta_callback: Callable[[str], None] | None = None,
) -> str | None:
    """
    用 LLM 生成更自然的策略说明，强调合规前提下商户利益最大化。

    fast_path=True 时优先小模型，若格式不符合约定则自动回退主模型补调一次。
    """
    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    briefs_for_fault = input_data.rule_briefs or []
    merchant_fault_signal = any(
        getattr(b, "stance_hint", "") == "buyer" or "支持买家" in b.brief
        for b in briefs_for_fault
    ) and evidence_quality == "high"
    prompt_payload = {
        "disposition": disposition,
        "strategy_stage": strategy_stage,
        "evidence_incomplete": _is_evidence_insufficient_for_decision(input_data),
        "merchant_fault_clear": merchant_fault_signal,
        "facts": input_data.facts.model_dump(),
        "buyer_profile": input_data.buyer_profile.model_dump(),
        "rule_briefs": [item.model_dump() for item in (input_data.rule_briefs or [])],
        "matched_rules": [item.model_dump() for item in input_data.matched_rules],
        "similar_cases": [item.model_dump() for item in input_data.similar_cases],
        "risk_factors": risk_factors,
        "order_amount": input_data.order_amount,
        "estimated_win_rate": estimated_win_rate,
    }
    model_env_key = "AGENT2_LLM_MODEL_FAST" if fast_path else "AGENT2_LLM_MODEL"
    fallback_key = "AGENT2_LLM_MODEL" if fast_path else None
    llm_text = chat_completion(
        messages=[
            {"role": "system", "content": _build_strategy_reasoning_system_prompt()},
            {"role": "user", "content": f"请基于以下输入生成策略说明：\n{json.dumps(prompt_payload, ensure_ascii=False)}"},
        ],
        model_env_key=model_env_key,
        fallback_model_env_key=fallback_key,
        temperature=0.3,
        stream_delta_callback=reasoning_delta_callback,
    )
    if not llm_text:
        return None
    normalized = llm_text.strip()
    if (
        "客户意图：" in normalized
        and "风险点：" in normalized
        and "建议动作：" in normalized
        and "推理理由：" in normalized
    ):
        return normalized

    # 小模型路径不满足格式时，回退主模型补调一次，优先保证结果质量与可读性。
    if fast_path and os.getenv("AGENT2_LLM_MODEL", "").strip():
        retry_text = chat_completion(
            messages=[
                {"role": "system", "content": _build_strategy_reasoning_system_prompt()},
                {"role": "user", "content": f"请基于以下输入生成策略说明：\n{json.dumps(prompt_payload, ensure_ascii=False)}"},
            ],
            model_env_key="AGENT2_LLM_MODEL",
            temperature=0.3,
            stream_delta_callback=reasoning_delta_callback,
        )
        if not retry_text:
            return None
        retry_normalized = retry_text.strip()
        if (
            "客户意图：" in retry_normalized
            and "风险点：" in retry_normalized
            and "建议动作：" in retry_normalized
            and "推理理由：" in retry_normalized
        ):
            return retry_normalized
    return None


# ---------- 主入口：汇总得分并生成 StrategyOutput ----------
def recommend(
    input_data: StrategyInput,
    fast_path: bool = False,
    reasoning_delta_callback: Callable[[str], None] | None = None,
) -> StrategyOutput:
    """
    综合规则、恶意、价值与判例，输出单链路处置方向与说明。

    纯函数：不读环境变量、不访问网络与数据库；所有外部数据须由调用方
    通过 StrategyInput 注入。

    参数:
        input_data: 含 facts、buyer_profile、matched_rules、similar_cases、order_amount。
        fast_path: 是否启用轻量模型优先路径（失败会自动回退主模型）。
        reasoning_delta_callback: 推理文本流式回调（用于前端增量展示）。

    返回:
        StrategyOutput，含 disposition、estimated_win_rate、customer_intent_analysis、
        reasoning、risk_factors 等。
    """
    risk_factors: List[str] = []

    # 第一层：规则层（纯计算，无外部依赖）
    policy_refs, rule_stance, rule_count = _analyze_rule_stance(input_data)

    # 第二层 + 第三层：恶意检测与客户价值评估并发执行
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_malicious = executor.submit(_apply_malicious_layer, input_data)
        future_value = executor.submit(_apply_customer_value_layer, input_data)
        malicious_result, malicious_risks = future_malicious.result()
        customer_value, value_risks = future_value.result()
    risk_factors.extend(malicious_risks)
    risk_factors.extend(value_risks)

    if _has_major_signal_conflict(input_data, malicious_result=malicious_result, rule_stance=rule_stance):
        risk_factors.append("[信号一致性] 恶意风险与商责明确信号同时存在，需说明冲突并按优先级谨慎处理")

    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    merchant_fault_signal = rule_stance == "buyer" and evidence_quality == "high"

    disposition = _determine_disposition(
        input_data,
        malicious_result=malicious_result,
        customer_value=customer_value,
        rule_stance=rule_stance,
    )
    strategy_stage = _infer_strategy_stage(
        input_data,
        disposition=disposition,
        merchant_fault_signal=merchant_fault_signal,
    )
    if strategy_stage == "evidence_first":
        risk_factors.append("[策略阶段] 举证未闭环：当前建议先补证并固定证据链，再进入协商/善后/拒赔决策")

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

    reasoning = _llm_generate_reasoning(
        disposition=disposition,
        input_data=input_data,
        risk_factors=risk_factors,
        estimated_win_rate=estimated_win_rate,
        strategy_stage=strategy_stage,
        fast_path=fast_path,
        reasoning_delta_callback=reasoning_delta_callback,
    )
    if not reasoning:
        reasoning = _build_fallback_reasoning(
            disposition=disposition,
            input_data=input_data,
            risk_factors=risk_factors,
            estimated_win_rate=estimated_win_rate,
        )

    policy_ref = ",".join(policy_refs[:3]) if policy_refs else None
    dedup_risks = list(dict.fromkeys(risk_factors))

    intent_extracted = _extract_customer_intent_from_reasoning(reasoning)
    customer_intent_analysis = intent_extracted or _compose_customer_intent_analysis(input_data)

    direction_extracted = _extract_strategy_direction_from_reasoning(reasoning)
    strategy_direction_summary = direction_extracted or _compose_strategy_direction_summary(
        disposition=disposition,
        input_data=input_data,
    )
    rationale_extracted = _extract_strategy_rationale_from_reasoning(reasoning)
    strategy_direction_rationale = _polish_merchant_facing_text(
        rationale_extracted
        or _compose_strategy_direction_rationale(
            disposition=disposition,
            input_data=input_data,
            risk_factors=dedup_risks,
            estimated_win_rate=estimated_win_rate,
        )
    )

    return StrategyOutput(
        disposition=disposition,
        estimated_win_rate=estimated_win_rate,
        policy_ref=policy_ref,
        customer_intent_analysis=customer_intent_analysis,
        strategy_direction_summary=strategy_direction_summary,
        strategy_direction_rationale=strategy_direction_rationale,
        platform_rule_basis=_build_platform_rule_basis(input_data),
        reasoning=reasoning,
        risk_factors=dedup_risks,
        confidence=confidence,
        customer_value=customer_value,
        malicious_detection=malicious_result,
    )
