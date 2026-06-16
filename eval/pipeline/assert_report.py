"""
报告结构化硬断言：对照 spec.expectation.expected_report 与 report.json 字段，确定性 pass/fail。

用法:
  python -m eval.pipeline.assert_report \\
    --scenario-output eval/output/scenarios/ma-01_review_blackmail \\
    --report eval/output/manual_reports/MA-01_REVIEW_BLACKMAIL.json -v
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from eval.pipeline.assert_models import AssertRecord, AssertResult
from eval.pipeline.paths import EVAL_RUNS_DIR, ROOT_DIR

if load_dotenv is not None:
    load_dotenv(ROOT_DIR / ".env")

ASSERT_LOG_PREFIX = "[AssertReport]"
logger = logging.getLogger(__name__)

# ---------- 风险等级序（用于下限比较） ----------
_RISK_LEVEL_RANK = {"low": 1, "medium": 2, "high": 3}


def _load_json(path: Path) -> dict[str, Any]:
    """读取 JSON 对象文件。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"{ASSERT_LOG_PREFIX} 无法读取文件：{path}，原因：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{ASSERT_LOG_PREFIX} JSON 非法：{path}，原因：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{ASSERT_LOG_PREFIX} JSON 根节点必须是对象：{path}")
    return payload


def _resolve_spec_path(scenario_output: Path) -> Path:
    """解析 scenario 目录下的 spec.json。"""
    spec_path = scenario_output / "spec.json"
    if not spec_path.is_file():
        raise FileNotFoundError(f"{ASSERT_LOG_PREFIX} 缺少 spec.json：{spec_path}")
    return spec_path


