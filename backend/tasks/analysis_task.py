"""
分析任务 Celery 入口。

职责：在独立 worker 中运行长耗时 Agent 链路，并将阶段事件、状态和最终报告
持久化到 MySQL，避免浏览器连接生命周期控制 LLM 执行生命周期。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from backend.controllers.assisted_controller import run_with_events
from backend.db.connection import get_engine
from backend.services.analysis_job_service import (
    append_event,
    get_job,
    is_cancel_requested,
    mark_failed,
    mark_running,
    mark_succeeded,
)
from backend.tasks.celery_app import celery_app


ANALYSIS_TASK_LOG_PREFIX = "[AnalysisTask]"
logger = logging.getLogger(__name__)


# ---------- worker 会话：Celery 不经过 FastAPI Depends，需显式创建并关闭数据库会话 ----------
def _open_session() -> Session:
    """
    创建分析任务专用数据库会话。

    返回:
        绑定当前 SQLAlchemy Engine 的 Session。
    """
    return Session(get_engine())


# ---------- 事件桥接：把 Agent 回调转为可恢复的 MySQL 事件 ----------
def _record_pipeline_event(job_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """
    持久化 Agent 阶段事件并检查协作式取消标记。

    当前外部 LLM 调用不可抢占；取消会在每次阶段事件之间生效，避免继续进入后续阶段。
    """
    with _open_session() as session:
        if is_cancel_requested(session, job_id):
            raise RuntimeError("分析任务已取消")
        append_event(session, job_id=job_id, event_type=event_type, payload=payload)


# ---------- Celery 主任务：领取 Job、执行主链路、提交终态 ----------
@celery_app.task(name="analysis.run")
def run_analysis_job(job_id: str) -> dict[str, Any]:
    """
    执行单个分析任务。

    参数:
        job_id: MySQL 中已创建的分析任务标识。
    返回:
        Celery 可序列化的任务执行摘要。
    """
    try:
        with _open_session() as session:
            if not mark_running(session, job_id):
                job = get_job(session, job_id)
                return {"job_id": job_id, "status": job.status if job else "missing"}
            job = get_job(session, job_id)
            if job is None:
                raise ValueError(f"分析任务不存在：{job_id}")
            dispute_id = job.dispute_id
            materials = json.loads(job.request_json)

        logger.info("%s 开始执行 job_id=%s dispute_id=%s", ANALYSIS_TASK_LOG_PREFIX, job_id, dispute_id)
        report = run_with_events(
            dispute_id=dispute_id,
            new_materials=materials,
            emit_event=lambda event_type, payload: _record_pipeline_event(job_id, event_type, payload),
        )
        with _open_session() as session:
            completed = mark_succeeded(session, job_id=job_id, report=report.model_dump())
        logger.info("%s 执行完成 job_id=%s status=%s", ANALYSIS_TASK_LOG_PREFIX, job_id, completed.status)
        return {"job_id": job_id, "status": completed.status}
    except SoftTimeLimitExceeded as exc:
        with _open_session() as session:
            failed = mark_failed(session, job_id=job_id, error_message="分析任务超过软时间限制")
        logger.error("%s 执行超时 job_id=%s status=%s", ANALYSIS_TASK_LOG_PREFIX, job_id, failed.status)
        raise exc
    except Exception as exc:  # noqa: BLE001
        with _open_session() as session:
            failed = mark_failed(session, job_id=job_id, error_message=str(exc))
        if failed.status == "cancelled":
            logger.info("%s 已取消 job_id=%s", ANALYSIS_TASK_LOG_PREFIX, job_id)
            return {"job_id": job_id, "status": "cancelled"}
        logger.exception("%s 执行失败 job_id=%s", ANALYSIS_TASK_LOG_PREFIX, job_id)
        raise
