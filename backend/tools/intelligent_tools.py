"""
智能模式工具集：状态更新、转人工判断等。

职责：供 conversation_agent function calling 使用的专用工具。
约束：不修改辅助模式任何代码。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from schemas import (
    AgentReply,
    EvidenceSummaryInput,
    IntelligentState,
    KeyDecision,
    ToolCallLog,
    UpdateStateInput,
    VALID_INTEL_PHASES,
    VALID_RESPONSIBILITIES,
    VALID_INTEL_STRATEGIES,
    VALID_BUYER_TYPES,
    VALID_RISK_LEVELS,
    INTEL_PHASE_HANDOFF,
)

LOG_PREFIX = "[IntelligentTools]"
logger = logging.getLogger(__name__)


def update_state(
    state: IntelligentState,
    update_input: UpdateStateInput,
) -> IntelligentState:
    """
    更新案件状态。仅在案件情况发生实质性变化时调用。

    参数:
        state: 当前状态。
        update_input: 更新内容。

    返回:
        更新后的新 IntelligentState 实例（不修改原对象）。
    """
    logger.info(
        "%s 状态更新开始 dispute_id=%s reason=%s",
        LOG_PREFIX,
        state.dispute_id,
        update_input.update_reason,
    )

    # 复制当前状态字段
    new_phase = update_input.phase if (update_input.phase and update_input.phase in VALID_INTEL_PHASES) else state.phase
    new_responsibility = (
        update_input.responsibility
        if (update_input.responsibility and update_input.responsibility in VALID_RESPONSIBILITIES)
        else state.responsibility
    )
    new_strategy = (
        update_input.current_strategy
        if (update_input.current_strategy and update_input.current_strategy in VALID_INTEL_STRATEGIES)
        else state.current_strategy
    )
    new_rationale = update_input.strategy_rationale if update_input.strategy_rationale is not None else state.strategy_rationale
    new_buyer_type = (
        update_input.buyer_type
        if (update_input.buyer_type and update_input.buyer_type in VALID_BUYER_TYPES)
        else state.buyer_type
    )
    new_risk_level = (
        update_input.risk_level
        if (update_input.risk_level and update_input.risk_level in VALID_RISK_LEVELS)
        else state.risk_level
    )
    new_risk_signals = update_input.risk_signals if update_input.risk_signals is not None else list(state.risk_signals)

    # 证据摘要：合并更新
    if update_input.evidence_summary is not None:
        existing_collected = set(state.evidence_summary.collected)
        new_collected = existing_collected | set(update_input.evidence_summary.collected)
        from schemas import EvidenceSummary

        new_evidence = EvidenceSummary(
            collected=list(new_collected),
            missing=list(update_input.evidence_summary.missing) if update_input.evidence_summary.missing else list(state.evidence_summary.missing),
            quality=update_input.evidence_summary.quality if update_input.evidence_summary.quality else state.evidence_summary.quality,
        )
    else:
        new_evidence = state.evidence_summary

    # 关键决策：追加
    new_decisions = list(state.key_decisions)
    if update_input.key_decision is not None:
        new_decisions.append(update_input.key_decision)

    now_str = datetime.now(timezone.utc).isoformat()

    updated_state = IntelligentState(
        dispute_id=state.dispute_id,
        phase=new_phase,
        responsibility=new_responsibility,
        current_strategy=new_strategy,
        strategy_rationale=new_rationale,
        buyer_type=new_buyer_type,
        evidence_summary=new_evidence,
        risk_level=new_risk_level,
        risk_signals=new_risk_signals,
        key_decisions=new_decisions,
        tool_calls_log=list(state.tool_calls_log),
        last_update_reason=update_input.update_reason,
        updated_at=now_str,
    )

    logger.info(
        "%s 状态更新完成 dispute_id=%s phase=%s responsibility=%s strategy=%s risk=%s",
        LOG_PREFIX,
        updated_state.dispute_id,
        updated_state.phase,
        updated_state.responsibility,
        updated_state.current_strategy,
        updated_state.risk_level,
    )
    return updated_state


def log_tool_call(state: IntelligentState, tool_name: str, turn: int, result_summary: str = "") -> IntelligentState:
    """
    记录工具调用到状态日志中，返回更新后的状态。

    参数:
        state: 当前状态。
        tool_name: 工具名称。
        turn: 当前对话轮次。
        result_summary: 调用结果摘要。

    返回:
        更新后的 IntelligentState。
    """
    new_log = list(state.tool_calls_log)
    new_log.append(ToolCallLog(tool=tool_name, turn=turn, result_summary=result_summary))

    return IntelligentState(
        dispute_id=state.dispute_id,
        phase=state.phase,
        responsibility=state.responsibility,
        current_strategy=state.current_strategy,
        strategy_rationale=state.strategy_rationale,
        buyer_type=state.buyer_type,
        evidence_summary=state.evidence_summary,
        risk_level=state.risk_level,
        risk_signals=state.risk_signals,
        key_decisions=state.key_decisions,
        tool_calls_log=new_log,
        last_update_reason=state.last_update_reason,
        updated_at=state.updated_at,
    )


# ---------- 转人工阈值判断 ----------

# 默认阈值配置（可后续从商家配置读取）
DEFAULT_AMOUNT_THRESHOLD = 500.0  # 订单金额超此值建议转人工
DEFAULT_HANDOFF_ROUNDS = 6  # 对话轮次超此值且无进展建议转人工

# 买家明确要求转人工的关键词
HANDOFF_KEYWORDS = frozenset({
    "转人工", "找人工", "人工客服", "叫你们老板", "找经理", "投诉",
    "12315", "工商", "法院", "起诉", "报警", "消协",
})


def check_handoff_threshold(
    *,
    order_amount: float = 0.0,
    buyer_type: str = "",
    risk_level: str = "",
    buyer_message: str = "",
    platform_service_tags: list[str] | None = None,
    round_count: int = 0,
    max_compensation: float = 0.0,
) -> tuple[bool, str]:
    """
    检查是否触发转人工阈值。

    参数:
        order_amount: 订单金额。
        buyer_type: 买家类型。
        risk_level: 风险等级。
        buyer_message: 买家最新消息。
        platform_service_tags: 平台服务标。
        round_count: 当前对话轮次。
        max_compensation: 商家赔偿上限。

    返回:
        (should_handoff, reason) — 是否转人工及原因。
    """
    message_lower = buyer_message.strip().lower() if buyer_message else ""

    # 买家明确要求转人工
    if message_lower and any(kw in message_lower for kw in HANDOFF_KEYWORDS):
        return True, "买家明确要求转人工或提及投诉/法律途径"

    # 高价值订单
    if order_amount >= DEFAULT_AMOUNT_THRESHOLD:
        return True, f"订单金额 {order_amount:.0f} 元，超过自动处理阈值 {DEFAULT_AMOUNT_THRESHOLD:.0f} 元"

    # 恶意行为高风险
    if risk_level == "high" and buyer_type in ("suspicious", "malicious"):
        return True, "恶意行为风险高，需人工介入判断"

    # 轮次过多无进展
    if round_count >= DEFAULT_HANDOFF_ROUNDS:
        return True, f"对话已进行 {round_count} 轮，建议人工接管"

    return False, ""


def build_handoff_summary(state: IntelligentState, chat_history_text: str = "") -> str:
    """
    从 IntelligentState 构建转人工交接摘要。

    参数:
        state: 当前案件状态。
        chat_history_text: 对话历史摘要。

    返回:
        面向人工客服的交接摘要文本。
    """
    parts: list[str] = []
    parts.append(f"【案件状态】阶段：{state.phase}，责任归属：{state.responsibility}")
    parts.append(f"当前策略：{state.current_strategy}（{state.strategy_rationale}）")
    parts.append(f"买家类型：{state.buyer_type}，风险等级：{state.risk_level}")

    if state.risk_signals:
        parts.append(f"风险信号：{', '.join(state.risk_signals)}")

    if state.evidence_summary.collected:
        parts.append(f"已收集证据：{', '.join(state.evidence_summary.collected)}")
    if state.evidence_summary.missing:
        parts.append(f"缺失证据：{', '.join(state.evidence_summary.missing)}")

    if state.key_decisions:
        decisions_text = "; ".join(
            f"第{d.turn}轮：{d.decision}（{d.reason}）" for d in state.key_decisions
        )
        parts.append(f"关键决策：{decisions_text}")

    if state.last_update_reason:
        parts.append(f"最近更新：{state.last_update_reason}")

    if chat_history_text:
        parts.append(f"对话摘要：{chat_history_text[:200]}")

    return "\n".join(parts)
