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


def _patch_dispute_cases_columns(engine: Engine) -> None:
    """
    为 dispute_cases 表补齐 Agent5 复盘所需列。

    说明：MySQL 的 TEXT 列不允许 DEFAULT，scenario_json 需分步补列并回填。
    """
    inspector = inspect(engine)
    if "dispute_cases" not in inspector.get_table_names():
        return

    column_names = {col["name"] for col in inspector.get_columns("dispute_cases")}
    dialect = engine.dialect.name
    patches: list[tuple[str, str]] = []
    needs_scenario_json = "scenario_json" not in column_names

    if "dispute_id" not in column_names:
        patches.append(("dispute_id", "VARCHAR(64) NOT NULL DEFAULT ''"))
    if "case_type" not in column_names:
        patches.append(("case_type", "VARCHAR(64) NOT NULL DEFAULT ''"))
    if "outcome" not in column_names:
        patches.append(("outcome", "VARCHAR(16) NOT NULL DEFAULT ''"))

    if not patches and not needs_scenario_json:
        return

    logger.info(
        "%s 开始补齐 dispute_cases 列：%s scenario_json=%s",
        DB_LOG_PREFIX,
        [name for name, _ in patches],
        needs_scenario_json,
    )
    with engine.begin() as conn:
        for col_name, col_def in patches:
            conn.execute(text(f"ALTER TABLE dispute_cases ADD COLUMN {col_name} {col_def}"))
        if needs_scenario_json:
            if dialect == "mysql":
                conn.execute(text("ALTER TABLE dispute_cases ADD COLUMN scenario_json TEXT NULL"))
                conn.execute(
                    text("UPDATE dispute_cases SET scenario_json = '{}' WHERE scenario_json IS NULL")
                )
            elif dialect == "sqlite":
                conn.execute(
                    text("ALTER TABLE dispute_cases ADD COLUMN scenario_json TEXT NOT NULL DEFAULT '{}'")
                )
            else:
                conn.execute(text("ALTER TABLE dispute_cases ADD COLUMN scenario_json TEXT NULL"))
                conn.execute(
                    text("UPDATE dispute_cases SET scenario_json = '{}' WHERE scenario_json IS NULL")
                )
    logger.info("%s dispute_cases 补列完成", DB_LOG_PREFIX)


# ---------- 一致性约束补丁：为已有 MySQL 表补齐 attempt 与复盘唯一约束 ----------
def _ensure_no_duplicate_pairs(engine: Engine, table_name: str, columns: tuple[str, ...]) -> None:
    """
    在新增唯一约束前检查历史重复数据，禁止静默删除或覆盖已有业务记录。
    """
    quoted_columns = ", ".join(columns)
    statement = text(
        f"SELECT {quoted_columns}, COUNT(*) AS duplicate_count "
        f"FROM {table_name} GROUP BY {quoted_columns} HAVING COUNT(*) > 1 LIMIT 1"
    )
    with engine.connect() as conn:
        duplicate = conn.execute(statement).first()
    if duplicate is not None:
        raise RuntimeError(
            f"{DB_LOG_PREFIX} 无法为 {table_name} 新增唯一约束：存在历史重复数据，"
            "请先人工核对并清理后重启服务"
        )


