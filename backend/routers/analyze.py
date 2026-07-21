"""
分析路由。

职责：创建可恢复的分析任务、查询任务状态，并通过 SSE 补发阶段事件。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.db.connection import get_db_session, get_engine
from backend.services.analysis_job_service import (
    TERMINAL_STATUSES,
    append_event,
    create_or_get_job,
    get_events_after,
    get_job,
    request_cancellation,
    serialize_job,
)
from backend.tasks.analysis_task import run_analysis_job


API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)
router = APIRouter(tags=["analyze"])


# ---------- 请求体模型：约束 /analyze 入参 ----------
class AnalyzeRequest(BaseModel):
    """
    /analyze 请求参数。
    """

    dispute_id: str = Field(min_length=1, max_length=64, description="纠纷编号")
    merchant_id: str = Field(min_length=1, max_length=64, description="商家编号")
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


def _build_materials(request: AnalyzeRequest) -> dict[str, Any]:
    """
    统一构建控制器所需材料，供普通与流式接口复用。
    """
    return {
        "merchant_id": request.merchant_id.strip(),
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


# ---------- 请求预处理：将 API 输入转换为可持久化的任务材料 ----------
def _prepare_analyze_context(request: AnalyzeRequest) -> tuple[str, dict[str, Any]]:
    """
    解析 dispute_id 并构建控制器材料 dict，供 /analyze 与 /analyze/stream 共用。
    """
    dispute_id = request.dispute_id.strip()
    materials = _build_materials(request=request)
    return dispute_id, materials


def _sse_pack(sequence: int, event_type: str, payload_json: str) -> str:
    """
    将持久化事件打包为含序号的 SSE 文本帧。

    sequence 会被浏览器作为 Last-Event-ID 回传，重连时服务端据此只补发缺失事件。
    """
    return f"id: {sequence}\nevent: {event_type}\ndata: {payload_json}\n\n"


# ---------- 创建任务：HTTP 只接收请求，长耗时链路由 Celery worker 执行 ----------
@router.post("/analyze", status_code=202)
def create_analysis_job(
    request: AnalyzeRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """
    创建或复用分析任务。

    相同材料重复提交时返回原 job_id；浏览器应订阅该任务，而不是再次执行完整 Agent 链路。
    """
    normalized_dispute_id, materials = _prepare_analyze_context(request)
    merchant_id = request.merchant_id.strip()
    job, created = create_or_get_job(
        session,
        merchant_id=merchant_id,
        dispute_id=normalized_dispute_id,
        materials=materials,
    )
    # 新建或发现尚未领取的 queued Job 都再次尝试入队。
    # 这补偿了“数据库已提交、进程在 delay 前崩溃”的空窗；重复消息由 worker 领取闸门吸收。
    if created or job.status == "queued":
        try:
            run_analysis_job.delay(job.job_id)
            logger.info(
                "%s 分析任务已入队 job_id=%s dispute_id=%s created=%s",
                API_LOG_PREFIX,
                job.job_id,
                normalized_dispute_id,
                created,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 分析任务入队失败 job_id=%s 原因=%s", API_LOG_PREFIX, job.job_id, exc)
            try:
                append_event(
                    session,
                    job_id=job.job_id,
                    event_type="enqueue_failed",
                    payload={"job_id": job.job_id, "message": "分析任务入队失败，请稍后重试"},
                )
            except Exception as event_exc:  # noqa: BLE001
                logger.warning(
                    "%s 入队失败事件写入失败 job_id=%s 原因=%s",
                    API_LOG_PREFIX,
                    job.job_id,
                    event_exc,
                )
            raise HTTPException(status_code=503, detail="分析任务入队失败，请稍后重试") from exc

    return {
        "job_id": job.job_id,
        "status": job.status,
        "reused": not created,
    }


# ---------- 状态查询：断线后的最终报告兜底，不依赖 SSE 连接仍保持存在 ----------
@router.get("/analyze/{job_id}")
def get_analysis_job(job_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """
    查询任务状态与已完成报告。

    参数:
        job_id: 创建任务时返回的唯一标识。
    返回:
        当前状态、失败原因或最终报告。
    """
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return serialize_job(job)


# ---------- 协作式取消：worker 在阶段边界读取取消标记并结束后续执行 ----------
@router.post("/analyze/{job_id}/cancel")
def cancel_analysis_job(job_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """
    请求取消分析任务。

    已进入外部 LLM 调用的任务无法抢占，会在当前调用结束后的事件检查点停止。
    """
    try:
        job = request_cancellation(session, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="分析任务不存在") from exc
    return serialize_job(job)


# ---------- SSE 订阅：从 MySQL 按序读取事件，支持 Last-Event-ID 断线补发 ----------
@router.get("/analyze/{job_id}/events")
def stream_analysis_events(
    job_id: str,
    after: int = Query(default=0, ge=0, description="已消费的最后事件序号"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """
    订阅分析任务事件。

    客户端初次连接从 after=0 读取；重连时浏览器会自动携带 Last-Event-ID，
    服务端仅补发后续事件，任务完成后连接自然关闭。
    """
    try:
        header_sequence = int(last_event_id) if last_event_id else 0
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Last-Event-ID 必须是整数") from exc
    start_sequence = max(after, header_sequence)

    with Session(get_engine()) as session:
        if get_job(session, job_id) is None:
            raise HTTPException(status_code=404, detail="分析任务不存在")

    def event_stream():
        """
        轮询 MySQL 中的新增事件并编码为 SSE。

        每轮查询后立即关闭会话，避免长连接持有数据库连接池资源。
        """
        cursor = start_sequence
        last_heartbeat_at = time.monotonic()
        while True:
            with Session(get_engine()) as session:
                events = get_events_after(session, job_id, cursor)
                job = get_job(session, job_id)

            for event in events:
                cursor = event.sequence
                yield _sse_pack(event.sequence, event.event_type, event.payload_json)

            if job is None or (job.status in TERMINAL_STATUSES and not events):
                return

            if time.monotonic() - last_heartbeat_at >= 15:
                yield ": heartbeat\n\n"
                last_heartbeat_at = time.monotonic()
            time.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
