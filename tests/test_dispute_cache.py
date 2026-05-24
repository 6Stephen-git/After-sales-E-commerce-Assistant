"""
纠纷 Redis 缓存单元测试。

覆盖：指纹稳定性、材料增量合并、B/C 层 hit/miss、reset 失效、Redis 降级。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.cache.fingerprint import compute_fp_agent1, compute_fp_report
from backend.cache.materials_store import clear_all_cache, merge_materials
from backend.cache.redis_client import reset_redis_client
from backend.cache.result_cache import get_cached_facts, get_cached_report, save_facts, save_report
from schemas import AnalysisReport, EVIDENCE_LOW, FactOutput, ScriptOutput, StrategyOutput


@pytest.fixture(autouse=True)
def reset_cache_state() -> None:
    """
    每个用例前清理本地缓存并重置 Redis 客户端单例。
    """
    clear_all_cache()
    reset_redis_client()
    yield
    clear_all_cache()
    reset_redis_client()


# ---------- 指纹：相同材料应得到相同 hash ----------
def test_fingerprint_should_be_stable_for_same_materials() -> None:
    """
    相同材料多次计算指纹应一致。
    """
    materials = {
        "chat_history": [{"role": "buyer", "content": "有质量问题"}],
        "image_urls": ["mock://a"],
        "buyer_text": "退款",
        "order_id": "O1",
        "order_amount": 99.0,
        "buyer_id": "b1",
        "merchant_id": "m1",
    }
    assert compute_fp_agent1(materials) == compute_fp_agent1(dict(materials))
    assert compute_fp_report(materials) == compute_fp_report(dict(materials))


# ---------- 指纹：关键字段变化应导致 hash 变化 ----------
def test_fingerprint_should_change_when_chat_or_amount_changes() -> None:
    """
    聊天或金额变化时，对应层指纹应变化。
    """
    base = {
        "chat_history": [{"role": "buyer", "content": "原消息"}],
        "image_urls": [],
        "buyer_text": "退款",
        "order_id": "O1",
        "order_amount": 99.0,
        "buyer_id": "b1",
        "merchant_id": "m1",
    }
    fp_agent1_base = compute_fp_agent1(base)
    fp_report_base = compute_fp_report(base)

    changed_chat = dict(base)
    changed_chat["chat_history"] = [{"role": "buyer", "content": "新消息"}]
    assert compute_fp_agent1(changed_chat) != fp_agent1_base
    assert compute_fp_report(changed_chat) != fp_report_base

    changed_amount = dict(base)
    changed_amount["order_amount"] = 199.0
    assert compute_fp_agent1(changed_amount) == fp_agent1_base
    assert compute_fp_report(changed_amount) != fp_report_base


# ---------- 材料层：同 dispute_id 增量合并 chat/image ----------
def test_merge_materials_should_append_incrementally() -> None:
    """
    同纠纷二次合并应追加 chat_history 与 image_urls。
    """
    first = merge_materials(
        "D-001",
        {
            "chat_history": [{"role": "buyer", "content": "第一条"}],
            "image_urls": ["mock://a"],
        },
    )
    second = merge_materials(
        "D-001",
        {
            "chat_history": [{"role": "buyer", "content": "第二条"}],
            "image_urls": ["mock://b"],
        },
    )
    assert len(first["chat_history"]) == 1
    assert len(second["chat_history"]) == 2
    assert len(second["image_urls"]) == 2


# ---------- B/C 层：mock Redis 下 hit/miss ----------
def test_result_cache_should_hit_after_save() -> None:
    """
    写入 B/C 层后，相同材料应命中缓存。
    """
    materials = {
        "chat_history": [{"role": "buyer", "content": "测试"}],
        "image_urls": [],
        "buyer_text": "测试",
        "order_id": "O2",
        "order_amount": 50.0,
        "buyer_id": "b2",
        "merchant_id": "m2",
    }
    facts = FactOutput(
        goods_received=True,
        defect_type="未知",
        evidence_quality=EVIDENCE_LOW,
        confidence=0.5,
    )
    report = AnalysisReport(
        dispute_id="D-002",
        facts=facts,
        strategy=StrategyOutput(
            disposition="defend",
            confidence=0.5,
        ),
        scripts=ScriptOutput(
            defense_version="话术",
            negotiate_version="话术",
            compensate_version="话术",
            recommended_version="defense_version",
        ),
    )

    mock_client = MagicMock()
    storage: dict[str, str] = {}

    def fake_setex(key: str, _ttl: int, value: str) -> None:
        storage[key] = value

    def fake_get(key: str) -> str | None:
        return storage.get(key)

    mock_client.setex.side_effect = fake_setex
    mock_client.get.side_effect = fake_get
    mock_client.expire.return_value = True
    mock_client.ping.return_value = True

    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            save_facts("D-002", materials, facts)
            save_report("D-002", materials, report)

            cached_facts = get_cached_facts("D-002", materials)
            cached_report = get_cached_report("D-002", materials)

    assert cached_facts is not None
    assert cached_facts.defect_type == "未知"
    assert cached_report is not None
    assert cached_report.strategy.disposition == "defend"


# ---------- 降级：Redis 读失败应视为 miss ----------
def test_result_cache_should_miss_when_redis_read_fails() -> None:
    """
    Redis 读异常时不应抛错，应返回 None。
    """
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
            assert get_cached_report("D-003", materials) is None
