"""
Scenario Designer Agent：按一个评测树叶子节点生成一个情景 Markdown。

本模块只负责生成可审阅的情景草稿，不生成 fixture、不跑全链路，也不批量产出 pilot 文件。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from eval.pipeline.designer_tools import parse_service_constraint_tags, run_designer_agent_loop
from eval.pipeline.paths import ROOT_DIR
from eval.pipeline.scenario_llm_utils import load_prompt

if load_dotenv is not None:
    load_dotenv(ROOT_DIR / ".env")

DESIGNER_LOG_PREFIX = "[ScenarioDesigner]"
logger = logging.getLogger(__name__)


def _build_user_prompt(
    *,
    axis: str,
    leaf: str,
    target_ability: str,
    factors: str,
    service_constraints: str,
    notes: str,
) -> str:
    """构造单场景生成输入，确保模型围绕一个叶子节点稳定展开。"""
    return (
        "请按以下评测树叶子节点生成 1 个全新的售后纠纷 Markdown 情景。\n\n"
        f"- 大维度：{axis.strip()}\n"
        f"- 小维度：{leaf.strip()}\n"
        f"- 目标能力：{target_ability.strip()}\n"
        f"- 必含业务因子：{factors.strip()}\n"
        f"- 规则/服务标约束：{service_constraints.strip() or '只使用现有平台规则和服务标，不编造新规则'}\n"
        f"- 额外要求：{notes.strip() or '无'}\n"
    )


def generate_scenario_markdown(
    *,
    axis: str,
    leaf: str,
    target_ability: str,
    factors: str,
    service_constraints: str = "",
    notes: str = "",
) -> str:
    """调用 Scenario Designer Agent 生成单个 Markdown 情景。"""
    system_prompt = load_prompt("scenario_designer_system.md")
    user_prompt = _build_user_prompt(
        axis=axis,
        leaf=leaf,
        target_ability=target_ability,
        factors=factors,
        service_constraints=service_constraints,
        notes=notes,
    )
    logger.info("%s 开始生成 axis=%s leaf=%s", DESIGNER_LOG_PREFIX, axis, leaf)
    raw = run_designer_agent_loop(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        required_service_tags=parse_service_constraint_tags(service_constraints),
    )
    if not str(raw).strip():
        raise RuntimeError(f"{DESIGNER_LOG_PREFIX} LLM 调用失败或返回为空")
    return _normalize_designer_markdown(str(raw).strip())


def _strip_markdown_fence(markdown: str) -> str:
    """移除模型偶发包裹的 Markdown 代码围栏。"""
    text = markdown.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _normalize_designer_markdown(markdown: str) -> str:
    """剥离前言与代码围栏，从首个 # 标题行起保留正文。"""
    text = _strip_markdown_fence(markdown)
    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.strip().startswith("# "):
            start = index
            break
    else:
        return text.strip() + "\n"
    return "\n".join(lines[start:]).strip() + "\n"


def _build_arg_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(description="生成单个 pilot 情景 Markdown 草稿")
    parser.add_argument("--axis", required=True, help="大维度，如 商责善后/妥善协商/恶意抗辩")
    parser.add_argument("--leaf", required=True, help="小维度，如 时效边界 + 客户价值")
    parser.add_argument("--target-ability", required=True, help="本场景主测能力")
    parser.add_argument("--factors", required=True, help="必含业务因子，至少 2 个，用逗号或顿号分隔")
    parser.add_argument("--service-constraints", default="", help="可用服务标/规则约束")
    parser.add_argument("--notes", default="", help="额外生成要求")
    parser.add_argument("--output", default="", help="输出 Markdown 路径；为空则打印到标准输出")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口：只生成单个 Markdown，不批量创建 9 个场景。"""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        markdown = generate_scenario_markdown(
            axis=args.axis,
            leaf=args.leaf,
            target_ability=args.target_ability,
            factors=args.factors,
            service_constraints=args.service_constraints,
            notes=args.notes,
        )
        if args.output.strip():
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
            print(f"已生成：{output_path}")
        else:
            print(markdown, end="")
    except Exception as exc:  # noqa: BLE001
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
