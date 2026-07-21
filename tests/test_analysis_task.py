"""
分析 Celery 任务测试。

覆盖：worker 将 Agent 事件和最终报告写入 MySQL，SSE 可从持久化事件恢复。
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import backend.tasks.analysis_task as analysis_task_module
from backend.db.models import Base
from backend.services.analysis_job_service import create_or_get_job, get_events_after, get_job


# ---------- 伪报告：隔离真实 LLM，验证任务调度与持久化边界 ----------
class _FakeReport:
    """模拟可被 AnalysisJob 持久化的结构化报告。"""

    def model_dump(self) -> dict[str, str]:
        """返回最小报告结构。"""
        return {"dispute_id": "D-TASK-001", "summary": "测试报告"}


# ---------- worker 成功：Agent 回调事件与最终报告均需可持久化读取 ----------
def test_run_analysis_job_should_persist_events_and_report(tmp_path, monkeypatch) -> None:
    """Celery worker 成功后，任务状态、事件序号和最终报告应一致。"""
    database_path = tmp_path / "analysis_task.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(analysis_task_module, "get_engine", lambda: engine)

    with Session(engine) as session:
        job, _ = create_or_get_job(
            session,
            merchant_id="M-TASK-001",
            dispute_id="D-TASK-001",
            materials={"merchant_id": "M-TASK-001", "chat_history": []},
        )
        job_id = job.job_id

    def fake_run_with_events(*, dispute_id, new_materials, emit_event):
        """模拟 Agent 链路产生阶段事件与最终报告。"""
        assert dispute_id == "D-TASK-001"
        assert new_materials["merchant_id"] == "M-TASK-001"
        emit_event("pipeline_start", {"dispute_id": dispute_id})
        emit_event("final_report", {"dispute_id": dispute_id, "report": _FakeReport().model_dump()})
        return _FakeReport()

    monkeypatch.setattr(analysis_task_module, "run_with_events", fake_run_with_events)

    result = analysis_task_module.run_analysis_job.run(job_id)

    with Session(engine) as session:
        completed = get_job(session, job_id)
        events = get_events_after(session, job_id, 0)

        assert result == {"job_id": job_id, "status": "succeeded"}
        assert completed is not None
        assert completed.status == "succeeded"
        assert completed.report_json is not None
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        assert [event.event_type for event in events] == [
            "job_started",
            "pipeline_start",
            "final_report",
            "job_completed",
        ]
