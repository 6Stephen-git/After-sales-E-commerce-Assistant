"""
Agent1 事实提取核心契约测试。

原则：仅保留典型材料路径与关键边界；业务回归以 eval 评测树 + Judge 为准。
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from schemas import EVIDENCE_LOW, EVIDENCE_MEDIUM, LogisticsInfo

import backend.agents.agent1.fact_extractor as fact_extractor_module
from backend.agents.agent1.fact_extractor import extract


def _mock_logistics_signed(monkeypatch):
    """注入已签收物流 mock。"""
    monkeypatch.setattr(
        fact_extractor_module,
        "query_logistics",
        lambda **_kwargs: LogisticsInfo(
            is_shipped=True,
            is_signed=True,
            stagnant_days=0,
            is_abnormal=False,
        ),
    )


def test_extract_with_complete_materials(monkeypatch):
    """材料齐全：多模态与物流信息应写入事实输出。"""
    _mock_logistics_signed(monkeypatch)
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda **_kwargs: {
            "visual_description": "衣物袖子区域可见破洞",
            "findings": ["袖子破洞"],
            "defect_type": "外观破损",
            "defect_location": "袖子",
            "visual_defect_severity": "moderate",
            "visual_goods_recoverability": "repairable",
        },
    )
    result = extract(
        {
            "order_id": "ORDER10001",
            "buyer_text": "我收到了衣服，袖子有破洞",
            "chat_history": [{"role": "buyer", "content": "已经签收，存在质量问题"}],
            "image_urls": ["https://example.com/evidence/damage.jpg"],
        }
    )
    assert result.issue_summary
    assert result.visual_observations
    assert result.goods_received is True
    assert result.defect_type == "外观破损"
    assert result.logistics_normal is True
    assert result.evidence_quality in ("high", "medium")
    assert result.confidence > 0.5


def test_extract_with_signed_but_claim_not_received(monkeypatch):
    """买家称未收 vs 物流已签收：应打红点。"""
    _mock_logistics_signed(monkeypatch)
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda **_kwargs: {"visual_description": "商品表面存在污渍", "findings": ["表面污渍"]},
    )
    result = extract(
        {
            "order_id": "ORDER10002",
            "buyer_text": "我还没收到货",
            "chat_history": [{"role": "buyer", "content": "你们显示签收不对"}],
            "image_urls": ["https://example.com/evidence/stain.jpg"],
        }
    )
    assert result.goods_received is False
    assert any("已签收" in flag for flag in result.red_flags)
    assert result.evidence_quality == EVIDENCE_MEDIUM


def test_extract_low_evidence_boundary():
    """缺图或空材料：证据质量下调且不崩溃。"""
    missing_img = extract(
        {
            "order_id": "ORDER10003",
            "buyer_text": "物流一直不动",
            "chat_history": [],
            "image_urls": [],
        }
    )
    assert any("缺少举证图片" in item for item in missing_img.missing_evidence)
    assert missing_img.evidence_quality in (EVIDENCE_LOW, EVIDENCE_MEDIUM)

    empty = extract({})
    assert empty.evidence_quality == EVIDENCE_LOW
    assert empty.confidence < 0.5
    assert len(empty.missing_evidence) >= 2


def test_extract_issue_llm_merge_and_fallback(monkeypatch):
    """诉求 LLM 成功时合并补证与 intent_tags；失败时回退基础摘要。"""
    def _rich_issue(**_kwargs):
        return {
            "issue_summary": "买家反馈商品有破损，需要核查位置",
            "intent_tags": ["质量问题", "退款诉求"],
            "confidence": 0.95,
            "missing_evidence": ["请补拍破损部位近景和全景"],
            "red_flags": ["描述与图片存在轻微冲突"],
        }

    monkeypatch.setattr(fact_extractor_module, "_llm_extract_issue", _rich_issue)
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda **_kwargs: {"visual_description": "袖口区域疑似破损", "findings": ["袖口疑似破损"]},
    )
    rich = extract(
        {
            "order_id": "ORDER10011",
            "buyer_text": "衣服有问题",
            "chat_history": [{"role": "buyer", "content": "你看图"}],
            "image_urls": ["https://example.com/evidence/custom.jpg"],
        }
    )
    assert any("补拍" in item for item in rich.missing_evidence)
    assert "质量问题" in rich.intent_tags

    monkeypatch.setattr(fact_extractor_module, "_llm_extract_issue", lambda **_kwargs: None)
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda **_kwargs: {
            "visual_description": "左袖口存在清晰破洞",
            "findings": ["左袖口破洞"],
            "defect_type": "破洞",
            "defect_location": "左袖口",
            "has_tag": True,
        },
    )
    fallback = extract(
        {
            "order_id": "ORDER10012",
            "buyer_text": "收到后袖口有问题",
            "image_urls": ["https://example.com/evidence/custom.jpg"],
        }
    )
    assert fallback.issue_summary
    assert fallback.defect_type == "破洞"
    assert fallback.has_tag_visible is True


def test_extract_vision_guidance_and_credential_trust(monkeypatch):
    """视觉分析以诉求为 guidance；可疑图源应写入 credential_trust。"""
    captured_guidance: list[str] = []

    def _mock_analyze_image(**kwargs):
        captured_guidance.append(str(kwargs.get("guidance", "")))
        return {
            "visual_description": "图像右下角有 sohu.com 水印",
            "findings": ["图片带有网址水印，疑似网络公开图片"],
            "credential_trust": "suspect",
            "credential_trust_note": "图片角落可见门户网站水印，疑似网图",
        }

    monkeypatch.setattr(
        fact_extractor_module,
        "_llm_extract_issue",
        lambda **_kwargs: {
            "issue_summary": "买家反映香蕉收到即严重褐变发黑",
            "intent_tags": ["质量问题", "退款诉求"],
            "confidence": 0.9,
        },
    )
    monkeypatch.setattr(fact_extractor_module, "analyze_image", _mock_analyze_image)

    result = extract(
        {
            "order_id": "ORDER10015",
            "buyer_text": "水果有问题",
            "image_urls": ["https://example.com/evidence/banana.jpg"],
        }
    )
    assert captured_guidance
    assert "严重褐变发黑" in captured_guidance[0]
    assert any("水印" in item for item in result.visual_observations)
    assert result.credential_trust == "suspect"
