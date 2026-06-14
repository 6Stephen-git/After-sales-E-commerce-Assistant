"""
对话 Agent 核心模块。

职责：智能模式下 LLM + function calling 驱动的客服对话主控。
接收买家消息 + 上下文，通过反思三问 + 工具调用生成回复。

约束：
- 不修改辅助模式任何代码
- 工具调用通过 xxx_simple 轻量入口
- 状态更新：LLM 通过 update_state；工具结论仅经 record_tool_finding 写入 Redis 并跨轮注入 prompt
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from backend.agents.conversation_agent.context import (
    build_initial_context,
    save_state_to_redis,
)
from backend.tools.agent1_tools import analyze_image_simple
from backend.tools.agent2_tools import (
    detect_malicious_simple,
    evaluate_customer_value_simple,
    query_buyer_profile,
    search_similar_cases_simple,
)
from backend.tools.intelligent_tools import (
    build_handoff_summary,
    check_handoff_threshold,
    format_tool_findings_for_prompt,
    record_tool_finding,
    update_state,
)
from backend.tools.platform_api import query_logistics
from backend.tools.rule_matcher import match_rules_simple
from backend.tools.simulation_fixture import (
    apply_simulated_order_context,
    get_simulated_logistics_text,
)
from backend.tools.llm_client import chat_completion_assistant_message
from schemas import (
    VALID_CREDENTIAL_TRUST,
    AgentReply,
    ChatTurn,
    EVIDENCE_LOW,
    EVIDENCE_MEDIUM,
    EvidenceSummary,
    EvidenceSummaryInput,
    FactOutput,
    INTEL_PHASE_DEFENSE,
    INTEL_PHASE_EVIDENCE,
    INTEL_PHASE_HANDOFF,
    INTEL_PHASE_SETTLE,
    INTEL_PHASE_STRATEGY,
    IntelligentContext,
    IntelligentState,
    KeyDecision,
    UpdateStateInput,
)

LOG_PREFIX = "[ConversationAgent]"
logger = logging.getLogger(__name__)

# 最大工具调用轮次（防止死循环）
MAX_TOOL_ROUNDS = 5

# System prompt 模板文件路径
_PROMPT_FILE = Path(__file__).parent / "prompts" / "system_prompt.md"


def _load_system_prompt_template() -> str:
    """从文件加载 system prompt 模板。"""
    try:
        return _PROMPT_FILE.read_text(encoding="utf-8").strip()
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 加载 system prompt 失败：%s", LOG_PREFIX, exc)
        # 最小化兜底
        return (
            "你是电商客服店主助理。温和、耐心、有担当。\n"
            "回复前先思考：当前目标是什么？这样说合理吗？客户能接受吗？\n"
            "像真人聊天，分多句说，主动担责，禁止人机感表达。"
        )


def _tools_already_called(state: IntelligentState) -> set[str]:
    """汇总历史 tool_findings 中已执行过的工具名。"""
    return {str(item.tool).strip() for item in state.tool_findings if str(item.tool).strip()}


def _format_phase_tool_suggestions(context: IntelligentContext) -> str:
    """
    按当前阶段与上下文生成工具建议（按需选用，不写死顺序）。
    """
    state = context.current_state
    phase = str(state.phase or INTEL_PHASE_EVIDENCE).strip()
    called = _tools_already_called(state)
    picks: list[str] = []

    if phase == INTEL_PHASE_EVIDENCE:
        if context.buyer_id and "query_buyer_profile" not in called:
            picks.append("query_buyer_profile")
        if context.order_id and "query_logistics" not in called:
            picks.append("query_logistics")
        if "match_rules_simple" not in called:
            picks.append("match_rules_simple")
    elif phase == INTEL_PHASE_STRATEGY:
        if "match_rules_simple" not in called:
            picks.append("match_rules_simple")
        if context.buyer_id and "evaluate_customer_value_simple" not in called:
            picks.append("evaluate_customer_value_simple")
        picks.append("detect_malicious_simple（有疑点时）")
        if "search_similar_cases_simple" not in called:
            picks.append("search_similar_cases_simple")
    elif phase == INTEL_PHASE_SETTLE:
        picks.append("update_state（记录方案与阶段）")
        if state.evidence_summary.missing:
            picks.append("先退回 evidence_collection 补证，再谈结案")
    elif phase == INTEL_PHASE_DEFENSE:
        if "match_rules_simple" not in called:
            picks.append("match_rules_simple")
        picks.append("detect_malicious_simple（有疑点时）")
        if "search_similar_cases_simple" not in called:
            picks.append("search_similar_cases_simple")
    elif phase == INTEL_PHASE_HANDOFF:
        picks.append("update_state（phase=handoff）")

    if not picks:
        return ""
    return "- 本阶段可考虑工具：" + "、".join(picks) + "（按需选用，非固定顺序）"


def _format_phase_stage_hint(context: IntelligentContext) -> str:
    """
    按当前 phase 与证据缺口生成本轮操作提醒（约束决策边界，不限具体说法）。
    """
    state = context.current_state
    phase = str(state.phase or INTEL_PHASE_EVIDENCE).strip()
    missing = [str(item).strip() for item in (state.evidence_summary.missing or []) if str(item).strip()]

    lines = ["---", "本阶段操作提醒（约束决策边界，不限说法）："]

    if phase == INTEL_PHASE_EVIDENCE:
        lines.append("- 当前重心：固定事实与举证，弄清问题、责任线索与材料缺口。")
        if missing:
            lines.append(
                "- 缺失证据未补齐前：勿承诺退款、仅退款或具体赔偿金额；可说核实后再定方案。"
            )
        else:
            lines.append(
                "- 关键材料基本齐全：可经 update_state 进入 strategy_negotiation 定策略方向。"
            )
        lines.append("- 过关自问：责任能判断吗？疑点核实了吗？还缺什么？")
    elif phase == INTEL_PHASE_STRATEGY:
        lines.append("- 当前重心：定策略方向（补证/协商/守底线），原则上不落地具体金额。")
        lines.append("- 过关自问：诉求在规则内吗？策略与证据一致吗？")
    elif phase == INTEL_PHASE_SETTLE:
        lines.append("- 当前重心：在赔偿上限内给出明确方案，争取买家确认。")
        if missing:
            lines.append("- 仍有缺失证据：应先退回 evidence_collection 补证，勿强行结案。")
    elif phase == INTEL_PHASE_DEFENSE:
        lines.append("- 当前重心：守底线、留痕，整理材料应对平台介入；冷静有据，不激化。")
    elif phase == INTEL_PHASE_HANDOFF:
        lines.append("- 当前重心：交接人工，说明现状与已收集材料。")
    else:
        lines.append("- 阶段未识别：建议先 evidence_collection 固定事实。")

    tool_line = _format_phase_tool_suggestions(context)
    if tool_line:
        lines.append(tool_line)

    lines.append("- 阶段变化须 update_state 并写 update_reason；买家新举证或改口可退回上一阶段。")
    return "\n".join(lines)


def _build_system_prompt(context: IntelligentContext, *, pending_attachments_addon: str = "") -> str:
    """
    将 system prompt 模板填充上下文变量，并注入阶段导航动态提醒。

    参数:
        context: 对话上下文。

    返回:
        填充后的 system prompt 文本。
    """
    template = _load_system_prompt_template()

    # 填充动态变量
    max_comp = context.max_compensation
    max_comp_text = f"{max_comp:.0f}" if max_comp > 0 else "不限制"

    # 将当前状态注入 prompt
    state = context.current_state
    state_text = (
        f"\n\n---\n当前案件状态：\n"
        f"- 阶段：{state.phase}\n"
        f"- 责任归属：{state.responsibility}\n"
        f"- 当前策略：{state.current_strategy}\n"
        f"- 买家类型：{state.buyer_type}\n"
        f"- 风险等级：{state.risk_level}\n"
        f"- 赔偿上限：{max_comp_text}元\n"
    )
    if state.evidence_summary.collected:
        state_text += f"- 已收集证据：{', '.join(state.evidence_summary.collected)}\n"
    if state.evidence_summary.missing:
        state_text += f"- 缺失证据：{', '.join(state.evidence_summary.missing)}\n"
    if state.risk_signals:
        state_text += f"- 风险信号：{', '.join(state.risk_signals)}\n"

    # 买家信息注入
    buyer_text = ""
    if context.buyer_id:
        buyer_text = f"\n买家ID：{context.buyer_id}"
    if context.order_amount > 0:
        buyer_text += f"\n订单金额：{context.order_amount:.0f}元"
    if context.platform_service_tags:
        buyer_text += f"\n服务标：{', '.join(context.platform_service_tags)}"

    prompt = template.replace("{max_compensation}", max_comp_text)
    prompt += state_text
    prompt += f"\n{_format_phase_stage_hint(context)}\n"
    if buyer_text:
        prompt += f"\n买家信息：{buyer_text}\n"

    findings_text = format_tool_findings_for_prompt(state)
    if findings_text:
        prompt += f"\n{findings_text}\n"
    if pending_attachments_addon.strip():
        prompt += f"\n{pending_attachments_addon.strip()}\n"

    return prompt


def _format_pending_attachments_addon(
    image_urls: list[str],
    buyer_claim: str,
    *,
    pre_analyzed: bool = False,
) -> str:
    """
    本轮买家附图说明注入 system。

    pre_analyzed=True 时表示服务端已在 LLM 前完成识图，结论在 tool_findings。
    """
    if not image_urls:
        return ""

    if pre_analyzed:
        lead = (
            f"本轮买家附图共 {len(image_urls)} 张（回复前已完成视觉分析，结论见下方工具发现；"
            "勿重复调用 analyze_image_simple，除非买家新发图）："
        )
    else:
        lead = (
            f"本轮买家附图共 {len(image_urls)} 张（须先调用 analyze_image_simple 分析后再回复买家；"
            "参数用 image_index（从 1 起），勿传 image_url）："
        )

    lines = ["---", lead, f"- 诉求锚点：{buyer_claim}"]
    for idx in range(1, len(image_urls) + 1):
        lines.append(f"- 附图{idx}")
    return "\n".join(lines)


def _preanalyze_pending_images(
    *,
    image_urls: list[str],
    context: IntelligentContext,
    state: IntelligentState,
    accumulated_facts: FactOutput,
    turn: int,
) -> tuple[IntelligentState, FactOutput, list[str], bool]:
    """
    本轮附图在 LLM 循环前由服务端识图，避免模型只查物流、跳过视觉分析。
    """
    tools_called: list[str] = []
    if not image_urls:
        return state, accumulated_facts, tools_called, False

    claim = _build_buyer_claim_from_context(context)
    state_updated = False
    for idx, image_url in enumerate(image_urls, start=1):
        logger.info(
            "%s 本轮附图预分析 image_index=%s dispute_id=%s",
            LOG_PREFIX,
            idx,
            context.dispute_id,
        )
        result = analyze_image_simple(image_url=image_url, buyer_claim=claim)
        state = record_tool_finding(
            state,
            "analyze_image_simple",
            turn,
            result,
            extra_facts={"buyer_claim": claim, "image_url": image_url, "image_index": idx},
        )
        _merge_vision_into_facts(accumulated_facts, result)
        tools_called.append("analyze_image_simple")
        state_updated = True

    return state, accumulated_facts, tools_called, state_updated


def _resolve_analyze_image_url(
    *,
    pending_image_urls: list[str],
    arguments: dict[str, Any],
) -> str:
    """
    从本轮 pending 附图或工具参数解析完整 image_url。

    优先 image_index；其次与 pending 精确匹配；单图时回退唯一附图；
    兼容 LLM 误传被截断的 URL 前缀。
    """
    pending = [str(url).strip() for url in pending_image_urls if str(url).strip()]
    if not pending:
        return str(arguments.get("image_url") or "").strip()

    raw_index = arguments.get("image_index")
    if raw_index is not None:
        try:
            idx = int(raw_index)
            if 1 <= idx <= len(pending):
                return pending[idx - 1]
        except (TypeError, ValueError):
            pass

    image_url = str(arguments.get("image_url") or "").strip()
    if image_url in pending:
        return image_url

    if image_url.endswith("..."):
        prefix = image_url[:-3]
        for candidate in pending:
            if candidate.startswith(prefix):
                return candidate

    if len(pending) == 1:
        return pending[0]

    if image_url:
        for candidate in pending:
            if candidate.startswith(image_url):
                return candidate

    return image_url


def _build_buyer_claim_from_context(context: IntelligentContext) -> str:
    """
    从对话历史合成视觉分析锚点文本（buyer_claim）。

    取最近若干轮买家文字，跳过纯 [图片] 占位；无有效文字时用通用说明。
    """
    buyer_parts: list[str] = []
    for turn in context.chat_history:
        if turn.role != "buyer":
            continue
        text = str(turn.content or "").strip()
        if not text or text == "[图片]":
            continue
        buyer_parts.append(text)
    if buyer_parts:
        return "；".join(buyer_parts[-4:])
    return "买家附图举证，请结合对话上下文判断诉求焦点与应关注的可见瑕疵。"


# 本轮附图标注：写入买家 user 消息，与 system 中识图结论对齐
_BUYER_ATTACHMENT_NOTE = (
    "[本轮买家已附图，服务端已完成视觉分析，结论见 system 工具事实；"
    "请据此回复，勿要求买家再发本轮已有的图]"
)


def _mark_current_turn_has_attachments(context: IntelligentContext, image_count: int) -> None:
    """
    在最后一轮买家消息中标注已附图。

    图与文字虽同一次 API 传入，但 messages 里 user 仅含 buyer_message 文本；
    """
    if image_count <= 0 or not context.chat_history:
        return
    last = context.chat_history[-1]
    if last.role != "buyer":
        return
    if _BUYER_ATTACHMENT_NOTE in str(last.content or ""):
        return
    base = str(last.content or "").strip() or "[图片]"
    extra = f"\n[本轮共附图{image_count}张]\n{_BUYER_ATTACHMENT_NOTE}" if image_count > 1 else f"\n{_BUYER_ATTACHMENT_NOTE}"
    context.chat_history[-1] = ChatTurn(role="buyer", content=f"{base}{extra}")


def _build_chat_messages(system_prompt: str, context: IntelligentContext) -> list[dict[str, Any]]:
    """
    构建发给 LLM 的完整消息列表。

    参数:
        system_prompt: 系统提示词。
        context: 对话上下文。

    返回:
        OpenAI 兼容 messages 数组。
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    # 历史对话
    for turn in context.chat_history:
        role = "user" if turn.role == "buyer" else "assistant"
        messages.append({"role": role, "content": turn.content})

    return messages


