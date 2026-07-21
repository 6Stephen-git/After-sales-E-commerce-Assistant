"""
纠纷 Redis 缓存核心测试：指纹、材料合并、B/C 层与降级。

原则：每类行为保留一条代表路径。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.cache.fingerprint import compute_fp_agent1, compute_fp_report, merchant_cache_scope
from backend.cache.materials_store import clear_all_cache, merge_materials
from backend.cache import redis_client
from backend.cache.redis_client import get_redis, reset_redis_client
from backend.cache.result_cache import _facts_key, get_cached_facts, get_cached_report, save_facts, save_report
from backend.cache.tool_cache import get_cached_cases, save_cases
from schemas import AnalysisReport, EVIDENCE_LOW, FactOutput, ScriptOutput, StrategyOutput


@pytest.fixture(autouse=True)
def reset_cache_state() -> None:
    """每个用例前清理本地缓存并重置 Redis 客户端单例。"""
    clear_all_cache()
    reset_redis_client()
    yield
    clear_all_cache()
    reset_redis_client()


def test_fingerprint_stability_and_sensitivity() -> None:
    """相同材料指纹稳定；聊天/金额变化应影响对应层指纹。"""
    base = {
        "chat_history": [{"role": "buyer", "content": "有质量问题"}],
        "image_urls": ["mock://a"],
        "buyer_text": "退款",
        "order_id": "O1",
        "order_amount": 99.0,
        "buyer_id": "b1",
        "merchant_id": "m1",
    }
    assert compute_fp_agent1(base) == compute_fp_agent1(dict(base))
    assert compute_fp_report(base) == compute_fp_report(dict(base))

    changed_chat = dict(base)
    changed_chat["chat_history"] = [{"role": "buyer", "content": "新消息"}]
    assert compute_fp_agent1(changed_chat) != compute_fp_agent1(base)
    assert compute_fp_report(changed_chat) != compute_fp_report(base)

    changed_amount = dict(base)
    changed_amount["order_amount"] = 199.0
    assert compute_fp_agent1(changed_amount) == compute_fp_agent1(base)
    assert compute_fp_report(changed_amount) != compute_fp_report(base)


def test_merge_materials_incremental_and_snapshot() -> None:
    """默认增量追加 chat/image；snapshot 模式覆盖旧会话。"""
    first = merge_materials(
        "merchant-1",
        "D-001",
        {
            "chat_history": [{"role": "buyer", "content": "第一条"}],
            "image_urls": ["mock://a"],
        },
    )
    second = merge_materials(
        "merchant-1",
        "D-001",
        {
            "chat_history": [{"role": "buyer", "content": "第二条"}],
            "image_urls": ["mock://b"],
            "materials_snapshot": False,
        },
    )
    assert len(first["chat_history"]) == 1
    assert len(second["chat_history"]) == 2
    assert len(second["image_urls"]) == 2

    merge_materials(
        "merchant-1",
        "D-snap",
        {
            "chat_history": [{"role": "buyer", "content": "手机有划痕"}],
            "image_urls": ["mock://phone"],
        },
    )
    merged = merge_materials(
        "merchant-1",
        "D-snap",
        {
            "chat_history": [{"role": "buyer", "content": "香蕉坏了"}],
            "image_urls": ["mock://banana"],
            "materials_snapshot": True,
        },
    )
    assert len(merged["chat_history"]) == 1
    assert merged["chat_history"][0]["content"] == "香蕉坏了"
    assert merged["image_urls"] == ["mock://banana"]


def test_result_cache_should_hit_after_save() -> None:
    """写入 B/C 层后，相同材料应命中缓存。"""
    materials = {
        "chat_history": [{"role": "buyer", "content": "测试"}],
        "image_urls": [],
        "buyer_text": "测试",
        "order_id": "O2",
        "order_amount": 50.0,
        "buyer_id": "b2",
        "merchant_id": "m2",
    }
    facts = FactOutput(goods_received=True, defect_type="未知", evidence_quality=EVIDENCE_LOW, confidence=0.5)
    report = AnalysisReport(
        dispute_id="D-002",
        facts=facts,
        strategy=StrategyOutput(disposition="defend", confidence=0.5),
        scripts=ScriptOutput(script="您好，这单我在跟进。", response_mode="neutral_negotiate"),
    )

    mock_client = MagicMock()
    storage: dict[str, str] = {}

    def fake_setex(key: str, _ttl: int, value: str) -> None:
        storage[key] = value

    def fake_get(key: str) -> str | None:
        return storage.get(key)

    mock_client.setex.side_effect = fake_setex
    mock_client.get.side_effect = fake_get
    mock_client.ping.return_value = True

    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            save_facts("m2", "D-002", materials, facts)
            save_report("m2", "D-002", materials, report)
            cached_facts = get_cached_facts("m2", "D-002", materials)
            cached_report = get_cached_report("m2", "D-002", materials)

    assert cached_facts is not None
    assert cached_facts.defect_type == "未知"
    assert cached_report is not None
    assert cached_report.strategy.disposition == "defend"


def test_result_cache_should_miss_when_redis_read_fails() -> None:
    """Redis 读异常时不应抛错，应返回 None。"""
    materials = {
        "chat_history": [],
        "image_urls": [],
        "buyer_text": "",
        "order_id": "",
        "order_amount": 0.0,
        "buyer_id": "",
        "merchant_id": "",
    }
    mock_client = MagicMock()
    mock_client.get.side_effect = RuntimeError("连接失败")
    mock_client.ping.return_value = True

    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            assert get_cached_report("merchant-3", "D-003", materials) is None


def test_result_cache_keys_are_tenant_scoped_and_fixed_ttl() -> None:
    """B/C key 不含原始标识；不同商家隔离，读取不调用 expire 续期。"""
    materials = {
        "chat_history": [],
        "image_urls": [],
        "buyer_text": "退款",
        "order_id": "ORDER-SECRET",
        "buyer_id": "buyer-secret",
        "merchant_id": "merchant-a",
    }
    facts = FactOutput(goods_received=True, defect_type="未知", evidence_quality=EVIDENCE_LOW, confidence=0.5)
    storage: dict[str, str] = {}
    mock_client = MagicMock()
    mock_client.setex.side_effect = lambda key, _ttl, value: storage.__setitem__(key, value)
    mock_client.get.side_effect = storage.get

    key = _facts_key("merchant-a", "DISPUTE-SECRET", compute_fp_agent1(materials))
    assert key.startswith(f"ea:v2:b:{merchant_cache_scope('merchant-a')}:")
    assert "merchant-a" not in key
    assert "DISPUTE-SECRET" not in key

    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1", "FACTS_CACHE_TTL_SECONDS": "86400"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            save_facts("merchant-a", "DISPUTE-SECRET", materials, facts)
            assert get_cached_facts("merchant-b", "DISPUTE-SECRET", materials) is None
            assert get_cached_facts("merchant-a", "DISPUTE-SECRET", materials) is not None

    assert mock_client.setex.call_args.args[1] == 86400
    mock_client.expire.assert_not_called()


def test_empty_cases_use_short_negative_ttl() -> None:
    """无匹配判例仅缓存 60 秒，避免长期掩盖后续新复盘数据。"""
    storage: dict[str, str] = {}
    mock_client = MagicMock()
    mock_client.setex.side_effect = lambda key, _ttl, value: storage.__setitem__(key, value)
    mock_client.get.side_effect = storage.get

    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1", "CASES_EMPTY_CACHE_TTL_SECONDS": "60"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            save_cases("merchant-a", "描述不存在判例", 3, [])
            assert get_cached_cases("merchant-a", "描述不存在判例", 3) == []

    assert mock_client.setex.call_args.args[1] == 60


def test_redis_reconnects_after_cooldown() -> None:
    """首次连接失败进入冷却；冷却结束后 Redis 恢复可自动重连。"""
    healthy_client = MagicMock()
    healthy_client.ping.return_value = True
    with patch.dict(
        os.environ,
        {
            "ENABLE_REDIS_CACHE": "1",
            "REDIS_CACHE_URL": "redis://localhost:6379/1",
            "REDIS_RECONNECT_COOLDOWN_SECONDS": "1",
        },
        clear=False,
    ):
        with patch("backend.cache.redis_client.redis.Redis.from_url", side_effect=[RuntimeError("连接失败"), healthy_client]):
            with patch.object(redis_client.time, "monotonic", side_effect=[0.0, 0.0, 2.0]):
                assert get_redis() is None
                assert get_redis() is healthy_client
