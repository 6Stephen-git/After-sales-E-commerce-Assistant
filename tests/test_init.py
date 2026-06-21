"""
项目初始化冒烟测试：schemas、规则索引、目录骨架、健康端点。

原则：仅验证仓库可导入与关键骨架存在；业务契约以 Agent/评测树为准。
"""

import importlib.util
import json
import os
import sys

from fastapi.testclient import TestClient

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

LEXICON_PATH = os.path.join(ROOT_DIR, "data", "rule_match_lexicon.json")

REQUIRED_DIRS = (
    "backend",
    "backend/agents",
    "backend/controllers",
    "backend/tools",
    "data",
    "frontend",
    "tests",
    "docs",
)

REQUIRED_FILES = (
    "schemas.py",
    "backend/main.py",
    "data/rule_match_lexicon.json",
    "frontend/package.json",
    ".env.example",
)


def test_schemas_core_models_instantiate():
    """核心 Pydantic 模型与枚举可实例化。"""
    from schemas import (
        AnalysisReport,
        DISPOSITION_DEFEND,
        DISPOSITION_NEGOTIATE,
        EVIDENCE_HIGH,
        EVIDENCE_LOW,
        EVIDENCE_MEDIUM,
        FactOutput,
        ScriptOutput,
        StrategyOutput,
        VALID_DISPOSITIONS,
        VALID_EVIDENCE_QUALITY,
    )

    assert DISPOSITION_DEFEND in VALID_DISPOSITIONS
    assert EVIDENCE_HIGH in VALID_EVIDENCE_QUALITY
    assert FactOutput().evidence_quality == EVIDENCE_MEDIUM
    assert StrategyOutput(disposition=DISPOSITION_DEFEND).disposition == DISPOSITION_DEFEND
    report = AnalysisReport(
        dispute_id="D-smoke",
        facts=FactOutput(evidence_quality=EVIDENCE_LOW),
        strategy=StrategyOutput(disposition=DISPOSITION_NEGOTIATE),
        scripts=ScriptOutput(),
    )
    assert report.dispute_id == "D-smoke"
    assert report.matched_rules == []


def test_rule_match_lexicon_valid():
    """规则索引文件存在且含 docs 与 lanes。"""
    assert os.path.isfile(LEXICON_PATH)
    with open(LEXICON_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)
    assert isinstance(data.get("docs"), list) and len(data["docs"]) >= 1
    assert isinstance(data.get("lanes"), dict) and data["lanes"].get("A_base")


def test_project_layout_complete():
    """关键目录与入口文件齐全。"""
    missing_dirs = [path for path in REQUIRED_DIRS if not os.path.isdir(os.path.join(ROOT_DIR, path))]
    missing_files = [path for path in REQUIRED_FILES if not os.path.isfile(os.path.join(ROOT_DIR, path))]
    assert not missing_dirs, f"缺少目录：{missing_dirs}"
    assert not missing_files, f"缺少文件：{missing_files}"


def test_health_endpoint():
    """FastAPI 应用可加载，/health 返回 ok。"""
    spec = importlib.util.spec_from_file_location(
        "backend_main",
        os.path.join(ROOT_DIR, "backend", "main.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "app")

    response = TestClient(module.app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
