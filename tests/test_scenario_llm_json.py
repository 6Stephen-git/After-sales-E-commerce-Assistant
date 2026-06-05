"""scenario_llm_utils JSON 解析单测。"""

from __future__ import annotations

import pytest

from scenario_llm_utils import extract_json_object


def test_extract_plain_json_object() -> None:
    text = '{"meta": {"case_id": "A"}}'
    obj = extract_json_object(text)

    assert obj["meta"]["case_id"] == "A"


def test_extract_fenced_json_object() -> None:
    inner = '{"meta": {"case_id": "B"}, "value": 1}'
    text = f"说明文字\n```json\n{inner}\n```\n谢谢"
    obj = extract_json_object(text)

    assert obj["meta"]["case_id"] == "B"


def test_extract_fails_on_invalid_json() -> None:
    broken = '{"meta": {"case_id": "A"},}'
    with pytest.raises(ValueError, match="JSON 解析失败"):
        extract_json_object(broken)
