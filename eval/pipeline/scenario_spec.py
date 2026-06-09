"""
情景生成：scenario_spec Pydantic 模型与校验。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ExpectationBlock(BaseModel):
    """商家期望策略倾向与禁忌。"""

    intent_summary: str = Field(default="")
    acceptable_dispositions: list[str] = Field(default_factory=list)
    forbidden_outputs: list[str] = Field(default_factory=list)


class HumanReviewBlock(BaseModel):
    """供商家审阅：确认生成器是否理解原意。"""

    scenario_restated: str = Field(default="")
    fixture_focus: str = Field(default="")
    checks_before_run: list[str] = Field(default_factory=list)


class Taxonomy(BaseModel):
    """情景分类标签。"""

    primary_axis: Literal["rule", "malicious", "value", "precedent", "conflict"] = "conflict"
    evidence_level: Literal["high", "medium", "low"] = "medium"
    buyer_risk: Literal["low", "medium", "high"] = "medium"
    order_amount_band: Literal["low", "medium", "high"] = "medium"
    complexity: Literal["simple", "conflict"] = "simple"


class ScenarioMeta(BaseModel):
    """用例元信息。"""

    case_id: str
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    source_key: str = Field(default="", description="源情景文件名 stem，用于输出目录与报告隔离")


class ScenarioLogisticsFacts(BaseModel):
    """事实证据栏：物流与签收（人编，对齐 FactOutput / materials）。"""

    goods_received: bool | None = Field(default=None, description="是否已签收/收到货")
    logistics_normal: bool | None = Field(default=None, description="物流是否正常")
    time_since_delivery_hours: float | None = Field(default=None, description="签收后至申请售后的小时数")
    note: str = Field(default="", description="物流补充说明")


class ScenarioEvidenceFacts(BaseModel):
    """
    事实证据栏：模拟有图/视频时由人编写的事实还原（不传 image_urls）。

    跑批时合并为 facts_override，并驱动客户价值视觉分维。
    """

    issue_summary: str = Field(default="", description="诉求与争议焦点一句话")
    dispute_issue_type: str = Field(default="", description="争议问题类型：质量、物流、服务承诺、规则边界、证据疑点或恶意风险等")
    defect_type: str = Field(default="", description="兼容旧字段；新情景请优先使用 dispute_issue_type")
    evidence_quality: str = Field(default="medium", description="high/medium/low 或 高/中/低")
    visual_observations: list[str] = Field(
        default_factory=list,
        description="图/视频解析要点（自然语言短句列表）",
    )
    visual_defect_severity: str | None = Field(
        default=None,
        description="minor/moderate/severe；有图还原时建议填写",
    )
    visual_goods_recoverability: str | None = Field(
        default=None,
        description="resalable/repairable/unrecoverable",
    )
    logistics: ScenarioLogisticsFacts = Field(default_factory=ScenarioLogisticsFacts)
    missing_evidence: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    media_present: bool = Field(
        default=False,
        description="true=模拟买家已上传图/视频；materials 仍 image_urls=[]",
    )

    def resolved_issue_type(self) -> str:
        """返回用于现有 FactOutput.defect_type 的兼容问题类型。"""
        return (self.dispute_issue_type or self.defect_type or "").strip()


class ScenarioSpec(BaseModel):
    """一段 NL 情景 → 结构化中间态。"""

    meta: ScenarioMeta
    scenario_narrative: str = ""
    human_review: HumanReviewBlock = Field(default_factory=HumanReviewBlock)
    expectation: ExpectationBlock = Field(default_factory=ExpectationBlock)
    taxonomy: Taxonomy = Field(default_factory=Taxonomy)
    materials: dict[str, Any] = Field(default_factory=dict)
    buyer_profile: dict[str, Any] = Field(default_factory=dict)
    similar_cases: list[dict[str, Any]] = Field(default_factory=list)
    test_overrides: dict[str, Any] = Field(default_factory=dict)
    evidence_facts: ScenarioEvidenceFacts = Field(default_factory=ScenarioEvidenceFacts)
    facts_override: dict[str, Any] = Field(
        default_factory=dict,
        description="兼容扩展字段；结构化事实请优先写 evidence_facts",
    )
    malicious_context: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator(
        "materials",
        "buyer_profile",
        "test_overrides",
        "facts_override",
        "malicious_context",
        mode="before",
    )
    @classmethod
    def _coerce_dict(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @field_validator("evidence_facts", mode="before")
    @classmethod
    def _coerce_evidence_facts(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value

    @field_validator("steps", "similar_cases", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []


def validate_spec_dict(payload: dict[str, Any]) -> ScenarioSpec:
    """校验并返回 ScenarioSpec。"""
    return ScenarioSpec.model_validate(payload)
