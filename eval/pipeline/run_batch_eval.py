"""
批量评测：评测树叶子 → scenario_gen + 全链路跑批 → 硬断言 → Judge → BATCH_SUMMARY。

用法:
  python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_ma.yaml -v
  python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_multistep.yaml --skip-gen -v
  python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_mixed.yaml --only mix-04 -v
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from eval.pipeline.assert_models import AssertRecord
from eval.pipeline.assert_report import assert_case, assert_multistep_case, write_assert_jsonl
from eval.pipeline.assert_report import _spec_has_steps
from eval.pipeline.eval_tree import (
    EVAL_TREE_PATH,
    EvalLeaf,
    filter_leaves,
    leaf_axis_groups,
    load_eval_tree,
    validate_eval_tree,
    write_jsonl_line,
)
from eval.pipeline.judge_cases import judge_case, judge_multistep_case, write_summary
from eval.pipeline.judge_models import JudgeRecord
from eval.pipeline.output_layout import (
    eval_run_dir,
    list_step_report_jsons,
    manual_report_json_path,
    manual_report_md_path,
    scenario_output_dir,
)
from eval.pipeline.paths import ROOT_DIR
from eval.pipeline.scenario_gen import (
    apply_source_identity,
    generate_spec_from_narrative,
    load_narrative,
    run_fixture,
    source_key_from_path,
    write_outputs,
)

if load_dotenv is not None:
    load_dotenv(ROOT_DIR / ".env")

BATCH_EVAL_LOG_PREFIX = "[BatchEval]"
logger = logging.getLogger(__name__)


def _now_run_id() -> str:
    """生成批次 run_id。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _scenario_output_dir(leaf: EvalLeaf) -> Path:
    """叶子对应的 scenario_gen 输出目录。"""
    return scenario_output_dir(leaf.source_key)


def _report_json_path(leaf: EvalLeaf) -> Path:
    """全链路报告 JSON 路径。"""
    return manual_report_json_path(leaf.report_case_id)


def _report_md_path(leaf: EvalLeaf) -> Path:
    """全链路报告 Markdown 路径。"""
    return manual_report_md_path(leaf.report_case_id)


def generate_and_run_leaf(leaf: EvalLeaf) -> tuple[Path, Path]:
    """
    单叶：Markdown → spec/fixture → 全链路跑批。

    返回 (scenario_output_dir, report_json_path)。
    """
    if not leaf.path.is_file():
        raise FileNotFoundError(f"情景 Markdown 不存在：{leaf.path}")

    narrative = load_narrative(text="", input_path=leaf.path)
    spec_dict = generate_spec_from_narrative(narrative, source_key=source_key_from_path(leaf.path))
    spec_dict = apply_source_identity(spec_dict, input_path=leaf.path)
    paths = write_outputs(spec_dict, source_key=source_key_from_path(leaf.path))

    written = run_fixture(paths["fixture"])
    if not written:
        raise RuntimeError(f"跑批未产出报告：{leaf.case_id}")

    report_md, report_json = written[-1]
    return paths["spec"].parent, report_json


def rerun_leaf(leaf: EvalLeaf) -> tuple[Path, Path]:
    """已有 spec/fixture 时仅重跑全链路。"""
    scenario_dir = _scenario_output_dir(leaf)
    fixture_path = scenario_dir / "fixture.json"
    if not fixture_path.is_file():
        raise FileNotFoundError(f"缺少 fixture.json：{fixture_path}")

    written = run_fixture(fixture_path)
    if not written:
        raise RuntimeError(f"跑批未产出报告：{leaf.case_id}")
    _, report_json = written[-1]
    return scenario_dir, report_json


