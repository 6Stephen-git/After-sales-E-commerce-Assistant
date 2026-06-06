"""CLI 直接执行 pipeline 脚本时，将仓库根目录加入 sys.path。"""

from __future__ import annotations

import sys

from eval.pipeline.paths import ROOT_DIR


def ensure_repo_on_path() -> None:
    """保证可从仓库根导入 backend、schemas、eval。"""
    root = str(ROOT_DIR)
    if root not in sys.path:
        sys.path.insert(0, root)
