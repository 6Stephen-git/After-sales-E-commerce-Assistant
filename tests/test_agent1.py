"""
Agent 1 内嵌测试：正常、冲突、缺失场景。
"""

import os
import sys

# ---------- 与仓库根对齐的导入路径（单测可直接 pytest 运行） ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from schemas import EVIDENCE_LOW, EVIDENCE_MEDIUM, LogisticsInfo

import backend.agents.agent1.fact_extractor as fact_extractor_module
from backend.agents.agent1.fact_extractor import extract


# ---------- 场景：材料齐全，多模态与物流由测试层注入 ----------
def test_extract_with_complete_materials(monkeypatch):
    monkeypatch.setattr(
        fact_extractor_module,
        "query_logistics",
        lambda order_id="": LogisticsInfo(
            is_shipped=True,
            is_signed=True,
            stagnant_days=0,
            is_abnormal=False,
        ),
    )
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda image_url="", guidance="": {
            "visual_description": "衣物袖子区域可见破洞",
            "findings": ["袖子破洞"],
            "defect_type": "外观破损",
            "defect_location": "袖子",
            "visual_defect_severity": "moderate",
            "visual_goods_recoverability": "repairable",
        },
    )

    materials = {
        "order_id": "ORDER10001",
        "buyer_text": "我收到了衣服，袖子有破洞",
        "chat_history": [{"role": "buyer", "content": "已经签收，存在质量问题"}],
        "image_urls": ["https://example.com/evidence/damage.jpg"],
    }

    result = extract(materials)
    assert result.issue_summary
    assert result.visual_observations
    assert result.evidence_items
    assert result.goods_received is True
    assert result.defect_type == "外观破损"
    assert result.logistics_normal is True
    assert result.evidence_quality in ["high", "medium"]
    assert result.confidence > 0.5


# ---------- 场景：买家称未收 vs 物流已签收，应打红点 ----------
def test_extract_with_signed_but_claim_not_received(monkeypatch):
    monkeypatch.setattr(
        fact_extractor_module,
        "query_logistics",
        lambda order_id="": LogisticsInfo(
            is_shipped=True,
            is_signed=True,
            stagnant_days=0,
            is_abnormal=False,
        ),
    )
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda image_url="", guidance="": {
            "visual_description": "商品表面存在污渍",
            "findings": ["表面污渍"],
        },
    )

    materials = {
        "order_id": "ORDER10002",
        "buyer_text": "我还没收到货",
        "chat_history": [{"role": "buyer", "content": "你们显示签收不对"}],
        "image_urls": ["https://example.com/evidence/stain.jpg"],
    }

    result = extract(materials)
    assert result.goods_received is False
    assert any("已签收" in flag for flag in result.red_flags)
    assert result.evidence_quality == EVIDENCE_MEDIUM


# ---------- 场景：缺图时证据质量应下调 ----------
def test_extract_missing_images():
    materials = {
        "order_id": "ORDER10003",
        "buyer_text": "物流一直不动",
        "chat_history": [],
        "image_urls": [],
    }

    result = extract(materials)
    assert any("缺少举证图片" in item for item in result.missing_evidence)
    assert result.evidence_quality in (EVIDENCE_LOW, EVIDENCE_MEDIUM)
    assert result.confidence <= 0.72


# ---------- 场景：空材料，缺失项与低证据兜底 ----------
def test_extract_empty_materials_boundary_case():
    result = extract({})
    assert result.evidence_quality == EVIDENCE_LOW
    assert result.confidence < 0.5
    assert len(result.missing_evidence) >= 2
    assert result.uncertainty_note is not None


# ---------- 场景：诉求提取模型输出补证要求，需合并进缺失证据 ----------
def test_extract_should_merge_missing_evidence_from_issue_llm(monkeypatch):
    def _mock_llm_extract_issue(**_kwargs):
        return {
            "issue_summary": "买家反馈商品有破损，需要核查位置",
            "intent_tags": ["质量问题", "退款诉求"],
            "confidence": 0.95,
            "missing_evidence": ["请补拍破损部位近景和全景"],
            "red_flags": ["描述与图片存在轻微冲突"],
        }

    monkeypatch.setattr(fact_extractor_module, "_llm_extract_issue", _mock_llm_extract_issue)
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda **_kwargs: {"visual_description": "袖口区域疑似破损", "findings": ["袖口疑似破损"]},
    )

    result = extract(
        {
            "order_id": "ORDER10011",
            "buyer_text": "衣服有问题",
            "chat_history": [{"role": "buyer", "content": "你看图"}],
            "image_urls": ["https://example.com/evidence/custom.jpg"],
        }
    )
    assert any("补拍" in item for item in result.missing_evidence)
    assert any("冲突" in flag for flag in result.red_flags)


