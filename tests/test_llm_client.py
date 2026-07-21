"""LLM 共用客户端的可靠性单元测试。"""

from __future__ import annotations

import httpx
import pytest

from backend.tools import llm_client


# ---------- 测试夹具：将共享连接池替换为 MockTransport，避免真实调用外部模型 ----------
@pytest.fixture(autouse=True)
def mock_llm_environment(monkeypatch: pytest.MonkeyPatch):
    """
    配置最小 LLM 环境，并在每个用例结束后关闭替换的连接池。
    """
    monkeypatch.setenv("LLM_API_ENDPOINT", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("TEST_LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_HTTP_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(llm_client.time, "sleep", lambda _: None)
    llm_client._close_http_client()
    yield
    llm_client._close_http_client()


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    """
    将模块级共享客户端绑定到指定 MockTransport。
    """
    client = httpx.Client(transport=handler)
    monkeypatch.setattr(llm_client, "_http_client", client)


# ---------- 连接池：进程内复用客户端，并允许通过环境变量限制并发连接 ----------
def test_shared_client_is_reused_with_configured_connection_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    同一进程内两次获取应返回同一客户端，连接池上限应读取环境变量。
    """
    monkeypatch.setenv("LLM_HTTP_MAX_CONNECTIONS", "7")
    monkeypatch.setenv("LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "3")

    limits = llm_client._build_httpx_limits()
    first_client = llm_client._get_http_client()
    second_client = llm_client._get_http_client()

    assert limits.max_connections == 7
    assert limits.max_keepalive_connections == 3
    assert first_client is second_client


# ---------- 非流式重试：只重试服务端瞬时失败，不重试确定性客户端错误 ----------
def test_chat_completion_retries_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    5xx 后应重试一次，并返回第二次的正常文本。
    """
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "完成"}}]})

    _install_transport(monkeypatch, httpx.MockTransport(handler))

    result = llm_client.chat_completion(
        messages=[{"role": "user", "content": "测试"}],
        model_env_key="TEST_LLM_MODEL",
    )

    assert result == "完成"
    assert request_count == 2


def test_chat_completion_retries_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    连接异常具有瞬时特征时，应重试并返回后续成功响应。
    """
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            raise httpx.ConnectError("临时网络故障", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "网络恢复"}}]})

    _install_transport(monkeypatch, httpx.MockTransport(handler))

    result = llm_client.chat_completion(
        messages=[{"role": "user", "content": "测试"}],
        model_env_key="TEST_LLM_MODEL",
    )

    assert result == "网络恢复"
    assert request_count == 2


def test_chat_completion_does_not_retry_deterministic_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    参数或鉴权类 4xx 不应向上游重复发送相同请求。
    """
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(400, json={"error": "invalid request"})

    _install_transport(monkeypatch, httpx.MockTransport(handler))

    result = llm_client.chat_completion(
        messages=[{"role": "user", "content": "测试"}],
        model_env_key="TEST_LLM_MODEL",
    )

    assert result is None
    assert request_count == 1


def test_chat_completion_respects_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    429 响应包含 Retry-After 时，应以该值作为下一次重试等待时间。
    """
    request_count = 0
    delays: list[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", delays.append)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"Retry-After": "2.5"}, json={"error": "rate limited"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "稍后成功"}}]})

    _install_transport(monkeypatch, httpx.MockTransport(handler))

    result = llm_client.chat_completion(
        messages=[{"role": "user", "content": "测试"}],
        model_env_key="TEST_LLM_MODEL",
    )

    assert result == "稍后成功"
    assert request_count == 2
    assert delays == [2.5]


def test_tool_completion_uses_shared_retry_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    tool-call 入口应复用同一套 5xx 重试与响应校验逻辑。
    """
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(502, json={"error": "bad gateway"})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "工具计划"}}]})

    _install_transport(monkeypatch, httpx.MockTransport(handler))

    result = llm_client.chat_completion_assistant_message(
        messages=[{"role": "user", "content": "测试"}],
        model_env_key="TEST_LLM_MODEL",
    )

    assert result == {"role": "assistant", "content": "工具计划"}
    assert request_count == 2


# ---------- 流式幂等：已向调用方输出内容后断流，禁止从头重试 ----------
class _InterruptedStream(httpx.SyncByteStream):
    """
    先输出一个合法 SSE 分片，再模拟上游读取中断。
    """

    def __iter__(self):
        yield 'data: {"choices":[{"delta":{"content":"首段"}}]}\n\n'.encode()
        raise httpx.ReadError("上游连接中断")

    def close(self) -> None:
        """
        MockTransport 关闭流时无需额外清理。
        """


def test_stream_does_not_replay_after_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    已回调首段 delta 后读取失败时不得发起第二次上游请求或重复回调文本。
    """
    request_count = 0
    deltas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, stream=_InterruptedStream())

    _install_transport(monkeypatch, httpx.MockTransport(handler))

    result = llm_client.chat_completion(
        messages=[{"role": "user", "content": "测试"}],
        model_env_key="TEST_LLM_MODEL",
        stream_delta_callback=deltas.append,
    )

    assert result is None
    assert deltas == ["首段"]
    assert request_count == 1
