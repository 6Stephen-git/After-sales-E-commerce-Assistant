"""
品类 slug 单一路径核心测试：API 优先、语义 LLM 兜底、二手消歧。

原则：不做中文硬匹配；细节以 rule_lexicon 与 eval 为准。
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.tools.rule_lexicon import (
    disambiguate_inferred_category_slug,
    infer_category_slug_from_materials,
    resolve_category_slug,
    should_infer_category_slug,
)


def test_api_slug_priority_and_no_hard_map(monkeypatch):
    """中文品类标签不硬映射；有效 API slug 直接使用且不调 LLM。"""
    assert infer_category_slug_from_materials({"category": "宠物"}) is None

    called = {"n": 0}

    def _no_llm(*_args, **_kwargs):
        called["n"] += 1
        return None, 0.0

    monkeypatch.setattr("backend.tools.rule_lexicon.infer_category_slug_llm", _no_llm)
    slug, conf, source = resolve_category_slug({"product_category_slug": "pet"}, "小狗死亡")
    assert slug == "pet"
    assert conf == 1.0
    assert source == "api"
    assert called["n"] == 0


def test_llm_inference_and_service_tag_gate(monkeypatch):
    """有服务标或聊天上下文时走 LLM 推断。"""
    def _fake_llm(_text: str, _materials: dict) -> tuple[str | None, float]:
        return "pet", 0.92

    monkeypatch.setattr("backend.tools.rule_lexicon.infer_category_slug_llm", _fake_llm)
    slug, conf, source = resolve_category_slug(
        {"category": "宠物", "platform_service_tags": ["伤亡大病包退"]},
        "小狗到家后死亡要求退款",
        issue_summary="宠物死亡退款",
        defect_type="宠物伤亡",
    )
    assert slug == "pet"
    assert source == "llm"
    assert conf >= 0.9
    assert should_infer_category_slug({"platform_service_tags": ["伤亡大病包退"]}, "")


def test_disambiguate_secondhand_context():
    """使用痕迹投诉应拒绝 secondhand；二手 listing 上下文保留。"""
    rejected_slug, rejected_conf = disambiguate_inferred_category_slug(
        "secondhand",
        0.7,
        {"category": "耳机"},
        "我在你们家买的耳机感觉有人使用过了啊",
        issue_summary="买家反映耳机疑似被使用过",
    )
    assert rejected_slug is None
    assert rejected_conf == 0.0

    kept_slug, kept_conf = disambiguate_inferred_category_slug(
        "secondhand",
        0.85,
        {"category": "二手数码"},
        "买的二手耳机和描述成色不符",
        issue_summary="二手耳机成色问题",
    )
    assert kept_slug == "secondhand"
    assert kept_conf == 0.85
