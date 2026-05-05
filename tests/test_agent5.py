"""
Agent5（复盘分析师）模块测试
覆盖：复盘输出结构、策略分歧标签、判例写入、异步任务入口
"""

import os
import sys


# ---------- 与仓库根对齐的导入路径 ----------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


from backend.agents.agent5.reviewer import review
from backend.tasks.review_task import async_review
from backend.tools.agent5_tools import save_case_to_db
from schemas import ReviewInput


# ---------- review：胜诉采纳、失败未采纳、空轨迹边界 ----------
class TestAgent5Review:
    def test_review_should_generate_structured_card_for_win_case(self):
        """胜诉 + 采纳 AI：应输出结构化经验卡片。"""
        output = review(
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
        assert output.case_type == "质量争议_抗辩"
        assert output.outcome == "胜"
        assert "strategy:defend" in output.tags
        assert "ai_adopted" in output.tags

    def test_review_should_mark_strategy_gap_when_not_adopted(self):
        """未采纳 AI 且失败：应体现策略分歧标签。"""
        output = review(
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
        assert output.outcome == "败"
        assert "strategy_gap" in output.tags
        assert "分歧" in output.lesson_text

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


# ---------- save_case_to_db：参数校验与写入结果 ----------
class TestAgent5Tools:
    def test_save_case_to_db_should_return_true_for_valid_input(self):
        """合法输入应返回写入成功。"""
        card = review(
            ReviewInput(
                dispute_id="D5-004",
                full_timeline={"strategy": "compensate", "facts_summary": "物流延迟3天"},
                final_outcome="和解",
                ai_strategy_adopted=True,
            )
        )
        assert save_case_to_db(review=card, merchant_id="M001") is True

    def test_save_case_to_db_should_return_false_for_invalid_merchant_id(self):
        """缺失 merchant_id 应返回写入失败。"""
        card = review(
            ReviewInput(
                dispute_id="D5-005",
                full_timeline={"strategy": "defend"},
                final_outcome="胜",
                ai_strategy_adopted=True,
            )
        )
        assert save_case_to_db(review=card, merchant_id="") is False


# ---------- async_review：任务入口序列化/反序列化 ----------
class TestAgent5Task:
    def test_async_review_should_return_true_for_valid_payload(self):
        """合法异步输入应走通复盘与保存流程。"""
        payload = {
            "dispute_id": "D5-006",
            "full_timeline": {
                "ai_strategy": "compensate",
                "facts_summary": "买家反馈做工问题",
                "final_action": "补偿部分退款",
            },
            "final_outcome": "和解",
            "ai_strategy_adopted": True,
        }
        assert async_review(review_input_dict=payload, merchant_id="M006") is True

    def test_async_review_should_return_false_when_payload_invalid(self):
        """缺失必填字段时应返回 False。"""
        invalid_payload = {
            "dispute_id": "D5-007",
            "full_timeline": {},
            # final_outcome 缺失
            "ai_strategy_adopted": False,
        }
        assert async_review(review_input_dict=invalid_payload, merchant_id="M007") is False
