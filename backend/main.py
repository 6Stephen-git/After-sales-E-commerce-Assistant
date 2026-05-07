"""
商家应诉助手 — FastAPI 后端入口
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from backend.db import init_db
from backend.routers import analyze_router, buyers_router, merchants_router

# 加载环境变量
load_dotenv()

# 将项目根目录加入模块搜索路径，保证 schemas.py 可直接导入
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import schemas  # noqa: E402（依赖路径注入，必须在 sys.path 调整后导入）

API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)


# ---------- 生命周期：启动时初始化数据库 ----------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    应用生命周期：在 yield 之前执行启动逻辑（初始化数据库），之后可扩展关闭逻辑。
    """
    logger.info("%s 应用启动，开始初始化数据库", API_LOG_PREFIX)
    init_db()
    logger.info("%s 应用启动完成", API_LOG_PREFIX)
    yield


app = FastAPI(
    title="商家应诉助手",
    description="电商纠纷辅助分析系统 API",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------- 全局异常处理：统一返回结构 ----------
@app.middleware("http")
async def exception_middleware(request: Request, call_next):
    """
    统一捕获未处理异常并返回标准错误响应。
    """
    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 未处理异常，path=%s，原因=%s", API_LOG_PREFIX, request.url.path, exc)
        return JSONResponse(status_code=500, content={"error": f"服务内部异常：{exc}"})


# ---------- 路由注册：阶段四 API 端点 ----------
app.include_router(analyze_router)
app.include_router(merchants_router)
app.include_router(buyers_router)


@app.get("/health")
def health_check():
    """健康检查端点，验证服务可用性"""
    return {"status": "ok", "version": app.version}
