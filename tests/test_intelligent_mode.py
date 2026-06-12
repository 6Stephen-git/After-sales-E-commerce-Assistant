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
    IntelligentContext,
    IntelligentState,
    KeyDecision,
    UpdateStateInput,
    ToolCallLog,
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

        should, reason = check_handoff_threshold(
            buyer_message="转人工，我要找人工客服",
        )
        assert should is True
        assert "转人工" in reason or "人工" in reason

    def test_handoff_when_amount_exceeds_threshold(self):
        """订单金额超阈值时应触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, reason = check_handoff_threshold(order_amount=600.0)
        assert should is True
        assert "600" in reason

    def test_handoff_when_high_risk_malicious(self):
        """高风险 + 可疑/恶意买家应触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, reason = check_handoff_threshold(
            risk_level="high",
            buyer_type="malicious",
        )
        assert should is True
        assert "恶意" in reason

    def test_handoff_when_too_many_rounds(self):
        """对话轮次过多无进展应触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, reason = check_handoff_threshold(round_count=7)
        assert should is True
        assert "7" in reason or "轮" in reason

    def test_no_handoff_for_normal_low_amount(self):
        """正常低金额对话不触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, reason = check_handoff_threshold(
            order_amount=50.0,
            buyer_type="normal",
            risk_level="low",
            buyer_message="你好，我的商品有点问题",
            round_count=1,
        )
        assert should is False
        assert reason == ""

    def test_handoff_on_complaint_keyword(self):
        """买家提及投诉相关关键词应触发转人工。"""
        from backend.tools.intelligent_tools import check_handoff_threshold

        should, _ = check_handoff_threshold(buyer_message="你们不处理我就投诉12315")
        assert should is True


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


# ---------- 工具调用日志测试 ----------
class TestToolCallLog:
    """log_tool_call 核心契约。"""

    def test_log_appends_tool_call(self):
        """工具调用日志应正确追加。"""
        from backend.tools.intelligent_tools import log_tool_call

        state = IntelligentState(dispute_id="test_002")
        new_state = log_tool_call(state, "query_buyer_profile", 0, "credit=high")
        assert len(new_state.tool_calls_log) == 1
        assert new_state.tool_calls_log[0].tool == "query_buyer_profile"
        assert new_state.tool_calls_log[0].result_summary == "credit=high"

    def test_log_preserves_existing_entries(self):
        """新日志应追加而非覆盖。"""
        from backend.tools.intelligent_tools import log_tool_call

        state = IntelligentState(dispute_id="test_002")
        state = log_tool_call(state, "query_buyer_profile", 0)
        state = log_tool_call(state, "analyze_image_simple", 1, "破损")
        assert len(state.tool_calls_log) == 2
        assert state.tool_calls_log[1].tool == "analyze_image_simple"


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
            tool_calls_log=[ToolCallLog(tool="query_buyer_profile", turn=0, result_summary="ok")],
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
        assert len(restored.tool_calls_log) == 1


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

    def test_update_state_tool(self):
        """update_state 工具应正确更新状态。"""
        from backend.agents.conversation_agent import _execute_tool_call

        ctx = self._make_context()
        result_text, new_state = _execute_tool_call(
            "update_state",
            {
                "phase": "settlement",
                "responsibility": "merchant_fault",
                "update_reason": "证据确认商责",
            },
            ctx,
            round_count=0,
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
        result_text, new_state = _execute_tool_call(
            "query_buyer_profile",
            {"buyer_id": "buyer_exec", "merchant_id": "merchant_exec"},
            ctx,
            round_count=0,
        )
        result = json.loads(result_text)
        assert result["buyer_id"] == "buyer_exec"
        assert result["credit_level"] == "high"

    def test_unknown_tool_returns_error(self):
        """未知工具应返回错误信息。"""
        from backend.agents.conversation_agent import _execute_tool_call

        ctx = self._make_context()
        result_text, _ = _execute_tool_call("nonexistent_tool", {}, ctx, 0)
        result = json.loads(result_text)
        assert "error" in result
        assert "未知工具" in result["error"]

    def test_query_logistics_returns_placeholder(self):
        """物流查询暂返回占位数据。"""
        from backend.agents.conversation_agent import _execute_tool_call

        ctx = self._make_context()
        result_text, _ = _execute_tool_call(
            "query_logistics",
            {"order_id": "ORD-999"},
            ctx,
            round_count=0,
        )
        result = json.loads(result_text)
        assert result["order_id"] == "ORD-999"
        assert "暂无" in result["status"] or "待接入" in result["note"]


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
