"""
Agent 2：策略参谋员。

职责：在既定输入（事实、规则命中、画像、判例）上做加权决策；本模块不读库、不调 HTTP。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List
import json
import logging
import os

from schemas import (
    CustomerValueInput,
    MaliciousDetectionInput,
    DISPOSITION_COMPENSATE,
    DISPOSITION_DEFEND,
    DISPOSITION_NEGOTIATE,
    StrategyInput,
    StrategyOutput,
)
from backend.tools.agent2_tools import detect_malicious_behavior, evaluate_customer_value
from backend.tools.llm_client import chat_completion


AGENT2_LOG_PREFIX = "[Agent2]"
logger = logging.getLogger(__name__)


# ---------- 多源加权：规则票权、事实、画像、判例、订单金额 ----------
def _build_customer_value_infer_messages(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    构建客户价值字段推断提示词（规则化判定 + 跨品类 few-shot）。

    设计要点：
    1. 明确四个字段的判定优先级与边界条件，减少模型随意发挥。
    2. 使用跨品类示例提升泛化能力，避免退化为单类目经验匹配。
    3. 强制结构化 JSON 输出，便于后续严格校验。
    """
    system_prompt = (
        "你是电商售后策略分析器。任务是从输入事实中推断 4 个结构化字段，"
        "用于后续客户价值评估。禁止假设固定品类；必须遵循以下判定规则。\n"
        "\n"
        "【字段1：defect_severity】\n"
        "- severe：核心功能不可用/影响安全/无法正常履约，或损坏程度显著。\n"
        "- moderate：存在明确问题并影响体验，但不构成完全不可用。\n"
        "- minor：轻微瑕疵或主观体验差异，基本功能可用。\n"
        "优先看事实证据（facts）中的问题描述、证据质量、使用影响，不要看品类名。\n"
        "\n"
        "【字段2：goods_recoverability】\n"
        "- unrecoverable：退回后基本无法二次销售，或修复成本显著不经济。\n"
        "- repairable：可修复后再处理，但存在明确损失。\n"
        "- resalable：可直接二次销售或轻微处理即可再次流转。\n"
        "优先看损坏可逆性与再销售可能性，不依赖类目经验。\n"
        "\n"
        "【字段3：buyer_cooperation】\n"
        "- good：愿意配合补充证据、反馈及时、沟通一致。\n"
        "- neutral：部分配合或信息不完整，但可继续推进。\n"
        "- poor：明显拒绝配合、前后矛盾、反复施压且缺乏有效信息。\n"
        "优先看聊天行为和证据配合度。\n"
        "\n"
        "【字段4：demand_reasonableness】\n"
        "- reasonable：诉求与事实证据、平台常规规则基本一致。\n"
        "- borderline：诉求有部分合理性，但金额或方式偏激进。\n"
        "- unreasonable：诉求明显超出事实支撑或违背规则边界。\n"
        "优先看诉求-证据一致性，再看金额与处理方式是否成比例。\n"
        "\n"
        "输出要求：\n"
        "1) 只输出 JSON 对象，不输出解释文本。\n"
        "2) JSON 严格包含且仅包含 4 个键：\n"
        '{"defect_severity":"minor|moderate|severe","goods_recoverability":"resalable|repairable|unrecoverable",'
        '"buyer_cooperation":"good|neutral|poor","demand_reasonableness":"reasonable|borderline|unreasonable"}\n'
        "3) 不允许返回 null、空字符串或中文枚举。"
    )

    # 跨品类 few-shot：服饰、3C、家居三个场景，增强泛化。
    few_shot_user_1 = (
        "示例输入1："
        '{"facts":{"defect_type":"污渍","evidence_quality":"high","missing_evidence":[],"red_flags":[]},'
        '"buyer_profile":{"purchase_count":6},"order_amount":159.0,'
        '"chat_behavior":"买家上传清晰图片并同意补充细节，诉求为部分退款"}'
    )
    few_shot_assistant_1 = (
        '{"defect_severity":"moderate","goods_recoverability":"repairable",'
        '"buyer_cooperation":"good","demand_reasonableness":"reasonable"}'
    )

    few_shot_user_2 = (
        "示例输入2："
        '{"facts":{"defect_type":"功能故障","evidence_quality":"high","missing_evidence":[],"red_flags":[]},'
        '"buyer_profile":{"purchase_count":2},"order_amount":899.0,'
        '"chat_behavior":"买家提供故障视频，诉求全额退款"}'
    )
    few_shot_assistant_2 = (
        '{"defect_severity":"severe","goods_recoverability":"unrecoverable",'
        '"buyer_cooperation":"good","demand_reasonableness":"reasonable"}'
    )

    few_shot_user_3 = (
        "示例输入3："
        '{"facts":{"defect_type":"无瑕疵","evidence_quality":"low","missing_evidence":["清晰照片"],"red_flags":["前后说法不一致"]},'
        '"buyer_profile":{"purchase_count":1},"order_amount":299.0,'
        '"chat_behavior":"拒绝补证，坚持仅退款并威胁差评"}'
    )
    few_shot_assistant_3 = (
        '{"defect_severity":"minor","goods_recoverability":"resalable",'
        '"buyer_cooperation":"poor","demand_reasonableness":"unreasonable"}'
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": few_shot_user_1},
        {"role": "assistant", "content": few_shot_assistant_1},
        {"role": "user", "content": few_shot_user_2},
        {"role": "assistant", "content": few_shot_assistant_2},
        {"role": "user", "content": few_shot_user_3},
        {"role": "assistant", "content": few_shot_assistant_3},
        {"role": "user", "content": f"请按相同规则输出当前输入的 JSON：{json.dumps(payload, ensure_ascii=False)}"},
    ]