def _as_str_list(value: Any) -> list[str]:
    """将配置值归一为去空字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _extract_expected_report(spec: dict[str, Any]) -> dict[str, Any]:
    """从 spec 取出 expected_report 字典。"""
    expectation = spec.get("expectation")
    if not isinstance(expectation, dict):
        return {}
    raw = expectation.get("expected_report")
    return dict(raw) if isinstance(raw, dict) else {}


def _report_view(report: dict[str, Any]) -> dict[str, Any]:
    """从 AnalysisReport JSON 提取断言常用字段。"""
    strategy = report.get("strategy") if isinstance(report.get("strategy"), dict) else {}
    malicious = strategy.get("malicious_detection") if isinstance(strategy.get("malicious_detection"), dict) else {}
    customer_value = strategy.get("customer_value") if isinstance(strategy.get("customer_value"), dict) else {}
    dialogue = strategy.get("dialogue_context") if isinstance(strategy.get("dialogue_context"), dict) else {}

    signals = malicious.get("triggered_signals") or []
    signal_types: list[str] = []
    if isinstance(signals, list):
        for item in signals:
            if isinstance(item, dict):
                st = str(item.get("signal_type") or "").strip()
                if st:
                    signal_types.append(st)

    reasoning_parts = [
        str(strategy.get("reasoning") or ""),
        str(strategy.get("strategy_direction_rationale") or ""),
        str(strategy.get("customer_intent_analysis") or ""),
    ]
    reasoning_blob = "\n".join(part for part in reasoning_parts if part)

    actionable = dialogue.get("actionable_evidence_requests") or []
    actionable_list = [str(x).strip() for x in actionable if str(x).strip()] if isinstance(actionable, list) else []

    matched_rules = report.get("matched_rules") or []
    similar_cases = report.get("similar_cases") or []

    return {
        "malicious_risk_level": str(malicious.get("risk_level") or "").strip().lower(),
        "triggered_signal_types": signal_types,
        "customer_value_channel": str(customer_value.get("channel") or "none").strip().lower(),
        "disposition": str(strategy.get("disposition") or "").strip().lower(),
        "action_type": str(strategy.get("action_type") or "").strip().lower(),
        "strategy_stage": str(strategy.get("strategy_stage") or "").strip().lower(),
        "matched_rules_count": len(matched_rules) if isinstance(matched_rules, list) else 0,
        "similar_cases_count": len(similar_cases) if isinstance(similar_cases, list) else 0,
        "actionable_evidence_requests": actionable_list,
        "reasoning_blob": reasoning_blob,
    }


def _fail(
    failures: list[str],
    *,
    field: str,
    expected: Any,
    actual: Any,
) -> None:
    """追加一条可读失败说明。"""
    failures.append(f"{field}：期望 {expected!r}，实际 {actual!r}")


def _risk_meets_minimum(actual: str, minimum: str) -> bool:
    """判定实际风险等级是否不低于下限。"""
    actual_rank = _RISK_LEVEL_RANK.get(actual, 0)
    min_rank = _RISK_LEVEL_RANK.get(minimum, 0)
    if actual_rank == 0 or min_rank == 0:
        return False
    return actual_rank >= min_rank


def _parse_non_negative_int(raw: Any, *, field: str, failures: list[str]) -> int | None:
    """
    将 expected_report 中的数量下限解析为非负整数。

    解析失败时写入 failures 并返回 None，避免静默按 0 处理导致误 pass。
    """
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _fail(failures, field=field, expected="非负整数", actual=raw)
        return None
    if value < 0:
        _fail(failures, field=field, expected="非负整数", actual=raw)
        return None
    return value


def assert_report_fields(
    *,
    case_id: str,
    expected: dict[str, Any],
    report: dict[str, Any],
) -> AssertResult:
    """
    对照 expected_report 与 report.json 执行硬断言。

    expected 为空时视为无约束，pass=True。
    """
    failures: list[str] = []
    checked = 0
    view = _report_view(report)

    # ---------- 恶意风险等级（集合或下限） ----------
    levels_in = _as_str_list(expected.get("malicious_risk_level_in"))
    if levels_in:
        checked += 1
        actual = view["malicious_risk_level"]
        if actual not in {level.lower() for level in levels_in}:
            _fail(failures, field="malicious_risk_level", expected=levels_in, actual=actual)

    min_level = str(expected.get("malicious_risk_level_min") or "").strip().lower()
    if min_level:
        checked += 1
        if not _risk_meets_minimum(view["malicious_risk_level"], min_level):
            _fail(
                failures,
                field="malicious_risk_level_min",
                expected=f">={min_level}",
                actual=view["malicious_risk_level"],
            )

    # ---------- 恶意信号类型 ----------
    for signal_type in _as_str_list(expected.get("triggered_signal_types_contains")):
        checked += 1
        if signal_type not in view["triggered_signal_types"]:
            _fail(
                failures,
                field="triggered_signal_types_contains",
                expected=signal_type,
                actual=view["triggered_signal_types"],
            )

    for signal_type in _as_str_list(expected.get("triggered_signal_types_not_contains")):
        checked += 1
        if signal_type in view["triggered_signal_types"]:
            _fail(
                failures,
                field="triggered_signal_types_not_contains",
                expected=f"不含 {signal_type}",
                actual=view["triggered_signal_types"],
            )

    # ---------- 客户价值通道 ----------
    channel_exact = str(expected.get("customer_value_channel") or "").strip().lower()
    if channel_exact:
        checked += 1
        if view["customer_value_channel"] != channel_exact:
            _fail(
                failures,
                field="customer_value_channel",
                expected=channel_exact,
                actual=view["customer_value_channel"],
            )

    channels_in = _as_str_list(expected.get("customer_value_channel_in"))
    if channels_in:
        checked += 1
        allowed = {c.lower() for c in channels_in}
        if view["customer_value_channel"] not in allowed:
            _fail(
                failures,
                field="customer_value_channel_in",
                expected=list(allowed),
                actual=view["customer_value_channel"],
            )

    # ---------- 处置方向 ----------
    disposition_in = _as_str_list(expected.get("disposition_in"))
    if disposition_in:
        checked += 1
        allowed = {d.lower() for d in disposition_in}
        if view["disposition"] not in allowed:
            _fail(failures, field="disposition_in", expected=list(allowed), actual=view["disposition"])

    disposition_not = _as_str_list(expected.get("disposition_not"))
    for blocked in disposition_not:
        checked += 1
        if view["disposition"] == blocked.lower():
            _fail(failures, field="disposition_not", expected=f"非 {blocked}", actual=view["disposition"])

    # ---------- 动作类型 ----------
    action_exact = str(expected.get("action_type") or "").strip().lower()
    if action_exact:
        checked += 1
        if view["action_type"] != action_exact:
            _fail(failures, field="action_type", expected=action_exact, actual=view["action_type"])

    action_in = _as_str_list(expected.get("action_type_in"))
    if action_in:
        checked += 1
        allowed = {a.lower() for a in action_in}
        if view["action_type"] not in allowed:
            _fail(failures, field="action_type_in", expected=list(allowed), actual=view["action_type"])

    for blocked in _as_str_list(expected.get("action_type_not")):
        checked += 1
        if view["action_type"] == blocked.lower():
            _fail(failures, field="action_type_not", expected=f"非 {blocked}", actual=view["action_type"])

    # ---------- 策略阶段 ----------
    for blocked in _as_str_list(expected.get("strategy_stage_not")):
        checked += 1
        if view["strategy_stage"] == blocked.lower():
            _fail(failures, field="strategy_stage_not", expected=f"非 {blocked}", actual=view["strategy_stage"])

    # ---------- 规则 / 判例数量下限 ----------
    rules_min = _parse_non_negative_int(
        expected.get("matched_rules_min"),
        field="matched_rules_min",
        failures=failures,
    )
    if rules_min is not None:
        checked += 1
        if view["matched_rules_count"] < rules_min:
            _fail(
                failures,
                field="matched_rules_min",
                expected=f">={rules_min}",
                actual=view["matched_rules_count"],
            )

    cases_min = _parse_non_negative_int(
        expected.get("similar_cases_min"),
        field="similar_cases_min",
        failures=failures,
    )
    if cases_min is not None:
        checked += 1
        if view["similar_cases_count"] < cases_min:
            _fail(
                failures,
                field="similar_cases_min",
                expected=f">={cases_min}",
                actual=view["similar_cases_count"],
            )

    # ---------- 专责补证请求（子串匹配） ----------
    requests_max = _parse_non_negative_int(
        expected.get("actionable_evidence_requests_max"),
        field="actionable_evidence_requests_max",
        failures=failures,
    )
    if requests_max is not None:
        checked += 1
        actual_count = len(view["actionable_evidence_requests"])
        if actual_count > requests_max:
            _fail(
                failures,
                field="actionable_evidence_requests_max",
                expected=f"<={requests_max}",
                actual=view["actionable_evidence_requests"],
            )

    for fragment in _as_str_list(expected.get("actionable_evidence_requests_contains")):
        checked += 1
        joined = " ".join(view["actionable_evidence_requests"])
        if fragment not in joined and fragment not in view["reasoning_blob"]:
            _fail(
                failures,
                field="actionable_evidence_requests_contains",
                expected=fragment,
                actual=view["actionable_evidence_requests"],
            )

    # ---------- 推理文本引用（子串，辅助） ----------
    for fragment in _as_str_list(expected.get("reasoning_contains_any")):
        checked += 1
        if fragment not in view["reasoning_blob"]:
            _fail(
                failures,
                field="reasoning_contains_any",
                expected=fragment,
                actual=view["reasoning_blob"][:120],
            )

    return AssertResult.model_validate(
        {
            "case_id": case_id,
            "pass": not failures,
            "failures": failures,
            "checked_count": checked,
        }
    )


def assert_case(
    *,
    scenario_output: Path,
    report_json_path: Path,
    run_id: str = "",
) -> AssertRecord:
    """
    读取 spec 与 report，执行硬断言并返回记录。

    参数:
        scenario_output: 含 spec.json 的目录。
        report_json_path: 全链路 report.json 路径。
        run_id: 可选批次 id，写入 AssertRecord。

    返回:
        AssertRecord，含 result.pass 与 failures 明细。
    """
    spec_path = _resolve_spec_path(scenario_output)
    spec = _load_json(spec_path)
    try:
        report = _load_json(report_json_path)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(f"{ASSERT_LOG_PREFIX} 读取报告失败：{exc}") from exc
    meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
    case_id = str(meta.get("case_id") or report_json_path.stem).strip()
    expected = _extract_expected_report(spec)
    result = assert_report_fields(case_id=case_id, expected=expected, report=report)
    if not result.pass_:
        logger.warning(
            "%s 硬断言未通过 case_id=%s failures=%s",
            ASSERT_LOG_PREFIX,
            case_id,
            result.failures,
        )
    else:
        logger.info(
            "%s 硬断言通过 case_id=%s checked=%s",
            ASSERT_LOG_PREFIX,
            case_id,
            result.checked_count,
        )
    return AssertRecord(
        run_id=run_id,
        case_id=case_id,
        spec_path=str(spec_path),
        report_json_path=str(report_json_path),
        result=result,
    )


def write_assert_jsonl(record: AssertRecord, *, run_dir: Path) -> Path:
    """追加写入 assert_records.jsonl；写入失败时抛出中文 RuntimeError。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "assert_records.jsonl"
    line = json.dumps(record.to_json_dict(), ensure_ascii=False) + "\n"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:
        raise RuntimeError(f"{ASSERT_LOG_PREFIX} 写入 assert_records.jsonl 失败：{exc}") from exc
    return path


def _build_arg_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数。"""
    parser = argparse.ArgumentParser(description="报告结构化硬断言")
    parser.add_argument("--scenario-output", required=True, help="scenario_gen 输出目录（含 spec.json）")
    parser.add_argument("--report", required=True, help="全链路报告 JSON 路径")
    parser.add_argument("--run-id", default="", help="可选 eval_runs 子目录名")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        record = assert_case(
            scenario_output=Path(args.scenario_output),
            report_json_path=Path(args.report),
            run_id=args.run_id,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"硬断言失败：{exc}", file=sys.stderr)
        return 1

    if args.run_id:
        write_assert_jsonl(record, run_dir=EVAL_RUNS_DIR / args.run_id)

    status = "PASS" if record.result.pass_ else "FAIL"
    print(f"{status} {record.case_id}（检查项 {record.result.checked_count}）")
    for line in record.result.failures:
        print(f"  - {line}")
    return 0 if record.result.pass_ else 1


if __name__ == "__main__":
    raise SystemExit(main())
