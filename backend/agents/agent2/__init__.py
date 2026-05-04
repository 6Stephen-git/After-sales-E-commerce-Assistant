"""
Agent 2 导出入口。

仅导出 `recommend`；规则/画像/判例数据由 Controller 经 Tools 注入 `StrategyInput`。
"""

from .strategist import recommend

__all__ = ["recommend"]
