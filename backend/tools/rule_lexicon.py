"""
规则匹配索引（rule_match_lexicon）加载与导航辅助。

职责：读取离线 lexicon、按通道/品类/服务标解析 doc_id、提供 Agent1 节候选表。
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

LOG_PREFIX = "[RuleLexicon]"
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LEXICON_PATH = ROOT_DIR / "data" / "rule_match_lexicon.json"
CE_CONFIDENCE_THRESHOLD = 0.75

# 诉求标签 → lexicon facet_tags，用于无 section 选择时推断相关节
INTENT_TO_FACETS: dict[str, tuple[str, ...]] = {
    "质量问题": ("quality_claim", "evidence_burden"),
    "物流异常": ("logistics", "receipt", "shipping"),
    "退款诉求": ("return_refund",),
    "描述不符": ("description_mismatch", "evidence_burden"),
    "七天无理由": ("seven_day_return", "return_refund"),
    "运费": ("freight", "shipping"),
    "假冒": ("counterfeit",),
}

# 聊天文本推断品类 slug 的补充关键词（slug 主键来自 lexicon category_slug）
CATEGORY_TEXT_EXTRA_HINTS: dict[str, tuple[str, ...]] = {
    "phone": ("iphone", "ipad", "智能手机", "安卓机"),
    "footwear": ("球鞋", "运动鞋", "皮鞋"),
    "apparel": ("T恤", "外套", "连衣裙"),
    "major_appliance": ("冰箱", "洗衣机", "空调"),
    "fresh": (
        "香蕉", "苹果", "橙子", "柑橘", "草莓", "葡萄", "榴莲", "芒果", "水果",
        "海鲜", "虾", "蟹", "鱼", "牛肉", "猪肉", "鸡肉", "生鲜", "蔬果", "果蔬",
        "腐烂", "变质", "不新鲜", "发臭", "发霉",
    ),
    "food": (
        "零食", "坚果", "特产", "粮油", "速食", "干货", "烘焙", "茶叶", "咖啡",
        "麦片", "冲饮", "奶粉", "保质期", "生产日期", "食品",
    ),
}

# 聊天中出现下列词且伴随生鲜/食品语境时，优先推断为 fresh
FRESH_ISSUE_TERMS: tuple[str, ...] = ("坏了", "烂了", "rotten", "腐", "臭", "霉")
FRESH_CONTEXT_TERMS: tuple[str, ...] = ("收到", "签收", "拆开", "打开", "吃", "买", "寄")


@lru_cache(maxsize=1)
def load_lexicon() -> dict[str, Any]:
    """
    加载 rule_match_lexicon.json；失败时返回空结构并记录日志。
    """
    path = Path(os.getenv("RULE_MATCH_LEXICON_PATH", str(DEFAULT_LEXICON_PATH)))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("%s 索引文件不存在：%s", LOG_PREFIX, path)
        return {"docs": [], "lanes": {}}
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 读取索引失败：%s", LOG_PREFIX, exc)
        return {"docs": [], "lanes": {}}
    if not isinstance(payload, dict):
        return {"docs": [], "lanes": {}}
    return payload


def get_doc_by_id(doc_id: str) -> dict[str, Any] | None:
    """按 doc_id 获取文档索引条目。"""
    for doc in load_lexicon().get("docs", []):
        if isinstance(doc, dict) and doc.get("doc_id") == doc_id:
            return doc
    return None


def infer_facets_from_intent(intent_tags: list[str]) -> list[str]:
    """从诉求标签推断 lexicon facet_tags。"""
    facets: list[str] = []
    for tag in intent_tags or []:
        text = str(tag or "").strip()
        for key, mapped in INTENT_TO_FACETS.items():
            if key in text:
                for facet in mapped:
                    if facet not in facets:
                        facets.append(facet)
    return facets


def truncate_excerpt(text: str, max_len: int = 56) -> str:
    """压缩正文摘录为 prompt 单行摘要。"""
    normalized = str(text or "").replace("\n", " ").strip()
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 1] + "…"


def format_matched_aliases(aliases: dict[str, Any], buyer_text: str) -> str:
    """仅输出聊天中已出现的口语及其规则词映射。"""
    blob = str(buyer_text or "")
    if not blob or not isinstance(aliases, dict):
        return ""
    parts: list[str] = []
    for colloquial, mapped in aliases.items():
        word = str(colloquial or "").strip()
        if not word or word not in blob:
            continue
        targets = [str(x).strip() for x in (mapped or []) if str(x).strip()]
        if targets:
            parts.append(f"{word}→{','.join(targets[:3])}")
    return "; ".join(parts)


def resolve_hint_section_keys(
    doc: dict[str, Any],
    section_keys: list[str] | None,
    intent_tags: list[str] | None,
) -> list[str]:
    """
    解析用于检索词补全的节 key：优先显式选择，其次 default，再按 facet 推断。
    """
    keys = [str(k).strip() for k in (section_keys or []) if str(k).strip()]
    if keys:
        return keys

    defaults = doc.get("default_section_keys") or []
    if defaults:
        return [str(k).strip() for k in defaults if str(k).strip()]

    facets = infer_facets_from_intent(intent_tags or [])
    if not facets:
        return []

    matched: list[str] = []
    for sec in doc.get("sections", []) or []:
        if not isinstance(sec, dict):
            continue
        sec_facets = sec.get("facet_tags") or []
        if any(f in sec_facets for f in facets):
            key = str(sec.get("section_key", "") or "").strip()
            if key:
                matched.append(key)
    return matched[:3]


def list_section_candidates_for_doc(
    doc_id: str,
    buyer_text: str = "",
    intent_tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    返回某 doc 的节候选（供 Agent1 勾选），含 rule_terms、口语映射与摘录摘要。
    """
    doc = get_doc_by_id(doc_id)
    if not doc:
        return []

    priority_facets = set(infer_facets_from_intent(intent_tags or []))
    rows: list[dict[str, Any]] = []
    for sec in doc.get("sections", []) or []:
        if not isinstance(sec, dict):
            continue
        sec_facets = sec.get("facet_tags") or []
        rule_terms = [str(t).strip() for t in (sec.get("rule_terms") or []) if str(t).strip()]
        aliases = sec.get("case_aliases") if isinstance(sec.get("case_aliases"), dict) else {}
        rows.append(
            {
                "section_key": sec.get("section_key", ""),
                "label": sec.get("label", ""),
                "facet_tags": sec_facets,
                "rule_terms_top": rule_terms[:5],
                "alias_hint": format_matched_aliases(aliases, buyer_text),
                "excerpt_snippet": truncate_excerpt(str(sec.get("content_excerpt", "") or "")),
                "facet_match": bool(priority_facets and any(f in sec_facets for f in priority_facets)),
            }
        )

    if priority_facets:
        rows.sort(key=lambda row: (0 if row.get("facet_match") else 1, str(row.get("label", ""))))
    return rows


