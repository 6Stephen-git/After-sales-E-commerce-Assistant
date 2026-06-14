"""
分析路由。

职责：接收辅助模式分析请求，调用 AssistedController 并返回 AnalysisReport。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.controllers.assisted_controller import run as assisted_run
from backend.controllers.assisted_controller import run_with_events as assisted_run_with_events
from backend.defaults import default_dispute_id, default_merchant_id


API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)
router = APIRouter(tags=["analyze"])


# ---------- 请求体模型：约束 /analyze 入参 ----------
class AnalyzeRequest(BaseModel):
    """
    /analyze 请求参数。
    """

    dispute_id: str = Field(default="", description="纠纷编号，空则使用 DEFAULT_DISPUTE_ID")
    merchant_id: str = Field(default="", description="商家编号，空则使用 DEFAULT_MERCHANT_ID")
    messages: list[dict[str, Any]] = Field(default_factory=list, description="纠纷消息列表")
    order_id: str = Field(default="", description="订单号")
    order_amount: float = Field(default=0.0, description="订单金额")
    buyer_id: str = Field(default="", description="买家脱敏标识")
    image_urls: list[str] = Field(default_factory=list, description="举证图片 URL")
    reset_context: bool = Field(default=False, description="是否重置该纠纷缓存并以本次材料为准")
    materials_snapshot: bool = Field(
        default=True,
        description="True 时 chat_history/image_urls 以本次请求为准覆盖缓存；False 时增量追加",
    )
    product_category_slug: str = Field(
        default="",
        description="平台商品品类 slug（API 接入后填入；有则直接激活对应品类规范）",
    )
    platform_service_tags: list[str] = Field(
        default_factory=list,
        description="订单服务标原文列表（如七天无理由、破损包退；有则直接激活对应服务规范）",
    )


# ---------- 请求转换：从消息列表提取 buyer_text ----------
def _extract_buyer_text(messages: list[dict[str, Any]]) -> str:
    """
    提取最近一条买家消息文本，作为 Agent1 的 buyer_text。
    """
    for message in reversed(messages):
        role = str(message.get("role", "")).strip().lower()
        content = str(message.get("content", "")).strip()
        if role in {"buyer", "user"} and content:
            return content
    return ""


def _is_enabled(flag_name: str, default: bool = False) -> bool:
    """
    读取布尔开关环境变量。
    """
    raw = os.getenv(flag_name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "y"}


def _build_materials(request: AnalyzeRequest) -> dict[str, Any]:
    """
    统一构建控制器所需材料，供普通与流式接口复用。
    """
    return {
        "merchant_id": _resolve_non_empty_id(request.merchant_id, default_merchant_id),
        "order_id": request.order_id.strip(),
        "order_amount": request.order_amount,
        "buyer_id": request.buyer_id.strip(),
        "buyer_text": _extract_buyer_text(messages=request.messages),
        "chat_history": request.messages,
        "image_urls": request.image_urls,
        "reset_context": request.reset_context,
        "materials_snapshot": request.materials_snapshot,
        "product_category_slug": request.product_category_slug.strip(),
        "platform_service_tags": [
            str(tag).strip() for tag in request.platform_service_tags if str(tag).strip()
        ],
    }


# ---------- 标识占位：前端未传时使用环境变量默认值 ----------
def _resolve_non_empty_id(raw_value: str, fallback: Callable[[], str]) -> str:
    """
    解析非空业务 ID；空字符串回退到 fallback 提供的默认值。
    """
    normalized = str(raw_value or "").strip()
    return normalized or fallback()


def _prepare_analyze_context(request: AnalyzeRequest) -> tuple[str, dict[str, Any]]:
    """
    解析 dispute_id 并构建控制器材料 dict，供 /analyze 与 /analyze/stream 共用。
    """
    dispute_id = _resolve_non_empty_id(request.dispute_id, default_dispute_id)
    materials = _build_materials(request=request)
    return dispute_id, materials


def _sse_pack(event_type: str, payload: dict[str, Any]) -> str:
    """
    将事件打包为 SSE 文本帧。
    """
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------- 核心端点：调用 AssistedController.run ----------
@router.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    """
    执行辅助模式分析并返回结构化报告。
    """
    request_start = time.perf_counter()
    normalized_dispute_id, materials = _prepare_analyze_context(request)
    logger.info("%s 开始处理 /analyze 请求，dispute_id=%s", API_LOG_PREFIX, normalized_dispute_id)
    try:
        report = assisted_run(dispute_id=normalized_dispute_id, new_materials=materials)
        elapsed_ms = int((time.perf_counter() - request_start) * 1000)
        logger.info(
            "%s /analyze 请求处理完成，dispute_id=%s elapsed_ms=%s",
            API_LOG_PREFIX,
            normalized_dispute_id,
            elapsed_ms,
        )
        return report.model_dump()
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - request_start) * 1000)
        logger.error("%s /analyze 执行失败：dispute_id=%s elapsed_ms=%s 原因=%s", API_LOG_PREFIX, normalized_dispute_id, elapsed_ms, exc)
        raise HTTPException(status_code=500, detail=f"分析失败：{exc}") from exc


@router.post("/analyze/stream")
def analyze_stream(request: AnalyzeRequest) -> StreamingResponse:
    """
    执行流式分析：按阶段推送事件，最后下发 final_report。
    """
    if not _is_enabled("ENABLE_ANALYZE_STREAM", default=False):
        raise HTTPException(status_code=404, detail="流式分析未启用")

    normalized_dispute_id, materials = _prepare_analyze_context(request)
    logger.info("%s 开始处理 /analyze/stream 请求，dispute_id=%s", API_LOG_PREFIX, normalized_dispute_id)

    def event_stream():
        """
        后台线程执行主链路，主生成器持续消费事件队列并输出 SSE。
        """
        event_queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

        def emit_event(event_type: str, payload: dict[str, Any]) -> None:
            event_queue.put((event_type, payload))

        def worker() -> None:
            try:
                assisted_run_with_events(
                    dispute_id=normalized_dispute_id,
                    new_materials=materials,
                    emit_event=emit_event,
                )
            except Exception as exc:  # noqa: BLE001
                event_queue.put(("pipeline_error", {"dispute_id": normalized_dispute_id, "message": str(exc)}))
            finally:
                event_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()
        yield _sse_pack("connected", {"dispute_id": normalized_dispute_id})

        while True:
            try:
                item = event_queue.get(timeout=20)
            except queue.Empty:
                # Agent 阶段耗时长时发心跳，避免代理/浏览器因空闲断开 SSE
                yield _sse_pack("heartbeat", {"dispute_id": normalized_dispute_id})
                continue
            if item is None:
                break
            event_type, payload = item
            yield _sse_pack(event_type, payload)

        yield _sse_pack("closed", {"dispute_id": normalized_dispute_id})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
