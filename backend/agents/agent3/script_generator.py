"""
Agent 3：话术生成员。

职责：把 Agent2 策略与 Agent1 事实填入模板生成三版话术；不直连数据库、不发起网络请求。
"""

from __future__ import annotations

from typing import Dict, Optional

from schemas import (
    SCRIPT_COMPENSATE,
    SCRIPT_DEFENSE,
    SCRIPT_NEGOTIATE,
    STRATEGY_COMPENSATE,
    STRATEGY_DEFEND,
    STRATEGY_NEGOTIATE,
    ScriptInput,
    ScriptOutput,
)

from backend.tools.agent3_tools import get_script_template


AGENT3_LOG_PREFIX = "[Agent3]"


# ---------- 展示用字符串：占位兜底、金额格式、面向买家的事实摘要 ----------
def _normalize_text(value: Optional[str], fallback: str = "待补充") -> str:
    """
    将可选字符串规范为非空展示文案。

    参数:
        value: 可能为 None 或仅空白的字符串。
        fallback: value 无效时使用的默认中文占位。

    返回:
        去掉首尾空白后的字符串；无效时返回 fallback。
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _format_money(amount: float) -> str:
    """
    将订单金额格式化为两位小数字符串，负数按 0 处理。

    参数:
        amount: 订单金额。

    返回:
        如 "99.00" 的字符串，供模板中 {{offer_amount}} 等占位符使用。
    """
    safe_amount = max(0.0, float(amount))
    return f"{safe_amount:.2f}"


def _build_fact_summary(input_data: ScriptInput) -> str:
    """
    从 ScriptInput.facts 拼出一句面向买家的事实摘要（不夸大、不编造字段外信息）。

    顺序覆盖：收货感知 → 瑕疵与位置 → 物流是否正常 → 是否存在待核实疑点；
    若全无有效信息则返回固定兜底句。

    参数:
        input_data: 含 facts 的脚本生成输入。

    返回:
        一句中文摘要，用于模板变量 fact_summary。
    """
    facts = input_data.facts
    parts = []

    if facts.goods_received is True:
        parts.append("买家反馈已收货")
    elif facts.goods_received is False:
        parts.append("买家反馈未收货")

    if facts.defect_type:
        location = _normalize_text(facts.defect_location, fallback="商品局部")
        parts.append(f"争议点集中在{location}的“{facts.defect_type}”")

    if facts.logistics_normal is True:
        parts.append("物流状态正常")
    elif facts.logistics_normal is False:
        parts.append("物流存在异常记录")

    if facts.red_flags:
        parts.append("目前还有待核实的细节")

    if not parts:
        return "目前证据还不完整，我们正在继续核对"
    return "，".join(parts)


# ---------- 模板渲染：占位符替换、推荐版本映射、usage_tip ----------
def _fill_template(template: str, variables: Dict[str, str]) -> str:
    """
    将模板中的 `{{键名}}` 占位符替换为 variables 中对应值。

    参数:
        template: 来自 get_script_template 的模板字符串。
        variables: 键为占位符名、值为已格式化的替换串。

    返回:
        去掉首尾空白后的成稿字符串。
    """
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result.strip()


def _strategy_to_recommended_version(strategy: str) -> str:
    """
    将 Agent2 输出的策略英文值映射为 ScriptOutput 中的 recommended_version 字段值。

    参数:
        strategy: defend / negotiate / compensate。

    返回:
        defense_version / negotiate_version / compensate_version 三者之一；
        未知策略时默认 negotiate_version。
    """
    normalized = (strategy or "").strip().lower()
    mapping = {
        STRATEGY_DEFEND: SCRIPT_DEFENSE,
        STRATEGY_NEGOTIATE: SCRIPT_NEGOTIATE,
        STRATEGY_COMPENSATE: SCRIPT_COMPENSATE,
    }
    return mapping.get(normalized, SCRIPT_NEGOTIATE)


def _build_usage_tip(input_data: ScriptInput, recommended_version: str) -> str:
    """
    根据主策略与 Agent2 reasoning 生成简短使用建议（中文）。

    参数:
        input_data: 含 strategy_output.reasoning。
        recommended_version: SCRIPT_DEFENSE / SCRIPT_NEGOTIATE / SCRIPT_COMPENSATE。

    返回:
        单行中文提示，说明优先发送哪一版及截取后的理由摘要。
    """
    strategy_output = input_data.strategy_output
    base_reason = _normalize_text(strategy_output.reasoning, fallback="综合事实和规则后建议先稳妥沟通")
    short_reason = base_reason[:60]

    if recommended_version == SCRIPT_DEFENSE:
        return f"建议先用抗辩版，重点是补齐证据链后再提交平台；理由：{short_reason}"
    if recommended_version == SCRIPT_COMPENSATE:
        return f"建议先用认赔版，优先止损并稳定买家情绪；理由：{short_reason}"
    return f"建议先用协商版，先把分歧控制在可谈区间；理由：{short_reason}"


# ---------- 主入口：拉三策略模板并组装 ScriptOutput ----------
def generate(input_data: ScriptInput) -> ScriptOutput:
    """
    拉取三策略模板、填入变量，生成抗辩/协商/认赔三版话术及推荐标识。

    纯函数：内部通过 get_script_template 读文件，不在本函数内直接 open 网络；
    模板读取副作用封装在工具层。

    参数:
        input_data: 含 strategy_output、facts、order_id、order_amount、可选 emotion_note。

    返回:
        ScriptOutput，三版话术字段均非空字符串。
    """
    fact_summary = _build_fact_summary(input_data=input_data)
    order_id = _normalize_text(input_data.order_id, fallback="未知订单")
    offer_amount = _format_money(input_data.order_amount)

    variables = {
        "order_id": order_id,
        "order_amount": _format_money(input_data.order_amount),
        "offer_amount": offer_amount,
        "fact_summary": fact_summary,
        "emotion_note": _normalize_text(input_data.emotion_note, fallback=""),
    }

    defense_template = get_script_template(STRATEGY_DEFEND)
    negotiate_template = get_script_template(STRATEGY_NEGOTIATE)
    compensate_template = get_script_template(STRATEGY_COMPENSATE)

    defense_version = _fill_template(defense_template, variables)
    negotiate_version = _fill_template(negotiate_template, variables)
    compensate_version = _fill_template(compensate_template, variables)

    recommended_version = _strategy_to_recommended_version(input_data.strategy_output.strategy)
    usage_tip = _build_usage_tip(input_data=input_data, recommended_version=recommended_version)

    return ScriptOutput(
        defense_version=defense_version,
        negotiate_version=negotiate_version,
        compensate_version=compensate_version,
        recommended_version=recommended_version,
        usage_tip=usage_tip,
    )
