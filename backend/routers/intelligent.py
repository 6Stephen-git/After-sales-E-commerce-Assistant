"""
智能模式路由。

职责：接收智能模式对话请求，调用 IntelligentController 并返回 AgentReply。
约束：不修改辅助模式任何代码。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.controllers.intelligent_controller import run_with_events
from schemas import AgentReply

API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)
router = APIRouter(tags=["intelligent"])


# ---------- 请求体模型 ----------
class IntelligentMessageRequest(BaseModel):
    """POST /intelligent/message 请求参数。"""

    dispute_id: str = Field(..., description="纠纷编号")
    buyer_message: str = Field(..., description="买家消息文本")
    order_id: str = Field(default="", description="订单号")
    order_amount: float = Field(default=0.0, description="订单金额")
    buyer_id: str = Field(default="", description="买家脱敏ID")
    merchant_id: str = Field(default="", description="商家ID")
    product_category_slug: str = Field(default="", description="商品品类 slug")
    platform_service_tags: list[str] = Field(default_factory=list, description="平台服务标标签")
    max_compensation: float = Field(default=0.0, description="商家赔偿上限（元），0 表示不限制")
    chat_history: list[dict[str, Any]] = Field(default_factory=list, description="额外历史对话")
    round_count: int = Field(default=0, description="当前对话轮次")
    image_urls: list[str] = Field(default_factory=list, description="买家附图 data URL 列表")
    dismiss_round_handoff: bool = Field(default=False, description="用户选择继续对话，忽略轮次转人工建议")


class SimulationFixturePayload(BaseModel):
    """PUT /intelligent/simulation 请求体。"""

    enabled: bool = Field(default=True, description="是否启用模拟数据")
    orders: dict[str, Any] = Field(default_factory=dict, description="按订单号索引的模拟订单")
    buyers: dict[str, Any] = Field(default_factory=dict, description="按买家ID索引的模拟画像")


class IntelligentTakeoverRequest(BaseModel):
    """POST /intelligent/takeover 请求参数。"""

    dispute_id: str = Field(..., description="纠纷编号")


# ---------- 端点：发送买家消息 ----------
@router.post("/intelligent/message", response_model=AgentReply)
def intelligent_message(request: IntelligentMessageRequest):
    """
    发送买家消息，返回 Agent 回复 + 最新状态。

    智能模式下的主对话入口。
    """
    logger.info(
        "%s POST /intelligent/message dispute_id=%s buyer_msg=%s",
        API_LOG_PREFIX,
        request.dispute_id,
        request.buyer_message[:60],
    )
    try:
        reply = run_with_events(
            dispute_id=request.dispute_id,
            buyer_message=request.buyer_message,
            order_id=request.order_id,
            order_amount=request.order_amount,
            buyer_id=request.buyer_id,
            merchant_id=request.merchant_id,
            product_category_slug=request.product_category_slug,
            platform_service_tags=request.platform_service_tags,
            max_compensation=request.max_compensation,
            chat_history=request.chat_history,
            round_count=request.round_count,
            image_urls=request.image_urls,
            dismiss_round_handoff=request.dismiss_round_handoff,
        )
        return reply
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("%s POST /intelligent/message 失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"智能模式处理失败：{exc}")


# ---------- 端点：商家接管 ----------
@router.post("/intelligent/takeover")
def intelligent_takeover(request: IntelligentTakeoverRequest):
    """
    商家接管，返回交接摘要。

    用于人工客服介入时获取案件当前状态和建议。
    """
    logger.info(
        "%s POST /intelligent/takeover dispute_id=%s",
        API_LOG_PREFIX,
        request.dispute_id,
    )
    try:
        from backend.agents.conversation_agent.context import load_state_from_redis
        from backend.tools.intelligent_tools import build_handoff_summary
        from schemas import INTEL_PHASE_HANDOFF, UpdateStateInput
        from backend.tools.intelligent_tools import update_state

        state = load_state_from_redis(request.dispute_id)
        if state is None:
            return {
                "dispute_id": request.dispute_id,
                "status": "not_found",
                "message": "未找到该纠纷的智能模式状态",
            }

        # 标记为人工接管
        handoff_update = UpdateStateInput(
            dispute_id=request.dispute_id,
            phase=INTEL_PHASE_HANDOFF,
            update_reason="商家主动接管",
        )
        updated_state = update_state(state, handoff_update)

        # 保存到 Redis
        from backend.agents.conversation_agent.context import save_state_to_redis

        save_state_to_redis(updated_state)

        summary = build_handoff_summary(updated_state)
        return {
            "dispute_id": request.dispute_id,
            "status": "taken_over",
            "summary": summary,
            "state": updated_state.model_dump(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("%s POST /intelligent/takeover 失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"接管失败：{exc}")


# ---------- 端点：查询当前状态 ----------
@router.get("/intelligent/status/{dispute_id}")
def intelligent_status(dispute_id: str):
    """
    查询指定纠纷的当前智能模式状态。
    """
    try:
        from backend.agents.conversation_agent.context import load_state_from_redis

        state = load_state_from_redis(dispute_id)
        if state is None:
            return {
                "dispute_id": dispute_id,
                "status": "not_found",
                "message": "未找到该纠纷的智能模式状态",
            }

        return {
            "dispute_id": dispute_id,
            "status": "active",
            "state": state.model_dump(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("%s GET /intelligent/status/%s 失败：%s", API_LOG_PREFIX, dispute_id, exc)
        raise HTTPException(status_code=500, detail=f"查询状态失败：{exc}")


# ---------- 端点：测试模拟数据读写 ----------
@router.get("/intelligent/simulation")
def get_simulation_fixture():
    """读取本地测试用订单/买家模拟配置。"""
    try:
        from backend.tools.simulation_fixture import get_fixture_path, load_simulation_fixture

        data = load_simulation_fixture()
        return {
            "path": str(get_fixture_path()),
            **data,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("%s GET /intelligent/simulation 失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"读取模拟配置失败：{exc}")


@router.put("/intelligent/simulation")
def put_simulation_fixture(payload: SimulationFixturePayload):
    """保存本地测试用订单/买家模拟配置。"""
    try:
        from backend.tools.simulation_fixture import get_fixture_path, save_simulation_fixture

        save_simulation_fixture(payload.model_dump())
        return {
            "status": "saved",
            "path": str(get_fixture_path()),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("%s PUT /intelligent/simulation 失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"保存模拟配置失败：{exc}")
