"""
数据库连接管理。

职责：从环境变量构建连接串，懒加载 SQLAlchemy Engine，并提供会话依赖。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


DB_LOG_PREFIX = "[DB]"
logger = logging.getLogger(__name__)

_ENGINE: Engine | None = None
_SESSION_FACTORY = sessionmaker(autocommit=False, autoflush=False)


# ---------- 连接串构建：优先 DB_URL，缺省拼接 MySQL URL ----------
def _build_database_url() -> str:
    """
    按优先级构建数据库连接串。

    返回:
        可直接用于 SQLAlchemy 的连接字符串。
    """
    custom_db_url = os.getenv("DB_URL", "").strip()
    if custom_db_url:
        return custom_db_url

    db_host = os.getenv("DB_HOST", "localhost").strip()
    db_port = os.getenv("DB_PORT", "3306").strip()
    db_name = os.getenv("DB_NAME", "ecommerce_assistant").strip()
    db_user = os.getenv("DB_USER", "root").strip()
    db_password = quote_plus(os.getenv("DB_PASSWORD", "").strip())
    return f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"


# ---------- 引擎初始化：懒加载并复用单例 ----------
def get_engine() -> Engine:
    """
    获取数据库引擎单例。

    返回:
        SQLAlchemy Engine。
    """
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    database_url = _build_database_url()
    logger.info("%s 初始化数据库引擎", DB_LOG_PREFIX)
    try:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        _ENGINE = create_engine(
            database_url,
            pool_pre_ping=True,
            future=True,
            connect_args=connect_args,
        )
        logger.info("%s 数据库引擎初始化成功", DB_LOG_PREFIX)
        return _ENGINE
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 数据库引擎初始化失败：%s", DB_LOG_PREFIX, exc)
        raise RuntimeError(f"{DB_LOG_PREFIX} 数据库引擎初始化失败：{exc}") from exc


# ---------- 会话依赖：供 FastAPI Depends 注入 ----------
def get_db_session() -> Session:
    """
    提供数据库会话依赖。

    Yields:
        SQLAlchemy Session。
    """
    engine = get_engine()
    _SESSION_FACTORY.configure(bind=engine)
    session = _SESSION_FACTORY()
    try:
        yield session
    finally:
        session.close()


# ---------- 增量补丁：create_all 不会为已有表补列 ----------
def _apply_schema_patches(engine: Engine) -> None:
    """
    对已有表补齐 ORM 新增列，避免模型与库表漂移导致查询 500。

    参数:
        engine: 已初始化的 SQLAlchemy Engine。
    """
    inspector = inspect(engine)
    if "merchant_config" not in inspector.get_table_names():
        return

    column_names = {col["name"] for col in inspector.get_columns("merchant_config")}
    if "max_compensation" in column_names:
        return

    dialect = engine.dialect.name
    logger.info("%s 检测到 merchant_config 缺少 max_compensation，开始补列", DB_LOG_PREFIX)
    if dialect == "mysql":
        ddl = (
            "ALTER TABLE merchant_config ADD COLUMN max_compensation FLOAT NOT NULL DEFAULT 0 "
            "COMMENT '智能模式个性化赔偿上限（元），0 表示不限制'"
        )
    elif dialect == "sqlite":
        ddl = "ALTER TABLE merchant_config ADD COLUMN max_compensation FLOAT NOT NULL DEFAULT 0"
    else:
        logger.warning("%s 未识别的数据库方言 %s，跳过 max_compensation 补列", DB_LOG_PREFIX, dialect)
        return

    with engine.begin() as conn:
        conn.execute(text(ddl))
    logger.info("%s merchant_config.max_compensation 补列完成", DB_LOG_PREFIX)


# ---------- 元数据建表：应用启动时按模型创建缺失表 ----------
def init_db() -> None:
    """
    初始化数据库表结构。
    """
    logger.info("%s 开始执行数据库建表", DB_LOG_PREFIX)
    try:
        from backend.db.models import Base

        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        _apply_schema_patches(engine)
        logger.info("%s 数据库建表完成", DB_LOG_PREFIX)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 数据库建表失败：%s", DB_LOG_PREFIX, exc)
        raise RuntimeError(f"{DB_LOG_PREFIX} 数据库建表失败：{exc}") from exc
