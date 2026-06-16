"""

情景「事实证据」栏 → FactOutput 覆盖字段与 materials 物流补充。



与 schemas.FactOutput、run_manual_cases._normalize_facts_overlay_for_model 字段对齐。

"""



from __future__ import annotations



from typing import Any



from eval.pipeline.scenario_spec import ScenarioEvidenceFacts, ScenarioLogisticsFacts, ScenarioSpec



EVIDENCE_QUALITY_ZH_TO_EN = {"高": "high", "中": "medium", "低": "low", "high": "high", "medium": "medium", "low": "low"}





def normalize_evidence_quality(value: Any) -> str:

    """证据强弱中英文归一。"""

    text = str(value or "medium").strip().lower()

    if text in EVIDENCE_QUALITY_ZH_TO_EN:

        return EVIDENCE_QUALITY_ZH_TO_EN[text]

    return "medium"





def evidence_facts_is_empty(ef: ScenarioEvidenceFacts | None) -> bool:

    """未填写结构化事实证据时视为空。"""

    if ef is None:

        return True

    if ef.issue_summary.strip() or ef.resolved_issue_type() or ef.visual_observations:

        return False

    if ef.logistics.goods_received is not None or ef.logistics.logistics_normal is not None:

        return False

    if ef.logistics.time_since_delivery_hours is not None or ef.logistics.note.strip():

        return False

    if ef.visual_defect_severity or ef.visual_goods_recoverability:

        return False

    if ef.missing_evidence or ef.red_flags or ef.media_present:

        return False

    return True





def evidence_facts_to_facts_override(spec: ScenarioSpec) -> dict[str, Any]:

    """

    将 evidence_facts + 兼容 facts_override 合并为跑批用 facts_override 字典。



    优先级：facts_override 顶层已知字段 < evidence_facts 结构化字段；

    facts_override 中其余键进入 attributes.rule_context。

    """

    legacy = dict(spec.facts_override or {})

    ef = spec.evidence_facts

    if evidence_facts_is_empty(ef):

        return legacy



    assert ef is not None

    merged: dict[str, Any] = {}

    if ef.issue_summary.strip():

        merged["issue_summary"] = ef.issue_summary.strip()

    issue_type = ef.resolved_issue_type()
    if issue_type:

        merged["defect_type"] = issue_type

    merged["evidence_quality"] = normalize_evidence_quality(ef.evidence_quality)

    if ef.visual_observations:

        merged["visual_observations"] = list(ef.visual_observations)

    if ef.visual_defect_severity:

        merged["visual_defect_severity"] = str(ef.visual_defect_severity).strip().lower()

    if ef.visual_goods_recoverability:

        merged["visual_goods_recoverability"] = str(ef.visual_goods_recoverability).strip().lower()

    if ef.missing_evidence:

        merged["missing_evidence"] = list(ef.missing_evidence)

    else:

        merged["missing_evidence"] = []

    if ef.red_flags:

        merged["red_flags"] = list(ef.red_flags)



    logistics = ef.logistics or ScenarioLogisticsFacts()

    if logistics.goods_received is not None:

        merged["goods_received"] = logistics.goods_received

    if logistics.logistics_normal is not None:

        merged["logistics_normal"] = logistics.logistics_normal



    rule_context: dict[str, Any] = {}

    if logistics.time_since_delivery_hours is not None:

        rule_context["time_since_delivery_hours"] = logistics.time_since_delivery_hours

    if logistics.note.strip():

        rule_context["logistics_note"] = logistics.note.strip()

    if ef.media_present:

        rule_context["media_present"] = True

        rule_context["evidence_mode"] = "simulated_media"



    existing_attrs = legacy.pop("attributes", None)

    if isinstance(existing_attrs, dict):

        merged_attrs = dict(existing_attrs)

        ctx = dict(merged_attrs.get("rule_context") or {})

        ctx.update(rule_context)

        if ctx:

            merged_attrs["rule_context"] = ctx

        merged["attributes"] = merged_attrs

    elif rule_context:

        merged["attributes"] = {"rule_context": rule_context}



    for key, value in legacy.items():

        if key == "attributes" and isinstance(value, dict):

            base_attrs = dict(merged.get("attributes") or {})

            base_ctx = dict(base_attrs.get("rule_context") or {})

            leg_ctx = dict(value.get("rule_context") or {})

            base_ctx.update(leg_ctx)

            base_attrs.update({k: v for k, v in value.items() if k != "rule_context"})

            if base_ctx:

                base_attrs["rule_context"] = base_ctx

            merged["attributes"] = base_attrs

        elif key not in merged:

            merged[key] = value

    return merged





def enrich_materials_logistics(materials: dict[str, Any], spec: ScenarioSpec) -> dict[str, Any]:

    """从事实证据物流子结构写入 materials，与线上一致字段对齐。"""

    out = dict(materials or {})

    ef = spec.evidence_facts

    if evidence_facts_is_empty(ef) or ef is None:

        return out

    logistics = ef.logistics

    if logistics.goods_received is not None:

        out["is_received"] = logistics.goods_received

    if logistics.logistics_normal is not None:

        out["logistics_normal"] = logistics.logistics_normal

    if logistics.note.strip() and not out.get("logistics_note"):

        out["logistics_note"] = logistics.note.strip()

    return out