# ---------- 场景：诉求提取失败时回退基础摘要并保持输出可用 ----------
def test_extract_should_fallback_when_issue_llm_unavailable(monkeypatch):
    def _mock_llm_extract_issue(**_kwargs):
        return None

    monkeypatch.setattr(fact_extractor_module, "_llm_extract_issue", _mock_llm_extract_issue)
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda **_kwargs: {
            "visual_description": "左袖口存在清晰破洞",
            "findings": ["左袖口破洞", "边缘毛糙"],
            "attributes": {"issue_location": "左袖口"},
            "defect_type": "破洞",
            "defect_location": "左袖口",
            "edge_condition": "毛糙",
            "has_tag": True,
        },
    )

    result = extract(
        {
            "order_id": "ORDER10012",
            "buyer_text": "收到后袖口有问题",
            "chat_history": [{"role": "buyer", "content": "请看处理"}],
            "image_urls": ["https://example.com/evidence/custom.jpg"],
        }
    )
    assert result.issue_summary is not None
    assert result.visual_observations
    assert result.defect_type == "破洞"
    assert result.defect_location == "左袖口"
    assert result.has_tag_visible is True


# ---------- 场景：诉求提取成功时可输出 intent_tags ----------
def test_extract_should_keep_intent_tags_from_issue_llm(monkeypatch):
    def _mock_llm_extract_issue(**_kwargs):
        return {
            "issue_summary": "买家反馈物流迟迟未更新",
            "intent_tags": ["物流异常", "催处理"],
            "goods_received": False,
            "confidence": 0.91,
        }

    monkeypatch.setattr(fact_extractor_module, "_llm_extract_issue", _mock_llm_extract_issue)
    monkeypatch.setattr(fact_extractor_module, "analyze_image", lambda **_kwargs: {"visual_description": "未见可判定瑕疵"})

    result = extract(
        {
            "order_id": "ORDER10013ABN",
            "buyer_text": "物流一直不更新",
            "chat_history": [{"role": "buyer", "content": "麻烦尽快处理"}],
            "image_urls": ["https://example.com/evidence/custom.jpg"],
        }
    )
    assert "物流异常" in result.intent_tags
    assert result.goods_received is False


# ---------- 场景：视觉分析应使用事实 LLM 提炼后的诉求作为 guidance ----------
def test_extract_vision_should_anchor_on_issue_summary(monkeypatch):
    captured_guidance: list[str] = []

    def _mock_llm_extract_issue(**_kwargs):
        return {
            "issue_summary": "买家反映香蕉收到即严重褐变发黑",
            "intent_tags": ["质量问题", "退款诉求"],
            "confidence": 0.9,
        }

    def _mock_analyze_image(**kwargs):
        captured_guidance.append(str(kwargs.get("guidance", "")))
        return {"visual_description": "图中香蕉大面积褐变", "findings": ["表皮黑斑明显"]}

    monkeypatch.setattr(fact_extractor_module, "_llm_extract_issue", _mock_llm_extract_issue)
    monkeypatch.setattr(fact_extractor_module, "analyze_image", _mock_analyze_image)

    extract(
        {
            "order_id": "ORDER10015",
            "buyer_text": "水果有问题",
            "chat_history": [{"role": "buyer", "content": "你看图"}],
            "image_urls": ["https://example.com/evidence/banana.jpg"],
        }
    )
    assert captured_guidance
    assert "严重褐变发黑" in captured_guidance[0]
    assert "质量问题" in captured_guidance[0]


# ---------- 场景：可疑图源线索由模型直接写入视觉描述 ----------
def test_extract_should_keep_external_source_clue_in_visual_observations(monkeypatch):
    monkeypatch.setattr(
        fact_extractor_module,
        "analyze_image",
        lambda **_kwargs: {
            "visual_description": "图像右下角有 sohu.com 水印",
            "findings": ["图片带有网址水印，疑似网络公开图片"],
            "credential_trust": "suspect",
            "credential_trust_note": "图片角落可见门户网站水印，疑似网图",
        },
    )

    result = extract(
        {
            "order_id": "ORDER10014",
            "buyer_text": "香蕉发霉了",
            "chat_history": [{"role": "buyer", "content": "这是我拍的图"}],
            "image_urls": ["https://example.com/evidence/custom.jpg"],
        }
    )
    assert any("水印" in item or "疑似网络公开图片" in item for item in result.visual_observations)
    assert result.credential_trust == "suspect"
