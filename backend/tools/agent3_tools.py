"""
Agent 3 工具集：买家话术 LLM 生成。

约束：结构化 JSON 输出；仅对客服套话与「空泛商量补偿金额」做轻量校验；失败返回 None 由上层走 fallback_script。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.tools.llm_client import chat_completion


logger = logging.getLogger(__name__)
AGENT3_LOG_PREFIX = "[Agent3]"

# ---------- 禁用词：仅平台客服套话（不做口语词硬拦） ----------
_FORBIDDEN_PHRASES = (
    "综上所述",
    "希望我的回答能帮到您",
    "感谢您的理解与支持",
    "不便之处敬请谅解",
    "给您带来不便深表歉意",
)

# ---------- 未定许诺：踢皮球式「能不能处理」 ----------
_HEDGING_PHRASES = (
    "看能不能处理",
    "能不能帮您处理",
    "看能不能赔",
    "帮您核对能不能",
    "进一步核对，看能不能",
)

# ---------- 非终局抗辩时：禁止向买家亮规则条文 ----------
_RULE_EXPOSURE_PATTERNS = (
    re.compile(r"根据.{0,12}规则"),
    re.compile(r"依据.{0,16}(规则|服务标|服务标识|平台)"),
    re.compile(r"《[^》]{2,}》"),
    re.compile(r"签收后.{0,12}\d+.{0,8}(小时|天).{0,12}(申请|举证|提交|退款)"),
)

# ---------- 须报金额时：空泛「商量补偿数额」表述（非禁止「商量」一词） ----------
_VAGUE_COMPENSATION_PATTERNS = (
    re.compile(r"补偿.{0,12}商量|商量.{0,12}(?:补偿|退多少|多少钱|多少元|数目)"),
    re.compile(r"(?:给点|给些|一点|些许).{0,6}补偿"),
    re.compile(r"一般.{0,8}给.{0,6}补偿"),
    re.compile(r"具体.{0,6}商量"),
)

_ACCEPTANCE_MARKERS = ("可以吗", "是否接受", "您看", "行吗", "能接受", "可不可以")

_AMOUNT_PATTERN = re.compile(r"\d+(?:\.\d+)?")

# ---------- 话术生成：system prompt（约束以 payload 字段为准，少枚举场景） ----------
_SCRIPT_SYSTEM_PROMPT = """你是电商店主本人（非平台客服），写一条可直接发送的口语回复。

user 消息为 JSON（含 action_type、compensation_policy、strategy_stage、next_step、rule_constraints、dialogue_context、recent_turns、missing_evidence、must_state_compensation_amount、malicious_risk_level、tone_hint 等）。**服从这些字段的业务意图**，勿自创规则或金额；但面向买家的表达须口语化、可执行。

要点：
- 基础语气亲切、热情，像真心想帮买家解决问题的店主；短句、一句一事；适度即可，勿谄媚、勿堆叠客套、勿平台腔。
- continue 须承接 recent_turns；dialogue_context.blocked_evidence_requests 禁再索要；next_step 的意图须体现，但禁止照搬其中的规则术语或条文措辞。
- compensation_policy 决定能否谈钱；must_state_compensation_amount=true 时须先报具体金额（元）并征求接受，不超 max_compensation_amount。
- forbid/none/soft_no_amount 或 rule_explain/evidence_request/return_inspection/defend_prepare：不主动金额和解。
- evidence_first 只推进补证；issue_summary 仅供理解，勿复述货损或重复买家诉求；禁客服套话，避免人机味话术和生硬的表示理解客户，如“这我明白”这种。

规则怎么说（面向买家）：
- 仅当 action_type=defend_prepare 且 malicious_risk_level=high 时，才可较直接说明不满足退款/补偿条件及规则边界。
- 其他情况（含 rule_explain、evidence_request、协商推进）：**禁止**引用服务标名称、平台规则原文、书名号条款、具体时效小时/天数；不把 rule_constraints 念给买家。可用口语表达「隔了挺久」「手头材料还差一点」，不亮底牌。
- recent_turns 或 next_step 已写明签收间隔/申请时间的：**禁止**再向买家核实、核对签收时间或天数；

补证怎么说（须可执行）：
- 只索要买家**客观上能当场提供**的材料（现状照、外包装、拆开/存放情况、物流面单等）。
- missing_evidence / actionable_evidence_requests 若含异味、口感、变质气味等感官描述，**禁止**要求检测报告、仪器读数、异味/气味的「客观证明」；感官问题以买家描述为准，改问可拍可说的内容。
- 禁踢皮球：不写「看能不能处理」「帮您核实能不能赔」等未定许诺；材料未齐时明确还要什么，材料已够时给出下一步（报价/方案/说明还需等待核实），勿悬空。

