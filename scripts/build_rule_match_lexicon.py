"""
从 data/rules_raw 构建 rule_match_lexicon.json（规则匹配索引）。

流程：脚本抽节 → 可选 LLM 填 facet/rule_terms 草稿 → 随机 10% 写入待审队列。
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

LOG_PREFIX = "[BuildRuleLexicon]"
logger = logging.getLogger(__name__)

RULES_RAW_DIR = ROOT_DIR / "data" / "rules_raw"
TARGETS_PATH = ROOT_DIR / "data" / "rule_crawl_targets.json"
LEXICON_PATH = ROOT_DIR / "data" / "rule_match_lexicon.json"
REVIEW_QUEUE_PATH = ROOT_DIR / "data" / "rule_match_lexicon_review_queue.json"

SECTION_RE = re.compile(r"^第[一二三四五六七八九十百千零〇0-9]+节\s+.+$")
BLOCK_RE = re.compile(r"^【.+】$")
ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百千零〇0-9]+条)")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千零〇0-9]+章")

# 从正文高频抽取的规则用语种子（全品类通用）
RULE_TERM_SEEDS = [
    "举证", "初步凭证", "退货退款", "质量问题", "描述不当", "表面不一致",
    "签收", "确认收货", "发货", "运费", "七天无理由", "检测凭证",
    "肉眼可识别", "支持买家", "支持打款", "维修", "换货",
]

# 品类 slug：从 doc_name 推导
CATEGORY_SLUG_MAP = {
    "手机": "phone",
    "服饰": "apparel",
    "食品": "food",
    "生鲜": "fresh",
    "鞋": "footwear",
    "箱包": "bags",
    "家具": "furniture",
    "宠物": "pet",
    "虚拟": "virtual",
    "汽车": "automotive",
    "大家电": "major_appliance",
    "大件": "bulky",
    "定制": "custom",
    "二手": "secondhand",
    "珠宝": "jewelry",
    "票务": "ticket",
    "盲盒": "blind_box",
    "鲜花": "flowers",
    "电动车": "ebike",
    "家装": "home_material",
    "成人用品": "adult",
    "手表": "watch",
    "服务类": "service_goods",
}


def load_targets() -> list[dict[str, Any]]:
    """读取 rule_crawl_targets 有效条目。"""
    try:
        payload = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{LOG_PREFIX} 读取 rule_crawl_targets 失败：{exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"{LOG_PREFIX} rule_crawl_targets 根节点须为数组")
    return [item for item in payload if item.get("enabled", True) and item.get("doc_id")]


def find_rules_raw_json(doc_id: str) -> Path | None:
    """按 doc_id 在 rules_raw 下定位 JSON 文件。"""
    matches = list(RULES_RAW_DIR.rglob(f"{doc_id}.json"))
    return matches[0] if matches else None


def find_rules_raw_txt(doc_id: str) -> Path | None:
    """按 doc_id 在 rules_raw 下定位 TXT 文件。"""
    matches = list(RULES_RAW_DIR.rglob(f"{doc_id}.txt"))
    return matches[0] if matches else None


def make_section_key(doc_id: str, label: str) -> str:
    """生成稳定 section_key。"""
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", label).strip("_")[:48]
    return f"{doc_id}::{safe}"


def parse_sections_from_txt(txt_path: Path) -> list[dict[str, Any]]:
    """
    从 TXT 正文解析节标题与条号范围（节边界以正文为准）。
    """
    lines = txt_path.read_text(encoding="utf-8").splitlines()
    sections: list[dict[str, Any]] = []
    current_label = "(篇首)"
    current_articles: list[str] = []
    order = 0

    def flush() -> None:
        nonlocal order, current_label, current_articles
        if not current_articles and current_label == "(篇首)":
            return
        sections.append(
            {
                "section_key": "",
                "label": current_label,
                "article_nos": list(current_articles),
                "order": order,
            }
        )
        order += 1
        current_articles = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if CHAPTER_RE.match(line) and "节" not in line:
            continue
        if SECTION_RE.match(line) or BLOCK_RE.match(line):
            flush()
            current_label = line
            continue
        match = ARTICLE_RE.match(line)
        if match:
            current_articles.append(match.group(1))

    flush()
    return sections


def attach_articles_to_sections(doc: dict[str, Any], sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 JSON articles 按 article_no 归入各节。"""
    articles = doc.get("articles") or []
    if not isinstance(articles, list):
        return sections

    by_no: dict[str, dict[str, Any]] = {}
    for art in articles:
        if isinstance(art, dict):
            no = str(art.get("article_no", "")).strip()
            if no:
                by_no[no] = art

    enriched: list[dict[str, Any]] = []
    doc_id = str(doc.get("doc_id", "")).strip()
    for sec in sections:
        label = str(sec.get("label", "")).strip()
        section_key = make_section_key(doc_id, label)
        article_nos = sec.get("article_nos") or []
        arts = [by_no[no] for no in article_nos if no in by_no]
        if not arts and len(sections) == 1 and articles:
            arts = articles
        content_blob = "\n".join(
            f"{a.get('article_title','')} {a.get('content','')}" for a in arts if isinstance(a, dict)
        )
        rule_terms = [t for t in RULE_TERM_SEEDS if t in content_blob]
        enriched.append(
            {
                "section_key": section_key,
                "label": label,
                "article_nos": article_nos,
                "article_count": len(arts),
                "content_excerpt": content_blob[:400],
                "facet_tags": _infer_facet_tags(label, content_blob),
                "rule_terms": rule_terms[:12],
                "case_aliases": _infer_case_aliases(label, content_blob),
                "exclude_when": _infer_exclude_when(label),
                "typical_disputes": [],
                "reviewed": False,
            }
        )
    return enriched


