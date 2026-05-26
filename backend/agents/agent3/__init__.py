"""
Agent 3 导出入口。

仅导出 `generate`；LLM 话术生成封装在 `backend.tools.agent3_tools`。
"""

from .script_generator import generate

__all__ = ["generate"]
