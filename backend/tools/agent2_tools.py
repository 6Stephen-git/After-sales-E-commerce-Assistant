"""
Agent 2 工具集：规则匹配、买家画像查询、相似判例检索。

约束：文件路径从环境变量读取；异常时按 Tools.md 约定记录日志或抛出由 Controller 捕获。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from schemas import BuyerProfile, FactOutput, MatchedRule, SimilarCase  # noqa: E402


logger = logging.getLogger(__name__)
AGENT2_LOG_PREFIX = "[Agent2]"


# ---------- 规则库：路径解析、单条件判定（供 match_rules 使用） ----------
def _resolve_rules_path() -> Path:
    """
    解析 dispute_rules.json 所在路径。

    优先读取环境变量 RULES_PATH；未设置则使用项目根下 data/dispute_rules.json。

    返回:
        规则文件的 Path 对象（未必已存在文件）。
    """
    env_path = os.getenv("RULES_PATH")
    if env_path:
        return Path(env_path)
    return ROOT_DIR / "data" / "dispute_rules.json"


def _is_condition_matched(field_value: Any, expected_value: Any, field_name: str) -> bool:
    """
    判断单条规则条件是否满足。

    支持：键名以 `_gte` 结尾时表示数值大于等于比较；expected 为 list 时表示
    field_value 也须为 list 且包含所有 expected 元素；否则做相等比较。
    JSON 中 null 与 Python None 对齐。

    参数:
        field_value: 来自 FactOutput.model_dump() 的当前字段值。
        expected_value: 规则 JSON 中的期望值。
        field_name: 条件键名，可能带 _gte 后缀。

    返回:
        是否匹配。
    """
    if field_name.endswith("_gte"):
        if field_value is None:
            return False
        return float(field_value) >= float(expected_value)

    if isinstance(expected_value, list):
        if not isinstance(field_value, list):
            return False
        return all(item in field_value for item in expected_value)

    return field_value == expected_value


# ---------- 对外工具：规则 JSON 命中列表 ----------
def match_rules(facts: FactOutput) -> List[MatchedRule]:
    """
    从本地规则库 JSON 中筛选条件全部满足的规则，并转为 MatchedRule 列表。

    文件不存在或 JSON 解析失败时记录 error 日志并返回空列表，不向调用方抛异常。

    参数:
        facts: Agent1 输出，字段名须与 rules 中 conditions 键一致。

    返回:
        命中规则的 MatchedRule 列表，可能为空。
    """
    rules_path = _resolve_rules_path()
    logger.info("%s 开始匹配规则，rules_path=%s", AGENT2_LOG_PREFIX, rules_path)

    if not rules_path.is_file():
        logger.error("%s 规则文件不存在：%s", AGENT2_LOG_PREFIX, rules_path)
        return []

    try:
        with rules_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 读取规则文件失败：%s", AGENT2_LOG_PREFIX, exc)
        return []

    matched: List[MatchedRule] = []
    facts_dict = facts.model_dump()
    for rule in payload.get("rules", []):
        conditions: Dict[str, Any] = rule.get("conditions", {})
        is_match = True

        for condition_key, expected in conditions.items():
            source_key = condition_key[:-4] if condition_key.endswith("_gte") else condition_key
            actual = facts_dict.get(source_key)
            if not _is_condition_matched(actual, expected, condition_key):
                is_match = False
                break

        if is_match:
            suggestion = str(rule.get("outcome_suggestion", "")).strip().lower()
            condition_result = f"规则条件全部满足；建议策略:{suggestion}"
            matched.append(
                MatchedRule(
                    rule_id=str(rule.get("rule_id", "")),
                    rule_summary=str(rule.get("rule_summary", "")),
                    condition_result=condition_result,
                )
            )

    logger.info("%s 规则匹配完成，命中数量=%s", AGENT2_LOG_PREFIX, len(matched))
    return matched


# ---------- 对外工具：买家画像（当前内存 Mock，可替换为 MySQL） ----------
def query_buyer_profile(buyer_id: str) -> BuyerProfile:
    """
    按买家脱敏 ID 查询画像（当前为内存 Mock，后续可换 MySQL）。

    成功返回 BuyerProfile；内部异常包装为 RuntimeError，供 Controller 捕获。

    参数:
        buyer_id: 买家标识，如测试用 buyer_high_risk、buyer_loyal。

    返回:
        BuyerProfile 实例。

    异常:
        RuntimeError: 查询逻辑异常时抛出，错误信息为中文。
    """
    logger.info("%s 开始查询买家画像，buyer_id=%s", AGENT2_LOG_PREFIX, buyer_id)

    try:
        mock_profiles = {
            "buyer_high_risk": BuyerProfile(
                buyer_id=buyer_id,
                purchase_count=2,
                dispute_count=3,
                dispute_rate=0.6,
                avg_order_value=79.0,
                return_rate=0.5,
                malicious_flags=2,
                credit_level="low",
            ),
            "buyer_loyal": BuyerProfile(
                buyer_id=buyer_id,
                purchase_count=18,
                dispute_count=1,
                dispute_rate=0.06,
                avg_order_value=135.0,
                return_rate=0.08,
                malicious_flags=0,
                credit_level="high",
            ),
        }

        default_profile = BuyerProfile(
            buyer_id=buyer_id,
            purchase_count=5,
            dispute_count=1,
            dispute_rate=0.2,
            avg_order_value=99.0,
            return_rate=0.15,
            malicious_flags=0,
            credit_level="medium",
        )
        profile = mock_profiles.get(buyer_id, default_profile)
        logger.info("%s 买家画像查询完成，credit_level=%s", AGENT2_LOG_PREFIX, profile.credit_level)
        return profile
    except Exception as exc:  # noqa: BLE001
        message = f"买家画像查询失败：{exc}"
        logger.error("%s %s", AGENT2_LOG_PREFIX, message)
        raise RuntimeError(message) from exc


# ---------- 对外工具：相似判例（当前关键词 Mock；后续可接向量库） ----------
def search_similar_cases(dispute_desc: str, top_k: int = 3) -> List[SimilarCase]:
    """
    按纠纷描述检索相似历史判例（当前为 Mock：固定候选池 + 关键词调相似度）。

    top_k 非法时抛出 ValueError；检索过程异常包装为 RuntimeError。

    参数:
        dispute_desc: 纠纷自然语言描述，用于关键词加权。
        top_k: 返回条数上限，须为正整数。

    返回:
        SimilarCase 列表，按 similarity 降序截断至 top_k 条。

    异常:
        ValueError: top_k <= 0。
        RuntimeError: 检索逻辑异常。
    """
    logger.info("%s 开始检索相似判例，top_k=%s", AGENT2_LOG_PREFIX, top_k)

    if top_k <= 0:
        raise ValueError("top_k 必须大于 0")

    try:
        text = dispute_desc.strip().lower()
        candidates: List[SimilarCase] = [
            SimilarCase(
                case_id="C001",
                similarity=0.76 if "色差" in text else 0.45,
                merchant_action="协商部分退款",
                outcome="和解",
                lesson="色差类争议优先协商，减少升级",
            ),
            SimilarCase(
                case_id="C002",
                similarity=0.82 if "破洞" in text else 0.42,
                merchant_action="提交平台复核并抗辩",
                outcome="支持商家",
                lesson="证据不足时及时要求补充，抗辩成功率更高",
            ),
            SimilarCase(
                case_id="C003",
                similarity=0.79 if "物流" in text else 0.4,
                merchant_action="快速退款并补偿券",
                outcome="支持买家",
                lesson="物流异常应优先止损，降低差评风险",
            ),
            SimilarCase(
                case_id="C004",
                similarity=0.67 if "吊牌" in text else 0.38,
                merchant_action="提交吊牌和出库质检记录",
                outcome="支持商家",
                lesson="完整证据链能明显提高平台采信",
            ),
        ]
        ranked = sorted(candidates, key=lambda item: item.similarity, reverse=True)
        return ranked[:top_k]
    except Exception as exc:  # noqa: BLE001
        message = f"相似判例检索失败：{exc}"
        logger.error("%s %s", AGENT2_LOG_PREFIX, message)
        raise RuntimeError(message) from exc


# ---------- 可选占位：向量检索未启用时恒返回空列表 ----------
def search_similar_cases_vector(dispute_desc: str, top_k: int = 3) -> List[SimilarCase]:
    """
    向量语义检索相似判例（可选能力占位）。

    MVP 未接入 ChromaDB 时固定返回空列表，并打 info 日志说明未启用。

    参数:
        dispute_desc: 预留，与后续向量查询语义对齐。
        top_k: 预留截断条数。

    返回:
        恒为空列表。
    """
    logger.info(
        "%s 向量检索未启用，返回空结果，dispute_desc=%s top_k=%s",
        AGENT2_LOG_PREFIX,
        dispute_desc,
        top_k,
    )
    return []
