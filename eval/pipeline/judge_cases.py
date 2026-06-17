"""
LLM-as-Judge 跑批评测：读取情景 spec 与售后报告，输出 JSONL 记录和 Markdown 汇总。

用法:
  python -m eval.pipeline.judge_cases \\
    --scenario-output eval/output/scenarios/phase1/ma-01_review_blackmail \\
    --report eval/output/manual_reports/phase1/MA-01_REVIEW_BLACKMAIL.json -v
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from eval.pipeline.judge_models import JudgeRecord, JudgeResult
from eval.pipeline.paths import EVAL_RUNS_DIR, ROOT_DIR
from eval.pipeline.scenario_llm_utils import call_llm_json, load_json_prompt, load_prompt

if load_dotenv is not None:
    load_dotenv(ROOT_DIR / ".env")

JUDGE_LOG_PREFIX = "[JudgeCases]"
JUDGE_MODEL_ENV_KEY = "JUDGE_LLM_MODEL"
JUDGE_FALLBACK_MODEL_ENV_KEY = "AGENT2_LLM_MODEL"
JUDGE_TEMPERATURE = 0.15
SUMMARY_SECTION_HEADERS = ("## 核心结论区", "## 关键依据区", "## 话术区")
OUTPUT_DIR = EVAL_RUNS_DIR


def _now_run_id() -> str:
    """生成本地可读 run_id。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _load_json_file(path: Path) -> dict[str, Any]:
    """读取 JSON 对象文件。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"{JUDGE_LOG_PREFIX} 无法读取 JSON 文件：{path}，原因：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{JUDGE_LOG_PREFIX} JSON 文件非法：{path}，原因：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{JUDGE_LOG_PREFIX} JSON 根节点必须是对象：{path}")
    return payload


def _resolve_spec_path(scenario_output: Path) -> Path:
    """从 scenario 输出目录解析 spec.json。"""
    spec_path = scenario_output / "spec.json"
    if not spec_path.is_file():
        raise FileNotFoundError(f"{JUDGE_LOG_PREFIX} 缺少 spec.json：{spec_path}")
    return spec_path


def _resolve_report_md_path(report_json_path: Path, explicit: Path | None = None) -> Path | None:
    """解析报告 Markdown 路径。"""
    if explicit is not None:
        return explicit if explicit.is_file() else None
    candidate = report_json_path.with_suffix(".md")
    return candidate if candidate.is_file() else None


def build_report_markdown_summary(markdown: str, *, max_chars: int = 6000) -> str:
    """仅保留报告中最适合 Judge 的核心、依据和话术区。"""
    lines = markdown.splitlines()
    selected: list[str] = []
    collecting = False
    for line in lines:
        is_header = line.startswith("## ")
        if line in SUMMARY_SECTION_HEADERS:
            collecting = True
            selected.append(line)
            continue
        if is_header and collecting:
            collecting = False
        if collecting:
            selected.append(line)
    summary = "\n".join(selected).strip()
    if not summary:
        summary = markdown.strip()
    return summary[:max_chars]


def _build_user_prompt(
    *,
    spec: dict[str, Any],
    report_json: dict[str, Any],
    report_markdown_summary: str,
    schema: dict[str, Any],
) -> str:
    """构造 Judge 用户 prompt。"""
    return (
        "请评审以下售后 agent 测试报告，并只输出符合 JSON Schema 的 JSON 对象。\n\n"
        f"## JSON Schema\n```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Scenario Spec\n```json\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## AnalysisReport JSON\n```json\n{json.dumps(report_json, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Report Markdown Summary\n{report_markdown_summary}\n"
    )


def _judge_result_from_llm(
    *,
    spec: dict[str, Any],
    report_json: dict[str, Any],
    report_markdown_summary: str,
) -> JudgeResult:
    """调用 LLM Judge 并校验输出。"""
    system_prompt = load_prompt("judge_system.md")
    schema = load_json_prompt("judge_result.schema.json")
    payload = call_llm_json(
        system_prompt=system_prompt,
        user_prompt=_build_user_prompt(
            spec=spec,
            report_json=report_json,
            report_markdown_summary=report_markdown_summary,
            schema=schema,
        ),
        model_env_key=JUDGE_MODEL_ENV_KEY,
        fallback_model_env_key=JUDGE_FALLBACK_MODEL_ENV_KEY,
        temperature=JUDGE_TEMPERATURE,
    )
    return JudgeResult.model_validate(payload)


def _append_record(record: JudgeRecord, record_path: Path) -> None:
    """追加写入 JSONL 记录。"""
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_json_dict(), ensure_ascii=False) + "\n")


def _load_records(record_path: Path) -> list[JudgeRecord]:
    """读取当前 run 的 JSONL 记录。"""
    if not record_path.is_file():
        return []
    records: list[JudgeRecord] = []
    for line in record_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(JudgeRecord.model_validate(json.loads(line)))
    return records


def _low_score_dimensions(records: list[JudgeRecord]) -> list[tuple[str, int]]:
    """统计低分维度出现次数。"""
    counts: dict[str, int] = {}
    for record in records:
        scores = record.result.scores.model_dump()
        for name, score in scores.items():
            if int(score) <= 3:
                counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _read_report_context(record: JudgeRecord) -> dict[str, str]:
    """读取记录对应报告中的策略选择和推荐话术，供 SUMMARY.md 展示。"""
    path = Path(record.report_json_path)
    if not path.is_file():
        return {"disposition": "—", "action_type": "—", "script": "—"}
    try:
        report = _load_json_file(path)
    except (RuntimeError, ValueError):
        return {"disposition": "—", "action_type": "—", "script": "—"}

    strategy = report.get("strategy") if isinstance(report.get("strategy"), dict) else {}
    scripts = report.get("scripts") if isinstance(report.get("scripts"), dict) else {}
    return {
        "disposition": str(strategy.get("disposition") or "—"),
        "action_type": str(strategy.get("action_type") or "—"),
        "script": str(scripts.get("script") or "—").strip() or "—",
    }


def _format_score_lines(record: JudgeRecord) -> list[str]:
    """格式化 Judge 各维度评分。"""
    scores = record.result.scores.model_dump()
    return [f"  - `{name}`：{score}/5" for name, score in scores.items()]


def write_summary(run_dir: Path) -> Path:
    """根据 records.jsonl 生成 SUMMARY.md。"""
    record_path = run_dir / "records.jsonl"
    records = _load_records(record_path)
    total = len(records)
    passed = sum(1 for record in records if record.result.pass_)
    failed = total - passed
    average = (
        sum(record.result.overall_score for record in records) / total
        if total
        else 0.0
    )
    pass_rate = (passed / total * 100) if total else 0.0

    lines = [
        f"# Judge Eval Summary：{run_dir.name}",
        "",
        f"- 总用例数：{total}",
        f"- 通过数：{passed}",
        f"- 失败数：{failed}",
        f"- 通过率：{pass_rate:.1f}%",
        f"- 平均分：{average:.1f}",
        "",
        "## 失败用例",
        "",
    ]
    failed_records = [record for record in records if not record.result.pass_]
    if failed_records:
        for record in failed_records:
            reasons = record.result.hard_failures or record.result.fail_reasons or ["未提供失败原因"]
            lines.append(f"- `{record.case_id}`：{'; '.join(reasons)}")
    else:
        lines.append("暂无失败用例")

    lines.extend(["", "## 低分维度", ""])
    low_dims = _low_score_dimensions(records)
    if low_dims:
        for name, count in low_dims:
            lines.append(f"- `{name}`：{count} 次")
    else:
        lines.append("暂无低分维度")

    lines.extend(["", "## 用例摘要", ""])
    for record in records:
        status = "PASS" if record.result.pass_ else "FAIL"
        report_context = _read_report_context(record)
        lines.append(f"### `{record.case_id}` [{status}] {record.result.overall_score} 分")
        lines.append("")
        lines.append(f"- 策略选择：`{report_context['disposition']}` / `{report_context['action_type']}`")
        lines.append(f"- 推荐话术：{report_context['script']}")
        lines.append(f"- Judge 摘要：{record.result.judge_summary}")
        if record.result.fail_reasons:
            lines.append(f"- 失败原因：{'; '.join(record.result.fail_reasons)}")
        if record.result.warnings:
            lines.append(f"- 评测警告：{'; '.join(record.result.warnings)}")
        lines.append("- 分项评分：")
        lines.extend(_format_score_lines(record))
        lines.append("")
    if not records:
        lines.append("暂无记录")

    summary_path = run_dir / "SUMMARY.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def build_step_spec_for_judge(spec: dict[str, Any], step_index: int) -> dict[str, Any]:
    """
    为多步快照 Judge 构造单步 spec 视图：注入该步期望/禁忌，避免 Judge 误用终局标准评过程步。
    """
    import copy

    view = copy.deepcopy(spec)
    steps = view.get("steps")
    if not isinstance(steps, list) or step_index < 0 or step_index >= len(steps):
        return view

    step = steps[step_index]
    if not isinstance(step, dict):
        return view

    label = str(step.get("label") or f"步骤{step_index + 1}")
    meta = dict(view.get("meta") or {})
    base_title = str(meta.get("title") or meta.get("case_id") or "").strip()
    meta["title"] = f"{base_title} / {label}" if base_title else label
    view["meta"] = meta

    step_exp = step.get("expectation") if isinstance(step.get("expectation"), dict) else {}
    top_exp = dict(view.get("expectation") or {})
    if step_exp.get("intent_summary"):
        top_exp["intent_summary"] = step_exp["intent_summary"]
    if isinstance(step_exp.get("forbidden_outputs"), list) and step_exp["forbidden_outputs"]:
        top_exp["forbidden_outputs"] = list(step_exp["forbidden_outputs"])
    view["expectation"] = top_exp
    view["steps"] = []
    return view


def judge_case(
    *,
    scenario_output: Path,
    report_json_path: Path,
    eval_root: Path = OUTPUT_DIR,
    run_id: str = "",
    report_md_path: Path | None = None,
    spec_override: dict[str, Any] | None = None,
    case_id_override: str = "",
) -> JudgeRecord:
    """评审单个场景报告并写入当前 run。"""
    resolved_run_id = run_id.strip() or _now_run_id()
    run_dir = eval_root / resolved_run_id
    spec_path = _resolve_spec_path(scenario_output)
    resolved_report_md = _resolve_report_md_path(report_json_path, report_md_path)

    spec = spec_override if spec_override is not None else _load_json_file(spec_path)
    report_json = _load_json_file(report_json_path)
    report_markdown_summary = ""
    if resolved_report_md is not None:
        report_markdown_summary = build_report_markdown_summary(
            resolved_report_md.read_text(encoding="utf-8")
        )

    result = _judge_result_from_llm(
        spec=spec,
        report_json=report_json,
        report_markdown_summary=report_markdown_summary,
    )
    case_id = case_id_override.strip() or str((spec.get("meta") or {}).get("case_id") or result.case_id)
    record = JudgeRecord(
        run_id=resolved_run_id,
        case_id=case_id,
        timestamp=datetime.now(timezone.utc),
        spec_path=str(spec_path),
        report_json_path=str(report_json_path),
        report_md_path=str(resolved_report_md or ""),
        judge_model_env_key=JUDGE_MODEL_ENV_KEY
        if os.getenv(JUDGE_MODEL_ENV_KEY, "").strip()
        else JUDGE_FALLBACK_MODEL_ENV_KEY,
        result=result,
    )

    _append_record(record, run_dir / "records.jsonl")
    write_summary(run_dir)
    return record


def judge_multistep_case(
    *,
    scenario_output: Path,
    report_json_paths: list[Path],
    eval_root: Path = OUTPUT_DIR,
    run_id: str = "",
) -> list[JudgeRecord]:
    """多步情景：对每一步报告分别 Judge。"""
    spec_path = _resolve_spec_path(scenario_output)
    spec = _load_json_file(spec_path)
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"{JUDGE_LOG_PREFIX} spec 不含 steps：{spec_path}")

    meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
    base_case_id = str(meta.get("case_id") or scenario_output.name).strip()
    if len(report_json_paths) != len(steps):
        raise RuntimeError(
            f"{JUDGE_LOG_PREFIX} 报告步数与 spec 不一致："
            f"reports={len(report_json_paths)} steps={len(steps)} case_id={base_case_id}"
        )

    records: list[JudgeRecord] = []
    for index, report_path in enumerate(report_json_paths):
        if not report_path.is_file():
            raise FileNotFoundError(f"{JUDGE_LOG_PREFIX} 缺少步骤报告：{report_path}")
        step_spec = build_step_spec_for_judge(spec, index)
        case_id = f"{base_case_id}__step{index + 1:02d}"
        records.append(
            judge_case(
                scenario_output=scenario_output,
                report_json_path=report_path,
                report_md_path=report_path.with_suffix(".md"),
                eval_root=eval_root,
                run_id=run_id,
                spec_override=step_spec,
                case_id_override=case_id,
            )
        )
    return records


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM-as-Judge 售后测试报告评测")
    parser.add_argument("--scenario-output", required=True, help="scenario_gen 输出目录，需包含 spec.json")
    parser.add_argument("--report", required=True, help="run_manual_cases 输出的 AnalysisReport JSON")
    parser.add_argument("--report-md", default="", help="报告 Markdown 路径；默认取 --report 同名 .md")
    parser.add_argument("--eval-root", default=str(OUTPUT_DIR), help="评测输出根目录")
    parser.add_argument("--run-id", default="", help="指定 run_id；默认按时间生成")
    parser.add_argument("--fail-on-judge-fail", action="store_true", help="Judge fail 时返回非 0")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印评审结果摘要")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        record = judge_case(
            scenario_output=Path(args.scenario_output),
            report_json_path=Path(args.report),
            report_md_path=Path(args.report_md) if args.report_md.strip() else None,
            eval_root=Path(args.eval_root),
            run_id=args.run_id,
        )
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 2

    if args.verbose:
        status = "PASS" if record.result.pass_ else "FAIL"
        print(f"{record.case_id}: {status} score={record.result.overall_score}")
        print(record.result.judge_summary)

    if args.fail_on_judge_fail and not record.result.pass_:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
