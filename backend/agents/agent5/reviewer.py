"""
Agent 5：复盘分析师。

职责：在纠纷关闭后，从完整轨迹中提炼结构化经验卡片。
"""

from __future__ import annotations

from typing import Any

from schemas import ReviewInput, ReviewOutput


AGENT5_LOG_PREFIX = "[Agent5]"
_FACT_CANDIDATE_KEYS = [
    "key_facts",
    "fact_summary",
    "facts_summary",
    "evidence_summary",
    "buyer_claim",
]
_ACTION_CANDIDATE_KEYS = [
    "merchant_action_taken",
    "merchant_action",
    "action_taken",
    "final_action",
]


# ---------- 轨迹扫描：递归查找指定键的首个非空文本 ----------
def _find_first_text_by_keys(payload: Any, keys: set[str]) -> str:
    """
    在 dict/list 嵌套结构中查找首个命中的文本字段。

    参数:
        payload: 任意嵌套结构。
        keys: 目标字段名集合。

    返回:
        命中文本；未命中返回空字符串。
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and value.strip():
                return value.strip()
            nested = _find_first_text_by_keys(payload=value, keys=keys)
            if nested:
                return nested
        return ""

    if isinstance(payload, list):
        for item in payload:
            nested = _find_first_text_by_keys(payload=item, keys=keys)
            if nested:
                return nested
        return ""

    return ""


# ---------- 轨迹扫描：递归识别 AI 策略类型 ----------
def _detect_strategy(payload: Any) -> str:
    """
    从轨迹中识别 AI 策略（defend/negotiate/compensate）。

    参数:
        payload: 任意嵌套轨迹结构。

    返回:
        策略标识；未识别返回 unknown。
    """
    valid_strategies = {"defend", "negotiate", "compensate"}
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key).strip().lower()
            if key_text in {"strategy", "recommended_strategy", "ai_strategy"}:
                candidate = str(value or "").strip().lower()
                if candidate in valid_strategies:
                    return candidate
            nested = _detect_strategy(payload=value)
            if nested != "unknown":
                return nested
        return "unknown"

    if isinstance(payload, list):
        for item in payload:
            nested = _detect_strategy(payload=item)
            if nested != "unknown":
                return nested
        return "unknown"

    return "unknown"


# ---------- 字段标准化：统一结果标签与案例类型 ----------
def _normalize_outcome(final_outcome: str) -> str:
    """
    标准化纠纷结果枚举。

    参数:
        final_outcome: 输入结果文本。

    返回:
        胜/败/和解/升级 之一，无法识别时返回原文或“未知”。
    """
    normalized = str(final_outcome or "").strip().lower()
    if not normalized:
        return "未知"
    if "胜" in normalized or normalized in {"win", "won", "support_merchant"}:
        return "胜"
    if "败" in normalized or normalized in {"lose", "lost", "support_buyer"}:
        return "败"
    if "和解" in normalized or normalized in {"settle", "settlement"}:
        return "和解"
    if "升级" in normalized or normalized in {"escalate", "escalated"}:
        return "升级"
    return str(final_outcome).strip() or "未知"


def _build_case_type(strategy: str) -> str:
    """
    根据策略构造案例类型标签。

    参数:
        strategy: 识别到的策略标识。

    返回:
        案例类型标签。
    """
    mapping = {
        "defend": "质量争议_抗辩",
        "negotiate": "质量争议_协商",
        "compensate": "质量争议_补偿",
    }
    return mapping.get(strategy, "通用纠纷")


# ---------- 经验归纳：生成核心教训文本 ----------
def _build_lesson_text(outcome: str, ai_strategy_adopted: bool) -> str:
    """
    根据结果和采纳情况生成经验教训。

    参数:
        outcome: 标准化后的结果。
        ai_strategy_adopted: 是否采纳 AI 建议。

    返回:
        中文经验文本。
    """
    if outcome == "胜":
        if ai_strategy_adopted:
            return "本案结果为胜，说明当前 AI 建议在该场景具备可复用价值，可沉淀为优先策略模板。"
        return "本案结果为胜且商家未采纳 AI 建议，出现策略分歧且商家做法优于 AI，需将该分歧纳入后续策略优化。"

    if outcome == "败":
        if ai_strategy_adopted:
            return "本案结果为败且已采纳 AI 建议，说明该场景存在策略盲区，需要补充反例并收紧风险阈值。"
        return "本案结果为败且商家未采纳 AI 建议，存在策略分歧，后续应补齐证据并提前止损。"

    if outcome == "和解":
        if ai_strategy_adopted:
            return "本案以和解收尾且采纳 AI 建议，适合复用温和协商路径以降低升级概率。"
        return "本案以和解收尾但存在策略分歧，建议保留商家一线谈判技巧并优化协商话术。"

    if ai_strategy_adopted:
        return "本案进入升级流程且采纳 AI 建议，后续需强化升级前的证据清单和时点控制。"
    return "本案进入升级流程且存在策略分歧，建议沉淀升级触发条件并补充人工接管指引。"


# ---------- 经验标签：构建多维度可检索 tags ----------
def _build_tags(case_type: str, outcome: str, strategy: str, ai_strategy_adopted: bool) -> list[str]:
    """
    生成经验卡片检索标签。

    参数:
        case_type: 案例类型。
        outcome: 标准化结果。
        strategy: 策略标识。
        ai_strategy_adopted: 是否采纳 AI 建议。

    返回:
        去重后的标签列表。
    """
    tags = [
        f"case_type:{case_type}",
        f"outcome:{outcome}",
        f"strategy:{strategy}",
        "ai_adopted" if ai_strategy_adopted else "ai_not_adopted",
    ]
    if not ai_strategy_adopted:
        tags.append("strategy_gap")
    if outcome == "胜" and not ai_strategy_adopted:
        tags.append("merchant_override_success")

    deduplicated: list[str] = []
    for tag in tags:
        if tag not in deduplicated:
            deduplicated.append(tag)
    return deduplicated


# ---------- 主入口：从 ReviewInput 生成结构化复盘卡片 ----------
def review(input: ReviewInput) -> ReviewOutput:
    """
    复盘分析主函数（纯函数）。

    参数:
        input: ReviewInput。

    返回:
        ReviewOutput。
    """
    if not isinstance(input, ReviewInput):
        raise TypeError(f"{AGENT5_LOG_PREFIX} input 必须是 ReviewInput 类型")

    full_timeline = input.full_timeline if isinstance(input.full_timeline, dict) else {}
    strategy = _detect_strategy(payload=full_timeline)
    outcome = _normalize_outcome(final_outcome=input.final_outcome)
    case_type = _build_case_type(strategy=strategy)

    fact_text = _find_first_text_by_keys(payload=full_timeline, keys=set(_FACT_CANDIDATE_KEYS))
    key_facts = f"{outcome}；{fact_text}" if fact_text else f"{outcome}；关键事实待补充"

    action_text = _find_first_text_by_keys(payload=full_timeline, keys=set(_ACTION_CANDIDATE_KEYS))
    adopted_prefix = "采纳AI建议" if input.ai_strategy_adopted else "未采纳AI建议"
    merchant_action_taken = f"{adopted_prefix}，{action_text}" if action_text else adopted_prefix

    lesson_text = _build_lesson_text(outcome=outcome, ai_strategy_adopted=input.ai_strategy_adopted)
    tags = _build_tags(
        case_type=case_type,
        outcome=outcome,
        strategy=strategy,
        ai_strategy_adopted=input.ai_strategy_adopted,
    )

    return ReviewOutput(
        case_type=case_type,
        key_facts=key_facts,
        merchant_action_taken=merchant_action_taken,
        outcome=outcome,
        lesson_text=lesson_text,
        tags=tags,
    )


if __name__ == "__main__":
    # ---------- 内嵌测试 1：胜诉 + 采纳 AI 建议 ----------
    case1 = review(
        ReviewInput(
            dispute_id="D001",
            full_timeline={
                "strategy_output": {"strategy": "defend"},
                "fact_summary": "买家无法提供拆封视频，吊牌完整。",
                "merchant_action": "提交质检与出库记录进行抗辩",
            },
            final_outcome="胜",
            ai_strategy_adopted=True,
        )
    )
    assert case1.outcome == "胜", f"期望胜，实际={case1.outcome}"
    assert "strategy:defend" in case1.tags, f"期望含 strategy:defend，实际={case1.tags}"

    # ---------- 内嵌测试 2：败诉 + 未采纳 AI 建议（需体现分歧） ----------
    case2 = review(
        ReviewInput(
            dispute_id="D002",
            full_timeline={
                "strategy_output": {"strategy": "negotiate"},
                "key_facts": "买家多次强调色差且拒绝补图。",
                "merchant_action_taken": "坚持不退导致平台介入",
            },
            final_outcome="败",
            ai_strategy_adopted=False,
        )
    )
    assert "分歧" in case2.lesson_text, f"期望 lesson 含分歧，实际={case2.lesson_text}"

    # ---------- 内嵌测试 3：和解 + 采纳 AI 建议 ----------
    case3 = review(
        ReviewInput(
            dispute_id="D003",
            full_timeline={
                "ai_strategy": "compensate",
                "facts_summary": "物流延迟3天，买家情绪激动。",
                "final_action": "提供部分退款并补偿优惠券",
            },
            final_outcome="和解",
            ai_strategy_adopted=True,
        )
    )
    assert case3.case_type != "", "期望 case_type 非空"
    assert len(case3.tags) >= 3, f"期望 tags 具备多维度，实际={case3.tags}"

    # ---------- 内嵌测试 4：边界场景（空轨迹） ----------
    case4 = review(
        ReviewInput(
            dispute_id="D004",
            full_timeline={},
            final_outcome="升级",
            ai_strategy_adopted=False,
        )
    )
    assert case4.key_facts.startswith("升级；"), f"期望 key_facts 含升级前缀，实际={case4.key_facts}"
    assert case4.case_type == "通用纠纷", f"期望通用纠纷，实际={case4.case_type}"

    print(f"{AGENT5_LOG_PREFIX} 内嵌测试全部通过")
