"""
数据库 ORM 模型定义。

职责：定义阶段四所需的 5 张核心业务表。
"""

from __future__ import annotations

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint, func
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
