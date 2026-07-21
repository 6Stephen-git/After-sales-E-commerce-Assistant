"""
Celery 应用配置。

职责：提供复盘与长耗时分析任务的统一队列入口。
"""

from __future__ import annotations

import os

from celery import Celery
from dotenv import load_dotenv


# ---------- 配置加载：Celery 独立进程也必须使用与 FastAPI 相同的环境变量 ----------
load_dotenv()


def _read_positive_seconds(env_name: str, default: int) -> int:
    """
    读取正整数秒数配置；非法值回退默认值，避免 worker 因环境变量错误无法启动。
    """
    raw = os.getenv(env_name, str(default)).strip()
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


# ---------- 投递可靠性：visibility 必须覆盖 hard limit 与陈旧 Job 租约 ----------
TASK_SOFT_TIME_LIMIT_SECONDS = _read_positive_seconds("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", 1080)
TASK_TIME_LIMIT_SECONDS = _read_positive_seconds("CELERY_TASK_TIME_LIMIT_SECONDS", 1200)
if TASK_SOFT_TIME_LIMIT_SECONDS >= TASK_TIME_LIMIT_SECONDS:
    TASK_SOFT_TIME_LIMIT_SECONDS = max(1, TASK_TIME_LIMIT_SECONDS - 60)

ANALYSIS_JOB_STALE_SECONDS = _read_positive_seconds("ANALYSIS_JOB_STALE_SECONDS", 1800)
if ANALYSIS_JOB_STALE_SECONDS <= TASK_TIME_LIMIT_SECONDS:
    ANALYSIS_JOB_STALE_SECONDS = TASK_TIME_LIMIT_SECONDS + 60

BROKER_VISIBILITY_TIMEOUT_SECONDS = _read_positive_seconds(
    "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS",
    2100,
)
BROKER_VISIBILITY_TIMEOUT_SECONDS = max(
    BROKER_VISIBILITY_TIMEOUT_SECONDS,
    ANALYSIS_JOB_STALE_SECONDS + 60,
    TASK_TIME_LIMIT_SECONDS + 60,
)
CELERY_RESULT_EXPIRES_SECONDS = _read_positive_seconds("CELERY_RESULT_EXPIRES_SECONDS", 3600)


# ---------- Celery 实例：显式加载任务模块，避免 worker 漏注册分析任务 ----------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery(
    "ecommerce_assistant_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["backend.tasks.review_task", "backend.tasks.analysis_task"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=TASK_TIME_LIMIT_SECONDS,
    broker_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT_SECONDS},
    result_expires=CELERY_RESULT_EXPIRES_SECONDS,
)