# ---------- 工具定义（OpenAI function calling 格式） ----------

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_buyer_profile",
            "description": "查询买家画像：购买次数、纠纷率、信誉等级等。对话初期优先调用，了解和谁说话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "buyer_id": {"type": "string", "description": "买家脱敏ID"},
                    "merchant_id": {"type": "string", "description": "商家ID"},
                },
                "required": ["buyer_id", "merchant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image_simple",
            "description": "分析买家发来的图片，提取视觉事实（瑕疵类型、严重度等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_index": {
                        "type": "integer",
                        "description": "附图序号，从 1 开始，对应 system 提示中的附图列表",
                    },
                    "image_url": {
                        "type": "string",
                        "description": "公网可访问图片 URL（可选；本地/base64 附图必须用 image_index）",
                    },
                    "buyer_claim": {"type": "string", "description": "买家的文字诉求描述，用于锚定分析焦点"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_logistics",
            "description": "查询订单物流状态（是否发货、签收、停滞天数等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "订单号"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "match_rules_simple",
            "description": "根据纠纷描述匹配相关平台规则，了解规则边界。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "纠纷自然语言描述"},
                    "service_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "平台服务标标签列表",
                    },
                    "category_slug": {"type": "string", "description": "商品品类slug"},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_customer_value_simple",
            "description": "评估客户长期价值与本单价值，判断是否为高价值老客。",
            "parameters": {
                "type": "object",
                "properties": {
                    "buyer_id": {"type": "string", "description": "买家脱敏ID"},
                    "merchant_id": {"type": "string", "description": "商家ID"},
                    "order_amount": {"type": "number", "description": "订单金额"},
                },
                "required": ["buyer_id", "merchant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_malicious_simple",
            "description": "检测买家是否存在恶意行为（勒索、伪造举证等）。仅在有可疑迹象时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_history": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "聊天记录文本列表",
                    },
                    "buyer_id": {"type": "string", "description": "买家脱敏ID"},
                    "merchant_id": {"type": "string", "description": "商家ID"},
                    "order_amount": {"type": "number", "description": "订单金额"},
                    "description": {"type": "string", "description": "纠纷描述"},
                },
                "required": ["chat_history"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_similar_cases_simple",
            "description": "检索相似历史判例，参考过往处理经验。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "纠纷描述"},
                    "top_k": {"type": "integer", "description": "返回条数", "default": 3},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_state",
            "description": "更新案件状态。仅在案件情况发生实质性变化时调用（新证据、新风险、策略转变）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "phase": {
                        "type": "string",
                        "enum": ["evidence_collection", "strategy_negotiation", "settlement", "defense", "handoff"],
                        "description": "新阶段",
                    },
                    "responsibility": {
                        "type": "string",
                        "enum": ["merchant_fault", "buyer_fault", "unclear", "mixed"],
                        "description": "责任归属",
                    },
                    "current_strategy": {
                        "type": "string",
                        "enum": ["collect_evidence", "negotiate", "compensate", "defend"],
                        "description": "当前策略",
                    },
                    "strategy_rationale": {"type": "string", "description": "策略理由"},
                    "buyer_type": {
                        "type": "string",
                        "enum": ["high_value_old", "normal", "first_time", "suspicious", "malicious"],
                        "description": "买家类型",
                    },
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "风险等级",
                    },
                    "risk_signals": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "风险信号列表",
                    },
                    "evidence_summary": {
                        "type": "object",
                        "description": "证据摘要更新",
                        "properties": {
                            "collected": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "已收集证据（与已有项合并）",
                            },
                            "missing": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "仍缺失的证据（覆盖写入）",
                            },
                            "quality": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "证据质量",
                            },
                        },
                    },
                    "update_reason": {"type": "string", "description": "本次更新原因（必填）"},
                },
                "required": ["update_reason"],
            },
        },
    },
]


