"""
对话 Agent 上下文模块。

职责：定义 IntelligentContext 数据结构（继承自 schemas.py），
并提供从 Redis 加载/保存状态的辅助函数。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from schemas import (
    ChatTurn,
    EvidenceSummary,
    IntelligentContext,
    IntelligentState,
    KeyDecision,
    ToolCallLog,
)

LOG_PREFIX = "[IntelligentContext]"
logger = logging.getLogger(__name__)

# Redis key 前缀
_STATE_KEY_PREFIX = "intel_state:"


def _state_redis_key(dispute_id: str) -> str:
    """构造 Redis 中状态的 key。"""
    return f"{_STATE_KEY_PREFIX}{dispute_id}"


def _serialize_state(state: IntelligentState) -> str:
    """将 IntelligentState 序列化为 JSON 字符串，用于 Redis 存储。"""
    return state.model_dump_json()


def _deserialize_state(data: str) -> IntelligentState | None:
    """从 JSON 字符串反序列化 IntelligentState；失败返回 None。"""
    try:
        return IntelligentState.model_validate_json(data)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 状态反序列化失败：%s", LOG_PREFIX, exc)
        return None


def load_state_from_redis(dispute_id: str) -> IntelligentState | None:
    """
    从 Redis 加载案件状态。

    参数:
        dispute_id: 纠纷编号。

    返回:
        IntelligentState 或 None（未找到或解析失败）。
    """
    try:
        from backend.cache.redis_client import get_redis

        redis = get_redis()
        key = _state_redis_key(dispute_id)
        raw = redis.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return _deserialize_state(raw)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s Redis 加载状态失败 dispute_id=%s：%s", LOG_PREFIX, dispute_id, exc)
        return None


def save_state_to_redis(state: IntelligentState, ttl_seconds: int = 86400) -> None:
    """
    将案件状态保存到 Redis。

    参数:
        state: 要保存的状态。
        ttl_seconds: 过期时间（秒），默认 24 小时。
    """
    try:
        from backend.cache.redis_client import get_redis

        redis = get_redis()
        key = _state_redis_key(state.dispute_id)
        redis.setex(key, ttl_seconds, _serialize_state(state))
        logger.info(
            "%s 状态已保存到 Redis dispute_id=%s phase=%s",
            LOG_PREFIX,
            state.dispute_id,
            state.phase,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("%s Redis 保存状态失败 dispute_id=%s：%s", LOG_PREFIX, state.dispute_id, exc)


def build_initial_context(
    dispute_id: str,
    buyer_message: str = "",
    order_id: str = "",
    order_amount: float = 0.0,
    buyer_id: str = "",
    merchant_id: str = "",
    product_category_slug: str = "",
    platform_service_tags: list[str] | None = None,
    max_compensation: float = 0.0,
) -> IntelligentContext:
    """
    构建初始上下文：从 Redis 加载已有状态（或创建新状态），组装 IntelligentContext。

    参数:
        dispute_id: 纠纷编号。
        buyer_message: 买家当前消息。
        order_id: 订单号。
        order_amount: 订单金额。
        buyer_id: 买家ID。
        merchant_id: 商家ID。
        product_category_slug: 商品品类。
        platform_service_tags: 服务标。
        max_compensation: 赔偿上限。

    返回:
        IntelligentContext 实例。
    """
    # 尝试从 Redis 加载已有状态
    existing_state = load_state_from_redis(dispute_id)
    if existing_state is None:
        existing_state = IntelligentState(dispute_id=dispute_id)

    # 构建对话历史（买家消息追加到末尾）
    chat_history: list[ChatTurn] = []
    if buyer_message.strip():
        chat_history.append(ChatTurn(role="buyer", content=buyer_message.strip()))

    return IntelligentContext(
        dispute_id=dispute_id,
        chat_history=chat_history,
        order_id=order_id,
        order_amount=max(0.0, order_amount),
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        product_category_slug=product_category_slug,
        platform_service_tags=platform_service_tags or [],
        max_compensation=max(0.0, max_compensation),
        current_state=existing_state,
    )
