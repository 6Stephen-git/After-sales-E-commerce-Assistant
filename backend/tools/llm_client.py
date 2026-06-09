"""
LLM 共用客户端工具。

职责：封装 OpenAI 兼容 chat/completions 调用，统一错误处理与降级策略。
约定：未配置关键环境变量或调用失败时返回 None，由上层 Agent 走规则/模板兜底。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

import httpx

LOG_PREFIX = "[LLM]"
DEFAULT_MODEL = "mimo-v2.5"
# 默认：连接 15s、读取 90s（LLM 首 token 常超过 12s；过短会误报 read timed out）
_DEFAULT_CONNECT = 15.0
_DEFAULT_READ = 90.0
RETRY_BACKOFF_SECONDS = [0.2, 0.4, 0.8]
logger = logging.getLogger(__name__)


# ---------- 超时配置：区分连接与读响应，避免弱网/慢模型误判为失败 ----------
def _parse_timeout_seconds(raw: str, fallback: float) -> float:
    """
    将环境变量中的超时字符串解析为秒数；非法或空则返回 fallback。
    """
    text = (raw or "").strip()
    if not text:
        return fallback
    try:
        value = float(text)
        return value if value > 0 else fallback
    except ValueError:
        logger.warning("%s 环境变量超时值非法，已使用默认值 fallback=%s", LOG_PREFIX, fallback)
        return fallback


def _build_httpx_timeout() -> httpx.Timeout:
    """
    构建 httpx 超时：连接、读-body 分离；可通过 LLM_HTTP_CONNECT_TIMEOUT / LLM_HTTP_READ_TIMEOUT 调整。
    """
    connect = _parse_timeout_seconds(os.getenv("LLM_HTTP_CONNECT_TIMEOUT", ""), _DEFAULT_CONNECT)
    read = _parse_timeout_seconds(os.getenv("LLM_HTTP_READ_TIMEOUT", ""), _DEFAULT_READ)
    return httpx.Timeout(connect=connect, read=read, write=connect, pool=connect)


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


def _extract_stream_delta(response_data: dict[str, Any]) -> str:
    """
    从流式分片中提取增量文本，提取失败返回空字符串。
    """
    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return ""

    delta = first_choice.get("delta")
    if not isinstance(delta, dict):
        return ""

    content = delta.get("content")
    if isinstance(content, str):
        return content
    return ""


# ---------- 主调用入口：统一重试、日志与降级 ----------
def chat_completion(
    messages: list[dict[str, Any]],
    model_env_key: str,
    temperature: float = 0.7,
    fallback_model_env_key: str | None = None,
    stream_delta_callback: Callable[[str], None] | None = None,
) -> str | None:
    """
    调用 LLM chat/completions 接口并返回文本结果。

    参数:
        messages: OpenAI 兼容消息数组。
        model_env_key: 对应模型环境变量名（如 AGENT2_LLM_MODEL）。
        temperature: 采样温度。
        fallback_model_env_key: 主模型未配置时的回退模型环境变量名。
        stream_delta_callback: 流式回调；传入后将使用 SSE 分片并在每个 delta 回调。

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
    primary_model = os.getenv(model_env_key, "").strip()
    fallback_model = os.getenv(fallback_model_env_key or "", "").strip() if fallback_model_env_key else ""
    model = primary_model or fallback_model or DEFAULT_MODEL
    stream_enabled = stream_delta_callback is not None
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if stream_enabled:
        payload["stream"] = True
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    timeout = _build_httpx_timeout()
    logger.info(
        "%s 开始请求：endpoint=%s model=%s env_key=%s fallback_env_key=%s stream=%s connect_timeout=%s read_timeout=%s",
        LOG_PREFIX,
        endpoint,
        model,
        model_env_key,
        fallback_model_env_key,
        stream_enabled,
        timeout.connect,
        timeout.read,
    )

    for attempt in range(3):
        attempt_index = attempt + 1
        try:
            with httpx.Client(timeout=timeout) as client:
                if stream_enabled:
                    with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                        response.raise_for_status()
                        chunks: list[str] = []
                        for line in response.iter_lines():
                            if not line:
                                continue
                            normalized_line = line.strip()
                            if not normalized_line.startswith("data:"):
                                continue
                            data_text = normalized_line[5:].strip()
                            if data_text == "[DONE]":
                                break
                            try:
                                response_data = json.loads(data_text)
                            except json.JSONDecodeError:
                                continue
                            delta_text = _extract_stream_delta(response_data=response_data)
                            if not delta_text:
                                continue
                            chunks.append(delta_text)
                            stream_delta_callback(delta_text)
                        if not chunks:
                            raise ValueError("流式响应未返回可用文本增量")
                        content = "".join(chunks).strip()
                        if content:
                            logger.info("%s 请求成功：已获得流式响应文本", LOG_PREFIX)
                            return content
                        raise ValueError("流式响应文本为空")
                else:
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
            if stream_enabled:
                logger.error("%s 第%d次调用失败：流式响应结构不符合约定", LOG_PREFIX, attempt_index)
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


