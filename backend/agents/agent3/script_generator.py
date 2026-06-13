"""
Agent 3：话术生成员。

职责：在 Agent2 输出的 action_contract 与 dialogue_context 约束下生成一条店主口吻话术。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from schemas import (
    ACTION_DEFEND_PREPARE,
    ACTION_MERCHANT_REMEDY,
    ACTION_MONETARY_SETTLE,
    ACTION_RULE_EXPLAIN,
    ACTION_EVIDENCE_REQUEST,
    ACTION_RETURN_INSPECTION,
    COMPENSATION_POLICY_EXPLICIT_AMOUNT,
    COMPENSATION_POLICY_FORBID,
    COMPENSATION_POLICY_NONE,
    COMPENSATION_POLICY_SOFT_NO_AMOUNT,
    ChatTurn,
    DialogueContext,
    DISPOSITION_COMPENSATE,
    DISPOSITION_NEGOTIATE,
    EVIDENCE_HIGH,
    RESPONSE_MODE_MALICIOUS_RISK,
    RESPONSE_MODE_MERCHANT_FAULT,
    RESPONSE_MODE_NEUTRAL_NEGOTIATE,
    STRATEGY_STAGE_EVIDENCE_FIRST,
    ScriptInput,
    ScriptOutput,
)

from backend.tools.agent3_tools import generate_buyer_script


logger = logging.getLogger(__name__)
AGENT3_LOG_PREFIX = "[Agent3]"

_VALID_COMPENSATION_POLICIES = frozenset(
    {
        COMPENSATION_POLICY_FORBID,
        COMPENSATION_POLICY_NONE,
        COMPENSATION_POLICY_SOFT_NO_AMOUNT,
        COMPENSATION_POLICY_EXPLICIT_AMOUNT,
    }
)


def _normalize_text(value: str | None, fallback: str = "") -> str:
    """将可选字符串规范为非空展示文案。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _format_money(amount: float) -> str:
    """将订单金额格式化为两位小数字符串。"""
    return f"{max(0.0, float(amount)):.2f}"


def _resolve_compensation_policy(strategy) -> str:
    """直接采用 Agent2 写入的补偿门禁，仅校验枚举合法性。"""
    policy = str(strategy.compensation_policy or "").strip().lower()
    if policy in _VALID_COMPENSATION_POLICIES:
        return policy
    return COMPENSATION_POLICY_NONE


def _derive_response_mode(input_data: ScriptInput) -> str:
    """报告展示用应对思想（不参与话术门禁，门禁以 Agent2 字段为准）。"""
    strategy = input_data.strategy_output
    malicious = strategy.malicious_detection
    if malicious and (malicious.risk_level or "").strip().lower() == "high":
        return RESPONSE_MODE_MALICIOUS_RISK

    disposition = (strategy.disposition or "").strip().lower()
    action_type = (strategy.action_type or "").strip().lower()
    evidence_quality = (input_data.facts.evidence_quality or "").strip().lower()

    if action_type in {ACTION_RULE_EXPLAIN, ACTION_EVIDENCE_REQUEST, ACTION_RETURN_INSPECTION}:
        return RESPONSE_MODE_NEUTRAL_NEGOTIATE
    if disposition == DISPOSITION_COMPENSATE:
        return RESPONSE_MODE_MERCHANT_FAULT
    if disposition == DISPOSITION_NEGOTIATE and evidence_quality == EVIDENCE_HIGH:
        return RESPONSE_MODE_MERCHANT_FAULT
    return RESPONSE_MODE_NEUTRAL_NEGOTIATE


