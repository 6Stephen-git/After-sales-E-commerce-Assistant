"""
Agent4（情绪监控员）模块测试
覆盖：情绪标签归一化、预警阈值、历史负面轨迹与工具层兜底
"""

import os
import sys


# ---------- 与仓库根对齐的导入路径 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


from backend.agents.agent4.emotion_monitor import monitor
from backend.tools.agent4_tools import analyze_sentiment


# ---------- monitor：强负面预警、温和负面、历史负面转缓和、空文本边界 ----------
class TestAgent4Monitor:
    def test_monitor_should_trigger_alert_for_high_negative_intensity(self):
        """强负面文本应触发预警。"""
        output = monitor(
            text="太差了！我要投诉平台！",
            context={"alert_threshold": 0.8, "chat_history": ["一直不处理", "非常失望"]},
        )
        assert output.sentiment == "negative"
        assert output.alert_triggered is True
        assert output.alert_reason and "超过阈值" in output.alert_reason
        assert output.emotion_note and "升级投诉" in output.emotion_note

    def test_monitor_should_not_trigger_alert_for_medium_negative(self):
        """中等负面强度不应触发预警。"""
        output = monitor(
            text="衣服有问题，我不满意，想确认怎么处理",
            context={"alert_threshold": 0.95, "chat_history": ["物流到了但不太满意"]},
        )
        assert output.sentiment in {"negative", "neutral"}
        assert output.alert_triggered is False
        assert output.alert_message == ""

    def test_monitor_should_include_reluctant_note_for_positive_with_negative_trace(self):
        """历史有负面轨迹时，缓和文本应输出“勉强”语义。"""
        output = monitor(
            text="好吧，先这样吧",
            context={
                "chat_history": ["这次购物很失望，准备投诉", "你们一直没给方案"],
                "alert_threshold": 0.8,
            },
        )
        assert output.sentiment == "positive"
        assert output.alert_triggered is False
        assert output.emotion_note and "勉强" in output.emotion_note

    def test_monitor_boundary_should_return_neutral_for_empty_text(self):
        """空文本边界：应返回 neutral 且不触发预警。"""
        output = monitor(text="", context={})
        assert output.sentiment == "neutral"
        assert output.intensity == 0.0
        assert output.alert_triggered is False


# ---------- 工具层：标签统一与关键词兜底 ----------
class TestAgent4Tools:
    def test_analyze_sentiment_should_fallback_to_negative_keywords(self):
        """模型不可用时，负面关键词应命中 negative。"""
        result = analyze_sentiment("太差了，质量很烂，我要退货")
        assert result["label"] == "negative"
        assert 0.0 <= result["intensity"] <= 1.0

    def test_analyze_sentiment_should_fallback_to_positive_keywords(self):
        """模型不可用时，正面关键词应命中 positive。"""
        result = analyze_sentiment("谢谢你们，问题已经解决了，可以接受")
        assert result["label"] == "positive"
        assert 0.0 <= result["intensity"] <= 1.0
