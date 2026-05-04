"""
Agent 4 对外入口。

仅导出 `monitor`，供 Controller 调用；实现细节见 `emotion_monitor`。
"""

from backend.agents.agent4.emotion_monitor import monitor

__all__ = ["monitor"]
