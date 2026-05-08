"""
Agent 3：话术生成员。

职责：把 Agent2 策略与 Agent1 事实填入模板生成三版话术；不直连数据库、不发起网络请求。
"""

from __future__ import annotations

from typing import Dict, Optional
import json
import os

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
from backend.tools.llm_client import chat_completion


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
        return f"建议优先用善后版：主动把问题收尾得漂亮，稳住体验与口碑；理由：{short_reason}"
    return f"建议先用协商版，先把分歧控制在可谈区间；理由：{short_reason}"


# ---------- 风格感知：根据情绪备注与事实完整度决定长度与语气 ----------
def _derive_tone_profile(input_data: ScriptInput) -> Dict[str, str]:
    """
    生成话术风格画像，指导 LLM 长短与语气。
    """
    emotion_note = _normalize_text(input_data.emotion_note, fallback="").lower()
    short_keywords = ["急", "尽快", "马上", "催", "快点", "快处理"]
    angry_keywords = ["生气", "投诉", "不满", "气愤", "差评"]
    missing_evidence = bool(input_data.facts.missing_evidence)

    length_style = "normal"
    if any(word in emotion_note for word in short_keywords):
        length_style = "short"
    elif missing_evidence:
        length_style = "medium"

    tone_style = "professional"
    if any(word in emotion_note for word in angry_keywords):
        tone_style = "calm"
    elif input_data.strategy_output.strategy == STRATEGY_COMPENSATE:
        tone_style = "empathetic"

    return {
        "length_style": length_style,
        "tone_style": tone_style,
        "must_request_evidence": "yes" if missing_evidence else "no",
    }


def _compress_short_sentence(text: str, limit: int = 40) -> str:
    """
    在短句模式下裁剪话术长度，避免大段文本。
    """
    normalized = _normalize_text(text, fallback="")
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _sanitize_script_text(text: str) -> str:
    """
    去除典型 AI 腔连接词，保持口语客服风格。
    """
    cleaned = _normalize_text(text, fallback="")
    replacements = {
        "尊敬的": "",
        "为了更好地为您服务": "",
        "首先": "先",
        "其次": "然后",
        "最后": "后续",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return cleaned.strip()


def _parse_llm_script_json(raw_text: str) -> Dict[str, str] | None:
    """
    解析 LLM 输出的 JSON 三版话术。
    """
    normalized = raw_text.strip()
    if normalized.startswith("```"):
        normalized = normalized.replace("```json", "").replace("```", "").strip()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    required_keys = {"defense_version", "negotiate_version", "compensate_version"}
    if not required_keys.issubset(payload.keys()):
        return None
    result: Dict[str, str] = {}
    for key in required_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        result[key] = value.strip()
    return result


def _llm_generate_scripts(
    input_data: ScriptInput,
    variables: Dict[str, str],
    tone_profile: Dict[str, str],
    fast_path: bool = False,
) -> Dict[str, str] | None:
    """
    通过 LLM 生成三版真人客服话术。

    fast_path=True 时优先小模型，若输出结构不合规则自动回退主模型补调一次。
    """
    payload = {
        "strategy": input_data.strategy_output.strategy,
        "reasoning": input_data.strategy_output.reasoning,
        "fact_summary": variables["fact_summary"],
        "order_id": variables["order_id"],
        "order_amount": variables["order_amount"],
        "tone_profile": tone_profile,
    }
    model_env_key = "AGENT3_LLM_MODEL_FAST" if fast_path else "AGENT3_LLM_MODEL"
    fallback_key = "AGENT3_LLM_MODEL" if fast_path else None
    llm_text = chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是资深电商客服。请输出JSON，包含 defense_version/negotiate_version/compensate_version 三个字段。"
                    "要求自然口语、像真人客服，避免模板腔和AI味。"
                    "若 tone_profile.length_style=short，每条尽量控制在40字内。"
                    "若 must_request_evidence=yes，话术要先提出补证请求。"
                ),
            },
            {"role": "user", "content": f"请生成三版话术：\n{json.dumps(payload, ensure_ascii=False)}"},
        ],
        model_env_key=model_env_key,
        fallback_model_env_key=fallback_key,
        temperature=0.5,
    )
    parsed = _parse_llm_script_json(llm_text) if llm_text else None
    if parsed:
        return parsed

    # 小模型路径未产出合规 JSON 时，回退主模型补调一次，保证三版话术可用性。
    if fast_path and os.getenv("AGENT3_LLM_MODEL", "").strip():
        retry_text = chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是资深电商客服。请输出JSON，包含 defense_version/negotiate_version/compensate_version 三个字段。"
                        "要求自然口语、像真人客服，避免模板腔和AI味。"
                        "若 tone_profile.length_style=short，每条尽量控制在40字内。"
                        "若 must_request_evidence=yes，话术要先提出补证请求。"
                    ),
                },
                {"role": "user", "content": f"请生成三版话术：\n{json.dumps(payload, ensure_ascii=False)}"},
            ],
            model_env_key="AGENT3_LLM_MODEL",
            temperature=0.5,
        )
        return _parse_llm_script_json(retry_text) if retry_text else None
    return None


# ---------- 主入口：拉三策略模板并组装 ScriptOutput ----------
def generate(input_data: ScriptInput, fast_path: bool = False) -> ScriptOutput:
    """
    拉取三策略模板、填入变量，生成抗辩/协商/善后三版话术及推荐标识。

    纯函数：内部通过 get_script_template 读文件，不在本函数内直接 open 网络；
    模板读取副作用封装在工具层。

    参数:
        input_data: 含 strategy_output、facts、order_id、order_amount、可选 emotion_note。
        fast_path: 是否启用轻量模型优先路径（失败会自动回退主模型）。

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
    tone_profile = _derive_tone_profile(input_data=input_data)

    defense_template = get_script_template(STRATEGY_DEFEND)
    negotiate_template = get_script_template(STRATEGY_NEGOTIATE)
    compensate_template = get_script_template(STRATEGY_COMPENSATE)

    defense_version = _fill_template(defense_template, variables)
    negotiate_version = _fill_template(negotiate_template, variables)
    compensate_version = _fill_template(compensate_template, variables)

    llm_scripts = _llm_generate_scripts(
        input_data=input_data,
        variables=variables,
        tone_profile=tone_profile,
        fast_path=fast_path,
    )
    if llm_scripts:
        defense_version = llm_scripts["defense_version"]
        negotiate_version = llm_scripts["negotiate_version"]
        compensate_version = llm_scripts["compensate_version"]

    defense_version = _sanitize_script_text(defense_version)
    negotiate_version = _sanitize_script_text(negotiate_version)
    compensate_version = _sanitize_script_text(compensate_version)
    if tone_profile["length_style"] == "short":
        defense_version = _compress_short_sentence(defense_version)
        negotiate_version = _compress_short_sentence(negotiate_version)
        compensate_version = _compress_short_sentence(compensate_version)

    recommended_version = _strategy_to_recommended_version(input_data.strategy_output.strategy)
    usage_tip = _build_usage_tip(input_data=input_data, recommended_version=recommended_version)

    return ScriptOutput(
        defense_version=defense_version,
        negotiate_version=negotiate_version,
        compensate_version=compensate_version,
        recommended_version=recommended_version,
        usage_tip=usage_tip,
    )
