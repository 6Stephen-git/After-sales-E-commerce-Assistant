"""
平台规则原文读取：按服务标从 data/rules_raw 定位并返回 TXT 全文。

供 Scenario Designer 等评测工具在生成情景前按需查阅规则原文。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.tools.rule_lexicon import normalize_service_tag_key, resolve_service_tag_doc_id

LOG_PREFIX = "[RuleTextReader]"
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
RULES_RAW_DIR = ROOT_DIR / "data" / "rules_raw"
MAX_RULE_TEXT_CHARS = 24_000
_OPERATE_CATEGORIES_RE = re.compile(
    r"经营\s*((?:[“\"][^”\"]+[”\"]\s*(?:类目|一级类目)?\s*[、，]\s*)+[“\"][^”\"]+[”\"](?:类目|一级类目)?)"
)
_SINGLE_OPERATE_RE = re.compile(r"经营[“\"]([^”\"]+)[”\"](?:类目|一级类目)")
_LEVEL1_IN_LINE_RE = re.compile(
    r"在\s*((?:[“\"][^”\"]+[”\"]\s*(?:[、，]\s*|[及]\s*))+[“\"][^”\"]+[”\"])\s*一级类目"
)
_QUOTED_TEXT_RE = re.compile(r"[“\"]([^”\"]+)[”\"]")


def find_rules_raw_txt(doc_id: str) -> Path | None:
    """按 doc_id 在 rules_raw 下定位 TXT 文件。"""
    text = str(doc_id or "").strip()
    if not text:
        return None
    try:
        matches = list(RULES_RAW_DIR.rglob(f"{text}.txt"))
    except OSError as exc:
        logger.error("%s 检索 rules_raw 失败 doc_id=%s：%s", LOG_PREFIX, text, exc)
        return None
    return matches[0] if matches else None


def read_service_rule_text(service_tag: str) -> str:
    """
    读取指定服务标的规则原文（rules_raw TXT）。

    返回全文或中文错误说明，供 Agent 工具回调使用。
    """
    raw = str(service_tag or "").strip()
    if not raw:
        return "错误：service_tag 不能为空。"

    resolved = resolve_service_tag_doc_id(raw)
    if not resolved:
        return f"错误：未在规则索引中找到服务标「{raw}」。请检查名称（如 坏单包退、七天无理由）。"

    canonical, doc_id = resolved
    txt_path = find_rules_raw_txt(doc_id)
    if txt_path is None or not txt_path.is_file():
        return (
            f"错误：已解析服务标「{canonical}」（doc_id={doc_id}），"
            f"但 rules_raw 中缺少对应 TXT 文件。"
        )

    try:
        text = txt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.error("%s 读取规则原文失败 path=%s：%s", LOG_PREFIX, txt_path, exc)
        return f"错误：读取规则原文失败：{exc}"

    if not text:
        return f"错误：规则原文为空（{txt_path.name}）。"

    display = normalize_service_tag_key(canonical) or raw
    header = f"# 服务标：{display}\n# doc_id：{doc_id}\n# 文件：{txt_path.name}\n\n"
    body = text
    if len(body) > MAX_RULE_TEXT_CHARS:
        body = (
            body[:MAX_RULE_TEXT_CHARS]
            + f"\n\n…（原文过长，已截断至前 {MAX_RULE_TEXT_CHARS} 字）"
        )
    logger.info("%s 已读取规则原文 tag=%s chars=%d", LOG_PREFIX, display, len(body))
    return header + body


def _dedupe_categories(categories: list[str]) -> list[str]:
    """去重并保持类目名顺序。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in categories:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def extract_applicable_categories(rule_body: str) -> list[str]:
    """
    从规则正文中提取适用品类/一级类目列表。

    兼容「经营 xxx 类目」「在一级类目下指定类目」等多种写法。
    """
    body = str(rule_body or "").strip()
    if not body:
        return []

    categories: list[str] = []

    operate_match = _OPERATE_CATEGORIES_RE.search(body)
    if operate_match:
        categories.extend(_QUOTED_TEXT_RE.findall(operate_match.group(1)))

    if not categories:
        single_match = _SINGLE_OPERATE_RE.search(body)
        if single_match:
            categories.append(single_match.group(1).strip())

    if not categories:
        level1_match = _LEVEL1_IN_LINE_RE.search(body)
        if level1_match:
            categories.extend(_QUOTED_TEXT_RE.findall(level1_match.group(1)))

    return _dedupe_categories(categories)


def extract_rule_design_brief(rule_text: str) -> str:
    """
    从规则原文提取一行设计约束（适用品类等），供 Markdown 生成轮使用。

    不含退款比例、赔付条款等易被套写进情景正文的内容。
    """
    text = str(rule_text or "").strip()
    if not text or text.startswith("错误："):
        return text

    tag_match = re.search(r"^#\s*服务标：(.+)$", text, flags=re.MULTILINE)
    tag_name = tag_match.group(1).strip() if tag_match else "服务标"
    body = re.sub(r"^#.*\n", "", text, flags=re.MULTILINE).strip()

    categories = extract_applicable_categories(body)
    if categories:
        return f"{tag_name} 适用于：{'、'.join(categories)}"

    if "【适用无理由退货与否的商品分类】" in body or "不适用七天无理由" in body:
        return f"{tag_name} 适用于：除规则列明例外类目外的大多数商品（设计前须对照例外清单）"

    logger.warning("%s 未能从规则原文解析适用品类 tag=%s", LOG_PREFIX, tag_name)
    return f"{tag_name} 已查阅；背景品类须落在规则适用范围内（请对照已读原文中的适用范围/准入条款）"
