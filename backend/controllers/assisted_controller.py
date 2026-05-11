"""
辅助模式控制器：负责串联 Agent1 -> Agent2 -> Agent3，并维护纠纷上下文缓存。

说明：材料缓存为进程内字典，MVP 阶段足够；多实例部署时需改为 Redis 等共享存储。
"""

from __future__ import annotations

# ---------- 标准库与类型 ----------
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any, Callable

from backend.agents.agent1 import extract
from backend.agents.agent2 import recommend
from backend.agents.agent3 import generate
from backend.tools.agent2_tools import match_rules, query_buyer_profile, search_similar_cases
from schemas import AnalysisReport, ScriptInput, StrategyInput

# ---------- 日志前缀与纠纷材料缓存（按 dispute_id） ----------
ASSISTED_LOG_PREFIX = "[AssistedController]"
logger = logging.getLogger(__name__)
_CACHE: dict[str, dict[str, Any]] = {}
EventEmitter = Callable[[str, dict[str, Any]], None]


# ---------- 材料合并：列表规范化与去重追加 ----------
def _to_list(value: Any) -> list[Any]:
    """
    将任意值安全转为列表。
    """
    if isinstance(value, list):
        return value
    return []


def _dedupe_preserve_order(items: list[Any]) -> list[Any]:
    """
    对列表按出现顺序去重，支持字典与基础类型混合元素。
    """
    deduped: list[Any] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _merge_materials(dispute_id: str, new_materials: dict[str, Any]) -> dict[str, Any]:
    """
    合并纠纷材料：首次全量写入，后续对 chat_history/image_urls 增量去重追加，其他字段覆盖。
    """
    # ---------- 显式重置：当前端声明 reset_context 时，直接覆写历史缓存 ----------
    if bool(new_materials.get("reset_context")):
        merged_materials = deepcopy(new_materials)
        _CACHE[dispute_id] = merged_materials
        return deepcopy(merged_materials)

    cached_materials = _CACHE.get(dispute_id)
    if cached_materials is None:
        merged_materials = deepcopy(new_materials)
        _CACHE[dispute_id] = merged_materials
        return deepcopy(merged_materials)

    merged_materials = deepcopy(cached_materials)
    for key, value in new_materials.items():
        if key in {"chat_history", "image_urls"}:
            old_items = _to_list(merged_materials.get(key))
            new_items = _to_list(value)
            merged_materials[key] = _dedupe_preserve_order(old_items + new_items)
        else:
            merged_materials[key] = deepcopy(value)

    _CACHE[dispute_id] = merged_materials
    return deepcopy(merged_materials)


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


# ---------- 金额等标量：容错转换，避免策略/话术链路因脏数据中断 ----------
def _safe_order_amount(raw_value: Any) -> float:
    """
    将 order_amount 转为非负浮点，转换失败时回退 0.0。
    """
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return 0.0


def _is_enabled(flag_name: str, default: bool = False) -> bool:
    """
    读取布尔开关环境变量。
    """
    raw = os.getenv(flag_name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "y"}


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


def _contains_risk_keyword(text: str) -> bool:
    """
    识别高争议关键词，用于简单任务判定。
    """
    keywords = (
        "投诉",
        "举报",
        "平台介入",
        "工商",
        "起诉",
        "假货",
        "退一赔三",
        "恶意",
        "欺诈",
    )
    return any(word in text for word in keywords)


