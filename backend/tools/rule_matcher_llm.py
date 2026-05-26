"""
规则匹配 LLM 层：在已锁定 doc/节候选集内，结构化判定适用条文。

职责：将买家案情语义映射到平台规则正文，输出 must/should 分级；替代篇内子串硬匹配主路径。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from backend.tools.llm_client import chat_completion
from schemas import (
    RULE_RELEVANCE_MUST,
    RULE_RELEVANCE_SHOULD,
    RULE_RELEVANCE_WEAK,
    VALID_RULE_RELEVANCE,
    FactOutput,
    MatchedRule,
)

LOG_PREFIX = "[RuleMatcherLLM]"
logger = logging.getLogger(__name__)

LLM_MODEL_ENV = "AGENT2_LLM_MODEL"


@dataclass
class RuleMatchLLMResult:
    """条文匹配 LLM 输出：命中池 + 可选同批前端展示 rule_id 列表。"""

    matched_rules: list[MatchedRule]
    display_rule_ids: list[str] | None = None


def _strip_markdown_json(raw_text: str) -> str:
    """
    去除 markdown 代码块包裹，便于 json.loads。
    """
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_object(raw_text: str) -> dict[str, Any] | None:
    """
    解析 LLM 返回的 JSON 对象；失败时尝试截取首个 {...}。
    """
    cleaned = _strip_markdown_json(raw_text)
    if not cleaned:
        return None
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None


def _build_dispute_context(facts: FactOutput) -> str:
    """
    拼装供 LLM 理解的案情摘要。
    """
    parts: list[str] = []
    if facts.issue_summary:
        parts.append(f"诉求摘要：{facts.issue_summary}")
    if facts.defect_type:
        parts.append(f"瑕疵类型：{facts.defect_type}")
    if facts.intent_tags:
        parts.append(f"诉求标签：{', '.join(facts.intent_tags)}")
    if facts.goods_received is not None:
        parts.append(f"是否收货：{facts.goods_received}")
    if facts.logistics_normal is not None:
        parts.append(f"物流是否正常：{facts.logistics_normal}")
    if facts.evidence_quality:
        parts.append(f"证据质量：{facts.evidence_quality}")
    if facts.missing_evidence:
        parts.append(f"缺失举证：{'、'.join(str(x) for x in facts.missing_evidence[:4])}")
    if facts.red_flags:
        parts.append(f"疑点：{'、'.join(str(x) for x in facts.red_flags[:4])}")
    if facts.visual_observations:
        parts.append(f"视觉观察：{'、'.join(str(x) for x in facts.visual_observations[:3])}")
    terms = facts.rule_match_plan.search_terms
    if terms.must_terms or terms.case_terms:
        parts.append(f"规则检索词 must={terms.must_terms} case={terms.case_terms}")
    return "\n".join(parts) if parts else "（案情信息有限）"


def _collect_candidate_articles(
    documents: dict[str, dict[str, Any]],
    section_map: dict[str, list[str] | None],
) -> list[dict[str, Any]]:
    """
    从已加载文档中收集候选条文（含 doc_id、article_no、标题与正文）。
    """
    from backend.tools.rule_matcher import _filter_articles_by_sections

    candidates: list[dict[str, Any]] = []
    for doc_id, doc in documents.items():
        articles = _filter_articles_by_sections(doc, section_map.get(doc_id))
        for article in articles:
            if not isinstance(article, dict):
                continue
            article_no = str(article.get("article_no", "") or "").strip()
            if not article_no:
                continue
            candidates.append(
                {
                    "doc_id": doc_id,
                    "article_no": article_no,
                    "article_title": str(article.get("article_title", "") or "").strip(),
                    "content": str(article.get("content", "") or "").strip(),
                    "chapter": str(article.get("chapter", "") or "").strip(),
                }
            )
    return candidates


def _build_article_match_system_prompt() -> str:
    """
    构建条文匹配 system prompt：判定目标、分级枚举、跨品类 few-shot。
    """
    return (
        "你是淘宝平台规则匹配专家。任务：在给定候选条文中，选出与当前纠纷事实直接相关的条款。\n"
        "要求：\n"
        "1) 只输出 JSON，不要解释性段落；\n"
        "2) matched_articles 仅包含 candidate_index（整数，从 0 起）与 relevance（must/should）；"
        "禁止输出候选集外的条号；\n"
        "3) 买家口语与规则用语不等价时，按规则语义判断（如破洞/撕裂/勾丝可适用「破损/质量问题」条）；\n"
        "4) 品类专项规范与基本规则均可能同时命中；品类条通常更具体，优先 must；"
        "若候选≥2 条且案情涉及质量/举证/退款，matched_articles 应输出 2～5 条（含品类专项 + 基本规则，勿只选一条）；\n"
        "5) reason 用中文一句话说明为何适用；\n"
        "6) display_rule_ids：从已选 matched_articles 对应 rule_id（doc_id::article_no）中挑 1～3 条与本案争点最直接相关的供前端展示；"
        "排除泛化程序条（如举证责任分配原则、运费风险归属），除非案情明确涉及。\n"
        "示例1（服饰破损）：候选含服饰「破损/质量问题」条 + 基本规则「初步凭证」条 → 两条均输出，品类 must、基本 should；"
        "display_rule_ids 取品类质量条 + 初步凭证条。\n"
        "示例2（手机划痕）：候选含手机规范条 + 基本规则条 → 至少输出 2 条。"
    )


def _build_article_match_user_prompt(
    facts: FactOutput,
    candidates: list[dict[str, Any]],
) -> str:
    """
    构建 user prompt：案情 + 候选条文列表。
    """
    lines = [
        "请根据案情从候选条文中选出所有适用项。",
        "",
        "【案情】",
        _build_dispute_context(facts),
        "",
        "【候选条文】",
    ]
    for index, item in enumerate(candidates):
        title = item.get("article_title", "")
        content = item.get("content", "")
        excerpt = content[:480] + ("…" if len(content) > 480 else "")
        lines.append(
            f"- candidate_index={index} doc_id={item.get('doc_id')} "
            f"article_no={item.get('article_no')} title={title} content={excerpt}"
        )
    lines.extend(
        [
            "",
            "输出 JSON：",
            '{"matched_articles":[{"candidate_index":0,"relevance":"must","reason":"..."}],'
            '"display_rule_ids":["doc_id::article_no"],'
            '"term_mapping":[{"buyer_term":"破洞","rule_term":"破损"}]}',
        ]
    )
    return "\n".join(lines)


def _parse_display_rule_ids(raw: Any, matched_rules: list[MatchedRule]) -> list[str] | None:
    """
    解析 display_rule_ids 并校验属于本次命中池。
    """
    if not isinstance(raw, list) or not matched_rules:
        return None
    allowed = {rule.rule_id for rule in matched_rules}
    ordered: list[str] = []
    for item in raw:
        rid = str(item or "").strip()
        if rid and rid in allowed and rid not in ordered:
            ordered.append(rid)
    return ordered or None


def _normalize_relevance(raw: Any) -> str:
    """
    校验 relevance 枚举。
    """
    value = str(raw or "").strip().lower()
    if value in VALID_RULE_RELEVANCE and value != RULE_RELEVANCE_WEAK:
        return value
    if value == RULE_RELEVANCE_WEAK:
        return RULE_RELEVANCE_SHOULD
    return RULE_RELEVANCE_SHOULD


def llm_match_articles(
    facts: FactOutput,
    documents: dict[str, dict[str, Any]],
    section_map: dict[str, list[str] | None],
) -> RuleMatchLLMResult | None:
    """
    调用 LLM 在候选条文中结构化选型；失败返回 None 供上层降级。
    """
    candidates = _collect_candidate_articles(documents=documents, section_map=section_map)
    if not candidates:
        logger.warning("%s 无候选条文，跳过 LLM 匹配", LOG_PREFIX)
        return RuleMatchLLMResult(matched_rules=[], display_rule_ids=None)

    llm_text = chat_completion(
        messages=[
            {"role": "system", "content": _build_article_match_system_prompt()},
            {"role": "user", "content": _build_article_match_user_prompt(facts, candidates)},
        ],
        model_env_key=LLM_MODEL_ENV,
        temperature=0.0,
    )
    if not llm_text:
        logger.warning("%s LLM 无响应，无法完成条文匹配", LOG_PREFIX)
        return None

    parsed = _parse_json_object(llm_text)
    if not parsed:
        logger.warning("%s LLM 返回非 JSON：%s", LOG_PREFIX, llm_text[:200])
        return None

    matched_rules: list[MatchedRule] = []
    for item in parsed.get("matched_articles") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("candidate_index"))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(candidates):
            continue
        candidate = candidates[index]
        from backend.tools.rule_matcher import (
            _format_merchant_rule_text,
            _infer_stance,
            _resolve_section_key,
        )

        doc_id = str(candidate.get("doc_id", ""))
        article_no = str(candidate.get("article_no", ""))
        relevance = _normalize_relevance(item.get("relevance"))
        reason = str(item.get("reason", "") or "").strip()[:120]
        content = str(candidate.get("content", "") or "")
        summary = _format_merchant_rule_text(candidate)
        section_key = _resolve_section_key(doc_id, article_no)
        stance = _infer_stance(content)
        condition = f"LLM判定：{reason or '适用当前案情'}；分级：{relevance}"
        matched_rules.append(
            MatchedRule(
                rule_id=f"{doc_id}::{article_no}",
                rule_summary=summary,
                condition_result=condition,
                relevance=relevance,
                doc_id=doc_id,
                section_key=section_key,
                article_no=article_no,
                stance_hint=stance,
            )
        )

    logger.info("%s LLM 匹配完成：候选=%s 命中=%s", LOG_PREFIX, len(candidates), len(matched_rules))
    display_rule_ids = _parse_display_rule_ids(parsed.get("display_rule_ids"), matched_rules)
    return RuleMatchLLMResult(matched_rules=matched_rules, display_rule_ids=display_rule_ids)


def _build_display_filter_system_prompt() -> str:
    """
    构造前端展示规则筛选的 system 提示。
    """
    return (
        "你是平台规则展示筛选助手。从候选规则中选出与「当前这一单售后争点」直接相关的条文，供商家前端展示。\n"
        "保留：与本案瑕疵/诉求/举证要求/处理标准直接相关的规则。\n"
        "排除：泛化程序性条文（如举证责任分配原则、账号异常、运费/破损风险归属等），"
        "除非案情摘要明确涉及该程序问题。\n"
        "输出 JSON：{\"selected_rule_ids\": [\"doc_id::article_no\", ...]}，数量 1～3 条，按相关度排序。\n"
        "Few-shot：破洞+退货诉求 → 保留质量瑕疵举证与处理标准，排除「交易异常时举证责任分配」「退货运费风险归属」类泛化条。"
    )


def llm_filter_event_display_rules(
    facts: FactOutput,
    candidates: list[MatchedRule],
    *,
    max_count: int = 3,
) -> list[MatchedRule] | None:
    """
    LLM 筛选与当前售后事件紧密相关的前端展示规则；失败返回 None。
    """
    if not candidates:
        return []
    if len(candidates) <= max_count:
        return candidates[:max_count]

    payload = {
        "issue_summary": facts.issue_summary,
        "defect_type": facts.defect_type,
        "intent_tags": facts.intent_tags,
        "candidates": [
            {"rule_id": r.rule_id, "summary": (r.rule_summary or "")[:400], "relevance": r.relevance}
            for r in candidates
        ],
        "max_count": max_count,
    }
    llm_text = chat_completion(
        messages=[
            {"role": "system", "content": _build_display_filter_system_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        model_env_key=LLM_MODEL_ENV,
        temperature=0.0,
    )
    if not llm_text:
        return None
    parsed = _parse_json_object(llm_text)
    if not parsed:
        return None
    selected_ids = parsed.get("selected_rule_ids")
    if not isinstance(selected_ids, list):
        return None
    id_set = {str(item).strip() for item in selected_ids if str(item).strip()}
    if not id_set:
        return None
    filtered = [r for r in candidates if r.rule_id in id_set]
    if not filtered:
        return None
    logger.info("%s 展示规则筛选：%s -> %s", LOG_PREFIX, len(candidates), len(filtered))
    return filtered[:max_count]
