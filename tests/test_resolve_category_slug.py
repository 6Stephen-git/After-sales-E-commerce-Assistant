"""品类 slug 单一路径：校验字段优先，语义 LLM 兜底，不做中文硬匹配。"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.tools.rule_lexicon import (
    infer_category_slug_from_materials,
    resolve_category_slug,
    should_infer_category_slug,
)


def test_materials_category_label_does_not_hard_map() -> None:
    assert infer_category_slug_from_materials({"category": "宠物"}) is None
    assert infer_category_slug_from_materials({"category": "水果"}) is None


def test_valid_slug_field_is_used_without_llm(monkeypatch) -> None:
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


def test_resolve_category_slug_uses_llm_for_pet_context(monkeypatch) -> None:
    def _fake_llm(_text: str, _materials: dict) -> tuple[str | None, float]:
        return "pet", 0.92

    monkeypatch.setattr("backend.tools.rule_lexicon.infer_category_slug_llm", _fake_llm)
    materials = {"category": "宠物", "platform_service_tags": ["伤亡大病包退"]}
    slug, conf, source = resolve_category_slug(
        materials,
        "小狗到家后死亡要求退款",
        issue_summary="宠物死亡退款",
        defect_type="宠物伤亡",
    )
    assert slug == "pet"
    assert source == "llm"
    assert conf >= 0.9


def test_should_infer_when_service_tag_present() -> None:
    assert should_infer_category_slug(
        {"platform_service_tags": ["伤亡大病包退"]},
        "",
    )


def test_disambiguate_rejects_secondhand_for_used_condition_complaint() -> None:
    from backend.tools.rule_lexicon import disambiguate_inferred_category_slug

    slug, conf = disambiguate_inferred_category_slug(
        "secondhand",
        0.7,
        {"category": "耳机"},
        "我在你们家买的耳机感觉有人使用过了啊",
        issue_summary="买家反映耳机疑似被使用过",
    )
    assert slug is None
    assert conf == 0.0


def test_disambiguate_keeps_secondhand_for_listing_context() -> None:
    from backend.tools.rule_lexicon import disambiguate_inferred_category_slug

    slug, conf = disambiguate_inferred_category_slug(
        "secondhand",
        0.85,
        {"category": "二手数码"},
        "买的二手耳机和描述成色不符",
        issue_summary="二手耳机成色问题",
    )
    assert slug == "secondhand"
    assert conf == 0.85
