"""
Agent 1 对外入口。

仅导出 `extract`，供 Controller 调用；实现细节见 `fact_extractor`。
"""

from backend.agents.agent1.fact_extractor import extract

__all__ = ["extract"]
