"""
数据库 ORM 模型定义。

职责：定义阶段四所需的 5 张核心业务表。
"""

from __future__ import annotations

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ---------- 模型基类：统一 metadata 入口 ----------
class Base(DeclarativeBase):
    """
    SQLAlchemy Declarative 基类。
    """


# ---------- 商家配置表：保存模式与自动化阈值 ----------
class MerchantConfig(Base):
    """
    商家配置（按 merchant_id 隔离）。
    """

    __tablename__ = "merchant_config"

    merchant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="assisted")
    auto_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    max_compensation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, comment="智能模式个性化赔偿上限（元），0 表示不限制")
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------- 判例经验表：仅 Agent5 写入的复盘卡片 ----------
class DisputeCase(Base):
    """
    纠纷判例库（商家私有）。
    """

    __tablename__ = "dispute_cases"
    __table_args__ = (
        UniqueConstraint("merchant_id", "dispute_id", name="uq_dispute_cases_merchant_dispute"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dispute_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    case_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    case_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lesson_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scenario_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())


# ---------- 买家画像表：手机号哈希画像快照 ----------
class BuyerProfileRecord(Base):
    """
    买家画像记录（商家私有）。
    """

    __tablename__ = "buyer_profiles"
    __table_args__ = (UniqueConstraint("merchant_id", "buyer_hash", name="uq_buyer_profiles_merchant_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    buyer_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------- 平台规则表：全局共享规则库 ----------
class PlatformRule(Base):
    """
    平台规则库（全局共享）。
    """

    __tablename__ = "platform_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    rule_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------- 分析任务表：保存长耗时分析的可恢复状态与最终报告 ----------
class AnalysisJob(Base):
    """
    分析任务。

    一个请求对应一个可追踪任务；任务状态和最终报告由 MySQL 持久化，
    使浏览器断线后能按 job_id 恢复，而不是重新调用 LLM。
    """

    __tablename__ = "analysis_jobs"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "idempotency_key",
            "attempt",
            name="uq_analysis_jobs_merchant_key_attempt",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dispute_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 分析材料可能包含 data URL 图片；MySQL TEXT 仅约 64 KiB，无法持久化普通截图。
    # SQLite 继续使用通用 Text，MySQL 新表使用 MEDIUMTEXT（约 16 MiB）。
    request_json: Mapped[str] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    # 最终报告包含 evidence_items 中的 data URL 图片引用，TEXT 仅约 64 KiB 会溢出；
    # MySQL 使用 MEDIUMTEXT（约 16 MiB），SQLite 继续使用通用 Text。
    report_json: Mapped[str | None] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    next_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------- 分析事件表：为 SSE 重连提供可按序补发的阶段事件 ----------
class AnalysisEvent(Base):
    """
    分析任务事件。

    sequence 在单个任务内严格递增；浏览器通过 Last-Event-ID 或 after
    只读取未消费事件，保证重连时不会遗漏最终报告。
    """

    __tablename__ = "analysis_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_analysis_events_job_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # stage_done/final_report 事件负载会携带含 data URL 图片的 facts/报告，
    # TEXT 仅约 64 KiB 会触发 MySQL Data too long；升级为 MEDIUMTEXT。
    payload_json: Mapped[str] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql"),
        nullable=False,
    )
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
