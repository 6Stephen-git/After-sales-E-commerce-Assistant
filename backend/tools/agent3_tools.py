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
from schemas import (
    SETTLEMENT_EXCHANGE,
    SETTLEMENT_PARTIAL_COMPENSATE,
    SETTLEMENT_REFUND_FULL,
    SETTLEMENT_REFUND_ONLY,
    SETTLEMENT_RETURN_REFUND,
)


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

# ---------- 举证已齐：拖延式「核对材料后再处理」 ----------
_EVIDENCE_COMPLETE_STALL_PATTERNS = (
    re.compile(r"核对.{0,12}材料"),
    re.compile(r"核实.{0,8}材料"),
    re.compile(r"正在.{0,8}核对.{0,16}(材料|凭证|举证)"),
    re.compile(r"看下.{0,8}材料"),
    re.compile(r"材料.{0,8}(齐|全|齐全|够了|完备)"),
    re.compile(r"材料.{0,12}(就|再|后).{0,8}(处理|回复|办理|给您)"),
    re.compile(r"核实完.{0,10}(马上|尽快|就).{0,10}(回|处理|回复|办理|给您)"),
    re.compile(r"核对完.{0,10}(马上|尽快|就).{0,10}(回|处理|回复|办理|给您)"),
)

# ---------- 方案空间：禁止仅退款表述 ----------
_REFUND_ONLY_PROMISE_PATTERNS = (
    re.compile(r"仅退(款)?"),
    re.compile(r"不用退(货)?"),
    re.compile(r"不退货.{0,6}(退|赔)"),
    re.compile(r"直接.{0,4}(退|赔).{0,6}到账"),
)

# ---------- 方案空间：各 offered_mode 的口语识别 ----------
_OFFERED_MODE_MARKERS: dict[str, tuple[str, ...]] = {
    SETTLEMENT_RETURN_REFUND: ("退货", "寄回", "退回"),
    SETTLEMENT_EXCHANGE: ("换货", "换新", "换一条"),
    SETTLEMENT_PARTIAL_COMPENSATE: ("赔", "补偿", "元"),
    SETTLEMENT_REFUND_FULL: ("全额退", "全款退"),
}
_ROBOT_MARKERS = (
    "我理解您的感受",
    "我非常理解",
    "我们会尽快处理",
    "请您放心我们会",
    "这边已经",
    "已经为您",
    "我们会尽力",
    "深感抱歉",
    "给您带来不便",
    "非常抱歉",
)

# ---------- 非终局抗辩时：禁止向买家亮规则条文 ----------
_RULE_EXPOSURE_PATTERNS = (
    re.compile(r"根据.{0,12}规则"),
    re.compile(r"依据.{0,16}(规则|服务标|服务标识|平台)"),
    re.compile(r"《[^》]{2,}》"),
    re.compile(r"签收后.{0,12}\d+.{0,8}(小时|天).{0,12}(申请|举证|提交|退款)"),
)

# ---------- 施压降格：禁止对买家抢先亮平台对峙牌 ----------
_PLATFORM_CONFRONTATION_PHRASES = (
    "平台介入",
    "申请平台介入",
    "走平台",
    "备平台",
    "等平台处理",
    "让平台来判",
)
_VAGUE_COMPENSATION_PATTERNS = (
    re.compile(r"补偿.{0,12}商量|商量.{0,12}(?:补偿|退多少|多少钱|多少元|数目)"),
    re.compile(r"(?:给点|给些|一点|些许).{0,6}补偿"),
    re.compile(r"一般.{0,8}给.{0,6}补偿"),
    re.compile(r"具体.{0,6}商量"),
)

_ACCEPTANCE_MARKERS = ("可以吗", "是否接受", "您看", "行吗", "能接受", "可不可以")

_BUYER_FACING_RATIO_PATTERNS = (
    re.compile(r"\d+(?:\.\d+)?\s*%"),
    re.compile(r"百分之"),
    re.compile(r"订单.{0,8}(总额|金额).{0,6}%"),
    re.compile(r"不超过.{0,12}%"),
    re.compile(r"占比"),
)

_AMOUNT_PATTERN = re.compile(r"\d+(?:\.\d+)?")


