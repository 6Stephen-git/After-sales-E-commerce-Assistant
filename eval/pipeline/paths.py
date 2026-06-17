"""
评测目录与产出路径常量。

eval/content/ 存放情景 Markdown 与 LLM 提示词；eval/output/ 为跑批与 Judge 产出（gitignore）。

产出按 phase1 / phase2 / phase3 分子目录，详见 eval/output/README.md 与 eval.pipeline.output_layout。
"""

from __future__ import annotations

from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = EVAL_DIR.parent
CONTENT_DIR = EVAL_DIR / "content"
SCENARIOS_DIR = CONTENT_DIR / "scenarios"
CASES_DIR = CONTENT_DIR / "cases"
PROMPTS_DIR = CONTENT_DIR / "prompts"
OUTPUT_DIR = EVAL_DIR / "output"
SCENARIO_OUTPUT_DIR = OUTPUT_DIR / "scenarios"
MANUAL_REPORTS_DIR = OUTPUT_DIR / "manual_reports"
EVAL_RUNS_DIR = OUTPUT_DIR / "eval_runs"
SCENARIO_TEMPLATE_PATH = SCENARIOS_DIR / "scenario_template.md"
EVAL_TREE_PATH = SCENARIOS_DIR / "eval_tree_ma.yaml"
BATCH_GEN_ERRORS_PATH = OUTPUT_DIR / "batch_gen_errors.jsonl"
