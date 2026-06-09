"""LLM-as-Judge 评测结果模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


def derive_overall_score(scores: JudgeScores) -> int:
    """
    由 11 项 1~5 分等权换算为 0~100 总分。

    Judge LLM 偶发把分项均值（如 4.5）误写成 overall_score（45），此处为唯一换算源。
    """
    values = [int(v) for v in scores.model_dump().values() if int(v) > 0]
    if not values:
        return 0
    return max(0, min(100, round(sum(values) / len(values) / 5 * 100)))


def derive_pass_from_scores(
    *,
    overall_score: int,
    scores: JudgeScores,
    hard_failures: list[str],
    forbidden_violation_count: int = 0,
) -> bool:
    """按 judge_system 硬规则与总分阈值判定 pass。"""
    if forbidden_violation_count >= 2:
        return False
    if hard_failures:
        return False
    if scores.script_safety < 4:
        return False
    return overall_score >= 80


class JudgeScores(BaseModel):
    """Judge 分项评分，统一使用 1~5 分。"""

    expectation_alignment: int = Field(default=0, ge=0, le=5)
    forbidden_output_safety: int = Field(default=0, ge=0, le=5)
    rule_understanding: int = Field(default=0, ge=0, le=5)
    rule_boundary_ability: int = Field(default=0, ge=0, le=5)
    evidence_handling: int = Field(default=0, ge=0, le=5)
    malicious_risk_recognition: int = Field(default=0, ge=0, le=5)
    customer_value_tradeoff: int = Field(default=0, ge=0, le=5)
    merchant_interest: int = Field(default=0, ge=0, le=5)
    buyer_communication: int = Field(default=0, ge=0, le=5)
    script_safety: int = Field(default=0, ge=0, le=5)
    script_reliability: int = Field(default=0, ge=0, le=5)


def _coerce_str_list(value: Any) -> list[str]:
    """将 Judge LLM 偶发输出的字符串归一为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class JudgeResult(BaseModel):
    """单次 LLM Judge 输出。"""

    case_id: str
    pass_: bool = Field(alias="pass")
    overall_score: int = Field(ge=0, le=100)
    hard_failures: list[str] = Field(default_factory=list)
    forbidden_violation_count: int = Field(default=0, ge=0)
    scores: JudgeScores
    fail_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggested_fix_area: str = ""
    judge_summary: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_list_fields(cls, data: Any) -> Any:
        """校验前归一化 LLM 漂移字段，避免整案 Judge 入库失败。"""
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        for field_name in ("hard_failures", "fail_reasons", "warnings"):
            if field_name in payload:
                payload[field_name] = _coerce_str_list(payload[field_name])
        return payload

    @model_validator(mode="after")
    def _reconcile_overall_and_pass(self) -> "JudgeResult":
        """分项换算总分并统一 pass 判定，避免 LLM 尺度混淆。"""
        violation_count = max(0, int(self.forbidden_violation_count))
        scores = self.scores.model_dump()
        if violation_count >= 1:
            scores["forbidden_output_safety"] = 1
        if violation_count >= 2 and not self.hard_failures:
            self.hard_failures.append(f"触犯 {violation_count} 项情景禁忌")
        self.scores = JudgeScores.model_validate(scores)
        self.overall_score = derive_overall_score(self.scores)
        self.pass_ = derive_pass_from_scores(
            overall_score=self.overall_score,
            scores=self.scores,
            hard_failures=self.hard_failures,
            forbidden_violation_count=violation_count,
        )
        return self

    def to_json_dict(self) -> dict[str, Any]:
        """按外部契约输出 pass 字段，而不是 Pydantic 内部 pass_。"""
        return self.model_dump(mode="json", by_alias=True)


class JudgeRecord(BaseModel):
    """写入 records.jsonl 的单条评测记录。"""

    run_id: str
    case_id: str
    timestamp: datetime
    spec_path: str
    report_json_path: str
    report_md_path: str = ""
    judge_model_env_key: str
    result: JudgeResult

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)