def _must_state_compensation_amount(
    *,
    action_type: str,
    strategy_stage: str,
    compensation_policy: str,
    response_mode: str,
) -> bool:
    """仅金额动作且 Agent2 已开 explicit_amount 时，要求话术报具体金额。"""
    action = (action_type or "").strip().lower()
    if action not in {ACTION_MONETARY_SETTLE, ACTION_MERCHANT_REMEDY}:
        return False
    if compensation_policy != COMPENSATION_POLICY_EXPLICIT_AMOUNT:
        return False
    if (strategy_stage or "").strip().lower() == STRATEGY_STAGE_EVIDENCE_FIRST:
        return False
    if response_mode == RESPONSE_MODE_MALICIOUS_RISK:
        return False
    return True


def _normalize_chat_turns(chat_history: list[ChatTurn]) -> list[dict[str, str]]:
    """将 ChatTurn 列表转为 role/content dict。"""
    turns: list[dict[str, str]] = []
    for item in chat_history or []:
        role = (item.role or "buyer").strip().lower()
        if role not in {"buyer", "merchant"}:
            role = "buyer"
        content = _normalize_text(item.content)
        if content:
            turns.append({"role": role, "content": content})
    return turns


def _minimal_dialogue_context(input_data: ScriptInput) -> DialogueContext:
    """Agent2 未提供 dialogue_context 时的最小结构。"""
    turns = _normalize_chat_turns(input_data.chat_history)
    missing = list(input_data.facts.missing_evidence or [])
    return DialogueContext(
        dialogue_mode="continue" if turns else "cold_start",
        blocked_evidence_requests=[],
        actionable_evidence_requests=missing,
        fallback_script="我这边还在核对材料，核实完马上回您。" if turns else "您好，我这边还在核对材料，核实完马上回您。",
    )


def _resolve_dialogue_context(input_data: ScriptInput) -> DialogueContext:
    """优先读取 Agent2 输出的 dialogue_context。"""
    ctx = input_data.strategy_output.dialogue_context
    if ctx is not None:
        return ctx
    logger.warning("%s 策略未含 dialogue_context，使用最小结构兜底", AGENT3_LOG_PREFIX)
    return _minimal_dialogue_context(input_data)


_ACTION_TONE_MAP: dict[str, str] = {
    ACTION_EVIDENCE_REQUEST: "专业、引导、不施压，像帮朋友补材料",
    ACTION_RETURN_INSPECTION: "专业、引导、不施压，语气平和",
    ACTION_RULE_EXPLAIN: "冷静、有理有据、不卑不亢",
    ACTION_MONETARY_SETTLE: "果断、清晰、有担当，主动给方案",
    ACTION_MERCHANT_REMEDY: "真诚、贴心、有温度，像店主亲自善后",
    ACTION_DEFEND_PREPARE: "冷静、有理有据、不卑不亢，守住底线",
}


def _build_tone_hint(input_data: ScriptInput) -> str:
    """
    按 action_type 动态生成语气指导，并叠加情绪与客户价值提示。

    输入：ScriptInput（含 strategy_output、emotion_note）
    输出：一句话语气指导字符串
    """
    parts: list[str] = []

    # 1. 按动作类型给基调
    action = (input_data.strategy_output.action_type or "").strip().lower()
    base_tone = _ACTION_TONE_MAP.get(action, "亲切自然、像店主本人")
    parts.append(base_tone)

    # 2. 叠加情绪提示
    emotion = _normalize_text(input_data.emotion_note)
    if emotion:
        parts.append(emotion)

    # 3. 叠加客户价值建议
    customer_value = input_data.strategy_output.customer_value
    if customer_value and customer_value.tone_suggestion:
        parts.append(str(customer_value.tone_suggestion).strip())

    return "；".join(parts) + "；短句优先"


