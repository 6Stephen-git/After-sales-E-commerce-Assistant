"""
分析任务服务。

职责：管理分析任务、可补发的阶段事件与最终报告；MySQL 是任务事实源，
Redis/Celery 只承担调度与执行，不承担断线恢复的持久化责任。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models import AnalysisEvent, AnalysisJob


JOB_LOG_PREFIX = "[AnalysisJob]"
logger = logging.getLogger(__name__)
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


# ---------- 序列化：稳定计算请求幂等键，重复点击只创建一个任务 ----------
def _dump_json(value: Any) -> str:
    """
    序列化 JSON 数据。

    参数:
        value: 可 JSON 序列化的业务数据。
    返回:
        字段顺序稳定的 JSON 文本。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_idempotency_key(merchant_id: str, dispute_id: str, materials: dict[str, Any]) -> str:
    """
    根据商家、纠纷与完整材料生成幂等键。

    相同材料的重复提交复用同一个 job；材料变化会生成新任务，避免错误复用旧报告。
    """
    payload = {
        "merchant_id": merchant_id,
        "dispute_id": dispute_id,
        "materials": materials,
    }
    return hashlib.sha256(_dump_json(payload).encode("utf-8")).hexdigest()


def _job_stale_seconds() -> int:
    """
    读取 running Job 租约阈值。

    此值必须大于 Celery hard time limit；只有 broker 晚到重投时才允许重新领取，
    防止正常执行中的 LLM 请求被第二个 worker 抢占。
    """
    raw = os.getenv("ANALYSIS_JOB_STALE_SECONDS", "1800").strip()
    try:
        configured = max(60, int(raw))
    except ValueError:
        logger.warning("%s ANALYSIS_JOB_STALE_SECONDS 无效，使用 1800 秒", JOB_LOG_PREFIX)
        configured = 1800
    try:
        hard_limit = max(1, int(os.getenv("CELERY_TASK_TIME_LIMIT_SECONDS", "1200").strip()))
    except ValueError:
        hard_limit = 1200
    return max(configured, hard_limit + 60)


def _is_stale_running_job(job: AnalysisJob) -> bool:
    """
    判断 running Job 是否已超过租约。
    """
    if job.status != "running" or job.updated_at is None:
        return False
    # server_default=func.now() 在 SQLite/MySQL 默认按 UTC 产生无时区时间；
    # 这里同样使用 utcnow，避免应用进程位于东八区时把新任务误判为陈旧任务。
    return (datetime.utcnow() - job.updated_at).total_seconds() >= _job_stale_seconds()


# ---------- 查询：统一按任务 ID 获取记录，避免路由层直接写 ORM ----------
def get_job(session: Session, job_id: str) -> AnalysisJob | None:
    """
    查询单个分析任务。

    参数:
        session: 当前数据库会话。
        job_id: 任务唯一标识。
    返回:
        任务记录；不存在时返回 None。
    """
    return session.get(AnalysisJob, job_id)


def get_events_after(session: Session, job_id: str, after_sequence: int) -> list[AnalysisEvent]:
    """
    查询指定序号之后的任务事件。

    参数:
        session: 当前数据库会话。
        job_id: 任务唯一标识。
        after_sequence: 客户端最后确认的事件序号。
    返回:
        按 sequence 升序排列的未消费事件。
    """
    statement = (
        select(AnalysisEvent)
        .where(AnalysisEvent.job_id == job_id, AnalysisEvent.sequence > max(0, after_sequence))
        .order_by(AnalysisEvent.sequence)
    )
    return list(session.scalars(statement))


