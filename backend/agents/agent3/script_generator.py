"""
Agent 3：话术生成员。

职责：在 Agent2 输出的 dialogue_context 约束下生成可嵌入当下聊天的一条店主口吻话术。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from schemas import (
    ACTION_EVIDENCE_REQUEST,
    ACTION_MERCHANT_REMEDY,
    ACTION_MONETARY_SETTLE,
    ACTION_RULE_EXPLAIN,
    ACTION_RETURN_INSPECTION,
    ChatTurn,
    COMPENSATION_POLICY_EXPLICIT_AMOUNT,
    COMPENSATION_POLICY_FORBID,
    COMPENSATION_POLICY_NONE,
    COMPENSATION_POLICY_SOFT_NO_AMOUNT,
    DialogueContext,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    EVIDENCE_HIGH,
    RESPONSE_MODE_MALICIOUS_RISK,
    RESPONSE_MODE_MERCHANT_FAULT,
    RESPONSE_MODE_NEUTRAL_NEGOTIATE,
    STRATEGY_STAGE_COMPENSATE_CLOSE,
    STRATEGY_STAGE_DEFEND_PLATFORM,
    STRATEGY_STAGE_EVIDENCE_FIRST,
    STRATEGY_STAGE_NEGOTIATE_SETTLE,
    ScriptInput,
    ScriptOutput,
)

from backend.tools.agent3_tools import generate_buyer_script


logger = logging.getLogger(__name__)
AGENT3_LOG_PREFIX = "[Agent3]"


# ---------- 文本规范化 ----------
def _normalize_text(value: str | None, fallback: str = "") -> str:
    """
    将可选字符串规范为非空展示文案。
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


# ---------- 金额格式化 ----------
def _format_money(amount: float) -> str:
    """
    将订单金额格式化为两位小数字符串。
    """
    return f"{max(0.0, float(amount)):.2f}"


# ---------- 应对思想推导：来自 Agent2 枚举信号 ----------
def _derive_response_mode(input_data: ScriptInput) -> str:
    """
    从 Agent2 输出信号推导话术应对思想（三枚举）。
    """
    strategy = input_data.strategy_output
    malicious = strategy.malicious_detection
    if malicious and (malicious.risk_level or "").strip().lower() == "high":
        return RESPONSE_MODE_MALICIOUS_RISK

    disposition = (strategy.disposition or "").strip().lower()
    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()
    action_type = (strategy.action_type or "").strip().lower()
    compensation_policy = (strategy.compensation_policy or "").strip().lower()

    if action_type in {ACTION_RULE_EXPLAIN, ACTION_EVIDENCE_REQUEST, ACTION_RETURN_INSPECTION}:
        return RESPONSE_MODE_NEUTRAL_NEGOTIATE
    if action_type and action_type in {ACTION_RULE_EXPLAIN, ACTION_EVIDENCE_REQUEST, ACTION_RETURN_INSPECTION}:
        return RESPONSE_MODE_NEUTRAL_NEGOTIATE
    if disposition == DISPOSITION_COMPENSATE:
        return RESPONSE_MODE_MERCHANT_FAULT
    if disposition == DISPOSITION_NEGOTIATE and evidence_quality == EVIDENCE_HIGH:
        return RESPONSE_MODE_MERCHANT_FAULT
    if disposition == DISPOSITION_DEFEND:
        return RESPONSE_MODE_MALICIOUS_RISK
    return RESPONSE_MODE_NEUTRAL_NEGOTIATE


# ---------- 补偿门禁：来自 strategy_stage 枚举 ----------
def _derive_compensation_policy(strategy_stage: str, explicit_policy: str | None = None) -> str:
    """
    将策略阶段映射为补偿门禁枚举。
    """
    policy = (explicit_policy or "").strip().lower()
    if policy in {
        COMPENSATION_POLICY_FORBID,
        COMPENSATION_POLICY_NONE,
        COMPENSATION_POLICY_SOFT_NO_AMOUNT,
        COMPENSATION_POLICY_EXPLICIT_AMOUNT,
    }:
        return policy

    normalized = (strategy_stage or "").strip().lower()
    if normalized == STRATEGY_STAGE_EVIDENCE_FIRST:
        return COMPENSATION_POLICY_FORBID
    if normalized == STRATEGY_STAGE_COMPENSATE_CLOSE:
        return COMPENSATION_POLICY_EXPLICIT_AMOUNT
    if normalized == STRATEGY_STAGE_NEGOTIATE_SETTLE:
        return COMPENSATION_POLICY_SOFT_NO_AMOUNT
    if normalized == STRATEGY_STAGE_DEFEND_PLATFORM:
        return COMPENSATION_POLICY_NONE
    return COMPENSATION_POLICY_SOFT_NO_AMOUNT


