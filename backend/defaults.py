"""
本地开发默认标识。

职责：当前端未传 merchant_id / dispute_id 时，由 API 层注入占位值。
"""

from __future__ import annotations

import os


# ---------- 默认商家 ID：设置页与 /analyze 共用 ----------
def default_merchant_id() -> str:
    """
    读取默认商家编号，环境变量未配置时回退为 default。
    """
    value = os.getenv("DEFAULT_MERCHANT_ID", "default").strip()
    return value or "default"


# ---------- 默认纠纷 ID：单页辅助模式会话缓存键 ----------
def default_dispute_id() -> str:
    """
    读取默认纠纷编号，环境变量未配置时回退为 local-dispute。
    """
    value = os.getenv("DEFAULT_DISPUTE_ID", "local-dispute").strip()
    return value or "local-dispute"
