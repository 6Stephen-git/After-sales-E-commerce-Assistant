"""
Agent4（情绪监控员）核心契约测试。

原则：预警阈值、early_warn 与工具层 LLM/关键词兜底各保留一条路径。
"""

import os
import sys
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.agents.agent4.emotion_monitor import monitor
from backend.tools.agent4_tools import analyze_seller_emotion

_MERCHANT_CTX = {"alert_threshold": 0.8, "chat_history": []}


class TestAgent4Monitor:
    def test_monitor_alert_thresholds(self):
        """攻击性文本触发 alert；克制文本与空文本不触发。"""
        aggressive = monitor(
            text="你爱买不买，随便你投诉，我不承担！",
            context={**_MERCHANT_CTX, "chat_history": [{"role": "buyer", "content": "我要退款"}]},
        )
        assert aggressive.sentiment == "negative"
        assert aggressive.alert_triggered is True
        assert aggressive.alert_reason and "超过阈值" in aggressive.alert_reason

        calm = monitor(
            text="不好意思让您久等了，我这边马上帮您核实处理。",
            context={**_MERCHANT_CTX, "alert_threshold": 0.95},
        )
        assert calm.alert_triggered is False
        assert calm.alert_message == ""

        empty = monitor(text="", context=_MERCHANT_CTX)
        assert empty.sentiment == "neutral"
        assert empty.intensity == 0.0
        assert empty.alert_triggered is False

    def test_monitor_should_trigger_early_warn_for_mild_negative_text(self, monkeypatch):
        """轻度负面情绪应触发 early_warn，供下一条发送前轻确认。"""
        monkeypatch.setattr(
            "backend.agents.agent4.emotion_monitor.analyze_seller_emotion",
            lambda **_: {
                "label": "negative",
                "intensity": 0.62,
                "emotion_note": "您语气偏硬，建议放慢节奏。",
            },
        )
        output = monitor(
            text="这事我已经说了很多遍了，请您看清楚规则。",
            context={**_MERCHANT_CTX, "chat_history": [{"role": "buyer", "content": "我要退款"}]},
        )
        assert output.early_warn_triggered is True
        assert output.alert_triggered is False
        assert output.early_warn_message


class TestAgent4Tools:
    def test_analyze_seller_emotion_should_fallback_to_negative_keywords(self, monkeypatch):
        """LLM 不可用时，卖家负面关键词应命中 negative。"""
        monkeypatch.setattr(
            "backend.tools.agent4_tools._call_seller_emotion_llm",
            lambda **_: None,
        )
        result = analyze_seller_emotion("你爱咋咋，随便你投诉，别烦我")
        assert result["label"] == "negative"
        assert 0.0 <= result["intensity"] <= 1.0
        assert result.get("emotion_note")

    @patch("backend.tools.agent4_tools.chat_completion")
    def test_analyze_seller_emotion_should_parse_llm_json(self, mock_chat):
        """LLM 返回 JSON 时应正确解析。"""
        mock_chat.return_value = (
            '{"sentiment":"negative","intensity":0.91,'
            '"emotion_note":"卖家语气强硬，建议冷静措辞。"}'
        )
        result = analyze_seller_emotion("你懂什么，爱买不买", chat_history=[])
        assert result["label"] == "negative"
        assert result["intensity"] == 0.91
        assert "冷静" in result["emotion_note"]
