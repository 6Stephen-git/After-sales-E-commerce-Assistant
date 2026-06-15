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
DEFAULT_LEXICON_CONFIG_PATH = ROOT_DIR / "data" / "rule_lexicon_config.json"
CE_CONFIDENCE_THRESHOLD = 0.75


@lru_cache(maxsize=1)
def load_lexicon_config() -> dict[str, Any]:
    """
    加载 rule_lexicon_config.json（intent/facet 检索扩展，与主索引分离便于维护）。
    """
    path = Path(os.getenv("RULE_LEXICON_CONFIG_PATH", str(DEFAULT_LEXICON_CONFIG_PATH)))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("%s 扩展配置不存在：%s", LOG_PREFIX, path)
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 读取扩展配置失败：%s", LOG_PREFIX, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


_load_lexicon_config = load_lexicon_config


@lru_cache(maxsize=1)
def load_lexicon() -> dict[str, Any]:
    """
    加载 rule_match_lexicon.json 并合并 rule_lexicon_config.json。
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
    config = _load_lexicon_config()
    for key in (
        "intent_to_facets",
        "facet_search_expansions",
        "intent_search_expansions",
        "category_slug_search_hints",
    ):
        if key in config:
            payload[key] = config[key]
    return payload


def get_intent_to_facets() -> dict[str, tuple[str, ...]]:
    """从 lexicon 合并配置读取诉求标签 → facet 映射。"""
    raw = load_lexicon().get("intent_to_facets") or {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if isinstance(value, list):
            result[str(key)] = tuple(str(item) for item in value)
    return result


def get_doc_by_id(doc_id: str) -> dict[str, Any] | None:
    """按 doc_id 获取文档索引条目。"""
    for doc in load_lexicon().get("docs", []):
        if isinstance(doc, dict) and doc.get("doc_id") == doc_id:
            return doc
    return None


def normalize_service_tag_key(text: str) -> str:
    """服务标展示键：去引号、空白与末尾「服务规范」。"""
    normalized = str(text or "").strip()
    for old, new in (("“", ""), ("”", ""), ("\"", ""), ("'", ""), (" ", ""), ("\u3000", "")):
        normalized = normalized.replace(old, new)
    if normalized.endswith("服务规范"):
        normalized = normalized[: -len("服务规范")]
    return normalized.strip()


def get_service_tag_doc_map() -> dict[str, str]:
    """读取 E 通道服务标 → doc_id 映射。"""
    return dict((load_lexicon().get("lanes") or {}).get("E_service_tag_to_doc_id") or {})


def resolve_service_tag_doc_id(raw_tag: str) -> tuple[str, str] | None:
    """
    将服务标中文名解析为 (canonical 名, doc_id)。

    支持精确匹配、去引号键与子串唯一模糊命中。
    """
    text = str(raw_tag or "").strip()
    if not text:
        return None

    svc_map = get_service_tag_doc_map()
    if not text:
        return None
    if text in svc_map:
        return text, str(svc_map[text])

    keyed = normalize_service_tag_key(text)
    aliases: dict[str, str] = {}
    for canonical in svc_map.keys():
        canon_text = str(canonical or "").strip()
        if not canon_text:
            continue
        for key in {canon_text, normalize_service_tag_key(canon_text)}:
            if key and key not in aliases:
                aliases[key] = canon_text

    if keyed in aliases:
        canonical = aliases[keyed]
        return canonical, str(svc_map[canonical])
    if keyed in svc_map:
        return keyed, str(svc_map[keyed])

    fuzzy: list[str] = []
    keyed_lower = keyed.lower()
    for canonical in svc_map.keys():
        canon_key = normalize_service_tag_key(canonical)
        if not canon_key:
            continue
        if keyed_lower == canon_key.lower():
            return canonical, str(svc_map[canonical])
        if len(canon_key) >= 3 and (canon_key in keyed or keyed in canon_key):
            fuzzy.append(canonical)

    if len(fuzzy) == 1:
        canonical = fuzzy[0]
        return canonical, str(svc_map[canonical])
    if len(fuzzy) > 1:
        fuzzy.sort(key=lambda item: len(normalize_service_tag_key(item)), reverse=True)
        canonical = fuzzy[0]
        return canonical, str(svc_map[canonical])
    return None


def resolve_doc_id_reference(ref: str) -> str | None:
    """
    将 LLM 可能输出的 doc_id / doc_name / 部分路径解析为 lexicon  canonical doc_id。
    """
    text = str(ref or "").strip()
    if not text:
        return None
    if get_doc_by_id(text):
        return text
    for doc in load_lexicon().get("docs", []):
        if not isinstance(doc, dict):
            continue
        canonical = str(doc.get("doc_id", "") or "").strip()
        doc_name = str(doc.get("doc_name", "") or "").strip()
        if not canonical:
            continue
        if doc_name and doc_name == text:
            return canonical
        if text in canonical:
            return canonical
    logger.warning("%s 无法解析规则 doc 引用=%s", LOG_PREFIX, text)
    return None


def infer_facets_from_intent(intent_tags: list[str]) -> list[str]:
    """从诉求标签推断 lexicon facet_tags。"""
    facets: list[str] = []
    for tag in intent_tags or []:
        text = str(tag or "").strip()
        for key, mapped in get_intent_to_facets().items():
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


def validate_category_slug(slug: str | None) -> str | None:
    """
    校验 slug 是否属于 lexicon 品类枚举；合法则返回规范化 slug，否则 None。
    """
    text = str(slug or "").strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return None
    valid_slugs = {item["slug"] for item in build_category_slug_catalog()}
    if text not in valid_slugs:
        logger.warning("%s 未知 category_slug=%s，已忽略", LOG_PREFIX, text)
        return None
    return text


def infer_category_slug_from_materials(materials: dict[str, Any]) -> str | None:
    """
    从 materials 读取已通过 lexicon 校验的 slug 字段；不做中文标签硬匹配。

    中文品类描述须走 resolve_category_slug 的语义推断路径。
    """
    for key in ("product_category_slug", "category_slug"):
        value = materials.get(key)
        if isinstance(value, str) and value.strip():
            validated = validate_category_slug(value.strip())
            if validated:
                return validated
    return None


def _extract_category_label_text(materials: dict[str, Any]) -> str:
    """合并 materials 中的中文品类描述字段。"""
    parts: list[str] = []
    for key in ("category", "product_category", "category_name"):
        value = materials.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " / ".join(dict.fromkeys(parts))


def _format_service_tags_for_prompt(materials: dict[str, Any]) -> str:
    """将平台服务标格式化为 prompt 辅助线索。"""
    tags = materials.get("platform_service_tags")
    if not isinstance(tags, list):
        return "无"
    cleaned = [str(item).strip() for item in tags if str(item or "").strip()]
    return "、".join(cleaned) if cleaned else "无"


def _build_category_inference_blob(
    materials: dict[str, Any],
    text_context: str,
    *,
    issue_summary: str | None = None,
    defect_type: str | None = None,
) -> str:
    """拼装供品类 LLM 理解的案情与材料摘要。"""
    parts: list[str] = []
    label = _extract_category_label_text(materials)
    if label:
        parts.append(f"商品品类（材料）：{label}")
    if defect_type and str(defect_type).strip():
        parts.append(f"瑕疵类型：{str(defect_type).strip()}")
    if issue_summary and str(issue_summary).strip():
        parts.append(f"诉求摘要：{str(issue_summary).strip()}")
    service_tags = _format_service_tags_for_prompt(materials)
    if service_tags != "无":
        parts.append(f"平台服务标：{service_tags}")
    if text_context.strip():
        parts.append(f"买家描述与聊天：\n{text_context.strip()}")
    return "\n".join(parts)


def should_infer_category_slug(
    materials: dict[str, Any],
    text_context: str,
    *,
    issue_summary: str | None = None,
    defect_type: str | None = None,
) -> bool:
    """
    判断是否具备足够上下文启动品类语义推断（无合法 slug 时）。

    不依赖争点关键词表；有品类描述、聊天、服务标或瑕疵摘要即可。
    """
    if infer_category_slug_from_materials(materials):
        return False
    if _extract_category_label_text(materials):
        return True
    if str(issue_summary or "").strip():
        return True
    if str(defect_type or "").strip():
        return True
    if _format_service_tags_for_prompt(materials) != "无":
        return True
    return bool(str(text_context or "").strip())


def resolve_category_slug(
    materials: dict[str, Any],
    text_context: str = "",
    *,
    issue_summary: str | None = None,
    defect_type: str | None = None,
) -> tuple[str | None, float, str]:
    """
    品类 slug 单一路径：校验 slug 字段 → 语义 LLM → 无则放弃。

    返回 (slug, confidence, source)，source 为 api / llm / none。
    """
    existing = infer_category_slug_from_materials(materials)
    if existing:
        return existing, 1.0, "api"

    if not should_infer_category_slug(
        materials,
        text_context,
        issue_summary=issue_summary,
        defect_type=defect_type,
    ):
        return None, 0.0, "none"

    blob = _build_category_inference_blob(
        materials,
        text_context,
        issue_summary=issue_summary,
        defect_type=defect_type,
    )
    slug, confidence = infer_category_slug_llm(blob, materials)
    if slug:
        slug, confidence = disambiguate_inferred_category_slug(
            slug,
            confidence,
            materials,
            text_context,
            issue_summary=issue_summary,
        )
        if slug:
            return slug, confidence, "llm"
    return None, 0.0, "none"


def disambiguate_inferred_category_slug(
    slug: str | None,
    confidence: float,
    materials: dict[str, Any],
    text_context: str,
    *,
    issue_summary: str | None = None,
) -> tuple[str | None, float]:
    """
    校验 LLM 推断的品类 slug 是否与买家实际主张一致，避免成色争议误绑类目规范。

    典型纠偏：买家质疑「有人使用过/有痕迹」但并未购买二手 listing 时，不得选 secondhand。
    """
    normalized = str(slug or "").strip()
    if not normalized:
        return None, 0.0

    if normalized != "secondhand":
        return normalized, confidence

    api_slug = infer_category_slug_from_materials(materials)
    if api_slug == "secondhand":
        return normalized, confidence

    blob = " ".join(
        part
        for part in (
            str(text_context or "").strip(),
            str(issue_summary or "").strip(),
            str(materials.get("product_name") or "").strip(),
            str(materials.get("category") or "").strip(),
        )
        if part
    )
    listing_signals = ("二手", "闲鱼", "翻新", "官翻", "中古", "成色", "99新", "95新", "9成新")
    if any(signal in blob for signal in listing_signals):
        return normalized, confidence

    condition_signals = ("有人使用", "使用过", "像二手", "二手感", "不是全新", "使用痕迹", "用过")
    if any(signal in blob for signal in condition_signals):
        logger.info(
            "%s 品类 slug=secondhand 与买家主张不一致（成色争议非二手类目），已剔除",
            LOG_PREFIX,
        )
        return None, 0.0

    return normalized, confidence


def format_category_slug_catalog_lines() -> str:
    """
    生成写入 LLM prompt 的可选 slug 列表文本。
    """
    catalog = build_category_slug_catalog()
    if not catalog:
        return "（无特殊品类 slug 枚举）"
    return "\n".join(f"- slug={item['slug']} doc={item['doc_name']}" for item in catalog)


def format_category_slug_compact() -> str:
    """
    生成紧凑 slug 枚举（逗号分隔），供 Agent1 事实 LLM 轻量引用。
    """
    catalog = build_category_slug_catalog()
    if not catalog:
        return ""
    return ", ".join(item["slug"] for item in catalog)


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
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    slug = validate_category_slug(str(payload.get("category_slug", "") or "").strip() or None)
    if not slug:
        return None, 0.0
    return slug, confidence


def infer_category_slug_llm(text: str, materials: dict[str, Any] | None = None) -> tuple[str | None, float]:
    """
    用 LLM 从聊天/材料推断品类 slug；失败时返回 (None, 0.0)。
    """
    from backend.tools.llm_client import chat_completion

    api_slug = validate_category_slug(str((materials or {}).get("product_category_slug", "") or "").strip() or None)
    if api_slug and get_category_doc_id(api_slug):
        return api_slug, 1.0

    blob = str(text or "").strip()
    if not blob:
        return None, 0.0

    catalog = build_category_slug_catalog()
    if not catalog:
        return None, 0.0

    catalog_lines = format_category_slug_catalog_lines()
    system_prompt = (
        "你是电商纠纷品类分类助手。根据「卖的是什么商品」与纠纷争点，从给定 slug 枚举中选择最匹配的特殊品类规范；"
        "无法判断则 category_slug 填 null。只输出 JSON："
        "{\"category_slug\":\"slug或null\",\"confidence\":0.0~1.0}\n"
        "规则：\n"
        "1) 必须从枚举中选择，禁止自造 slug；\n"
        "2) 先判断商品品类（宠物/水果生鲜/包装食品/服饰/手机等），再参考争点；"
        "禁止仅因「呕吐、死亡、坏了、烂了」等症状词就选 fresh；\n"
        "3) 活体宠物、猫狗鸟等 → pet；伤亡大病包退等服务标仅作辅助，不能单独决定 slug；\n"
        "4) 需保鲜且易腐的生鲜（水果/海鲜/肉类腐烂变质）→ fresh，勿用 food 替代；\n"
        "5) 包装零食、粮油干货、冲饮奶粉、保质期/标签描述不符 → food；\n"
        "6) 服饰破损/划痕 → apparel；手机/平板功能或外观 → phone。\n"
        "示例：「小狗死亡要求退款」→ pet；「葡萄发霉仅退款」→ fresh；"
        "「零食保质期与页面不符」→ food；「T恤破洞」→ apparel。"
    )
    category_label = _extract_category_label_text(materials or {})
    service_tags = _format_service_tags_for_prompt(materials or {})
    user_prompt = (
        f"可选 slug 列表：\n{catalog_lines}\n\n"
        f"product_category_slug(API)={api_slug or '无'}\n"
        f"商品品类（材料）={category_label or '无'}\n"
        f"平台服务标={service_tags}\n"
        f"案情：\n{blob}"
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
    intent_blob = " ".join(str(item) for item in (intent_tags or []))
    combined_blob = f"{blob} {intent_blob}"
    intent_facets = set(infer_facets_from_intent(intent_tags or []))

    for sec in doc.get("sections", []) or []:
        if not isinstance(sec, dict):
            continue
        sec_key = str(sec.get("section_key", "") or "")
        if key_set and sec_key not in key_set:
            continue
        _extend_unique(must, sec.get("rule_terms"))
        sec_facets = sec.get("facet_tags") or []
        _apply_facet_search_expansions(sec_facets, must, should, case)
        if intent_facets and any(f in sec_facets for f in intent_facets):
            _extend_unique(should, sec.get("rule_terms"))

        aliases = sec.get("case_aliases") if isinstance(sec.get("case_aliases"), dict) else {}
        for colloquial, mapped in aliases.items():
            if colloquial and colloquial in blob:
                _extend_unique(case, [colloquial])
                _extend_unique(must, mapped if isinstance(mapped, list) else [])

    slug = str(doc.get("category_slug", "") or "").strip()
    if slug:
        slug_hints = (load_lexicon().get("category_slug_search_hints") or {}).get(slug)
        if isinstance(slug_hints, dict):
            _extend_unique(must, slug_hints.get("must"))
            _extend_unique(should, slug_hints.get("should"))

    _apply_intent_search_expansions(
        intent_tags=intent_tags or [],
        combined_blob=combined_blob,
        must=must,
        should=should,
        case=case,
    )

    return must, should, case


def _apply_facet_search_expansions(
    sec_facets: list[Any],
    must: list[str],
    should: list[str],
    case: list[str],
) -> None:
    """
    按节 facet_tags 从配置追加检索词，替代 Python 内 facet 穷举。
    """
    expansions = load_lexicon().get("facet_search_expansions") or {}
    if not isinstance(expansions, dict):
        return
    for facet in sec_facets:
        cfg = expansions.get(str(facet))
        if not isinstance(cfg, dict):
            continue
        _extend_unique(must, cfg.get("must"))
        _extend_unique(should, cfg.get("should"))
        _extend_unique(case, cfg.get("case"))


def _apply_intent_search_expansions(
    *,
    intent_tags: list[str],
    combined_blob: str,
    must: list[str],
    should: list[str],
    case: list[str],
) -> None:
    """
    当 intent_tags 或案情文本命中配置触发条件时，追加通用检索扩展词。
    """
    intent_blob = " ".join(str(item) for item in intent_tags)
    for expansion in load_lexicon().get("intent_search_expansions") or []:
        if not isinstance(expansion, dict):
            continue
        tag_triggers = [str(item) for item in (expansion.get("intent_tags") or [])]
        phrase_triggers = [str(item) for item in (expansion.get("trigger_phrases") or [])]
        triggered = any(tag in intent_blob for tag in tag_triggers) or any(
            phrase in combined_blob for phrase in phrase_triggers
        )
        if not triggered:
            continue
        _extend_unique(must, expansion.get("must_terms"))
        _extend_unique(should, expansion.get("should_terms"))
        _extend_unique(case, expansion.get("case_terms"))
        phrase_case = expansion.get("phrase_case_terms") if isinstance(expansion.get("phrase_case_terms"), dict) else {}
        phrase_should = (
            expansion.get("phrase_should_terms") if isinstance(expansion.get("phrase_should_terms"), dict) else {}
        )
        for phrase, case_items in phrase_case.items():
            if phrase and phrase in combined_blob:
                _extend_unique(case, case_items if isinstance(case_items, list) else [case_items])
        for phrase, should_items in phrase_should.items():
            if phrase and phrase in combined_blob:
                _extend_unique(should, should_items if isinstance(should_items, list) else [should_items])


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


def _dedupe(items: list[str]) -> list[str]:
    """保序去重。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
