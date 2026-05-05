"""
后端 API 测试
覆盖：健康检查、/analyze 主流程、商家配置查询与更新
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient


# ---------- 与仓库根对齐的导入路径 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


# ---------- 测试库配置：使用独立 sqlite 文件避免依赖本地 MySQL ----------
TEST_DB_PATH = os.path.join(ROOT_DIR, "tests", "tmp_api.sqlite3")
if os.path.exists(TEST_DB_PATH):
    os.remove(TEST_DB_PATH)
os.environ["DB_URL"] = f"sqlite+pysqlite:///{TEST_DB_PATH.replace(os.sep, '/')}"

from backend.main import app  # noqa: E402


# ---------- 测试客户端：确保 startup 事件触发建表 ----------
@pytest.fixture(scope="module")
def api_client():
    """模块级 TestClient，自动触发 startup/shutdown。"""
    with TestClient(app) as test_client:
        yield test_client


# ---------- /health：服务可用性 ----------
def test_health_should_return_ok_status(api_client):
    """健康检查应返回状态与版本。"""
    response = api_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "version" in payload


# ---------- /analyze：辅助控制器链路 ----------
def test_analyze_should_return_analysis_report(api_client):
    """/analyze 应返回完整分析报告结构。"""
    request_body = {
        "dispute_id": "D-API-001",
        "merchant_id": "M-API-001",
        "messages": [
            {"role": "buyer", "content": "我收到衣服了，但是有破洞，想退款"},
            {"role": "merchant", "content": "您好，我们先核实一下情况"},
        ],
        "order_id": "ORDER-API-001",
        "order_amount": 129.0,
        "buyer_id": "buyer_api_001",
        "image_urls": ["mock://tear-tag"],
    }
    response = api_client.post("/analyze", json=request_body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["dispute_id"] == "D-API-001"
    assert "facts" in payload
    assert "strategy" in payload
    assert "scripts" in payload


# ---------- /merchants/{id}/config：默认创建、更新回读 ----------
def test_merchant_config_should_support_get_and_put(api_client):
    """商家配置应支持默认读取与更新。"""
    merchant_id = "M-API-002"

    get_response = api_client.get(f"/merchants/{merchant_id}/config")
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["merchant_id"] == merchant_id
    assert get_payload["mode"] == "assisted"

    update_response = api_client.put(
        f"/merchants/{merchant_id}/config",
        json={"mode": "intelligent", "auto_threshold": 0.65},
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["mode"] == "intelligent"
    assert update_payload["auto_threshold"] == 0.65

    verify_response = api_client.get(f"/merchants/{merchant_id}/config")
    assert verify_response.status_code == 200
    verify_payload = verify_response.json()
    assert verify_payload["mode"] == "intelligent"
    assert verify_payload["auto_threshold"] == 0.65


# ---------- 参数校验：空 merchant_id 与非法 mode ----------
def test_merchant_config_should_validate_mode(api_client):
    """非法 mode 应返回 400。"""
    response = api_client.put(
        "/merchants/M-API-003/config",
        json={"mode": "auto", "auto_threshold": 0.5},
    )
    assert response.status_code == 400
