"""
缓存层通用工具：材料与指纹模块共享的列表安全转换等。
"""

from __future__ import annotations

from typing import Any


def as_list(value: Any) -> list[Any]:
    """
    将任意值安全转为列表；非 list 返回空列表。
    """
    if isinstance(value, list):
        return value
    return []
