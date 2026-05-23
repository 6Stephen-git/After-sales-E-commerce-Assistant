"""
辅助模式浏览器端到端测试。

覆盖：分栏布局、手动触发分析、话术填入输入框。
"""

from __future__ import annotations

import json
import re

import pytest


# ---------- 依赖守卫：未安装 Playwright 时跳过该测试文件 ----------
playwright_sync_api = pytest.importorskip("playwright.sync_api")


# ---------- Mock 报告：模拟 /api/analyze 返回结构（对齐 A2-6/A2-7 新 schema） ----------
MOCK_ANALYZE_REPORT = {
    "dispute_id": "DISPUTE_DEMO_001",
    "facts": {
        "goods_received": True,
        "defect_type": "破洞",
        "defect_location": "袖口",
        "defect_edge": "撕裂",
        "has_tag_visible": True,
        "photo_background": "平铺",
        "wear_signs": "轻微",
        "logistics_normal": True,
        "missing_evidence": [],
        "red_flags": ["fake_evidence:图片带有非实拍水印"],
        "evidence_quality": "high",
        "confidence": 0.86,
        "uncertainty_note": None,
        "issue_summary": "买家反馈袖口存在撕裂破洞",
        "intent_tags": ["质量问题"],
        "visual_observations": [
            "袖口可见线性撕裂",
            "面料为深灰色针织",
            "吊牌信息清晰可辨",
            "背景为室内白桌",
        ],
        "attributes": {},
        "evidence_items": [],
    },
    "strategy": {
        "disposition": "defend",
        "customer_intent_analysis": "主诉为质量问题维权，诉求聚焦袖口破损与退换货。",
        "strategy_direction_summary": "先固定己方证据链，引导买家按规则补充完整开箱视频，暂不承诺退款。",
        "strategy_direction_rationale": "现有图文不足以闭环认定划痕责任；规则要求物理损伤须完整开箱视频；补证有利于抗辩并控制损失。",
        "reasoning": "客户意图：质量问题维权。\n风险点：举证不足。\n建议动作：先固定己方证据链，引导买家按规则补充完整开箱视频，暂不承诺退款。\n推理理由：现有图文不足以闭环认定划痕责任；规则要求物理损伤须完整开箱视频；补证有利于抗辩并控制损失。",
        "estimated_win_rate": 0.78,
        "risk_factors": [],
        "confidence": 0.82,
        "policy_ref": "R002,R004",
        "customer_value": {
            "long_term_score": 65,
            "order_score": 40,
            "long_term_breakdown": [],
            "order_breakdown": [],
            "long_term_triggered": False,
            "order_triggered": False,
            "channel": "none",
            "compensation_uplift": None,
            "tone_suggestion": "保持专业克制",
        },
        "malicious_detection": {
            "risk_score": 12,
            "risk_level": "low",
            "triggered_signals": [],
            "hard_rule_summary": "硬规则层未命中异常项。",
            "malicious_risk_hints": "当前未命中明确恶意行为信号。",
            "disposition_advice": "",
        },
    },
    "matched_rules": [
        {
            "rule_id": "R002",
            "rule_summary": "质量问题需完整开箱视频举证",
            "condition_result": "买家已上传照片，建议策略:defend",
        }
    ],
    "scripts": {
        "defense_version": "您好，订单已核实，基于证据我们建议先走平台复核流程。",
        "negotiate_version": "您好，我们可以先协商部分补偿，请您确认诉求。",
        "compensate_version": "您好，问题已确认，我们可按流程为您办理补偿。",
        "recommended_version": "defense_version",
    },
    "emotion_alert": None,
    "buyer_profile": {
        "buyer_id": "buyer_demo",
        "purchase_count": 6,
        "dispute_count": 1,
        "dispute_rate": 0.16,
        "avg_order_value": 128.0,
        "return_rate": 0.08,
        "malicious_flags": 0,
        "positive_review_count": 3,
        "credit_level": "high",
    },
    "similar_cases": [
        {
            "case_id": "CASE-2025-0042",
            "similarity": 0.88,
            "merchant_action": "提交质检照片与出库记录进行抗辩",
            "outcome": "平台支持商家",
            "lesson": "高清晰实物照片 + 出库记录可有效对抗无拆封视频的退款申请",
        },
        {
            "case_id": "CASE-2025-0107",
            "similarity": 0.79,
            "merchant_action": "提供物流签收截图与买家确认收货聊天记录",
            "outcome": "协商成功",
            "lesson": "物流签收截图配合买家确认收货的聊天记录，可作为补充证据提升抗辩成功率",
        },
    ],
}