def _is_valid_tool_chat_messages(messages: list[dict[str, Any]]) -> bool:
    """校验含 tool 角色的多轮消息结构。"""
    if not isinstance(messages, list) or not messages:
        return False

    for item in messages:
        if not isinstance(item, dict):
            return False
        role = item.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            return False
        if role in {"system", "user"}:
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                return False
        elif role == "assistant":
            content = item.get("content")
            tool_calls = item.get("tool_calls")
            has_content = isinstance(content, str) and bool(content.strip())
            has_tools = isinstance(tool_calls, list) and bool(tool_calls)
            if not has_content and not has_tools:
                return False
        elif role == "tool":
            tool_call_id = item.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                return False
            if not isinstance(item.get("content"), str):
                return False
    return True


def _extract_assistant_message(response_data: dict[str, Any]) -> dict[str, Any] | None:
    """从 chat/completions 响应中提取 assistant message（含 tool_calls）。"""
    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None

    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None
    if str(message.get("role") or "") != "assistant":
        return None
    return message


def chat_completion_assistant_message(
    messages: list[dict[str, Any]],
    model_env_key: str,
    temperature: float = 0.7,
    fallback_model_env_key: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
) -> dict[str, Any] | None:
    """
    调用 chat/completions 并返回完整 assistant message（支持 tools）。

    用于 Agent 多轮 tool 循环；失败返回 None。
    """
    if not _is_valid_tool_chat_messages(messages):
        logger.warning("%s tool 调用跳过：messages 结构非法或为空", LOG_PREFIX)
        return None

    if not isinstance(model_env_key, str) or not model_env_key.strip():
        logger.warning("%s tool 调用跳过：model_env_key 为空", LOG_PREFIX)
        return None

    raw_endpoint = os.getenv("LLM_API_ENDPOINT", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not raw_endpoint or not api_key:
        logger.info("%s tool 调用跳过：LLM 未配置", LOG_PREFIX)
        return None

    endpoint = _resolve_endpoint(raw_endpoint=raw_endpoint)
    primary_model = os.getenv(model_env_key, "").strip()
    fallback_model = os.getenv(fallback_model_env_key or "", "").strip() if fallback_model_env_key else ""
    model = primary_model or fallback_model or DEFAULT_MODEL
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    timeout = _build_httpx_timeout()
    logger.info(
        "%s 开始 tool 请求：model=%s tools=%s",
        LOG_PREFIX,
        model,
        len(tools or []),
    )

    for attempt in range(3):
        attempt_index = attempt + 1
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                response_data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "%s tool 第%d次失败：HTTP %s",
                LOG_PREFIX,
                attempt_index,
                exc.response.status_code,
            )
        except httpx.RequestError as exc:
            logger.error("%s tool 第%d次失败：网络异常 %s", LOG_PREFIX, attempt_index, exc)
        except ValueError as exc:
            logger.error("%s tool 第%d次失败：JSON 解析 %s", LOG_PREFIX, attempt_index, exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s tool 第%d次失败：%s", LOG_PREFIX, attempt_index, exc)
        else:
            message = _extract_assistant_message(response_data=response_data)
            if message is not None:
                logger.info("%s tool 请求成功", LOG_PREFIX)
                return message
            logger.error("%s tool 第%d次失败：响应无 assistant message", LOG_PREFIX, attempt_index)

        if attempt == 2:
            return None
        time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    return None
