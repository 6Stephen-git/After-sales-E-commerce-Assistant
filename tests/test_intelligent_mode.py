"""
智能模式端到端契约测试。

覆盖：
- conversation_agent.chat 核心流程（LLM mock）
- 转人工阈值判断
- 状态更新工具
- 上下文构建与序列化
- 工具调用执行
- 控制器入口
"""

import json
import os
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------- 路径对齐 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 确保不连接真实 Redis
os.environ["ENABLE_REDIS_CACHE"] = "0"

from schemas import (
    AgentReply,
    ChatTurn,
    EvidenceSummary,
    FactOutput,
    IntelligentContext,
    IntelligentState,
    KeyDecision,
    UpdateStateInput,
    ToolFinding,
    INTEL_PHASE_EVIDENCE,
    INTEL_PHASE_HANDOFF,
    INTEL_PHASE_SETTLE,
    INTEL_STRATEGY_COLLECT_EVIDENCE,
    INTEL_STRATEGY_COMPENSATE,
    RESPONSIBILITY_UNCLEAR,
    RESPONSIBILITY_MERCHANT,
    BUYER_TYPE_NORMAL,
    BUYER_TYPE_HIGH_VALUE_OLD,
    RISK_LOW,
    RISK_HIGH,
)


# ---------- 转人工阈值测试 ----------
class TestHandoffThreshold:
    """check_handoff_threshold 核心契约。"""

    def test_handoff_when_buyer_demands_human(self):
        """买家明确要求转人工时应触发。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, suggested, reason = check_handoff_threshold(
            buyer_message="转人工，我要找人工客服",
        )
        assert should is True
        assert suggested is False
        assert "转人工" in reason or "人工" in reason

    def test_handoff_when_amount_exceeds_threshold(self):
        """订单金额超阈值时应触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, suggested, reason = check_handoff_threshold(order_amount=600.0)
        assert should is True
        assert suggested is False
        assert "600" in reason

    def test_handoff_when_high_risk_malicious(self):
        """高风险 + 可疑/恶意买家应触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, suggested, reason = check_handoff_threshold(
            risk_level="high",
            buyer_type="malicious",
        )
        assert should is True
        assert suggested is False
        assert "恶意" in reason

    def test_suggest_handoff_when_too_many_rounds(self):
        """对话轮次过多应建议转人工而非强制。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, suggested, reason = check_handoff_threshold(round_count=7)
        assert should is False
        assert suggested is True
        assert "7" in reason or "轮" in reason

    def test_no_handoff_for_normal_low_amount(self):
        """正常低金额对话不触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, suggested, reason = check_handoff_threshold(
            order_amount=50.0,
            buyer_type="normal",
            risk_level="low",
            buyer_message="你好，我的商品有点问题",
            round_count=1,
        )
        assert should is False
        assert suggested is False
        assert reason == ""

    def test_handoff_on_complaint_keyword(self):
        """买家提及投诉相关关键词应触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, suggested, _ = check_handoff_threshold(buyer_message="你们不处理我就投诉12315")
        assert should is True
        assert suggested is False

    def test_dismiss_round_handoff_skips_suggestion(self):
        """用户选择继续后不再因轮次建议转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, suggested, reason = check_handoff_threshold(
            round_count=10,
            dismiss_round_handoff=True,
        )
        assert should is False
        assert suggested is False
        assert reason == ""


# ---------- 状态更新工具测试 ----------
class TestUpdateState:
    """update_state 工具的核心契约。"""

    def _base_state(self) -> IntelligentState:
        return IntelligentState(dispute_id="test_001")

    def test_update_phase_and_responsibility(self):
        """应正确更新阶段和责任归属。"""
        from backend.tools.intelligent_tools import update_state

        state = self._base_state()
        update_input = UpdateStateInput(
            dispute_id="test_001",
            phase=INTEL_PHASE_SETTLE,
            responsibility=RESPONSIBILITY_MERCHANT,
            update_reason="证据确认商责",
        )
        new_state = update_state(state, update_input)
        assert new_state.phase == INTEL_PHASE_SETTLE
        assert new_state.responsibility == RESPONSIBILITY_MERCHANT
        assert new_state.last_update_reason == "证据确认商责"

    def test_update_preserves_unspecified_fields(self):
        """未指定的字段应保留原值。"""
        from backend.tools.intelligent_tools import update_state

        state = self._base_state()
        update_input = UpdateStateInput(
            dispute_id="test_001",
            buyer_type=BUYER_TYPE_HIGH_VALUE_OLD,
            update_reason="识别高价值老客",
        )
        new_state = update_state(state, update_input)
        assert new_state.buyer_type == BUYER_TYPE_HIGH_VALUE_OLD
        assert new_state.phase == INTEL_PHASE_EVIDENCE  # 未变
        assert new_state.responsibility == RESPONSIBILITY_UNCLEAR  # 未变

    def test_update_ignores_invalid_phase(self):
        """无效阶段值应被忽略。"""
        from backend.tools.intelligent_tools import update_state

        state = self._base_state()
        update_input = UpdateStateInput(
            dispute_id="test_001",
            phase="invalid_phase",
            update_reason="测试无效值",
        )
        new_state = update_state(state, update_input)
        assert new_state.phase == INTEL_PHASE_EVIDENCE  # 保持原值

    def test_update_appends_key_decision(self):
        """关键决策应追加到列表。"""
        from backend.tools.intelligent_tools import update_state

        state = self._base_state()
        decision = KeyDecision(turn=1, decision="商责善后", reason="图片清晰")
        update_input = UpdateStateInput(
            dispute_id="test_001",
            key_decision=decision,
            update_reason="做出决策",
        )
        new_state = update_state(state, update_input)
        assert len(new_state.key_decisions) == 1
        assert new_state.key_decisions[0].decision == "商责善后"

    def test_update_merges_evidence_collected(self):
        """已收集证据应合并而非覆盖。"""
        from backend.tools.intelligent_tools import update_state

        state = IntelligentState(
            dispute_id="test_001",
            evidence_summary=EvidenceSummary(collected=["照片"], missing=["视频"]),
        )
        from schemas import EvidenceSummaryInput

        update_input = UpdateStateInput(
            dispute_id="test_001",
            evidence_summary=EvidenceSummaryInput(collected=["物流单号"]),
            update_reason="新增证据",
        )
        new_state = update_state(state, update_input)
        assert "照片" in new_state.evidence_summary.collected
        assert "物流单号" in new_state.evidence_summary.collected

    def test_update_returns_new_instance(self):
        """update_state 应返回新实例，不修改原对象。"""
        from backend.tools.intelligent_tools import update_state

        state = self._base_state()
        update_input = UpdateStateInput(
            dispute_id="test_001",
            phase=INTEL_PHASE_SETTLE,
            update_reason="测试不可变性",
        )
        new_state = update_state(state, update_input)
        assert state.phase == INTEL_PHASE_EVIDENCE  # 原对象不变
        assert new_state.phase == INTEL_PHASE_SETTLE  # 新对象已变

    def test_update_can_clear_missing_with_empty_list(self):
        """传入 missing=[] 应清空缺失项，而非因空列表被忽略。"""
        from backend.tools.intelligent_tools import update_state
        from schemas import EvidenceSummaryInput

        state = IntelligentState(
            dispute_id="test_001",
            evidence_summary=EvidenceSummary(collected=[], missing=["买家举证图片"]),
        )
        update_input = UpdateStateInput(
            dispute_id="test_001",
            evidence_summary=EvidenceSummaryInput(collected=["买家举证图片"], missing=[]),
            update_reason="核销缺证",
        )
        new_state = update_state(state, update_input)
        assert new_state.evidence_summary.missing == []


class TestEvidenceStateSync:
    """工具结论自动写入 IntelligentState 的契约。"""

    def test_sync_state_after_vision_clears_generic_missing(self):
        from backend.tools.intelligent_tools import sync_state_after_vision

        state = IntelligentState(
            dispute_id="vision_001",
            evidence_summary=EvidenceSummary(
                collected=[],
                missing=["买家举证图片", "商品实物照片", "开箱视频"],
            ),
        )
        vision = {
            "visual_description": "书角明显翘边",
            "defect_type": "翘边",
            "visual_defect_severity": "moderate",
        }
        new_state = sync_state_after_vision(
            state,
            vision,
            update_reason="测试视觉同步",
        )
        assert any("买家举证图片" in item for item in new_state.evidence_summary.collected)
        assert "买家举证图片" not in new_state.evidence_summary.missing
        assert "商品实物照片" not in new_state.evidence_summary.missing
        assert "开箱视频" in new_state.evidence_summary.missing

    def test_sync_state_after_vision_skips_error(self):
        from backend.tools.intelligent_tools import sync_state_after_vision

        state = IntelligentState(dispute_id="vision_002")
        unchanged = sync_state_after_vision(state, {"error": "失败"}, update_reason="x")
        assert unchanged.model_dump() == state.model_dump()

    def test_sync_state_after_logistics_adds_collected(self):
        from backend.tools.intelligent_tools import sync_state_after_logistics

        state = IntelligentState(
            dispute_id="logistics_001",
            evidence_summary=EvidenceSummary(missing=["物流状态"]),
        )
        new_state = sync_state_after_logistics(
            state,
            {"order_id": "9999", "is_signed": True},
        )
        assert "物流状态" in new_state.evidence_summary.collected
        assert "物流状态" not in new_state.evidence_summary.missing


class TestToolFindings:
    """工具事实层：record_tool_finding 与跨轮 prompt 注入。"""

    def test_record_tool_finding_appends_vision(self):
        from backend.tools.intelligent_tools import record_tool_finding

        state = IntelligentState(dispute_id="tf_001")
        vision = {
            "visual_description": "笔记本边角翘起",
            "defect_type": "翘边",
            "visual_defect_severity": "moderate",
        }
        new_state = record_tool_finding(
            state,
            "analyze_image_simple",
            1,
            vision,
            extra_facts={"image_url": "data:image/png;base64,abc"},
        )
        assert len(new_state.tool_findings) == 1
        assert new_state.tool_findings[0].tool == "analyze_image_simple"
        assert "翘边" in new_state.tool_findings[0].summary
        assert new_state.tool_findings[0].facts.get("defect_type") == "翘边"

    def test_format_tool_findings_for_prompt(self):
        from backend.tools.intelligent_tools import format_tool_findings_for_prompt, record_tool_finding

        state = IntelligentState(dispute_id="tf_002")
        state = record_tool_finding(
            state,
            "query_logistics",
            0,
            {"order_id": "9999", "is_signed": True, "status_text": "已签收1天"},
        )
        text = format_tool_findings_for_prompt(state)
        assert "已确认的工具事实" in text
        assert "query_logistics" in text
        assert "9999" in text

    def test_summarize_logistics_with_status_text(self):
        from backend.tools.intelligent_tools import summarize_tool_result

        summary, facts = summarize_tool_result(
            "query_logistics",
            {"order_id": "9999", "is_signed": True, "status_text": "已签收1天"},
        )
        assert "9999" in summary
        assert "签收" in summary
        assert facts.get("order_id") == "9999"

    def test_record_tool_finding_appends_without_overwrite(self):
        from backend.tools.intelligent_tools import record_tool_finding

        state = IntelligentState(dispute_id="tf_003")
        state = record_tool_finding(state, "query_buyer_profile", 0, {"credit_level": "高"})
        state = record_tool_finding(
            state,
            "analyze_image_simple",
            1,
            {"visual_description": "破损", "defect_type": "破损"},
        )
        assert len(state.tool_findings) == 2
        assert state.tool_findings[1].tool == "analyze_image_simple"

    def test_record_tool_finding_syncs_evidence_for_vision(self):
        from backend.tools.intelligent_tools import record_tool_finding

        state = IntelligentState(
            dispute_id="tf_004",
            evidence_summary=EvidenceSummary(missing=["买家举证图片"]),
        )
        state = record_tool_finding(
            state,
            "analyze_image_simple",
            0,
            {"visual_description": "翘边", "defect_type": "翘边"},
        )
        assert state.tool_findings
        assert "买家举证图片" not in state.evidence_summary.missing


class TestBuyerClaimFromContext:
    """视觉锚点文本合成。"""

    def test_build_buyer_claim_skips_image_placeholder(self):
        from backend.agents.conversation_agent import _build_buyer_claim_from_context

        context = IntelligentContext(
            dispute_id="claim_001",
            chat_history=[
                ChatTurn(role="buyer", content="本子翘边"),
                ChatTurn(role="merchant", content="请发近照"),
                ChatTurn(role="buyer", content="9999"),
                ChatTurn(role="buyer", content="[图片]"),
            ],
            current_state=IntelligentState(dispute_id="claim_001"),
        )
        claim = _build_buyer_claim_from_context(context)
        assert "本子翘边" in claim
        assert "9999" in claim
        assert "[图片]" not in claim


class TestResolveAnalyzeImageUrl:
    """附图 URL 服务端解析。"""

    def test_resolve_by_image_index(self):
        from backend.agents.conversation_agent import _resolve_analyze_image_url

        pending = ["data:image/png;base64,abc", "https://example.com/b.jpg"]
        url = _resolve_analyze_image_url(
            pending_image_urls=pending,
            arguments={"image_index": 2},
        )
        assert url == "https://example.com/b.jpg"

    def test_resolve_single_pending_when_llm_passes_truncated_url(self):
        from backend.agents.conversation_agent import _resolve_analyze_image_url

        full = "data:image/jpeg;base64," + ("A" * 200)
        truncated = full[:117] + "..."
        url = _resolve_analyze_image_url(
            pending_image_urls=[full],
            arguments={"image_url": truncated},
        )
        assert url == full


class TestPhaseStageHint:
    """阶段导航动态提醒。"""

    def test_evidence_phase_with_missing_warns_no_settlement(self):
        from backend.agents.conversation_agent import _format_phase_stage_hint

        context = IntelligentContext(
            dispute_id="phase_001",
            current_state=IntelligentState(
                dispute_id="phase_001",
                phase="evidence_collection",
                evidence_summary=EvidenceSummary(
                    missing=["开箱连续视频", "内页使用痕迹"],
                ),
            ),
        )
        hint = _format_phase_stage_hint(context)
        assert "勿承诺退款" in hint
        assert "过关自问" in hint

    def test_evidence_phase_suggests_profile_and_logistics(self):
        from backend.agents.conversation_agent import _format_phase_stage_hint

        context = IntelligentContext(
            dispute_id="phase_003",
            buyer_id="buyer_01",
            order_id="9999",
            merchant_id="m1",
            current_state=IntelligentState(
                dispute_id="phase_003",
                phase="evidence_collection",
            ),
        )
        hint = _format_phase_stage_hint(context)
        assert "query_buyer_profile" in hint
        assert "query_logistics" in hint
        assert "match_rules_simple" in hint


# ---------- 构建转人工摘要测试 ----------
class TestHandoffSummary:
    """build_handoff_summary 核心契约。"""

    def test_summary_contains_key_fields(self):
        """摘要应包含案件状态关键信息。"""
        from backend.tools.intelligent_tools import build_handoff_summary

        state = IntelligentState(
            dispute_id="test_003",
            phase=INTEL_PHASE_EVIDENCE,
            responsibility=RESPONSIBILITY_MERCHANT,
            current_strategy=INTEL_STRATEGY_COMPENSATE,
            strategy_rationale="商责明确",
            risk_signals=["买家情绪激动"],
            evidence_summary=EvidenceSummary(collected=["照片"], missing=["视频"]),
        )
        summary = build_handoff_summary(state)
        assert "evidence_collection" in summary or "证据" in summary
        assert "merchant_fault" in summary
        assert "照片" in summary
        assert "视频" in summary
        assert "情绪激动" in summary


# ---------- 上下文构建测试 ----------
class TestContextBuilding:
    """build_initial_context 核心契约。"""

    def test_build_context_with_new_state(self):
        """新纠纷应创建默认 IntelligentState。"""
        from backend.agents.conversation_agent.context import build_initial_context

        ctx = build_initial_context(
            dispute_id="ctx_001",
            buyer_message="商品有问题",
            order_amount=100.0,
        )
        assert ctx.dispute_id == "ctx_001"
        assert ctx.current_state.dispute_id == "ctx_001"
        assert ctx.current_state.phase == INTEL_PHASE_EVIDENCE
        assert len(ctx.chat_history) == 1
        assert ctx.chat_history[0].role == "buyer"
        assert ctx.chat_history[0].content == "商品有问题"

    def test_build_context_preserves_order_info(self):
        """订单信息应正确传入上下文。"""
        from backend.agents.conversation_agent.context import build_initial_context

        ctx = build_initial_context(
            dispute_id="ctx_002",
            order_id="ORD-123",
            order_amount=299.0,
            buyer_id="buyer_a",
            merchant_id="merchant_b",
            max_compensation=50.0,
        )
        assert ctx.order_id == "ORD-123"
        assert ctx.order_amount == 299.0
        assert ctx.buyer_id == "buyer_a"
        assert ctx.merchant_id == "merchant_b"
        assert ctx.max_compensation == 50.0

    def test_build_context_with_empty_message(self):
        """空消息不应添加到对话历史。"""
        from backend.agents.conversation_agent.context import build_initial_context

        ctx = build_initial_context(dispute_id="ctx_003", buyer_message="")
        assert len(ctx.chat_history) == 0


# ---------- 状态序列化测试 ----------
class TestStateSerialization:
    """IntelligentState JSON 序列化/反序列化。"""

    def test_roundtrip_serialization(self):
        """序列化后反序列化应保持一致。"""
        state = IntelligentState(
            dispute_id="ser_001",
            phase=INTEL_PHASE_SETTLE,
            responsibility=RESPONSIBILITY_MERCHANT,
            current_strategy=INTEL_STRATEGY_COMPENSATE,
            strategy_rationale="商责确认",
            buyer_type=BUYER_TYPE_HIGH_VALUE_OLD,
            evidence_summary=EvidenceSummary(collected=["照片", "物流单"], missing=["视频"]),
            risk_level=RISK_LOW,
            risk_signals=["信号A"],
            key_decisions=[KeyDecision(turn=1, decision="善后", reason="商责")],
            tool_findings=[
                ToolFinding(tool="query_buyer_profile", turn=0, summary="信誉=高", facts={"credit_level": "高"}),
            ],
            last_update_reason="测试序列化",
        )
        json_str = state.model_dump_json()
        restored = IntelligentState.model_validate_json(json_str)

        assert restored.dispute_id == "ser_001"
        assert restored.phase == INTEL_PHASE_SETTLE
        assert restored.responsibility == RESPONSIBILITY_MERCHANT
        assert restored.buyer_type == BUYER_TYPE_HIGH_VALUE_OLD
        assert len(restored.evidence_summary.collected) == 2
        assert len(restored.key_decisions) == 1
        assert restored.key_decisions[0].decision == "善后"
        assert len(restored.tool_findings) == 1
        assert restored.tool_findings[0].tool == "query_buyer_profile"


# ---------- conversation_agent 工具执行测试 ----------
class TestToolExecution:
    """_execute_tool_call 核心契约（LLM mock）。"""

    def _make_context(self) -> IntelligentContext:
        return IntelligentContext(
            dispute_id="exec_001",
            chat_history=[ChatTurn(role="buyer", content="商品破损了")],
            order_id="ORD-999",
            order_amount=150.0,
            buyer_id="buyer_exec",
            merchant_id="merchant_exec",
            current_state=IntelligentState(dispute_id="exec_001"),
        )

    def _make_facts(self) -> FactOutput:
        return FactOutput(
            issue_summary="商品破损了",
            evidence_quality="low",
            confidence=0.3,
        )

    def test_update_state_tool(self):
        """update_state 工具应正确更新状态。"""
        from backend.agents.conversation_agent import _execute_tool_call

        ctx = self._make_context()
        result_text, new_state, _facts = _execute_tool_call(
            "update_state",
            {
                "phase": "settlement",
                "responsibility": "merchant_fault",
                "update_reason": "证据确认商责",
            },
            ctx,
            round_count=0,
            accumulated_facts=self._make_facts(),
        )
        result = json.loads(result_text)
        assert result["status"] == "updated"
        assert result["phase"] == "settlement"
        assert new_state.responsibility == "merchant_fault"

    def test_query_buyer_profile_tool(self, monkeypatch):
        """query_buyer_profile 工具应返回买家画像。"""
        from backend.agents import conversation_agent as ca_module
        from backend.agents.conversation_agent import _execute_tool_call
        from schemas import BuyerProfile

        monkeypatch.setattr(
            ca_module,
            "query_buyer_profile",
            lambda buyer_id, merchant_id="": BuyerProfile(
                buyer_id=buyer_id, credit_level="high", purchase_count=10
            ),
        )

        ctx = self._make_context()
        result_text, new_state, _facts = _execute_tool_call(
            "query_buyer_profile",
            {"buyer_id": "buyer_exec", "merchant_id": "merchant_exec"},
            ctx,
            round_count=0,
            accumulated_facts=self._make_facts(),
        )
        result = json.loads(result_text)
        assert result["buyer_id"] == "buyer_exec"
        assert result["credit_level"] == "high"

    def test_unknown_tool_returns_error(self):
        """未知工具应返回错误信息。"""
        from backend.agents.conversation_agent import _execute_tool_call

        ctx = self._make_context()
        result_text, _state, _facts = _execute_tool_call(
            "nonexistent_tool", {}, ctx, 0, accumulated_facts=self._make_facts(),
        )
        result = json.loads(result_text)
        assert "error" in result
        assert "未知工具" in result["error"]

    def test_query_logistics_returns_structured_data(self):
        """物流查询返回结构化字段。"""
        from backend.agents.conversation_agent import _execute_tool_call

        ctx = self._make_context()
        result_text, _state, _facts = _execute_tool_call(
            "query_logistics",
            {"order_id": "ORD-999"},
            ctx,
            round_count=0,
            accumulated_facts=self._make_facts(),
        )
        result = json.loads(result_text)
        assert result["order_id"] == "ORD-999"
        assert "is_shipped" in result
        assert "is_signed" in result


# ---------- conversation_agent.chat 核心测试 ----------
class TestConversationAgentChat:
    """conversation_agent.chat 端到端流程（mock LLM + Redis）。"""

    def _mock_llm_reply(self, reply_text: str):
        """构造一个无工具调用的 LLM assistant 消息。"""
        return {
            "role": "assistant",
            "content": reply_text,
            "tool_calls": None,
        }

    def test_chat_returns_reply(self, monkeypatch):
        """正常对话应返回 AgentReply。"""
        from backend.agents import conversation_agent

        monkeypatch.setattr(
            conversation_agent,
            "chat_completion_assistant_message",
            lambda **kwargs: self._mock_llm_reply("亲亲不好意思，我马上帮您看一下哈"),
        )

        reply = conversation_agent.chat(
            buyer_message="我的商品破损了",
            dispute_id="chat_001",
            order_amount=80.0,
            buyer_id="buyer_001",
        )
        assert isinstance(reply, AgentReply)
        assert reply.reply_text
        assert reply.handoff is False
        assert reply.state.dispute_id == "chat_001"

    def test_chat_returns_fallback_on_llm_failure(self, monkeypatch):
        """LLM 失败时应返回兜底话术。"""
        from backend.agents import conversation_agent

        monkeypatch.setattr(
            conversation_agent,
            "chat_completion_assistant_message",
            lambda **kwargs: None,
        )

        reply = conversation_agent.chat(
            buyer_message="商品有问题",
            dispute_id="chat_002",
        )
        assert isinstance(reply, AgentReply)
        assert "系统" in reply.reply_text or "稍等" in reply.reply_text
        assert reply.handoff is False

    def test_chat_triggers_handoff_on_high_amount(self, monkeypatch):
        """高额订单应触发转人工。"""
        from backend.agents import conversation_agent

        monkeypatch.setattr(
            conversation_agent,
            "chat_completion_assistant_message",
            lambda **kwargs: self._mock_llm_reply("不应到达此处"),
        )

        reply = conversation_agent.chat(
            buyer_message="商品有问题",
            dispute_id="chat_003",
            order_amount=800.0,
        )
        assert reply.handoff is True
        assert reply.handoff_reason
        assert reply.state.phase == INTEL_PHASE_HANDOFF

    def test_chat_triggers_handoff_on_buyer_demand(self, monkeypatch):
        """买家要求转人工应触发。"""
        from backend.agents import conversation_agent

        monkeypatch.setattr(
            conversation_agent,
            "chat_completion_assistant_message",
            lambda **kwargs: self._mock_llm_reply("不应到达此处"),
        )

        reply = conversation_agent.chat(
            buyer_message="转人工，我要找人工客服",
            dispute_id="chat_004",
            order_amount=50.0,
        )
        assert reply.handoff is True

    def test_chat_with_tool_calling_round(self, monkeypatch):
        """LLM 调用工具后再回复，应正确执行工具并返回最终回复。"""
        from backend.agents import conversation_agent

        call_count = {"n": 0}

        def mock_llm(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 第一轮：请求工具调用
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": "query_buyer_profile",
                                "arguments": json.dumps(
                                    {"buyer_id": "buyer_005", "merchant_id": "merchant_005"}
                                ),
                            },
                        }
                    ],
                }
            else:
                # 第二轮：最终回复
                return {
                    "role": "assistant",
                    "content": "亲亲我刚查了一下，马上帮您处理哈",
                    "tool_calls": None,
                }

        monkeypatch.setattr(
            conversation_agent,
            "chat_completion_assistant_message",
            mock_llm,
        )

        reply = conversation_agent.chat(
            buyer_message="商品破损了",
            dispute_id="chat_005",
            buyer_id="buyer_005",
            merchant_id="merchant_005",
        )
        assert reply.reply_text == "亲亲我刚查了一下，马上帮您处理哈"
        assert "query_buyer_profile" in reply.tools_called
        assert call_count["n"] == 2

    def test_chat_with_update_state_tool(self, monkeypatch):
        """LLM 调用 update_state 后，状态应正确更新。"""
        from backend.agents import conversation_agent

        call_count = {"n": 0}

        def mock_llm(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_002",
                            "type": "function",
                            "function": {
                                "name": "update_state",
                                "arguments": json.dumps(
                                    {
                                        "responsibility": "merchant_fault",
                                        "current_strategy": "compensate",
                                        "update_reason": "图片确认商责",
                                    }
                                ),
                            },
                        }
                    ],
                }
            else:
                return {
                    "role": "assistant",
                    "content": "真不好意思了哥，商品确实是我们这边出了问题",
                    "tool_calls": None,
                }

        monkeypatch.setattr(
            conversation_agent,
            "chat_completion_assistant_message",
            mock_llm,
        )

        reply = conversation_agent.chat(
            buyer_message="你看这个破洞",
            dispute_id="chat_006",
        )
        assert reply.state.responsibility == "merchant_fault"
        assert reply.state.current_strategy == "compensate"
        assert reply.state_updated is True

    def test_chat_max_tool_rounds_guard(self, monkeypatch):
        """超过最大工具轮次时应终止循环。"""
        from backend.agents import conversation_agent

        def mock_llm(**kwargs):
            # 每轮都返回工具调用，永远不返回最终回复
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_loop",
                        "type": "function",
                        "function": {
                            "name": "query_buyer_profile",
                            "arguments": json.dumps(
                                {"buyer_id": "b", "merchant_id": "m"}
                            ),
                        },
                    }
                ],
            }

        monkeypatch.setattr(
            conversation_agent,
            "chat_completion_assistant_message",
            mock_llm,
        )

        reply = conversation_agent.chat(
            buyer_message="测试循环",
            dispute_id="chat_007",
        )
        # 应有兜底回复或最后一条 assistant content
        assert reply.reply_text
        assert reply.handoff is False

    def test_chat_with_image_syncs_state_and_injects_vision_context(self, monkeypatch):
        """附图时服务端预识图，应写入 tool_findings 与 evidence_summary。"""
        from backend.agents import conversation_agent

        captured: dict[str, Any] = {}
        vision_calls: list[dict[str, Any]] = []

        def mock_vision(image_url: str, buyer_claim: str = "") -> dict[str, Any]:
            vision_calls.append({"image_url": image_url, "buyer_claim": buyer_claim})
            return {
                "visual_description": "笔记本边角翘起",
                "defect_type": "翘边",
                "visual_defect_severity": "moderate",
            }

        def mock_llm(**kwargs):
            messages = kwargs.get("messages") or []
            captured["system"] = messages[0]["content"] if messages else ""
            captured["messages"] = messages
            return {
                "role": "assistant",
                "content": "看到了，翘边问题我这边给您处理",
                "tool_calls": None,
            }

        monkeypatch.setattr(conversation_agent, "analyze_image_simple", mock_vision)
        monkeypatch.setattr(conversation_agent, "chat_completion_assistant_message", mock_llm)

        reply = conversation_agent.chat(
            buyer_message="9999",
            dispute_id="chat_image_001",
            chat_history=[
                ChatTurn(role="buyer", content="本子翘边"),
                ChatTurn(role="merchant", content="请发订单号和近照"),
            ],
            image_urls=["data:image/png;base64,abc"],
            round_count=1,
        )

        assert "analyze_image_simple" in reply.tools_called
        assert reply.state_updated is True
        assert any("买家举证图片" in item for item in reply.state.evidence_summary.collected)
        assert len(reply.state.tool_findings) >= 1
        assert "笔记本边角翘起" in reply.state.tool_findings[-1].summary
        assert "附图1" in captured.get("system", "")
        assert "已完成视觉分析" in captured.get("system", "")
        assert len(vision_calls) == 1
        assert vision_calls[0]["image_url"] == "data:image/png;base64,abc"
        last_user = next(m for m in reversed(captured.get("messages") or []) if m.get("role") == "user")
        assert "本轮买家已附图" in last_user.get("content", "")
        assert "9999" in last_user.get("content", "")

    def test_short_reply_with_image_marks_user_message(self, monkeypatch):
        """短回复「行」+ 附图时，user 消息应标注已附图。"""
        from backend.agents import conversation_agent

        captured: dict[str, Any] = {}

        def mock_vision(image_url: str, buyer_claim: str = "") -> dict[str, Any]:
            return {"visual_description": "耳机插头近景", "defect_type": "使用痕迹"}

        def mock_llm(**kwargs):
            captured["messages"] = kwargs.get("messages") or []
            return {"role": "assistant", "content": "好的哥", "tool_calls": None}

        monkeypatch.setattr(conversation_agent, "analyze_image_simple", mock_vision)
        monkeypatch.setattr(conversation_agent, "chat_completion_assistant_message", mock_llm)
        monkeypatch.setattr(
            "backend.agents.conversation_agent.context.load_state_from_redis",
            lambda _id: None,
        )

        conversation_agent.chat(
            buyer_message="行",
            dispute_id="attach_mark_001",
            chat_history=[
                ChatTurn(role="buyer", content="感觉有人用过"),
                ChatTurn(role="merchant", content="麻烦拍几张近照"),
            ],
            image_urls=["data:image/jpeg;base64,xyz"],
            round_count=3,
        )

        last_user = next(m for m in reversed(captured["messages"]) if m.get("role") == "user")
        assert last_user["content"].startswith("行")
        assert "本轮买家已附图" in last_user["content"]

    def test_cross_round_tool_findings_in_prompt(self, monkeypatch):
        """第二轮应能在 system 中看到第一轮持久化的 tool_findings。"""
        from backend.agents import conversation_agent
        from backend.tools.intelligent_tools import record_tool_finding

        captured: dict[str, Any] = {}
        persisted = IntelligentState(dispute_id="cross_001")
        persisted = record_tool_finding(
            persisted,
            "query_logistics",
            0,
            {"order_id": "9999", "is_signed": True, "status_text": "已签收1天"},
        )

        def mock_load(dispute_id: str):
            if dispute_id == "cross_001":
                return persisted
            return None

        monkeypatch.setattr(
            "backend.agents.conversation_agent.context.load_state_from_redis",
            mock_load,
        )

        def mock_llm(**kwargs):
            messages = kwargs.get("messages") or []
            captured["system"] = messages[0]["content"] if messages else ""
            return {"role": "assistant", "content": "好的", "tool_calls": None}

        monkeypatch.setattr(conversation_agent, "chat_completion_assistant_message", mock_llm)

        reply = conversation_agent.chat(
            buyer_message="单号9999",
            dispute_id="cross_001",
            chat_history=[
                ChatTurn(role="buyer", content="本子翘边"),
                ChatTurn(role="merchant", content="请发订单号"),
            ],
            round_count=1,
        )

        assert "query_logistics" in captured.get("system", "")
        assert "9999" in captured.get("system", "")
        assert reply.state.tool_findings


# ---------- 控制器入口测试 ----------
class TestIntelligentController:
    """run_with_events 核心契约。"""

    def test_run_returns_agent_reply(self, monkeypatch):
        """正常调用应返回 AgentReply。"""
        from backend.controllers import intelligent_controller
        from backend.agents import conversation_agent

        monkeypatch.setattr(
            conversation_agent,
            "chat_completion_assistant_message",
            lambda **kwargs: {
                "role": "assistant",
                "content": "好的收到，马上处理",
                "tool_calls": None,
            },
        )

        reply = intelligent_controller.run_with_events(
            dispute_id="ctrl_001",
            buyer_message="商品有问题",
            order_amount=100.0,
        )
        assert isinstance(reply, AgentReply)
        assert reply.reply_text == "好的收到，马上处理"

    def test_run_rejects_empty_dispute_id(self):
        """空 dispute_id 应抛 ValueError。"""
        from backend.controllers import intelligent_controller

        with pytest.raises(ValueError, match="dispute_id"):
            intelligent_controller.run_with_events(
                dispute_id="",
                buyer_message="你好",
            )

    def test_run_rejects_empty_message(self):
        """空 buyer_message 应抛 ValueError。"""
        from backend.controllers import intelligent_controller

        with pytest.raises(ValueError, match="buyer_message"):
            intelligent_controller.run_with_events(
                dispute_id="ctrl_002",
                buyer_message="",
            )

    def test_run_converts_chat_history(self, monkeypatch):
        """字典格式的 chat_history 应正确转换为 ChatTurn。"""
        from backend.controllers import intelligent_controller
        from backend.agents import conversation_agent

        captured_context = {}

        def mock_chat(*args, **kwargs):
            captured_context["chat_history"] = kwargs.get("chat_history")
            return AgentReply(reply_text="ok")

        monkeypatch.setattr(intelligent_controller, "chat", mock_chat)

        intelligent_controller.run_with_events(
            dispute_id="ctrl_003",
            buyer_message="新消息",
            chat_history=[
                {"role": "buyer", "content": "之前的消息"},
                {"role": "merchant", "content": "客服回复"},
            ],
        )
        history = captured_context["chat_history"]
        assert history is not None
        assert len(history) == 2
        assert history[0].role == "buyer"
        assert history[1].role == "merchant"

    def test_run_with_platform_service_tags(self, monkeypatch):
        """平台服务标标签应透传到 chat 函数。"""
        from backend.controllers import intelligent_controller

        captured = {}

        def mock_chat(*args, **kwargs):
            captured["tags"] = kwargs.get("platform_service_tags")
            captured["max_comp"] = kwargs.get("max_compensation")
            return AgentReply(reply_text="ok")

        monkeypatch.setattr(intelligent_controller, "chat", mock_chat)

        intelligent_controller.run_with_events(
            dispute_id="ctrl_004",
            buyer_message="商品有问题",
            platform_service_tags=["极速退款", "运费险"],
            max_compensation=100.0,
        )
        assert captured["tags"] == ["极速退款", "运费险"]
        assert captured["max_comp"] == 100.0


# ---------- AgentReply 结构测试 ----------
class TestAgentReplyStructure:
    """AgentReply 数据结构契约。"""

    def test_default_agent_reply(self):
        """默认 AgentReply 字段应合法。"""
        reply = AgentReply()
        assert reply.reply_text == ""
        assert reply.state_updated is False
        assert reply.handoff is False
        assert reply.handoff_reason == ""
        assert reply.tools_called == []

    def test_agent_reply_with_all_fields(self):
        """AgentReply 应能携带所有字段。"""
        state = IntelligentState(dispute_id="reply_001")
        reply = AgentReply(
            reply_text="处理好了",
            state_updated=True,
            state=state,
            handoff=False,
            tools_called=["query_buyer_profile", "update_state"],
        )
        assert reply.reply_text == "处理好了"
        assert reply.state_updated is True
        assert len(reply.tools_called) == 2
