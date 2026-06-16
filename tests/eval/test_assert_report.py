"""assert_report 硬断言单元测试。"""

from __future__ import annotations

from eval.pipeline.assert_report import assert_report_fields


def _minimal_report(**strategy_overrides: object) -> dict:
    """构造最小 AnalysisReport 形 JSON。"""
    strategy = {
        "disposition": "negotiate",
        "action_type": "rule_explain",
        "strategy_stage": "negotiate_settle",
        "reasoning": "测试推理",
        "malicious_detection": {"risk_level": "low", "triggered_signals": []},
        "customer_value": {"channel": "none"},
        "dialogue_context": {"actionable_evidence_requests": []},
    }
    strategy.update(strategy_overrides)
    return {
        "dispute_id": "DISPUTE-TEST",
        "strategy": strategy,
        "matched_rules": [],
        "similar_cases": [],
    }


def test_empty_expected_report_passes() -> None:
    """无 expected_report 约束时应 pass。"""
    result = assert_report_fields(
        case_id="T-01",
        expected={},
        report=_minimal_report(),
    )
    assert result.pass_ is True
    assert result.checked_count == 0


def test_customer_value_channel_exact() -> None:
    """customer_value_channel 精确匹配。"""
    result = assert_report_fields(
        case_id="T-02",
        expected={"customer_value_channel": "long_term"},
        report=_minimal_report(customer_value={"channel": "long_term"}),
    )
    assert result.pass_ is True

    fail = assert_report_fields(
        case_id="T-02",
        expected={"customer_value_channel": "long_term"},
        report=_minimal_report(customer_value={"channel": "none"}),
    )
    assert fail.pass_ is False
    assert any("customer_value_channel" in line for line in fail.failures)


def test_malicious_risk_level_in_and_signal_types() -> None:
    """恶意等级与硬规则信号断言。"""
    report = _minimal_report(
        malicious_detection={
            "risk_level": "high",
            "triggered_signals": [
                {"signal_type": "abuse_refund_only", "description": "x", "score": 20, "source": "hard_rule"}
            ],
        }
    )
    ok = assert_report_fields(
        case_id="T-03",
        expected={
            "malicious_risk_level_in": ["medium", "high"],
            "triggered_signal_types_contains": ["abuse_refund_only"],
        },
        report=report,
    )
    assert ok.pass_ is True

    bad = assert_report_fields(
        case_id="T-03",
        expected={"action_type_not": ["evidence_request"]},
        report=_minimal_report(action_type="evidence_request"),
    )
    assert bad.pass_ is False


def test_actionable_evidence_requests_contains() -> None:
    """专责补证请求子串匹配。"""
    report = _minimal_report(
        dialogue_context={"actionable_evidence_requests": ["请补充多角度穿着照片以确认二次销售影响"]},
    )
    ok = assert_report_fields(
        case_id="T-04",
        expected={"actionable_evidence_requests_contains": ["多角度"]},
        report=report,
    )
    assert ok.pass_ is True


def test_invalid_matched_rules_min_fails_parse() -> None:
    """matched_rules_min 非法时应 fail 而非静默通过。"""
    result = assert_report_fields(
        case_id="T-05",
        expected={"matched_rules_min": "not_a_number"},
        report=_minimal_report(),
    )
    assert result.pass_ is False
    assert any("matched_rules_min" in line for line in result.failures)


def test_actionable_evidence_requests_max_zero() -> None:
    """举证已齐案禁止向买家索要泛化补证。"""
    ok = assert_report_fields(
        case_id="T-06",
        expected={"actionable_evidence_requests_max": 0},
        report=_minimal_report(
            dialogue_context={"actionable_evidence_requests": []},
        ),
    )
    assert ok.pass_ is True
    bad = assert_report_fields(
        case_id="T-06",
        expected={"actionable_evidence_requests_max": 0},
        report=_minimal_report(
            dialogue_context={"actionable_evidence_requests": ["快递单照片"]},
        ),
    )
    assert bad.pass_ is False
