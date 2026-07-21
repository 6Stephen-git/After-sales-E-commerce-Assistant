"""
V/T 层缓存核心测试：视觉、物流、画像命中与 Redis 降级。

原则：每类缓存保留 hit/miss 或 bypass 代表路径。
"""

from __future__ import annotations

import json as json_module
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.cache.materials_store import clear_all_cache
from backend.cache.redis_client import reset_redis_client
from backend.cache.tool_cache import clear_tool_caches, get_cached_cases, save_cases
from backend.cache.vision_cache import get_cached_vision, save_vision
from backend.tools.agent1_tools import analyze_image
from backend.tools.agent2_tools import query_buyer_profile, search_similar_cases
from backend.tools.platform_api import query_logistics
from schemas import BuyerProfile, LogisticsInfo, SimilarCase


@pytest.fixture(autouse=True)
def reset_cache_state() -> None:
    """每个用例前清理纠纷缓存与 V/T 缓存。"""
    clear_all_cache()
    clear_tool_caches()
    reset_redis_client()
    yield
    clear_all_cache()
    clear_tool_caches()
    reset_redis_client()


def _mock_redis_storage() -> tuple[MagicMock, dict[str, str]]:
    """构造内存 Redis mock。"""
    storage: dict[str, str] = {}
    mock_client = MagicMock()

    def fake_setex(key: str, _ttl: int, value: str) -> None:
        storage[key] = value

    def fake_get(key: str) -> str | None:
        return storage.get(key)

    mock_client.setex.side_effect = fake_setex
    mock_client.get.side_effect = fake_get
    mock_client.ping.return_value = True
    mock_client.scan_iter.return_value = iter([])
    mock_client.delete.return_value = 1
    return mock_client, storage


def test_vision_cache_hit_and_guidance_miss() -> None:
    """同图同 guidance 命中；guidance 变化应 miss。"""
    image_ref = "mock://product-image"
    model = "qwen3-vl-flash"
    payload = {"visual_description": "可见划痕", "findings": ["左侧有划痕"]}
    mock_client, _storage = _mock_redis_storage()

    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            save_vision("merchant-1", image_ref, model, "诉求A", payload)
            assert get_cached_vision("merchant-1", image_ref, model, "诉求A") is not None
            assert get_cached_vision("merchant-1", image_ref, model, "诉求B") is None


def test_analyze_image_should_use_cache_without_second_http_call() -> None:
    """二次 analyze_image 在 V 层命中时不应再次发起 HTTP。"""
    vision_payload = {
        "visual_description": "包装破损",
        "findings": ["外箱凹陷"],
        "visual_red_flags": [],
        "credential_trust": "unknown",
        "credential_trust_note": None,
        "defect_type": "包装破损",
        "defect_location": "外箱",
        "visual_defect_severity": "moderate",
        "visual_goods_recoverability": "resalable",
        "category_slug": None,
    }
    http_call_count = {"count": 0}

    class FakeHttpxClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, *_args, **_kwargs):
            http_call_count["count"] += 1
            response = MagicMock()
            response.status_code = 200
            response.raise_for_status = MagicMock()
            response.json.return_value = {
                "output": {
                    "choices": [
                        {"message": {"content": json_module.dumps(vision_payload, ensure_ascii=False)}}
                    ]
                }
            }
            return response

    mock_client, _storage = _mock_redis_storage()
    env = {
        "ENABLE_REDIS_CACHE": "1",
        "VISION_API_ENDPOINT": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        "VISION_API_KEY": "test-key",
        "VISION_API_MODEL": "qwen3-vl-flash",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            with patch("backend.tools.agent1_tools.httpx.Client", FakeHttpxClient):
                first = analyze_image("mock://box", guidance="包装问题", merchant_id="merchant-1")
                second = analyze_image("mock://box", guidance="包装问题", merchant_id="merchant-1")

    assert "error" not in first
    assert http_call_count["count"] == 1
    assert second == first


def test_logistics_and_cases_cache() -> None:
    """物流同 order_id 命中；判例按描述隔离。"""
    mock_client, _storage = _mock_redis_storage()
    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            first_logistics = query_logistics("ORDER-9001", merchant_id="merchant-1")
            second_logistics = query_logistics("ORDER-9001", merchant_id="merchant-1")
            assert isinstance(first_logistics, LogisticsInfo)
            assert second_logistics == first_logistics

            cases = [
                SimilarCase(
                    case_id="C1",
                    similarity=0.9,
                    merchant_action="协商",
                    outcome="和解",
                    lesson="及时沟通",
                )
            ]
            save_cases("merchant-1", "描述A", 3, cases)
            assert get_cached_cases("merchant-1", "描述A", 3) is not None
            assert get_cached_cases("merchant-1", "描述B", 3) is None

            assert search_similar_cases("买家称手机划痕", top_k=3) == []


def test_profile_cache_should_hit_without_second_db_query() -> None:
    """同 merchant_id + buyer_id 二次查询画像应只访问一次数据库。"""
    db_call_count = {"count": 0}
    profile_json = json_module.dumps(
        {
            "purchase_count": 3,
            "dispute_count": 1,
            "dispute_rate": 0.1,
            "avg_order_value": 120.0,
            "return_rate": 0.05,
            "malicious_flags": 0,
            "positive_review_count": 2,
            "credit_level": "good",
        },
        ensure_ascii=False,
    )
    fake_record = MagicMock()
    fake_record.profile_json = profile_json

    def fake_execute(*_args, **_kwargs):
        db_call_count["count"] += 1
        result = MagicMock()
        result.scalar_one_or_none.return_value = fake_record
        return result

    mock_session = MagicMock()
    mock_session.execute.side_effect = fake_execute
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = False

    mock_client, _storage = _mock_redis_storage()
    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            with patch("backend.tools.agent2_tools.Session", return_value=mock_session):
                with patch("backend.tools.agent2_tools.get_engine", return_value=MagicMock()):
                    first = query_buyer_profile("buyer_hash_1", merchant_id="merchant_1")
                    second = query_buyer_profile("buyer_hash_1", merchant_id="merchant_1")

    assert isinstance(first, BuyerProfile)
    assert db_call_count["count"] == 1
    assert second.credit_level == first.credit_level


def test_tool_cache_should_bypass_when_redis_disabled() -> None:
    """ENABLE_REDIS_CACHE=0 时工具层应直查且不写入缓存。"""
    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "0"}, clear=False):
        assert get_cached_vision("merchant-1", "mock://x", "qwen3-vl-flash", "") is None
        assert isinstance(query_logistics("ORDER-1"), LogisticsInfo)