只输出 JSON：{"script": "..."}
"""


# ---------- JSON 解析：话术正文 ----------
def _parse_script_json(raw_text: str) -> str | None:
    """
    解析话术 LLM 输出的 JSON。
    """
    normalized = (raw_text or "").strip()
    if normalized.startswith("```"):
        normalized = normalized.replace("```json", "").replace("```", "").strip()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        logger.warning("%s 话术 JSON 解析失败", AGENT3_LOG_PREFIX)
        return None
    if not isinstance(payload, dict):
        return None
    script = payload.get("script")
    if not isinstance(script, str) or not script.strip():
        return None
    return script.strip()


def _contains_forbidden_phrase(text: str) -> bool:
    """
    拦截客服套话与未定许诺套话。
    """
    normalized = (text or "").strip()
    if not normalized:
        return True
    if any(phrase in normalized for phrase in _FORBIDDEN_PHRASES):
        return True
    return any(phrase in normalized for phrase in _HEDGING_PHRASES)


def _allows_explicit_rule_citation(payload: dict[str, Any]) -> bool:
    """
    仅终局抗辩（高恶意 + defend_prepare）允许向买家较直接亮规则。
    """
    action = str(payload.get("action_type") or "").strip().lower()
    risk = str(payload.get("malicious_risk_level") or "").strip().lower()
    return action == "defend_prepare" and risk == "high"


def _has_premature_rule_exposure(script: str, payload: dict[str, Any]) -> bool:
    """
    非终局抗辩时检测是否向买家暴露规则条文或时效数字。
    """
    if _allows_explicit_rule_citation(payload):
        return False
    normalized = (script or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _RULE_EXPOSURE_PATTERNS)


def _has_vague_compensation_negotiation(script: str) -> bool:
    """
    检测是否把「补偿金额」留给空泛商量（须报金额场景下才调用）。
    """
    return any(pattern.search(script) for pattern in _VAGUE_COMPENSATION_PATTERNS)


def _collect_quality_issues(script: str, payload: dict[str, Any]) -> list[str]:
    """
    须报金额场景：缺数字、缺征求同意、或空泛商量补偿数额。
    """
    issues: list[str] = []
    if not payload.get("must_state_compensation_amount"):
        return issues
    if not _AMOUNT_PATTERN.search(script):
        issues.append("须先写出具体补偿金额（含数字）")
    if not any(marker in script for marker in _ACCEPTANCE_MARKERS):
        issues.append("须征求买家是否接受已报出的方案")
    if _has_vague_compensation_negotiation(script):
        issues.append("勿空泛商量补偿金额，应先报价再征求同意")
    max_amount = payload.get("max_compensation_amount")
    try:
        max_amount_value = float(max_amount)
    except (TypeError, ValueError):
        max_amount_value = None
    if max_amount_value is not None:
        amounts = [float(match.group(0)) for match in _AMOUNT_PATTERN.finditer(script)]
        if amounts and max(amounts) > max_amount_value:
            issues.append(f"补偿金额不得超过上限 {max_amount_value:g} 元")
    return issues


def _collect_style_issues(script: str, payload: dict[str, Any]) -> list[str]:
    """
    口语风格门禁：过早亮规则、踢皮球许诺。
    """
    issues: list[str] = []
    if _has_premature_rule_exposure(script, payload):
        issues.append("非终局抗辩阶段勿向买家引用规则条文或具体时效数字")
    return issues


def _script_fails_quality_check(script: str, payload: dict[str, Any]) -> bool:
    """
    质量未达标时触发一次重试。
    """
    if _contains_forbidden_phrase(script):
        return True
    if _collect_style_issues(script, payload):
        return True
    return bool(_collect_quality_issues(script, payload))


# ---------- LLM 调用：话术生成 ----------
def _call_script_llm(*, payload: dict[str, Any], model_env_key: str) -> str | None:
    """
    调用 LLM 生成买家话术。
    """
    logger.info("%s 开始 LLM 话术生成 model_env_key=%s", AGENT3_LOG_PREFIX, model_env_key)
    return chat_completion(
        messages=[
            {"role": "system", "content": _SCRIPT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"请生成一条面向买家的话术：\n{json.dumps(payload, ensure_ascii=False)}",
            },
        ],
        model_env_key=model_env_key,
        temperature=0.55,
    )


def generate_buyer_script(payload: dict[str, Any]) -> str | None:
    """
    在 dialogue_context 约束下调用 LLM 生成单条买家话术。
    """
    raw = _call_script_llm(payload=payload, model_env_key="AGENT3_LLM_MODEL")
    if not raw:
        logger.error("%s LLM 话术生成失败", AGENT3_LOG_PREFIX)
        return None
    script = _parse_script_json(raw)
    if not script:
        logger.error("%s LLM 话术生成失败：JSON 解析失败", AGENT3_LOG_PREFIX)
        return None
    if _script_fails_quality_check(script, payload):
        issues = _collect_quality_issues(script, payload) or _collect_style_issues(script, payload)
        logger.warning(
            "%s 话术未通过质量校验 issues=%s，重试一次",
            AGENT3_LOG_PREFIX,
            issues or ["客服套话或踢皮球表述"],
        )
        retry_raw = _call_script_llm(payload=payload, model_env_key="AGENT3_LLM_MODEL")
        if retry_raw:
            retry_script = _parse_script_json(retry_raw)
            if retry_script and not _script_fails_quality_check(retry_script, payload):
                logger.info("%s LLM 话术生成成功（重试）", AGENT3_LOG_PREFIX)
                return retry_script
        # 重试仍失败时返回首次结果，避免整条链路无话术
        if script and not _contains_forbidden_phrase(script):
            logger.warning("%s 重试未通过校验，沿用首次话术", AGENT3_LOG_PREFIX)
            return script
        return None
    logger.info("%s LLM 话术生成成功", AGENT3_LOG_PREFIX)
    return script
