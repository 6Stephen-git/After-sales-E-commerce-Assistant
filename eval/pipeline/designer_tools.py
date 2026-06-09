"""
Scenario Designer Agent 工具：规则原文查阅与多轮 tool 调用循环。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.tools.llm_client import chat_completion, chat_completion_assistant_message
from backend.tools.rule_text_reader import extract_rule_design_brief, read_service_rule_text
from eval.pipeline.scenario_llm_utils import scenario_gen_model_env, scenario_gen_temperature

DESIGNER_TOOL_LOG_PREFIX = "[ScenarioDesignerTool]"
logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 3
_TAG_SPLIT_RE = re.compile(r"[,，、;；]+")
_FINAL_USER_NUDGE = (
    "请只输出完整情景 Markdown：第一行必须是 `# 标题`；不要前言、解释、`---` 或代码块。"
    "严格遵守 system 中的段落结构与买家表格列名。"
    "背景品类必须落在「设计约束」列出的适用范围内。"
    "对话记录最后一条必须是买家发言。"
    "商家禁止「图片看到了」「核实下情况」「变形明显」等复述证据或空泛流程句，只追问缺什么材料。"
)


def parse_service_constraint_tags(service_constraints: str) -> list[str]:
    """从 CLI --service-constraints 解析服务标列表。"""
    text = str(service_constraints or "").strip()
    if not text:
        return []
    return [part.strip() for part in _TAG_SPLIT_RE.split(text) if part.strip()]

READ_SERVICE_RULE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_service_rule",
        "description": (
            "读取平台服务标规则原文（data/rules_raw TXT）。"
            "设计情景前可查阅适用范围、服务要求、举证条件、赔偿/退款边界等；需要哪段自己从原文取用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "service_tag": {
                    "type": "string",
                    "description": "服务标中文名，如 坏单包退、七天无理由、伤亡大病包退、破损包退",
                }
            },
            "required": ["service_tag"],
        },
    },
}

DESIGNER_TOOLS: list[dict[str, Any]] = [READ_SERVICE_RULE_TOOL]

# 同一会话内已查阅的服务标，避免重复 read 干扰生成轮次。
_session_read_tags: set[str] = set()


def reset_designer_tool_session() -> None:
    """清空工具会话状态，供单次生成前调用。"""
    _session_read_tags.clear()


def execute_designer_tool(tool_name: str, arguments: dict[str, Any] | str) -> str:
    """执行 Designer 注册的单个工具并返回文本结果。"""
    name = str(tool_name or "").strip()
    if isinstance(arguments, str):
        try:
            parsed_args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            parsed_args = {}
    else:
        parsed_args = arguments if isinstance(arguments, dict) else {}

    if name == "read_service_rule":
        service_tag = str(parsed_args.get("service_tag") or "").strip()
        logger.info("%s 查阅服务标原文 tag=%s", DESIGNER_TOOL_LOG_PREFIX, service_tag)
        if service_tag in _session_read_tags:
            return f"本会话已查阅过「{service_tag}」，请直接进入 Markdown 生成。"
        _session_read_tags.add(service_tag)
        return read_service_rule_text(service_tag)

    return f"错误：未知工具「{name}」。"


def _prefetch_required_service_rules(required_tags: list[str]) -> list[str]:
    """按任务指定服务标预读规则原文，不依赖 LLM 是否发起 tool 调用。"""
    notes: list[str] = []
    for tag in required_tags:
        if tag in _session_read_tags:
            continue
        result = read_service_rule_text(tag)
        _session_read_tags.add(tag)
        notes.append(result)
        if result.startswith("错误："):
            logger.error("%s 预读规则失败 tag=%s：%s", DESIGNER_TOOL_LOG_PREFIX, tag, result)
        else:
            logger.info("%s 预读规则原文 tag=%s", DESIGNER_TOOL_LOG_PREFIX, tag)
    return notes


def run_designer_agent_loop(
    *,
    messages: list[dict[str, Any]],
    required_service_tags: list[str] | None = None,
    model_env_key: str | None = None,
    fallback_model_env_key: str = "AGENT2_LLM_MODEL",
    temperature: float | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
) -> str:
    """
    两阶段生成：先按需 tool 查阅规则，再单独一轮只输出 Markdown（避免前言污染正文）。

    required_service_tags：任务指定服务标，代码侧强制预读原文后再进入 tool/生成轮。
    """
    reset_designer_tool_session()
    env_key = model_env_key or scenario_gen_model_env()
    temp = scenario_gen_temperature() if temperature is None else temperature
    history = list(messages)

    prefetched_notes = _prefetch_required_service_rules(list(required_service_tags or []))
    tool_notes = list(prefetched_notes)

    for round_index in range(max_tool_rounds):
        assistant_message = chat_completion_assistant_message(
            messages=history,
            model_env_key=env_key,
            fallback_model_env_key=fallback_model_env_key,
            temperature=temp,
            tools=DESIGNER_TOOLS,
            tool_choice="auto",
        )
        if assistant_message is None:
            raise RuntimeError(f"{DESIGNER_TOOL_LOG_PREFIX} LLM 调用失败或未配置")

        history.append(assistant_message)
        tool_calls = assistant_message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            break

        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            tool_name = str(function.get("name") or "").strip()
            raw_args = function.get("arguments")
            if isinstance(raw_args, dict):
                args_text = json.dumps(raw_args, ensure_ascii=False)
            else:
                args_text = str(raw_args or "")
            result = execute_designer_tool(tool_name, args_text)
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or ""),
                    "content": result,
                }
            )
        logger.info(
            "%s 第 %d 轮工具查阅完成，tool_calls=%d",
            DESIGNER_TOOL_LOG_PREFIX,
            round_index + 1,
            len(tool_calls),
        )
        if _session_read_tags:
            break

    tool_notes.extend(
        str(item.get("content") or "").strip()
        for item in history
        if isinstance(item, dict) and item.get("role") == "tool" and str(item.get("content") or "").strip()
    )
    # 只保留含规则原文头的 tool 结果，忽略「本会话已查阅」等占位回复。
    rule_notes: list[str] = []
    seen_tags: set[str] = set()
    for note in tool_notes:
        tag_match = re.search(r"^#\s*服务标：(.+)$", note, flags=re.MULTILINE)
        if not tag_match:
            continue
        tag_key = tag_match.group(1).strip()
        if tag_key in seen_tags:
            continue
        seen_tags.add(tag_key)
        rule_notes.append(note)
    tool_notes = rule_notes
    final_user_parts = [str(messages[-1].get("content") or "").strip()]
    if tool_notes:
        brief_lines = [extract_rule_design_brief(note) for note in tool_notes]
        brief_lines = [line for line in brief_lines if line.strip()]
        if brief_lines:
            final_user_parts.append(
                "设计约束（仅用于选品类与争议因子，禁止写入聊天与 `## 其他说明`）：\n"
                + "\n".join(f"- {line}" for line in brief_lines)
            )
    final_user_parts.append(_FINAL_USER_NUDGE)
    final_messages = [
        messages[0],
        {"role": "user", "content": "\n\n".join(part for part in final_user_parts if part)},
    ]
    final_text = chat_completion(
        messages=final_messages,
        model_env_key=env_key,
        fallback_model_env_key=fallback_model_env_key,
        temperature=temp,
    )
    if final_text is None or not str(final_text).strip():
        raise RuntimeError(f"{DESIGNER_TOOL_LOG_PREFIX} Markdown 生成轮次失败或返回为空")
    return str(final_text).strip()