# ---------- 话术生成：system prompt ----------
_SCRIPT_SYSTEM_PROMPT = """你是电商店主本人（非平台客服），写一条可直接发送的口语回复。

---

## 思考框架

先在内部回答三个问题（不输出给买家）：
1. **当前目标是什么？** — 安抚情绪 / 请求补证 / 给出方案 / 讲清规则 / 善后收尾
2. **这样说合理吗？** — 在规则和事实上站得住脚吗
3. **如果我是客户，我能接受吗？** — 换位思考

想清楚了再开口。

---

## 语气随目标走

| 目标 | 语气 |
|------|------|
| 安抚情绪 | 温和、耐心、共情 |
| 请求补证 | 专业、引导、不施压 |
| 给出方案 | 果断、清晰、有担当 |
| 讲清规则 | 冷静、有理有据、不卑不亢；施压场景先接住情绪再讲边界 |
| 善后收尾 | 真诚、贴心、有温度 |

---

## 说话方式

- 真人聊天
- 复杂话语分多句说，不堆大段
- 短句优先，一句一事
- 主动担责，给人安全感

### 优秀示例

- "这单我来帮您搞定"
- "您放心，有消息我第一时间回复您"
- "真不好意思了哥，给您添麻烦了"
- "亲亲麻烦您拍一下商品正面的近照呗"
- 商责善后："感谢您的体谅，希望您能再给小店一次机会！"

### 禁止出现

- "非常抱歉给您带来不便"
- "这边建议您..."
- "感谢您的理解与支持"
- "我们会尽力满足您的要求"
- "这图我看了"、"这确实让您不舒服了"
- "我理解您的感受"、"我非常理解"、"我们会尽快处理"
- 任何明显的客服套话或模板腔

---

## 约束（必须遵守）

user 消息为 JSON，含 action_type、compensation_policy、next_step、rule_constraints、resolution_contract 等。
**服从这些字段的业务意图**，但面向买家的表达须口语化。

- resolution_contract 为方案空间真源：decision_ready=true 时须说明 offered_modes 中至少一种可执行方案；forbidden_modes 禁止承诺；require_inspection_before_refund=true 时禁口头「直接退款到账」
- dialogue_context.blocked_evidence_requests 禁再索要
- compensation_policy 决定能否谈钱；resolution_contract.proposed_compensation_amount 有值时须按该**具体元**报价并征求接受
- **面向买家禁止出现补偿比例**：不写「30%」「百分之」「订单总额X%」「不超过…%」等，部分补偿只说「赔您XX元」
- evidence_first 只推进补证，禁提前承诺补偿
- 禁踢皮球：不写「看能不能处理」「帮您核实能不能赔」
- evidence_complete=true 或 missing_evidence 为空且非补证动作：**禁止**「核对材料」「材料齐了就处理」「核实完马上回您」等拖延腔；须直接说明当前结论、边界或可接受方案

### 规则怎么说

- defend_prepare + 高恶意：可说明规则边界，但须先承接情绪再给结论，勿首句提平台介入
- buyer_service_posture=de_escalate_within_bounds：识别到施压但对外协商降格，先服务后讲边界，禁止向买家提平台介入/备料
- 其他场景：**禁止**引用服务标名称、规则原文、书名号条款、时效数字
- recent_turns 或 next_step 已写明签收间隔的：**禁止**再向买家核实签收时间
- 用口语表达「隔了挺久」「手头材料还差一点」，不亮底牌

### 补证怎么说

- 只索要买家**客观上能当场提供**的材料（现状照、外包装、拆开/存放情况、物流面单等）
- 感官问题（异味、口感）以买家描述为准，禁要求检测报告
- 材料未齐时明确还要什么
- 材料已够时直接讲结论/方案/边界，**勿**再说「再看下材料」「核实完马上处理」

---

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


def _contains_robot_marker(text: str) -> bool:
    """
    检测话术中是否含人机味标记词。
    """
    normalized = (text or "").strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in _ROBOT_MARKERS)


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


def _has_buyer_facing_ratio_exposure(script: str) -> bool:
    """面向买家话术是否暴露补偿比例（应只说具体元）。"""
    normalized = (script or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _BUYER_FACING_RATIO_PATTERNS)


def _collect_quality_issues(script: str, payload: dict[str, Any]) -> list[str]:
    """
    须报金额场景：缺数字、缺征求同意、或未按契约确定数额报价。
    """
    issues: list[str] = []
    proposed = payload.get("proposed_compensation_amount")
    rc = payload.get("resolution_contract")
    partial_offered = isinstance(rc, dict) and "partial_compensate" in (rc.get("offered_modes") or [])
    if proposed is not None or partial_offered or payload.get("must_state_compensation_amount"):
        if _has_buyer_facing_ratio_exposure(script):
            issues.append("面向买家勿说补偿比例或百分之，部分补偿须用具体元报价")

    if proposed is not None:
        try:
            proposed_value = float(proposed)
        except (TypeError, ValueError):
            proposed_value = None
        if proposed_value is not None:
            if not _AMOUNT_PATTERN.search(script):
                issues.append("须按契约写出具体补偿金额（含数字）")
            elif not any(
                abs(float(match.group(0)) - proposed_value) < 0.01
                for match in _AMOUNT_PATTERN.finditer(script)
            ):
                issues.append(f"须按契约报价 {proposed_value:g} 元，勿改为上限内商量")
            if not any(marker in script for marker in _ACCEPTANCE_MARKERS):
                issues.append("须征求买家是否接受已报出的方案")
        return issues

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


def _is_evidence_complete_context(payload: dict[str, Any]) -> bool:
    """
    判断当前是否处于「举证已齐、应给明确结论」的语境。

    evidence_request / evidence_first 阶段不适用本条门禁。
    """
    if payload.get("evidence_complete") is True:
        return True
    action = str(payload.get("action_type") or "").strip().lower()
    if action == "evidence_request":
        return False
    stage = str(payload.get("strategy_stage") or "").strip().lower()
    if stage == "evidence_first":
        return False
    missing = payload.get("missing_evidence")
    if isinstance(missing, list):
        return len(missing) == 0
    return False


def _has_evidence_complete_stall(script: str, payload: dict[str, Any]) -> bool:
    """举证已齐时，检测是否用「核对材料后再处理」类拖延表述。"""
    if not _is_evidence_complete_context(payload):
        return False
    normalized = (script or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _EVIDENCE_COMPLETE_STALL_PATTERNS)


def _violates_resolution_contract(script: str, payload: dict[str, Any]) -> list[str]:
    """检测话术是否越界 resolution_contract 方案空间。"""
    rc = payload.get("resolution_contract")
    if not isinstance(rc, dict):
        return []
    issues: list[str] = []
    normalized = (script or "").strip()
    if not normalized:
        return issues

    forbidden = rc.get("forbidden_modes") or []
    if SETTLEMENT_REFUND_ONLY in forbidden:
        if any(pattern.search(normalized) for pattern in _REFUND_ONLY_PROMISE_PATTERNS):
            issues.append("方案契约禁止仅退款不退货，须给出退货退款/换货/规则内补偿等可执行选项")

    if rc.get("require_inspection_before_refund"):
        if "到账" in normalized and not any(
            word in normalized for word in ("退货", "寄回", "验收", "退回")
        ):
            issues.append("商责善后须走退货验收，验收前勿承诺退款到账")

    if rc.get("decision_ready") and SETTLEMENT_REFUND_FULL in forbidden:
        if any(word in normalized for word in ("全额退", "全款退", "全退")) and "部分" not in normalized:
            if not any(word in normalized for word in ("退货", "寄回", "验收")):
                issues.append("时效不满足时勿承诺无前提全额退款，可说明规则内部分补偿或退货流程")

    offered = rc.get("offered_modes") or []
    if rc.get("decision_ready") and offered:
        if payload.get("must_state_compensation_amount") and SETTLEMENT_PARTIAL_COMPENSATE in offered:
            return issues
        if not any(
            any(marker in normalized for marker in _OFFERED_MODE_MARKERS.get(mode, (mode,)))
            for mode in offered
        ):
            issues.append("须向买家明确说明 resolution_contract.offered_modes 中的可接受方案")
    return issues


def _has_platform_confrontation_opener(script: str) -> bool:
    """施压降格场景：检测是否向买家抢先亮平台对峙表述。"""
    normalized = (script or "").strip()
    if not normalized:
        return False
    head = normalized[:48]
    return any(phrase in head for phrase in _PLATFORM_CONFRONTATION_PHRASES)


def _collect_style_issues(script: str, payload: dict[str, Any]) -> list[str]:
    """
    口语风格门禁：过早亮规则、踢皮球许诺、人机味表达、施压场景亮平台牌。
    """
    issues: list[str] = []
    if _has_premature_rule_exposure(script, payload):
        issues.append("非终局抗辩阶段勿向买家引用规则条文或具体时效数字")
    if str(payload.get("buyer_service_posture") or "").strip() == "de_escalate_within_bounds":
        if _has_platform_confrontation_opener(script):
            issues.append("施压降格场景勿向买家抢先提平台介入，先承接情绪并说明材料与可接受处理方式")
    if _has_evidence_complete_stall(script, payload):
        issues.append("举证已齐时勿说核对材料/核实完再处理，应直接说明结论、边界或可接受方案")
    issues.extend(_violates_resolution_contract(script, payload))
    if _contains_robot_marker(script):
        issues.append("话术含人机味表达，请改为店主口吻")
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