def _build_execution_profile(merged_materials: dict[str, Any]) -> dict[str, Any]:
    """
    生成执行画像：是否走 fast_path 以及判定依据。
    """
    enable_fast_path = _is_enabled("ENABLE_FAST_PATH", default=False)
    if not enable_fast_path:
        return {"fast_path": False, "reason": "fast_path_disabled"}

    dispute_text = _build_dispute_desc(merged_materials)
    text_length = len(dispute_text)
    message_count = len(_to_list(merged_materials.get("chat_history")))
    image_count = len(_to_list(merged_materials.get("image_urls")))
    order_amount = _safe_order_amount(merged_materials.get("order_amount", 0.0))

    is_simple = (
        image_count == 0
        and message_count <= 4
        and text_length <= 160
        and order_amount <= 200
        and not _contains_risk_keyword(dispute_text)
    )
    reason = "simple_case" if is_simple else "complex_case"
    return {
        "fast_path": is_simple,
        "reason": reason,
        "message_count": message_count,
        "image_count": image_count,
        "text_length": text_length,
        "order_amount": order_amount,
    }


def _collect_agent2_tool_inputs(merged_materials: dict[str, Any]) -> tuple[str, str, str]:
    """
    从合并材料中抽取 Agent2 工具调用输入。
    """
    buyer_id = str(merged_materials.get("buyer_id", "") or "")
    merchant_id = str(merged_materials.get("merchant_id", "") or "")
    dispute_desc = _build_dispute_desc(merged_materials)
    return buyer_id, merchant_id, dispute_desc


def _run_agent2_tools_sequential(
    *,
    facts: Any,
    buyer_id: str,
    merchant_id: str,
    dispute_desc: str,
) -> tuple[Any, Any, Any]:
    """
    串行执行 Agent2 工具调用（兼容路径）。
    """
    matched_rules = match_rules(facts=facts)
    buyer_profile = query_buyer_profile(buyer_id=buyer_id, merchant_id=merchant_id)
    similar_cases = search_similar_cases(dispute_desc=dispute_desc, top_k=3)
    return matched_rules, buyer_profile, similar_cases


def _run_agent2_tools_parallel(
    *,
    facts: Any,
    buyer_id: str,
    merchant_id: str,
    dispute_desc: str,
) -> tuple[Any, Any, Any]:
    """
    并行执行 Agent2 工具调用，降低规则/画像/判例的等待时间。
    """
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_rules = executor.submit(match_rules, facts=facts)
        future_profile = executor.submit(query_buyer_profile, buyer_id=buyer_id, merchant_id=merchant_id)
        future_cases = executor.submit(search_similar_cases, dispute_desc=dispute_desc, top_k=3)
        matched_rules = future_rules.result()
        buyer_profile = future_profile.result()
        similar_cases = future_cases.result()
    return matched_rules, buyer_profile, similar_cases


def clear_cache(dispute_id: str | None = None) -> None:
    """
    清理辅助模式缓存，用于集成测试或手工重置。
    """
    if dispute_id is None:
        _CACHE.clear()
        return
    _CACHE.pop(dispute_id, None)


