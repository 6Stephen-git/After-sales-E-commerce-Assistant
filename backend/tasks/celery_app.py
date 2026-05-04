"""
Celery 应用配置。

职责：提供 Agent5 复盘任务的统一队列入口。
"""

from __future__ import annotations

import os

from celery import Celery


# ---------- Celery 实例：通过 REDIS_URL 配置 broker 与 backend ----------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("review_tasks", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=False,
)
