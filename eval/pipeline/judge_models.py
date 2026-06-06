"""LLM-as-Judge 评测结果模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class JudgeScores(BaseModel):
    """Judge 分项评分，统一使用 1~5 分。"""

    expectation_alignment: int = Field(default=0, ge=0, le=5)
    forbidden_output_safety: int = Field(default=0, ge=0, le=5)
    rule_understanding: int = Field(default=0, ge=0, le=5)
    evidence_handling: int = Field(default=0, ge=0, le=5)
    merchant_interest: int = Field(default=0, ge=0, le=5)
    buyer_communication: int = Field(default=0, ge=0, le=5)
    script_safety: int = Field(default=0, ge=0, le=5)
    report_readability: int = Field(default=0, ge=0, le=5)


class JudgeResult(BaseModel):
    """单次 LLM Judge 输出。"""

    case_id: str
    pass_: bool = Field(alias="pass")
    overall_score: int = Field(ge=0, le=100)
    hard_failures: list[str] = Field(default_factory=list)
    scores: JudgeScores
    fail_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggested_fix_area: str = ""
    judge_summary: str = ""

    @model_validator(mode="after")
    def _hard_failures_force_fail(self) -> "JudgeResult":
        if self.hard_failures:
            self.pass_ = False
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
