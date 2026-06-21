"""
辅助模式控制器集成测试。

覆盖：全链路、增量缓存、空材料边界、多纠纷缓存隔离、C 层报告缓存命中；依赖 `clear_cache` 保证用例独立。
"""

import os
import sys
from unittest.mock import MagicMock, patch


# ---------- 与 Agent 单测一致：保证可从仓库根导入 schemas 与 backend ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("ENABLE_REDIS_CACHE", "0")

from backend.controllers.assisted_controller import clear_cache, run
import backend.controllers.assisted_controller as assisted_controller_module
import backend.tools.agent2_tools as agent2_tools_module
import backend.agents.agent2.strategist as strategist_module
from backend.cache.redis_client import reset_redis_client
from schemas import EVIDENCE_LOW, MatchedRule, RuleBrief, RuleMatchResult, RULE_RELEVANCE_SHOULD


# ---------- 每个用例前：清空进程内缓存 ----------
def setup_function() -> None:
    """
    每个用例前清空控制器缓存，避免互相污染。
    """
    clear_cache()
    reset_redis_client()
    def _mock_match_rules_from_facts(facts):
        if facts.defect_type == "破洞" and facts.evidence_quality == "high":
            rule = MatchedRule(
                rule_id="mock::第六十五条",
                rule_summary="买家主张商品存在质量问题系肉眼可识别的，应提供初步凭证",
                condition_result="mock",
                relevance=RULE_RELEVANCE_SHOULD,
                stance_hint="merchant",
            )
            brief = RuleBrief(article_ref="第六十五条", brief=rule.rule_summary, relevance=RULE_RELEVANCE_SHOULD)
            return RuleMatchResult(matched_rules=[rule], rule_briefs=[brief], display_rules=[rule])
        return RuleMatchResult()

    assisted_controller_module.match_rules_from_facts = _mock_match_rules_from_facts
    strategist_module._llm_generate_strategy = lambda **kwargs: None


# ---------- 场景一：首次全量材料，三 Agent 串行产出完整报告 ----------
def test_run_full_chain_should_return_valid_report() -> None:
    """
    完整材料首次调用，返回合法 AnalysisReport。
    """
    report = run(
        dispute_id="DISPUTE-C-001",
        new_materials={
            "order_id": "ORDER10001",
            "buyer_id": "buyer_loyal",
            "order_amount": 129.0,
            "buyer_text": "我收到了衣服，袖子有破洞",
            "chat_history": [{"role": "buyer", "content": "已经签收，存在质量问题"}],
            "image_urls": ["mock://tear-tag"],
        },
    )

    assert report.dispute_id == "DISPUTE-C-001"
    assert report.facts is not None
    assert report.strategy.disposition in {"defend", "negotiate", "compensate"}
    assert report.scripts.script.strip() != ""
    assert report.scripts.response_mode in {
        "merchant_fault",
        "malicious_risk",
        "neutral_negotiate",
    }


# ---------- 场景二：同 dispute_id 二次调用仅追加图片/消息，事实层应更新 ----------
def test_run_should_append_incremental_materials() -> None:
    """
    第二次调用追加新图片后，事实输出应感知到新增举证。
    """
    first_report = run(
        dispute_id="DISPUTE-C-002",
        new_materials={
            "order_id": "ORDER10002",
            "buyer_id": "buyer_loyal",
            "order_amount": 88.0,
            "buyer_text": "收到商品但有问题",
            "chat_history": [{"role": "buyer", "content": "请处理一下"}],
            "image_urls": [],
        },
    )
    second_report = run(
        dispute_id="DISPUTE-C-002",
        new_materials={
            "chat_history": [{"role": "buyer", "content": "补充了图片"}],
            "image_urls": ["mock://tear-tag"],
        },
    )

    assert first_report.facts is not None
    assert second_report.facts is not None
    assert len(second_report.facts.evidence_items) >= len(first_report.facts.evidence_items)


# ---------- 场景三：空 dict 材料，链路仍返回合法低证据报告 ----------
def test_run_should_handle_empty_materials_boundary() -> None:
    """
    空材料边界：不崩溃且返回合法低证据报告。
    """
    report = run(dispute_id="DISPUTE-C-003", new_materials={})

    assert report.dispute_id == "DISPUTE-C-003"
    assert report.facts.evidence_quality == EVIDENCE_LOW
    assert report.strategy.disposition in {"defend", "negotiate", "compensate"}
    assert report.scripts.script.strip() != ""


# ---------- 场景四：不同 dispute_id 并行缓存互不影响 ----------
def test_run_should_isolate_cache_by_dispute_id() -> None:
    """
    不同 dispute_id 的缓存隔离，互不干扰。
    """
    run(
        dispute_id="DISPUTE-C-004-A",
        new_materials={
            "order_id": "ORDER10004",
            "buyer_id": "buyer_loyal",
            "buyer_text": "A 纠纷",
            "image_urls": ["mock://tear-tag"],
        },
    )
    run(
        dispute_id="DISPUTE-C-004-B",
        new_materials={
            "order_id": "ORDER10005",
            "buyer_id": "buyer_high_risk",
            "buyer_text": "B 纠纷",
            "image_urls": ["mock://stain-no-tag"],
        },
    )

    report_a = run(dispute_id="DISPUTE-C-004-A", new_materials={})
    report_b = run(dispute_id="DISPUTE-C-004-B", new_materials={})

    assert report_a.facts is not None
    assert report_b.facts is not None


# ---------- 场景五：同材料二次调用应命中 C 层，不再调用 Agent1 ----------
def test_run_should_hit_report_cache_on_same_materials() -> None:
    """
    Redis C 层命中时，第二次调用不应再触发 extract。
    """
    extract_call_count = {"count": 0}
    original_extract = assisted_controller_module.run_agent1_extract

    def counting_extract(materials):
        extract_call_count["count"] += 1
        return original_extract(materials)

    materials = {
        "order_id": "ORDER10006",
        "buyer_id": "buyer_loyal",
        "order_amount": 129.0,
        "buyer_text": "有质量问题",
        "chat_history": [{"role": "buyer", "content": "请处理"}],
        "image_urls": ["mock://tear-tag"],
    }

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
    mock_client.scan_iter.return_value = iter([])
    mock_client.delete.return_value = 1

    with patch.dict(os.environ, {"ENABLE_REDIS_CACHE": "1"}, clear=False):
        with patch("backend.cache.redis_client.get_redis", return_value=mock_client):
            with patch.object(assisted_controller_module, "run_agent1_extract", side_effect=counting_extract):
                first_report = run(dispute_id="DISPUTE-C-005", new_materials=materials)
                second_report = run(dispute_id="DISPUTE-C-005", new_materials={})

    assert first_report.dispute_id == "DISPUTE-C-005"
    assert second_report.dispute_id == "DISPUTE-C-005"
    assert extract_call_count["count"] == 1
    assert second_report.strategy.disposition == first_report.strategy.disposition