def _infer_facet_tags(label: str, content: str) -> list[str]:
    """根据节标题与正文启发式归纳 facet（LLM 草稿前的基线）。"""
    text = f"{label} {content}"
    tags: list[str] = []
    mapping = [
        ("质量问题", "quality_claim"),
        ("表面不一致", "surface_inconsistency"),
        ("描述不当", "description_mismatch"),
        ("举证", "evidence_burden"),
        ("发货", "shipping"),
        ("签收", "receipt"),
        ("运费", "freight"),
        ("退货", "return_refund"),
        ("换货", "return_refund"),
        ("七天无理由", "seven_day_return"),
        ("假冒", "counterfeit"),
        ("物流", "logistics"),
    ]
    for kw, tag in mapping:
        if kw in text and tag not in tags:
            tags.append(tag)
    if not tags:
        tags.append("general")
    return tags


def _infer_case_aliases(label: str, content: str) -> dict[str, list[str]]:
    """买家口语到规则词的映射草稿。"""
    aliases: dict[str, list[str]] = {}
    if "质量" in label or "质量" in content:
        aliases["划痕"] = ["商品质量问题", "表面不一致", "初步凭证"]
        aliases["花屏"] = ["商品质量问题", "肉眼可识别"]
        aliases["破损"] = ["商品质量问题", "表面不一致"]
    if "表面不一致" in label:
        aliases["划痕"] = ["表面不一致", "签收"]
        aliases["破损"] = ["表面不一致", "签收时"]
    return aliases


def _infer_exclude_when(label: str) -> list[str]:
    """不宜激活本节的纠纷情形（简短描述）。"""
    if "运费" in label:
        return ["纯质量争议且无运费争议"]
    if "七天无理由" in label:
        return ["订单未购买该服务"]
    return []


def infer_category_slug(doc_name: str) -> str | None:
    """从 doc_name 推导品类 slug。"""
    for key, slug in CATEGORY_SLUG_MAP.items():
        if key in doc_name:
            return slug
    if "餐饮美食卡券" in doc_name:
        return "food_voucher"
    if "二手奢侈品" in doc_name:
        return "luxury_secondhand"
    if "二手数码" in doc_name:
        return "digital_secondhand"
    return None


