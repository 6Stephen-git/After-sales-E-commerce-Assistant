"""
平台 API 工具封装。

说明：真实千牛等平台接口接入后，仅替换本模块内部实现；Agent 仍只调用 query_logistics。
本地测试时优先读取 data/intelligent_simulation.json 中的模拟物流。
"""

from __future__ import annotations

import logging

from backend.tools.simulation_fixture import get_simulated_logistics
from schemas import LogisticsInfo

LOG_PREFIX = "[PlatformAPI]"
logger = logging.getLogger(__name__)


def query_logistics(order_id: str) -> LogisticsInfo:
    """
    根据订单号查询物流状态摘要。

    参数:
        order_id: 平台订单号字符串。

    返回:
        LogisticsInfo，字段含义见 schemas.py。
    """
    normalized = str(order_id or "").strip()
    if normalized:
        simulated = get_simulated_logistics(normalized)
        if simulated is not None:
            logger.info("%s 使用模拟物流 order_id=%s", LOG_PREFIX, normalized)
            return simulated

    return LogisticsInfo(
        is_shipped=False,
        is_signed=False,
        stagnant_days=0,
        is_abnormal=False,
    )
