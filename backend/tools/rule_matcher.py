"""
平台规则匹配引擎：doc 锁定 → 节过滤 → LLM 结构化条文选型 → must/should 分级。

单一路径：MySQL 爬取正文 + Agent1 rule_match_plan；LLM 不可用时可回退字面检索。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.connection import get_engine
from backend.db.models import PlatformRule
from backend.tools.rule_lexicon import CE_CONFIDENCE_THRESHOLD, get_doc_by_id
from schemas import (
    RULE_RELEVANCE_MUST,
    RULE_RELEVANCE_SHOULD,
    RULE_RELEVANCE_WEAK,
    RULE_STANCE_BUYER,
    RULE_STANCE_MERCHANT,
    RULE_STANCE_NEUTRAL,
    RULE_CONSTRAINT_APPLIES,
    RULE_CONSTRAINT_EVIDENCE,
    RULE_CONSTRAINT_MISSING_FACT,
    RULE_CONSTRAINT_NO_PROMISE,
    RULE_CONSTRAINT_OTHER,
    RULE_CONSTRAINT_PROCESS,
    RULE_CONSTRAINT_RATIO_LIMIT,
    RULE_CONSTRAINT_TIMING,
    RULE_CONSTRAINT_VIOLATED,
    FRAMES_SKIP_QUALITY_EVIDENCE_GATE,
    FactOutput,
    MatchedRule,
    RuleConstraint,
    RuleBrief,
    RuleMatchPlan,
    RuleMatchResult,
    RuleSearchTerms,
)

LOG_PREFIX = "[RuleMatcher]"
logger = logging.getLogger(__name__)

RULE_DOCUMENT_CACHE_MAX = 64
_rule_document_cache: dict[str, dict[str, Any] | None] = {}

MATCH_POOL_CAP = 30
DISPLAY_MAX = 5
RULE_SUMMARY_MAX_LEN = 480
RULE_BRIEF_MAX_LEN = 320
NON_CATEGORY_LLM_CAP_PER_DOC = 10
LLM_ARTICLE_EXCERPT_MAX = 200
RULE_MATCH_LITERAL_ONLY_MAX = 5

GENERIC_ARTICLE_TITLES = frozenset(
    {"商品质量问题", "描述不当问题", "描述不符问题", "物流问题", "举证要求", "处理标准", "买家原因退换货"}
)
# 仅 case 命中且全部为下列泛化词时，字面降级路径判为 weak 并丢弃
GENERIC_WEAK_ONLY_TERMS = frozenset(
    {"举证", "初步凭证", "商品质量问题", "表面不一致", "签收", "确认收货", "处理标准", "举证要求"}
)

def _strip_rule_display_appendix(text: str) -> str:
    """
    剔除面向商家展示不需要的附录块（规则解读、生效说明等），避免污染报告。
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned
    for marker in ("规则解读：", "规则解读:", "规则解读"):
        idx = cleaned.find(marker)
        if idx >= 0:
            cleaned = cleaned[:idx].strip()
            break
    for marker in ("本规范于", "生效时间", "最新修订"):
        idx = cleaned.find(marker)
        if idx >= 0:
            cleaned = cleaned[:idx].strip()
            break
    return cleaned


