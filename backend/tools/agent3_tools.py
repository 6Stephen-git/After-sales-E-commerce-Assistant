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

user 消息为 JSON（含 action_type、compensation_policy、strategy_stage、next_step、rule_constraints、dialogue_context、recent_turns、must_state_compensation_amount 等）。**严格服从这些字段**，勿自创规则或金额。

要点：
- continue 须承接 recent_turns；blocked 项禁再索要；next_step 必须体现。
- compensation_policy 决定能否谈钱；must_state_compensation_amount=true 时须先报具体金额（元）并征求接受，不超 max_compensation_amount。
- forbid/none/soft_no_amount 或 rule_explain/evidence_request/return_inspection/defend_prepare：不主动金额和解。
- evidence_first 只推进补证；issue_summary 仅供理解，勿复述货损或重复买家诉求；禁客服套话。

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
    仅拦截典型客服套话。
    """
    normalized = (text or "").strip()
    if not normalized:
        return True
    return any(phrase in normalized for phrase in _FORBIDDEN_PHRASES)


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


def _script_fails_quality_check(script: str, payload: dict[str, Any]) -> bool:
    """
    质量未达标时触发一次重试。
    """
    if _contains_forbidden_phrase(script):
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
        issues = _collect_quality_issues(script, payload)
        logger.warning(
            "%s 话术未通过质量校验 issues=%s，重试一次",
            AGENT3_LOG_PREFIX,
            issues or ["客服套话"],
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
