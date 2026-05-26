"""
Agent 3 工具集：买家话术 LLM 生成。

约束：结构化 JSON 输出；禁用词命中时重试；失败返回 None 由上层走 fallback_script。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.tools.llm_client import chat_completion


logger = logging.getLogger(__name__)
AGENT3_LOG_PREFIX = "[Agent3]"

# ---------- 禁用词：话术生成后轻量校验 ----------
_FORBIDDEN_PHRASES = (
    "综上所述",
    "希望我的回答能帮到您",
    "感谢您的理解与支持",
    "不便之处敬请谅解",
    "给您带来不便深表歉意",
)

# ---------- 话术生成：system prompt + 跨品类 few-shot ----------
_SCRIPT_SYSTEM_PROMPT = """你是电商小店的店主本人，不是平台客服。用自然口语写一条可直接发送的买家回复。

## 必须遵守 dialogue_context（由 Agent2 策略 LLM 给出）
- dialogue_mode=continue：承接 recent_turns，禁止「您好」式重新开场。
- blocked_evidence_requests 中的项禁止再向买家索要。
- 仅使用 actionable_evidence_requests 中的举证方向。
- issue_summary 仅供理解背景，**禁止在话术中描述或评价货损程度**；买家已表达的诉求无需再确认一遍。
- 举证/补证类：先点证据疑点 + 补证请求即可，不要「问题很明显，但…」式转折铺垫。

## 补偿门禁 compensation_policy
- forbid：禁止任何退款/补偿/优惠券/换新承诺。
- negotiate_soft：可商量但不报具体金额。
- explicit：责任已确认，可给明确方案（参考 order_amount、compensation_uplift）。
- none：不主动提补偿，聚焦举证或规则。

## 应对思想 response_mode
- merchant_fault：主动担责，给可执行方案。
- malicious_risk：礼貌、逻辑清楚，少让步。
- neutral_negotiate：理解诉求，留协商余地。

## 客户价值（只调语气与补偿弹性，不改变 disposition / response_mode）
- 读取 payload 中的 `tone_hint`、`customer_value_channel`、`compensation_uplift`（有则参考）。
- `customer_value_channel=long_term`：老客，语气稍暖、可自然表达重视，禁止刻意讨好或额外让利。
- `customer_value_channel=order`：高价值本单，体现认真跟进；`negotiate_soft` / `explicit` 时可参考 `compensation_uplift` 微调补偿表述。
- `customer_value_channel=none` 或未传：保持礼貌即可，不必额外热情。
- `compensation_policy=forbid` / `none` 或 `response_mode=malicious_risk`：不因客户价值提前让步或承诺补偿。

## 风格
- 举证/补图类回复：**一句话搞定**，亲切自然，像熟人帮忙，不要客服腔。
- 禁用：综上所述、希望我的回答能帮到您、感谢您的理解与支持。

## 输出
只输出 JSON：{"script": "..."}

## Few-shot 1
dialogue_mode=continue，举证阶段
{"script": "细节有些看不清，麻烦您对着破洞的地方再拍一段近景视频。"}

## Few-shot 2
dialogue_mode=cold_start，compensate_close，商责确认
{"script": "这次确实是我们的问题，给您添麻烦了。我给您安排退货退款，钱原路返回，您把货寄回就行，您看可以吗？"}

## Few-shot 3
举证阶段，malicious_risk
{"script": "图片右下好像有网络水印，麻烦拍段近景或开箱连续录像方便核实。"}
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


# ---------- 禁用词检测 ----------
def _contains_forbidden_phrase(text: str) -> bool:
    """
    检测话术是否含禁用套话。
    """
    normalized = (text or "").strip()
    if not normalized:
        return True
    return any(phrase in normalized for phrase in _FORBIDDEN_PHRASES)


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


# ---------- 对外工具：生成买家话术正文 ----------
def generate_buyer_script(payload: dict[str, Any]) -> str | None:
    """
    在 dialogue_context 约束下调用 LLM 生成单条买家话术。

    参数:
        payload: 含 dialogue_context、response_mode、strategy_stage、compensation_policy 等。

    返回:
        话术正文字符串；全部失败时返回 None。
    """
    raw = _call_script_llm(payload=payload, model_env_key="AGENT3_LLM_MODEL")
    if not raw:
        logger.error("%s LLM 话术生成失败", AGENT3_LOG_PREFIX)
        return None
    script = _parse_script_json(raw)
    if not script:
        logger.error("%s LLM 话术生成失败：JSON 解析失败", AGENT3_LOG_PREFIX)
        return None
    if _contains_forbidden_phrase(script):
        logger.warning("%s 话术含禁用词，重试", AGENT3_LOG_PREFIX)
        retry_raw = _call_script_llm(payload=payload, model_env_key="AGENT3_LLM_MODEL")
        if retry_raw:
            retry_script = _parse_script_json(retry_raw)
            if retry_script and not _contains_forbidden_phrase(retry_script):
                logger.info("%s LLM 话术生成成功", AGENT3_LOG_PREFIX)
                return retry_script
        return None
    logger.info("%s LLM 话术生成成功", AGENT3_LOG_PREFIX)
    return script
