"""
纠纷分析共享批处理：Agent1 extract + Agent2 工具链。

辅助模式控制器通过本模块复用聊天抽取与 Agent2 并行批处理，避免在控制器内重复编排。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend.agents.agent1 import extract
from backend.cache.helpers import as_list
from backend.tools.agent2_tools import (
    detect_malicious_behavior,
    needs_rule_match,
    query_buyer_profile,
    run_customer_value_analysis,
    search_similar_cases,
)
from backend.tools.rule_matcher import match_rules_from_facts
from schemas import (
    BuyerProfile,
    ChatTurn,
    CustomerValueOutput,
    FactOutput,
    MaliciousDetectionInput,
    MaliciousDetectionOutput,
    RuleMatchResult,
    SimilarCase,
    StrategyInput,
)

LOG_PREFIX = "[DisputeBatch]"
logger = logging.getLogger(__name__)


def extract_chat_bundle(
    materials: dict[str, Any],
) -> tuple[str, list[str], list[ChatTurn]]:
    """
    从材料 dict 抽取 chat 相关三份输出。

    返回:
        (dispute_desc, chat_history_texts, chat_turns)
    """
    desc_parts: list[str] = []
    buyer_text = materials.get("buyer_text")
    if isinstance(buyer_text, str) and buyer_text.strip():
        desc_parts.append(buyer_text.strip())

    chat_history_texts: list[str] = []
    chat_turns: list[ChatTurn] = []
    for message in as_list(materials.get("chat_history")):
        if isinstance(message, dict):
            role = str(message.get("role") or "buyer").strip().lower()
            if role not in {"buyer", "merchant"}:
                role = "buyer"
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                text = content.strip()
                desc_parts.append(text)
                chat_history_texts.append(text)
                chat_turns.append(ChatTurn(role=role, content=text))
        elif isinstance(message, str) and message.strip():
            text = message.strip()
            desc_parts.append(text)
            chat_history_texts.append(text)
            chat_turns.append(ChatTurn(role="buyer", content=text))

    dispute_desc = " ".join(desc_parts) if desc_parts else "买家诉求待补充"
    return dispute_desc, chat_history_texts, chat_turns


def run_agent1_extract(materials: dict[str, Any]) -> FactOutput:
    """调用 Agent1 extract，产出含 rule_match_plan 的完整 FactOutput。"""
    logger.info("%s Agent1 extract 开始 order_id=%s", LOG_PREFIX, materials.get("order_id"))
    facts = extract(materials)
    logger.info(
        "%s Agent1 extract 完成 intent_tags=%s readiness=%s",
        LOG_PREFIX,
        facts.intent_tags,
        facts.decision_readiness,
    )
    return facts


def fetch_buyer_profile_and_cases(
    *,
    buyer_id: str,
    merchant_id: str,
    dispute_desc: str,
    top_k: int = 3,
) -> tuple[BuyerProfile, list[SimilarCase]]:
    """Batch0 并行：买家画像 + 相似判例。"""
    normalized_buyer_id = buyer_id.strip() or "unknown"
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_profile = executor.submit(
            query_buyer_profile,
            buyer_id=normalized_buyer_id,
            merchant_id=merchant_id,
        )
        future_cases = executor.submit(
            search_similar_cases,
            dispute_desc=dispute_desc,
            top_k=top_k,
            merchant_id=merchant_id,
        )
        return future_profile.result(), future_cases.result()


def run_agent2_tool_batch(
    *,
    facts: FactOutput,
    buyer_profile: BuyerProfile,
    similar_cases: list[SimilarCase],
    order_amount: float,
    chat_history_texts: list[str],
    chat_turns: list[ChatTurn],
) -> tuple[CustomerValueOutput, MaliciousDetectionOutput, RuleMatchResult, bool]:
    """
    Batch1：客户价值 + 恶意检测并行 → needs_rule_match 门控 → 条文匹配。

    返回:
        (customer_value, malicious_detection, rule_result, rule_match_skipped)
    """
    partial_strategy_input = StrategyInput(
        facts=facts,
        buyer_profile=buyer_profile,
        matched_rules=[],
        rule_briefs=[],
        similar_cases=similar_cases,
        order_amount=max(0.0, order_amount),
        chat_history=chat_history_texts,
        chat_turns=chat_turns,
    )
    malicious_input = MaliciousDetectionInput(
        buyer_profile=buyer_profile,
        facts=facts,
        order_amount=max(0.0, order_amount),
        chat_history=chat_history_texts,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_value = executor.submit(run_customer_value_analysis, partial_strategy_input)
        future_malicious = executor.submit(detect_malicious_behavior, malicious_input)
        customer_value = future_value.result()
        malicious_detection = future_malicious.result()

    if needs_rule_match(facts, malicious_detection, customer_value):
        rule_result = match_rules_from_facts(facts)
        logger.info("%s 规则匹配完成 matched=%s", LOG_PREFIX, len(rule_result.matched_rules))
        return customer_value, malicious_detection, rule_result, False

    logger.info("%s 简单案跳过规则匹配", LOG_PREFIX)
    return customer_value, malicious_detection, RuleMatchResult(), True