def _build_facts_from_context(context: IntelligentContext) -> FactOutput:
    """
    从上下文构建最小 FactOutput，供轻量工具入口补充事实字段。

    missing_evidence 优先沿用 Redis 中 evidence_summary.missing；
    仅在全新案件且无已收集项时使用默认缺证占位。
    """
    desc_parts: list[str] = []
    for turn in context.chat_history:
        if turn.content.strip() and turn.content.strip() != "[图片]":
            desc_parts.append(turn.content.strip())
    description = " ".join(desc_parts[-3:]) if desc_parts else "买家诉求待补充"

    state = context.current_state
    if state.evidence_summary.collected or state.evidence_summary.missing:
        missing = list(state.evidence_summary.missing)
    else:
        missing = ["买家举证图片", "商品实物照片"]

    quality = state.evidence_summary.quality or EVIDENCE_LOW
    confidence = 0.5 if state.evidence_summary.collected else 0.3
    return FactOutput(
        issue_summary=description,
        intent_tags=[],
        evidence_quality=quality,
        confidence=confidence,
        missing_evidence=missing,
    )


def _merge_vision_into_facts(facts: FactOutput, vision: dict[str, Any]) -> None:
    """
    将视觉分析结果合并到已有的 FactOutput（原地修改）。

    只写入视觉模块产出的字段，不覆盖 facts 中已有的非空值。
    """
    if not isinstance(vision, dict) or vision.get("error"):
        return

    # 视觉观察结论
    visual_desc = vision.get("visual_description")
    if isinstance(visual_desc, str) and visual_desc.strip() and visual_desc not in facts.visual_observations:
        facts.visual_observations.append(visual_desc.strip())

    for key in ("findings", "visual_red_flags"):
        for item in vision.get(key) or []:
            text = str(item or "").strip()
            if text and text not in facts.visual_observations:
                facts.visual_observations.append(text)

    # 举证可信度
    trust = str(vision.get("credential_trust", "") or "").strip().lower()
    if trust in VALID_CREDENTIAL_TRUST and trust != "unknown":
        facts.credential_trust = trust
    trust_note = vision.get("credential_trust_note")
    if isinstance(trust_note, str) and trust_note.strip() and not facts.credential_trust_note:
        facts.credential_trust_note = trust_note.strip()

    # 缺陷信息
    defect_type = vision.get("defect_type")
    if isinstance(defect_type, str) and defect_type.strip() and not facts.defect_type:
        facts.defect_type = defect_type.strip()

    defect_location = vision.get("defect_location")
    if isinstance(defect_location, str) and defect_location.strip() and not facts.defect_location:
        facts.defect_location = defect_location.strip()

    # 严重度与可挽回性
    severity = vision.get("visual_defect_severity")
    if isinstance(severity, str) and severity.strip() and not facts.visual_defect_severity:
        facts.visual_defect_severity = severity.strip()

    recoverability = vision.get("visual_goods_recoverability")
    if isinstance(recoverability, str) and recoverability.strip() and not facts.visual_goods_recoverability:
        facts.visual_goods_recoverability = recoverability.strip()

    # 疑点
    for flag in vision.get("visual_red_flags") or []:
        text = str(flag or "").strip()
        if text and text not in facts.red_flags:
            facts.red_flags.append(text)

    # 视觉信息到手后，提升证据质量与置信度
    if facts.visual_observations and facts.evidence_quality == EVIDENCE_LOW:
        facts.evidence_quality = EVIDENCE_MEDIUM
    if facts.confidence < 0.5:
        facts.confidence = 0.5