# ---------- 场景 E5+E6：布局展示、触发分析、话术填入 ----------
def test_e2e_browser_assisted_flow_should_render_report_and_apply_script(frontend_server: str) -> None:
    """
    访问辅助模式页面后，点击分析并将话术填入聊天输入框。
    """
    with playwright_sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()

        # 通过路由拦截稳定返回分析结果，避免依赖外部后端可用性。
        context.route(
            "**/api/analyze",
            lambda route: route.fulfill(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(MOCK_ANALYZE_REPORT, ensure_ascii=False),
            ),
        )

        page = context.new_page()
        page.goto(f"{frontend_server}/dispute", wait_until="networkidle")

        # E5：验证辅助模式左右分栏存在。
        expect = playwright_sync_api.expect
        expect(page.locator(".chat-column")).to_have_count(1)
        expect(page.locator(".strategy-column")).to_have_count(1)

        # E5：商家点击按钮触发分析并展示核心结论区。
        page.get_by_role("button", name="请求 AI 帮助").click()
        expect(page.get_by_text("策略建议")).to_be_visible()
        expect(page.get_by_text("核心结论区")).to_be_visible()
        expect(page.get_by_text("客户意图分析")).to_be_visible()
        expect(page.get_by_text("主诉为质量问题维权")).to_be_visible()
        expect(page.get_by_text("策略方向")).to_be_visible()
        expect(page.get_by_text("推理理由")).to_be_visible()
        expect(page.get_by_text("预估胜率")).to_be_visible()
        expect(page.get_by_text("策略置信度")).to_be_visible()

        # E5：关键依据区与事实摘要（视觉描述折叠）。
        expect(page.get_by_text("关键依据区")).to_be_visible()
        expect(page.get_by_text("疑点列表")).to_be_visible()
        expect(page.get_by_text("疑似虚假举证")).to_be_visible()
        expect(page.get_by_text("平台规则依据")).to_be_visible()
        # 结构化 matched_rules 与 policy_ref 兜底均可能含 R002
        expect(page.locator(".strategy-column").get_by_text(re.compile(r"R002"))).to_be_visible()
        expect(page.get_by_text("恶意风险提示")).to_be_visible()
        expect(page.get_by_text("聚合说明")).to_be_visible()
        expect(page.get_by_text("当前未命中明确恶意行为信号")).to_be_visible()
        expect(page.get_by_text("客户价值提示")).to_be_visible()
        expect(page.get_by_text("未触发优待通道")).to_be_visible()
        expect(page.get_by_text("保持专业克制")).to_be_visible()
        expect(page.locator(".direction-text").filter(has_text="协商与补偿口径")).to_be_visible()
        expect(page.get_by_text("其余 1 条（点击展开）")).to_be_visible()

        # E5：话术选项卡片可见。
        expect(page.get_by_text("话术选项")).to_be_visible()

        # E5：情绪提醒位于关键依据区之后（不崩溃即可，无内容时为空）。
        # 此处不强制断言 EmotionAlert 内容，仅确认页面结构完整。

        # E6：展开话术折叠项（仅此一处匹配 collapse header，避免与「推荐版本：抗辩版」告警标题冲突）。
        page.locator(".strategy-panel .el-collapse-item__header").filter(has_text="抗辩版").click()
        page.get_by_role("button", name="使用该话术").first.click()
        expect(page.locator("textarea")).to_have_value(
            "您好，订单已核实，基于证据我们建议先走平台复核流程。"
        )

        context.close()
        browser.close()
