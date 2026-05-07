"""
LLM 共用客户端工具。

职责：封装 OpenAI 兼容 chat/completions 调用，统一错误处理与降级策略。
约定：未配置关键环境变量或调用失败时返回 None，由上层 Agent 走规则/模板兜底。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

LOG_PREFIX = "[LLM]"
DEFAULT_MODEL = "mimo-v2.5"
REQUEST_TIMEOUT_SECONDS = 12.0
RETRY_BACKOFF_SECONDS = [0.2, 0.4, 0.8]
logger = logging.getLogger(__name__)


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


# ---------- 端点标准化：统一补全 chat/completions 路径 ----------
def _resolve_endpoint(raw_endpoint: str) -> str:
    """
    将端点规范化到 OpenAI 兼容 chat/completions 地址。
    """
    endpoint = raw_endpoint.strip().rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return f"{endpoint}/chat/completions"
    return endpoint


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


# ---------- 主调用入口：统一重试、日志与降级 ----------
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
        logger.warning("%s 调用跳过：messages 结构非法或为空", LOG_PREFIX)
        return None

    if not isinstance(model_env_key, str) or not model_env_key.strip():
        logger.warning("%s 调用跳过：model_env_key 为空", LOG_PREFIX)
        return None

    raw_endpoint = os.getenv("LLM_API_ENDPOINT", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not raw_endpoint:
        logger.info("%s 调用跳过：未配置 LLM_API_ENDPOINT", LOG_PREFIX)
        return None
    if not api_key:
        logger.info("%s 调用跳过：未配置 LLM_API_KEY", LOG_PREFIX)
        return None

    endpoint = _resolve_endpoint(raw_endpoint=raw_endpoint)
    model = os.getenv(model_env_key, "").strip() or DEFAULT_MODEL
    payload = {"model": model, "messages": messages, "temperature": temperature}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    logger.info("%s 开始请求：endpoint=%s model=%s env_key=%s", LOG_PREFIX, endpoint, model, model_env_key)

    for attempt in range(3):
        attempt_index = attempt + 1
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                response_data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "%s 第%d次调用失败：HTTP状态异常，status=%s，原因=%s",
                LOG_PREFIX,
                attempt_index,
                exc.response.status_code,
                exc,
            )
        except httpx.RequestError as exc:
            logger.error("%s 第%d次调用失败：网络请求异常，原因=%s", LOG_PREFIX, attempt_index, exc)
        except ValueError as exc:
            logger.error("%s 第%d次调用失败：响应JSON解析失败，原因=%s", LOG_PREFIX, attempt_index, exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 第%d次调用失败：未知异常，原因=%s", LOG_PREFIX, attempt_index, exc)
        else:
            content = _extract_content(response_data=response_data)
            if content is not None:
                logger.info("%s 请求成功：已获得响应文本", LOG_PREFIX)
                return content
            logger.error("%s 第%d次调用失败：响应结构不符合约定", LOG_PREFIX, attempt_index)

        if attempt == 2:
            logger.error("%s 调用终止：达到最大重试次数", LOG_PREFIX)
            return None
        time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    logger.error("%s 调用终止：未知错误", LOG_PREFIX)
    return None