def _must_state_compensation_amount(
    *,
    action_type: str,
    strategy_stage: str,
    compensation_policy: str,
    response_mode: str,
) -> bool:
    """
    仅金额动作且明确允许报金额时，要求话术给出具体金额并征求买家同意。
    """
    action = (action_type or "").strip().lower()
    if action not in {ACTION_MONETARY_SETTLE, ACTION_MERCHANT_REMEDY}:
        return False
    if compensation_policy != COMPENSATION_POLICY_EXPLICIT_AMOUNT:
        return False
    stage = (strategy_stage or "").strip().lower()
    if stage == STRATEGY_STAGE_EVIDENCE_FIRST:
        return False
    if response_mode == RESPONSE_MODE_MALICIOUS_RISK:
        return False
    return True


# ---------- 聊天轮次：仅做结构规范化，不做语义判断 ----------
def _normalize_chat_turns(chat_history: list[ChatTurn]) -> list[dict[str, str]]:
    """
    将 ChatTurn 列表转为 role/content dict。
    """
    turns: list[dict[str, str]] = []
    for item in chat_history or []:
        role = (item.role or "buyer").strip().lower()
        if role not in {"buyer", "merchant"}:
            role = "buyer"
        content = _normalize_text(item.content)
        if content:
            turns.append({"role": role, "content": content})
    return turns


# ---------- dialogue_context 兜底：Agent2 未输出时使用最小结构 ----------
def _minimal_dialogue_context(input_data: ScriptInput) -> DialogueContext:
    """
    Agent2 未提供 dialogue_context 时的最小结构（无 blocked 推断）。
    """
    turns = _normalize_chat_turns(input_data.chat_history)
    missing = list(input_data.facts.missing_evidence or [])
    return DialogueContext(
        dialogue_mode="continue" if turns else "cold_start",
        blocked_evidence_requests=[],
        actionable_evidence_requests=missing,
        fallback_script="我这边还在核对材料，核实完马上回您。" if turns else "您好，我这边还在核对材料，核实完马上回您。",
    )


def _resolve_dialogue_context(input_data: ScriptInput) -> DialogueContext:
    """
    优先读取 Agent2 输出的 dialogue_context，缺失时走最小兜底。
    """
    ctx = input_data.strategy_output.dialogue_context
    if ctx is not None:
        return ctx
    logger.warning("%s 策略未含 dialogue_context，使用最小结构兜底", AGENT3_LOG_PREFIX)
    return _minimal_dialogue_context(input_data)


# ---------- 语气与事实边界 ----------
def _build_tone_hint(input_data: ScriptInput) -> str:
    """
    合并情绪与客户价值提示。
    """
    parts: list[str] = []
    emotion = _normalize_text(input_data.emotion_note)
    if emotion:
        parts.append(emotion)
    customer_value = input_data.strategy_output.customer_value
    if customer_value and customer_value.tone_suggestion:
        parts.append(str(customer_value.tone_suggestion).strip())
    if not parts:
        return "自然、真诚、像店主本人；短句优先"
    return "；".join(parts) + "；短句优先"


def _build_facts_boundary(input_data: ScriptInput) -> dict[str, Any]:
    """
    提取关键事实字段作为边界约束。
    """
    facts = input_data.facts
    boundary: dict[str, Any] = {}
    if facts.goods_received is not None:
        boundary["goods_received"] = facts.goods_received
    if facts.defect_type:
        boundary["defect_type"] = facts.defect_type
    if facts.defect_location:
        boundary["defect_location"] = facts.defect_location
    if facts.logistics_normal is not None:
        boundary["logistics_normal"] = facts.logistics_normal
    if facts.evidence_quality:
        boundary["evidence_quality"] = facts.evidence_quality
    if facts.red_flags:
        boundary["red_flags"] = list(facts.red_flags)
    if isinstance(facts.attributes, dict) and isinstance(facts.attributes.get("rule_context"), dict):
        boundary["rule_context"] = dict(facts.attributes["rule_context"])
    return boundary


