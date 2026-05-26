"""
辅助模式 API 端到端测试。

覆盖：碎片化对话全链路、参数异常、商家配置读写。
"""

from __future__ import annotations

from typing import Any
import os
import sys

import pytest

# ---------- 与仓库根对齐的导入路径 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from schemas import VALID_DISPOSITIONS


# ---------- 依赖守卫：未安装 httpx 时跳过该测试文件 ----------
httpx = pytest.importorskip("httpx")


# ---------- HTTP 客户端：禁用环境代理，避免本地回环被代理链返回 502 ----------
def _e2e_http_client(base_url: str) -> Any:
    """
    构造端到端测试专用 httpx 客户端。

    Windows 等环境下若设置了 HTTP(S)_PROXY，请求 127.0.0.1 可能被错误转发。
    trust_env=False 强制直连被测服务。
    """
    return httpx.Client(base_url=base_url, timeout=15.0, trust_env=False)


# ---------- 请求构造：统一生成 /analyze 所需基础字段 ----------
def _build_analyze_payload(
    dispute_id: str,
    merchant_id: str,
    messages: list[dict[str, Any]],
    image_urls: list[str],
) -> dict[str, Any]:
    """
    构造 analyze 请求体，减少重复字段拼接。
    """
    return {
        "dispute_id": dispute_id,
        "merchant_id": merchant_id,
        "messages": messages,
        "order_id": "ORDER-E2E-001",
        "order_amount": 129.0,
        "buyer_id": "buyer_loyal",
        "image_urls": image_urls,
    }


# ---------- 场景 E1：首次分析应返回完整报告 ----------
def test_e2e_first_analysis_should_return_valid_report(backend_server: str) -> None:
    """
    首次分析（含图片）应返回可用报告并命中策略枚举。
    """
    payload = _build_analyze_payload(
        dispute_id="E2E-001",
        merchant_id="M-E2E-001",
        messages=[{"role": "buyer", "content": "我收到了衣服，袖子有破洞，需要退款"}],
        image_urls=["mock://tear-tag"],
    )

    with _e2e_http_client(base_url=backend_server) as client:
        response = client.post("/analyze", json=payload)

    assert response.status_code == 200
    report = response.json()
    assert report["dispute_id"] == "E2E-001"
    assert report["facts"]["defect_type"] == "破洞"
    assert report["strategy"]["disposition"] in VALID_DISPOSITIONS
    assert report["scripts"]["script"].strip() != ""
    assert report["scripts"]["response_mode"] in {
        "merchant_fault",
        "malicious_risk",
        "neutral_negotiate",
    }


# ---------- 场景 E2：碎片化对话二次分析应感知新增举证 ----------
def test_e2e_fragmented_dialog_should_update_facts_after_second_analyze(backend_server: str) -> None:
    """
    首次无图、二次补图后，应从缺失举证变为识别缺陷类型。
    """
    first_messages = [
        {"role": "buyer", "content": "商品有问题，先帮我处理"},
        {"role": "merchant", "content": "您好，请补充照片方便核实"},
    ]
    second_messages = first_messages + [
        {"role": "buyer", "content": "我又补充三条文字说明，先看下"},
        {"role": "buyer", "content": "破损在袖口附近"},
        {"role": "buyer", "content": "吊牌还在"},
    ]

    with _e2e_http_client(base_url=backend_server) as client:
        first_response = client.post(
            "/analyze",
            json=_build_analyze_payload(
                dispute_id="E2E-002",
                merchant_id="M-E2E-001",
                messages=first_messages,
                image_urls=[],
            ),
        )
        second_response = client.post(
            "/analyze",
            json=_build_analyze_payload(
                dispute_id="E2E-002",
                merchant_id="M-E2E-001",
                messages=second_messages,
                image_urls=["mock://tear-tag"],
            ),
        )

    assert first_response.status_code == 200
    first_report = first_response.json()
    assert first_report["facts"]["defect_type"] is None

    assert second_response.status_code == 200
    second_report = second_response.json()
    assert second_report["facts"]["defect_type"] == "破洞"
    assert "缺少举证图片" not in second_report["facts"]["missing_evidence"]


# ---------- 场景 E3：非法输入应返回 400 ----------
def test_e2e_analyze_should_reject_empty_dispute_id(backend_server: str) -> None:
    """
    dispute_id 为空字符串时，接口应直接返回 400。
    """
    payload = _build_analyze_payload(
        dispute_id="",
        merchant_id="M-E2E-001",
        messages=[{"role": "buyer", "content": "有问题"}],
        image_urls=[],
    )

    with _e2e_http_client(base_url=backend_server) as client:
        response = client.post("/analyze", json=payload)

    assert response.status_code == 400


# ---------- 场景 E4：商家配置读写与模式校验 ----------
def test_e2e_merchant_config_should_support_switch_and_validate_mode(backend_server: str) -> None:
    """
    商家配置应支持默认读取、模式切换、非法模式拦截。
    """
    merchant_id = "M-E2E-002"

    with _e2e_http_client(base_url=backend_server) as client:
        get_response = client.get(f"/merchants/{merchant_id}/config")
        assert get_response.status_code == 200
        assert get_response.json()["mode"] == "assisted"

        update_response = client.put(
            f"/merchants/{merchant_id}/config",
            json={"mode": "intelligent", "auto_threshold": 0.66},
        )
        assert update_response.status_code == 200
        assert update_response.json()["mode"] == "intelligent"

        verify_response = client.get(f"/merchants/{merchant_id}/config")
        assert verify_response.status_code == 200
        verify_payload = verify_response.json()
        assert verify_payload["mode"] == "intelligent"
        assert verify_payload["auto_threshold"] == 0.66

        invalid_response = client.put(
            f"/merchants/{merchant_id}/config",
            json={"mode": "invalid", "auto_threshold": 0.5},
        )
        assert invalid_response.status_code == 400
