"""
辅助模式控制器：负责串联 Agent1 -> Agent2 -> Agent3，并维护纠纷 Redis 三层缓存。
"""

from __future__ import annotations

# ---------- 标准库与类型 ----------
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from backend.agents.agent1 import extract
from backend.agents.agent2 import recommend
from backend.agents.agent3 import generate
from backend.cache import (
    clear_all_cache,
    clear_dispute_cache,
    get_cached_facts,
    get_cached_report,
    merge_materials,
    save_facts,
    save_report,
)
from backend.tools.agent2_tools import (
    detect_malicious_behavior,
    match_rules_full,
    query_buyer_profile,
    run_customer_value_analysis,
    search_similar_cases,
)
from schemas import (
    AnalysisReport,
    ChatTurn,
    MaliciousDetectionInput,
    MatchedRule,
    RuleBrief,
    ScriptInput,
    StrategyInput,
)

# ---------- 日志前缀 ----------
ASSISTED_LOG_PREFIX = "[AssistedController]"
logger = logging.getLogger(__name__)
EventEmitter = Callable[[str, dict[str, Any]], None]


# ---------- 列表工具：供 dispute_desc 与 Agent2 输入抽取复用 ----------
def _to_list(value: Any) -> list[Any]:
    """
    将任意值安全转为列表。
    """
    if isinstance(value, list):
        return value
    return []


# ---------- 判例检索输入：从材料中抽取可读纠纷描述 ----------
def _build_dispute_desc(merged_materials: dict[str, Any]) -> str:
    """
    按约定拼接 dispute_desc：buyer_text + chat_history.content。
    """
    parts: list[str] = []
    buyer_text = merged_materials.get("buyer_text")
    if isinstance(buyer_text, str) and buyer_text.strip():
        parts.append(buyer_text.strip())

    for message in _to_list(merged_materials.get("chat_history")):
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        elif isinstance(message, str) and message.strip():
            parts.append(message.strip())

    return " ".join(parts)


def _extract_chat_history_texts(merged_materials: dict[str, Any]) -> list[str]:
    """
    提取聊天文本列表，供 Agent2 语义分析使用。
    """
    return [turn.content for turn in _extract_chat_turns(merged_materials)]


def _extract_chat_turns(merged_materials: dict[str, Any]) -> list[ChatTurn]:
    """
    提取带角色的聊天轮次，供 Agent3 话术续写。
    """
    turns: list[ChatTurn] = []
    for message in _to_list(merged_materials.get("chat_history")):
        if isinstance(message, dict):
            role = str(message.get("role") or "buyer").strip().lower()
            if role not in {"buyer", "merchant"}:
                role = "buyer"
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                turns.append(ChatTurn(role=role, content=content.strip()))
        elif isinstance(message, str) and message.strip():
            turns.append(ChatTurn(role="buyer", content=message.strip()))
    return turns


# ---------- 金额等标量：容错转换，避免策略/话术链路因脏数据中断 ----------
def _safe_order_amount(raw_value: Any) -> float:
    """
    将 order_amount 转为非负浮点，转换失败时回退 0.0。
    """
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return 0.0


def _emit_event(emit_event: EventEmitter | None, event_type: str, payload: dict[str, Any]) -> None:
    """
    统一发送阶段事件；未提供回调时静默跳过。
    """
    if emit_event is None:
        return
    emit_event(event_type, payload)


def _elapsed_ms(start: float) -> int:
    """
    将 perf_counter 起点转换为已耗时毫秒。
    """
    return int((time.perf_counter() - start) * 1000)


def _collect_agent2_tool_inputs(merged_materials: dict[str, Any]) -> tuple[str, str, str]:
    """
    从合并材料中抽取 Agent2 工具调用输入。
    """
    buyer_id = str(merged_materials.get("buyer_id", "") or "")
    merchant_id = str(merged_materials.get("merchant_id", "") or "")
    dispute_desc = _build_dispute_desc(merged_materials)
    return buyer_id, merchant_id, dispute_desc


def _log_quality_baseline(normalized_dispute_id: str, report: AnalysisReport) -> None:
    """
    记录报告质量基线日志，便于联调对比。
    """
    win_rate_text = (
        "None" if report.strategy.estimated_win_rate is None else f"{report.strategy.estimated_win_rate:.3f}"
    )
    cv_channel = report.strategy.customer_value.channel if report.strategy.customer_value else "N/A"
    cv_lt_score = report.strategy.customer_value.long_term_score if report.strategy.customer_value else "N/A"
    cv_order_score = report.strategy.customer_value.order_score if report.strategy.customer_value else "N/A"
    mal_level = report.strategy.malicious_detection.risk_level if report.strategy.malicious_detection else "N/A"
    mal_score = report.strategy.malicious_detection.risk_score if report.strategy.malicious_detection else "N/A"
    logger.info(
        "%s 质量基线：%s disposition=%s win_rate=%s confidence=%.3f evidence=%s risk_count=%s "
        "cv_channel=%s cv_lt=%s cv_order=%s mal_level=%s mal_score=%s",
        ASSISTED_LOG_PREFIX,
        normalized_dispute_id,
        report.strategy.disposition,
        win_rate_text,
        report.strategy.confidence,
        report.facts.evidence_quality,
        len(report.strategy.risk_factors),
        cv_channel,
        cv_lt_score,
        cv_order_score,
        mal_level,
        mal_score,
    )