def _strip_markdown_json(raw_text: str) -> str:
    """
    去除 LLM 可能返回的 markdown 代码块包裹，便于 JSON 解析。
    """
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return text


def _require_customer_value_field(value: str, valid_values: set[str], field_name: str) -> str:
    """
    校验 LLM 推断字段值；不合法时直接抛错，避免多路径兜底。
    """
    normalized = (value or "").strip().lower()
    if normalized not in valid_values:
        raise ValueError(f"字段 {field_name} 返回非法值：{value}")
    return normalized


def _llm_infer_customer_value_fields(input_data: StrategyInput) -> Dict[str, str]:
    """
    通过 LLM 推断客户价值工具所需字段，保证对全品类场景的普适性。
    """
    payload: Dict[str, Any] = {
        "facts": input_data.facts.model_dump(),
        "buyer_profile": input_data.buyer_profile.model_dump(),
        "order_amount": input_data.order_amount,
    }
    logger.info("%s 开始调用 LLM 推断客户价值字段", AGENT2_LOG_PREFIX)
    llm_text = chat_completion(
        messages=_build_customer_value_infer_messages(payload),
        model_env_key="AGENT2_LLM_MODEL",
        temperature=0.0,
    )
    if not llm_text:
        raise RuntimeError("客户价值字段推断失败：LLM 无返回内容")

    try:
        parsed = json.loads(_strip_markdown_json(llm_text))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"客户价值字段推断失败：LLM 输出解析异常，原因：{exc}") from exc

    return {
        "defect_severity": _require_customer_value_field(
            str(parsed.get("defect_severity", "")),
            {"minor", "moderate", "severe"},
            "defect_severity",
        ),
        "goods_recoverability": _require_customer_value_field(
            str(parsed.get("goods_recoverability", "")),
            {"resalable", "repairable", "unrecoverable"},
            "goods_recoverability",
        ),
        "buyer_cooperation": _require_customer_value_field(
            str(parsed.get("buyer_cooperation", "")),
            {"good", "neutral", "poor"},
            "buyer_cooperation",
        ),
        "demand_reasonableness": _require_customer_value_field(
            str(parsed.get("demand_reasonableness", "")),
            {"reasonable", "borderline", "unreasonable"},
            "demand_reasonableness",
        ),
    }


