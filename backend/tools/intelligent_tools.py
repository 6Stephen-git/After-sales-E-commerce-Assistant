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
    ToolFinding,
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

# 视觉分析成功后应从 missing 核销的通用举证项（全品类复用）
_GENERIC_PHOTO_EVIDENCE_LABELS = frozenset({"买家举证图片", "商品实物照片"})
_LOGISTICS_EVIDENCE_LABELS = frozenset({"物流信息", "物流状态"})
# tool_findings 保留上限，超出丢弃最旧条目
MAX_TOOL_FINDINGS = 24


def _clone_state(state: IntelligentState, **updates: Any) -> IntelligentState:
    """基于现有状态浅拷贝并覆盖指定字段。"""
    data = state.model_dump()
    data.update(updates)
    return IntelligentState.model_validate(data)


def summarize_tool_result(tool_name: str, payload: Any) -> tuple[str, dict[str, Any]]:
    """
    将工具原始返回提炼为 (自然语言摘要, 结构化 facts)。

    参数:
        tool_name: 工具名。
        payload: dict / JSON 字符串 / 具 model_dump 的对象。

    返回:
        (summary, facts) 供 record_tool_finding 写入 Redis。
    """
    if hasattr(payload, "model_dump"):
        data: dict[str, Any] = payload.model_dump()
    elif isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            data = parsed if isinstance(parsed, dict) else {"raw": payload}
        except json.JSONDecodeError:
            data = {"raw": payload}
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {"raw": str(payload)}

    if data.get("error"):
        err = str(data.get("error") or "工具执行失败")
        return f"{tool_name} 失败：{err}", {"error": err}

    if tool_name == "analyze_image_simple":
        desc = str(data.get("visual_description") or "").strip() or "（无视觉描述）"
        defect = str(data.get("defect_type") or "").strip()
        severity = str(data.get("visual_defect_severity") or "").strip()
        parts = [desc]
        if defect:
            parts.append(f"现象：{defect}")
        if severity:
            parts.append(f"程度：{severity}")
        summary = "；".join(parts)
        facts = {
            k: data.get(k)
            for k in (
                "visual_description",
                "defect_type",
                "defect_location",
                "visual_defect_severity",
                "visual_goods_recoverability",
                "credential_trust",
            )
            if data.get(k) not in (None, "", [])
        }
        return summary, facts

    if tool_name == "query_logistics":
        order_id = str(data.get("order_id") or "").strip()
        status_text = str(data.get("status_text") or "").strip()
        is_signed = data.get("is_signed")
        stagnant = data.get("stagnant_days")
        if status_text:
            summary = f"订单{order_id}：{status_text}" if order_id else status_text
        elif is_signed is True:
            summary = f"订单{order_id}已签收" + (f"，停滞{stagnant}天" if stagnant is not None else "")
        elif is_signed is False:
            summary = f"订单{order_id}未签收" if order_id else "物流未签收"
        else:
            summary = f"订单{order_id}物流已查询" if order_id else "物流已查询"
        facts = {
            k: data.get(k)
            for k in ("order_id", "is_shipped", "is_signed", "stagnant_days", "is_abnormal", "status_text")
            if data.get(k) is not None
        }
        return summary, facts

    if tool_name == "query_buyer_profile":
        credit = str(data.get("credit_level") or "").strip()
        purchase_count = data.get("purchase_count")
        dispute_rate = data.get("dispute_rate")
        summary = f"信誉={credit or '未知'}"
        if purchase_count is not None:
            summary += f"，购买{purchase_count}次"
        if dispute_rate is not None:
            summary += f"，纠纷率{dispute_rate}"
        facts = {
            k: data.get(k)
            for k in ("credit_level", "purchase_count", "dispute_rate", "is_high_value", "buyer_type_hint")
            if data.get(k) not in (None, "")
        }
        return summary, facts

    if tool_name == "match_rules_simple":
        count = int(data.get("count") or 0)
        briefs = data.get("briefs") or []
        top = briefs[0] if briefs else ""
        summary = f"匹配规则 {count} 条" + (f"，首条：{top}" if top else "")
        facts = {"count": count, "briefs": briefs[:3]}
        return summary, facts

    if tool_name == "evaluate_customer_value_simple":
        channel = str(data.get("channel") or "").strip()
        uplift = data.get("compensation_uplift")
        summary = f"客户价值通道={channel or '未知'}"
        if uplift is not None:
            summary += f"，补偿系数{uplift}"
        facts = {
            k: data.get(k)
            for k in (
                "long_term_score",
                "order_score",
                "channel",
                "long_term_triggered",
                "order_triggered",
                "compensation_uplift",
                "tone_suggestion",
            )
            if data.get(k) is not None
        }
        return summary, facts

    if tool_name == "detect_malicious_simple":
        risk_level = str(data.get("risk_level") or "").strip()
        score = data.get("risk_score")
        advice = str(data.get("disposition_advice") or "").strip()
        summary = f"恶意风险={risk_level or 'unknown'}"
        if score is not None:
            summary += f"（{score}）"
        if advice:
            summary += f"，建议：{advice}"
        facts = {
            k: data.get(k)
            for k in ("risk_score", "risk_level", "disposition_advice", "signals")
            if data.get(k) not in (None, "", [])
        }
        return summary, facts

    if tool_name == "search_similar_cases_simple":
        count = int(data.get("count") or 0)
        cases = data.get("cases") or []
        lesson = cases[0].get("lesson", "") if cases and isinstance(cases[0], dict) else ""
        summary = f"相似案例 {count} 条" + (f"，参考：{lesson}" if lesson else "")
        facts = {"count": count, "cases": cases[:2]}
        return summary, facts

    raw = json.dumps(data, ensure_ascii=False)
    return raw[:200], {"raw_preview": raw[:200]}