def _coerce_float(value: Any) -> float | None:
    """安全转换浮点数。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_compensation_ratio_cap(input_data: ScriptInput) -> float | None:
    """从事实 rule_context 或比例约束中提取补偿上限比例。"""
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


def _build_script_payload(
    input_data: ScriptInput,
    *,
    compensation_policy: str,
    dialogue_context: DialogueContext,
    must_state_compensation_amount: bool,
) -> dict[str, Any]:
    """组装话术 LLM payload：只传 Agent2 契约字段与必要上下文。"""
    strategy = input_data.strategy_output
    facts = input_data.facts
    payload: dict[str, Any] = {
        "action_type": strategy.action_type,
        "compensation_policy": compensation_policy,
        "strategy_stage": strategy.strategy_stage,
        "next_step": _normalize_text(strategy.next_step),
        "rule_constraints": list(strategy.rule_constraints or []),
        "must_state_compensation_amount": must_state_compensation_amount,
        "dialogue_context": dialogue_context.model_dump(),
        "recent_turns": _normalize_chat_turns(input_data.chat_history),
        "issue_summary": _normalize_text(
            facts.issue_summary,
            fallback="买家反馈了售后问题，细节仍在核对",
        ),
        "order_amount": _format_money(input_data.order_amount),
        "tone_hint": _build_tone_hint(input_data),
    }
    if facts.evidence_quality:
        payload["evidence_quality"] = facts.evidence_quality
    # 视觉观察供话术与 Agent1 结论对齐，避免对外复述或矛盾描述
    visual_observations = [
        str(item).strip() for item in (facts.visual_observations or []) if str(item).strip()
    ]
    if visual_observations:
        payload["visual_observations"] = visual_observations[:5]
    missing = [str(item).strip() for item in (facts.missing_evidence or []) if str(item).strip()]
    if missing:
        payload["missing_evidence"] = missing[:3]

    ratio_cap = _extract_compensation_ratio_cap(input_data)
    if ratio_cap is not None and input_data.order_amount > 0:
        payload["max_compensation_amount"] = round(float(input_data.order_amount) * ratio_cap, 2)

    customer_value = strategy.customer_value
    if customer_value:
        if customer_value.channel:
            payload["customer_value_channel"] = customer_value.channel
        if must_state_compensation_amount and customer_value.compensation_uplift:
            payload["compensation_uplift"] = customer_value.compensation_uplift
    if strategy.malicious_detection and strategy.malicious_detection.risk_level:
        payload["malicious_risk_level"] = strategy.malicious_detection.risk_level
    return payload


def _build_usage_tip(
    *,
    response_mode: str,
    strategy_stage: str,
    must_state_compensation_amount: bool,
) -> str:
    """根据应对思想与策略阶段生成简短使用说明（仅报告展示）。"""
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


def generate(input_data: ScriptInput) -> ScriptOutput:
    """在 Agent2 契约约束下生成单条买家话术；LLM 失败走 dialogue_context.fallback_script。"""
    strategy = input_data.strategy_output
    compensation_policy = _resolve_compensation_policy(strategy)
    response_mode = _derive_response_mode(input_data)
    dialogue_context = _resolve_dialogue_context(input_data)
    must_state_amount = _must_state_compensation_amount(
        action_type=strategy.action_type,
        strategy_stage=strategy.strategy_stage,
        compensation_policy=compensation_policy,
        response_mode=response_mode,
    )

    logger.info(
        "%s 话术生成 action=%s policy=%s must_state_amount=%s",
        AGENT3_LOG_PREFIX,
        strategy.action_type,
        compensation_policy,
        must_state_amount,
    )

    script = generate_buyer_script(
        _build_script_payload(
            input_data,
            compensation_policy=compensation_policy,
            dialogue_context=dialogue_context,
            must_state_compensation_amount=must_state_amount,
        )
    )
    if not script:
        logger.warning("%s 话术 LLM 不可用，使用 fallback_script", AGENT3_LOG_PREFIX)
        script = str(dialogue_context.fallback_script or "").strip()
    if not script:
        script = "我这边还在跟进，核实清楚后马上回复您。"

    return ScriptOutput(
        script=script.strip(),
        response_mode=response_mode,
        usage_tip=_build_usage_tip(
            response_mode=response_mode,
            strategy_stage=strategy.strategy_stage,
            must_state_compensation_amount=must_state_amount,
        ),
    )
