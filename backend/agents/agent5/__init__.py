"""
Agent 5 导出入口。

仅导出 `review`，供异步任务层或控制器调用。
"""

from .reviewer import review

__all__ = ["review"]
