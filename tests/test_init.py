"""
阶段一初始化验收测试
验收项：schemas 可导入、规则库合法、目录结构完整、FastAPI 健康端点可用
"""

import json
import os
import sys

import pytest

# 将项目根目录加入模块搜索路径
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


# ============================================================
# 1. schemas.py 可导入且所有 Pydantic 类可实例化
# ============================================================

class TestSchemas:
    def test_import_schemas(self):
        """schemas.py 可正常导入"""
        import schemas
        assert schemas is not None

    def test_fact_output_instantiation(self):
        """FactOutput 可使用默认值实例化"""
        from schemas import FactOutput
        obj = FactOutput()
        assert obj.evidence_quality == "medium"
        assert isinstance(obj.missing_evidence, list)
        assert isinstance(obj.red_flags, list)

    def test_buyer_profile_instantiation(self):
        """BuyerProfile 可实例化，必填字段 buyer_id 需传入"""
        from schemas import BuyerProfile
        obj = BuyerProfile(buyer_id="test_hash_abc123")
        assert obj.buyer_id == "test_hash_abc123"
        assert obj.dispute_rate >= 0.0

    def test_strategy_output_instantiation(self):
        """StrategyOutput 可实例化，必填字段 disposition 需传入"""
        from schemas import StrategyOutput, DISPOSITION_DEFEND
        obj = StrategyOutput(disposition=DISPOSITION_DEFEND)
        assert obj.disposition == "defend"
        assert obj.customer_intent_analysis == ""
        assert obj.strategy_direction_summary == ""
        assert obj.strategy_direction_rationale == ""
        assert obj.platform_rule_basis == []

    def test_script_output_instantiation(self):
        """ScriptOutput 可使用默认值实例化"""
        from schemas import ScriptOutput, RESPONSE_MODE_NEUTRAL_NEGOTIATE
        obj = ScriptOutput()
        assert obj.script == ""
        assert obj.response_mode == RESPONSE_MODE_NEUTRAL_NEGOTIATE

    def test_emotion_output_instantiation(self):
        """EmotionOutput 可使用默认值实例化"""
        from schemas import EmotionOutput
        obj = EmotionOutput()
        assert obj.sentiment == "neutral"
        assert obj.alert_triggered is False

    def test_review_output_instantiation(self):
        """ReviewOutput 可实例化，必填字段需传入"""
        from schemas import ReviewOutput
        obj = ReviewOutput(
            case_type="色差纠纷",
            key_facts="买家投诉色差",
            merchant_action_taken="协商退款",
            outcome="和解",
            lesson_text="及时跟进买家诉求"
        )
        assert obj.case_type == "色差纠纷"

    def test_analysis_report_instantiation(self):
        """AnalysisReport 聚合结构可实例化"""
        from schemas import AnalysisReport, FactOutput, StrategyOutput, ScriptOutput, DISPOSITION_DEFEND
        obj = AnalysisReport(
            dispute_id="D202600001",
            facts=FactOutput(),
            strategy=StrategyOutput(disposition=DISPOSITION_DEFEND),
            scripts=ScriptOutput(),
        )
        assert obj.dispute_id == "D202600001"
        assert obj.emotion_alert is None
        assert obj.matched_rules == []

    def test_valid_strategy_enums(self):
        """处置方向枚举值定义正确"""
        from schemas import VALID_DISPOSITIONS, DISPOSITION_DEFEND, DISPOSITION_NEGOTIATE, DISPOSITION_COMPENSATE
        assert DISPOSITION_DEFEND in VALID_DISPOSITIONS
        assert DISPOSITION_NEGOTIATE in VALID_DISPOSITIONS
        assert DISPOSITION_COMPENSATE in VALID_DISPOSITIONS

    def test_valid_evidence_quality_enums(self):
        """证据质量枚举值定义正确"""
        from schemas import VALID_EVIDENCE_QUALITY, EVIDENCE_HIGH, EVIDENCE_MEDIUM, EVIDENCE_LOW
        assert EVIDENCE_HIGH in VALID_EVIDENCE_QUALITY
        assert EVIDENCE_MEDIUM in VALID_EVIDENCE_QUALITY
        assert EVIDENCE_LOW in VALID_EVIDENCE_QUALITY

    def test_logistics_info_instantiation(self):
        """LogisticsInfo 可使用默认值实例化"""
        from schemas import LogisticsInfo
        obj = LogisticsInfo()
        assert obj.is_shipped is False
        assert obj.stagnant_days == 0