def record_tool_finding(
    state: IntelligentState,
    tool_name: str,
    turn: int,
    payload: Any,
    *,
    extra_facts: dict[str, Any] | None = None,
    evidence_update_reason: str = "",
) -> IntelligentState:
    """
    记录工具结论到 tool_findings，并按工具类型同步 evidence_summary。

    视觉/物流工具在写入 finding 后自动核销对应缺证项；update_state 不产生 finding。
    """
    if tool_name == "update_state":
        return state

    summary, facts = summarize_tool_result(tool_name, payload)
    if extra_facts:
        facts = {**facts, **{k: v for k, v in extra_facts.items() if v not in (None, "")}}

    new_findings = list(state.tool_findings)
    new_findings.append(
        ToolFinding(
            tool=tool_name,
            turn=turn,
            summary=summary[:500],
            facts=facts,
        )
    )
    if len(new_findings) > MAX_TOOL_FINDINGS:
        new_findings = new_findings[-MAX_TOOL_FINDINGS:]

    state = _clone_state(state, tool_findings=new_findings)

    # 证据标签与 tool_findings 同路径写入，避免调用方重复 sync
    payload_dict = payload.model_dump() if hasattr(payload, "model_dump") else payload
    if not isinstance(payload_dict, dict):
        payload_dict = {}

    if tool_name == "analyze_image_simple" and not payload_dict.get("error"):
        reason = evidence_update_reason or f"第{turn + 1}轮视觉分析完成"
        state = sync_state_after_vision(state, payload_dict, update_reason=reason)
    elif tool_name == "query_logistics" and not payload_dict.get("error"):
        reason = evidence_update_reason or "已查询物流状态"
        state = sync_state_after_logistics(state, payload_dict, update_reason=reason)

    logger.info(
        "%s 记录工具事实 tool=%s turn=%s summary=%s",
        LOG_PREFIX,
        tool_name,
        turn,
        summary[:80],
    )
    return state


def format_tool_findings_for_prompt(state: IntelligentState) -> str:
    """
    将 tool_findings 格式化为 system prompt 追加块。

    不向买家复述；帮助模型知晓已执行工具及结论。
    """
    if not state.tool_findings:
        return ""

    lines = [
        "---",
        "已确认的工具事实（勿向买家复述；据此判断已做过什么、还未做什么）：",
    ]
    for item in state.tool_findings:
        line = f"- [第{item.turn}轮] {item.tool}：{item.summary}"
        lines.append(line)
    return "\n".join(lines)


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
            missing=list(update_input.evidence_summary.missing),
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
        tool_findings=list(state.tool_findings),
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


def _merge_evidence_summary(
    state: IntelligentState,
    *,
    collected_add: list[str] | None = None,
    missing_remove: frozenset[str] | None = None,
    quality: str | None = None,
) -> EvidenceSummaryInput:
    """
    合并证据摘要增量：追加 collected、从 missing 移除指定项、可选更新 quality。

    参数:
        state: 当前状态。
        collected_add: 本轮新增已收集项。
        missing_remove: 要从 missing 剔除的标签集合。
        quality: 新质量等级；None 表示保留原值。

    返回:
        EvidenceSummaryInput 供 update_state 使用。
    """
    from schemas import VALID_EVIDENCE_QUALITY

    merged_collected = set(state.evidence_summary.collected)
    if collected_add:
        merged_collected.update(item.strip() for item in collected_add if str(item or "").strip())

    merged_missing = list(state.evidence_summary.missing)
    if missing_remove:
        merged_missing = [m for m in merged_missing if m not in missing_remove]

    resolved_quality = quality if quality else state.evidence_summary.quality
    if resolved_quality not in VALID_EVIDENCE_QUALITY:
        resolved_quality = state.evidence_summary.quality

    return EvidenceSummaryInput(
        collected=list(merged_collected),
        missing=merged_missing,
        quality=resolved_quality,
    )