def _coerce_float(value: Any) -> float | None:
    """安全转换浮点数。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_compensation_ratio_cap(input_data: ScriptInput) -> float | None:
    """
    从事实上下文或结构化规则约束中提取补偿比例上限。
    """
    facts = input_data.facts
    context = facts.attributes.get("rule_context") if isinstance(facts.attributes, dict) else None
    if isinstance(context, dict):
        cap = _coerce_float(context.get("compensation_ratio_cap"))
        if cap is not None:
            return cap if cap <= 1 else cap / 100
        policy_limits = context.get("policy_limits")
        if isinstance(policy_limits, dict):
            cap = _coerce_float(policy_limits.get("compensation_ratio_cap"))
            if cap is not None:
                return cap if cap <= 1 else cap / 100

    for constraint in input_data.strategy_output.structured_rule_constraints or []:
        text = str(constraint.text or "")
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if match:
            cap = _coerce_float(match.group(1))
            if cap is not None:
                return cap / 100
    return None


# ---------- 话术生成 payload ----------
def _build_script_payload(
    input_data: ScriptInput,
    *,
    response_mode: str,
    compensation_policy: str,
    dialogue_context: DialogueContext,
    must_state_compensation_amount: bool,
) -> dict[str, Any]:
    """
    组装传入 generate_buyer_script 的 payload。
    """
    strategy = input_data.strategy_output
    customer_value = strategy.customer_value
    payload: dict[str, Any] = {
        "response_mode": response_mode,
        "strategy_stage": strategy.strategy_stage,
        "action_type": strategy.action_type,
        "compensation_policy": compensation_policy,
        "rule_constraints": list(strategy.rule_constraints or []),
        "next_step": _normalize_text(strategy.next_step),
        "must_state_compensation_amount": must_state_compensation_amount,
        "disposition": strategy.disposition,
        "dialogue_context": dialogue_context.model_dump(),
        "recent_turns": _normalize_chat_turns(input_data.chat_history),
        "issue_summary": _normalize_text(
            input_data.facts.issue_summary,
            fallback="买家反馈了售后问题，细节仍在核对",
        ),
        "facts_boundary": _build_facts_boundary(input_data),
        "order_id": _normalize_text(input_data.order_id, fallback="本单"),
        "order_amount": _format_money(input_data.order_amount),
        "tone_hint": _build_tone_hint(input_data),
        "strategy_direction_summary": _normalize_text(strategy.strategy_direction_summary),
        "structured_rule_constraints": [
            item.model_dump() for item in (strategy.structured_rule_constraints or [])
        ],
    }
    ratio_cap = _extract_compensation_ratio_cap(input_data)
    if ratio_cap is not None and input_data.order_amount > 0:
        payload["max_compensation_amount"] = round(float(input_data.order_amount) * ratio_cap, 2)
    if customer_value:
        payload["customer_value_channel"] = customer_value.channel
        if must_state_compensation_amount and customer_value.compensation_uplift:
            payload["compensation_uplift"] = customer_value.compensation_uplift
    if strategy.malicious_detection and strategy.malicious_detection.risk_level:
        payload["malicious_risk_level"] = strategy.malicious_detection.risk_level
    return payload


# ---------- 使用提示 ----------
def _build_usage_tip(
    *,
    response_mode: str,
    strategy_stage: str,
    must_state_compensation_amount: bool,
) -> str:
    """
    根据应对思想与策略阶段生成简短使用说明。
    """
    stage = (strategy_stage or "").strip().lower()
    if stage == STRATEGY_STAGE_EVIDENCE_FIRST:
        return "当前处于举证阶段：请按对话语境组织话术，勿提前承诺补偿。"
    if must_state_compensation_amount:
        return "协商/善后：此话术已包含具体补偿金额并征求买家是否接受，发送前请核对金额是否合理。"
    if response_mode == RESPONSE_MODE_MERCHANT_FAULT:
        return "商责已基本明确：此话术主动担责并给出处理方向，可直接发送。"
    if response_mode == RESPONSE_MODE_MALICIOUS_RISK:
        return "建议保留对话记录：此话术侧重规则与举证，不轻易让步。"
    return "协商推进：根据买家回复再微调方案。"


# ---------- 主入口 ----------
def generate(input_data: ScriptInput) -> ScriptOutput:
    """
    在 Agent2 dialogue_context 约束下生成单条买家话术；LLM 失败走 fallback_script。
    """
    strategy = input_data.strategy_output
    strategy_stage = strategy.strategy_stage
    response_mode = _derive_response_mode(input_data)
    compensation_policy = _derive_compensation_policy(strategy_stage, strategy.compensation_policy)
    dialogue_context = _resolve_dialogue_context(input_data)
    must_state_amount = _must_state_compensation_amount(
        action_type=strategy.action_type,
        strategy_stage=strategy_stage,
        compensation_policy=compensation_policy,
        response_mode=response_mode,
    )

    logger.info(
        "%s 话术生成 dialogue_mode=%s blocked=%s actionable=%s must_state_amount=%s",
        AGENT3_LOG_PREFIX,
        dialogue_context.dialogue_mode,
        dialogue_context.blocked_evidence_requests,
        dialogue_context.actionable_evidence_requests,
        must_state_amount,
    )

    script_payload = _build_script_payload(
        input_data,
        response_mode=response_mode,
        compensation_policy=compensation_policy,
        dialogue_context=dialogue_context,
        must_state_compensation_amount=must_state_amount,
    )
    script = generate_buyer_script(script_payload)
    if not script:
        logger.warning("%s 话术 LLM 不可用，使用 fallback_script", AGENT3_LOG_PREFIX)
        script = str(dialogue_context.fallback_script or "").strip()
    if not script:
        script = "我这边还在跟进，核实清楚后马上回复您。"

    usage_tip = _build_usage_tip(
        response_mode=response_mode,
        strategy_stage=strategy_stage,
        must_state_compensation_amount=must_state_amount,
    )
    return ScriptOutput(
        script=script.strip(),
        response_mode=response_mode,
        usage_tip=usage_tip,
    )
