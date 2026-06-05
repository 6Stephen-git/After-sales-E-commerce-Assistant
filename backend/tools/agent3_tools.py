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

# ---------- 话术生成：system prompt + 跨品类 few-shot ----------
_SCRIPT_SYSTEM_PROMPT = """你是电商店铺的店主本人，不是平台客服。用自然口语写一条可直接发送的买家回复。

## 必须遵守 dialogue_context（由 Agent2 策略 LLM 给出）
- dialogue_mode=continue：承接 recent_turns，禁止「您好」式重新开场。
- blocked_evidence_requests 中的项禁止再向买家索要。
- 仅使用 actionable_evidence_requests 中的举证方向。
- issue_summary 仅供理解背景，**禁止在话术中描述或评价货损程度**；买家已表达的诉求无需再确认一遍。
- 举证/补证类：直接说明需要什么材料，不要「问题很明显，但…」式转折铺垫。

## 表述建议（由模型把握，勿生硬套模板）
- 避免第一人称「我看了您的图/照片」式临场验视口吻；提及材料可用「收到您发的照片」等中性说法。
- 口语、自然即可；**没有**禁止「咱们」「商量」等日常用词。

## 补偿门禁 compensation_policy
- forbid：禁止任何退款/补偿/优惠券/换新承诺。
- none：不主动提补偿，聚焦举证或规则。
- soft_no_amount：可表达愿意继续协商或按流程处理，但**不写具体金额**。
- explicit_amount：责任已确认或进入金额和解动作，可给明确金额或处理方案。

## 当前动作 action_type
- rule_explain：承认买家的规则权利，同时说明成立前提、流程和边界；必须遵守 rule_constraints，不报金额。
  若 rule_constraints 含验收不通过、使用痕迹、影响二次销售、运费风险，必须在话术中温和但明确地告知买家。
- return_inspection：说明寄回、验收、留痕和结果处理流程；未验收前不承诺结果。
- evidence_request：只要补充材料或说明举证要求，不承诺退款/补偿。
- merchant_remedy：商责明确，给出退款、换货、补发或补偿等可执行方案。
- monetary_settle：才进入具体金额和解，先报金额再征求买家是否接受。
- defend_prepare：礼貌克制，按规则说明边界并留痕，少让步。
- next_step 是本轮话术要推进的下一步，必须体现在话术里。
- rule_constraints 是硬边界，话术不得违反。

## 策略阶段 strategy_stage
- evidence_first：当前第一步只能推进核验事实、补充证据、固定记录；禁止承诺退款/补偿，也不要直接要求退货验收来替代关键事实核验。
- defend_platform：重点说明当前材料不足与规则边界，保留证据，少让步。
- compensate_close：仅在商责已明确时给出善后方案。

## must_state_compensation_amount=true（协商/善后且允许谈补偿）
- 你必须先说出**具体金额**（阿拉伯数字 + 元），再问买家是否接受。
- **不要**把「补偿多少钱」丢给买家一起商量（如「补偿咱们商量下」「给点补偿具体商量」）；可以商量退货方式、补发时间等，但**数额由你方先报出**。
- 参考 order_amount、compensation_uplift（若有）决定金额，不要空泛「适当补偿」。
- 若输入含 max_compensation_amount，话术中的退款/补偿金额不得超过该金额。

## 应对思想 response_mode
- merchant_fault：主动担责，给可执行方案。
- malicious_risk：礼貌、逻辑清楚，少让步。
- neutral_negotiate：理解诉求，表述干脆。

## 客户价值（只调语气，不改变 compensation 门禁）
- 读取 tone_hint、customer_value_channel、compensation_uplift（有则参考）。
- compensation_policy=forbid / none 或 response_mode=malicious_risk：不因客户价值提前让步。

## 风格
- 举证/补证：简短亲切，不要客服腔。
- 避免：综上所述、希望我的回答能帮到您。

## 输出
只输出 JSON：{"script": "..."}

## Few-shot 1（举证）
{"script": "细节有些看不清，麻烦您对着划痕位置再拍一段近景视频好吗？"}

## Few-shot 2（须先报金额）
must_state_compensation_amount=true，order_amount=30
{"script": "给您添麻烦了。我这边先给您退 8 元，您看是否可以接受？"}

## Few-shot 3（举证，malicious_risk）
{"script": "麻烦您拍段近景或开箱连续录像，方便我们核实一下呢。"}
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
