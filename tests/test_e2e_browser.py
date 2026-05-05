"""
辅助模式浏览器端到端测试。

覆盖：分栏布局、手动触发分析、话术填入输入框。
"""

from __future__ import annotations

import json

import pytest


# ---------- 依赖守卫：未安装 Playwright 时跳过该测试文件 ----------
playwright_sync_api = pytest.importorskip("playwright.sync_api")


# ---------- Mock 报告：模拟 /api/analyze 返回结构 ----------
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
        "red_flags": [],
        "evidence_quality": "high",
        "confidence": 0.86,
        "uncertainty_note": None,
    },
    "strategy": {
        "strategy": "defend",
        "reasoning": "证据充分且风险低，优先抗辩。",
        "estimated_win_rate": 0.78,
        "risk_factors": [],
        "confidence": 0.82,
    },
    "scripts": {
        "defense_version": "您好，订单已核实，基于证据我们建议先走平台复核流程。",
        "negotiate_version": "您好，我们可以先协商部分补偿，请您确认诉求。",
        "compensate_version": "您好，问题已确认，我们可按流程为您办理补偿。",
        "recommended_version": "defense_version",
    },
    "emotion_alert": None,
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

        # E5：商家点击按钮触发分析并展示三块结果卡片。
        page.get_by_role("button", name="请求 AI 帮助").click()
        expect(page.get_by_text("事实摘要")).to_be_visible()
        expect(page.get_by_text("策略建议")).to_be_visible()
        expect(page.get_by_text("话术选项")).to_be_visible()

        # E6：展开话术折叠项（仅此一处匹配 collapse header，避免与「推荐版本：抗辩版」告警标题冲突）。
        page.locator(".strategy-panel .el-collapse-item__header").filter(has_text="抗辩版").click()
        page.get_by_role("button", name="使用该话术").first.click()
        expect(page.locator("textarea")).to_have_value(
            "您好，订单已核实，基于证据我们建议先走平台复核流程。"
        )

        context.close()
        browser.close()