def _execute_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    context: IntelligentContext,
    round_count: int,
    accumulated_facts: FactOutput,
    pending_image_urls: list[str] | None = None,
) -> tuple[str, IntelligentState, FactOutput]:
    """
    执行单个工具调用，返回结果文本、可能更新的状态和累积事实。

    accumulated_facts 在本轮对话的多轮工具调用间共享，使后续工具
    （如 detect_malicious_simple）能读到前面工具（如 analyze_image_simple）
    产出的视觉/举证信息，避免信息孤岛。

    参数:
        tool_name: 工具名称。
        arguments: 工具参数。
        context: 当前上下文。
        round_count: 当前轮次。
        accumulated_facts: 跨工具轮次累积的 FactOutput。

    返回:
        (result_text, updated_state, updated_accumulated_facts)
    """
    state = context.current_state
    result_text = ""

    try:
        if tool_name == "query_buyer_profile":
            profile = query_buyer_profile(
                buyer_id=arguments.get("buyer_id", context.buyer_id),
                merchant_id=arguments.get("merchant_id", context.merchant_id),
            )
            result_text = profile.model_dump_json()
            state = record_tool_finding(state, tool_name, round_count, profile)

        elif tool_name == "analyze_image_simple":
            claim = str(arguments.get("buyer_claim") or "").strip()
            if not claim:
                claim = _build_buyer_claim_from_context(context)
            image_url = _resolve_analyze_image_url(
                pending_image_urls=pending_image_urls or [],
                arguments=arguments,
            )
            result = analyze_image_simple(
                image_url=image_url,
                buyer_claim=claim,
            )
            result_text = json.dumps(result, ensure_ascii=False)
            state = record_tool_finding(
                state,
                tool_name,
                round_count,
                result,
                extra_facts={"buyer_claim": claim, "image_url": image_url},
            )
            _merge_vision_into_facts(accumulated_facts, result)

        elif tool_name == "query_logistics":
            order_id = arguments.get("order_id", context.order_id)
            logistics = query_logistics(order_id)
            status_text = get_simulated_logistics_text(order_id)
            payload: dict[str, Any] = {
                "order_id": order_id,
                "is_shipped": logistics.is_shipped,
                "is_signed": logistics.is_signed,
                "stagnant_days": logistics.stagnant_days,
                "is_abnormal": logistics.is_abnormal,
            }
            if status_text:
                payload["status_text"] = status_text
            result_text = json.dumps(payload, ensure_ascii=False)
            state = record_tool_finding(state, tool_name, round_count, payload)

        elif tool_name == "match_rules_simple":
            # LLM 参数优先；未传时从 context 回填品类/服务标，确保 doc_id 能被解析
            arg_service_tags = arguments.get("service_tags")
            arg_category_slug = arguments.get("category_slug", "")
            rule_result = match_rules_simple(
                description=arguments.get("description", ""),
                service_tags=arg_service_tags if arg_service_tags else context.platform_service_tags or None,
                category_slug=arg_category_slug or context.product_category_slug or "",
            )
            # 精简输出：只返回 brief 和 display
            brief_texts = [b.brief for b in rule_result.rule_briefs[:5]]
            display_texts = [r.rule_summary for r in rule_result.display_rules[:3]]
            rule_payload = {
                "briefs": brief_texts,
                "display_rules": display_texts,
                "count": len(rule_result.matched_rules),
            }
            result_text = json.dumps(rule_payload, ensure_ascii=False)
            state = record_tool_finding(state, tool_name, round_count, rule_payload)

        elif tool_name == "evaluate_customer_value_simple":
            cv = evaluate_customer_value_simple(
                buyer_id=arguments.get("buyer_id", context.buyer_id),
                merchant_id=arguments.get("merchant_id", context.merchant_id),
                order_amount=arguments.get("order_amount", context.order_amount),
            )
            cv_payload = {
                "long_term_score": cv.long_term_score,
                "order_score": cv.order_score,
                "channel": cv.channel,
                "long_term_triggered": cv.long_term_triggered,
                "order_triggered": cv.order_triggered,
                "compensation_uplift": cv.compensation_uplift,
                "tone_suggestion": cv.tone_suggestion,
            }
            result_text = json.dumps(cv_payload, ensure_ascii=False)
            state = record_tool_finding(state, tool_name, round_count, cv_payload)

        elif tool_name == "detect_malicious_simple":
            # LLM 参数优先；聊天历史与 context 合并，避免只用 LLM 传入的空列表
            arg_chat = arguments.get("chat_history", [])
            if not arg_chat and context.chat_history:
                arg_chat = [turn.content for turn in context.chat_history if turn.content]
            mal = detect_malicious_simple(
                chat_history=arg_chat,
                buyer_id=arguments.get("buyer_id", context.buyer_id),
                merchant_id=arguments.get("merchant_id", context.merchant_id),
                order_amount=arguments.get("order_amount", context.order_amount),
                description=arguments.get("description", ""),
                facts=accumulated_facts,
            )
            mal_payload = {
                "risk_score": mal.risk_score,
                "risk_level": mal.risk_level,
                "signals": [{"type": s.signal_type, "desc": s.description} for s in mal.triggered_signals[:5]],
                "disposition_advice": mal.disposition_advice,
            }
            result_text = json.dumps(mal_payload, ensure_ascii=False)
            state = record_tool_finding(state, tool_name, round_count, mal_payload)

        elif tool_name == "search_similar_cases_simple":
            cases = search_similar_cases_simple(
                description=arguments.get("description", ""),
                top_k=arguments.get("top_k", 3),
            )
            cases_data = [
                {"case_id": c.case_id, "lesson": c.lesson, "outcome": c.outcome}
                for c in cases
            ]
            cases_payload = {"cases": cases_data, "count": len(cases)}
            result_text = json.dumps(cases_payload, ensure_ascii=False)
            state = record_tool_finding(state, tool_name, round_count, cases_payload)

        elif tool_name == "update_state":
            evidence_input = None
            raw_evidence = arguments.get("evidence_summary")
            if isinstance(raw_evidence, dict):
                evidence_input = EvidenceSummaryInput(
                    collected=list(raw_evidence.get("collected") or []),
                    missing=list(raw_evidence.get("missing") or []),
                    quality=str(raw_evidence.get("quality") or EVIDENCE_MEDIUM),
                )
            update_input = UpdateStateInput(
                dispute_id=context.dispute_id,
                update_reason=arguments.get("update_reason", ""),
                phase=arguments.get("phase"),
                responsibility=arguments.get("responsibility"),
                current_strategy=arguments.get("current_strategy"),
                strategy_rationale=arguments.get("strategy_rationale"),
                buyer_type=arguments.get("buyer_type"),
                risk_level=arguments.get("risk_level"),
                risk_signals=arguments.get("risk_signals"),
                evidence_summary=evidence_input,
            )
            state = update_state(state, update_input)
            result_text = json.dumps({"status": "updated", "phase": state.phase}, ensure_ascii=False)

        else:
            result_text = json.dumps({"error": f"未知工具：{tool_name}"}, ensure_ascii=False)

    except Exception as exc:  # noqa: BLE001
        logger.error("%s 工具执行失败 tool=%s：%s", LOG_PREFIX, tool_name, exc)
        result_text = json.dumps({"error": f"工具执行失败：{exc}"}, ensure_ascii=False)

    return result_text, state, accumulated_facts