def sync_state_after_vision(
    state: IntelligentState,
    vision: dict[str, Any],
    *,
    update_reason: str,
) -> IntelligentState:
    """
    视觉分析成功后，将结论写入 IntelligentState.evidence_summary。

    触发条件：vision 返回有效 JSON 且无 error 字段。
    业务含义：买家附图已分析，通用「缺图」项核销，避免下轮重复索要。
    """
    if not isinstance(vision, dict) or vision.get("error"):
        return state

    collected_label = "买家举证图片"
    defect_type = str(vision.get("defect_type") or "").strip()
    if defect_type:
        collected_label = f"买家举证图片（{defect_type}）"

    quality = state.evidence_summary.quality
    severity = str(vision.get("visual_defect_severity") or "").strip().lower()
    from schemas import EVIDENCE_HIGH, EVIDENCE_MEDIUM

    if severity == "severe":
        quality = EVIDENCE_HIGH
    elif severity in ("moderate", "minor") and quality != EVIDENCE_HIGH:
        quality = EVIDENCE_MEDIUM

    evidence = _merge_evidence_summary(
        state,
        collected_add=[collected_label],
        missing_remove=_GENERIC_PHOTO_EVIDENCE_LABELS,
        quality=quality,
    )
    return update_state(
        state,
        UpdateStateInput(
            dispute_id=state.dispute_id,
            update_reason=update_reason,
            evidence_summary=evidence,
        ),
    )


def sync_state_after_logistics(
    state: IntelligentState,
    logistics_payload: dict[str, Any],
    *,
    update_reason: str = "已查询物流状态",
) -> IntelligentState:
    """
    物流工具返回后，将物流状态记入 evidence_summary.collected。
    """
    if not isinstance(logistics_payload, dict) or logistics_payload.get("error"):
        return state

    evidence = _merge_evidence_summary(
        state,
        collected_add=["物流状态"],
        missing_remove=_LOGISTICS_EVIDENCE_LABELS,
    )
    return update_state(
        state,
        UpdateStateInput(
            dispute_id=state.dispute_id,
            update_reason=update_reason,
            evidence_summary=evidence,
        ),
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
    dismiss_round_handoff: bool = False,
) -> tuple[bool, bool, str]:
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
        dismiss_round_handoff: 用户已选择继续对话，跳过轮次建议。

    返回:
        (force_handoff, suggest_handoff, reason)
        — force 为强制转人工；suggest 为建议转人工（可继续）。
    """
    message_lower = buyer_message.strip().lower() if buyer_message else ""

    # 买家明确要求转人工
    if message_lower and any(kw in message_lower for kw in HANDOFF_KEYWORDS):
        return True, False, "买家明确要求转人工或提及投诉/法律途径"

    # 高价值订单
    if order_amount >= DEFAULT_AMOUNT_THRESHOLD:
        return True, False, f"订单金额 {order_amount:.0f} 元，超过自动处理阈值 {DEFAULT_AMOUNT_THRESHOLD:.0f} 元"

    # 恶意行为高风险
    if risk_level == "high" and buyer_type in ("suspicious", "malicious"):
        return True, False, "恶意行为风险高，需人工介入判断"

    # 轮次过多：建议转人工，由前端让用户选择是否继续
    if not dismiss_round_handoff and round_count >= DEFAULT_HANDOFF_ROUNDS:
        return False, True, f"对话已进行 {round_count} 轮，建议人工接管"

    return False, False, ""


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

    if state.tool_findings:
        findings_text = "; ".join(
            f"第{f.turn}轮{f.tool}：{f.summary}" for f in state.tool_findings[-5:]
        )
        parts.append(f"工具事实：{findings_text}")

    if state.last_update_reason:
        parts.append(f"最近更新：{state.last_update_reason}")

    if chat_history_text:
        parts.append(f"对话摘要：{chat_history_text[:200]}")

    return "\n".join(parts)