def assert_leaf_records(leaf: EvalLeaf, *, run_id: str) -> list[AssertRecord]:
    """对单叶（或逐步）报告执行结构化硬断言。"""
    scenario_dir = _scenario_output_dir(leaf)
    spec_path = scenario_dir / "spec.json"
    if not spec_path.is_file():
        raise FileNotFoundError(f"缺少 spec.json：{spec_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    if _spec_has_steps(spec):
        report_paths = list_step_report_jsons(leaf.report_case_id)
        if not report_paths:
            raise FileNotFoundError(f"缺少多步报告 JSON：{leaf.report_case_id}__step*.json")
        return assert_multistep_case(
            scenario_output=scenario_dir,
            report_json_paths=report_paths,
            run_id=run_id,
        )

    report_json = _report_json_path(leaf)
    if not report_json.is_file():
        raise FileNotFoundError(f"缺少报告 JSON：{report_json}")
    return [
        assert_case(
            scenario_output=scenario_dir,
            report_json_path=report_json,
            run_id=run_id,
        )
    ]


def judge_leaf_records(leaf: EvalLeaf, *, run_id: str, eval_root: Path) -> list[JudgeRecord]:
    """对单叶（或多步每步）报告执行 Judge。"""
    scenario_dir = _scenario_output_dir(leaf)
    spec_path = scenario_dir / "spec.json"
    if not spec_path.is_file():
        raise FileNotFoundError(f"缺少 spec.json：{spec_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    if _spec_has_steps(spec):
        report_paths = list_step_report_jsons(leaf.report_case_id)
        if not report_paths:
            raise FileNotFoundError(f"缺少多步报告 JSON：{leaf.report_case_id}__step*.json")
        return judge_multistep_case(
            scenario_output=scenario_dir,
            report_json_paths=report_paths,
            eval_root=eval_root,
            run_id=run_id,
        )

    report_json = _report_json_path(leaf)
    if not report_json.is_file():
        raise FileNotFoundError(f"缺少报告 JSON：{report_json}")
    return [
        judge_case(
            scenario_output=scenario_dir,
            report_json_path=report_json,
            report_md_path=_report_md_path(leaf),
            eval_root=eval_root,
            run_id=run_id,
        )
    ]


def _axis_label(axis_id: str) -> str:
    """轴 id → 展示名。"""
    mapping = {
        "negotiation": "NG 妥善协商",
        "merchant_fault": "MF 商责善后",
        "malicious": "MA 恶意抗辩",
        "value": "VAL 客户价值",
        "precedent": "PREC 判例",
        "rule_evidence": "RULE 专责补证",
        "mixed": "MIX 混合单步",
        "multistep": "MS 多步快照",
    }
    return mapping.get(axis_id, axis_id)


def _records_for_axis(records: list[JudgeRecord], axis_id: str, leaves: list[EvalLeaf]) -> list[JudgeRecord]:
    """按轴筛选 Judge 记录。"""
    case_ids = {leaf.report_case_id for leaf in leaves if leaf.axis_id == axis_id}
    return [record for record in records if record.case_id in case_ids]


def write_batch_summary(
    *,
    run_dir: Path,
    records: list[JudgeRecord],
    assert_records: list[AssertRecord],
    leaves: list[EvalLeaf],
    errors: list[dict[str, Any]],
) -> Path:
    """写入批次汇总 BATCH_SUMMARY.md。"""
    total = len(records)
    judge_passed = sum(1 for record in records if record.result.pass_)
    judge_failed = total - judge_passed
    assert_total = len(assert_records)
    assert_passed = sum(1 for record in assert_records if record.result.pass_)
    assert_failed = assert_total - assert_passed
    average = (
        sum(record.result.overall_score for record in records) / total
        if total
        else 0.0
    )
    judge_pass_rate = (judge_passed / total * 100) if total else 0.0
    assert_pass_rate = (assert_passed / assert_total * 100) if assert_total else 0.0

    lines = [
        f"# Batch Eval Summary：{run_dir.name}",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 评测树叶子数：{len(leaves)}",
        f"- 完成硬断言数：{assert_total}",
        f"- 硬断言通过：{assert_passed}（{assert_pass_rate:.1f}%）",
        f"- 硬断言失败：{assert_failed}",
        f"- 完成 Judge 数：{total}",
        f"- Judge 通过数：{judge_passed}（{judge_pass_rate:.1f}%）",
        f"- Judge 失败数：{judge_failed}",
        f"- Judge 平均分：{average:.1f}" if total else "- Judge：**已跳过**",
        "",
        "> 功能验证以**硬断言**为准；Judge 不挡门，话术问题见 Judge 记录与 warnings。",
        "",
        "## 分轴统计（Judge）",
        "",
    ]

    groups = leaf_axis_groups(leaves)
    for axis_id, axis_leaves in groups.items():
        axis_records = _records_for_axis(records, axis_id, leaves)
        axis_total = len(axis_records)
        axis_passed = sum(1 for record in axis_records if record.result.pass_)
        axis_rate = (axis_passed / axis_total * 100) if axis_total else 0.0
        axis_avg = (
            sum(record.result.overall_score for record in axis_records) / axis_total
            if axis_total
            else 0.0
        )
        lines.append(
            f"- {_axis_label(axis_id)}：{axis_passed}/{axis_total} 通过（{axis_rate:.1f}%），均分 {axis_avg:.1f}"
        )

    hard_fail_records = [record for record in records if record.result.hard_failures]
    assert_fail_records = [record for record in assert_records if not record.result.pass_]

    lines.extend(["", "## 硬断言失败清单", ""])
    if assert_fail_records:
        for record in assert_fail_records:
            reasons = "; ".join(record.result.failures) or "未知"
            lines.append(f"- `{record.case_id}`：{reasons}")
    else:
        lines.append("暂无硬断言失败")

    lines.extend(["", "## Judge 硬失败清单", ""])
    if hard_fail_records:
        for record in hard_fail_records:
            reasons = "; ".join(record.result.hard_failures)
            lines.append(f"- `{record.case_id}`：{reasons}")
    else:
        lines.append("暂无 Judge 硬失败")

    lines.extend(["", "## 跑批错误", ""])
    if errors:
        for item in errors:
            lines.append(f"- `{item.get('case_id', '?')}`：{item.get('error', '未知错误')}")
    else:
        lines.append("无")

    lines.extend(
        [
            "",
            "## 单案详情",
            "",
            "完整分项见本目录 `SUMMARY.md`、assert_records.jsonl 与各案 Judge 记录。",
            "",
        ]
    )

    summary_path = run_dir / "BATCH_SUMMARY.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def run_batch_eval(
    *,
    tree_path: Path,
    only: str = "",
    skip_gen: bool = False,
    skip_judge: bool = False,
    skip_assert: bool = False,
    run_id: str = "",
    error_log: Path | None = None,
) -> Path:
    """
    执行批量评测，返回 eval_runs 目录路径。

    单案失败写入 error_log 并继续其余案。
    """
    leaves = load_eval_tree(tree_path)
    validate_eval_tree(leaves, tree_path=tree_path)
    targets = filter_leaves(leaves, only=only, require_file=True)
    if not targets:
        raise ValueError("无可用叶子（Markdown 文件不存在或 --only 无匹配）")

    resolved_run_id = run_id.strip() or _now_run_id()
    run_dir = eval_run_dir(resolved_run_id, tree_path=tree_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    eval_root = run_dir.parent

    errors_path = error_log or (run_dir / "batch_errors.jsonl")
    if errors_path.is_file():
        errors_path.unlink()

    records: list[JudgeRecord] = []
    assert_records: list[AssertRecord] = []
    assert_jsonl = run_dir / "assert_records.jsonl"
    if assert_jsonl.is_file():
        assert_jsonl.unlink()

    for leaf in targets:
        logger.info("%s 开始 %s", BATCH_EVAL_LOG_PREFIX, leaf.case_id)
        try:
            if skip_gen:
                rerun_leaf(leaf)
            else:
                generate_and_run_leaf(leaf)

            step_assert_records = assert_leaf_records(leaf, run_id=resolved_run_id)
            if not skip_assert:
                for assert_record in step_assert_records:
                    assert_record.timestamp = datetime.now(timezone.utc)
                    write_assert_jsonl(assert_record, run_dir=run_dir)
                    assert_records.append(assert_record)
                    assert_status = "PASS" if assert_record.result.pass_ else "FAIL"
                    if not assert_record.result.pass_:
                        logger.warning(
                            "%s 硬断言 %s：%s；%s",
                            BATCH_EVAL_LOG_PREFIX,
                            assert_record.case_id,
                            assert_status,
                            "; ".join(assert_record.result.failures),
                        )
                    else:
                        logger.info(
                            "%s 硬断言 %s：%s（%d 项）",
                            BATCH_EVAL_LOG_PREFIX,
                            assert_record.case_id,
                            assert_status,
                            assert_record.result.checked_count,
                        )
            else:
                logger.info("%s 跳过硬断言 %s", BATCH_EVAL_LOG_PREFIX, leaf.case_id)

            if not skip_judge:
                judge_records = judge_leaf_records(leaf, run_id=resolved_run_id, eval_root=eval_root)
                for record in judge_records:
                    records.append(record)
                    judge_status = "PASS" if record.result.pass_ else "FAIL"
                    logger.info(
                        "%s Judge %s：%s %d 分",
                        BATCH_EVAL_LOG_PREFIX,
                        record.case_id,
                        judge_status,
                        record.result.overall_score,
                    )
            else:
                logger.info("%s 跳过 Judge %s", BATCH_EVAL_LOG_PREFIX, leaf.case_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "%s 失败 %s：%s",
                BATCH_EVAL_LOG_PREFIX,
                leaf.case_id,
                exc,
            )
            write_jsonl_line(
                errors_path,
                {"case_id": leaf.case_id, "error": str(exc)},
            )

    write_summary(run_dir)
    error_items: list[dict[str, Any]] = []
    if errors_path.is_file():
        for line in errors_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                error_items.append(json.loads(line))

    write_batch_summary(
        run_dir=run_dir,
        records=records,
        assert_records=assert_records,
        leaves=targets,
        errors=error_items,
    )
    return run_dir


def _build_arg_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数。"""
    parser = argparse.ArgumentParser(description="评测树批量跑批 + Judge")
    parser.add_argument("--tree", default=str(EVAL_TREE_PATH), help="评测树 YAML")
    parser.add_argument("--only", default="", help="仅跑匹配 case_id/source_key 的叶子")
    parser.add_argument(
        "--skip-gen",
        action="store_true",
        help="跳过 scenario_gen，仅重跑 fixture 链路与 Judge",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="跑完整链路，跳过 Judge LLM",
    )
    parser.add_argument(
        "--skip-assert",
        action="store_true",
        help="跳过结构化硬断言，仅跑链路与 Judge",
    )
    parser.add_argument("--run-id", default="", help="指定 eval_runs 子目录名")
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
        run_dir = run_batch_eval(
            tree_path=Path(args.tree),
            only=args.only,
            skip_gen=args.skip_gen,
            skip_judge=args.skip_judge,
            skip_assert=args.skip_assert,
            run_id=args.run_id,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"批量评测失败：{exc}", file=sys.stderr)
        return 1

    print(f"批次完成：{run_dir}")
    print(f"汇总：{run_dir / 'BATCH_SUMMARY.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
