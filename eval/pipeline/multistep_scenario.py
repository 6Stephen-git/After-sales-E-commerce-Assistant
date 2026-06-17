"""
三期多步情景 Markdown → scenario_spec（确定性解析，不调用 LLM）。

格式：共享 ## 背景 / ## 买家 / ## 参考 / ## 其他说明；各步 ### StepN + #### 对话记录 / 事实证据 / expected_report。
期望与禁忌仅写入 spec.steps[].expectation，不进 fixture。
"""

from __future__ import annotations

import re
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    yaml = None  # type: ignore[assignment]

_STEP_HEADER_RE = re.compile(r"^###\s+Step\s*(\d+)\s*(.*)$", re.MULTILINE)
_SUBSECTION_RE = re.compile(r"^####\s+(.+?)\s*$", re.MULTILINE)
_CHAT_LINE_RE = re.compile(r"^-\s*(买家|商家)\s*[：:]\s*(.+)$")
_FENCED_YAML_RE = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
_BULLET_RE = re.compile(r"^-\s*(.+?)\s*[：:]\s*(.+)$")


def _slug_from_source_key(source_key: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", source_key.strip()).strip("_").lower()
    return slug or "scenario"


def _case_id_from_source_key(source_key: str) -> str:
    return _slug_from_source_key(source_key).upper() or "SCENARIO-UNKNOWN"


def is_multistep_narrative(narrative: str) -> bool:
    """是否含 ### Step 段的多步情景。"""
    return bool(_STEP_HEADER_RE.search(narrative))


def _parse_yaml_block(text: str) -> dict[str, Any]:
    """解析 fenced yaml 为字典。"""
    if yaml is None:
        raise RuntimeError("解析多步情景需要 PyYAML：pip install PyYAML")
    match = _FENCED_YAML_RE.search(text)
    if not match:
        return {}
    loaded = yaml.safe_load(match.group(1))
    return dict(loaded) if isinstance(loaded, dict) else {}


def _parse_section(narrative: str, header: str) -> str:
    """提取 ## header 至下一 ## 或 ### 之间的正文。"""
    pattern = re.compile(
        rf"^{re.escape(header)}\s*\n(.*?)(?=^##\s|^###\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(narrative)
    return match.group(1).strip() if match else ""


def _parse_background(text: str) -> dict[str, Any]:
    """解析背景栏。"""
    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- 品类"):
            out["category"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("- 本单金额"):
            amount_match = re.search(r"(\d+(?:\.\d+)?)", line)
            if amount_match:
                out["order_amount"] = float(amount_match.group(1))
        elif line.startswith("- 是否已签收"):
            out["is_received"] = "是" in line
        elif line.startswith("- 平台服务标"):
            tag = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            out["service_tag"] = tag if tag not in {"无", ""} else ""
    return out


def _parse_buyer_table(text: str) -> dict[str, Any]:
    """解析买家表格为 buyer_profile 字典。"""
    profile: dict[str, Any] = {}
    for line in text.splitlines():
        row = _TABLE_ROW_RE.match(line.strip())
        if not row:
            continue
        key = row.group(1).strip()
        value_raw = row.group(2).strip()
        if key in {"字段", "---"} or key.startswith("-"):
            continue
        if key in {"purchase_count", "dispute_count", "malicious_flags", "positive_review_count"}:
            try:
                profile[key] = int(float(value_raw))
            except ValueError:
                profile[key] = value_raw
        elif key in {"dispute_rate", "return_rate", "refund_only_rate", "avg_order_value"}:
            try:
                profile[key] = float(value_raw)
            except ValueError:
                profile[key] = value_raw
        else:
            profile[key] = value_raw
    return profile


def _parse_other_notes(text: str) -> dict[str, Any]:
    """解析「其他说明」为 test_overrides 键值。"""
    overrides: dict[str, Any] = {}
    for line in text.splitlines():
        bullet = _BULLET_RE.match(line.strip())
        if bullet:
            overrides[bullet.group(1).strip()] = bullet.group(2).strip()
    return overrides


def _parse_chat_history(text: str) -> list[dict[str, str]]:
    """解析对话列表。"""
    turns: list[dict[str, str]] = []
    for line in text.splitlines():
        match = _CHAT_LINE_RE.match(line.strip())
        if not match:
            continue
        role = "buyer" if match.group(1) == "买家" else "merchant"
        turns.append({"role": role, "content": match.group(2).strip()})
    return turns


def _parse_evidence_facts(text: str) -> dict[str, Any]:
    """将事实证据段转为 facts_override 字典。"""
    facts: dict[str, Any] = {"media_present": True}
    observations: list[str] = []
    missing: list[str] = []
    rule_context: dict[str, Any] = {}

    for line in text.splitlines():
        raw = line.strip()
        if not raw.startswith("-"):
            continue
        body = raw.lstrip("- ").strip()
        if body.startswith("图/视频解析"):
            obs = body.split("：", 1)[-1].split(":", 1)[-1].strip()
            if obs:
                observations.append(obs)
        elif body.startswith("物流"):
            logistics_text = body.split("：", 1)[-1].split(":", 1)[-1].strip()
            if "签收" in logistics_text:
                facts["goods_received"] = "否" not in logistics_text.split("；")[0]
            if "物流是否正常" in logistics_text:
                normal_part = logistics_text.split("物流是否正常", 1)[-1]
                facts["logistics_normal"] = "否" not in normal_part.split("；")[0].lstrip("：:")
            hours_match = re.search(r"签收后约\s*(\d+(?:\.\d+)?)\s*小时", logistics_text)
            if hours_match:
                rule_context["time_since_delivery_hours"] = float(hours_match.group(1))
        elif body.startswith("视觉严重度"):
            severity = body.split("：", 1)[-1].split(":", 1)[-1].strip()
            severity = severity.split("；")[0].split(";")[0].strip().lower()
            if severity:
                facts["visual_defect_severity"] = severity
        elif "可挽回性" in body:
            recover_match = re.search(r"可挽回性[：:]\s*(\w+)", body)
            if recover_match:
                facts["visual_goods_recoverability"] = recover_match.group(1).strip().lower()
        elif body.startswith("缺失材料"):
            missing_text = body.split("：", 1)[-1].split(":", 1)[-1].strip()
            if missing_text in {"无", ""} or missing_text.startswith("无（"):
                missing = []
                facts["evidence_quality"] = "high"
            else:
                for part in re.split(r"[；;、]", missing_text):
                    item = part.strip().strip("（）()")
                    if item and item not in {"无"}:
                        missing.append(item)
                facts["evidence_quality"] = "medium" if missing else "high"
        elif body.startswith("证据强弱"):
            level = body.split("：", 1)[-1].split(":", 1)[-1].strip().lower()
            if level in {"high", "medium", "low", "高", "中", "低"}:
                mapping = {"高": "high", "中": "medium", "低": "low"}
                facts["evidence_quality"] = mapping.get(level, level)

    if observations:
        facts["visual_observations"] = observations
        summary = observations[0][:80]
        facts["issue_summary"] = summary
    facts["missing_evidence"] = missing
    if rule_context:
        facts.setdefault("attributes", {})["rule_context"] = rule_context
    facts.setdefault("evidence_quality", "medium" if missing else "high")
    return facts


def _split_steps(narrative: str) -> list[tuple[int, str, str]]:
    """返回 (step_num, label, body) 列表。"""
    parts = _STEP_HEADER_RE.split(narrative)
    # split: [preamble, num, label, body, num, label, body, ...]
    if len(parts) < 4:
        return []
    steps: list[tuple[int, str, str]] = []
    index = 1
    while index + 2 < len(parts):
        step_num = int(parts[index])
        label = parts[index + 1].strip() or f"步骤{step_num}"
        body = parts[index + 2].strip()
        steps.append((step_num, label, body))
        index += 3
    return steps


def _parse_judge_expectation_block(text: str) -> dict[str, Any]:
    """解析单步「期望与禁忌」（仅进 spec，不进 fixture），供 Judge 使用。"""
    intent_parts: list[str] = []
    forbidden: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw.startswith("-"):
            continue
        body = raw.lstrip("- ").strip()
        if body.startswith(("目标方向", "关键动作")):
            _, _, tail = body.partition("：")
            if not tail:
                _, _, tail = body.partition(":")
            if tail.strip():
                intent_parts.append(tail.strip())
        elif body.startswith("禁忌"):
            _, _, tail = body.partition("：")
            if not tail:
                _, _, tail = body.partition(":")
            if tail.strip():
                forbidden.append(tail.strip())
    block: dict[str, Any] = {}
    if intent_parts:
        block["intent_summary"] = "；".join(intent_parts)
    if forbidden:
        block["forbidden_outputs"] = forbidden
    return block


def _parse_step_body(body: str) -> dict[str, Any]:
    """解析单步内的子段。"""
    sections: dict[str, str] = {}
    matches = list(_SUBSECTION_RE.finditer(body))
    for idx, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        sections[name] = body[start:end].strip()
    return sections


def parse_multistep_narrative(narrative: str, *, source_key: str = "") -> dict[str, Any]:
    """
    多步情景 Markdown → scenario_spec 字典。

    参数:
        narrative: 完整 Markdown 文本。
        source_key: 源文件名 stem，用于 case_id。

    返回:
        可经 validate_spec_dict 校验的 spec 字典。
    """
    title_match = re.match(r"^#\s+(.+)$", narrative.strip(), re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "多步情景"

    background = _parse_background(_parse_section(narrative, "## 背景"))
    buyer_text = _parse_section(narrative, "## 买家")
    buyer_profile = _parse_buyer_table(buyer_text)
    reference_text = _parse_section(narrative, "## 参考").strip()
    other_notes = _parse_other_notes(_parse_section(narrative, "## 其他说明"))

    case_id = _case_id_from_source_key(source_key) if source_key else "MS-UNKNOWN"
    order_amount = float(background.get("order_amount") or 0)
    service_tag = str(background.get("service_tag") or "").strip()
    category = str(background.get("category") or "").strip()
    is_received = background.get("is_received", True)

    base_materials: dict[str, Any] = {
        "order_amount": order_amount,
        "is_received": bool(is_received),
        "platform_service_tags": [service_tag] if service_tag else [],
    }
    if category:
        base_materials["product_title"] = category

    test_overrides = dict(other_notes)
    test_overrides["agent1_mode"] = "replace"

    steps_out: list[dict[str, Any]] = []
    for step_num, label, body in _split_steps(narrative):
        sections = _parse_step_body(body)
        chat_text = sections.get("对话记录", "")
        evidence_text = sections.get("事实证据", "")
        expected_text = sections.get("expected_report", "")
        judge_text = sections.get("期望与禁忌", "")

        materials = dict(base_materials)
        materials["chat_history"] = _parse_chat_history(chat_text)
        materials["dispute_id"] = f"DISPUTE-{case_id}-S{step_num:02d}"

        facts_override = _parse_evidence_facts(evidence_text)
        if category and not facts_override.get("issue_summary"):
            facts_override["issue_summary"] = f"{category}售后争议"

        expectation: dict[str, Any] = {"expected_report": _parse_yaml_block(expected_text)}
        expectation.update(_parse_judge_expectation_block(judge_text))

        steps_out.append(
            {
                "label": label,
                "materials": materials,
                "facts_override": facts_override,
                "expectation": expectation,
            }
        )

    if not steps_out:
        raise ValueError("多步情景未解析到任何 ### Step 段")

    step_labels = "、".join(step["label"] for step in steps_out)
    return {
        "meta": {
            "case_id": case_id,
            "title": title,
            "tags": ["multistep", "phase3"],
            **({"source_key": source_key} if source_key else {}),
        },
        "scenario_narrative": narrative,
        "human_review": {
            "scenario_restated": f"{title}：共 {len(steps_out)} 步（{step_labels}），每步独立快照跑批。",
            "fixture_focus": "验证举证递进与终局可执行方案；期望仅存在于 spec，不进 fixture。",
            "checks_before_run": [
                "确认每步对话末条为买家发言",
                "确认 Step 终局步不再索要已齐材料",
                "确认 fixture 无 expected_report / 禁忌泄题",
            ],
        },
        "expectation": {
            "intent_summary": "多步硬断言：过程正确 + 终局给方案",
            "forbidden_outputs": [],
            "expected_report": {},
        },
        "taxonomy": {
            "primary_axis": "conflict",
            "evidence_level": "medium",
            "complexity": "conflict",
        },
        "materials": {},
        "buyer_profile": buyer_profile,
        "similar_cases": [],
        "test_overrides": test_overrides,
        "evidence_facts": {},
        "facts_override": {},
        "steps": steps_out,
    }
