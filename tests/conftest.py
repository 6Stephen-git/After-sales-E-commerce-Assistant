"""
端到端测试公共夹具。

职责：启动/停止后端与前端测试进程，并提供稳定的健康探活能力。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


# ---------- 项目路径与固定端口：统一供 E2E 用例复用 ----------
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_PORT = 18080
FRONTEND_PORT = 15173
BACKEND_BASE_URL = f"http://127.0.0.1:{BACKEND_PORT}"
FRONTEND_BASE_URL = f"http://127.0.0.1:{FRONTEND_PORT}"
BACKEND_HEALTH_PATH = "/health"
TEST_DB_PATH = ROOT_DIR / "tests" / "tmp_e2e.sqlite3"


# ---------- 探活工具：轮询 HTTP 直到服务就绪 ----------
def _wait_http_ready(url: str, timeout_seconds: float = 30.0) -> None:
    """
    轮询目标 URL，直到返回 2xx/3xx 或超时抛错。
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2):  # noqa: S310（仅本地测试端口）
                return
        except (URLError, OSError):
            time.sleep(0.3)
    raise RuntimeError(f"服务启动超时：{url}")


# ---------- 进程回收：优先优雅终止，失败后强制杀进程 ----------
def _terminate_process(process: subprocess.Popen[str]) -> None:
    """
    停止子进程并等待退出，避免残留端口占用。
    """
    if process.poll() is not None:
        return

    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.terminate()

    try:
        process.wait(timeout=8)
        return
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


# ---------- 后端夹具：真实 uvicorn 进程 + 独立 SQLite ----------
@pytest.fixture(scope="session")
def backend_server() -> str:
    """
    启动真实后端服务，返回基础 URL。
    """
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    env = os.environ.copy()
    env["DB_URL"] = f"sqlite+pysqlite:///{TEST_DB_PATH.as_posix()}"
    env.setdefault("PYTHONUTF8", "1")

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(BACKEND_PORT),
        ],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        text=True,
    )

    try:
        _wait_http_ready(f"{BACKEND_BASE_URL}{BACKEND_HEALTH_PATH}", timeout_seconds=35.0)
        yield BACKEND_BASE_URL
    finally:
        _terminate_process(process)


# ---------- 前端夹具：Vite 预览服务，供浏览器 E2E 访问 ----------
@pytest.fixture(scope="session")
def frontend_server() -> str:
    """
    启动前端预览服务，返回页面基础 URL。
    """
    frontend_dir = ROOT_DIR / "frontend"
    dist_dir = frontend_dir / "dist"
    if not dist_dir.is_dir():
        pytest.skip("前端 dist 不存在，请先在 frontend 目录执行 npm run build")

    env = os.environ.copy()
    env.setdefault("BROWSER", "none")
    npm_command = "npm.cmd" if os.name == "nt" else "npm"

    process = subprocess.Popen(
        [
            npm_command,
            "run",
            "preview",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(FRONTEND_PORT),
            "--strictPort",
        ],
        cwd=str(frontend_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        text=True,
        shell=False,
    )

    try:
        _wait_http_ready(FRONTEND_BASE_URL, timeout_seconds=35.0)
        yield FRONTEND_BASE_URL
    finally:
        _terminate_process(process)
