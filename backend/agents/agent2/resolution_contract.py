"""
Agent2 方案空间契约：从事实、规则与画像推断可执行处置方案，驱动 action_type 与 Agent3 边界。

decision_readiness / missing_evidence 为举证闭环真源；offered_modes / forbidden_modes 为话术方案真源。
"""

from __future__ import annotations

import re
from typing import Any

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
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    DISPUTE_FRAME_SEVEN_DAY_RETURN,
    MaliciousDetectionOutput,
    ResolutionContract,
    RULE_CONSTRAINT_RATIO_LIMIT,
    RULE_CONSTRAINT_APPLIES,
    SETTLEMENT_EXCHANGE,
    SETTLEMENT_PARTIAL_COMPENSATE,
    SETTLEMENT_REFUND_FULL,
    SETTLEMENT_REFUND_ONLY,
    SETTLEMENT_RETURN_REFUND,
    StrategyInput,
)
from backend.tools.agent2_tools import is_semantic_pressure_profile
from backend.tools.text_signals import contains_any, signal_group

_MODE_LABELS: dict[str, str] = {
    SETTLEMENT_RETURN_REFUND: "退货退款",
    SETTLEMENT_EXCHANGE: "换货",
    SETTLEMENT_PARTIAL_COMPENSATE: "规则内部分补偿",
    SETTLEMENT_REFUND_FULL: "完成验收后全额退款",
}


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_compensation_ratio_cap(input_data: StrategyInput) -> float | None:
    """从 rule_context 或结构化比例约束提取补偿上限比例（0-1）。"""
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

    for constraint in input_data.rule_constraints or []:
        if str(constraint.constraint_type or "").strip().lower() != RULE_CONSTRAINT_RATIO_LIMIT:
            continue
        if str(constraint.status or "").strip().lower() != RULE_CONSTRAINT_APPLIES:
            continue
        text = str(constraint.text or "")
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if match:
            cap = _coerce_float(match.group(1))
            if cap is not None:
                return cap / 100
    return None


def buyer_demands_refund_only(input_data: StrategyInput) -> bool:
    """买家是否明确要求仅退款不退货。"""
    parts: list[str] = []
    for turn in input_data.chat_turns or []:
        role = (turn.role or "").strip().lower()
        if role == "buyer":
            parts.append(str(turn.content or ""))
    for line in input_data.chat_history or []:
        parts.append(str(line))
    parts.append(str(input_data.facts.issue_summary or ""))
    blob = " ".join(parts)
    return contains_any(blob, signal_group("refund_only_demand_markers"))


def is_decision_ready(input_data: StrategyInput, *, evidence_insufficient: bool) -> bool:
    """举证是否已闭环、可给出处置方案。"""
    return not evidence_insufficient