# ---------- 对外主流程：校验 → 合并 → Agent1/2/3 → 组装 AnalysisReport ----------
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

    # 1) 合并本次传入与历史缓存，得到 Agent1 所需的完整 materials
    merge_start = time.perf_counter()
    try:
        merged_materials = _merge_materials(normalized_dispute_id, new_materials)
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

    execution_profile = _build_execution_profile(merged_materials=merged_materials)
    _emit_event(
        emit_event,
        "execution_profile",
        {"dispute_id": normalized_dispute_id, **execution_profile},
    )
    logger.info(
        "%s 执行画像：%s fast_path=%s reason=%s message_count=%s image_count=%s text_length=%s",
        ASSISTED_LOG_PREFIX,
        normalized_dispute_id,
        execution_profile.get("fast_path"),
        execution_profile.get("reason"),
        execution_profile.get("message_count"),
        execution_profile.get("image_count"),
        execution_profile.get("text_length"),
    )

    # 2) 事实还原（仅 Tools 层对外部能力封装）
    _emit_event(
        emit_event,
        "stage_start",
        {"stage": "agent1", "dispute_id": normalized_dispute_id},
    )
    agent1_start = time.perf_counter()
    try:
        facts = extract(materials=merged_materials)
        agent1_elapsed = _elapsed_ms(agent1_start)
        logger.info(
            "%s Agent1 完成：%s stage=agent1 elapsed_ms=%s",
            ASSISTED_LOG_PREFIX,
            normalized_dispute_id,
            agent1_elapsed,
        )
        _emit_event(
            emit_event,
            "stage_done",
            {
                "stage": "agent1",
                "elapsed_ms": agent1_elapsed,
                "dispute_id": normalized_dispute_id,
                "partial_report": {"facts": facts.model_dump()},
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

    # 3) 策略参谋：工具层拉规则/画像/判例，再调用无状态 recommend
    _emit_event(
        emit_event,
        "stage_start",
        {"stage": "agent2_tools", "dispute_id": normalized_dispute_id},
    )
    tools_parallel_enabled = _is_enabled("ENABLE_AGENT2_PARALLEL_TOOLS", default=False)
    tools_start = time.perf_counter()
    try:
        buyer_id, merchant_id, dispute_desc = _collect_agent2_tool_inputs(merged_materials=merged_materials)
        if tools_parallel_enabled:
            matched_rules, buyer_profile, similar_cases = _run_agent2_tools_parallel(
                facts=facts,
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                dispute_desc=dispute_desc,
            )
        else:
            matched_rules, buyer_profile, similar_cases = _run_agent2_tools_sequential(
                facts=facts,
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                dispute_desc=dispute_desc,
            )
        tools_elapsed = _elapsed_ms(tools_start)
        _emit_event(
            emit_event,
            "stage_done",
            {
                "stage": "agent2_tools",
                "elapsed_ms": tools_elapsed,
                "dispute_id": normalized_dispute_id,
                "parallel": tools_parallel_enabled,
            },
        )
        logger.info(
            "%s Agent2工具完成：%s stage=agent2_tools elapsed_ms=%s parallel=%s",
            ASSISTED_LOG_PREFIX,
            normalized_dispute_id,
            tools_elapsed,
            tools_parallel_enabled,
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
            similar_cases=similar_cases,
            order_amount=_safe_order_amount(merged_materials.get("order_amount", 0.0)),
        )
        def emit_reasoning_delta(delta_text: str) -> None:
            """
            将 Agent2 推理增量文本透传给流式事件，供前端实时拼接展示。
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
            fast_path=bool(execution_profile.get("fast_path")),
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
            order_amount=_safe_order_amount(merged_materials.get("order_amount", 0.0)),
            emotion_note=merged_materials.get("emotion_note"),
        )
        scripts = generate(input_data=script_input, fast_path=bool(execution_profile.get("fast_path")))
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
    )
    total_elapsed = _elapsed_ms(total_start)
    logger.info(
        "%s 报告组装完成：%s total_elapsed_ms=%s",
        ASSISTED_LOG_PREFIX,
        normalized_dispute_id,
        total_elapsed,
    )
    logger.info(
        "%s 质量基线：%s strategy=%s win_rate=%.3f confidence=%.3f evidence=%s risk_count=%s",
        ASSISTED_LOG_PREFIX,
        normalized_dispute_id,
        report.strategy.strategy,
        report.strategy.estimated_win_rate,
        report.strategy.confidence,
        report.facts.evidence_quality,
        len(report.strategy.risk_factors),
    )
    _emit_event(
        emit_event,
        "final_report",
        {
            "dispute_id": normalized_dispute_id,
            "elapsed_ms": total_elapsed,
            "report": report.model_dump(),
        },
    )
    _emit_event(
        emit_event,
        "pipeline_done",
        {"dispute_id": normalized_dispute_id, "elapsed_ms": total_elapsed},
    )
    return report


def run(dispute_id: str, new_materials: dict[str, Any]) -> AnalysisReport:
    """
    运行辅助模式完整链路并返回 AnalysisReport（非流式入口）。
    """
    return run_with_events(dispute_id=dispute_id, new_materials=new_materials, emit_event=None)

