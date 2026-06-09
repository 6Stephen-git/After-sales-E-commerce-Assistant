"""
半结构化情景 Markdown → 审阅说明 + spec + fixture →（可选）跑全链路。

情景须含固定 6 段（背景/买家/争议/证据/参考/期望与禁忌），可选「其他说明」。
模板见 eval/content/scenarios/scenario_template.md。

用法:
  python -m eval.pipeline.scenario_gen --input eval/content/scenarios/pilot/negotiation/NG-02_evidence_compensation.md -v
  python -m eval.pipeline.scenario_gen --input eval/content/scenarios/pilot/negotiation/NG-02_evidence_compensation.md --run -v
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from eval.pipeline.paths import ROOT_DIR, SCENARIO_OUTPUT_DIR, SCENARIO_TEMPLATE_PATH
from eval.pipeline.scenario_llm_utils import (
    call_llm_json,
    load_json_prompt,
    load_prompt,
    scenario_gen_model_env,
    scenario_gen_temperature,
)
from eval.pipeline.scenario_spec import ScenarioSpec, validate_spec_dict
from eval.pipeline.spec_to_fixture import fixture_from_spec_dict, write_fixture

if load_dotenv is not None:
    load_dotenv(ROOT_DIR / ".env")

GEN_LOG_PREFIX = "[ScenarioGen]"
logger = logging.getLogger(__name__)
OUTPUT_DIR = SCENARIO_OUTPUT_DIR

# ---------- 半结构化情景：固定段落标题 ----------
SCENARIO_SECTION_HEADERS = (
    "## 背景",
    "## 买家",
    "## 对话记录",
    "## 事实证据",
    "## 参考",
    "## 期望与禁忌",
)
SCENARIO_EVIDENCE_SECTION_ALIASES = ("## 事实证据", "## 证据")
OPTIONAL_SECTION_HEADER = "## 其他说明"


def _missing_section_headers(narrative: str) -> list[str]:
    """检查情景是否包含必需的半结构化段落标题。"""
    missing: list[str] = []
    for header in SCENARIO_SECTION_HEADERS:
        if header == "## 事实证据":
            if not any(alias in narrative for alias in SCENARIO_EVIDENCE_SECTION_ALIASES):
                missing.append(header)
            continue
        if header not in narrative:
            missing.append(header)
    return missing


def _slug_from_case_id(case_id: str) -> str:
    """case_id → 文件名安全片段。"""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", case_id.strip()).strip("_").lower()
    return slug or "scenario"


def source_key_from_path(path: Path) -> str:
    """情景 Markdown 路径 stem → 输出目录键（小写，如 ng-02_evidence_compensation）。"""
    return _slug_from_case_id(path.stem)


def case_id_from_source_key(source_key: str) -> str:
    """源文件 stem → 稳定 case_id（大写，与 pilot 文件名一致，如 NG-02_EVIDENCE_COMPENSATION）。"""
    normalized = _slug_from_case_id(source_key)
    return normalized.upper() or "SCENARIO-UNKNOWN"


def apply_source_identity(
    spec_dict: dict[str, Any],
    *,
    input_path: Path | None = None,
    source_key: str | None = None,
) -> dict[str, Any]:
    """
    用源文件名覆盖 LLM 自拟的 case_id，避免多情景串案与报告覆盖。

    --text 无文件时需显式传入 source_key。
    """
    resolved_key = (source_key or "").strip()
    if not resolved_key and input_path is not None:
        resolved_key = source_key_from_path(input_path)
    if not resolved_key:
        return spec_dict

    meta = dict(spec_dict.get("meta") or {})
    meta["source_key"] = resolved_key
    meta["case_id"] = case_id_from_source_key(resolved_key)
    spec_dict["meta"] = meta
    return spec_dict


def load_narrative(*, text: str, input_path: Path | None) -> str:
    """读取半结构化情景 Markdown（--text 与 --input 二选一）。"""
    if text.strip():
        return text.strip()
    if input_path is None:
        raise ValueError(f"{GEN_LOG_PREFIX} 请提供 --text 或 --input")
    if not input_path.is_file():
        raise FileNotFoundError(f"{GEN_LOG_PREFIX} 情景文件不存在：{input_path}")
    narrative = input_path.read_text(encoding="utf-8").strip()
    if not narrative:
        raise ValueError(f"{GEN_LOG_PREFIX} 情景内容为空：{input_path}")
    return narrative


def _has_dialogue_section(narrative: str) -> bool:
    """判断情景是否显式包含对话记录段。"""
    return "## 对话记录" in narrative


def _chat_history_is_empty(spec_dict: dict[str, Any]) -> bool:
    """检查 LLM 是否成功把对话记录抽入 materials.chat_history。"""
    materials = spec_dict.get("materials") if isinstance(spec_dict.get("materials"), dict) else {}
    chat_history = materials.get("chat_history") if isinstance(materials, dict) else None
    return not isinstance(chat_history, list) or not chat_history


def generate_spec_from_narrative(narrative: str) -> dict[str, Any]:
    """调用编写 LLM：半结构化 Markdown → scenario_spec。"""
    missing = _missing_section_headers(narrative)
    if missing:
        logger.warning(
            "%s 情景缺少固定段落：%s（请复制 %s 填写）",
            GEN_LOG_PREFIX,
            "、".join(missing),
            SCENARIO_TEMPLATE_PATH,
        )

    system_prompt = load_prompt("scenario_gen_system.md")
    schema = load_json_prompt("scenario_spec.schema.json")
    user_prompt = (
        "商家提供半结构化情景（背景/买家/对话记录/事实证据/参考/期望与禁忌 + 可选「其他说明」）。请按段提取并生成 scenario_spec JSON。\n\n"
        f"## JSON Schema\n```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 商家情景（原文）\n{narrative}\n"
    )

    payload = call_llm_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_env_key=scenario_gen_model_env(),
        fallback_model_env_key="AGENT2_LLM_MODEL",
        temperature=scenario_gen_temperature(),
    )
    spec = validate_spec_dict(payload)
    if not (spec.human_review.scenario_restated or "").strip():
        raise ValueError(f"{GEN_LOG_PREFIX} 生成结果缺少 human_review.scenario_restated，请重试")
    spec_dict = spec.model_dump(mode="json")
    if _has_dialogue_section(narrative) and _chat_history_is_empty(spec_dict):
        raise ValueError(f"{GEN_LOG_PREFIX} 生成结果未抽取 materials.chat_history，请检查对话记录格式或提示词")
    logger.info("%s 已生成 spec case_id=%s", GEN_LOG_PREFIX, spec.meta.case_id)
    return spec_dict


def render_review_markdown(spec: ScenarioSpec) -> str:
    """把 spec 中的审阅块与期望整理为可读 Markdown。"""
    hr = spec.human_review
    exp = spec.expectation
    lines = [
        f"# 用例审阅：{spec.meta.case_id}",
        "",
        f"**标题**：{spec.meta.title}",
        "",
        "## 我们是否理解您的情景",
        "",
        hr.scenario_restated.strip(),
        "",
        "## 本用例要验证什么",
        "",
        hr.fixture_focus.strip(),
        "",
        "## 跑批前请确认",
        "",
    ]
    for item in hr.checks_before_run:
        lines.append(f"- {item}")
    lines.extend(["", "## 期望策略倾向", "", exp.intent_summary.strip() or "（未提取）", ""])
    if exp.forbidden_outputs:
        lines.extend(["## 禁忌（不应出现的处理）", ""])
        for item in exp.forbidden_outputs:
            lines.append(f"- {item}")
    if exp.acceptable_dispositions:
        lines.extend(["", "## 可接受 disposition", "", ", ".join(exp.acceptable_dispositions)])
    lines.extend(
        [
            "",
            "## 技术产物",
            "",
            "- `spec.json`：完整中间态（含原文 `scenario_narrative`）",
            "- `fixture.json`：可跑 `run_manual_cases` 的用例",
            "",
            "确认无误后再执行：`python -m eval.pipeline.scenario_gen --spec <spec路径> --run`",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(
    spec_dict: dict[str, Any],
    *,
    out_dir: Path | None = None,
    source_key: str | None = None,
) -> dict[str, Path]:
    """落盘 spec、fixture、审阅 Markdown。"""
    spec = validate_spec_dict(spec_dict)
    slug = (source_key or spec.meta.source_key or _slug_from_case_id(spec.meta.case_id)).strip()
    if not slug:
        slug = _slug_from_case_id(spec.meta.case_id)
    base = out_dir or (OUTPUT_DIR / slug)
    base.mkdir(parents=True, exist_ok=True)

    spec_path = base / "spec.json"
    fixture_path = base / "fixture.json"
    review_path = base / "REVIEW.md"

    spec_path.write_text(json.dumps(spec_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    write_fixture(fixture_from_spec_dict(spec_dict), fixture_path)
    review_path.write_text(render_review_markdown(spec), encoding="utf-8")

    return {"spec": spec_path, "fixture": fixture_path, "review": review_path}


def run_fixture(fixture_path: Path) -> list[tuple[Path, Path]]:
    """跑全链路（含真实规则匹配）并返回报告路径。"""
    from eval.pipeline.run_manual_cases import run_cases_file

    return run_cases_file(fixture_path)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="半结构化情景 Markdown → 审阅说明 + 测试用例（你审后再 --run）",
    )
    parser.add_argument("--text", type=str, default="", help="直接粘贴半结构化情景（含 ## 背景 等标题）")
    parser.add_argument(
        "--input",
        type=str,
        default="",
        help="情景 .md 路径（推荐；可复制 scenario_template.md）",
    )
    parser.add_argument("--spec", type=str, default="", help="已审过的 spec.json，与 --run 联用")
    parser.add_argument("--run", action="store_true", help="跑全链路（须已生成或指定 --spec 同目录 fixture）")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    # ---------- 仅跑批：--spec + --run ----------
    if args.run and args.spec.strip() and not args.text.strip() and not args.input.strip():
        spec_path = Path(args.spec)
        fixture_path = spec_path.parent / "fixture.json"
        if not fixture_path.is_file():
            print(f"同目录缺少 fixture.json：{fixture_path}", file=sys.stderr)
            return 1
        try:
            written = run_fixture(fixture_path)
        except Exception as exc:  # noqa: BLE001
            print(f"跑批失败：{exc}", file=sys.stderr)
            return 1
        for md_path, json_path in written:
            print(f"报告：{md_path}\n      {json_path}")
        return 0

    # ---------- 生成：--text 或 --input ----------
    input_path = Path(args.input) if args.input.strip() else None
    try:
        narrative = load_narrative(
            text=args.text,
            input_path=input_path,
        )
        spec_dict = generate_spec_from_narrative(narrative)
        spec_dict = apply_source_identity(spec_dict, input_path=input_path)
        resolved_source_key = str((spec_dict.get("meta") or {}).get("source_key") or "").strip()
        paths = write_outputs(spec_dict, source_key=resolved_source_key or None)
    except Exception as exc:  # noqa: BLE001
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1

    print("已生成（请先打开 REVIEW.md 审阅）：")
    print(f"  审阅说明：{paths['review']}")
    print(f"  spec：    {paths['spec']}")
    print(f"  fixture： {paths['fixture']}")

    if args.run:
        try:
            written = run_fixture(paths["fixture"])
        except Exception as exc:  # noqa: BLE001
            print(f"跑批失败：{exc}", file=sys.stderr)
            return 1
        for md_path, json_path in written:
            print(f"报告：{md_path}\n      {json_path}")
    else:
        print("确认后跑批：")
        print(f"  python -m eval.pipeline.scenario_gen --spec {paths['spec']} --run -v")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
