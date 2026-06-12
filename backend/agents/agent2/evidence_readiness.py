"""
举证就绪度：策略层唯一门控，判定是否锁定 evidence_first 及可执行补证项。

仅当规则约束 status=missing_fact 或恶意高风险时阻断终局决策；其余缺证写入风险说明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from schemas import (
    ChatTurn,
    FactOutput,
    MaliciousDetectionOutput,
    EVIDENCE_LOW,
    RULE_CONSTRAINT_EVIDENCE,
    RULE_CONSTRAINT_MISSING_FACT,
    RULE_CONSTRAINT_TIMING,
    RuleConstraint,
)
from backend.tools.text_signals import contains_any, signal_group

# ---------- 买家可补 vs 商家后台操作类缺口 ----------
_OPERATIONAL_GAP_MARKERS = (
    "订单号",
    "快递单号",
    "物流状态",
    "缺少买家文字",
    "聊天记录缺失",
    "签收证明",
    "签收截图",
    "签收时间",
    "官方证明",
)

# ---------- 商家索要举证 / 买家拒证（全品类口语） ----------
_EVIDENCE_REQUEST_MARKERS = (
    "视频",
    "照片",
    "图片",
    "开箱",
    "外包装",
    "面单",
    "序列号",
    "单号",
    "凭证",
    "特写",
    "全貌",
)

_REFUSAL_MARKERS = (
    "没录",
    "未录",
    "没有录",
    "没想录",
    "没拍",
    "未拍",
    "没法录",
    "无法录",
    "不方便",
    "补不了",
    "提供不了",
    "没有视频",
    "扔了",
    "丢了",
    "只有照片",
    "就拍了",
    "已经扔了",
)


@dataclass
class EvidenceReadiness:
    """举证是否阻断当前终局策略决策。"""

    blocks_decision: bool = False
    blocking_reasons: list[str] = field(default_factory=list)


def is_operational_evidence_gap(text: str) -> bool:
    """
    判断缺证描述是否属于商家后台操作项，而非应向买家索要的举证。

    参数:
        text: missing_evidence 单条文案。

    返回:
        True 表示不应进入买家面向补证动作。
    """
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return contains_any(normalized, _OPERATIONAL_GAP_MARKERS)


def buyer_facing_missing_evidence(facts: FactOutput) -> list[str]:
    """
    从事实层提取面向买家的缺证列表，过滤后台操作类条目。

    参数:
        facts: Agent1 事实输出。

    返回:
        去重后的缺证短句列表。
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in facts.missing_evidence or []:
        text = str(raw or "").strip()
        if not text or is_operational_evidence_gap(text):
            continue
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result


def evidence_gap_blocks_settlement(facts: FactOutput) -> bool:
    """
    缺证是否足以阻断终局决策：低证据或无视觉结论时阻断；中/高证据且已有视觉观察时仅作风险提示。

    参数:
        facts: Agent1 事实输出。

    返回:
        True 表示缺证仍应优先补证后再决策。
    """
    gaps = buyer_facing_missing_evidence(facts)
    if not gaps:
        return False
    evidence_quality = (facts.evidence_quality or "").strip().lower()
    if evidence_quality == EVIDENCE_LOW:
        return True
    if not (facts.visual_observations or []):
        return True
    return False


def assess_evidence_readiness(
    *,
    facts: FactOutput,
    rule_constraints: Iterable[RuleConstraint],
    malicious_result: MaliciousDetectionOutput,
) -> EvidenceReadiness:
    """
    分级门控：举证类 missing_fact（且缺证仍阻断）或恶意高风险时锁定 evidence_first。

    时效类 missing_fact 由 rule_explain 动作契约处理，不重复锁举证阶段。

    参数:
        facts: Agent1 事实输出。
        rule_constraints: 结构化规则约束（来自条文匹配）。
        malicious_result: 恶意检测结果。

    返回:
        EvidenceReadiness，含是否阻断及原因列表。
    """
    reasons: list[str] = []
    gap_blocks = evidence_gap_blocks_settlement(facts)
    for item in rule_constraints or []:
        if str(item.status or "").strip() != RULE_CONSTRAINT_MISSING_FACT:
            continue
        constraint_type = str(item.constraint_type or "").strip()
        if constraint_type == RULE_CONSTRAINT_TIMING:
            continue
        if constraint_type == RULE_CONSTRAINT_EVIDENCE and not gap_blocks:
            continue
        text = str(item.text or "").strip()
        reasons.append(text or "规则要求的事实尚未核验闭环")

    risk_level = str(malicious_result.risk_level or "").strip().lower()
    if risk_level == "high":
        advice = str(malicious_result.disposition_advice or "").strip()
        reasons.append(advice or "买家恶意风险高，需先固定举证与规则边界")

    unique_reasons = list(dict.fromkeys(reason for reason in reasons if reason))
    return EvidenceReadiness(
        blocks_decision=bool(unique_reasons),
        blocking_reasons=unique_reasons,
    )