def _emit_final_report(
    *,
    emit_event: EventEmitter | None,
    normalized_dispute_id: str,
    report: AnalysisReport,
    total_start: float,
    cache_hit: bool = False,
) -> None:
    """
    发送 final_report 与 pipeline_done 事件。
    """
    total_elapsed = _elapsed_ms(total_start)
    logger.info(
        "%s 报告返回：%s total_elapsed_ms=%s cache_hit=%s",
        ASSISTED_LOG_PREFIX,
        normalized_dispute_id,
        total_elapsed,
        cache_hit,
    )
    _log_quality_baseline(normalized_dispute_id, report)
    _emit_event(
        emit_event,
        "final_report",
        {
            "dispute_id": normalized_dispute_id,
            "elapsed_ms": total_elapsed,
            "report": report.model_dump(),
            "cache_hit": cache_hit,
        },
    )
    _emit_event(
        emit_event,
        "pipeline_done",
        {"dispute_id": normalized_dispute_id, "elapsed_ms": total_elapsed, "cache_hit": cache_hit},
    )


def clear_cache(dispute_id: str | None = None) -> None:
    """
    清理辅助模式缓存，用于集成测试或手工重置。
    """
    if dispute_id is None:
        clear_all_cache()
        return
    clear_dispute_cache(dispute_id)


