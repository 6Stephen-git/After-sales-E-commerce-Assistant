"""
分析任务服务测试。

覆盖：重复提交复用任务、SSE 事件序号补发与协作式取消状态。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import AnalysisEvent, AnalysisJob, Base
from backend.services.analysis_job_service import (
    append_event,
    create_or_get_job,
    get_events_after,
    get_job,
    mark_failed,
    mark_running,
    mark_succeeded,
    reclaim_stale_job,
    request_cancellation,
)


# ---------- 测试会话：使用独立内存 SQLite，隔离任务状态机测试 ----------
def _open_test_session() -> Session:
    """
    创建已初始化任务表的内存数据库会话。

    返回:
        可直接供分析任务服务使用的 SQLAlchemy Session。
    """
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_analysis_job_request_json_should_use_mediumtext_on_mysql() -> None:
    """MySQL 新建表应能保存带 data URL 图片的分析材料。"""
    ddl = str(CreateTable(AnalysisJob.__table__).compile(dialect=mysql.dialect())).upper()
    event_ddl = str(CreateTable(AnalysisEvent.__table__).compile(dialect=mysql.dialect())).upper()

    assert "REQUEST_JSON MEDIUMTEXT NOT NULL" in ddl
    assert "REPORT_JSON MEDIUMTEXT" in ddl
    assert "REPORT_JSON MEDIUMTEXT NOT NULL" not in ddl
    assert "PAYLOAD_JSON MEDIUMTEXT NOT NULL" in event_ddl


# ---------- 幂等：相同材料的双击不应创建两份任务 ----------
def test_create_or_get_job_should_reuse_same_materials() -> None:
    """同一商家、纠纷和材料应复用 job_id。"""
    with _open_test_session() as session:
        materials = {"merchant_id": "M-JOB-001", "chat_history": [{"role": "buyer", "content": "退款"}]}
        first, first_created = create_or_get_job(
            session,
            merchant_id="M-JOB-001",
            dispute_id="D-JOB-001",
            materials=materials,
        )
        second, second_created = create_or_get_job(
            session,
            merchant_id="M-JOB-001",
            dispute_id="D-JOB-001",
            materials=materials,
        )

        assert first_created is True
        assert second_created is False
        assert second.job_id == first.job_id


# ---------- 补发：断线客户端只读取 Last-Event-ID 之后的事件 ----------
def test_events_should_be_replayed_after_last_sequence() -> None:
    """事件序号应严格递增，after 过滤已消费事件。"""
    with _open_test_session() as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-002",
            dispute_id="D-JOB-002",
            materials={"merchant_id": "M-JOB-002", "chat_history": []},
        )
        first = append_event(
            session,
            job_id=job.job_id,
            event_type="stage_start",
            payload={"stage": "agent1"},
        )
        second = append_event(
            session,
            job_id=job.job_id,
            event_type="final_report",
            payload={"report": {"dispute_id": "D-JOB-002"}},
        )

        replayed = get_events_after(session, job.job_id, first.sequence)
        assert first.sequence == 1
        assert second.sequence == 2
        assert [event.sequence for event in replayed] == [2]
        assert replayed[0].event_type == "final_report"


# ---------- 取消：执行中的任务仅打标，由 worker 在阶段检查点协作停止 ----------
def test_cancel_running_job_should_record_cancel_request() -> None:
    """运行中取消不强杀线程，而是保存 cancel_requested 和取消事件。"""
    with _open_test_session() as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-003",
            dispute_id="D-JOB-003",
            materials={"merchant_id": "M-JOB-003", "chat_history": []},
        )
        assert mark_running(session, job.job_id) is True

        cancelled = request_cancellation(session, job.job_id)
        events = get_events_after(session, job.job_id, 0)

        assert cancelled.status == "running"
        assert cancelled.cancel_requested is True
        assert [event.event_type for event in events] == ["job_started", "cancel_requested"]


# ---------- attempt：失败或取消的相同材料必须保留历史并创建下一次执行 ----------
def test_failed_job_should_create_next_attempt_for_same_materials() -> None:
    """失败 Job 再次提交相同材料时应创建 attempt=2，而非复用失败记录。"""
    with _open_test_session() as session:
        materials = {"merchant_id": "M-JOB-004", "chat_history": [{"role": "buyer", "content": "退款"}]}
        first, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-004",
            dispute_id="D-JOB-004",
            materials=materials,
        )
        assert mark_running(session, first.job_id) is True
        mark_failed(session, job_id=first.job_id, error_message="上游暂时不可用")

        second, created = create_or_get_job(
            session,
            merchant_id="M-JOB-004",
            dispute_id="D-JOB-004",
            materials=materials,
        )

        assert created is True
        assert second.job_id != first.job_id
        assert second.attempt == 2
        assert second.status == "queued"


# ---------- 终态：晚到 worker 不得覆盖报告、失败状态或继续写 SSE 事件 ----------
def test_terminal_job_should_not_be_overwritten_or_append_events() -> None:
    """已成功 Job 接收到晚到失败消息时，状态和报告应保持不变。"""
    with _open_test_session() as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-005",
            dispute_id="D-JOB-005",
            materials={"merchant_id": "M-JOB-005", "chat_history": []},
        )
        assert mark_running(session, job.job_id) is True
        mark_succeeded(session, job_id=job.job_id, report={"summary": "最终报告"})
        mark_failed(session, job_id=job.job_id, error_message="晚到失败")

        with pytest.raises(RuntimeError, match="禁止追加事件"):
            append_event(session, job_id=job.job_id, event_type="stage_done", payload={})

        completed = get_job(session, job.job_id)
        assert completed is not None
        assert completed.status == "succeeded"
        assert completed.report_json == '{"summary":"最终报告"}'


# ---------- 取消：运行中重复投递不得提前写入 cancelled 终态 ----------
def test_duplicate_delivery_after_running_cancel_should_not_finish_job() -> None:
    """取消标记后的重复 worker 只能退出，不能抢先写终态。"""
    with _open_test_session() as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-006",
            dispute_id="D-JOB-006",
            materials={"merchant_id": "M-JOB-006", "chat_history": []},
        )
        assert mark_running(session, job.job_id) is True
        request_cancellation(session, job.job_id)

        assert mark_running(session, job.job_id) is False
        current = get_job(session, job.job_id)
        events = get_events_after(session, job.job_id, 0)

        assert current is not None
        assert current.status == "running"
        assert [event.event_type for event in events] == ["job_started", "cancel_requested"]


# ---------- 租约：broker 重投陈旧 running Job 时才允许重新领取 ----------
def test_stale_running_job_should_be_reclaimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过 hard time limit 的陈旧任务可由晚到消息重新领取，正常 running 不可抢占。"""
    monkeypatch.setenv("CELERY_TASK_TIME_LIMIT_SECONDS", "120")
    monkeypatch.setenv("ANALYSIS_JOB_STALE_SECONDS", "180")
    with _open_test_session() as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-007",
            dispute_id="D-JOB-007",
            materials={"merchant_id": "M-JOB-007", "chat_history": []},
        )
        assert mark_running(session, job.job_id) is True
        session.execute(
            update(AnalysisJob)
            .where(AnalysisJob.job_id == job.job_id)
            .values(updated_at=datetime.utcnow() - timedelta(seconds=300))
        )
        session.commit()

        assert mark_running(session, job.job_id) is True
        events = get_events_after(session, job.job_id, 0)

        assert [event.event_type for event in events] == [
            "job_started",
            "job_reclaimed",
            "job_restarted",
        ]