def infer_resolution_contract(
    input_data: StrategyInput,
    *,
    disposition: str,
    merchant_fault: bool,
    timing_not_satisfied: bool,
    malicious_result: MaliciousDetectionOutput,
    de_escalate_pressure: bool,
    evidence_insufficient: bool,
) -> ResolutionContract:
    """
    推断本案方案空间：禁止什么、必须提供什么可执行选项。

    参数:
        input_data: 策略输入。
        disposition: 处置方向。
        merchant_fault: 是否商责（置信度已在外部判定）。
        timing_not_satisfied: 规则时效是否不满足。
        malicious_result: 恶意检测结果。
        de_escalate_pressure: 是否语义施压降格协商。
        evidence_insufficient: 是否尚不足以终局决策。

    返回:
        ResolutionContract。
    """
    facts = input_data.facts
    decision_ready = is_decision_ready(input_data, evidence_insufficient=evidence_insufficient)
    forbidden: list[str] = []
    offered: list[str] = []
    ratio_cap = extract_compensation_ratio_cap(input_data)
    refund_only_demand = buyer_demands_refund_only(input_data)
    recoverability = str(facts.visual_goods_recoverability or "").strip().lower()
    resalable = recoverability in {"resalable", "recoverable"}
    risk_level = (malicious_result.risk_level or "").strip().lower()
    service_return_frame = str(facts.primary_dispute_frame or "").strip() == DISPUTE_FRAME_SEVEN_DAY_RETURN

    if not decision_ready:
        return ResolutionContract(
            decision_ready=False,
            forbidden_modes=[],
            offered_modes=[],
            require_inspection_before_refund=False,
            compensation_ratio_cap=ratio_cap,
        )

    if refund_only_demand or de_escalate_pressure or risk_level in {"medium", "high"}:
        forbidden.append(SETTLEMENT_REFUND_ONLY)

    if timing_not_satisfied:
        forbidden.append(SETTLEMENT_REFUND_FULL)

    if merchant_fault and not service_return_frame:
        forbidden.append(SETTLEMENT_REFUND_ONLY)
        offered.extend([SETTLEMENT_RETURN_REFUND, SETTLEMENT_EXCHANGE])
        if ratio_cap and ratio_cap > 0:
            offered.append(SETTLEMENT_PARTIAL_COMPENSATE)
        return ResolutionContract(
            decision_ready=True,
            forbidden_modes=_dedupe_modes(forbidden),
            offered_modes=_dedupe_modes(offered),
            require_inspection_before_refund=True,
            compensation_ratio_cap=ratio_cap,
        )

    if refund_only_demand and resalable:
        offered.append(SETTLEMENT_RETURN_REFUND)
        offered.append(SETTLEMENT_EXCHANGE)

    if timing_not_satisfied:
        if ratio_cap and ratio_cap > 0:
            offered.append(SETTLEMENT_PARTIAL_COMPENSATE)
        elif recoverability == "unrecoverable":
            offered.append(SETTLEMENT_RETURN_REFUND)
    elif disposition in {DISPOSITION_NEGOTIATE, DISPOSITION_DEFEND}:
        if ratio_cap and ratio_cap > 0:
            offered.append(SETTLEMENT_PARTIAL_COMPENSATE)
        if resalable or recoverability == "unrecoverable":
            if SETTLEMENT_RETURN_REFUND not in offered:
                offered.append(SETTLEMENT_RETURN_REFUND)

    if disposition == DISPOSITION_COMPENSATE and not merchant_fault:
        if ratio_cap and ratio_cap > 0:
            offered.append(SETTLEMENT_PARTIAL_COMPENSATE)
        offered.append(SETTLEMENT_RETURN_REFUND)

    if de_escalate_pressure and not offered:
        offered.append(SETTLEMENT_RETURN_REFUND)
        if ratio_cap and ratio_cap > 0:
            offered.append(SETTLEMENT_PARTIAL_COMPENSATE)

    if decision_ready and service_return_frame:
        if SETTLEMENT_RETURN_REFUND not in offered:
            offered.append(SETTLEMENT_RETURN_REFUND)

    return ResolutionContract(
        decision_ready=True,
        forbidden_modes=_dedupe_modes(forbidden),
        offered_modes=_dedupe_modes(offered),
        require_inspection_before_refund=False,
        compensation_ratio_cap=ratio_cap,
    )


def finalize_proposed_compensation(
    resolution: ResolutionContract,
    *,
    order_amount: float,
) -> ResolutionContract:
    """
    方案空间含部分补偿时，在契约中写入确定报价（元）。

    比例仅作内部折算，面向买家只说具体金额，不说百分之/订单占比。
    """
    if SETTLEMENT_PARTIAL_COMPENSATE not in resolution.offered_modes:
        return resolution
    cap = resolution.compensation_ratio_cap
    if cap is None or order_amount <= 0:
        return resolution
    proposed = round(float(order_amount) * cap, 2)
    return resolution.model_copy(update={"proposed_compensation_amount": proposed})