# ---------- 创建：成功任务复用，失败/取消任务创建下一 attempt 并保留历史 ----------
def create_or_get_job(
    session: Session,
    *,
    merchant_id: str,
    dispute_id: str,
    materials: dict[str, Any],
) -> tuple[AnalysisJob, bool]:
    """
    创建或复用分析任务。

    参数:
        session: 当前数据库会话。
        merchant_id: 商家标识。
        dispute_id: 纠纷标识。
        materials: 已归一化的分析材料。
    返回:
        (任务记录, 是否新建) 元组。失败或取消的同材料任务会生成新的 attempt。
    """
    idempotency_key = _build_idempotency_key(merchant_id, dispute_id, materials)
    statement = select(AnalysisJob).where(
        AnalysisJob.merchant_id == merchant_id,
        AnalysisJob.idempotency_key == idempotency_key,
    ).order_by(desc(AnalysisJob.attempt))
    existing = session.scalar(statement)
    if existing is not None and existing.status not in {"failed", "cancelled"}:
        return existing, False

    attempt = (existing.attempt + 1) if existing is not None else 1
    job = AnalysisJob(
        job_id=str(uuid.uuid4()),
        merchant_id=merchant_id,
        dispute_id=dispute_id,
        idempotency_key=idempotency_key,
        attempt=attempt,
        request_json=_dump_json(materials),
        status="queued",
        next_event_id=1,
    )
    session.add(job)
    try:
        session.commit()
        session.refresh(job)
        logger.info(
            "%s 创建分析任务 job_id=%s dispute_id=%s attempt=%s",
            JOB_LOG_PREFIX,
            job.job_id,
            dispute_id,
            attempt,
        )
        return job, True
    except IntegrityError:
        session.rollback()
        existing = session.scalar(statement)
        if existing is None:
            raise
        return existing, False


# ---------- 事件写入：单事务分配 sequence，确保 SSE 可按游标补发 ----------
def _append_event_locked(
    session: Session,
    job: AnalysisJob,
    *,
    event_type: str,
    payload: dict[str, Any],
) -> AnalysisEvent:
    """
    为已加锁的任务追加一条事件。

    调用方必须在同一事务内持有任务行锁，避免多个 worker 分配相同 sequence。
    """
    event = AnalysisEvent(
        job_id=job.job_id,
        sequence=job.next_event_id,
        event_type=event_type,
        payload_json=_dump_json(payload),
    )
    job.next_event_id += 1
    session.add(event)
    return event