def resolve_doc_ids_from_materials(materials: dict[str, Any]) -> tuple[list[str], list[str]]:
    """
    根据 materials 与 lanes 解析应激活的 doc_id 列表与通道字母列表。

    返回:
        (target_doc_ids, activated_lanes)
    """
    lexicon = load_lexicon()
    lanes = lexicon.get("lanes") or {}
    activated: list[str] = []
    doc_ids: list[str] = []

    base = lanes.get("A_base") or []
    if base:
        doc_ids.extend(base)
        activated.append("A")

    slug = str(materials.get("product_category_slug", "") or "").strip()
    if slug:
        cat_map = lanes.get("C_category_slug_to_doc_id") or {}
        if slug in cat_map:
            doc_ids.append(cat_map[slug])
            activated.append("C")

    tags = materials.get("platform_service_tags") or []
    if isinstance(tags, list):
        svc_map = lanes.get("E_service_tag_to_doc_id") or {}
        for tag in tags:
            text = str(tag).strip()
            if text and text in svc_map:
                doc_ids.append(svc_map[text])
                if "E" not in activated:
                    activated.append("E")

    return _dedupe(doc_ids), activated


def is_category_doc(doc_id: str) -> bool:
    """判断 doc_id 是否属于特殊品类通道 C。"""
    doc = get_doc_by_id(doc_id)
    return bool(doc and doc.get("lane") == "C")


def get_category_doc_id(slug: str) -> str | None:
    """按 category_slug 解析品类规范 doc_id。"""
    text = str(slug or "").strip()
    if not text:
        return None
    cat_map = (load_lexicon().get("lanes") or {}).get("C_category_slug_to_doc_id") or {}
    doc_id = cat_map.get(text)
    return str(doc_id).strip() if doc_id else None


def build_category_slug_catalog() -> list[dict[str, str]]:
    """
    列出 lexicon 中全部特殊品类 slug 与 doc 名称，供 LLM 结构化分类。
    """
    catalog: list[dict[str, str]] = []
    seen: set[str] = set()
    cat_map = (load_lexicon().get("lanes") or {}).get("C_category_slug_to_doc_id") or {}
    for slug, doc_id in cat_map.items():
        slug_text = str(slug or "").strip()
        if not slug_text or slug_text in seen:
            continue
        seen.add(slug_text)
        doc = get_doc_by_id(str(doc_id))
        catalog.append(
            {
                "slug": slug_text,
                "doc_name": str((doc or {}).get("doc_name", "") or doc_id),
            }
        )
    catalog.sort(key=lambda item: item["slug"])
    return catalog


