"""Celery 投递可靠性配置测试。"""

from __future__ import annotations

from backend.tasks.celery_app import (
    ANALYSIS_JOB_STALE_SECONDS,
    BROKER_VISIBILITY_TIMEOUT_SECONDS,
    TASK_TIME_LIMIT_SECONDS,
    TASK_SOFT_TIME_LIMIT_SECONDS,
    celery_app,
)


# ---------- Redis broker：晚确认、单条预取和超时顺序必须同时成立 ----------
def test_celery_delivery_configuration_should_preserve_recovery_window() -> None:
    """
    验证 worker 崩溃后消息可重投，且 visibility 与 Job 租约不会早于硬超时。
    """
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert TASK_SOFT_TIME_LIMIT_SECONDS < TASK_TIME_LIMIT_SECONDS
    assert ANALYSIS_JOB_STALE_SECONDS > TASK_TIME_LIMIT_SECONDS
    assert BROKER_VISIBILITY_TIMEOUT_SECONDS > ANALYSIS_JOB_STALE_SECONDS
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == BROKER_VISIBILITY_TIMEOUT_SECONDS
