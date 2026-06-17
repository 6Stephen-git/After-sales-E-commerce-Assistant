"""
eval/output 目录布局：按评测阶段（phase1/2/3）归档 scenarios、manual_reports、eval_runs。
"""

from __future__ import annotations

import re
from pathlib import Path

from eval.pipeline.paths import EVAL_RUNS_DIR, MANUAL_REPORTS_DIR, SCENARIO_OUTPUT_DIR

_CASE_PREFIX_PHASE: dict[str, str] = {
    "MA": "phase1",
    "VAL": "phase1",
    "PREC": "phase1",
    "RULE": "phase1",
    "MIX": "phase2",
    "MS": "phase3",
}

_SLUG_HEAD_PHASE: dict[str, str] = {
    "ma": "phase1",
    "val": "phase1",
    "prec": "phase1",
    "rule": "phase1",
    "mix": "phase2",
    "ms": "phase3",
}

_CASE_TOKEN_RE = re.compile(r"^([A-Za-z]+)-\d+", re.ASCII)
_META_REPORT_RE = re.compile(
    r"^(PHASE|MIX-\d+_MIX-|.*_(RUN_REPORT|REVIEW)(\.|$))",
    re.IGNORECASE,
)


def _strip_step_suffix(stem: str) -> str:
    """多步报告名去掉 ``__stepNN_`` 后缀，得到案号 stem。"""
    if "__step" in stem:
        return stem.split("__step")[0]
    return stem


def case_id_phase(case_or_report_stem: str) -> str:
    """
    从案号或报告文件名推断阶段目录名。

    人工批次报告（PHASE*、*RUN_REPORT*、*REVIEW*）归入 ``_meta``。
    """
    stem = Path(case_or_report_stem).stem
    if _META_REPORT_RE.match(stem):
        return "_meta"
    token = _strip_step_suffix(stem)
    match = _CASE_TOKEN_RE.match(token)
    if not match:
        return "other"
    return _CASE_PREFIX_PHASE.get(match.group(1).upper(), "other")


def slug_phase(source_key: str) -> str:
    """从 scenario slug（小写 stem）推断阶段。"""
    head = source_key.split("_", 1)[0].split("-", 1)[0].lower()
    return _SLUG_HEAD_PHASE.get(head, "other")


def scenario_output_dir(source_key: str) -> Path:
    """spec/fixture 产出目录。"""
    return SCENARIO_OUTPUT_DIR / slug_phase(source_key) / source_key


def manual_reports_bucket(case_or_report_stem: str) -> Path:
    """单案或批次报告所在子目录。"""
    return MANUAL_REPORTS_DIR / case_id_phase(case_or_report_stem)


def manual_report_json_path(report_case_id: str) -> Path:
    """全链路报告 JSON 路径。"""
    stem = _strip_step_suffix(report_case_id)
    return manual_reports_bucket(stem) / f"{report_case_id}.json"


def manual_report_md_path(report_case_id: str) -> Path:
    """全链路报告 Markdown 路径。"""
    stem = _strip_step_suffix(report_case_id)
    return manual_reports_bucket(stem) / f"{report_case_id}.md"


def list_step_report_jsons(case_id: str) -> list[Path]:
    """多步报告 JSON，按 step 序号排序。"""
    pattern = f"{case_id}__step*.json"
    return sorted(manual_reports_bucket(case_id).glob(pattern))


def run_id_phase(run_id: str, *, tree_path: Path | None = None) -> str:
    """批次 run_id 对应阶段子目录。"""
    rid = run_id.strip().lower()
    if rid.startswith("phase1"):
        return "phase1"
    if rid.startswith("phase2"):
        return "phase2"
    if rid.startswith("phase3"):
        return "phase3"
    if tree_path is not None:
        name = tree_path.name.lower()
        if "multistep" in name:
            return "phase3"
        if "mixed" in name:
            return "phase2"
        if any(token in name for token in ("_ma", "_val", "_prec", "_rule", "rule_evidence")):
            return "phase1"
    return "other"


def eval_run_dir(run_id: str, *, tree_path: Path | None = None) -> Path:
    """批次汇总产出目录。"""
    phase = run_id_phase(run_id, tree_path=tree_path)
    return EVAL_RUNS_DIR / phase / run_id
