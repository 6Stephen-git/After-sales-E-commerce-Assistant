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
    DialogueContext,
    MaliciousDetectionOutput,
    CustomerValueOutput,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    STRATEGY_STAGE_EVIDENCE_FIRST,
    StrategyInput,
    StrategyOutput,
)
from backend.tools.llm_client import chat_completion


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
    """从条文匹配 display_rules 提取与争点贴合的规则要点；无贴合时不回退 brief 凑数。"""
    lines: List[str] = []
    seen: set[str] = set()
    for rule in input_data.matched_rules or []:
        text = _polish_merchant_facing_text(str(getattr(rule, "rule_summary", "") or ""))
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
    return lines


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


def _malicious_risks_from_result(malicious_result: MaliciousDetectionOutput) -> List[str]:
    """
    根据恶意检测结果生成策略层风险提示条目。
    """
    layer_risks: List[str] = []
    if malicious_result.risk_level == "high":
        layer_risks.append("[恶意层] 高风险恶意：优先抗辩并准备平台介入，先固定证据链后再沟通")
    elif malicious_result.risk_level == "medium":
        layer_risks.append("[恶意层] 中风险恶意：谨慎协商；若证据质量 low，则先走抗辩补证路径")
    return layer_risks


def _value_risks_from_result(customer_value: CustomerValueOutput) -> List[str]:
    """
    根据客户价值通道生成策略层风险提示条目。
    """
    layer_risks: List[str] = []
    if customer_value.channel == "long_term":
        layer_risks.append("[客户价值层] 触发长期优待通道，优先协商维护关系")
    elif customer_value.channel == "order":
        layer_risks.append("[客户价值层] 触发本单重点处理，建议快速协商收敛纠纷")
    return layer_risks


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
    简单案跳过条文匹配（rule_match_skipped）且无命中时，规则维按 0.30 计。
    """
    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    missing_count = len(input_data.facts.missing_evidence)
    merchant_fault_signal = rule_stance == "buyer" and evidence_quality == "high"

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
        '  "strategy_direction_rationale": "2～4句推理理由，口语化",\n'
        '  "risk_factors": ["风险点1"],\n'
        '  "dialogue_context": {\n'
        '    "dialogue_mode": "continue|cold_start",\n'
        '    "blocked_evidence_requests": ["买家已拒举证项"],\n'
        '    "actionable_evidence_requests": ["仍可请求的替代举证"],\n'
        '    "fallback_script": "话术 LLM 失败时的 1~3 句备用话术"\n'
        "  }\n"
        "}\n"
        "若 strategy_stage=evidence_first：strategy_direction_summary 只能要求补证、固定己方证据，禁止先给退款/补偿方案。"
        "若 dialogue_context.blocked_evidence_requests 非空：不得再要求其中任何一项。"
        "若 recent_turns 非空：须承接对话，禁止重复商家已提且买家已拒的举证要求。"
        "禁止罗列多套备选方案。"
        "dialogue_context.fallback_script 须遵守 compensation_policy 与 strategy_stage，嵌入对话语境。"
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

    blocked = _as_list(data.get("blocked_evidence_requests"))
    actionable = _as_list(data.get("actionable_evidence_requests"))
    if not actionable:
        actionable = list(input_data.facts.missing_evidence or [])

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
    }
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
            }
        )
    return briefs


def _build_strategy_prompt_payload(
    *,
    disposition: str,
    strategy_stage: str,
    compensation_policy: str,
    evidence_incomplete: bool,
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
        "compensation_policy": compensation_policy,
        "evidence_incomplete": evidence_incomplete,
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
    return payload


def _llm_generate_strategy(
    *,
    disposition: str,
    input_data: StrategyInput,
    risk_factors: List[str],
    estimated_win_rate: float | None,
    strategy_stage: str,
    malicious_result: MaliciousDetectionOutput,
    customer_value: CustomerValueOutput,
    merchant_fault_signal: bool,
    has_signal_conflict: bool,
    reasoning_delta_callback: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """
    输出结构化策略 JSON（固定使用 AGENT2_LLM_MODEL_STRATEGY）。
    """
    compensation_policy = "forbid" if (input_data.facts.missing_evidence or []) else "negotiate_soft"
    prompt_payload = _build_strategy_prompt_payload(
        disposition=disposition,
        strategy_stage=strategy_stage,
        compensation_policy=compensation_policy,
        evidence_incomplete=_is_evidence_insufficient_for_decision(input_data),
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
    malicious_risks = _malicious_risks_from_result(malicious_result)
    customer_value = input_data.precomputed_customer_value
    value_risks = _value_risks_from_result(customer_value)

    risk_factors.extend(malicious_risks)
    risk_factors.extend(value_risks)

    has_signal_conflict = _has_major_signal_conflict(
        input_data,
        malicious_result=malicious_result,
        rule_stance=rule_stance,
    )
    if has_signal_conflict:
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

    strategy_json = _llm_generate_strategy(
        disposition=disposition,
        input_data=input_data,
        risk_factors=risk_factors,
        estimated_win_rate=estimated_win_rate,
        strategy_stage=strategy_stage,
        malicious_result=malicious_result,
        customer_value=customer_value,
        merchant_fault_signal=merchant_fault_signal,
        has_signal_conflict=has_signal_conflict,
        reasoning_delta_callback=reasoning_delta_callback,
    )

    if strategy_json:
        customer_intent_analysis = _polish_merchant_facing_text(
            str(strategy_json.get("customer_intent_analysis") or "").strip()
        ) or _compose_customer_intent_analysis(input_data)
        strategy_direction_summary = _polish_merchant_facing_text(
            str(strategy_json.get("strategy_direction_summary") or "").strip()
        ) or _compose_strategy_direction_summary(disposition=disposition, input_data=input_data)
        strategy_direction_rationale = _polish_merchant_facing_text(
            str(strategy_json.get("strategy_direction_rationale") or "").strip()
        ) or _compose_strategy_direction_rationale(
            disposition=disposition,
            input_data=input_data,
            risk_factors=risk_factors,
            estimated_win_rate=estimated_win_rate,
        )
        llm_risks = strategy_json.get("risk_factors")
        if isinstance(llm_risks, list):
            for item in llm_risks:
                text = str(item or "").strip()
                if text:
                    risk_factors.append(text)
        platform_rule_basis = _build_platform_rule_basis(input_data)
        dialogue_context = _normalize_dialogue_context(strategy_json.get("dialogue_context"), input_data)
        reasoning = _compose_reasoning_from_fields(
            customer_intent_analysis=customer_intent_analysis,
            risk_factors=list(dict.fromkeys(risk_factors)),
            strategy_direction_summary=strategy_direction_summary,
            strategy_direction_rationale=strategy_direction_rationale,
        )
    else:
        reasoning = _build_fallback_reasoning(
            disposition=disposition,
            input_data=input_data,
            risk_factors=risk_factors,
            estimated_win_rate=estimated_win_rate,
        )
        customer_intent_analysis = _compose_customer_intent_analysis(input_data)
        strategy_direction_summary = _compose_strategy_direction_summary(
            disposition=disposition,
            input_data=input_data,
        )
        strategy_direction_rationale = _compose_strategy_direction_rationale(
            disposition=disposition,
            input_data=input_data,
            risk_factors=risk_factors,
            estimated_win_rate=estimated_win_rate,
        )
        platform_rule_basis = _build_platform_rule_basis(input_data)
        dialogue_context = _normalize_dialogue_context({}, input_data)

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
        customer_value=customer_value,
        malicious_detection=malicious_result,
        dialogue_context=dialogue_context,
    )
