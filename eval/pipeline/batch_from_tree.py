"""
按评测树批量生成情景 Markdown：对 status=new 的叶子调用 Scenario Designer。

用法:
  python -m eval.pipeline.batch_from_tree
  python -m eval.pipeline.batch_from_tree --only NG-01_rule_boundary_negotiate -v
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from eval.pipeline.eval_tree import (
    EVAL_TREE_PATH,
    EvalLeaf,
    diversity_warnings,
    filter_leaves,
    list_lexicon_service_tags,
    load_eval_tree,
    validate_eval_tree,
    validate_service_tag_hints,
    write_jsonl_line,
)
from eval.pipeline.paths import BATCH_GEN_ERRORS_PATH, ROOT_DIR
from eval.pipeline.scenario_designer import generate_scenario_markdown

if load_dotenv is not None:
    load_dotenv(ROOT_DIR / ".env")

BATCH_GEN_LOG_PREFIX = "[BatchFromTree]"
logger = logging.getLogger(__name__)


def _build_designer_notes(leaf: EvalLeaf) -> str:
    """合并叶子备注与多样性提示，供 Designer 约束品类/服务标。"""
    parts: list[str] = []
    if leaf.category_hint:
        parts.append(f"背景品类建议：{leaf.category_hint}")
    if leaf.service_tag_hint:
        parts.append(f"平台服务标建议：{leaf.service_tag_hint}")
    if leaf.notes:
        parts.append(leaf.notes)
    parts.append(
        "平台服务标必须使用规则库已有标识（勿自造）；本叶指定："
        f"{leaf.service_tag_hint or '无'}"
    )
    return "；".join(parts)


def generate_leaf_markdown(leaf: EvalLeaf) -> str:
    """调用 Designer 为单个叶子生成 Markdown。"""
    service_constraints = leaf.service_constraints.strip() or leaf.service_tag_hint
    return generate_scenario_markdown(
        axis=leaf.axis_label,
        leaf=leaf.leaf,
        target_ability=leaf.target_ability,
        factors=leaf.factors,
        service_constraints=service_constraints,
        notes=_build_designer_notes(leaf),
    )


def run_batch_from_tree(
    *,
    tree_path: Path,
    only: str = "",
    force: bool = False,
    error_log: Path = BATCH_GEN_ERRORS_PATH,
) -> list[tuple[EvalLeaf, Path]]:
    """
    批量生成 new 叶子 Markdown。

    返回成功写入的 (leaf, path) 列表；失败写入 error_log 不抛异常。
    """
    leaves = load_eval_tree(tree_path)
    validate_eval_tree(leaves, tree_path=tree_path)
    for warning in diversity_warnings(leaves):
        logger.warning("%s %s", BATCH_GEN_LOG_PREFIX, warning)
    for warning in validate_service_tag_hints(leaves):
        logger.warning("%s %s", BATCH_GEN_LOG_PREFIX, warning)
    logger.info(
        "%s 规则库服务标共 %d 个",
        BATCH_GEN_LOG_PREFIX,
        len(list_lexicon_service_tags()),
    )

    targets = filter_leaves(leaves, only=only, status="new")
    if not targets:
        logger.info("%s 无待生成叶子（status=new）", BATCH_GEN_LOG_PREFIX)
        return []

    written: list[tuple[EvalLeaf, Path]] = []
    error_log.parent.mkdir(parents=True, exist_ok=True)
    if error_log.is_file() and force:
        error_log.unlink()

    for leaf in targets:
        output_path = leaf.path
        if output_path.is_file() and not force:
            logger.info("%s 跳过已存在：%s", BATCH_GEN_LOG_PREFIX, output_path)
            continue

        started = time.perf_counter()
        logger.info("%s 开始生成 %s", BATCH_GEN_LOG_PREFIX, leaf.case_id)
        try:
            markdown = generate_leaf_markdown(leaf)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "%s 已写入 %s（%d ms）",
                BATCH_GEN_LOG_PREFIX,
                output_path,
                elapsed_ms,
            )
            written.append((leaf, output_path))
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.error(
                "%s 生成失败 %s：%s",
                BATCH_GEN_LOG_PREFIX,
                leaf.case_id,
                exc,
            )
            write_jsonl_line(
                error_log,
                {
                    "case_id": leaf.case_id,
                    "path": str(leaf.path),
                    "error": str(exc),
                    "elapsed_ms": elapsed_ms,
                },
            )
    return written


def _build_arg_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数。"""
    parser = argparse.ArgumentParser(description="按评测树批量生成情景 Markdown")
    parser.add_argument(
        "--tree",
        default=str(EVAL_TREE_PATH),
        help="评测树 YAML 路径",
    )
    parser.add_argument("--only", default="", help="仅生成匹配 case_id/source_key 的叶子")
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的 Markdown",
    )
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
        written = run_batch_from_tree(
            tree_path=Path(args.tree),
            only=args.only,
            force=args.force,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"批量生成失败：{exc}", file=sys.stderr)
        return 1

    print(f"已生成 {len(written)} 个情景 Markdown")
    for leaf, path in written:
        print(f"  {leaf.case_id} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