def _parse_category_llm_json(raw_text: str) -> tuple[str | None, float]:
    """
    解析品类 LLM JSON：category_slug + confidence。
    """
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None, 0.0
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None, 0.0
    if not isinstance(payload, dict):
        return None, 0.0
    slug = str(payload.get("category_slug", "") or "").strip()
    if slug.lower() in {"none", "null", "unknown", ""}:
        return None, 0.0
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    valid_slugs = {item["slug"] for item in build_category_slug_catalog()}
    if slug not in valid_slugs:
        logger.warning("%s LLM 返回未知 category_slug=%s，已忽略", LOG_PREFIX, slug)
        return None, 0.0
    return slug, confidence


def infer_category_slug_llm(text: str, materials: dict[str, Any] | None = None) -> tuple[str | None, float]:
    """
    用 LLM 从聊天/材料推断品类 slug；失败时返回 (None, 0.0)。
    """
    from backend.tools.llm_client import chat_completion

    api_slug = str((materials or {}).get("product_category_slug", "") or "").strip()
    if api_slug and get_category_doc_id(api_slug):
        return api_slug, 1.0

    blob = str(text or "").strip()
    if not blob:
        return None, 0.0

    catalog = build_category_slug_catalog()
    if not catalog:
        return None, 0.0

    catalog_lines = "\n".join(f"- slug={item['slug']} doc={item['doc_name']}" for item in catalog)
    system_prompt = (
        "你是电商纠纷品类分类助手。根据买家描述，从给定 slug 枚举中选择最匹配的特殊品类；"
        "无法判断则 category_slug 填 null。\n"
        "只输出 JSON：{\"category_slug\":\"apparel或null\",\"confidence\":0.0~1.0}\n"
        "示例1：「衣服袖子破洞」→ apparel, 0.92\n"
        "示例2：「手机屏幕划痕」→ phone, 0.95\n"
        "示例3：「香蕉收到就烂了」→ fresh, 0.9"
    )
    user_prompt = (
        f"可选 slug 列表：\n{catalog_lines}\n\n"
        f"product_category_slug(API)={api_slug or '无'}\n"
        f"买家描述：\n{blob}"
    )
    llm_text = chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model_env_key="AGENT1_LLM_MODEL",
        temperature=0.0,
    )
    if not llm_text:
        logger.info("%s 品类 LLM 无响应，跳过推断", LOG_PREFIX)
        return None, 0.0
    slug, confidence = _parse_category_llm_json(llm_text)
    if slug:
        logger.info("%s 品类 LLM 推断 slug=%s confidence=%.2f", LOG_PREFIX, slug, confidence)
    return slug, confidence


def infer_category_slug_from_text(text: str) -> str | None:
    """
    从聊天/诉求文本推断品类 slug（LLM 主路径；LLM 不可用时不做关键词猜测）。
    """
    slug, _confidence = infer_category_slug_llm(text=text, materials=None)
    return slug