# ============================================================
# 2. rule_match_lexicon.json 存在且结构合法
# ============================================================

LEXICON_PATH = os.path.join(ROOT_DIR, "data", "rule_match_lexicon.json")


class TestRuleMatchLexicon:
    def test_file_exists(self):
        """data/rule_match_lexicon.json 文件存在"""
        assert os.path.isfile(LEXICON_PATH), f"规则索引文件不存在：{LEXICON_PATH}"

    def test_valid_json(self):
        """rule_match_lexicon.json 是合法 JSON"""
        with open(LEXICON_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        assert data is not None

    def test_has_docs_and_lanes(self):
        """索引包含 docs 与 lanes"""
        with open(LEXICON_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        assert isinstance(data.get("docs"), list)
        assert len(data["docs"]) >= 1
        assert isinstance(data.get("lanes"), dict)
        assert data["lanes"].get("A_base")


# ============================================================
# 3. 目录结构完整
# ============================================================

REQUIRED_DIRS = [
    "backend",
    "backend/agents",
    "backend/agents/agent1",
    "backend/agents/agent2",
    "backend/agents/agent3",
    "backend/agents/agent4",
    "backend/agents/agent5",
    "backend/controllers",
    "backend/tools",
    "backend/api",
    "data",
    "frontend",
    "frontend/src",
    "frontend/src/components",
    "frontend/src/views",
    "tests",
    "docs",
]

REQUIRED_FILES = [
    "schemas.py",
    "backend/main.py",
    "backend/requirements.txt",
    "data/rule_match_lexicon.json",
    "frontend/package.json",
    "frontend/vite.config.js",
    "frontend/src/main.js",
    "frontend/src/App.vue",
    ".env.example",
    "docs/Naming.md",
    "docs/Tools.md",
]

class TestDirectoryStructure:
    @pytest.mark.parametrize("dir_path", REQUIRED_DIRS)
    def test_required_directory_exists(self, dir_path):
        """必需目录存在"""
        full_path = os.path.join(ROOT_DIR, dir_path)
        assert os.path.isdir(full_path), f"目录不存在：{dir_path}"

    @pytest.mark.parametrize("file_path", REQUIRED_FILES)
    def test_required_file_exists(self, file_path):
        """必需文件存在"""
        full_path = os.path.join(ROOT_DIR, file_path)
        assert os.path.isfile(full_path), f"文件不存在：{file_path}"


# ============================================================
# 4. FastAPI 应用可正常创建，/health 端点响应正确
# ============================================================

class TestFastAPIApp:
    def test_app_import(self):
        """backend/main.py 可正常导入"""
        backend_dir = os.path.join(ROOT_DIR, "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "backend_main",
            os.path.join(ROOT_DIR, "backend", "main.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "app"), "backend/main.py 未暴露 app 对象"

    def test_health_endpoint(self):
        """FastAPI /health 端点返回 200 和正确响应体"""
        from fastapi.testclient import TestClient
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "backend_main",
            os.path.join(ROOT_DIR, "backend", "main.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        client = TestClient(module.app)
        response = client.get("/health")
        assert response.status_code == 200, f"期望 200，实际 {response.status_code}"
        body = response.json()
        assert body["status"] == "ok", f"期望 status=ok，实际 {body}"
        assert "version" in body, "响应体缺少 version 字段"
