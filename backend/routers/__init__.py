"""
路由模块导出。

职责：统一暴露 API Router，供 main.py 集中注册。
"""

from backend.routers.analyze import router as analyze_router
from backend.routers.buyers import router as buyers_router
from backend.routers.intelligent import router as intelligent_router
from backend.routers.merchants import router as merchants_router

__all__ = ["analyze_router", "buyers_router", "intelligent_router", "merchants_router"]
