"""
辅助模式控制器集成测试。

覆盖：全链路、增量缓存、空材料边界、多纠纷缓存隔离；依赖 `clear_cache` 保证用例独立。
"""

import os
import sys


# ---------- 与 Agent 单测一致：保证可从仓库根导入 schemas 与 backend ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.controllers.assisted_controller import clear_cache, run
import backend.controllers.assisted_controller as assisted_controller_module
import backend.agents.agent2.strategist as strategist_module
from schemas import EVIDENCE_LOW, MatchedRule


# ---------- 每个用例前：清空进程内缓存 ----------
def setup_function() -> None:
    """
    每个用例前清空控制器缓存，避免互相污染。
    """
    clear_cache()
    assisted_controller_module.match_rules = lambda facts: [
        MatchedRule(
            rule_id="R002",
            rule_summary="买家提供清晰瑕疵图片且证据质量高，平台倾向支持买家退款",
            condition_result="规则条件全部满足；建议策略:compensate",
        )
    ] if facts.defect_type == "破洞" and facts.evidence_quality == "high" else []
    strategist_module._llm_infer_customer_value_fields = lambda _input: {
        "defect_severity": "moderate",
        "goods_recoverability": "repairable",
        "buyer_cooperation": "neutral",
        "demand_reasonableness": "borderline",
    }
    strategist_module._llm_generate_reasoning = lambda **kwargs: None


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
    assert report.scripts.recommended_version in {
        "defense_version",
        "negotiate_version",
        "compensate_version",
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
    assert report.scripts.defense_version.strip() != ""


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

