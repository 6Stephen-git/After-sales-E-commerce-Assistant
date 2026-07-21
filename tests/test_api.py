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
from backend.db.connection import get_engine  # noqa: E402
from backend.services.analysis_job_service import append_event, mark_succeeded  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


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


# ---------- /analyze：创建异步任务并返回可恢复的 job_id ----------
def test_analyze_should_create_analysis_job(api_client, monkeypatch):
    """/analyze 应入队一次并返回可供 SSE 订阅的任务标识。"""
    queued_job_ids: list[str] = []
    monkeypatch.setattr(
        "backend.routers.analyze.run_analysis_job.delay",
        lambda job_id: queued_job_ids.append(job_id),
    )
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
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["reused"] is False
    assert payload["job_id"]
    assert queued_job_ids == [payload["job_id"]]

    status_response = api_client.get(f"/analyze/{payload['job_id']}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "queued"


# ---------- queued 补偿：已落库但尚未领取的同一任务应在重试请求时再次入队 ----------
def test_analyze_should_requeue_existing_queued_job(api_client, monkeypatch):
    """同材料第二次 POST 复用 job_id，同时补偿一次 delay 调用。"""
    queued_job_ids: list[str] = []
    monkeypatch.setattr(
        "backend.routers.analyze.run_analysis_job.delay",
        lambda job_id: queued_job_ids.append(job_id),
    )
    request_body = {
        "dispute_id": "D-API-REQUEUE-001",
        "merchant_id": "M-API-REQUEUE-001",
        "messages": [{"role": "buyer", "content": "申请退款"}],
    }

    first = api_client.post("/analyze", json=request_body)
    second = api_client.post("/analyze", json=request_body)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["reused"] is True
    assert second.json()["job_id"] == first.json()["job_id"]
    assert queued_job_ids == [first.json()["job_id"], first.json()["job_id"]]


# ---------- SSE 补发：按 Last-Event-ID 仅推送尚未消费的持久化事件 ----------
def test_analysis_events_should_replay_only_missing_sequences(api_client, monkeypatch):
    """重连订阅应忽略已消费事件，并返回剩余终态事件。"""
    monkeypatch.setattr("backend.routers.analyze.run_analysis_job.delay", lambda _job_id: None)
    request_body = {
        "dispute_id": "D-API-SSE-001",
        "merchant_id": "M-API-SSE-001",
        "messages": [{"role": "buyer", "content": "申请退款"}],
    }
    created = api_client.post("/analyze", json=request_body)
    job_id = created.json()["job_id"]
    with Session(get_engine()) as session:
        event = append_event(
            session,
            job_id=job_id,
            event_type="final_report",
            payload={"report": {"dispute_id": "D-API-SSE-001"}},
        )
        last_sequence = event.sequence
        mark_succeeded(session, job_id=job_id, report={"dispute_id": "D-API-SSE-001"})

    response = api_client.get(
        f"/analyze/{job_id}/events",
        headers={"Last-Event-ID": str(last_sequence)},
    )
    assert response.status_code == 200
    assert "id: 2" in response.text
    assert "event: job_completed" in response.text
    assert "event: final_report" not in response.text


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
        json={"mode": "assisted", "auto_threshold": 0.65},
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["mode"] == "assisted"
    assert update_payload["auto_threshold"] == 0.65

    verify_response = api_client.get(f"/merchants/{merchant_id}/config")
    assert verify_response.status_code == 200
    verify_payload = verify_response.json()
    assert verify_payload["mode"] == "assisted"
    assert verify_payload["auto_threshold"] == 0.65


# ---------- 参数校验：空 merchant_id 与非法 mode ----------
def test_merchant_config_should_validate_mode(api_client):
    """非法 mode 应返回 400。"""
    response = api_client.put(
        "/merchants/M-API-003/config",
        json={"mode": "auto", "auto_threshold": 0.5},
    )
    assert response.status_code == 400


# ---------- /merchants/{id}/buyers/{hash}：画像 upsert/get/delete ----------
def test_buyer_profile_should_support_upsert_get_delete(api_client):
    """买家画像接口应支持新增更新、查询和删除。"""
    merchant_id = "M-API-BUYER-001"
    buyer_hash = "buyer_hash_001"
    request_body = {
        "purchase_count": 12,
        "dispute_count": 1,
        "dispute_rate": 0.08,
        "avg_order_value": 156.5,
        "return_rate": 0.1,
        "malicious_flags": 0,
        "credit_level": "high",
    }

    put_response = api_client.put(f"/merchants/{merchant_id}/buyers/{buyer_hash}", json=request_body)
    assert put_response.status_code == 200
    put_payload = put_response.json()
    assert put_payload["buyer_id"] == buyer_hash
    assert put_payload["purchase_count"] == 12
    assert put_payload["credit_level"] == "high"

    get_response = api_client.get(f"/merchants/{merchant_id}/buyers/{buyer_hash}")
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["buyer_id"] == buyer_hash
    assert get_payload["dispute_rate"] == 0.08

    delete_response = api_client.delete(f"/merchants/{merchant_id}/buyers/{buyer_hash}")
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "买家画像删除成功"

    verify_response = api_client.get(f"/merchants/{merchant_id}/buyers/{buyer_hash}")
    assert verify_response.status_code == 404
