"""
智能模式控制器。

职责：智能模式下消息接收、转人工阈值检查、对话Agent调用。
约束：不修改辅助模式任何代码。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from backend.agents.conversation_agent import chat
from backend.tools.intelligent_tools import (
    build_handoff_summary,
    check_handoff_threshold,
)
from schemas import AgentReply

LOG_PREFIX = "[IntelligentController]"
logger = logging.getLogger(__name__)


def run_with_events(
    dispute_id: str,
    buyer_message: str,
    *,
    order_id: str = "",
    order_amount: float = 0.0,
    buyer_id: str = "",
    merchant_id: str = "",
    product_category_slug: str = "",
    platform_service_tags: list[str] | None = None,
    max_compensation: float = 0.0,
    chat_history: list[dict[str, Any]] | None = None,
    round_count: int = 0,
    image_urls: list[str] | None = None,
    dismiss_round_handoff: bool = False,
) -> AgentReply:
    """
    运行智能模式单轮对话并返回 AgentReply。

    工作流程：
    1. 参数校验
    2. 构建对话历史（ChatTurn列表）
    3. 转人工阈值检查
    4. 调用对话Agent
    5. 返回回复

    参数:
        dispute_id: 纠纷编号。
        buyer_message: 买家消息。
        order_id: 订单号。
        order_amount: 订单金额。
        buyer_id: 买家ID。
        merchant_id: 商家ID。
        product_category_slug: 商品品类。
        platform_service_tags: 平台服务标。
        max_compensation: 赔偿上限。
        chat_history: 额外的历史对话。
        round_count: 当前对话轮次。

    返回:
        AgentReply。
    """
    if not isinstance(dispute_id, str) or not dispute_id.strip():
        raise ValueError("dispute_id 不能为空")
    normalized_message = str(buyer_message or "").strip()
    normalized_images = [str(url).strip() for url in (image_urls or []) if str(url).strip()]
    if not normalized_message and normalized_images:
        normalized_message = "[图片]"
    if not normalized_message and not normalized_images:
        raise ValueError("buyer_message 与 image_urls 不能同时为空")

    normalized_dispute_id = dispute_id.strip()
    logger.info(
        "%s 开始处理 dispute_id=%s buyer_msg=%s images=%s",
        LOG_PREFIX,
        normalized_dispute_id,
        normalized_message[:60],
        len(normalized_images),
    )

    # 转换chat_history为ChatTurn列表
    from schemas import ChatTurn
    turns = []
    if chat_history:
        for item in chat_history:
            if isinstance(item, dict):
                role = str(item.get("role") or "buyer").strip().lower()
                if role not in {"buyer", "merchant"}:
                    role = "buyer"
                content = str(item.get("content") or "").strip()
                if content:
                    turns.append(ChatTurn(role=role, content=content))

    # 调用对话Agent
    start = time.perf_counter()
    reply = chat(
        buyer_message=normalized_message,
        dispute_id=normalized_dispute_id,
        order_id=order_id,
        order_amount=order_amount,
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        product_category_slug=product_category_slug,
        platform_service_tags=platform_service_tags,
        max_compensation=max_compensation,
        chat_history=turns if turns else None,
        round_count=round_count,
        image_urls=normalized_images or None,
        dismiss_round_handoff=dismiss_round_handoff,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    logger.info(
        "%s 处理完成 dispute_id=%s elapsed_ms=%s handoff=%s",
        LOG_PREFIX,
        normalized_dispute_id,
        elapsed_ms,
        reply.handoff,
    )

    return reply