def _patch_consistency_constraints(engine: Engine) -> None:
    """
    迁移 AnalysisJob attempt 和 DisputeCase 幂等唯一约束。

    生产目标数据库为 MySQL；SQLite 的新库由 create_all 直接生成正确结构，
    已存在 SQLite 表不做破坏性重建。
    """
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    dialect = engine.dialect.name
    if dialect != "mysql":
        return

    if "analysis_jobs" in table_names:
        analysis_job_columns = {
            column["name"]: column for column in inspector.get_columns("analysis_jobs")
        }
        columns = set(analysis_job_columns)
        unique_names = {item["name"] for item in inspector.get_unique_constraints("analysis_jobs")}
        with engine.begin() as conn:
            if "attempt" not in columns:
                conn.execute(
                    text("ALTER TABLE analysis_jobs ADD COLUMN attempt INT NOT NULL DEFAULT 1")
                )
            request_json_column = analysis_job_columns.get("request_json")
            request_json_type = (
                str(request_json_column["type"]).upper()
                if request_json_column is not None
                else ""
            )
            # TEXT 的上限只有约 64 KiB；前端会将本地图片作为 data URL 随材料提交，
            # 因此旧表必须扩容。LONGTEXT 同样兼容，避免无意义地缩小已手工扩容的列。
            if request_json_type not in {"MEDIUMTEXT", "LONGTEXT"}:
                conn.execute(
                    text("ALTER TABLE analysis_jobs MODIFY COLUMN request_json MEDIUMTEXT NOT NULL")
                )
                logger.info("%s analysis_jobs.request_json 已升级为 MEDIUMTEXT", DB_LOG_PREFIX)
            if "uq_analysis_jobs_merchant_key_attempt" not in unique_names:
                if "uq_analysis_jobs_merchant_key" in unique_names:
                    conn.execute(text("ALTER TABLE analysis_jobs DROP INDEX uq_analysis_jobs_merchant_key"))
                conn.execute(
                    text(
                        "ALTER TABLE analysis_jobs ADD CONSTRAINT "
                        "uq_analysis_jobs_merchant_key_attempt "
                        "UNIQUE (merchant_id, idempotency_key, attempt)"
                    )
                )
        logger.info("%s analysis_jobs attempt 与唯一约束已核对", DB_LOG_PREFIX)

    if "dispute_cases" in table_names:
        unique_names = {item["name"] for item in inspector.get_unique_constraints("dispute_cases")}
        if "uq_dispute_cases_merchant_dispute" not in unique_names:
            _ensure_no_duplicate_pairs(engine, "dispute_cases", ("merchant_id", "dispute_id"))
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE dispute_cases ADD CONSTRAINT "
                        "uq_dispute_cases_merchant_dispute UNIQUE (merchant_id, dispute_id)"
                    )
                )
        logger.info("%s dispute_cases 复盘唯一约束已补齐", DB_LOG_PREFIX)


def _patch_event_payload_column_sizes(engine: Engine) -> None:
    """
    将分析事件与最终报告 JSON 列从 TEXT（约 64 KiB）升级为 MEDIUMTEXT（约 16 MiB）。

    前端会把本地图片以 data URL 随材料提交；stage_done/final_report 事件负载与
    最终报告均包含这些图片引用，TEXT 会触发 MySQL Data too long for column。
    """
    if engine.dialect.name != "mysql":
        return
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    patches: list[tuple[str, str, str]] = []

    if "analysis_events" in table_names:
        columns = {column["name"]: column for column in inspector.get_columns("analysis_events")}
        payload_column = columns.get("payload_json")
        payload_type = str(payload_column["type"]).upper() if payload_column is not None else ""
        if payload_type not in {"MEDIUMTEXT", "LONGTEXT"}:
            patches.append(("analysis_events", "payload_json", "MEDIUMTEXT NOT NULL"))

    if "analysis_jobs" in table_names:
        columns = {column["name"]: column for column in inspector.get_columns("analysis_jobs")}
        report_column = columns.get("report_json")
        report_type = str(report_column["type"]).upper() if report_column is not None else ""
        if report_type not in {"MEDIUMTEXT", "LONGTEXT"}:
            patches.append(("analysis_jobs", "report_json", "MEDIUMTEXT NULL"))

    if not patches:
        return
    with engine.begin() as conn:
        for table_name, column_name, column_def in patches:
            conn.execute(text(f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} {column_def}"))
            logger.info(
                "%s %s.%s 已升级为 %s",
                DB_LOG_PREFIX,
                table_name,
                column_name,
                column_def.split()[0],
            )


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
        _patch_dispute_cases_columns(engine)
        _patch_consistency_constraints(engine)
        _patch_event_payload_column_sizes(engine)
        logger.info("%s 数据库建表完成", DB_LOG_PREFIX)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 数据库建表失败：%s", DB_LOG_PREFIX, exc)
        raise RuntimeError(f"{DB_LOG_PREFIX} 数据库建表失败：{exc}") from exc
