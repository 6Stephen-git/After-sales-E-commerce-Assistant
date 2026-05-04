"""
平台 API 工具封装（当前为可测试的轻量实现）。

说明：真实千牛等平台接口接入后，仅替换本模块内部实现；Agent 仍只调用 query_logistics。
"""

from __future__ import annotations

from schemas import LogisticsInfo


def query_logistics(order_id: str) -> LogisticsInfo:
    """
    根据订单号查询物流状态摘要（当前为占位实现，便于单测与本地演示）。

    约定：order_id 以 `ABN` 结尾时模拟「已发货、未签收、停滞、异常」；
    空订单号返回全 False/零停滞；其余返回已签收且正常。

    参数:
        order_id: 平台订单号字符串。

    返回:
        LogisticsInfo，字段含义见 schemas.py。
    """
    # 分支一：无订单号 → 视为无物流数据（与 Agent1 缺失证据提示配合）
    if not order_id:
        return LogisticsInfo(
            is_shipped=False,
            is_signed=False,
            stagnant_days=0,
            is_abnormal=False,
        )

    # 分支二：约定后缀 → 模拟异常物流（停滞、未签收），用于测试 ORDER***ABN
    if str(order_id).upper().endswith("ABN"):
        return LogisticsInfo(
            is_shipped=True,
            is_signed=False,
            stagnant_days=5,
            is_abnormal=True,
        )

    # 分支三：默认正常签收（占位实现，真实接入后替换本分支逻辑）
    return LogisticsInfo(
        is_shipped=True,
        is_signed=True,
        stagnant_days=0,
        is_abnormal=False,
    )