def infer_service_tag(doc_name: str) -> str | None:
    """从服务保障 doc_name 提取服务标原文。"""
    m = re.search(r"[「\"]([^」\"]+)[」\"]", doc_name)
    if m:
        return m.group(1).strip()
    if "七天无理由" in doc_name:
        return "七天无理由"
    if "破损包退" in doc_name:
        return "破损包退"
    if "破损包赔" in doc_name:
        return "破损包赔"
    if "顺丰包邮" in doc_name:
        return "顺丰包邮"
    return doc_name.replace("淘宝网", "").replace("淘宝平台", "").strip()[:32] or None


def build_lanes(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """构建通道 A～I 的 doc 映射。"""
    lanes: dict[str, Any] = {
        "A_base": [],
        "B_special_trade": [],
        "C_category_slug_to_doc_id": {},
        "D_appeal": [],
        "E_service_tag_to_doc_id": {},
        "F_service_norm": [],
        "G_logistics": [],
        "H_timeout": [],
        "I_review": [],
    }
    for doc in docs:
        doc_id = doc["doc_id"]
        category = doc.get("category", "")
        if category == "争议处理/争议处理基本规则":
            lanes["A_base"].append(doc_id)
        elif category == "争议处理/特殊交易争议处理":
            lanes["B_special_trade"].append(doc_id)
        elif category == "争议处理/特殊品类争议处理":
            slug = doc.get("category_slug")
            if slug:
                lanes["C_category_slug_to_doc_id"][slug] = doc_id
        elif category == "争议处理/纠纷投申诉":
            lanes["D_appeal"].append(doc_id)
        elif category == "交易管理/服务保障":
            tag = doc.get("service_tag")
            if tag:
                lanes["E_service_tag_to_doc_id"][tag] = doc_id
        elif category == "交易管理/服务规范":
            lanes["F_service_norm"].append(doc_id)
        elif category == "交易管理/物流规范":
            lanes["G_logistics"].append(doc_id)
        elif category == "交易管理/超时说明":
            lanes["H_timeout"].append(doc_id)
        elif category == "交易管理/评价规范":
            lanes["I_review"].append(doc_id)
    return lanes


def build_document_entry(target: dict[str, Any]) -> dict[str, Any] | None:
    """合并 target 元数据与 rules_raw 正文，生成单 doc 索引。"""
    doc_id = str(target["doc_id"]).strip()
    json_path = find_rules_raw_json(doc_id)
    if json_path is None:
        logger.warning("%s 未找到 rules_raw JSON，跳过 doc_id=%s", LOG_PREFIX, doc_id)
        return None
    try:
        doc = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 读取 JSON 失败 doc_id=%s 原因=%s", LOG_PREFIX, doc_id, exc)
        return None

    txt_path = find_rules_raw_txt(doc_id)
    if txt_path is not None:
        sections_raw = parse_sections_from_txt(txt_path)
    else:
        sections_raw = [{"section_key": "", "label": "(全文)", "article_nos": [], "order": 0}]
        for art in doc.get("articles") or []:
            if isinstance(art, dict) and art.get("article_no"):
                sections_raw[0]["article_nos"].append(str(art["article_no"]))

    sections = attach_articles_to_sections(doc, sections_raw)
    default_keys = []
    for sec in sections:
        if any(t in sec.get("facet_tags", []) for t in ("quality_claim", "evidence_burden", "general")):
            default_keys.append(sec["section_key"])
            if len(default_keys) >= 2:
                break
    if not default_keys and sections:
        default_keys = [sections[0]["section_key"]]

    doc_name = str(target.get("doc_name", doc.get("doc_name", ""))).strip()
    category = str(target.get("category", doc.get("category", ""))).strip()
    entry: dict[str, Any] = {
        "doc_id": doc_id,
        "doc_name": doc_name,
        "category": category,
        "lane": _lane_for_category(category),
        "category_slug": infer_category_slug(doc_name),
        "service_tag": infer_service_tag(doc_name) if "服务保障" in category else None,
        "default_section_keys": default_keys,
        "sections": sections,
    }
    return entry


def _lane_for_category(category: str) -> str:
    """category 路径映射到通道字母。"""
    if category == "争议处理/争议处理基本规则":
        return "A"
    if category == "争议处理/特殊交易争议处理":
        return "B"
    if category == "争议处理/特殊品类争议处理":
        return "C"
    if category == "争议处理/纠纷投申诉":
        return "D"
    if category == "交易管理/服务保障":
        return "E"
    if category == "交易管理/服务规范":
        return "F"
    if category == "交易管理/物流规范":
        return "G"
    if category == "交易管理/超时说明":
        return "H"
    if category == "交易管理/评价规范":
        return "I"
    return "X"


def maybe_llm_enrich_section(section: dict[str, Any], doc_name: str) -> dict[str, Any]:
    """可选：调用 LLM 丰富 facet/rule_terms（失败则保留启发式结果）。"""
    try:
        from backend.tools.llm_client import chat_completion
    except Exception:  # noqa: BLE001
        return section

    user = (
        f"文档：{doc_name}\n节：{section.get('label')}\n正文摘录：{section.get('content_excerpt','')[:600]}\n"
        "请输出 JSON：facet_tags(数组), rule_terms(数组,须能在摘录中出现), case_aliases(对象), "
        "exclude_when(数组), typical_disputes(数组,最多2条)。禁止编造条号。"
    )
    raw = chat_completion(
        messages=[
            {"role": "system", "content": "你是淘宝规则索引编辑，只输出合法 JSON。"},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
    )
    if not raw:
        return section
    text = raw.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return section
    if isinstance(payload, dict):
        for key in ("facet_tags", "rule_terms", "exclude_when", "typical_disputes"):
            if isinstance(payload.get(key), list):
                section[key] = payload[key]
        if isinstance(payload.get("case_aliases"), dict):
            section["case_aliases"] = payload["case_aliases"]
    return section


def build_review_queue(all_sections: list[dict[str, Any]], ratio: float, seed: int) -> list[dict[str, Any]]:
    """随机抽取 ratio 比例的节写入待审队列。"""
    rng = random.Random(seed)
    pool = list(all_sections)
    rng.shuffle(pool)
    count = max(1, int(len(pool) * ratio)) if pool else 0
    return [
        {
            "section_key": item["section_key"],
            "doc_id": item.get("doc_id", ""),
            "label": item.get("label", ""),
            "reviewed": False,
        }
        for item in pool[:count]
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="构建 rule_match_lexicon.json")
    parser.add_argument("--with-llm", action="store_true", help="对每节调用 LLM 丰富索引字段")
    parser.add_argument("--review-ratio", type=float, default=0.1, help="人工抽检比例，默认 0.1")
    parser.add_argument("--seed", type=int, default=42, help="抽检随机种子")
    args = parser.parse_args()

    targets = load_targets()
    docs: list[dict[str, Any]] = []
    all_sections_flat: list[dict[str, Any]] = []

    logger.info("%s 开始构建，targets=%s", LOG_PREFIX, len(targets))
    for target in targets:
        entry = build_document_entry(target)
        if entry is None:
            continue
        if args.with_llm:
            for idx, sec in enumerate(entry["sections"]):
                entry["sections"][idx] = maybe_llm_enrich_section(sec, entry["doc_name"])
        for sec in entry["sections"]:
            sec["doc_id"] = entry["doc_id"]
            all_sections_flat.append(sec)
        docs.append(entry)

    lanes = build_lanes(docs)
    lexicon = {
        "version": "1.0",
        "generated_from": "data/rules_raw",
        "confidence_threshold_ce": 0.75,
        "docs": docs,
        "lanes": lanes,
    }

    try:
        LEXICON_PATH.write_text(json.dumps(lexicon, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{LOG_PREFIX} 写入 lexicon 失败：{exc}") from exc

    queue = build_review_queue(all_sections_flat, args.review_ratio, args.seed)
    try:
        REVIEW_QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{LOG_PREFIX} 写入抽检队列失败：{exc}") from exc

    logger.info("%s 完成：docs=%s sections=%s 待审=%s", LOG_PREFIX, len(docs), len(all_sections_flat), len(queue))


if __name__ == "__main__":
    main()
