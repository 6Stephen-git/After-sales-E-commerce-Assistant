"""
Agent 5 工具集：LLM 复盘生成与判例库写入。

约束：写入时必须携带 merchant_id 与 dispute_id，用于商家数据隔离与追溯。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from backend.db.connection import get_engine
from backend.db.models import DisputeCase
from backend.tools.llm_client import chat_completion
from schemas import CaseScenario, ReviewInput, ReviewOutput


AGENT5_LOG_PREFIX = "[Agent5]"
logger = logging.getLogger(__name__)

_REVIEW_SYSTEM_PROMPT = """你是电商售后纠纷复盘分析师。
根据完整纠纷轨迹、最终结果与商家是否采纳 AI 建议，提炼可复用经验卡片。

输出严格 JSON，不要 markdown：
{
  "case_type": "纠纷类型标签，如质量争议_抗辩",
  "key_facts": "关键事实一句话",
  "merchant_action_taken": "商家实际采取的行动",
  "outcome": "胜|败|和解|升级",
  "lesson_text": "经验教训自然语言",
  "tags": ["case_type:...", "outcome:...", "strategy:...", "ai_adopted|ai_not_adopted"],
  "scenario": {
    "dispute_type": "纠纷类型/品类",
    "customer_value": "客户价值描述",
    "evidence_quality": "high|medium|low",
    "responsibility": "商责|买责|不清|混合",
    "strategy_direction": "defend|negotiate|compensate|unknown",
    "risk_level": "低|中|高",
    "order_amount": 订单金额数字或null
  }
}

要求：基于轨迹事实，不虚构；商家未采纳 AI 时 tags 应含 strategy_gap。"""


def _parse_review_json(raw_text: str) -> dict[str, Any] | None:
    """解析复盘 LLM JSON。"""
    text = (raw_text or "").strip()
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _validate_review_input(review: ReviewOutput, merchant_id: str, dispute_id: str) -> None:
    """校验保存判例所需的关键输入。"""
    if not merchant_id or not str(merchant_id).strip():
        raise ValueError("merchant_id 不能为空")
    if not dispute_id or not str(dispute_id).strip():
        raise ValueError("dispute_id 不能为空")
    if not isinstance(review, ReviewOutput):
        raise TypeError("review 必须是 ReviewOutput 类型")


def generate_review_card(review_input: ReviewInput) -> ReviewOutput | None:
    """
    调用 LLM 生成结构化复盘卡片。

    参数:
        review_input: ReviewInput。

    返回:
        ReviewOutput 或 None（LLM 失败）。
    """
    user_payload = {
        "dispute_id": review_input.dispute_id,
        "final_outcome": review_input.final_outcome,
        "outcome_note": review_input.outcome_note,
        "ai_strategy_adopted": review_input.ai_strategy_adopted,
        "full_timeline": review_input.full_timeline,
    }
    logger.info("%s 开始 LLM 复盘生成 dispute_id=%s", AGENT5_LOG_PREFIX, review_input.dispute_id)
    raw = chat_completion(
        messages=[
            {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        model_env_key="AGENT5_LLM_MODEL",
        temperature=0.4,
    )
    if not raw:
        logger.error("%s LLM 复盘生成失败", AGENT5_LOG_PREFIX)
        return None

    parsed = _parse_review_json(raw)
    if parsed is None:
        logger.error("%s LLM 复盘 JSON 解析失败", AGENT5_LOG_PREFIX)
        return None

    scenario_raw = parsed.get("scenario")
    scenario = CaseScenario()
    if isinstance(scenario_raw, dict):
        try:
            scenario = CaseScenario.model_validate(scenario_raw)
        except Exception:  # noqa: BLE001
            logger.warning("%s scenario 字段校验失败，使用默认值", AGENT5_LOG_PREFIX)

    tags = parsed.get("tags")
    if not isinstance(tags, list):
        tags = []

    try:
        output = ReviewOutput(
            case_type=str(parsed.get("case_type") or "通用纠纷"),
            key_facts=str(parsed.get("key_facts") or ""),
            merchant_action_taken=str(parsed.get("merchant_action_taken") or ""),
            outcome=str(parsed.get("outcome") or review_input.final_outcome),
            lesson_text=str(parsed.get("lesson_text") or ""),
            tags=[str(item) for item in tags if str(item).strip()],
            scenario=scenario,
        )
        logger.info("%s LLM 复盘生成成功 case_type=%s", AGENT5_LOG_PREFIX, output.case_type)
        return output
    except Exception as exc:  # noqa: BLE001
        logger.error("%s LLM 复盘结构校验失败：%s", AGENT5_LOG_PREFIX, exc)
        return None


def save_case_to_db(review: ReviewOutput, merchant_id: str, dispute_id: str) -> bool:
    """
    保存经验卡片到 MySQL 判例库。

    参数:
        review: 复盘输出。
        merchant_id: 商家标识。
        dispute_id: 纠纷编号。

    返回:
        写入成功 True，否则 False。
    """
    logger.info(
        "%s 开始写入经验卡片 merchant_id=%s dispute_id=%s",
        AGENT5_LOG_PREFIX,
        merchant_id,
        dispute_id,
    )
    try:
        _validate_review_input(review=review, merchant_id=merchant_id, dispute_id=dispute_id)
        case_summary = f"{review.key_facts}；{review.merchant_action_taken}".strip("；")
        tags_json = json.dumps(review.tags or [], ensure_ascii=False)
        scenario_json = review.scenario.model_dump_json()

        engine = get_engine()
        with Session(engine) as session:
            record = DisputeCase(
                merchant_id=merchant_id.strip(),
                dispute_id=dispute_id.strip(),
                case_type=review.case_type,
                outcome=review.outcome,
                case_summary=case_summary,
                lesson_text=review.lesson_text,
                tags=tags_json,
                scenario_json=scenario_json,
            )
            session.add(record)
            session.commit()

        logger.info(
            "%s 经验卡片写入成功 merchant_id=%s dispute_id=%s case_type=%s",
            AGENT5_LOG_PREFIX,
            merchant_id,
            dispute_id,
            review.case_type,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 经验卡片写入失败：%s", AGENT5_LOG_PREFIX, exc)
        return False
