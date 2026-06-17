"""功能验证一期评测树加载与校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.pipeline.eval_tree import load_eval_tree, validate_eval_tree, validate_service_tag_hints

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "eval" / "content" / "scenarios"

FUNCTIONAL_TREES = [
    ("eval_tree_ma.yaml", 3, {"malicious": 3}),
    ("eval_tree_val.yaml", 3, {"value": 3}),
    ("eval_tree_prec.yaml", 3, {"precedent": 3}),
    ("eval_tree_rule_evidence.yaml", 3, {"rule_evidence": 3}),
]


@pytest.mark.parametrize("tree_name,leaf_count,axis_counts", FUNCTIONAL_TREES)
def test_functional_tree_loads_and_validates(
    tree_name: str,
    leaf_count: int,
    axis_counts: dict[str, int],
) -> None:
    """四棵功能验证树各 3 叶，Markdown 路径存在。"""
    tree_path = SCENARIOS_DIR / tree_name
    leaves = load_eval_tree(tree_path)
    validate_eval_tree(leaves, tree_path=tree_path)
    assert len(leaves) == leaf_count
    for axis_id, expected in axis_counts.items():
        actual = sum(1 for leaf in leaves if leaf.axis_id == axis_id)
        assert actual == expected
    for leaf in leaves:
        assert leaf.path.is_file(), f"缺少情景文件：{leaf.path}"


def test_functional_phase1_total_twelve_leaves() -> None:
    """一期四轴合计 12 叶。"""
    total = 0
    for tree_name, leaf_count, _ in FUNCTIONAL_TREES:
        leaves = load_eval_tree(SCENARIOS_DIR / tree_name)
        total += len(leaves)
        assert len(leaves) == leaf_count
    assert total == 12


MIXED_TREE = ("eval_tree_mixed.yaml", 6, {"mixed": 6})


def test_functional_phase2_mixed_tree_loads() -> None:
    """二期混合树 6 叶，Markdown 路径存在。"""
    tree_name, leaf_count, axis_counts = MIXED_TREE
    tree_path = SCENARIOS_DIR / tree_name
    leaves = load_eval_tree(tree_path)
    validate_eval_tree(leaves, tree_path=tree_path)
    assert len(leaves) == leaf_count
    for axis_id, expected in axis_counts.items():
        actual = sum(1 for leaf in leaves if leaf.axis_id == axis_id)
        assert actual == expected
    for leaf in leaves:
        assert leaf.path.is_file(), f"缺少情景文件：{leaf.path}"


def test_functional_mixed_service_tags_resolve() -> None:
    """二期 MIX 树 service_tag_hint 可解析（无服务标叶跳过）。"""
    tree_name, _, _ = MIXED_TREE
    leaves = load_eval_tree(SCENARIOS_DIR / tree_name)
    warnings = validate_service_tag_hints(leaves)
    assert not warnings, f"服务标无法解析：{warnings}"


MULTISTEP_TREE = ("eval_tree_multistep.yaml", 8, {"multistep": 8})


def test_functional_phase3_multistep_tree_loads() -> None:
    """三期多步树 3 叶，Markdown 路径存在。"""
    tree_name, leaf_count, axis_counts = MULTISTEP_TREE
    tree_path = SCENARIOS_DIR / tree_name
    leaves = load_eval_tree(tree_path)
    validate_eval_tree(leaves, tree_path=tree_path)
    assert len(leaves) == leaf_count
    for axis_id, expected in axis_counts.items():
        actual = sum(1 for leaf in leaves if leaf.axis_id == axis_id)
        assert actual == expected
    for leaf in leaves:
        assert leaf.path.is_file(), f"缺少情景文件：{leaf.path}"


def test_functional_multistep_service_tags_resolve() -> None:
    """三期 MS 树 service_tag_hint 可解析。"""
    tree_name, _, _ = MULTISTEP_TREE
    leaves = load_eval_tree(SCENARIOS_DIR / tree_name)
    warnings = validate_service_tag_hints(leaves)
    assert not warnings, f"服务标无法解析：{warnings}"


def test_functional_service_tags_resolve() -> None:
    """功能验证树 service_tag_hint 可解析到规则库。"""
    warnings: list[str] = []
    for tree_name, _, _ in FUNCTIONAL_TREES:
        leaves = load_eval_tree(SCENARIOS_DIR / tree_name)
        warnings.extend(validate_service_tag_hints(leaves))
    assert not warnings, f"服务标无法解析：{warnings}"
