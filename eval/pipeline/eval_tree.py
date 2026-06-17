"""
评测树加载与校验：解析 eval_tree*.yaml，提供批量生成/跑批共用的叶子列表。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.pipeline.paths import EVAL_TREE_PATH, ROOT_DIR, SCENARIOS_DIR

_SERVICE_TAG_SKIP = frozenset({"无", ""})
_ONLY_FILTER_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class EvalLeaf:
    """评测树单个叶子节点。"""

    case_id: str
    status: str
    path: Path
    leaf: str
    target_ability: str
    factors: str
    axis_id: str
    axis_label: str
    axis_prefix: str
    category_hint: str = ""
    service_tag_hint: str = ""
    service_constraints: str = ""
    notes: str = ""

    @property
    def source_key(self) -> str:
        """情景文件名 stem（小写）。"""
        return self.path.stem.lower()

    @property
    def report_case_id(self) -> str:
        """跑批报告 case_id（大写 stem）。"""
        return self.path.stem.upper()


def _load_yaml_text(path: Path) -> Any:
    """读取评测树 YAML。"""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "加载评测树需要 PyYAML，请执行：pip install PyYAML"
        ) from exc
    return yaml.safe_load(text)


def _resolve_leaf_path(raw_path: str, axis_dir: str, case_id: str) -> Path:
    """解析叶子 Markdown 路径（显式 path 或按轴目录默认）。"""
    if raw_path.strip():
        candidate = Path(raw_path.strip())
        return candidate if candidate.is_absolute() else ROOT_DIR / candidate
    relative = SCENARIOS_DIR / axis_dir / f"{case_id}.md"
    return relative


def load_eval_tree(tree_path: Path | None = None) -> list[EvalLeaf]:
    """加载评测树全部叶子。"""
    path = tree_path or EVAL_TREE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"评测树文件不存在：{path}")

    payload = _load_yaml_text(path)
    if not isinstance(payload, dict):
        raise ValueError(f"评测树根节点必须是对象：{path}")

    leaves: list[EvalLeaf] = []
    axes = payload.get("axes")
    if not isinstance(axes, list):
        raise ValueError("评测树缺少 axes 列表")

    for axis in axes:
        if not isinstance(axis, dict):
            continue
        axis_id = str(axis.get("id") or "").strip()
        axis_label = str(axis.get("label") or "").strip()
        axis_prefix = str(axis.get("prefix") or "").strip()
        axis_dir = str(axis.get("dir") or "").strip()
        raw_leaves = axis.get("leaves")
        if not axis_id or not isinstance(raw_leaves, list):
            raise ValueError(f"轴 {axis_id or '?'} 配置不完整")

        for item in raw_leaves:
            if not isinstance(item, dict):
                continue
            case_id = str(item.get("case_id") or "").strip()
            if not case_id:
                raise ValueError(f"轴 {axis_id} 存在缺少 case_id 的叶子")
            leaf_path = _resolve_leaf_path(
                str(item.get("path") or ""),
                axis_dir,
                case_id,
            )
            leaves.append(
                EvalLeaf(
                    case_id=case_id,
                    status=str(item.get("status") or "existing").strip(),
                    path=leaf_path,
                    leaf=str(item.get("leaf") or "").strip(),
                    target_ability=str(item.get("target_ability") or "").strip(),
                    factors=str(item.get("factors") or "").strip(),
                    axis_id=axis_id,
                    axis_label=axis_label,
                    axis_prefix=axis_prefix,
                    category_hint=str(item.get("category_hint") or "").strip(),
                    service_tag_hint=str(item.get("service_tag_hint") or "").strip(),
                    service_constraints=str(item.get("service_constraints") or "").strip(),
                    notes=str(item.get("notes") or "").strip(),
                )
            )
    return leaves


def get_tree_expectations(tree_path: Path | None = None) -> tuple[int, dict[str, int]]:
    """从评测树 YAML 读取叶子总数与分轴数量期望。"""
    path = tree_path or EVAL_TREE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"评测树文件不存在：{path}")
    payload = _load_yaml_text(path)
    if not isinstance(payload, dict):
        raise ValueError(f"评测树根节点必须是对象：{path}")
    leaf_count = payload.get("leaf_count")
    raw_counts = payload.get("axis_counts")
    if leaf_count is None or not isinstance(raw_counts, dict) or not raw_counts:
        raise ValueError(f"评测树须声明 leaf_count 与 axis_counts：{path}")
    axis_counts = {str(axis_id): int(count) for axis_id, count in raw_counts.items()}
    return int(leaf_count), axis_counts


def validate_eval_tree(leaves: list[EvalLeaf], *, tree_path: Path | None = None) -> None:
    """校验叶子总数、分轴数量与路径唯一性。"""
    expected_total, axis_counts = get_tree_expectations(tree_path)
    if len(leaves) != expected_total:
        raise ValueError(f"评测树须含 {expected_total} 个叶子，当前为 {len(leaves)}")

    counts: dict[str, int] = {}
    paths: set[str] = set()
    for leaf in leaves:
        counts[leaf.axis_id] = counts.get(leaf.axis_id, 0) + 1
        key = str(leaf.path.resolve())
        if key in paths:
            raise ValueError(f"评测树路径重复：{leaf.path}")
        paths.add(key)

    for axis_id, expected in axis_counts.items():
        actual = counts.get(axis_id, 0)
        if actual != expected:
            raise ValueError(f"轴 {axis_id} 须 {expected} 叶，当前为 {actual}")


def list_lexicon_service_tags() -> list[str]:
    """返回规则库 E 通道服务标短名列表（去引号/「服务规范」后缀，供评测树选题）。"""
    try:
        from backend.tools.rule_lexicon import get_service_tag_doc_map, normalize_service_tag_key
    except ImportError as exc:
        raise RuntimeError("无法导入 rule_lexicon，服务标列表不可用") from exc
    seen: set[str] = set()
    tags: list[str] = []
    for key in get_service_tag_doc_map().keys():
        short = normalize_service_tag_key(str(key))
        if short and short not in seen:
            seen.add(short)
            tags.append(short)
    return sorted(tags)


def validate_service_tag_hints(leaves: list[EvalLeaf]) -> list[str]:
    """校验叶子 service_tag_hint 是否可解析到规则库；返回警告列表。"""
    try:
        from backend.tools.rule_lexicon import resolve_service_tag_doc_id
    except ImportError:
        return []

    warnings: list[str] = []
    for leaf in leaves:
        hint = leaf.service_tag_hint.strip()
        if hint in _SERVICE_TAG_SKIP:
            continue
        if resolve_service_tag_doc_id(hint) is None:
            warnings.append(f"{leaf.case_id} 服务标无法解析：{hint}")
    return warnings


def diversity_warnings(leaves: list[EvalLeaf]) -> list[str]:
    """同轴内「品类+服务标」组合重复时返回警告（不阻断）。"""
    warnings: list[str] = []
    by_axis: dict[str, dict[str, list[str]]] = {}
    for leaf in leaves:
        combo = f"{leaf.category_hint}|{leaf.service_tag_hint}"
        if not leaf.category_hint:
            continue
        axis_map = by_axis.setdefault(leaf.axis_id, {})
        axis_map.setdefault(combo, []).append(leaf.case_id)

    for axis_id, combos in by_axis.items():
        for combo, case_ids in combos.items():
            if len(case_ids) > 1:
                warnings.append(
                    f"轴 {axis_id} 品类+服务标重复 {combo}：{', '.join(case_ids)}"
                )
    return warnings


def filter_leaves(
    leaves: list[EvalLeaf],
    *,
    only: str = "",
    status: str = "",
    require_file: bool = False,
) -> list[EvalLeaf]:
    """按 --only 片段、status 与文件是否存在过滤叶子。"""
    raw = only.strip().lower()
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts and raw:
        parts = [raw]
    tokens = [_ONLY_FILTER_RE.sub("", part) for part in parts if _ONLY_FILTER_RE.sub("", part)]
    result: list[EvalLeaf] = []
    for leaf in leaves:
        if status and leaf.status != status:
            continue
        if tokens:
            haystack = f"{leaf.case_id} {leaf.source_key}".lower()
            if not any(part in haystack for part in tokens):
                continue
        if require_file and not leaf.path.is_file():
            continue
        result.append(leaf)
    return result


def leaf_axis_groups(leaves: list[EvalLeaf]) -> dict[str, list[EvalLeaf]]:
    """按 axis_id 分组。"""
    groups: dict[str, list[EvalLeaf]] = {}
    for leaf in leaves:
        groups.setdefault(leaf.axis_id, []).append(leaf)
    return groups


def dump_tree_summary(leaves: list[EvalLeaf]) -> str:
    """输出评测树摘要（调试/日志）。"""
    lines = [f"评测树共 {len(leaves)} 叶"]
    for axis_id, group in leaf_axis_groups(leaves).items():
        lines.append(f"- {axis_id}: {len(group)}")
    return "\n".join(lines)


def write_jsonl_line(path: Path, payload: dict[str, Any]) -> None:
    """追加一行 JSONL。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
