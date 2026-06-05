"""
手工全链路用例跑批：读取 scenario_gen 产出的 fixture.json，注入画像与判例 mock，走 AssistedController 并导出报告。

用法（一般由 scenario_gen 调用；也可单独重跑已生成的 fixture）:
  python tests/scenario_gen.py --input tests/scenarios/case/case3.md --run -v
  python tests/run_manual_cases.py --file tests/output/scenarios/scenario-001/fixture.json -v

  须配置 .env（LLM、MySQL 平台规则库）；无图用例依赖 facts_override / visual_preset。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

# ---------- 路径：保证可从仓库根导入 backend / schemas ----------
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("ENABLE_REDIS_CACHE", "0")
os.environ.setdefault("PYTHONUTF8", "1")

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass

from schemas import (  # noqa: E402
    AnalysisReport,
    BuyerProfile,
    FactOutput,
    MaliciousDetectionInput,
    MaliciousDetectionOutput,
    SimilarCase,
)

import backend.agents.agent1 as agent1_module  # noqa: E402
import backend.agents.agent1.fact_extractor as fact_extractor_module  # noqa: E402
import backend.controllers.assisted_controller as assisted_controller_module  # noqa: E402
import backend.tools.agent2_tools as agent2_tools_module  # noqa: E402
from backend.controllers.assisted_controller import clear_cache, run  # noqa: E402

RUNNER_LOG_PREFIX = "[ManualCases]"
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "manual_reports"

DISPOSITION_LABEL = {
    "defend": "抗辩",
    "negotiate": "协商",
    "compensate": "体面善后",
}
RESPONSE_MODE_LABEL = {
    "merchant_fault": "主动担责",
    "malicious_risk": "依据应对",
    "neutral_negotiate": "协商沟通",
}
RISK_LEVEL_LABEL = {"low": "低", "medium": "中", "high": "高"}
EVIDENCE_QUALITY_LABEL = {"high": "高", "medium": "中", "low": "低"}
MALICIOUS_SIGNAL_SOURCE_LABEL = {"hard_rule": "硬规则", "llm_semantic": "语义层"}
CHANNEL_LABEL = {
    "long_term": "长期客户优待",
    "order": "本单重点处理",
    "none": "未触发优待通道",
}

# ---------- 测试专用覆盖项：仅 run_manual_cases 跑批时注入，不改生产默认 ----------
CUSTOMER_VALUE_OVERRIDE_KEYS = {
    "order_value_score_threshold": "ORDER_VALUE_SCORE_THRESHOLD",
    "order_value_amount_only_threshold": "ORDER_VALUE_AMOUNT_ONLY_THRESHOLD",
    "channel_threshold": "LONG_TERM_VALUE_AMOUNT_THRESHOLD",
    "long_term_value_amount_threshold": "LONG_TERM_VALUE_AMOUNT_THRESHOLD",
}

MALICIOUS_CONTEXT_FIELDS = frozenset(
    {
        "order_address",
        "recent_refund_only_count",
        "return_rate_category_avg",
        "freight_insurance_used",
        "swap_flag_count",
        "related_account_count",
    }
)

MALICIOUS_HARD_RULE_FIELDS = frozenset(
    {
        "refund_only_count_threshold",
        "return_rate_multiple_threshold",
        "high_return_rate_multiple_for_insurance",
        "batch_order_purchase_threshold",
        "batch_order_dispute_rate_threshold",
        "swap_flag_threshold",
        "related_account_threshold",
    }
)

# ---------- 无图测试：仅证据强弱预设（不写 defect_type / 瑕疵叙事，由情景 facts_override 提供） ----------
EVIDENCE_PRESETS: dict[str, dict[str, Any]] = {
    "high_evidence": {
        "evidence_quality": "high",
        "confidence": 0.85,
        "missing_evidence": [],
        "red_flags": [],
    },
    "low_evidence": {
        "evidence_quality": "low",
        "confidence": 0.45,
        "missing_evidence": ["缺少清晰举证材料"],
        "red_flags": [],
    },
    "medium_evidence": {
        "evidence_quality": "medium",
        "confidence": 0.65,
        "missing_evidence": [],
        "red_flags": [],
    },
}
# 兼容旧 fixture 枚举名
EVIDENCE_PRESET_ALIASES = {
    "high_quality_defect": "high_evidence",
    "medium_neutral": "medium_evidence",
}


# ---------- 用例文件加载：去 _comment、归一化聊天角色 ----------
def _strip_comments(value: Any) -> Any:
    """递归删除以 _comment 开头的键，避免污染 materials。"""
    if isinstance(value, dict):
        return {
            key: _strip_comments(item)
            for key, item in value.items()
            if not str(key).startswith("_comment")
        }
    if isinstance(value, list):
        return [_strip_comments(item) for item in value]
    return value


def _normalize_chat_history(chat_history: list[Any]) -> list[dict[str, str]]:
    """将 user 角色归一为 buyer，供 Controller 与 Agent3 消费。"""
    normalized: list[dict[str, str]] = []
    for message in chat_history:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "buyer").strip().lower()
        if role == "user":
            role = "buyer"
        if role not in {"buyer", "merchant"}:
            role = "buyer"
        content = str(message.get("content") or "").strip()
        if content:
            normalized.append({"role": role, "content": content})
    return normalized


def _normalize_materials(raw: dict[str, Any]) -> dict[str, Any]:
    """合并材料字段默认值并归一化聊天列表。"""
    materials = dict(raw)
    if "chat_history" in materials:
        materials["chat_history"] = _normalize_chat_history(
            materials.get("chat_history") if isinstance(materials.get("chat_history"), list) else []
        )
    if "reset_context" not in materials:
        materials["reset_context"] = True
    if "materials_snapshot" not in materials:
        materials["materials_snapshot"] = True
    return materials


def _load_case_file(path: Path) -> dict[str, Any]:
    """读取 JSON 用例集并去除注释键。"""
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except OSError as exc:
        raise RuntimeError(f"无法读取用例文件：{path}，原因：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"用例文件 JSON 非法：{path}，原因：{exc}") from exc
    return _strip_comments(payload)


def _split_test_only_buyer_fields(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """从 fixture 画像中拆出仅测试用、不进生产 BuyerProfile 的字段。"""
    data = dict(raw or {})
    extras: dict[str, Any] = {}
    if "refund_only_rate" in data:
        extras["refund_only_rate"] = data.pop("refund_only_rate")
    return data, extras


def _parse_buyer_profile(raw: dict[str, Any], test_overrides: dict[str, Any] | None = None) -> BuyerProfile:
    """构造 BuyerProfile，校验必填 buyer_id（不含测试专用扩展字段）。"""
    data = dict(raw or {})
    overrides = test_overrides if isinstance(test_overrides, dict) else {}
    lifetime_value = overrides.get("customer_lifetime_value")
    if lifetime_value in (None, ""):
        lifetime_value = data.get("lifetime_value")
    if lifetime_value not in (None, ""):
        try:
            purchase_count = max(1, int(data.get("purchase_count") or 1))
            data["avg_order_value"] = max(0.0, float(lifetime_value)) / purchase_count
        except (TypeError, ValueError):
            logger.warning("%s customer_lifetime_value 无法解析：%s", RUNNER_LOG_PREFIX, lifetime_value)
    try:
        return BuyerProfile.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"buyer_profile 字段不合法：{exc}") from exc


def _parse_similar_cases(raw_list: list[Any]) -> list[SimilarCase]:
    """解析相似判例列表，跳过空项。"""
    cases: list[SimilarCase] = []
    if not isinstance(raw_list, list):
        return cases
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        try:
            cases.append(SimilarCase.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 跳过非法 similar_case：%s", RUNNER_LOG_PREFIX, exc)
    return cases


def _slugify_filename(text: str) -> str:
    """将步骤标签转为安全文件名片段。"""
    slug = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", text.strip(), flags=re.UNICODE)
    return slug.strip("_") or "step"


# ---------- 用例内画像与判例注入工具层（不 mock 规则匹配） ----------
@contextmanager
def _patch_case_tools(
    *,
    buyer_profile: BuyerProfile,
    similar_cases: list[SimilarCase],
) -> Iterator[None]:
    """同步 patch agent2_tools 与 assisted_controller 已导入的工具引用。"""

    def _mock_query_buyer_profile(buyer_id: str, merchant_id: str = "") -> BuyerProfile:
        _ = merchant_id
        normalized_id = buyer_id.strip() or buyer_profile.buyer_id
        return buyer_profile.model_copy(update={"buyer_id": normalized_id})

    def _mock_search_similar_cases(dispute_desc: str, top_k: int = 3) -> list[SimilarCase]:
        _ = dispute_desc
        if top_k <= 0:
            return []
        return similar_cases[:top_k]

    with (
        patch.object(agent2_tools_module, "query_buyer_profile", side_effect=_mock_query_buyer_profile),
        patch.object(agent2_tools_module, "search_similar_cases", side_effect=_mock_search_similar_cases),
        patch.object(assisted_controller_module, "query_buyer_profile", side_effect=_mock_query_buyer_profile),
        patch.object(assisted_controller_module, "search_similar_cases", side_effect=_mock_search_similar_cases),
    ):
        yield


def _parse_test_overrides(case: dict[str, Any]) -> dict[str, Any]:
    """解析用例内 test_overrides（仅手工跑批生效）。"""
    raw = case.get("test_overrides")
    if not isinstance(raw, dict):
        return {}
    return raw


def _resolve_facts_overlay(
    *,
    case: dict[str, Any],
    step: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """
    合并 visual_preset 与 facts_override（步骤级覆盖用例级）。
    返回 (preset_name, overlay_dict)。
    """
    test_overrides = _parse_test_overrides(case)
    preset = test_overrides.get("visual_preset")
    preset_name = str(preset).strip() if preset else None

    overlay: dict[str, Any] = {}
    case_facts = case.get("facts_override")
    if isinstance(case_facts, dict):
        overlay.update(case_facts)
    for key in ("compensation_ratio_cap", "policy_limits"):
        value = test_overrides.get(key)
        if value not in (None, "", [], {}):
            overlay.setdefault(key, value)
    if step is not None:
        step_facts = step.get("facts_override")
        if isinstance(step_facts, dict):
            overlay.update(step_facts)

    return preset_name, overlay


FACT_OUTPUT_FIELD_ALIASES = {
    "is_received": "goods_received",
}


def _normalize_facts_overlay_for_model(facts_overlay: dict[str, Any]) -> dict[str, Any]:
    """
    将测试覆盖字段规范为 FactOutput 可承载结构。

    已有 FactOutput 字段保持顶层覆盖；未知字段统一进入 attributes.rule_context，
    供规则匹配与策略层读取，避免时效、补偿上限等规则上下文被 Pydantic 静默丢弃。
    """
    known_fields = set(FactOutput.model_fields)
    normalized: dict[str, Any] = {}
    rule_context: dict[str, Any] = {}
    raw_attributes = facts_overlay.get("attributes")
    if isinstance(raw_attributes, dict):
        normalized["attributes"] = dict(raw_attributes)

    for raw_key, value in facts_overlay.items():
        key = FACT_OUTPUT_FIELD_ALIASES.get(raw_key, raw_key)
        if key == "attributes":
            continue
        if key in known_fields:
            normalized[key] = value
        elif value not in (None, "", [], {}):
            rule_context[raw_key] = value

    if rule_context:
        attributes = dict(normalized.get("attributes") or {})
        existing_context = dict(attributes.get("rule_context") or {})
        existing_context.update(rule_context)
        attributes["rule_context"] = existing_context
        normalized["attributes"] = attributes
    return normalized


def _strip_images_from_materials(materials: dict[str, Any]) -> dict[str, Any]:
    """跑批覆盖事实时清空图片字段，避免触发视觉 API。"""
    stripped = dict(materials)
    stripped["image_urls"] = []
    stripped["evidence_images"] = []
    return stripped


def _build_fact_output_with_overlay(
    materials: dict[str, Any],
    *,
    visual_preset: str | None,
    facts_overlay: dict[str, Any],
    agent1_mode: str,
    original_extract: Any,
) -> FactOutput:
    """
    按测试配置构造 FactOutput：可完全手填，或在无图 Agent1 文本结果上叠加覆盖。
    """
    overlay: dict[str, Any] = {}
    if visual_preset:
        preset_key = EVIDENCE_PRESET_ALIASES.get(str(visual_preset).strip(), str(visual_preset).strip())
        if preset_key not in EVIDENCE_PRESETS:
            allowed = sorted(set(EVIDENCE_PRESETS) | set(EVIDENCE_PRESET_ALIASES))
            raise ValueError(f"未知 visual_preset={visual_preset!r}，可选：{', '.join(allowed)}")
        overlay.update(EVIDENCE_PRESETS[preset_key])
    # 情景 facts_override 后写入，覆盖预设；瑕疵类型等以情景为准
    overlay.update(_normalize_facts_overlay_for_model(facts_overlay))

    mode = str(agent1_mode or "merge_text").strip().lower()
    if mode == "replace":
        try:
            base_facts = original_extract(_strip_images_from_materials(materials))
            if "rule_match_plan" not in overlay:
                overlay["rule_match_plan"] = base_facts.rule_match_plan.model_dump()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 无图 Agent1 规则导航提取失败，将仅使用覆盖字段：%s", RUNNER_LOG_PREFIX, exc)
        return FactOutput.model_validate(overlay)

    try:
        base_facts = original_extract(_strip_images_from_materials(materials))
        merged = base_facts.model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s 无图 Agent1 文本提取失败，将仅使用覆盖字段：%s", RUNNER_LOG_PREFIX, exc)
        merged = {}
    merged.update(overlay)
    return FactOutput.model_validate(merged)


@contextmanager
def _patch_facts_override(
    *,
    case_id: str,
    case: dict[str, Any],
    step: dict[str, Any] | None = None,
) -> Iterator[None]:
    """
    无图测试：覆盖 Agent1 extract，跳过视觉 API。
    配置来源：test_overrides.visual_preset（仅证据强弱）、facts_override（情景事实还原，必填瑕疵等）。
    """
    preset_name, facts_overlay = _resolve_facts_overlay(case=case, step=step)
    test_overrides = _parse_test_overrides(case)
    agent1_mode = str(test_overrides.get("agent1_mode") or "merge_text")

    if not preset_name and not facts_overlay:
        yield
        return

    logger.info(
        "%s 用例 %s 无图事实覆盖：visual_preset=%s facts_override_keys=%s agent1_mode=%s",
        RUNNER_LOG_PREFIX,
        case_id,
        preset_name or "—",
        list(facts_overlay.keys()),
        agent1_mode,
    )

    original_extractor = fact_extractor_module.extract

    def _extract_stub(materials: dict[str, Any]) -> FactOutput:
        return _build_fact_output_with_overlay(
            materials,
            visual_preset=preset_name,
            facts_overlay=facts_overlay,
            agent1_mode=agent1_mode,
            original_extract=original_extractor,
        )

    fact_extractor_module.extract = _extract_stub
    agent1_module.extract = _extract_stub
    assisted_controller_module.extract = _extract_stub

    try:
        yield
    finally:
        fact_extractor_module.extract = original_extractor
        agent1_module.extract = original_extractor
        assisted_controller_module.extract = original_extractor


def _merge_malicious_detection_input(
    input_data: MaliciousDetectionInput,
    malicious_context: dict[str, Any],
) -> MaliciousDetectionInput:
    """将用例 malicious_context 合并进恶意检测输入（仅测试 runner）。"""
    updates: dict[str, Any] = {}
    for field in MALICIOUS_CONTEXT_FIELDS:
        if field not in malicious_context:
            continue
        value = malicious_context[field]
        if value is None or value == "":
            continue
        updates[field] = value
    if not updates:
        return input_data
    return input_data.model_copy(update=updates)


def _detect_malicious_with_test_overrides(
    input_data: MaliciousDetectionInput,
    *,
    malicious_context: dict[str, Any],
    malicious_hard_rules: dict[str, Any],
) -> MaliciousDetectionOutput:
    """复刻 detect_malicious_behavior，支持用例级上下文与硬规则阈值覆盖。"""
    merged_input = _merge_malicious_detection_input(input_data, malicious_context)
    hard_kwargs = {
        key: malicious_hard_rules[key]
        for key in MALICIOUS_HARD_RULE_FIELDS
        if key in malicious_hard_rules
    }
    hard_signals = agent2_tools_module._run_hard_rules(merged_input, **hard_kwargs)
    semantic_signals = agent2_tools_module._run_llm_semantic(
        input_data=merged_input,
        hard_signals=hard_signals,
    )
    all_signals = hard_signals + semantic_signals
    risk_total_score = min(agent2_tools_module.RISK_SCORE_CAP, sum(signal.score for signal in all_signals))
    risk_level = agent2_tools_module._resolve_risk_level(risk_total_score, all_signals)
    hard_rule_summary = agent2_tools_module._build_hard_rule_summary(hard_signals)
    malicious_risk_hints = agent2_tools_module._format_malicious_risk_hints(all_signals)
    disposition_advice = agent2_tools_module._disposition_advice_from_level(risk_level)
    return MaliciousDetectionOutput(
        risk_score=risk_total_score,
        risk_level=risk_level,
        triggered_signals=all_signals,
        hard_rule_summary=hard_rule_summary,
        malicious_risk_hints=malicious_risk_hints,
        disposition_advice=disposition_advice,
    )


@contextmanager
def _patch_test_overrides(
    *,
    case_id: str,
    test_overrides: dict[str, Any],
    malicious_context: dict[str, Any] | None,
) -> Iterator[None]:
    """
    临时覆盖客户价值阈值、恶意检测硬规则参数，并注入 malicious_context。
    仅在本 context 存活期间生效，跑批结束即恢复。
    """
    cv_raw = test_overrides.get("customer_value")
    cv_config = dict(cv_raw) if isinstance(cv_raw, dict) else {}
    for key in ("channel_threshold", "order_value_score_threshold", "order_value_amount_only_threshold"):
        if key in test_overrides and key not in cv_config:
            cv_config[key] = test_overrides[key]
    hard_raw = test_overrides.get("malicious_hard_rules")
    hard_config = hard_raw if isinstance(hard_raw, dict) else {}
    mc_config = malicious_context if isinstance(malicious_context, dict) else {}

    has_cv = any(key in cv_config for key in CUSTOMER_VALUE_OVERRIDE_KEYS)
    has_hard = any(key in hard_config for key in MALICIOUS_HARD_RULE_FIELDS)
    has_mc = any(
        field in mc_config and mc_config[field] not in (None, "", 0, False)
        for field in MALICIOUS_CONTEXT_FIELDS
    )
    if not has_cv and not has_hard and not has_mc:
        yield
        return

    saved_module_attrs: dict[str, Any] = {}
    for json_key, module_attr in CUSTOMER_VALUE_OVERRIDE_KEYS.items():
        if json_key not in cv_config:
            continue
        saved_module_attrs[module_attr] = getattr(agent2_tools_module, module_attr)
        setattr(agent2_tools_module, module_attr, cv_config[json_key])

    if has_cv or has_hard or has_mc:
        logger.info(
            "%s 用例 %s 应用 test_overrides：customer_value=%s malicious_hard_rules=%s malicious_context=%s",
            RUNNER_LOG_PREFIX,
            case_id,
            list(cv_config.keys()),
            list(hard_config.keys()),
            [field for field in MALICIOUS_CONTEXT_FIELDS if field in mc_config],
        )

    original_detect_tools = agent2_tools_module.detect_malicious_behavior
    original_detect_ctrl = assisted_controller_module.detect_malicious_behavior

    def _detect_wrapper(input_data: MaliciousDetectionInput) -> MaliciousDetectionOutput:
        return _detect_malicious_with_test_overrides(
            input_data,
            malicious_context=mc_config,
            malicious_hard_rules=hard_config,
        )

    agent2_tools_module.detect_malicious_behavior = _detect_wrapper
    assisted_controller_module.detect_malicious_behavior = _detect_wrapper

    try:
        yield
    finally:
        agent2_tools_module.detect_malicious_behavior = original_detect_tools
        assisted_controller_module.detect_malicious_behavior = original_detect_ctrl
        for module_attr, previous in saved_module_attrs.items():
            setattr(agent2_tools_module, module_attr, previous)


# ---------- 报告渲染：对齐 StrategyCard.vue 分区 ----------
def _format_percent(value: float | None) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _format_red_flag_item(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    for sep in ("：", ":"):
        if sep in text:
            return text
    return text


def _render_markdown(
    *,
    report: AnalysisReport,
    case_title: str,
    step_label: str | None,
    test_buyer_extras: dict[str, Any] | None = None,
) -> str:
    """生成与前端侧栏结构一致的中文 Markdown 报告（测例画像扩展字段仅出现在本报告）。"""
    strategy = report.strategy
    facts = report.facts
    scripts = report.scripts
    lines: list[str] = []

    heading = f"# {case_title}"
    if step_label:
        heading += f" — {step_label}"
    lines.append(heading)
    lines.append("")
    lines.append(f"- 纠纷编号：`{report.dispute_id}`")
    lines.append("")

    lines.append("## 核心结论区")
    lines.append("")
    lines.append(f"**客户意图分析**：{strategy.customer_intent_analysis or '—'}")
    lines.append("")
    disp_label = DISPOSITION_LABEL.get(strategy.disposition, strategy.disposition or "—")
    lines.append(f"**策略方向**（{disp_label}）：")
    lines.append("")
    lines.append(strategy.strategy_direction_summary or "—")
    lines.append("")
    lines.append(f"**推理理由**：{strategy.strategy_direction_rationale or '—'}")
    lines.append("")
    if strategy.disposition == "defend" and strategy.estimated_win_rate is not None:
        win_pct = round(float(strategy.estimated_win_rate) * 100)
        lines.append(f"**预估胜率**：{win_pct}%")
    else:
        lines.append("**预估胜率**：—")
    lines.append("")
    conf_pct = round(float(strategy.confidence or 0) * 100)
    lines.append(f"**策略置信度**：{conf_pct}%")
    lines.append("")

    lines.append("## 关键依据区")
    lines.append("")
    lines.append("### 疑点列表")
    red_flags = [_format_red_flag_item(item) for item in (facts.red_flags or [])]
    red_flags = [item for item in red_flags if item]
    if red_flags:
        for item in red_flags:
            lines.append(f"- {item}")
    else:
        lines.append("暂无疑点")
    lines.append("")

    lines.append("### 平台规则依据")
    basis = strategy.platform_rule_basis or []
    if basis:
        for idx, line in enumerate(basis, start=1):
            lines.append(f"{idx}. {line}")
    elif report.matched_rules:
        for idx, rule in enumerate(report.matched_rules[:5], start=1):
            lines.append(f"{idx}. {rule.rule_summary}")
    else:
        lines.append("暂无规则命中")
    lines.append("")

    lines.append("### 恶意风险提示")
    md = strategy.malicious_detection
    if md:
        lines.append(f"- 恶意风险等级：{RISK_LEVEL_LABEL.get(md.risk_level, md.risk_level)}")
        lines.append(f"- 风险评分：{md.risk_score}")
        hints = (md.malicious_risk_hints or md.hard_rule_summary or "").strip() or "—"
        lines.append(f"- 聚合说明：{hints}")
        lines.append(f"- 处置建议：{md.disposition_advice or '—'}")
        for sig in md.triggered_signals or []:
            src = MALICIOUS_SIGNAL_SOURCE_LABEL.get(sig.source, sig.source)
            lines.append(
                f"- 信号：{sig.signal_type} — {sig.description}（{sig.score} 分，{src}）"
            )
    else:
        lines.append("暂无恶意风险信号")
    lines.append("")

    lines.append("### 客户价值提示")
    cv = strategy.customer_value
    if cv:
        lines.append(f"- 长期价值分：{cv.long_term_score}")
        lines.append(f"- 本单价值分：{cv.order_score}")
        lines.append(f"- 触发通道：{CHANNEL_LABEL.get(cv.channel, cv.channel)}")
        if cv.compensation_uplift:
            lines.append(f"- 补偿上限建议：{cv.compensation_uplift}")
        lines.append(f"- 话术温度建议：{cv.tone_suggestion or '—'}")
    else:
        lines.append("—")
    lines.append("")

    lines.append("## 话术区")
    lines.append("")
    mode_label = RESPONSE_MODE_LABEL.get(scripts.response_mode, scripts.response_mode or "—")
    lines.append(f"**应对思想**：{mode_label}")
    lines.append("")
    lines.append("**推荐话术**：")
    lines.append("")
    lines.append(scripts.script.strip() or "—")
    lines.append("")
    if scripts.usage_tip:
        lines.append(f"**使用提示**：{scripts.usage_tip}")
        lines.append("")

    lines.append("## 参考信息区")
    lines.append("")
    bp = report.buyer_profile
    if bp:
        lines.append("### 买家画像摘要")
        lines.append(f"- 购买次数：{bp.purchase_count}")
        lines.append(f"- 退货并退款率（用例语义·映射 return_rate）：{_format_percent(bp.return_rate)}")
        extras = test_buyer_extras or {}
        if extras.get("refund_only_rate") is not None:
            try:
                ror = float(extras["refund_only_rate"])
                lines.append(f"- 仅退款率（用例专用）：{_format_percent(ror)}")
            except (TypeError, ValueError):
                pass
        lines.append(f"- 纠纷次数：{bp.dispute_count}")
        lines.append(f"- 纠纷率：{_format_percent(bp.dispute_rate)}")
        lines.append(f"- 平均客单价：{bp.avg_order_value:.2f}")
        lines.append(f"- 信誉等级：{bp.credit_level or '—'}")
        lines.append(f"- 恶意标记次数：{bp.malicious_flags}")
        lines.append(f"- 好评次数：{bp.positive_review_count}")
        lines.append("")
    sc_list = report.similar_cases or []
    if sc_list:
        lines.append("### 相似判例")
        for idx, case in enumerate(sc_list, start=1):
            sim_pct = round(float(case.similarity) * 100)
            lines.append(f"#### #{idx} {case.case_id}（相似度 {sim_pct}%）")
            lines.append(f"- 当时商家行动：{case.merchant_action}")
            lines.append(f"- 结果：{case.outcome}")
            lines.append(f"- 经验：{case.lesson}")
            lines.append("")
    if not bp and not sc_list:
        lines.append("暂无参考信息")
        lines.append("")

    lines.append("## 事实还原附录（Agent 1）")
    lines.append("")
    lines.append(f"- 诉求摘要：{facts.issue_summary or '—'}")
    tags = facts.intent_tags or []
    lines.append(f"- 诉求标签：{', '.join(tags) if tags else '—'}")
    visuals = facts.visual_observations or []
    if visuals:
        lines.append("- 视觉观察：")
        for item in visuals:
            lines.append(f"  - {item}")
    else:
        lines.append("- 视觉观察：—")
    eq = EVIDENCE_QUALITY_LABEL.get(facts.evidence_quality, facts.evidence_quality)
    lines.append(f"- 证据质量：{eq}")
    lines.append(f"- 瑕疵类型：{facts.defect_type or '—'}")
    lines.append(f"- 是否收货：{facts.goods_received if facts.goods_received is not None else '—'}")
    lines.append(f"- 物流是否正常：{facts.logistics_normal if facts.logistics_normal is not None else '—'}")
    missing = facts.missing_evidence or []
    lines.append(f"- 缺失证据：{', '.join(missing) if missing else '—'}")
    if facts.visual_defect_severity:
        lines.append(f"- 视觉严重度：{facts.visual_defect_severity}")
    if facts.visual_goods_recoverability:
        lines.append(f"- 商品可挽回性：{facts.visual_goods_recoverability}")
    if facts.uncertainty_note:
        lines.append(f"- 不确定说明：{facts.uncertainty_note}")
    lines.append("")

    lines.append("## 结构化附录")
    lines.append("")
    lines.append("完整 `AnalysisReport` JSON 见同目录 `.json` 文件。")
    lines.append("")

    return "\n".join(lines)


def _write_outputs(
    *,
    case_id: str,
    step_suffix: str,
    report: AnalysisReport,
    markdown_body: str,
) -> tuple[Path, Path]:
    """写入 Markdown 与 JSON 报告。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base_name = f"{case_id}{step_suffix}"
    md_path = OUTPUT_DIR / f"{base_name}.md"
    json_path = OUTPUT_DIR / f"{base_name}.json"
    try:
        md_path.write_text(markdown_body, encoding="utf-8")
        json_path.write_text(
            json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError(f"写入报告失败：{exc}") from exc
    return md_path, json_path


# ---------- 单条用例执行：单快照或 steps 序列 ----------
def _resolve_dispute_id(materials: dict[str, Any], fallback: str) -> str:
    dispute_id = str(materials.get("dispute_id") or "").strip()
    return dispute_id or fallback


def _run_single_materials(
    *,
    materials: dict[str, Any],
    buyer_profile: BuyerProfile,
    similar_cases: list[SimilarCase],
    case_id: str,
    case: dict[str, Any],
    test_overrides: dict[str, Any],
    malicious_context: dict[str, Any] | None,
    step: dict[str, Any] | None = None,
) -> AnalysisReport:
    """对一份 materials 调用 Controller.run（含真实条文匹配）。"""
    normalized = _normalize_materials(materials)
    dispute_id = _resolve_dispute_id(normalized, buyer_profile.buyer_id)

    tool_ctx = _patch_case_tools(buyer_profile=buyer_profile, similar_cases=similar_cases)
    override_ctx = _patch_test_overrides(
        case_id=case_id,
        test_overrides=test_overrides,
        malicious_context=malicious_context,
    )
    facts_ctx = _patch_facts_override(case_id=case_id, case=case, step=step)
    with tool_ctx, override_ctx, facts_ctx:
        return run(dispute_id=dispute_id, new_materials=normalized)


def _run_case(
    case: dict[str, Any],
) -> list[tuple[Path, Path]]:
    """执行一条用例，返回生成的报告路径列表。"""
    meta = case.get("meta") or {}
    case_id = str(meta.get("case_id") or "CASE-UNKNOWN")
    title = str(meta.get("title") or case_id)

    test_overrides = _parse_test_overrides(case)
    buyer_raw, test_buyer_extras = _split_test_only_buyer_fields(case.get("buyer_profile") or {})
    buyer_profile = _parse_buyer_profile(buyer_raw, test_overrides)
    similar_cases = _parse_similar_cases(case.get("similar_cases") or [])
    malicious_context = case.get("malicious_context")
    if not isinstance(malicious_context, dict):
        malicious_context = None

    clear_cache()
    written: list[tuple[Path, Path]] = []

    steps = case.get("steps")
    if isinstance(steps, list) and steps:
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            label = str(step.get("label") or f"步骤{index}")
            step_materials = step.get("materials")
            if not isinstance(step_materials, dict):
                logger.warning("%s 用例 %s 步骤 %s 缺少 materials，已跳过", RUNNER_LOG_PREFIX, case_id, label)
                continue
            merged_materials = {**step_materials}
            if "buyer_id" not in merged_materials:
                merged_materials["buyer_id"] = buyer_profile.buyer_id
            if "merchant_id" not in merged_materials and case.get("materials"):
                parent = case.get("materials")
                if isinstance(parent, dict) and parent.get("merchant_id"):
                    merged_materials.setdefault("merchant_id", parent["merchant_id"])

            logger.info("%s 开始执行 %s / %s", RUNNER_LOG_PREFIX, case_id, label)
            report = _run_single_materials(
                materials=merged_materials,
                buyer_profile=buyer_profile,
                similar_cases=similar_cases,
                case_id=case_id,
                case=case,
                test_overrides=test_overrides,
                malicious_context=malicious_context,
                step=step,
            )
            step_suffix = f"__step{index:02d}_{_slugify_filename(label)}"
            md_body = _render_markdown(
                report=report,
                case_title=title,
                step_label=label,
                test_buyer_extras=test_buyer_extras,
            )
            written.append(
                _write_outputs(case_id=case_id, step_suffix=step_suffix, report=report, markdown_body=md_body)
            )
        return written

    materials = case.get("materials")
    if not isinstance(materials, dict):
        raise ValueError(f"用例 {case_id} 须包含 materials 或 steps")

    merged_materials = dict(materials)
    merged_materials.setdefault("buyer_id", buyer_profile.buyer_id)

    logger.info("%s 开始执行 %s（单快照）", RUNNER_LOG_PREFIX, case_id)
    report = _run_single_materials(
        materials=merged_materials,
        buyer_profile=buyer_profile,
        similar_cases=similar_cases,
        case_id=case_id,
        case=case,
        test_overrides=test_overrides,
        malicious_context=malicious_context,
        step=None,
    )
    md_body = _render_markdown(
        report=report,
        case_title=title,
        step_label=None,
        test_buyer_extras=test_buyer_extras,
    )
    written.append(_write_outputs(case_id=case_id, step_suffix="", report=report, markdown_body=md_body))
    return written


# ---------- 对外封装：供外部编排脚本 import 调用 ----------
def run_cases_file(
    case_file: str | Path,
    *,
    case_id: str = "",
) -> list[tuple[Path, Path]]:
    """
    读取 manual_cases JSON 并跑批，返回 (md_path, json_path) 列表。
    """
    case_path = Path(case_file)
    payload = _load_case_file(case_path)
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{RUNNER_LOG_PREFIX} 用例文件缺少非空 cases 数组：{case_path}")

    filter_id = case_id.strip()
    selected = cases
    if filter_id:
        selected = [
            item
            for item in cases
            if isinstance(item, dict) and str((item.get("meta") or {}).get("case_id")) == filter_id
        ]
        if not selected:
            raise ValueError(f"{RUNNER_LOG_PREFIX} 未找到 case_id={filter_id}")

    all_written: list[tuple[Path, Path]] = []
    for case in selected:
        if not isinstance(case, dict):
            continue
        paths = _run_case(case)
        all_written.extend(paths)
    return all_written


# ---------- CLI 入口 ----------
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="手工全链路测试用例跑批")
    parser.add_argument(
        "--file",
        type=str,
        required=True,
        help="用例 JSON 路径（通常为 scenario_gen 输出的 fixture.json）",
    )
    parser.add_argument(
        "--case-id",
        type=str,
        default="",
        help="仅运行指定 meta.case_id",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出 INFO 日志",
    )
    return parser


def main() -> int:
    """CLI 主入口。"""
    parser = _build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    case_path = Path(args.file)
    if not case_path.is_file():
        print(f"用例文件不存在：{case_path}", file=sys.stderr)
        return 1

    try:
        payload = _load_case_file(case_path)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        print("用例文件缺少非空 cases 数组", file=sys.stderr)
        return 1

    filter_id = args.case_id.strip()
    selected = cases
    if filter_id:
        selected = [
            item
            for item in cases
            if isinstance(item, dict) and str((item.get("meta") or {}).get("case_id")) == filter_id
        ]
        if not selected:
            print(f"未找到 case_id={filter_id}", file=sys.stderr)
            return 1

    try:
        all_written = run_cases_file(case_path, case_id=filter_id)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    print(f"完成 {len(selected) if not filter_id else len(all_written)} 条用例，生成 {len(all_written)} 份报告：")
    for md_path, json_path in all_written:
        print(f"  - {md_path}")
        print(f"  - {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
