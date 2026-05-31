"""
规则匹配 LLM 层：在已锁定 doc/节候选集内，结构化判定适用条文。

职责：将买家案情语义映射到平台规则正文，输出 must/should 分级；替代篇内子串硬匹配主路径。
"""

from __future__ import annotations

import json
import logging
import re
import time
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

LLM_MODEL_ENV = "AGENT2_LLM_MODEL_RULE"
LLM_MODEL_FALLBACK_ENV = "AGENT2_LLM_MODEL"


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
    return "\n".join(parts) if parts else "（案情信息有限）"


def _build_article_match_system_prompt() -> str:
    """
    构建条文匹配 system prompt：判定目标、分级枚举、跨品类 few-shot。
    """
    return (
        "你是淘宝平台规则匹配专家。任务：在给定候选条文中，选出与当前纠纷事实、诉求直接相关的条款。\n"
        "要求：\n"
        "1) 只输出 JSON，不要解释性段落；\n"
        "2) matched_articles 仅包含 candidate_index（整数，从 0 起）与 relevance（must/should）；"
        "禁止输出候选集外的条号；无贴合条文时输出空数组；\n"
        "3) 买家口语与规则用语不等价时，按规则语义判断（如破洞/撕裂/勾丝可适用「破损/质量问题」条）；"
        "争点未涉及的条文类型一律排除（如划痕/花屏案不选参数不符、改装条）；\n"
        "4) matched_articles 条数由案情决定，禁止为凑数纳入无关条；"
        "品类专项与基本规则可同时命中，但每条须有明确争点关联；\n"
        "5) reason 用中文一句话说明为何适用（≤40字）；\n"
        "6) display_rule_ids 必填：从 matched_articles 的 rule_id 中挑出与本案争点直接相关的子集供前端展示，"
        "可为 0 条（无贴合）或多条（均相关）；"
        "排除泛化程序条（如举证责任分配原则、运费风险归属），除非案情明确涉及。\n"
        "示例1（服饰破损）：候选含服饰「破损/质量问题」条 + 基本规则「初步凭证」条 → 两条均输出，品类 must、基本 should；"
        "display_rule_ids 取品类质量条 + 初步凭证条。\n"
        "示例2（手机划痕）：候选含手机「商品质量问题」「参数不符」「改装」条 + 基本规则条 → "
        "matched_articles 仅输出质量问题条 must；display_rule_ids 仅含该质量问题 rule_id，"
        "参数/改装/基本规则均不输出。"
    )


def _build_article_match_user_prompt(
    facts: FactOutput,
    candidates: list[dict[str, Any]],
) -> str:
    """
    构建 user prompt：案情 + 预筛后的候选条文列表。
    """
    from backend.tools.rule_matcher import LLM_ARTICLE_EXCERPT_MAX

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
        excerpt = content[:LLM_ARTICLE_EXCERPT_MAX] + ("…" if len(content) > LLM_ARTICLE_EXCERPT_MAX else "")
        rule_id = f"{item.get('doc_id')}::{item.get('article_no')}"
        lines.append(
            f"- index={index} rule_id={rule_id} title={title} excerpt={excerpt}"
        )
    lines.extend(
        [
            "",
            "输出 JSON：",
            '{"matched_articles":[{"candidate_index":0,"relevance":"must","reason":"..."}],'
            '"display_rule_ids":["doc_id::article_no"]}',
        ]
    )
    return "\n".join(lines)


def _parse_display_rule_ids(raw: Any, matched_rules: list[MatchedRule]) -> list[str] | None:
    """
    解析 display_rule_ids 并校验属于本次命中池；空数组表示 LLM 判定无贴合展示条。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    if not raw:
        return []
    if not matched_rules:
        return []
    allowed = {rule.rule_id for rule in matched_rules}
    ordered: list[str] = []
    for item in raw:
        rid = str(item or "").strip()
        if rid and rid in allowed and rid not in ordered:
            ordered.append(rid)
    return ordered


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
    candidates: list[dict[str, Any]],
) -> RuleMatchLLMResult | None:
    """
    调用 LLM 在预筛候选条文中结构化选型；失败返回 None 供上层降级。
    """
    if not candidates:
        logger.warning("%s 无候选条文，跳过 LLM 匹配", LOG_PREFIX)
        return RuleMatchLLMResult(matched_rules=[], display_rule_ids=None)

    user_prompt = _build_article_match_user_prompt(facts, candidates)
    system_prompt = _build_article_match_system_prompt()
    call_start = time.perf_counter()
    llm_text = chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model_env_key=LLM_MODEL_ENV,
        fallback_model_env_key=LLM_MODEL_FALLBACK_ENV,
        temperature=0.0,
    )
    elapsed_ms = int((time.perf_counter() - call_start) * 1000)
    logger.info(
        "%s 条文 LLM 完成 elapsed_ms=%s candidates=%s prompt_chars=%s",
        LOG_PREFIX,
        elapsed_ms,
        len(candidates),
        len(user_prompt) + len(system_prompt),
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
    if "display_rule_ids" in parsed:
        display_rule_ids = _parse_display_rule_ids(parsed.get("display_rule_ids"), matched_rules)
    else:
        display_rule_ids = None
    return RuleMatchLLMResult(matched_rules=matched_rules, display_rule_ids=display_rule_ids)
