"""
LLM 共用客户端工具。

职责：封装 OpenAI 兼容 chat/completions 调用，统一错误处理与降级策略。
约定：未配置关键环境变量或调用失败时返回 None，由上层 Agent 走规则/模板兜底。
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from atexit import register
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, TypeVar

import httpx

LOG_PREFIX = "[LLM]"
DEFAULT_MODEL = "deepseek-v4-flash"
# 默认：连接 15s、读取 90s（LLM 首 token 常超过 12s；过短会误报 read timed out）
_DEFAULT_CONNECT = 15.0
_DEFAULT_READ = 90.0
_DEFAULT_POOL_TIMEOUT = 15.0
_DEFAULT_MAX_CONNECTIONS = 20
_DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 10
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_BASE_SECONDS = 0.5
_DEFAULT_RETRY_MAX_SECONDS = 8.0
logger = logging.getLogger(__name__)
_http_client: httpx.Client | None = None
ResultType = TypeVar("ResultType")


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


def _parse_positive_int(raw: str, fallback: int) -> int:
    """
    解析正整数配置；非法值回退默认值并记录原因。
    """
    text = (raw or "").strip()
    if not text:
        return fallback
    try:
        value = int(text)
        return value if value > 0 else fallback
    except ValueError:
        logger.warning("%s 环境变量整数值非法，已使用默认值 fallback=%s", LOG_PREFIX, fallback)
        return fallback


def _build_httpx_timeout() -> httpx.Timeout:
    """
    构建 httpx 超时：连接、读-body、连接池等待分离。

    可通过 LLM_HTTP_CONNECT_TIMEOUT、LLM_HTTP_READ_TIMEOUT 与
    LLM_HTTP_POOL_TIMEOUT 调整，避免把连接池耗尽误判为上游模型慢。
    """
    connect = _parse_timeout_seconds(os.getenv("LLM_HTTP_CONNECT_TIMEOUT", ""), _DEFAULT_CONNECT)
    read = _parse_timeout_seconds(os.getenv("LLM_HTTP_READ_TIMEOUT", ""), _DEFAULT_READ)
    pool = _parse_timeout_seconds(os.getenv("LLM_HTTP_POOL_TIMEOUT", ""), _DEFAULT_POOL_TIMEOUT)
    return httpx.Timeout(connect=connect, read=read, write=connect, pool=pool)


def _build_httpx_limits() -> httpx.Limits:
    """
    构建连接池上限，限制单个 Celery worker 对上游 LLM 的并发连接数。
    """
    max_connections = _parse_positive_int(
        os.getenv("LLM_HTTP_MAX_CONNECTIONS", ""),
        _DEFAULT_MAX_CONNECTIONS,
    )
    max_keepalive = min(
        _parse_positive_int(
            os.getenv("LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", ""),
            _DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        ),
        max_connections,
    )
    return httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive,
    )


# ---------- HTTP 客户端生命周期：同一进程复用连接池，退出时释放空闲连接 ----------
def _get_http_client() -> httpx.Client:
    """
    返回进程内共享的同步 HTTP 客户端。

    Celery 每个 worker 进程各自维护连接池；不跨进程共享 socket，
    既复用 keep-alive 连接，也避免在 fork 后错误复用父进程连接。
    """
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.Client(timeout=_build_httpx_timeout(), limits=_build_httpx_limits())
    return _http_client


def _close_http_client() -> None:
    """
    在解释器退出时关闭共享客户端，释放连接池中的空闲连接。
    """
    global _http_client
    if _http_client is not None:
        _http_client.close()
        _http_client = None


register(_close_http_client)


# ---------- 失败分类：只重试具有瞬时特征的网络、限流与服务端异常 ----------
def _is_retryable_exception(exc: Exception) -> bool:
    """
    判断调用失败是否值得重试。

    4xx 请求错误通常由参数、鉴权或权限导致，除 429 限流外不重试；
    网络层异常与 5xx 则可能随时间恢复。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.RequestError)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """
    解析服务端 Retry-After 秒数或 HTTP 日期；非法、过期值返回 None。
    """
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _retry_delay_seconds(exc: Exception, attempt_index: int) -> float:
    """
    计算下一次重试等待时间。

    429 优先遵守 Retry-After；其他可重试失败使用带随机抖动的指数退避，
    防止多个 worker 同时重试造成上游雪崩。
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry_after = _retry_after_seconds(exc.response)
        if retry_after is not None:
            return retry_after
    base = _parse_timeout_seconds(
        os.getenv("LLM_HTTP_RETRY_BASE_SECONDS", ""),
        _DEFAULT_RETRY_BASE_SECONDS,
    )
    maximum = _parse_timeout_seconds(
        os.getenv("LLM_HTTP_RETRY_MAX_SECONDS", ""),
        _DEFAULT_RETRY_MAX_SECONDS,
    )
    delay = min(maximum, base * (2 ** (attempt_index - 1)))
    return delay + random.uniform(0, delay * 0.2)


def _max_attempts() -> int:
    """
    获取单次 LLM 调用最大尝试次数，包含首次调用。
    """
    return _parse_positive_int(os.getenv("LLM_HTTP_MAX_ATTEMPTS", ""), _DEFAULT_MAX_ATTEMPTS)


def _run_with_retries(
    operation: Callable[[], ResultType],
    *,
    request_name: str,
    retry_allowed: Callable[[], bool] | None = None,
) -> ResultType | None:
    """
    执行单次上游操作并按失败类型有限重试。

    retry_allowed 用于流式场景：已向调用方发出任何 delta 后返回 False，
    从而绝不重放请求并造成重复文本。
    """
    attempts = _max_attempts()
    for attempt_index in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            can_retry = (
                _is_retryable_exception(exc)
                and attempt_index < attempts
                and (retry_allowed is None or retry_allowed())
            )
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if not can_retry:
                logger.error(
                    "%s %s调用失败且不再重试：attempt=%s/%s status=%s reason=%s",
                    LOG_PREFIX,
                    request_name,
                    attempt_index,
                    attempts,
                    status_code,
                    exc,
                )
                return None
            delay = _retry_delay_seconds(exc, attempt_index)
            logger.warning(
                "%s %s调用失败，准备重试：attempt=%s/%s status=%s wait_seconds=%.3f reason=%s",
                LOG_PREFIX,
                request_name,
                attempt_index,
                attempts,
                status_code,
                delay,
                exc,
            )
            time.sleep(delay)
    return None


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
    # 裸 Base URL（如 https://api.deepseek.com）自动补全路径
    return f"{endpoint}/chat/completions"


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
    logger.info(
        "%s 开始请求：endpoint=%s model=%s env_key=%s fallback_env_key=%s stream=%s",
        LOG_PREFIX,
        endpoint,
        model,
        model_env_key,
        fallback_model_env_key,
        stream_enabled,
    )

    if not stream_enabled:
        def request_text() -> str:
            """
            执行非流式请求并校验 OpenAI 兼容响应结构。
            """
            response = _get_http_client().post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            content = _extract_content(response.json())
            if content is None:
                raise ValueError("响应结构不符合 OpenAI chat/completions 约定")
            return content

        content = _run_with_retries(request_text, request_name="非流式")
        if content is not None:
            logger.info("%s 请求成功：已获得响应文本", LOG_PREFIX)
        return content

    stream_has_emitted_delta = False

    def request_stream() -> str:
        """
        执行流式请求；一旦向调用方回调 delta，禁止后续重放本次生成。
        """
        nonlocal stream_has_emitted_delta
        chunks: list[str] = []
        with _get_http_client().stream("POST", endpoint, headers=headers, json=payload) as response:
            response.raise_for_status()
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
                stream_has_emitted_delta = True
        content = "".join(chunks).strip()
        if not content:
            raise ValueError("流式响应未返回可用文本增量")
        return content

    content = _run_with_retries(
        request_stream,
        request_name="流式",
        retry_allowed=lambda: not stream_has_emitted_delta,
    )
    if content is not None:
        logger.info("%s 请求成功：已获得流式响应文本", LOG_PREFIX)
    return content


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
    logger.info(
        "%s 开始 tool 请求：model=%s tools=%s",
        LOG_PREFIX,
        model,
        len(tools or []),
    )

    def request_assistant_message() -> dict[str, Any]:
        """
        执行带工具定义的请求，并验证响应中存在 assistant message。
        """
        response = _get_http_client().post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        message = _extract_assistant_message(response.json())
        if message is None:
            raise ValueError("响应无 assistant message")
        return message

    message = _run_with_retries(request_assistant_message, request_name="tool")
    if message is not None:
        logger.info("%s tool 请求成功", LOG_PREFIX)
    return message
