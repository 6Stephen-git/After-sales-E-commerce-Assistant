"""
数据库模块导出。

职责：统一暴露连接与模型初始化入口，供路由层按需注入。
"""

from backend.db.connection import get_db_session, get_engine, init_db

__all__ = ["get_db_session", "get_engine", "init_db"]