def _format_merchant_rule_text(article: dict[str, Any]) -> str:
    """
    将条文转为面向商家展示的通俗要点，不含条号/章节名。
    """
    title = str(article.get("article_title", "") or "").strip()
    content = str(article.get("content", "") or "").strip()
    article_no = str(article.get("article_no", "") or "").strip()
    chapter = str(article.get("chapter", "") or "").strip()

    if article_no and title.startswith(article_no):
        title = title[len(article_no) :].strip(" ：:，,")
    if chapter and title.startswith(chapter):
        title = title[len(chapter) :].strip(" ：:，,")

    if title in GENERIC_ARTICLE_TITLES or len(title) < 6:
        base = content or title
    elif content and content not in title:
        first_sentence = content.split("。")[0].strip()
        base = title if len(title) >= 20 else f"{title}。{first_sentence}" if first_sentence else title
    else:
        base = title or content

    text = _strip_rule_display_appendix(base)
    text = re.sub(r"第[一二三四五六七八九十百千零\d]+条", "", text)
    text = re.sub(r"第[一二三四五六七八九十]+节[^，。；]*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" 。；，,")
    if len(text) > RULE_SUMMARY_MAX_LEN:
        text = text[: RULE_SUMMARY_MAX_LEN - 1].rstrip("，、；") + "…"
    return text or "相关平台规则要点"


def match_rules_from_facts(facts: FactOutput) -> RuleMatchResult:
    """
    根据 FactOutput.rule_match_plan 执行规则匹配。

    参数:
        facts: Agent1 输出，须含 rule_match_plan。

    返回:
        RuleMatchResult（命中池、brief、前端代表条）。
    """
    plan = facts.rule_match_plan
    if not plan.target_doc_ids:
        logger.warning("%s rule_match_plan 无 target_doc_ids，跳过匹配", LOG_PREFIX)
        return RuleMatchResult()

    documents = _load_documents(plan.target_doc_ids)
    if not documents:
        logger.warning("%s 未从 MySQL 加载到任何规则文档", LOG_PREFIX)
        return RuleMatchResult()

    section_map = {sel.doc_id: sel.section_keys for sel in plan.section_selections}

    from backend.tools.rule_matcher_llm import llm_match_articles

    llm_display_ids: list[str] | None = None
    llm_candidates = prepare_llm_candidates(
        documents=documents,
        section_map=section_map,
        terms=plan.search_terms,
    )
    if not llm_candidates:
        logger.warning("%s 无 LLM 候选条文，跳过匹配", LOG_PREFIX)
        return RuleMatchResult()
    if len(llm_candidates) <= RULE_MATCH_LITERAL_ONLY_MAX:
        logger.info(
            "%s 候选条文数≤%s，跳过 LLM 条文选型，走字面评分",
            LOG_PREFIX,
            RULE_MATCH_LITERAL_ONLY_MAX,
        )
        llm_result = None
    else:
        llm_result = llm_match_articles(facts=facts, candidates=llm_candidates)
    if llm_result is None:
        logger.warning("%s LLM 条文匹配失败，回退字面检索", LOG_PREFIX)
        terms = plan.search_terms
        scored: list[tuple[int, str, MatchedRule]] = []
        for doc_id, doc in documents.items():
            articles = _filter_articles_by_sections(doc, section_map.get(doc_id))
            for article in articles:
                rule = _score_article(doc_id, doc, article, terms)
                if rule is not None:
                    scored.append((_relevance_rank(rule.relevance), rule.relevance, rule))
        pool = _sort_pool_category_first([item[2] for item in sorted(scored, key=lambda x: (x[0], -_count_must_signal(x[2])))[:MATCH_POOL_CAP]])
    else:
        llm_display_ids = llm_result.display_rule_ids
        pool = _sort_pool_category_first(llm_result.matched_rules[:MATCH_POOL_CAP])
        pool, supplemented_ids = _ensure_explicit_priority_doc_coverage(
            pool=pool,
            documents=documents,
            section_map=section_map,
            terms=plan.search_terms,
            category_confidence=plan.category_confidence,
            service_confidence=plan.service_confidence,
        )
        if supplemented_ids:
            llm_display_ids = list(llm_display_ids or [])
            for rule_id in supplemented_ids:
                if rule_id not in llm_display_ids:
                    llm_display_ids.append(rule_id)
    constraints = _build_rule_constraints(pool, facts)
    briefs = _build_briefs(pool)
    display = _resolve_display_rules(
        pool,
        facts=facts,
        llm_display_ids=llm_display_ids,
    )
    logger.info(
        "%s 匹配完成 pool=%s briefs=%s display=%s",
        LOG_PREFIX,
        len(pool),
        len(briefs),
        len(display),
    )
    return RuleMatchResult(
        matched_rules=pool,
        rule_briefs=briefs,
        display_rules=display,
        rule_constraints=constraints,
    )


def _parse_rule_document_payload(doc_id: str, rule_content: str | None) -> dict[str, Any] | None:
    """解析 MySQL 中存储的单份规则 JSON。"""
    try:
        payload = json.loads(rule_content or "{}")
    except json.JSONDecodeError:
        logger.warning("%s 规则 JSON 解析失败 doc_id=%s", LOG_PREFIX, doc_id)
        return None
    if not isinstance(payload, dict):
        return None
    normalized_id = str(payload.get("doc_id", "")).strip() or doc_id
    payload["doc_id"] = normalized_id
    return payload


def _rule_key_for_doc_id(doc_id: str) -> str:
    """构造 PlatformRule.rule_key。"""
    return f"taobao_rule::{doc_id}"


def _store_rule_document_cache(doc_id: str, payload: dict[str, Any] | None) -> None:
    """写入规则文档缓存，超出容量时淘汰最早项。"""
    if doc_id not in _rule_document_cache and len(_rule_document_cache) >= RULE_DOCUMENT_CACHE_MAX:
        oldest_key = next(iter(_rule_document_cache))
        _rule_document_cache.pop(oldest_key, None)
    _rule_document_cache[doc_id] = payload


def _fetch_rule_documents_from_db(doc_ids: list[str]) -> dict[str, dict[str, Any] | None]:
    """批量从 MySQL 加载多份规则文档。"""
    if not doc_ids:
        return {}
    rule_keys = [_rule_key_for_doc_id(doc_id) for doc_id in doc_ids]
    key_to_doc_id = {rule_key: doc_id for rule_key, doc_id in zip(rule_keys, doc_ids)}
    loaded: dict[str, dict[str, Any] | None] = {doc_id: None for doc_id in doc_ids}
    engine = get_engine()
    try:
        with Session(bind=engine) as session:
            rows = session.execute(
                select(PlatformRule).where(PlatformRule.rule_key.in_(rule_keys))
            ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.error("%s MySQL 批量加载规则失败 doc_ids=%s：%s", LOG_PREFIX, doc_ids, exc)
        raise RuntimeError(f"平台规则批量加载失败：{exc}") from exc

    for row in rows:
        rule_key = str(row.rule_key or "").strip()
        doc_id = key_to_doc_id.get(rule_key)
        if not doc_id:
            continue
        loaded[doc_id] = _parse_rule_document_payload(doc_id, row.rule_content)

    for doc_id in doc_ids:
        if loaded.get(doc_id) is None:
            logger.warning("%s MySQL 未找到规则 doc_id=%s", LOG_PREFIX, doc_id)
    return loaded


def _load_documents(doc_ids: list[str]) -> dict[str, dict[str, Any]]:
    """按 doc_id 从 MySQL 加载爬取规则 JSON（内存缓存 + 批量查询）。"""
    unique_ids = list(dict.fromkeys(doc_id for doc_id in doc_ids if doc_id))
    missing_ids = [doc_id for doc_id in unique_ids if doc_id not in _rule_document_cache]
    if missing_ids:
        batch_loaded = _fetch_rule_documents_from_db(missing_ids)
        for doc_id, payload in batch_loaded.items():
            _store_rule_document_cache(doc_id, payload)

    loaded: dict[str, dict[str, Any]] = {}
    for doc_id in unique_ids:
        payload = _rule_document_cache.get(doc_id)
        if payload:
            loaded[doc_id] = payload
    return loaded


def clear_rule_document_cache() -> None:
    """清空规则文档内存缓存（规则库更新后联调可调用）。"""
    _rule_document_cache.clear()


def _filter_articles_by_sections(doc: dict[str, Any], section_keys: list[str] | None) -> list[dict[str, Any]]:
    """按 Agent1 选中的 section_key 过滤 articles；未选则回退 default_section_keys。"""
    articles = [a for a in (doc.get("articles") or []) if isinstance(a, dict)]
    if not articles:
        return []

    doc_id = str(doc.get("doc_id", "")).strip()
    if not section_keys:
        lex = get_doc_by_id(doc_id)
        section_keys = (lex or {}).get("default_section_keys") or []

    if not section_keys:
        return articles

    lex = get_doc_by_id(doc_id)
    allowed_nos: set[str] = set()
    if lex:
        for sec in lex.get("sections", []) or []:
            if not isinstance(sec, dict):
                continue
            if sec.get("section_key") in section_keys:
                for no in sec.get("article_nos") or []:
                    allowed_nos.add(str(no).strip())

    if not allowed_nos:
        return articles
    return [a for a in articles if str(a.get("article_no", "")).strip() in allowed_nos]


def _literal_article_score(article: dict[str, Any], terms: RuleSearchTerms) -> int:
    """计算条文与检索词字面命中分，供非 C doc 预筛 cap 排序。"""
    title = str(article.get("article_title", "") or "")
    chapter = str(article.get("chapter", "") or "")
    article_no = str(article.get("article_no", "") or "")
    content = str(article.get("content", "") or "")
    header = f"{chapter} {article_no} {title}"
    blob = f"{header} {content}"
    score = 0
    for term in terms.must_terms:
        if not term:
            continue
        if term in header:
            score += 3
        elif term in blob:
            score += 1
    for term in terms.should_terms:
        if term and term in blob:
            score += 1
    for term in terms.case_terms:
        if term and term in blob:
            score += 1
    return score


def _doc_lane(doc_id: str) -> str:
    """读取规则文档所属通道，未知时返回空字符串。"""
    doc = get_doc_by_id(doc_id)
    return str((doc or {}).get("lane", "") or "").strip()


def _doc_name(doc_id: str) -> str:
    """读取规则文档名称，供 LLM 区分品类、服务与通用规则。"""
    doc = get_doc_by_id(doc_id)
    return str((doc or {}).get("doc_name", "") or doc_id).strip()


def _is_priority_lane_doc(doc_id: str) -> bool:
    """C 品类与 E 服务为贴案优先通道，候选与展示均优先处理。"""
    return _doc_lane(doc_id) in {"C", "E"}


def _lane_rank(doc_id: str) -> int:
    """规则展示排序：品类/服务优先，通用规则靠后。"""
    lane = _doc_lane(doc_id)
    if lane in {"C", "E"}:
        return 0
    if lane == "A":
        return 2
    return 1


def prepare_llm_candidates(
    documents: dict[str, dict[str, Any]],
    section_map: dict[str, list[str] | None],
    terms: RuleSearchTerms,
) -> list[dict[str, Any]]:
    """
    收集条文匹配 LLM 候选：C/E 贴案通道全保留，通用/其它通道按字面分 cap 后合并。
    """
    candidates: list[dict[str, Any]] = []
    for doc_id, doc in documents.items():
        articles = _filter_articles_by_sections(doc, section_map.get(doc_id))
        if _is_priority_lane_doc(doc_id):
            selected = articles
        else:
            ranked = sorted(
                enumerate(articles),
                key=lambda item: (-_literal_article_score(item[1], terms), item[0]),
            )
            selected = [article for _, article in ranked[:NON_CATEGORY_LLM_CAP_PER_DOC]]
        for article in selected:
            if not isinstance(article, dict):
                continue
            article_no = str(article.get("article_no", "") or "").strip()
            if not article_no:
                continue
            candidates.append(
                {
                    "doc_id": doc_id,
                    "doc_name": _doc_name(doc_id),
                    "lane": _doc_lane(doc_id),
                    "article_no": article_no,
                    "article_title": str(article.get("article_title", "") or "").strip(),
                    "content": str(article.get("content", "") or "").strip(),
                    "chapter": str(article.get("chapter", "") or "").strip(),
                }
            )
    logger.info(
        "%s LLM 候选预筛完成 total=%s docs=%s",
        LOG_PREFIX,
        len(candidates),
        len(documents),
    )
    return candidates


def _score_article(
    doc_id: str,
    doc: dict[str, Any],
    article: dict[str, Any],
    terms: RuleSearchTerms,
) -> MatchedRule | None:
    """对单条 article 做篇内评分与分级。"""
    title = str(article.get("article_title", "") or "")
    chapter = str(article.get("chapter", "") or "")
    article_no = str(article.get("article_no", "") or "")
    content = str(article.get("content", "") or "")
    header = f"{chapter} {article_no} {title}"
    blob = f"{header} {content}"

    for ex in terms.exclude_terms:
        if ex and ex in blob:
            return None

    must_hits = 0
    should_hits = 0
    case_hits = 0
    hit_terms: list[str] = []

    for t in terms.must_terms:
        if not t:
            continue
        if t in header:
            must_hits += 3
            hit_terms.append(f"must:{t}")
        elif t in blob:
            must_hits += 1
            hit_terms.append(f"must:{t}")

    for t in terms.should_terms:
        if t and t in blob:
            should_hits += 1
            hit_terms.append(f"should:{t}")

    for t in terms.case_terms:
        if t and t in blob:
            case_hits += 1
            hit_terms.append(f"case:{t}")

    total = must_hits + should_hits + case_hits
    if total <= 0:
        return None

    relevance = _classify_relevance(must_hits, should_hits, case_hits, title, hit_terms)
    if relevance == RULE_RELEVANCE_WEAK:
        return None

    section_key = _resolve_section_key(doc_id, article_no)
    stance = _infer_stance(content)
    summary = _format_merchant_rule_text(article)
    condition = f"通道命中词：{', '.join(hit_terms[:6])}；分级：{relevance}"
    rule_id = f"{doc_id}::{article_no}"

    return MatchedRule(
        rule_id=rule_id,
        rule_summary=summary,
        condition_result=condition,
        relevance=relevance,
        doc_id=doc_id,
        section_key=section_key,
        article_no=article_no,
        stance_hint=stance,
    )


def _classify_relevance(
    must_hits: int,
    should_hits: int,
    case_hits: int,
    title: str,
    hit_terms: list[str],
) -> str:
    """判定 must / should / weak。"""
    if must_hits >= 2 or (must_hits >= 1 and case_hits >= 1):
        return RULE_RELEVANCE_MUST
    if "商品质量问题" in title and (must_hits >= 1 or case_hits >= 1):
        return RULE_RELEVANCE_MUST
    if must_hits >= 1 or should_hits >= 1:
        return RULE_RELEVANCE_SHOULD
    if case_hits >= 1 and not must_hits and not should_hits:
        only_generic = all(
            any(g in ht for g in GENERIC_WEAK_ONLY_TERMS) for ht in hit_terms
        )
        if only_generic:
            return RULE_RELEVANCE_WEAK
        return RULE_RELEVANCE_SHOULD
    return RULE_RELEVANCE_WEAK


def _infer_stance(content: str) -> str:
    """从处理标准归纳站位提示。"""
    text = content.lower()
    if "支持打款" in content or "驳回买家" in content:
        return RULE_STANCE_MERCHANT
    if "支持买家" in content or "退货退款" in content and "卖家" in content:
        return RULE_STANCE_BUYER
    if "支持退款" in content:
        return RULE_STANCE_BUYER
    return RULE_STANCE_NEUTRAL


def _resolve_section_key(doc_id: str, article_no: str) -> str:
    """根据 article_no 反查 section_key。"""
    doc = get_doc_by_id(doc_id)
    if not doc:
        return ""
    for sec in doc.get("sections", []) or []:
        if not isinstance(sec, dict):
            continue
        nos = sec.get("article_nos") or []
        if article_no in nos:
            return str(sec.get("section_key", ""))
    return ""


def _relevance_rank(relevance: str) -> int:
    """排序：must 优先。"""
    return {RULE_RELEVANCE_MUST: 0, RULE_RELEVANCE_SHOULD: 1, RULE_RELEVANCE_WEAK: 2}.get(relevance, 3)


def _count_must_signal(rule: MatchedRule) -> int:
    """用于同分级内排序。"""
    return 1 if rule.relevance == RULE_RELEVANCE_MUST else 0


BASE_BRIEF_CAP_WHEN_CATEGORY = 5


def _sort_pool_category_first(pool: list[MatchedRule]) -> list[MatchedRule]:
    """
    命中池排序：品类/服务（C/E）must/should 在前；有贴案通道时基本规则（A）最多保留少量补充。
    供 rule_briefs（策略 LLM）与 display_rules 共用。
    """
    ordered = sorted(
        pool,
        key=lambda rule: (_lane_rank(rule.doc_id), _relevance_rank(rule.relevance)),
    )
    if any(_is_priority_lane_doc(rule.doc_id) for rule in ordered):
        priority = [rule for rule in ordered if _is_priority_lane_doc(rule.doc_id)]
        non_priority = [rule for rule in ordered if not _is_priority_lane_doc(rule.doc_id)]
        ordered = priority + non_priority[:BASE_BRIEF_CAP_WHEN_CATEGORY]
    return ordered[:MATCH_POOL_CAP]


def _ensure_explicit_priority_doc_coverage(
    *,
    pool: list[MatchedRule],
    documents: dict[str, dict[str, Any]],
    section_map: dict[str, list[str] | None],
    terms: RuleSearchTerms,
    category_confidence: float,
    service_confidence: float,
) -> tuple[list[MatchedRule], list[str]]:
    """
    显式 C/E 通道保底：LLM 选条不得把已锁定的品类/服务规则整份漏掉。

    参数:
        pool: LLM 已选中的规则池。
        documents: 本轮已加载的候选规则文档。
        section_map: Agent1 选中的节。
        terms: 规则检索词。
        category_confidence: 品类通道置信度。
        service_confidence: 服务通道置信度。

    返回:
        (补齐后的 pool, 新增展示 rule_id 列表)。
    """
    existing_doc_ids = {rule.doc_id for rule in pool}
    supplemented: list[MatchedRule] = []
    supplemented_ids: list[str] = []
    for doc_id, doc in documents.items():
        lane = _doc_lane(doc_id)
        if lane == "C" and category_confidence < CE_CONFIDENCE_THRESHOLD:
            continue
        if lane == "E" and service_confidence < CE_CONFIDENCE_THRESHOLD:
            continue
        if lane not in {"C", "E"} or doc_id in existing_doc_ids:
            continue

        scored: list[MatchedRule] = []
        for article in _filter_articles_by_sections(doc, section_map.get(doc_id)):
            rule = _score_article(doc_id, doc, article, terms)
            if rule is None:
                continue
            rule.condition_result = f"{rule.condition_result}；显式{lane}通道补回：LLM未选中已锁定规则文档"
            scored.append(rule)
        scored.sort(key=lambda rule: (_relevance_rank(rule.relevance), -len(rule.rule_summary)))
        if not scored:
            continue
        picked = scored[0]
        supplemented.append(picked)
        supplemented_ids.append(picked.rule_id)

    if not supplemented:
        return pool, []
    merged = _sort_pool_category_first(pool + supplemented)
    return merged, supplemented_ids


def _build_briefs(pool: list[MatchedRule]) -> list[RuleBrief]:
    """仅 must/should 进入策略 brief（pool 须已按品类优先排序）。"""
    briefs: list[RuleBrief] = []
    for rule in pool:
        if rule.relevance not in (RULE_RELEVANCE_MUST, RULE_RELEVANCE_SHOULD):
            continue
        briefs.append(
            RuleBrief(
                article_ref=rule.article_no or rule.rule_id,
                brief=rule.rule_summary[:RULE_BRIEF_MAX_LEN],
                relevance=rule.relevance,
                stance_hint=rule.stance_hint,
                strategy_constraints=list(rule.strategy_constraints or []),
            )
        )
    return briefs


def _facts_rule_context(facts: FactOutput) -> dict[str, Any]:
    """读取 Agent1 attributes.rule_context 中的规则判定上下文。"""
    attrs = facts.attributes if isinstance(facts.attributes, dict) else {}
    context = attrs.get("rule_context")
    return context if isinstance(context, dict) else {}


def _coerce_float(value: Any) -> float | None:
    """安全转换浮点数；失败返回 None。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_ratio(value: float) -> str:
    """将 0~1 比例转成中文百分比文本。"""
    if 0 <= value <= 1:
        return f"{value * 100:.0f}%"
    return f"{value:.0f}%"


def _append_constraint(
    constraints: list[RuleConstraint],
    *,
    constraint_type: str,
    text: str,
    status: str,
    source_rule_id: str,
    confidence: float = 0.8,
) -> None:
    """去重追加结构化规则约束。"""
    normalized = text.strip()
    if not normalized:
        return
    key = (constraint_type, status, normalized, source_rule_id)
    existing = {
        (item.constraint_type, item.status, item.text, item.source_rule_id)
        for item in constraints
    }
    if key in existing:
        return
    constraints.append(
        RuleConstraint(
            constraint_type=constraint_type,
            text=normalized,
            status=status,
            source_rule_id=source_rule_id,
            confidence=confidence,
        )
    )


def _extract_rule_threshold_hours(text: str) -> int | None:
    """从规则文本中提取「签收/服务申请」相关小时阈值。"""
    patterns = (
        r"签收[^。；，,]{0,24}?(\d{1,3})\s*小时内",
        r"签收商品之时起\s*(\d{1,3})\s*小时内",
        r"收到商品[^。；，,]{0,24}?(\d{1,3})\s*小时内",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def _build_rule_constraints(pool: list[MatchedRule], facts: FactOutput) -> list[RuleConstraint]:
    """
    从匹配规则与事实上下文生成可执行约束，供策略层优先消费。
    """
    constraints: list[RuleConstraint] = []
    context = _facts_rule_context(facts)
    time_since_delivery = _coerce_float(context.get("time_since_delivery_hours"))
    ratio_cap = _coerce_float(context.get("compensation_ratio_cap"))
    policy_limits = context.get("policy_limits")
    if ratio_cap is None and isinstance(policy_limits, dict):
        ratio_cap = _coerce_float(policy_limits.get("compensation_ratio_cap"))

    for rule in pool:
        source_rule_id = rule.rule_id
        for text in rule.strategy_constraints or []:
            _append_constraint(
                constraints,
                constraint_type=_infer_constraint_type_from_text(text),
                text=text,
                status=RULE_CONSTRAINT_APPLIES,
                source_rule_id=source_rule_id,
                confidence=0.85,
            )

        rule_text = " ".join(
            str(item)
            for item in [
                rule.rule_summary,
                rule.condition_result,
                " ".join(rule.matched_conditions or []),
                " ".join(rule.violated_or_missing_conditions or []),
            ]
            if item
        )
        if time_since_delivery is not None:
            threshold = _extract_rule_threshold_hours(rule_text)
            if threshold is not None:
                if time_since_delivery >= threshold:
                    status = RULE_CONSTRAINT_VIOLATED if time_since_delivery > threshold else RULE_CONSTRAINT_MISSING_FACT
                    # violated：签收间隔已确定且超窗，策略侧固定事实即可，勿再写「核验是否超过」
                    if status == RULE_CONSTRAINT_VIOLATED:
                        text = (
                            f"本案售后发生在签收后约{time_since_delivery:g}小时，"
                            f"已超过规则要求的{threshold:g}小时申请/举证时效窗口"
                        )
                    else:
                        text = (
                            f"本案售后发生在签收后约{time_since_delivery:g}小时，"
                            f"需先核验并说明是否超过规则要求的{threshold:g}小时内申请/举证时效"
                        )
                else:
                    status = RULE_CONSTRAINT_APPLIES
                    text = f"本案仍在签收后{threshold}小时规则时效内，后续处理仍需按举证和比例规则执行"
                _append_constraint(
                    constraints,
                    constraint_type=RULE_CONSTRAINT_TIMING,
                    text=text,
                    status=status,
                    source_rule_id=source_rule_id,
                    confidence=0.9,
                )
        from backend.agents.agent1.dispute_frame import is_no_defect_claim

        frame = str(facts.primary_dispute_frame or "").strip()
        skip_generic_evidence = frame in FRAMES_SKIP_QUALITY_EVIDENCE_GATE and is_no_defect_claim(
            facts.defect_type
        )
        if not skip_generic_evidence and (
            "举证" in rule_text or "凭证" in rule_text or "照片" in rule_text or "视频" in rule_text
        ):
            _append_constraint(
                constraints,
                constraint_type=RULE_CONSTRAINT_EVIDENCE,
                text="处理退款或补偿前，应先核验买家举证是否满足规则要求，并固定商品照片、视频、快递单和聊天记录",
                status=RULE_CONSTRAINT_APPLIES,
                source_rule_id=source_rule_id,
                confidence=0.8,
            )

    if ratio_cap is not None:
        _append_constraint(
            constraints,
            constraint_type=RULE_CONSTRAINT_RATIO_LIMIT,
            text=f"补偿或让步金额不得超过本单金额的{_format_ratio(ratio_cap)}，超出需人工确认",
            status=RULE_CONSTRAINT_APPLIES,
            source_rule_id="policy_limits",
            confidence=1.0,
        )
    return constraints


def _infer_constraint_type_from_text(text: str) -> str:
    """按配置化词表将约束文案归类为结构化 constraint_type。"""
    from backend.tools.text_signals import constraint_type_markers, contains_any

    normalized = str(text or "")
    if contains_any(normalized, constraint_type_markers("timing")):
        return RULE_CONSTRAINT_TIMING
    if contains_any(normalized, constraint_type_markers("evidence")):
        return RULE_CONSTRAINT_EVIDENCE
    if contains_any(normalized, constraint_type_markers("ratio_limit")):
        return RULE_CONSTRAINT_RATIO_LIMIT
    if contains_any(normalized, constraint_type_markers("no_promise")):
        return RULE_CONSTRAINT_NO_PROMISE
    if contains_any(normalized, constraint_type_markers("process")):
        return RULE_CONSTRAINT_PROCESS
    return RULE_CONSTRAINT_OTHER


def _resolve_display_rules(
    pool: list[MatchedRule],
    *,
    facts: FactOutput,
    llm_display_ids: list[str] | None,
) -> list[MatchedRule]:
    """
    解析前端展示条：LLM 路径只认同批 display_rule_ids（可 0 条）；字面降级走争点启发式，不凑条数。
    """
    if llm_display_ids is not None:
        if not llm_display_ids:
            logger.info("%s 展示规则为空（LLM 判定无贴合展示条）", LOG_PREFIX)
            return []
        id_order = {rid: index for index, rid in enumerate(llm_display_ids)}
        picked = [rule for rule in pool if rule.rule_id in id_order]
        picked.sort(key=lambda rule: (_lane_rank(rule.doc_id), id_order.get(rule.rule_id, 999)))
        logger.info("%s 展示规则使用条文匹配 LLM display_rule_ids count=%s", LOG_PREFIX, len(picked))
        return picked

    return _pick_display_rules(pool)[:DISPLAY_MAX]


def _pick_display_rules(pool: list[MatchedRule]) -> list[MatchedRule]:
    """
    字面降级展示：优先 case 争点命中条，其次品类/服务 must；不强制凑满条数。
    """
    if not pool:
        return []

    case_hit = [
        r
        for r in pool
        if "case:" in r.condition_result and r.relevance in (RULE_RELEVANCE_MUST, RULE_RELEVANCE_SHOULD)
    ]
    if case_hit:
        return _dedupe_rules(sorted(case_hit, key=lambda rule: _lane_rank(rule.doc_id)))

    priority_must = [
        r for r in pool if r.relevance == RULE_RELEVANCE_MUST and _is_priority_lane_doc(r.doc_id)
    ]
    if priority_must:
        return _dedupe_rules(priority_must)

    must_rules = [r for r in pool if r.relevance == RULE_RELEVANCE_MUST]
    if must_rules:
        return _dedupe_rules(must_rules)

    should_rules = [r for r in pool if r.relevance == RULE_RELEVANCE_SHOULD]
    return _dedupe_rules(should_rules)


def _dedupe_rules(rules: list[MatchedRule]) -> list[MatchedRule]:
    """按 rule_id 去重并保持原顺序。"""
    seen: set[str] = set()
    picked: list[MatchedRule] = []
    for rule in rules:
        if rule.rule_id in seen:
            continue
        seen.add(rule.rule_id)
        picked.append(rule)
    return picked

