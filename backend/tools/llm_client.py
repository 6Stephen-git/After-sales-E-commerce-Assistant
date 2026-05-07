"""
LLM 共用客户端工具。

职责：封装 OpenAI 兼容 chat/completions 调用，统一错误处理与降级策略。
约定：未配置端点或调用失败时返回 None，由上层 Agent 走规则/模板兜底。
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

LOG_PREFIX = "[LLM]"
DEFAULT_MODEL = "mimo-v2.5"
REQUEST_TIMEOUT_SECONDS = 12.0
RETRY_BACKOFF_SECONDS = [0.2, 0.4, 0.8]


# ---------- 基础校验：消息列表结构 ----------
def _is_valid_messages(messages: list[dict[str, Any]]) -> bool:
    """
    校验 messages 是否为可发送到 chat/completions 的基础结构。

    要求每个元素都包含 role 与 content，且 role/content 为非空字符串。
    """
    if not isinstance(messages, list) or not messages:
        return False

    for item in messages:
        if not isinstance(item, dict):
            return False
        role = item.get("role")
        content = item.get("content")
        if not isinstance(role, str) or not role.strip():
            return False
        if not isinstance(content, str) or not content.strip():
            return False
    return True


# ---------- 响应解析：提取首个 choices 文本 ----------
def _extract_content(response_data: dict[str, Any]) -> str | None:
    """
    从 OpenAI 兼容响应中提取文本内容。

    目标路径：choices[0].message.content。
    若结构不符合约定，返回 None。
    """
    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None

    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


# ---------- 主调用入口：统一重试与降级 ----------
def chat_completion(
    messages: list[dict[str, Any]],
    model_env_key: str,
    temperature: float = 0.7,
) -> str | None:
    """
    调用 LLM chat/completions 接口并返回文本结果。

    参数:
        messages: OpenAI 兼容消息数组。
        model_env_key: 对应模型环境变量名（如 AGENT2_LLM_MODEL）。
        temperature: 采样温度。

    返回:
        成功返回文本；未配置或调用失败返回 None。
    """
    if not _is_valid_messages(messages=messages):
        print(f"{LOG_PREFIX} 调用跳过：messages 结构非法或为空")
        return None

    endpoint = os.getenv("LLM_API_ENDPOINT", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not endpoint:
        print(f"{LOG_PREFIX} 调用跳过：未配置 LLM_API_ENDPOINT")
        return None

    model = os.getenv(model_env_key, "").strip() or DEFAULT_MODEL
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    print(f"{LOG_PREFIX} 开始请求：model={model} env_key={model_env_key}")

    for attempt in range(3):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                response_data = response.json()
        except Exception as exc:  # noqa: BLE001
            print(f"{LOG_PREFIX} 第{attempt + 1}次调用失败：{exc}")
            if attempt == 2:
                print(f"{LOG_PREFIX} 调用终止：达到最大重试次数")
                return None
            time.sleep(RETRY_BACKOFF_SECONDS[attempt])
            continue

        content = _extract_content(response_data=response_data)
        if content is not None:
            print(f"{LOG_PREFIX} 请求成功：已获得响应文本")
            return content

        print(f"{LOG_PREFIX} 第{attempt + 1}次调用失败：响应结构不符合约定")
        if attempt == 2:
            print(f"{LOG_PREFIX} 调用终止：响应结构持续异常")
            return None
        time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    print(f"{LOG_PREFIX} 调用终止：未知错误")
    return None
