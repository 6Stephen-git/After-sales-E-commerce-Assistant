"""
平台 API 工具封装。

说明：真实千牛等平台接口接入后，仅替换本模块内部实现；Agent 仍只调用 query_logistics。
"""

from __future__ import annotations

import logging

from backend.cache.tool_cache import get_cached_logistics
from schemas import LogisticsInfo

LOG_PREFIX = "[PlatformAPI]"
logger = logging.getLogger(__name__)


def query_logistics(order_id: str, merchant_id: str = "") -> LogisticsInfo:
    """
    根据订单号查询物流状态摘要。

    参数:
        order_id: 平台订单号字符串。
        merchant_id: 商家标识；用于物流缓存租户隔离。

    返回:
        LogisticsInfo，字段含义见 schemas.py。
    """
    normalized = str(order_id or "").strip()
    normalized_merchant = str(merchant_id or "").strip()
    if not normalized:
        logger.info("%s order_id 为空，返回默认物流状态", LOG_PREFIX)
        return LogisticsInfo(
            is_shipped=False,
            is_signed=False,
            stagnant_days=0,
            is_abnormal=False,
        )

    cached = get_cached_logistics(normalized_merchant, normalized)
    if cached is not None:
        return cached

    logistics = LogisticsInfo(
        is_shipped=False,
        is_signed=False,
        stagnant_days=0,
        is_abnormal=False,
    )
    # 当前为未接入平台的占位结果，不写入 Redis，避免将未知状态缓存 15 分钟。
    return logistics