def collect_lexicon_search_hints(
    doc_id: str,
    section_keys: list[str] | None,
    buyer_text: str,
    intent_tags: list[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """
    从 lexicon 节条目收集规则词，并将买家口语经 case_aliases 映射为 must 词。

    返回:
        (must_terms, should_terms, case_terms)
    """
    doc = get_doc_by_id(doc_id)
    if not doc:
        return [], [], []

    keys = resolve_hint_section_keys(doc, section_keys, intent_tags)
    key_set = set(keys)

    must: list[str] = []
    should: list[str] = []
    case: list[str] = []
    blob = str(buyer_text or "")
    intent_facets = set(infer_facets_from_intent(intent_tags or []))

    for sec in doc.get("sections", []) or []:
        if not isinstance(sec, dict):
            continue
        sec_key = str(sec.get("section_key", "") or "")
        if key_set and sec_key not in key_set:
            continue
        _extend_unique(must, sec.get("rule_terms"))
        sec_facets = sec.get("facet_tags") or []
        if "evidence_burden" in sec_facets:
            _extend_unique(must, ["举证", "初步凭证"])
        if "quality_claim" in sec_facets:
            _extend_unique(must, ["商品质量问题", "肉眼可识别", "检测凭证"])
            _extend_unique(should, ["表面不一致", "描述不当"])
        if "return_refund" in sec_facets:
            _extend_unique(should, ["退货退款", "签收"])
        if "logistics" in sec_facets or "shipping" in sec_facets:
            _extend_unique(must, ["发货", "物流"])
        if intent_facets and any(f in sec_facets for f in intent_facets):
            _extend_unique(should, sec.get("rule_terms"))

        slug = str(doc.get("category_slug", "") or "")
        if slug == "fresh":
            _extend_unique(must, ["腐烂", "变质", "48小时", "拆包视频"])
            _extend_unique(should, ["签收", "退货退款"])
            for colloquial, mapped in {
                "坏了": ["腐烂", "变质", "质量问题"],
                "烂了": ["腐烂", "变质"],
                "香蕉": ["腐烂", "变质"],
                "不新鲜": ["腐烂", "变质"],
            }.items():
                if colloquial in blob:
                    _extend_unique(case, [colloquial])
                    _extend_unique(must, mapped)
        elif slug == "food":
            _extend_unique(must, ["保质期", "生产日期", "描述不符"])
            _extend_unique(should, ["退货退款", "运费"])

        aliases = sec.get("case_aliases") if isinstance(sec.get("case_aliases"), dict) else {}
        for colloquial, mapped in aliases.items():
            if colloquial and colloquial in blob:
                _extend_unique(case, [colloquial])
                _extend_unique(must, mapped if isinstance(mapped, list) else [])

    return must, should, case


def _extend_unique(target: list[str], new_items: Any) -> None:
    """去重追加字符串列表。"""
    if not isinstance(new_items, list):
        return
    for item in new_items:
        text = str(item or "").strip()
        if text and text not in target:
            target.append(text)


def infer_lanes_from_intent(intent_tags: list[str], logistics_normal: bool | None) -> list[str]:
    """
    从诉求标签推断需追加的通道（无 API 标时的辅助）。
    """
    lanes: list[str] = []
    text = " ".join(intent_tags)
    if any(k in text for k in ("物流", "未签收", "发货")) or logistics_normal is False:
        lanes.append("G")
    if any(k in text for k in ("申诉", "标错")):
        lanes.append("D")
    return lanes


def expand_doc_ids_by_lanes(lane_letters: list[str]) -> list[str]:
    """将通道字母展开为 doc_id 列表。"""
    lexicon = load_lexicon()
    lanes = lexicon.get("lanes") or {}
    mapping = {
        "A": "A_base",
        "B": "B_special_trade",
        "C": None,
        "D": "D_appeal",
        "E": None,
        "F": "F_service_norm",
        "G": "G_logistics",
        "H": "H_timeout",
        "I": "I_review",
    }
    doc_ids: list[str] = []
    for letter in lane_letters:
        key = mapping.get(letter)
        if key is None:
            continue
        block = lanes.get(key)
        if isinstance(block, list):
            doc_ids.extend(block)
    return _dedupe(doc_ids)


def validate_section_keys(doc_id: str, section_keys: list[str]) -> list[str]:
    """过滤不在 lexicon 中的 section_key。"""
    doc = get_doc_by_id(doc_id)
    if not doc:
        return []
    valid = {str(s.get("section_key", "")) for s in doc.get("sections", []) if isinstance(s, dict)}
    kept = [k for k in section_keys if k in valid]
    dropped = [k for k in section_keys if k not in valid]
    if dropped:
        logger.warning("%s 丢弃无效 section_key doc_id=%s keys=%s", LOG_PREFIX, doc_id, dropped)
    return kept


def build_agent1_lane_hint(materials: dict[str, Any], intent_tags: list[str]) -> str:
    """
    为 Agent1 prompt 生成精简的通道与 doc 导航说明（控制 token）。
    """
    doc_ids, lanes = resolve_doc_ids_from_materials(materials)
    extra_lanes = infer_lanes_from_intent(intent_tags, materials.get("_logistics_normal"))
    for letter in extra_lanes:
        if letter not in lanes:
            lanes.append(letter)
    if "G" in lanes:
        doc_ids = _dedupe(doc_ids + expand_doc_ids_by_lanes(["G"]))

    lines = [f"已解析通道：{','.join(lanes) or 'A'}", f"候选 doc 数：{len(doc_ids)}"]
    for doc_id in doc_ids[:6]:
        doc = get_doc_by_id(doc_id)
        if not doc:
            continue
        secs = list_section_candidates_for_doc(doc_id)
        sec_labels = [f"{s['section_key']}:{s['label'][:20]}" for s in secs[:8]]
        lines.append(f"- {doc.get('doc_name','')}: " + "; ".join(sec_labels))
    return "\n".join(lines)


def _dedupe(items: list[str]) -> list[str]:
    """保序去重。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
