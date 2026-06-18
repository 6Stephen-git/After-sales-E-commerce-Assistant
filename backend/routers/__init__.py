"""
路由模块导出。

职责：统一暴露 API Router，供 main.py 集中注册。
"""

from backend.routers.analyze import router as analyze_router
from backend.routers.buyers import router as buyers_router
from backend.routers.emotion import router as emotion_router
from backend.routers.merchants import router as merchants_router
from backend.routers.review import router as review_router

__all__ = ["analyze_router", "buyers_router", "emotion_router", "merchants_router", "review_router"]
