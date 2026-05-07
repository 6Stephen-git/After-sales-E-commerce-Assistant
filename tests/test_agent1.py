"""
Agent 1 内嵌测试：正常、冲突、缺失场景。
"""

import os
import sys

# ---------- 与仓库根对齐的导入路径（单测可直接 pytest 运行） ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from schemas import EVIDENCE_LOW, EVIDENCE_MEDIUM

import backend.agents.agent1.fact_extractor as fact_extractor_module
from backend.agents.agent1.fact_extractor import extract


# ---------- 场景：材料齐全，多模态 mock + 物流正常 ----------
def test_extract_with_complete_materials():
    materials = {
        "order_id": "ORDER10001",
        "buyer_text": "我收到了衣服，袖子有破洞",
        "chat_history": [{"role": "buyer", "content": "已经签收，存在质量问题"}],
        "image_urls": ["mock://tear-tag"],
    }

    result = extract(materials)
    assert result.goods_received is True
    assert result.defect_type == "破洞"
    assert result.has_tag_visible is True
    assert result.logistics_normal is True
    assert result.evidence_quality in ["high", "medium"]
    assert result.confidence > 0.5


# ---------- 场景：买家称未收 vs 物流已签收，应打红点 ----------
def test_extract_with_signed_but_claim_not_received():
    materials = {
        "order_id": "ORDER10002",
        "buyer_text": "我还没收到货",
        "chat_history": [{"role": "buyer", "content": "你们显示签收不对"}],
        "image_urls": ["mock://stain-no-tag"],
    }

    result = extract(materials)
    assert result.goods_received is False
    assert any("已签收" in flag for flag in result.red_flags)
    assert result.evidence_quality == EVIDENCE_MEDIUM


# ---------- 场景：缺图 + 异常物流后缀，证据质量下调 ----------
def test_extract_missing_images():
    materials = {
        "order_id": "ORDER10003ABN",
        "buyer_text": "物流一直不动",
        "chat_history": [],
        "image_urls": [],
    }

    result = extract(materials)
    assert any("缺少举证图片" in item for item in result.missing_evidence)
    assert any("物流异常" in flag for flag in result.red_flags)
    assert result.logistics_normal is False
    assert result.confidence <= 0.66


# ---------- 场景：空材料，缺失项与低证据兜底 ----------
def test_extract_empty_materials_boundary_case():
    result = extract({})
    assert result.evidence_quality == EVIDENCE_LOW
    assert result.confidence < 0.5
    assert len(result.missing_evidence) >= 2
    assert result.uncertainty_note is not None


# ---------- 场景：LLM要求补证时禁止覆盖结论，并写入补拍指引 ----------
def test_extract_should_not_force_conclusion_when_llm_requires_clarify(monkeypatch):
    def _mock_llm_extract_facts(**_kwargs):
        return {
            "need_clarify": True,
            "confidence": 0.95,
            "defect_type": "破洞",
            "missing_evidence": ["请补拍破损部位近景和全景"],
            "clarify_requests": ["请补拍吊牌与衣领位置细节"],
            "uncertainty_reasons": ["当前图片边缘过暗，无法确认是否人为破损"],
        }

    monkeypatch.setattr(fact_extractor_module, "_llm_extract_facts", _mock_llm_extract_facts)
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda image_url: {"defect_type": "无法判断", "defect_location": "袖口"},
    )

    result = extract(
        {
            "order_id": "ORDER10011",
            "buyer_text": "衣服有问题",
            "chat_history": [{"role": "buyer", "content": "你看图"}],
            "image_urls": ["mock://custom"],
        }
    )
    assert result.defect_type is None
    assert any("补拍" in item for item in result.missing_evidence)
    assert result.uncertainty_note is not None


# ---------- 场景：LLM高置信且无需补证时允许补全空字段 ----------
def test_extract_should_fill_missing_fields_from_high_confidence_llm(monkeypatch):
    def _mock_llm_extract_facts(**_kwargs):
        return {
            "need_clarify": False,
            "confidence": 0.91,
            "defect_type": "破洞",
            "defect_location": "袖口",
            "defect_edge": "毛糙",
            "has_tag_visible": True,
            "photo_background": "木桌",
            "wear_signs": "无明显穿着痕迹",
        }

    monkeypatch.setattr(fact_extractor_module, "_llm_extract_facts", _mock_llm_extract_facts)
    monkeypatch.setattr(fact_extractor_module, "analyze_image", lambda image_url: {})

    result = extract(
        {
            "order_id": "ORDER10012",
            "buyer_text": "收到后袖口有问题",
            "chat_history": [{"role": "buyer", "content": "请看处理"}],
            "image_urls": ["mock://custom"],
        }
    )
    assert result.defect_type == "破洞"
    assert result.defect_location == "袖口"
    assert result.has_tag_visible is True