def _extract_evidence_topic(merchant_text: str) -> str | None:
    """从商家话术提取举证主题短语，供拒证配对。"""
    text = str(merchant_text or "").strip()
    if not text or not contains_any(text, _EVIDENCE_REQUEST_MARKERS):
        return None
    for marker in signal_group("colloquial_must_blocklist"):
        if marker in text:
            return marker
    for marker in _EVIDENCE_REQUEST_MARKERS:
        if marker in text:
            return marker
    return "举证材料"


def derive_blocked_evidence_requests(chat_turns: Iterable[ChatTurn]) -> list[str]:
    """
    从近期对话推导买家已拒举证项：商家索要后买家明确无法/不愿提供。

    参数:
        chat_turns: 带角色的聊天轮次。

    返回:
        去重后的已阻断举证主题列表。
    """
    blocked: list[str] = []
    pending_topic: str | None = None
    for turn in chat_turns or []:
        role = str(turn.role or "").strip().lower()
        content = str(turn.content or "").strip()
        if not content:
            continue
        if role in {"merchant", "seller", "商家"}:
            pending_topic = _extract_evidence_topic(content)
            continue
        if role in {"buyer", "买家"} and pending_topic:
            if contains_any(content, _REFUSAL_MARKERS):
                if pending_topic not in blocked:
                    blocked.append(pending_topic)
            pending_topic = None
    return blocked


def _is_blocked_item(item: str, blocked: Iterable[str]) -> bool:
    """缺证项与已拒主题是否互含（口语模糊匹配）。"""
    normalized = str(item or "").strip()
    if not normalized:
        return True
    for topic in blocked:
        topic_text = str(topic or "").strip()
        if not topic_text:
            continue
        if topic_text in normalized or normalized in topic_text:
            return True
    return False


def filter_actionable_evidence_requests(
    missing: Iterable[str],
    blocked: Iterable[str],
) -> list[str]:
    """
    过滤仍可向买家请求的举证项，排除已拒与后台操作类缺口。

    参数:
        missing: 原始缺证列表。
        blocked: 对话已拒举证主题。

    返回:
        可执行补证请求列表。
    """
    result: list[str] = []
    for raw in missing or []:
        text = str(raw or "").strip()
        if not text or is_operational_evidence_gap(text):
            continue
        if _is_blocked_item(text, blocked):
            continue
        if text not in result:
            result.append(text)
    return result


def build_evidence_risk_notes(facts: FactOutput) -> list[str]:
    """
    非阻断缺证与疑点摘要，供策略 risk_factors 旁路提示。

    参数:
        facts: Agent1 事实输出。

    返回:
        风险说明条目列表。
    """
    notes: list[str] = []
    missing = buyer_facing_missing_evidence(facts)
    if missing:
        notes.append(f"[证据提示] 仍缺：{'、'.join(missing[:4])}")

    doubt_corpus_parts: list[str] = []
    if facts.uncertainty_note:
        doubt_corpus_parts.append(str(facts.uncertainty_note))
    for item in facts.red_flags or []:
        doubt_corpus_parts.append(str(item))
    doubt_blob = " ".join(doubt_corpus_parts)
    if contains_any(doubt_blob, signal_group("evidence_doubt_markers")):
        snippet = doubt_blob.strip()[:80]
        if snippet:
            notes.append(f"[证据提示] 疑点待关注：{snippet}")

    operational = [
        str(item).strip()
        for item in (facts.missing_evidence or [])
        if is_operational_evidence_gap(str(item or ""))
    ]
    if operational:
        notes.append(f"[操作提示] {'、'.join(operational[:2])}")

    return notes


def compose_evidence_stage_next_step(
    *,
    facts: FactOutput,
    chat_turns: Iterable[ChatTurn],
    red_flags: Iterable[str] | None = None,
) -> str:
    """
    举证优先阶段的 next_step：排除已拒与不可执行项，无剩余可索要项时转固定证据与方案评估。

    参数:
        facts: 事实输出。
        chat_turns: 近期对话。
        red_flags: 疑点列表，可选。

    返回:
        面向商家的下一步动作说明。
    """
    flags = [str(item).strip() for item in (red_flags or facts.red_flags or []) if str(item).strip()]
    blocked = derive_blocked_evidence_requests(chat_turns)
    missing = filter_actionable_evidence_requests(
        buyer_facing_missing_evidence(facts),
        blocked,
    )
    parts: list[str] = []
    if flags:
        parts.append(f"先围绕“{flags[0]}”核验关键事实")
    else:
        parts.append("先把当前关键事实核验清楚")
    if missing:
        parts.append(f"请买家补充{'、'.join(missing[:3])}")
    elif buyer_facing_missing_evidence(facts):
        parts.append(
            "缺证项买家已无法补全，在现有材料基础上固定影响说明并评估规则内可执行方案"
        )
    else:
        parts.append("请买家补充可核实责任归属的材料")
    parts.append("商家同步固定发货、聊天和已有举证记录，事实闭环后再判断是否退款、补偿或抗辩")
    return "，".join(parts)