# ---------- 对外主流程：校验 → 合并 → 缓存短路 → Agent1/2/3 → 组装 AnalysisReport ----------
def run_with_events(
    dispute_id: str,
    new_materials: dict[str, Any],
    emit_event: EventEmitter | None = None,
) -> AnalysisReport:
    """
    运行辅助模式完整链路并返回 AnalysisReport。

    支持可选事件回调，供流式接口按阶段推送进度和部分结果。
    """
    if not isinstance(dispute_id, str) or not dispute_id.strip():
        raise ValueError(f"{ASSISTED_LOG_PREFIX} dispute_id 不能为空")
    if not isinstance(new_materials, dict):
        raise ValueError(f"{ASSISTED_LOG_PREFIX} new_materials 必须是 dict")

    normalized_dispute_id = dispute_id.strip()
    logger.info("%s 开始处理纠纷：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)
    total_start = time.perf_counter()
    _emit_event(
        emit_event,
        "pipeline_start",
        {"dispute_id": normalized_dispute_id},
    )

    if bool(new_materials.get("reset_context")):
        clear_dispute_cache(normalized_dispute_id)

    # 1) 合并本次传入与历史缓存，得到 Agent1 所需的完整 materials
    merge_start = time.perf_counter()
    try:
        merged_materials = merge_materials(normalized_dispute_id, new_materials)
        merge_elapsed = _elapsed_ms(merge_start)
        logger.info(
            "%s 材料合并完成：%s stage=merge elapsed_ms=%s",
            ASSISTED_LOG_PREFIX,
            normalized_dispute_id,
            merge_elapsed,
        )
        _emit_event(
            emit_event,
            "stage_done",
            {"stage": "merge", "elapsed_ms": merge_elapsed, "dispute_id": normalized_dispute_id},
        )
    except Exception as exc:  # noqa: BLE001
        message = f"材料合并失败：{exc}"
        logger.error("%s %s", ASSISTED_LOG_PREFIX, message)
        _emit_event(
            emit_event,
            "pipeline_error",
            {"stage": "merge", "dispute_id": normalized_dispute_id, "message": message},
        )
        raise RuntimeError(f"{ASSISTED_LOG_PREFIX} {message}") from exc

    cached_report = get_cached_report(normalized_dispute_id, merged_materials)
    if cached_report is not None:
        logger.info("%s C 层短路返回：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)
        _emit_final_report(
            emit_event=emit_event,
            normalized_dispute_id=normalized_dispute_id,
            report=cached_report,
            total_start=total_start,
            cache_hit=True,
        )
        return cached_report

    buyer_id, merchant_id, dispute_desc = _collect_agent2_tool_inputs(merged_materials=merged_materials)
    order_amount = _safe_order_amount(merged_materials.get("order_amount", 0.0))
    chat_turns = _extract_chat_turns(merged_materials)
    chat_history_texts = _extract_chat_history_texts(merged_materials)
    emotion_note = merged_materials.get("emotion_note")

    # 2) Batch0：Agent1 与画像/判例并行（B 层命中则跳过 Agent1）
    _emit_event(
        emit_event,
        "stage_start",
        {"stage": "agent1", "dispute_id": normalized_dispute_id},
    )
    agent1_start = time.perf_counter()
    facts_from_cache = get_cached_facts(normalized_dispute_id, merged_materials)
    buyer_profile = None
    similar_cases = None
    try:
        if facts_from_cache is not None:
            facts = facts_from_cache
            logger.info("%s Agent1 跳过（B 层命中）：%s", ASSISTED_LOG_PREFIX, normalized_dispute_id)
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_profile = executor.submit(
                    query_buyer_profile,
                    buyer_id=buyer_id,
                    merchant_id=merchant_id,
                )
                future_cases = executor.submit(search_similar_cases, dispute_desc=dispute_desc, top_k=3)
                buyer_profile = future_profile.result()
                similar_cases = future_cases.result()
        else:
            with ThreadPoolExecutor(max_workers=3) as executor:
                future_facts = executor.submit(extract, materials=merged_materials)
                future_profile = executor.submit(
                    query_buyer_profile,
                    buyer_id=buyer_id,
                    merchant_id=merchant_id,
                )
                future_cases = executor.submit(search_similar_cases, dispute_desc=dispute_desc, top_k=3)
                facts = future_facts.result()
                buyer_profile = future_profile.result()
                similar_cases = future_cases.result()
            save_facts(normalized_dispute_id, merged_materials, facts)

        agent1_elapsed = _elapsed_ms(agent1_start)
        logger.info(
            "%s Agent1 完成：%s stage=agent1 elapsed_ms=%s cached=%s",
            ASSISTED_LOG_PREFIX,
            normalized_dispute_id,
            agent1_elapsed,
            facts_from_cache is not None,
        )
        _emit_event(
            emit_event,
            "stage_done",
            {
                "stage": "agent1",
                "elapsed_ms": agent1_elapsed,
                "dispute_id": normalized_dispute_id,
                "partial_report": {"facts": facts.model_dump()},
                "cache_hit": facts_from_cache is not None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        message = f"Agent1 执行失败：{exc}"
        logger.error("%s %s", ASSISTED_LOG_PREFIX, message)
        _emit_event(
            emit_event,
            "pipeline_error",
            {"stage": "agent1", "dispute_id": normalized_dispute_id, "message": message},
        )
        raise RuntimeError(f"{ASSISTED_LOG_PREFIX} {message}") from exc

    # 3) Batch1：规则匹配 + 客户价值 + 恶意检测并行，再策略 LLM
    _emit_event(
        emit_event,
        "stage_start",
        {"stage": "agent2_tools", "dispute_id": normalized_dispute_id},
    )
    tools_start = time.perf_counter()
    try:
        partial_strategy_input = StrategyInput(
            facts=facts,
            buyer_profile=buyer_profile,
            matched_rules=[],
            rule_briefs=[],
            similar_cases=similar_cases,
            order_amount=order_amount,
            chat_history=chat_history_texts,
            chat_turns=chat_turns,
            emotion_note=emotion_note,
        )
        malicious_input = MaliciousDetectionInput(
            buyer_profile=buyer_profile,
            facts=facts,
            order_amount=order_amount,
            chat_history=chat_history_texts,
            emotion_note=emotion_note,
        )

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_rules = executor.submit(match_rules_full, facts=facts)
            future_value = executor.submit(run_customer_value_analysis, partial_strategy_input)
            future_malicious = executor.submit(detect_malicious_behavior, malicious_input)
            rule_result = future_rules.result()
            customer_value = future_value.result()
            malicious_detection = future_malicious.result()

        matched_rules = rule_result.display_rules
        rule_briefs = rule_result.rule_briefs
        tools_elapsed = _elapsed_ms(tools_start)
        _emit_event(
            emit_event,
            "stage_done",
            {
                "stage": "agent2_tools",
                "elapsed_ms": tools_elapsed,
                "dispute_id": normalized_dispute_id,
                "parallel": True,
                "partial_report": {
                    "matched_rules": [item.model_dump() for item in matched_rules],
                },
            },
        )
        logger.info(
            "%s Agent2工具完成：%s stage=agent2_tools elapsed_ms=%s parallel=True",
            ASSISTED_LOG_PREFIX,
            normalized_dispute_id,
            tools_elapsed,
        )

        _emit_event(
            emit_event,
            "stage_start",
            {"stage": "agent2", "dispute_id": normalized_dispute_id},
        )
        agent2_start = time.perf_counter()
        strategy_input = StrategyInput(
            facts=facts,
            buyer_profile=buyer_profile,
            matched_rules=matched_rules,
            rule_briefs=rule_briefs,
            similar_cases=similar_cases,
            order_amount=order_amount,
            chat_history=chat_history_texts,
            chat_turns=chat_turns,
            emotion_note=emotion_note,
            precomputed_customer_value=customer_value,
            precomputed_malicious_detection=malicious_detection,
        )

        def emit_reasoning_delta(delta_text: str) -> None:
            """
            将 Agent2 策略增量文本透传给流式事件，供前端实时拼接展示。
            """
            if not delta_text:
                return
            _emit_event(
                emit_event,
                "stage_delta",
                {
                    "stage": "agent2",
                    "field": "reasoning",
                    "delta": delta_text,
                    "dispute_id": normalized_dispute_id,
                },
            )

        strategy_output = recommend(
            input_data=strategy_input,
            reasoning_delta_callback=emit_reasoning_delta if emit_event is not None else None,
        )
        agent2_elapsed = _elapsed_ms(agent2_start)
        logger.info(
            "%s Agent2 完成：%s stage=agent2 elapsed_ms=%s",
            ASSISTED_LOG_PREFIX,
            normalized_dispute_id,
            agent2_elapsed,
        )
        _emit_event(
            emit_event,
            "stage_done",
            {
                "stage": "agent2",
                "elapsed_ms": agent2_elapsed,
                "dispute_id": normalized_dispute_id,
                "partial_report": {"strategy": strategy_output.model_dump()},
            },
        )
    except Exception as exc:  # noqa: BLE001
        message = f"Agent2 执行失败：{exc}"
        logger.error("%s %s", ASSISTED_LOG_PREFIX, message)
        _emit_event(
            emit_event,
            "pipeline_error",
            {"stage": "agent2", "dispute_id": normalized_dispute_id, "message": message},
        )
        raise RuntimeError(f"{ASSISTED_LOG_PREFIX} {message}") from exc

    # 4) 话术生成：模板变量来自 facts + 订单字段，策略来自上一步输出
    _emit_event(
        emit_event,
        "stage_start",
        {"stage": "agent3", "dispute_id": normalized_dispute_id},
    )
    agent3_start = time.perf_counter()
    try:
        script_input = ScriptInput(
            strategy_output=strategy_output,
            facts=facts,
            order_id=str(merged_materials.get("order_id", "") or ""),
            order_amount=order_amount,
            emotion_note=emotion_note,
            chat_history=chat_turns,
        )
        scripts = generate(input_data=script_input)
        agent3_elapsed = _elapsed_ms(agent3_start)
        logger.info(
            "%s Agent3 完成：%s stage=agent3 elapsed_ms=%s",
            ASSISTED_LOG_PREFIX,
            normalized_dispute_id,
            agent3_elapsed,
        )
        _emit_event(
            emit_event,
            "stage_done",
            {
                "stage": "agent3",
                "elapsed_ms": agent3_elapsed,
                "dispute_id": normalized_dispute_id,
                "partial_report": {"scripts": scripts.model_dump()},
            },
        )
    except Exception as exc:  # noqa: BLE001
        message = f"Agent3 执行失败：{exc}"
        logger.error("%s %s", ASSISTED_LOG_PREFIX, message)
        _emit_event(
            emit_event,
            "pipeline_error",
            {"stage": "agent3", "dispute_id": normalized_dispute_id, "message": message},
        )
        raise RuntimeError(f"{ASSISTED_LOG_PREFIX} {message}") from exc

    # 5) 聚合为前端/API 使用的单对象（情绪预警由后续 Agent4 接入）
    report = AnalysisReport(
        dispute_id=normalized_dispute_id,
        facts=facts,
        strategy=strategy_output,
        scripts=scripts,
        emotion_alert=None,
        buyer_profile=buyer_profile,
        similar_cases=similar_cases[:2],
        matched_rules=matched_rules,
    )
    save_report(normalized_dispute_id, merged_materials, report)
    logger.info(
        "%s 报告组装完成：%s total_elapsed_ms=%s",
        ASSISTED_LOG_PREFIX,
        normalized_dispute_id,
        _elapsed_ms(total_start),
    )
    _emit_final_report(
        emit_event=emit_event,
        normalized_dispute_id=normalized_dispute_id,
        report=report,
        total_start=total_start,
        cache_hit=False,
    )
    return report


def run(dispute_id: str, new_materials: dict[str, Any]) -> AnalysisReport:
    """
    运行辅助模式完整链路并返回 AnalysisReport（非流式入口）。
    """
    return run_with_events(dispute_id=dispute_id, new_materials=new_materials, emit_event=None)
