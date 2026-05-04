"""
商家应诉助手 — FastAPI 后端入口
"""

import sys
import os

from fastapi import FastAPI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 将项目根目录加入模块搜索路径，保证 schemas.py 可直接导入
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import schemas  # noqa: E402（依赖路径注入，必须在 sys.path 调整后导入）

app = FastAPI(
    title="商家应诉助手",
    description="电商纠纷辅助分析系统 API",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    """健康检查端点，验证服务可用性"""
    return {"status": "ok", "version": app.version}
