"""
Agent 4 对外入口。

仅导出 `monitor`，供情绪 API 调用；实现见 `emotion_monitor`。
"""

from backend.agents.agent4.emotion_monitor import monitor

__all__ = ["monitor"]