def chat(
    buyer_message: str,
    *,
    dispute_id: str,
    order_id: str = "",
    order_amount: float = 0.0,
    buyer_id: str = "",
    merchant_id: str = "",
    product_category_slug: str = "",
    platform_service_tags: list[str] | None = None,
    max_compensation: float = 0.0,
    chat_history: list[ChatTurn] | None = None,
    round_count: int = 0,
    image_urls: list[str] | None = None,
    dismiss_round_handoff: bool = False,
) -> AgentReply:
    """
    对话 Agent 主入口：接收买家消息，返回回复。

    工作流程：
    1. 构建上下文（加载/创建 IntelligentState）
    2. 转人工阈值检查
    3. 构建 system prompt + 消息列表
    4. LLM function calling 循环（最多 MAX_TOOL_ROUNDS 轮）
    5. 保存状态到 Redis
    6. 返回 AgentReply

    参数:
        buyer_message: 买家当前消息。
        dispute_id: 纠纷编号。
        order_id: 订单号。
        order_amount: 订单金额。
        buyer_id: 买家脱敏ID。
        merchant_id: 商家ID。
        product_category_slug: 商品品类。
        platform_service_tags: 平台服务标。
        max_compensation: 赔偿上限。
        chat_history: 额外的历史对话（追加到 Redis 状态后）。
        round_count: 当前对话轮次。

    返回:
        AgentReply 实例。
    """
    total_start = time.perf_counter()
    logger.info(
        "%s 开始处理 dispute_id=%s buyer_msg=%s",
        LOG_PREFIX,
        dispute_id,
        buyer_message[:60],
    )

    # 1) 模拟订单补全上下文
    resolved_amount, resolved_buyer_id, resolved_category, resolved_tags = apply_simulated_order_context(
        order_id=order_id,
        order_amount=order_amount,
        buyer_id=buyer_id,
        product_category_slug=product_category_slug,
        platform_service_tags=platform_service_tags,
    )

    # 2) 构建上下文
    context = build_initial_context(
        dispute_id=dispute_id,
        buyer_message=buyer_message,
        order_id=order_id,
        order_amount=resolved_amount,
        buyer_id=resolved_buyer_id,
        merchant_id=merchant_id,
        product_category_slug=resolved_category,
        platform_service_tags=resolved_tags,
        max_compensation=max_compensation,
    )

    # 合并额外历史对话
    if chat_history:
        existing = list(context.chat_history)
        context.chat_history = chat_history + existing

    # 3) 转人工阈值检查
    force_handoff, suggest_handoff, handoff_reason = check_handoff_threshold(
        order_amount=context.order_amount,
        buyer_type=context.current_state.buyer_type,
        risk_level=context.current_state.risk_level,
        buyer_message=buyer_message,
        platform_service_tags=context.platform_service_tags,
        round_count=round_count,
        max_compensation=context.max_compensation,
        dismiss_round_handoff=dismiss_round_handoff,
    )

    if force_handoff:
        handoff_summary = build_handoff_summary(context.current_state)
        logger.info(
            "%s 触发转人工 dispute_id=%s reason=%s",
            LOG_PREFIX,
            dispute_id,
            handoff_reason,
        )
        # 更新状态为 handoff
        state = context.current_state
        handoff_update = UpdateStateInput(
            dispute_id=dispute_id,
            phase="handoff",
            update_reason=handoff_reason,
        )
        state = update_state(state, handoff_update)
        save_state_to_redis(state)

        return AgentReply(
            reply_text="",
            state_updated=True,
            state=state,
            handoff=True,
            handoff_reason=handoff_reason,
            handoff_summary=handoff_summary,
            handoff_suggested=False,
            tools_called=[],
        )

    # 3) 构建 prompt 与累积事实；附图由服务端先识图，再进入 LLM 循环
    accumulated_facts = _build_facts_from_context(context)
    state = context.current_state
    tools_called: list[str] = []
    state_updated = False

    normalized_images = [str(url).strip() for url in (image_urls or []) if str(url).strip()]
    vision_preanalyzed = False
    if normalized_images:
        state, accumulated_facts, pre_tools, pre_updated = _preanalyze_pending_images(
            image_urls=normalized_images,
            context=context,
            state=state,
            accumulated_facts=accumulated_facts,
            turn=round_count,
        )
        tools_called.extend(pre_tools)
        vision_preanalyzed = bool(pre_tools)
        if pre_updated:
            state_updated = True
        context.current_state = state
        _mark_current_turn_has_attachments(context, len(normalized_images))

    attachments_addon = ""
    if normalized_images:
        attachments_addon = _format_pending_attachments_addon(
            normalized_images,
            _build_buyer_claim_from_context(context),
            pre_analyzed=vision_preanalyzed,
        )

    system_prompt = _build_system_prompt(context, pending_attachments_addon=attachments_addon)
    messages = _build_chat_messages(system_prompt, context)

    # 4) LLM function calling 循环
    model_env_key = "CONVERSATION_AGENT_LLM_MODEL"
    fallback_env_key = "AGENT2_LLM_MODEL"

    for tool_round in range(MAX_TOOL_ROUNDS):
        logger.info(
            "%s LLM 调用第 %d 轮 dispute_id=%s",
            LOG_PREFIX,
            tool_round + 1,
            dispute_id,
        )

        assistant_msg = chat_completion_assistant_message(
            messages=messages,
            model_env_key=model_env_key,
            temperature=0.6,
            fallback_model_env_key=fallback_env_key,
            tools=_TOOLS,
            tool_choice="auto",
        )

        if assistant_msg is None:
            logger.error("%s LLM 调用失败 dispute_id=%s", LOG_PREFIX, dispute_id)
            # 降级：返回兜底话术
            fallback_reply = "亲亲不好意思，我这边系统暂时有点问题，稍等一下哈，马上帮您处理"
            save_state_to_redis(state)
            return AgentReply(
                reply_text=fallback_reply,
                state_updated=state_updated,
                state=state,
                handoff=False,
                tools_called=tools_called,
            )

        # 追加 assistant 消息到 messages
        messages.append(assistant_msg)

        # 检查是否有 tool_calls
        tool_calls = assistant_msg.get("tool_calls")
        if not tool_calls or not isinstance(tool_calls, list):
            # 没有工具调用 → 最终回复
            reply_text = assistant_msg.get("content", "") or ""
            break

        # 执行所有工具调用
        for tc in tool_calls:
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            tc_name = func.get("name", "")
            tc_args_str = func.get("arguments", "{}")

            try:
                tc_args = json.loads(tc_args_str) if isinstance(tc_args_str, str) else tc_args_str
            except json.JSONDecodeError:
                tc_args = {}

            logger.info(
                "%s 执行工具 tool=%s round=%s",
                LOG_PREFIX,
                tc_name,
                tool_round + 1,
            )

            result_text, state, accumulated_facts = _execute_tool_call(
                tc_name,
                tc_args,
                context,
                round_count + tool_round,
                accumulated_facts,
                pending_image_urls=normalized_images,
            )
            tools_called.append(tc_name)

            # 检测状态是否发生实质性变化（模型级比较）
            if state.model_dump() != context.current_state.model_dump():
                state_updated = True

            # 追加 tool result 到 messages
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_text,
            })

        # 更新 context 中的状态供下一轮使用
        context.current_state = state
    else:
        # 达到最大轮次，用最后一条 assistant 消息的 content
        reply_text = assistant_msg.get("content", "") or ""
        logger.warning(
            "%s 达到最大工具调用轮次 dispute_id=%s",
            LOG_PREFIX,
            dispute_id,
        )

    # 如果 LLM 没有回复文本，给兜底
    if not reply_text or not reply_text.strip():
        reply_text = "亲亲不好意思，我这边处理上出了点小状况，稍等一下哈"

    # 5) 保存状态到 Redis
    save_state_to_redis(state)

    elapsed_ms = int((time.perf_counter() - total_start) * 1000)
    logger.info(
        "%s 处理完成 dispute_id=%s elapsed_ms=%s tools_called=%s handoff=%s",
        LOG_PREFIX,
        dispute_id,
        elapsed_ms,
        tools_called,
        False,
    )

    return AgentReply(
        reply_text=reply_text.strip(),
        state_updated=state_updated,
        state=state,
        handoff=False,
        handoff_suggested=suggest_handoff,
        handoff_reason=handoff_reason if suggest_handoff else "",
        tools_called=tools_called,
    )
