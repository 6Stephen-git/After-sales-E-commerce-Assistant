"""
辅助判例库：eval/content/cases/CASE-*.json 与 SimilarCase 同构，按 case_id 直读。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from eval.pipeline.paths import CASES_DIR
from schemas import SimilarCase

AUX_CASE_LOG_PREFIX = "[AuxCase]"
logger = logging.getLogger(__name__)

_CASE_ID_PATTERN = re.compile(r"CASE-(?:MAL|VAL|MF|CONF|PREC)-\d{2}", re.IGNORECASE)
_REFERENCE_SECTION_PATTERN = re.compile(
    r"^##\s*参考\s*\n(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _normalize_case_id(raw: str) -> str:
    """统一 CASE 编号为大写。"""
    match = _CASE_ID_PATTERN.search(raw.strip().upper())
    return match.group(0).upper() if match else raw.strip().upper()


def _reference_section(narrative: str) -> str:
    """提取情景 ## 参考 段正文。"""
    match = _REFERENCE_SECTION_PATTERN.search(narrative)
    return match.group(1).strip() if match else ""


def _reference_is_none(section: str) -> bool:
    """参考段为空或「无」。"""
    text = section.strip().replace(" ", "").replace("　", "")
    return not text or text in {"无", "none", "n/a", "na", "—", "-"}


def _case_ids_in_reference(section: str) -> list[str]:
    """从参考段按出现顺序提取不重复的 CASE-*。"""
    seen: set[str] = set()
    ids: list[str] = []
    for match in _CASE_ID_PATTERN.finditer(section):
        case_id = match.group(0).upper()
        if case_id not in seen:
            seen.add(case_id)
            ids.append(case_id)
    return ids


def load_auxiliary_case(case_id: str) -> dict[str, Any]:
    """读取 CASE-XX.json 并校验为 SimilarCase。"""
    normalized = _normalize_case_id(case_id)
    path = CASES_DIR / f"{normalized}.json"
    if not path.is_file():
        raise FileNotFoundError(f"{AUX_CASE_LOG_PREFIX} 未找到：{path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"{AUX_CASE_LOG_PREFIX} 无法读取 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{AUX_CASE_LOG_PREFIX} JSON 非法：{path}，{exc}") from exc

    try:
        return SimilarCase.model_validate(payload).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{AUX_CASE_LOG_PREFIX} 字段不合法 {path.name}：{exc}") from exc


def resolve_similar_cases_from_narrative(narrative: str) -> list[dict[str, Any]] | None:
    """
    根据 ## 参考 解析 similar_cases。

    返回 []（无判例）、list（已加载）、None（非空但无 CASE 引用，保留 LLM 结果）。
    """
    section = _reference_section(narrative)
    if _reference_is_none(section):
        return []
    case_ids = _case_ids_in_reference(section)
    if not case_ids:
        return None

    cases = [load_auxiliary_case(case_id) for case_id in case_ids]
    for case_id in case_ids:
        logger.info("%s 已加载 %s", AUX_CASE_LOG_PREFIX, case_id)
    return cases


def apply_auxiliary_cases(spec_dict: dict[str, Any], narrative: str) -> dict[str, Any]:
    """将 ## 参考 中的 CASE-* 写入 spec.similar_cases。"""
    resolved = resolve_similar_cases_from_narrative(narrative)
    if resolved is None:
        return spec_dict
    return {**spec_dict, "similar_cases": resolved}