def _analyze_rule_stance(input_data: StrategyInput) -> tuple[List[str], str, int]:
    """
    解析规则命中站位，输出规则引用、站位方向与命中数。
    """
    merchant_support = 0
    buyer_support = 0
    policy_refs: List[str] = []
    for rule in input_data.matched_rules:
        combined_text = f"{rule.rule_summary} {rule.condition_result}".lower()
        policy_refs.append(rule.rule_id)
        if "建议策略:defend" in combined_text or "抗辩" in combined_text or "支持商家" in combined_text:
            merchant_support += 1
        elif "建议策略:compensate" in combined_text or "退款" in combined_text or "赔付" in combined_text or "支持买家" in combined_text:
            buyer_support += 1
    if merchant_support > buyer_support:
        return policy_refs, "merchant", len(input_data.matched_rules)
    if buyer_support > merchant_support:
        return policy_refs, "buyer", len(input_data.matched_rules)
    return policy_refs, "neutral", len(input_data.matched_rules)


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

    返回 (customer_value, new_risk_factors) 元组，不修改外部 risk_factors，
    保证并发安全。
    """
    logger.info("%s 开始生成客户价值评估输入字段", AGENT2_LOG_PREFIX)
    inferred_fields = _llm_infer_customer_value_fields(input_data)
    customer_value_input = CustomerValueInput(
        buyer_profile=input_data.buyer_profile,
        order_amount=input_data.order_amount,
        defect_severity=inferred_fields["defect_severity"],
        goods_recoverability=inferred_fields["goods_recoverability"],
        buyer_cooperation=inferred_fields["buyer_cooperation"],
        demand_reasonableness=inferred_fields["demand_reasonableness"],
    )
    logger.info("%s 客户价值输入字段生成完成：%s", AGENT2_LOG_PREFIX, inferred_fields)
    customer_value = evaluate_customer_value(customer_value_input)
    layer_risks: List[str] = []
    if customer_value.channel == "long_term":
        layer_risks.append("[客户价值层] 触发长期优待通道，优先协商维护关系")
    elif customer_value.channel == "order":
        layer_risks.append("[客户价值层] 触发本单重点处理，建议快速协商收敛纠纷")
    return customer_value, layer_risks


def _determine_disposition(
    input_data: StrategyInput,
    *,
    malicious_result,
    customer_value,
    rule_stance: str,
) -> str:
    """
    单链路处置决策：默认协商，强信号覆盖。
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


def _build_fallback_reasoning(
    *,
    disposition: str,
    input_data: StrategyInput,
    risk_factors: List[str],
    estimated_win_rate: float | None,
) -> str:
    """
    当 LLM 不可用时，输出结构化三段 reasoning。
    """
    intent = _infer_customer_intent(input_data)
    risk_text = "；".join(risk_factors[:3]) if risk_factors else "当前未识别到高风险项"
    if disposition == DISPOSITION_DEFEND:
        action = "先固定证据链并要求买家补证，再按规则提交抗辩材料"
    elif disposition == DISPOSITION_COMPENSATE:
        action = "主动体面善后：及时补救并同步方案，把体验与口碑损失压到最低"
    else:
        action = "先给可接受协商方案，保留后续平台申诉与补证空间"
    win_rate_note = ""
    if estimated_win_rate is not None:
        win_rate_note = f"；当前抗辩胜率={estimated_win_rate:.3f}"
    return (
        f"客户意图：{intent}。\n"
        f"风险点：{risk_text}。\n"
        f"建议动作：{action}{win_rate_note}。"
    )


def _llm_generate_reasoning(
    *,
    disposition: str,
    input_data: StrategyInput,
    risk_factors: List[str],
    estimated_win_rate: float | None,
    fast_path: bool = False,
    reasoning_delta_callback: Callable[[str], None] | None = None,
) -> str | None:
    """
    用 LLM 生成更自然的策略说明，强调合规前提下商户利益最大化。

    fast_path=True 时优先小模型，若格式不符合约定则自动回退主模型补调一次。
    """
    prompt_payload = {
        "disposition": disposition,
        "facts": input_data.facts.model_dump(),
        "buyer_profile": input_data.buyer_profile.model_dump(),
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
        model_env_key=model_env_key,
        fallback_model_env_key=fallback_key,
        temperature=0.3,
        stream_delta_callback=reasoning_delta_callback,
    )
    if not llm_text:
        return None
    normalized = llm_text.strip()
    if "客户意图：" in normalized and "风险点：" in normalized and "建议动作：" in normalized:
        return normalized

    # 小模型路径不满足格式时，回退主模型补调一次，优先保证结果质量与可读性。
    if fast_path and os.getenv("AGENT2_LLM_MODEL", "").strip():
        retry_text = chat_completion(
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
            stream_delta_callback=reasoning_delta_callback,
        )
        if not retry_text:
            return None
        retry_normalized = retry_text.strip()
        if "客户意图：" in retry_normalized and "风险点：" in retry_normalized and "建议动作：" in retry_normalized:
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
        StrategyOutput，含 disposition、estimated_win_rate、reasoning、risk_factors 等。
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

    disposition = _determine_disposition(
        input_data,
        malicious_result=malicious_result,
        customer_value=customer_value,
        rule_stance=rule_stance,
    )
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

    return StrategyOutput(
        disposition=disposition,
        estimated_win_rate=estimated_win_rate,
        policy_ref=policy_ref,
        reasoning=reasoning,
        risk_factors=dedup_risks,
        confidence=confidence,
        customer_value=customer_value,
        malicious_detection=malicious_result,
    )
