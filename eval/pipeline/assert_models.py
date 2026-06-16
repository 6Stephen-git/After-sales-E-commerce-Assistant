"""结构化硬断言结果模型：单案 pass/fail 与批次 jsonl 记录。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssertResult(BaseModel):
    """单案硬断言结果：case_id、是否通过、失败明细与检查项计数。"""

    model_config = ConfigDict(populate_by_name=True)

    case_id: str
    pass_: bool = Field(alias="pass")
    failures: list[str] = Field(default_factory=list)
    checked_count: int = Field(default=0, ge=0)

    def to_json_dict(self) -> dict[str, Any]:
        """导出 JSON 兼容字典（pass 字段使用外部别名）。"""
        return self.model_dump(mode="json", by_alias=True)


class AssertRecord(BaseModel):
    """写入 assert_records.jsonl 的单条硬断言记录。"""

    run_id: str = ""
    case_id: str
    timestamp: datetime | None = None
    spec_path: str = ""
    report_json_path: str = ""
    result: AssertResult

    def to_json_dict(self) -> dict[str, Any]:
        """序列化记录；timestamp 使用 ISO 格式字符串。"""
        payload = self.model_dump(mode="json", by_alias=True)
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp.isoformat()
        return payload