def append_event(
    session: Session,
    *,
    job_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> AnalysisEvent:
    """
    持久化一条阶段事件。

    参数:
        session: 当前数据库会话。
        job_id: 任务唯一标识。
        event_type: SSE 事件名称。
        payload: 前端可消费的事件数据。
    返回:
        新增事件。
    异常:
        ValueError: 任务不存在。
    """
    statement = select(AnalysisJob).where(AnalysisJob.job_id == job_id).with_for_update()
    job = session.scalar(statement)
    if job is None:
        raise ValueError(f"分析任务不存在：{job_id}")
    if job.status in TERMINAL_STATUSES:
        raise RuntimeError(f"分析任务已结束，禁止追加事件：job_id={job_id}")
    event = _append_event_locked(session, job, event_type=event_type, payload=payload)
    session.commit()
    session.refresh(event)
    return event


# ---------- 状态流转：仅 queued Job 可领取，陈旧 running Job 仅在 broker 重投时恢复 ----------
def mark_running(session: Session, job_id: str) -> bool:
    """
    将排队任务切换为执行中。

    返回:
        True 表示当前 worker 获得执行权；False 表示任务已执行、已结束或已取消。
    """
    statement = select(AnalysisJob).where(AnalysisJob.job_id == job_id).with_for_update()
    job = session.scalar(statement)
    if job is None:
        raise ValueError(f"分析任务不存在：{job_id}")
    if job.status in TERMINAL_STATUSES:
        return False
    if job.status == "running":
        if not _is_stale_running_job(job):
            return False
        logger.warning(
            "%s 检测到陈旧任务重投，重新领取 job_id=%s stale_seconds=%s",
            JOB_LOG_PREFIX,
            job_id,
            _job_stale_seconds(),
        )
        _append_event_locked(session, job, event_type="job_reclaimed", payload={"job_id": job_id})
    elif job.status != "queued":
        return False

    if job.cancel_requested:
        job.status = "cancelled"
        _append_event_locked(session, job, event_type="cancelled", payload={"job_id": job_id})
        session.commit()
        return False
    job.status = "running"
    event_type = "job_restarted" if job.next_event_id > 1 else "job_started"
    _append_event_locked(session, job, event_type=event_type, payload={"job_id": job_id})
    session.commit()
    return True


def is_cancel_requested(session: Session, job_id: str) -> bool:
    """
    查询任务是否已收到协作式取消请求。

    正在调用外部 LLM 时无法强制中断；下一个阶段事件产生时，worker 会检测此标记并停止。
    """
    job = get_job(session, job_id)
    return bool(job and job.cancel_requested)


def request_cancellation(session: Session, job_id: str) -> AnalysisJob:
    """
    标记任务取消。

    排队任务会立即转为 cancelled；运行中的任务等待 worker 在安全检查点停止。
    """
    statement = select(AnalysisJob).where(AnalysisJob.job_id == job_id).with_for_update()
    job = session.scalar(statement)
    if job is None:
        raise ValueError(f"分析任务不存在：{job_id}")
    if job.status in TERMINAL_STATUSES:
        return job
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        _append_event_locked(session, job, event_type="cancelled", payload={"job_id": job_id})
    else:
        _append_event_locked(session, job, event_type="cancel_requested", payload={"job_id": job_id})
    session.commit()
    session.refresh(job)
    return job


# ---------- 结束：最终报告和状态在同一事务提交，状态接口可作为 SSE 的兜底 ----------
def mark_succeeded(session: Session, *, job_id: str, report: dict[str, Any]) -> AnalysisJob:
    """
    保存最终报告并将任务标记为成功。

    最终报告既保留在 final_report 事件中，也保留在任务表中，避免事件已过期后无法恢复结果。
    """
    statement = select(AnalysisJob).where(AnalysisJob.job_id == job_id).with_for_update()
    job = session.scalar(statement)
    if job is None:
        raise ValueError(f"分析任务不存在：{job_id}")
    if job.status in TERMINAL_STATUSES:
        return job
    job.report_json = _dump_json(report)
    job.status = "cancelled" if job.cancel_requested else "succeeded"
    completion_event = "cancelled" if job.cancel_requested else "job_completed"
    _append_event_locked(session, job, event_type=completion_event, payload={"job_id": job_id})
    session.commit()
    session.refresh(job)
    return job


def mark_failed(session: Session, *, job_id: str, error_message: str) -> AnalysisJob:
    """
    保存失败原因并终止任务。

    错误文本仅用于任务状态查询和服务端日志；路由层可按需要对外脱敏。
    """
    statement = select(AnalysisJob).where(AnalysisJob.job_id == job_id).with_for_update()
    job = session.scalar(statement)
    if job is None:
        raise ValueError(f"分析任务不存在：{job_id}")
    if job.status in TERMINAL_STATUSES:
        return job
    job.status = "cancelled" if job.cancel_requested else "failed"
    job.error_message = error_message[:2000]
    event_type = "cancelled" if job.cancel_requested else "job_failed"
    _append_event_locked(
        session,
        job,
        event_type=event_type,
        payload={"job_id": job_id, "message": job.error_message},
    )
    session.commit()
    session.refresh(job)
    return job


# ---------- 响应转换：路由层只返回稳定、可 JSON 序列化的任务视图 ----------
def serialize_job(job: AnalysisJob, *, include_request: bool = False) -> dict[str, Any]:
    """
    将 ORM 任务转换为 API 响应。

    参数:
        job: 任务记录。
        include_request: 是否包含原始分析材料，仅 worker 内部调试时使用。
    返回:
        任务状态与可用结果。
    """
    report = json.loads(job.report_json) if job.report_json else None
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "merchant_id": job.merchant_id,
        "dispute_id": job.dispute_id,
        "attempt": job.attempt,
        "status": job.status,
        "cancel_requested": job.cancel_requested,
        "report": report,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
    if include_request:
        payload["materials"] = json.loads(job.request_json)
    return payload
