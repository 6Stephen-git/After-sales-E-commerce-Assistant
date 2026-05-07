"""
Agent 2：策略参谋员。

职责：在既定输入（事实、规则命中、画像、判例）上做加权决策；本模块不读库、不调 HTTP。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple
import json

from schemas import (
    STRATEGY_COMPENSATE,
    STRATEGY_DEFEND,
    STRATEGY_NEGOTIATE,
    StrategyInput,
    StrategyOutput,
)
from backend.tools.llm_client import chat_completion


AGENT2_LOG_PREFIX = "[Agent2]"


# ---------- 多源加权：规则票权、事实、画像、判例、订单金额 ----------
def _extract_rule_strategy_votes(matched_rules: List) -> Tuple[Dict[str, float], List[str]]:
    """
    解析已匹配规则列表，得到各策略英文键的票权分数及规则 id 列表。

    从 rule_summary + condition_result 拼接文本中识别「建议策略:xxx」或中文关键词，
    命中则给对应策略加较高分；无法归类时给 negotiate 少量分，体现规则优先下的保守倾向。

    参数:
        matched_rules: Agent2 工具 match_rules 的输出，元素为 MatchedRule。

    返回:
        (votes, policy_refs)：votes 为 strategy -> 浮点得分；policy_refs 为 rule_id 顺序列表。
    """
    votes = defaultdict(float)
    policy_refs: List[str] = []

    for rule in matched_rules:
        combined_text = f"{rule.rule_summary} {rule.condition_result}".lower()
        policy_refs.append(rule.rule_id)

        if "建议策略:defend" in combined_text or "抗辩" in combined_text:
            votes[STRATEGY_DEFEND] += 0.45
        elif "建议策略:negotiate" in combined_text or "协商" in combined_text:
            votes[STRATEGY_NEGOTIATE] += 0.45
        elif "建议策略:compensate" in combined_text or "退款" in combined_text or "赔付" in combined_text:
            votes[STRATEGY_COMPENSATE] += 0.45
        else:
            # 规则存在但无法归因时，给保守协商少量票权。
            votes[STRATEGY_NEGOTIATE] += 0.1

    return votes, policy_refs


def _score_by_facts(strategy_scores: Dict[str, float], input_data: StrategyInput, risk_factors: List[str]) -> None:
    """
    根据 Agent1 的 FactOutput 对三策略得分做增量调整，并写入可读风险文案。

    考虑证据质量、瑕疵类型、吊牌可见、red_flags、missing_evidence 等；
    本函数原地修改 strategy_scores 与 risk_factors，无返回值。

    参数:
        strategy_scores: 三策略累计分数字典，键为 defend/negotiate/compensate。
        input_data: 含 facts 的完整策略输入。
        risk_factors: 风险说明字符串列表，可能被 append。
    """
    facts = input_data.facts
    evidence_quality = facts.evidence_quality.lower().strip()

    if evidence_quality == "high" and facts.defect_type not in (None, "无瑕疵"):
        strategy_scores[STRATEGY_COMPENSATE] += 0.25
    elif evidence_quality == "low":
        strategy_scores[STRATEGY_DEFEND] += 0.25
        risk_factors.append("买家证据质量低，可能触发补充举证")
    else:
        strategy_scores[STRATEGY_NEGOTIATE] += 0.15

    if facts.defect_type == "色差":
        strategy_scores[STRATEGY_NEGOTIATE] += 0.2
    if facts.defect_type == "无瑕疵" and facts.has_tag_visible:
        strategy_scores[STRATEGY_DEFEND] += 0.2

    if facts.red_flags:
        strategy_scores[STRATEGY_DEFEND] += 0.15
        risk_factors.append("存在疑点信号，需准备更完整证据链")
    if facts.missing_evidence:
        strategy_scores[STRATEGY_NEGOTIATE] += 0.1
        risk_factors.append(f"缺失关键证据：{','.join(facts.missing_evidence)}")


def _score_by_buyer_profile(strategy_scores: Dict[str, float], input_data: StrategyInput, risk_factors: List[str]) -> None:
    """
    根据 BuyerProfile 对三策略得分做增量调整。

    高纠纷率、恶意标记、高退货率偏向 defend；高信誉低纠纷略偏向 negotiate；
    老客低纠纷略偏向 negotiate。原地修改 strategy_scores 与 risk_factors。

    参数:
        strategy_scores: 三策略累计分。
        input_data: 含 buyer_profile。
        risk_factors: 风险文案列表。
    """
    profile = input_data.buyer_profile

    if profile.dispute_rate >= 0.4 or profile.malicious_flags >= 2:
        strategy_scores[STRATEGY_DEFEND] += 0.25
        risk_factors.append("买家历史纠纷率较高，存在重复争议风险")
    elif profile.credit_level == "high" and profile.dispute_rate <= 0.1:
        strategy_scores[STRATEGY_NEGOTIATE] += 0.1

    if profile.return_rate >= 0.4:
        strategy_scores[STRATEGY_DEFEND] += 0.1
    if profile.purchase_count >= 10 and profile.dispute_count <= 1:
        strategy_scores[STRATEGY_NEGOTIATE] += 0.1


def _score_by_similar_cases(strategy_scores: Dict[str, float], input_data: StrategyInput) -> None:
    """
    根据相似判例列表 outcome / merchant_action 关键词向三策略加分。

    每条判例贡献 similarity 映射后的权重；原地修改 strategy_scores。

    参数:
        strategy_scores: 三策略累计分。
        input_data: 含 similar_cases。
    """
    for case in input_data.similar_cases:
        case_weight = max(0.0, min(1.0, case.similarity)) * 0.2
        outcome_text = case.outcome.lower()
        action_text = case.merchant_action.lower()

        if "支持商家" in outcome_text or "抗辩" in action_text:
            strategy_scores[STRATEGY_DEFEND] += case_weight
        elif "和解" in outcome_text or "协商" in action_text:
            strategy_scores[STRATEGY_NEGOTIATE] += case_weight
        elif "支持买家" in outcome_text or "退款" in action_text:
            strategy_scores[STRATEGY_COMPENSATE] += case_weight


# ---------- 输出侧量化：胜率裁剪、主策略胜率估计、策略置信度 ----------
def _normalize_win_rate(raw_rate: float) -> float:
    """
    将原始胜率裁剪到 [0.05, 0.95] 并保留三位小数，避免对外输出极端 0/1。

    参数:
        raw_rate: 未裁剪的胜率估计。

    返回:
        裁剪后的浮点数。
    """
    return max(0.05, min(0.95, round(raw_rate, 3)))


def _estimate_win_rate(strategy: str, strategy_scores: Dict[str, float], risk_factors: List[str]) -> float:
    """
    为最终选定的主策略估计 0～1 胜率。

    思路：主策略得分占总分的比例越高基础胜率越高；risk_factors 条数带来惩罚；
    compensate 略上调、defend 略下调以反映平台倾向差异。

    参数:
        strategy: 已选主策略，defend / negotiate / compensate。
        strategy_scores: 三策略最终得分。
        risk_factors: 风险项列表，用于惩罚项计数。

    返回:
        经 _normalize_win_rate 处理后的胜率。
    """
    total_score = max(0.001, sum(strategy_scores.values()))
    chosen_score = strategy_scores[strategy]
    dominance = chosen_score / total_score

    base_rate = 0.42 + dominance * 0.42
    risk_penalty = min(0.2, len(risk_factors) * 0.03)

    if strategy == STRATEGY_COMPENSATE:
        base_rate += 0.05
    elif strategy == STRATEGY_DEFEND:
        base_rate -= 0.03

    return _normalize_win_rate(base_rate - risk_penalty)


def _estimate_confidence(strategy_scores: Dict[str, float]) -> float:
    """
    根据三策略得分的「第一名与第二名分差」估计策略置信度。

    分差越大置信越高；结果限制在 [0.1, 0.95]。

    参数:
        strategy_scores: 三策略最终得分。

    返回:
        0～1 之间的置信度浮点数。
    """
    ranked = sorted(strategy_scores.values(), reverse=True)
    top_score = ranked[0]
    second_score = ranked[1] if len(ranked) > 1 else 0.0
    gap = max(0.0, top_score - second_score)
    confidence = 0.5 + min(0.45, gap * 0.9)
    return max(0.1, min(0.95, round(confidence, 3)))


# ---------- LLM推理：基于既定主策略生成可执行 reasoning（不改策略结果） ----------
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


def _build_fallback_reasoning(
    *,
    strategy: str,
    input_data: StrategyInput,
    strategy_scores: Dict[str, float],
    risk_factors: List[str],
) -> str:
    """
    当 LLM 不可用时，输出结构化三段 reasoning。
    """
    intent = _infer_customer_intent(input_data)
    risk_text = "；".join(risk_factors[:3]) if risk_factors else "当前未识别到高风险项"
    if strategy == STRATEGY_DEFEND:
        action = "先固定证据链并要求买家补证，再按规则提交抗辩材料"
    elif strategy == STRATEGY_COMPENSATE:
        action = "主动体面善后：及时补救并同步方案，把体验与口碑损失压到最低"
    else:
        action = "先给可接受协商方案，保留后续平台申诉与补证空间"

    return (
        f"客户意图：{intent}。\n"
        f"风险点：{risk_text}。\n"
        f"建议动作：{action}；当前得分 defend={strategy_scores[STRATEGY_DEFEND]:.2f}、"
        f"negotiate={strategy_scores[STRATEGY_NEGOTIATE]:.2f}、compensate={strategy_scores[STRATEGY_COMPENSATE]:.2f}。"
    )


def _llm_generate_reasoning(
    *,
    strategy: str,
    input_data: StrategyInput,
    strategy_scores: Dict[str, float],
    risk_factors: List[str],
) -> str | None:
    """
    用 LLM 生成更自然的策略说明，强调合规前提下商户利益最大化。
    """
    prompt_payload = {
        "strategy": strategy,
        "scores": {
            "defend": round(strategy_scores[STRATEGY_DEFEND], 3),
            "negotiate": round(strategy_scores[STRATEGY_NEGOTIATE], 3),
            "compensate": round(strategy_scores[STRATEGY_COMPENSATE], 3),
        },
        "facts": input_data.facts.model_dump(),
        "buyer_profile": input_data.buyer_profile.model_dump(),
        "matched_rules": [item.model_dump() for item in input_data.matched_rules],
        "similar_cases": [item.model_dump() for item in input_data.similar_cases],
        "risk_factors": risk_factors,
        "order_amount": input_data.order_amount,
    }
    llm_text = chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是资深电商客服策略参谋。必须在规则和道德边界内，优先保护商户长期利益。"
                    "输出三段中文，每段分别以“客户意图：”“风险点：”“建议动作：”开头，"
                    "避免空话，不要使用AI口吻。"
                ),
            },
            {"role": "user", "content": f"请基于以下输入生成策略说明：\n{json.dumps(prompt_payload, ensure_ascii=False)}"},
        ],
        model_env_key="AGENT2_LLM_MODEL",
        temperature=0.3,
    )
    if not llm_text:
        return None
    normalized = llm_text.strip()
    if "客户意图：" not in normalized or "风险点：" not in normalized or "建议动作：" not in normalized:
        return None
    return normalized


# ---------- 主入口：汇总得分并生成 StrategyOutput ----------
def recommend(input_data: StrategyInput) -> StrategyOutput:
    """
    综合规则票权、事实、画像、判例与订单金额，输出主策略与说明。

    纯函数：不读环境变量、不访问网络与数据库；所有外部数据须由调用方
    通过 StrategyInput 注入。

    参数:
        input_data: 含 facts、buyer_profile、matched_rules、similar_cases、order_amount。

    返回:
        StrategyOutput，含 strategy、estimated_win_rate、reasoning、risk_factors 等。
    """
    strategy_scores = {
        STRATEGY_DEFEND: 0.0,
        STRATEGY_NEGOTIATE: 0.0,
        STRATEGY_COMPENSATE: 0.0,
    }
    risk_factors: List[str] = []

    rule_votes, policy_refs = _extract_rule_strategy_votes(input_data.matched_rules)
    for strategy, score in rule_votes.items():
        strategy_scores[strategy] += score

    _score_by_facts(strategy_scores, input_data, risk_factors)
    _score_by_buyer_profile(strategy_scores, input_data, risk_factors)
    _score_by_similar_cases(strategy_scores, input_data)

    if input_data.order_amount >= 500:
        strategy_scores[STRATEGY_NEGOTIATE] += 0.08
        risk_factors.append("订单金额较高，升级纠纷成本更高")

    strategy = max(strategy_scores, key=strategy_scores.get)
    estimated_win_rate = _estimate_win_rate(strategy, strategy_scores, risk_factors)
    confidence = _estimate_confidence(strategy_scores)

    reasoning = _llm_generate_reasoning(
        strategy=strategy,
        input_data=input_data,
        strategy_scores=strategy_scores,
        risk_factors=risk_factors,
    )
    if not reasoning:
        reasoning = _build_fallback_reasoning(
            strategy=strategy,
            input_data=input_data,
            strategy_scores=strategy_scores,
            risk_factors=risk_factors,
        )

    policy_ref = ",".join(policy_refs[:3]) if policy_refs else None
    dedup_risks = list(dict.fromkeys(risk_factors))

    return StrategyOutput(
        strategy=strategy,
        estimated_win_rate=estimated_win_rate,
        policy_ref=policy_ref,
        reasoning=reasoning,
        risk_factors=dedup_risks,
        confidence=confidence,
    )
