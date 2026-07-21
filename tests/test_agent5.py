"""
Agent5（复盘分析师）核心契约测试。

原则：结构化复盘、策略分歧标签、判例写入与异步入口各保留一条路径。
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.agents.agent5.reviewer import review
import backend.agents.agent5.reviewer as reviewer_module
from backend.tasks.review_task import async_review
import backend.tools.agent5_tools as agent5_tools_module
from backend.db.models import Base, DisputeCase
from backend.tools.agent5_tools import save_case_to_db
from schemas import CaseScenario, ReviewInput


@pytest.fixture(autouse=True)
def _review_uses_rule_fallback(monkeypatch):
    """单测固定走规则复盘，避免全量跑批时 LLM 返回不稳定。"""
    monkeypatch.setattr(reviewer_module, "generate_review_card", lambda **_kwargs: None)


class TestAgent5Review:
    def test_review_structured_output_and_strategy_tags(self):
        """胜诉+采纳 AI 与未采纳失败：应输出结构化卡片与正确标签。"""
        win = review(
            ReviewInput(
                dispute_id="D5-001",
                full_timeline={
                    "strategy_output": {"strategy": "defend"},
                    "fact_summary": "买家无法提供拆封视频，吊牌完整。",
                    "merchant_action": "提交质检与出库记录进行抗辩",
                },
                final_outcome="胜",
                ai_strategy_adopted=True,
            )
        )
        assert win.case_type == "质量争议_抗辩"
        assert win.outcome == "胜"
        assert "strategy:defend" in win.tags
        assert "ai_adopted" in win.tags
        assert isinstance(win.scenario, CaseScenario)

        loss = review(
            ReviewInput(
                dispute_id="D5-002",
                full_timeline={
                    "strategy_output": {"strategy": "negotiate"},
                    "key_facts": "买家多次强调色差且拒绝补图。",
                    "merchant_action_taken": "坚持不退导致平台介入",
                },
                final_outcome="败",
                ai_strategy_adopted=False,
            )
        )
        assert loss.outcome == "败"
        assert "strategy_gap" in loss.tags
        assert "分歧" in loss.lesson_text

    def test_review_boundary_should_return_generic_case_for_empty_timeline(self):
        """空轨迹边界：应降级为通用纠纷。"""
        output = review(
            ReviewInput(
                dispute_id="D5-003",
                full_timeline={},
                final_outcome="升级",
                ai_strategy_adopted=False,
            )
        )
        assert output.case_type == "通用纠纷"
        assert output.key_facts.startswith("升级；")


class TestAgent5Persistence:
    @patch("backend.tools.agent5_tools.Session")
    @patch("backend.tools.agent5_tools.get_engine")
    def test_save_case_to_db_valid_and_invalid(self, mock_engine, mock_session_cls):
        """合法输入写入成功；缺失 merchant_id 返回失败。"""
        mock_engine.return_value = MagicMock()
        session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = session
        session.scalar.return_value = None

        card = review(
            ReviewInput(
                dispute_id="D5-004",
                full_timeline={"strategy": "compensate", "facts_summary": "物流延迟3天"},
                final_outcome="和解",
                ai_strategy_adopted=True,
            )
        )
        assert save_case_to_db(review=card, merchant_id="M001", dispute_id="D5-004") is True
        session.add.assert_called_once()
        session.commit.assert_called_once()

        invalid_card = review(
            ReviewInput(
                dispute_id="D5-005",
                full_timeline={"strategy": "defend"},
                final_outcome="胜",
                ai_strategy_adopted=True,
            )
        )
        assert save_case_to_db(review=invalid_card, merchant_id="", dispute_id="D5-005") is False

    def test_save_case_to_db_should_upsert_same_merchant_and_dispute(self, tmp_path, monkeypatch):
        """同一商家、纠纷的重复复盘应更新同一行，不得累积重复判例。"""
        database_path = tmp_path / "agent5.sqlite3"
        engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(engine)
        monkeypatch.setattr(agent5_tools_module, "get_engine", lambda: engine)

        first = review(
            ReviewInput(
                dispute_id="D5-UPSERT",
                full_timeline={"strategy": "defend", "fact_summary": "首次复盘"},
                final_outcome="胜",
                ai_strategy_adopted=True,
            )
        )
        second = review(
            ReviewInput(
                dispute_id="D5-UPSERT",
                full_timeline={"strategy": "compensate", "fact_summary": "二次复盘"},
                final_outcome="和解",
                ai_strategy_adopted=False,
            )
        )

        assert save_case_to_db(first, "M-UPSERT", "D5-UPSERT") is True
        assert save_case_to_db(second, "M-UPSERT", "D5-UPSERT") is True

        with Session(engine) as session:
            rows = list(
                session.scalars(
                    select(DisputeCase).where(
                        DisputeCase.merchant_id == "M-UPSERT",
                        DisputeCase.dispute_id == "D5-UPSERT",
                    )
                )
            )
        assert len(rows) == 1
        assert rows[0].outcome == "和解"


class TestAgent5Task:
    @patch("backend.tasks.review_task.save_case_to_db", return_value=True)
    def test_async_review_valid_and_invalid(self, _mock_save):
        """合法异步输入走通；缺失必填字段应抛异常交给 Celery 重试。"""
        valid_payload = {
            "dispute_id": "D5-006",
            "full_timeline": {
                "ai_strategy": "compensate",
                "facts_summary": "买家反馈做工问题",
                "final_action": "补偿部分退款",
            },
            "final_outcome": "和解",
            "ai_strategy_adopted": True,
            "outcome_note": "",
        }
        assert async_review.run(review_input_dict=valid_payload, merchant_id="M006") is True

        invalid_payload = {
            "dispute_id": "D5-007",
            "full_timeline": {},
            "ai_strategy_adopted": False,
        }
        with pytest.raises(Exception):
            async_review.run(review_input_dict=invalid_payload, merchant_id="M007")