def _dedupe_modes(modes: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for mode in modes:
        if mode not in seen:
            seen.add(mode)
            ordered.append(mode)
    return ordered


def _format_offered_modes(resolution: ResolutionContract) -> str:
  labels = [_MODE_LABELS.get(mode, mode) for mode in resolution.offered_modes]
  return "、".join(labels)


def compose_offered_modes_next_step(
    resolution: ResolutionContract,
    *,
    de_escalate_pressure: bool = False,
) -> str:
    """根据方案空间生成对内 next_step。"""
    if not resolution.offered_modes:
        return "先向买家说明规则边界，再根据反馈推进"

    offered_text = _format_offered_modes(resolution)
    parts: list[str] = []
    if de_escalate_pressure:
        parts.append("先承接买家情绪")
    parts.append(f"向买家明确可接受方案：{offered_text}")
    if SETTLEMENT_RETURN_REFUND in resolution.offered_modes:
        parts.append("按退货寄回并验收后处理")
    if SETTLEMENT_REFUND_ONLY in resolution.forbidden_modes:
        parts.append("说明不接受仅退款不退货")
    if resolution.require_inspection_before_refund:
        parts.append("退款须走退货验收，验收前不承诺到账")
    if resolution.proposed_compensation_amount is not None:
        parts.append(f"向买家报价{resolution.proposed_compensation_amount:g}元部分补偿（勿向买家说比例或百分之）")
    parts.append("征求买家是否接受")
    return "，".join(parts) + "。"


def _compose_evidence_first_next_step(input_data: StrategyInput) -> str:
    """举证未闭环时的下一步（与 strategist 语义一致，避免循环依赖）。"""
    facts = input_data.facts
    red_flags = [str(item).strip() for item in (facts.red_flags or []) if str(item).strip()]
    missing = [str(item).strip() for item in (facts.missing_evidence or []) if str(item).strip()]
    parts: list[str] = []
    if red_flags:
        parts.append(f"先围绕“{red_flags[0]}”核验关键事实")
    else:
        parts.append("先把当前关键事实核验清楚")
    if missing:
        parts.append(f"请买家补充{'、'.join(missing[:3])}")
    else:
        parts.append("请买家补充可核实责任归属的材料")
    parts.append("商家同步固定发货、聊天和已有举证记录，事实闭环后再判断是否退款、补偿或抗辩")
    return "，".join(parts)


def materialize_action_from_resolution(
    resolution: ResolutionContract,
    *,
    input_data: StrategyInput,
    disposition: str,
    merchant_fault: bool,
    timing_not_satisfied: bool,
    de_escalate_pressure: bool,
    timing_constraint_text: str = "",
) -> dict[str, Any]:
    """
    由方案空间契约物化 action_type、compensation_policy、next_step。

    返回:
        含 action_type、compensation_policy、next_step 的字典。
    """
    if not resolution.decision_ready:
        return {
            "action_type": ACTION_EVIDENCE_REQUEST,
            "compensation_policy": COMPENSATION_POLICY_FORBID,
            "next_step": _compose_evidence_first_next_step(input_data),
        }

    if merchant_fault and resolution.require_inspection_before_refund:
        policy = (
            COMPENSATION_POLICY_EXPLICIT_AMOUNT
            if disposition == DISPOSITION_COMPENSATE
            else COMPENSATION_POLICY_SOFT_NO_AMOUNT
        )
        return {
            "action_type": ACTION_MERCHANT_REMEDY,
            "compensation_policy": policy,
            "next_step": compose_offered_modes_next_step(resolution),
        }

    partial_primary = (
        SETTLEMENT_PARTIAL_COMPENSATE in resolution.offered_modes
        and resolution.compensation_ratio_cap
        and disposition in {DISPOSITION_NEGOTIATE, DISPOSITION_DEFEND, DISPOSITION_COMPENSATE}
    )
    if partial_primary:
        return {
            "action_type": ACTION_MONETARY_SETTLE,
            "compensation_policy": COMPENSATION_POLICY_EXPLICIT_AMOUNT,
            "next_step": compose_offered_modes_next_step(
                resolution,
                de_escalate_pressure=de_escalate_pressure,
            ),
        }

    if timing_not_satisfied and not resolution.offered_modes:
        return {
            "action_type": ACTION_RULE_EXPLAIN,
            "compensation_policy": COMPENSATION_POLICY_NONE,
            "next_step": timing_constraint_text or "先向买家说明规则时效与处理边界，再核验保存方式和举证材料",
        }

    if resolution.offered_modes:
        policy = COMPENSATION_POLICY_NONE
        action_type = ACTION_RULE_EXPLAIN
        if (
            SETTLEMENT_PARTIAL_COMPENSATE in resolution.offered_modes
            and len(resolution.offered_modes) == 1
        ):
            action_type = ACTION_MONETARY_SETTLE
            policy = COMPENSATION_POLICY_EXPLICIT_AMOUNT
        return {
            "action_type": action_type,
            "compensation_policy": policy,
            "next_step": compose_offered_modes_next_step(
                resolution,
                de_escalate_pressure=de_escalate_pressure,
            ),
        }

    if disposition == DISPOSITION_DEFEND and not de_escalate_pressure:
        return {
            "action_type": ACTION_DEFEND_PREPARE,
            "compensation_policy": COMPENSATION_POLICY_NONE,
            "next_step": "按规则说明当前不满足直接退款或补偿条件，并整理聊天、订单、物流和举证材料以备平台介入",
        }

    return {
        "action_type": ACTION_RETURN_INSPECTION,
        "compensation_policy": COMPENSATION_POLICY_SOFT_NO_AMOUNT,
        "next_step": "按退回验收流程推进，结果确认前不承诺最终退款或补偿",
    }


def should_de_escalate_pressure(
    malicious_result: MaliciousDetectionOutput,
    *,
    responsibility: str,
    responsibility_confidence: float,
    merchant_fault: bool,
) -> bool:
    """高/中恶意但仅语义施压、且非明确商责时，对外协商降格。"""
    from schemas import RESPONSIBILITY_MERCHANT

    if merchant_fault:
        return False
    if responsibility == RESPONSIBILITY_MERCHANT and responsibility_confidence >= 0.6:
        return False
    level = (malicious_result.risk_level or "").strip().lower()
    if level not in {"high", "medium"}:
        return False
    return is_semantic_pressure_profile(malicious_result)