# ---------- 回收：SSE 订阅端对长时间 queued/running 无进展任务自动重投，达上限后终止 ----------
def test_reclaim_stale_queued_job_should_requeue_until_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """陈旧 queued 任务应重投；重投达上限后标记 failed 并追加 job_failed 事件。"""
    monkeypatch.setenv("ANALYSIS_JOB_QUEUED_STALE_SECONDS", "30")
    with _open_test_session() as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-008",
            dispute_id="D-JOB-008",
            materials={"merchant_id": "M-JOB-008", "chat_history": []},
        )

        def _make_stale() -> None:
            session.execute(
                update(AnalysisJob)
                .where(AnalysisJob.job_id == job.job_id)
                .values(updated_at=datetime.utcnow() - timedelta(seconds=300))
            )
            session.commit()

        _make_stale()
        assert reclaim_stale_job(session, job_id=job.job_id) == "requeue"
        _make_stale()
        assert reclaim_stale_job(session, job_id=job.job_id) == "requeue"
        _make_stale()
        assert reclaim_stale_job(session, job_id=job.job_id) == "requeue"
        _make_stale()
        assert reclaim_stale_job(session, job_id=job.job_id) == "failed"

        current = get_job(session, job.job_id)
        assert current is not None
        assert current.status == "failed"
        assert "重投" in (current.error_message or "")
        event_types = [event.event_type for event in get_events_after(session, job.job_id, 0)]
        assert event_types.count("requeued") == 3
        assert event_types[-1] == "job_failed"


def test_reclaim_stale_job_should_skip_fresh_job() -> None:
    """未过租约的 queued 任务不应被回收。"""
    with _open_test_session() as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-009",
            dispute_id="D-JOB-009",
            materials={"merchant_id": "M-JOB-009", "chat_history": []},
        )
        assert reclaim_stale_job(session, job_id=job.job_id) == "ok"
        assert get_job(session, job.job_id).status == "queued"


def test_reclaim_stale_running_job_should_requeue(monkeypatch: pytest.MonkeyPatch) -> None:
    """陈旧 running 任务（worker 丢失）也应由订阅端重投，由 worker 按租约复核领取。"""
    monkeypatch.setenv("CELERY_TASK_TIME_LIMIT_SECONDS", "120")
    monkeypatch.setenv("ANALYSIS_JOB_STALE_SECONDS", "180")
    with _open_test_session() as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-JOB-010",
            dispute_id="D-JOB-010",
            materials={"merchant_id": "M-JOB-010", "chat_history": []},
        )
        assert mark_running(session, job.job_id) is True
        session.execute(
            update(AnalysisJob)
            .where(AnalysisJob.job_id == job.job_id)
            .values(updated_at=datetime.utcnow() - timedelta(seconds=300))
        )
        session.commit()

        assert reclaim_stale_job(session, job_id=job.job_id) == "requeue"
        event_types = [event.event_type for event in get_events_after(session, job.job_id, 0)]
        assert event_types == ["job_started", "requeued"]
