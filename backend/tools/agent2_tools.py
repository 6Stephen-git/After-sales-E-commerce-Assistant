"""
Agent 2 工具集：规则匹配、买家画像查询、相似判例检索、恶意行为检测、客户价值完整分析。

约束：文件路径从环境变量读取；异常时按 Tools.md 约定记录日志或抛出由 Controller 捕获。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.db.connection import get_engine  # noqa: E402
from backend.db.models import BuyerProfileRecord, PlatformRule  # noqa: E402
from backend.tools.llm_client import chat_completion  # noqa: E402
from schemas import (  # noqa: E402
    BuyerProfile,
    CustomerValueInput,
    CustomerValueOutput,
    CustomerValueScoreItem,
    FactOutput,
    MaliciousDetectionInput,
    MaliciousDetectionOutput,
    MaliciousSignal,
    MatchedRule,
    SimilarCase,
    StrategyInput,
)


logger = logging.getLogger(__name__)
AGENT2_LOG_PREFIX = "[Agent2]"


# ---------- 规则库：MySQL主链、开发JSON回退、单条件判定（供 match_rules 使用） ----------
def _resolve_rules_path() -> Path:
    """
    解析开发回退用 dispute_rules.json 所在路径。

    优先读取环境变量 RULES_PATH；未设置则使用项目根下 data/dispute_rules.json。

    返回:
        规则文件的 Path 对象（未必已存在文件）。
    """
    env_path = os.getenv("RULES_PATH")
    if env_path:
        return Path(env_path)
    return ROOT_DIR / "data" / "dispute_rules.json"


def _is_local_rule_fallback_enabled() -> bool:
    """
    判断是否允许使用本地 JSON 作为开发回退规则源。
    """
    return os.getenv("ENABLE_LOCAL_RULES_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _load_rules_from_mysql() -> List[Dict[str, Any]]:
    """
    从 MySQL platform_rules 加载规则列表（生产主链）。

    兼容两种 rule_content 结构：
    1) {"rules":[...]}；
    2) 单条规则对象。
    """
    engine = get_engine()
    rules: List[Dict[str, Any]] = []
    with Session(bind=engine) as session:
        records = session.execute(select(PlatformRule)).scalars().all()
    for row in records:
        try:
            payload = json.loads(row.rule_content or "{}")
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict) and isinstance(payload.get("rules"), list):
            for item in payload["rules"]:
                if isinstance(item, dict):
                    rules.append(item)
            continue
        if isinstance(payload, dict) and payload.get("conditions"):
            rules.append(payload)
    return rules


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


# ---------- 对外工具：MySQL规则命中列表 ----------
def match_rules(facts: FactOutput) -> List[MatchedRule]:
    """
    从 MySQL platform_rules 中筛选条件全部满足的规则，并转为 MatchedRule 列表。

    仅当 ENABLE_LOCAL_RULES_FALLBACK=true 时，才允许使用本地 JSON 作为开发回退。

    参数:
        facts: Agent1 输出，字段名须与 rules 中 conditions 键一致。

    返回:
        命中规则的 MatchedRule 列表，可能为空。
    """
    rules: List[Dict[str, Any]] = []
    try:
        rules = _load_rules_from_mysql()
        logger.info("%s 规则来源=MySQL，加载数量=%s", AGENT2_LOG_PREFIX, len(rules))
    except Exception as exc:  # noqa: BLE001
        logger.error("%s MySQL规则加载失败，原因=%s", AGENT2_LOG_PREFIX, exc)
        if not _is_local_rule_fallback_enabled():
            raise RuntimeError(f"平台规则加载失败：{exc}") from exc

    # 开发回退：显式开启时，才在 MySQL 不可用或无规则时读取本地 JSON。
    if not rules:
        if not _is_local_rule_fallback_enabled():
            logger.warning("%s MySQL未加载到可用规则，未启用本地JSON回退", AGENT2_LOG_PREFIX)
            return []
        rules_path = _resolve_rules_path()
        logger.info("%s 规则来源=JSON回退，rules_path=%s", AGENT2_LOG_PREFIX, rules_path)
        if not rules_path.is_file():
            logger.error("%s 规则文件不存在：%s", AGENT2_LOG_PREFIX, rules_path)
            return []
        try:
            with rules_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            rules = payload.get("rules", []) if isinstance(payload, dict) else []
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 读取规则文件失败：%s", AGENT2_LOG_PREFIX, exc)
            return []

    matched: List[MatchedRule] = []
    facts_dict = facts.model_dump()
    for rule in rules:
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


# ---------- 买家画像：默认值与数据库记录解析 ----------
def _build_default_buyer_profile(buyer_id: str) -> BuyerProfile:
    """
    构建默认买家画像（未命中库记录时回退）。
    """
    return BuyerProfile(
        buyer_id=buyer_id,
        purchase_count=5,
        dispute_count=1,
        dispute_rate=0.2,
        avg_order_value=99.0,
        return_rate=0.15,
        malicious_flags=0,
        positive_review_count=1,
        credit_level="medium",
    )


def _profile_from_db_json(buyer_id: str, profile_json: str) -> BuyerProfile:
    """
    将 buyer_profiles.profile_json 解析为 BuyerProfile。
    """
    try:
        payload = json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"profile_json 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("profile_json 根节点必须是对象")
    payload["buyer_id"] = buyer_id
    return BuyerProfile(**payload)


# ---------- 对外工具：买家画像（优先 MySQL，失败回退默认） ----------
def query_buyer_profile(buyer_id: str, merchant_id: str = "") -> BuyerProfile:
    """
    按买家脱敏 ID 查询画像，优先查 MySQL，未命中回退默认画像。

    参数:
        buyer_id: 买家标识（手机号 SHA256 哈希）。
        merchant_id: 商家标识，用于租户隔离查询；为空时直接走默认画像。

    返回:
        BuyerProfile 实例。

    异常:
        RuntimeError: 数据库读取发生致命异常时抛出，错误信息为中文。
    """
    normalized_buyer_id = buyer_id.strip()
    normalized_merchant_id = merchant_id.strip()
    logger.info(
        "%s 开始查询买家画像，merchant_id=%s buyer_id=%s",
        AGENT2_LOG_PREFIX,
        normalized_merchant_id,
        normalized_buyer_id,
    )

    try:
        mock_profiles = {
            "buyer_high_risk": BuyerProfile(
                buyer_id=normalized_buyer_id,
                purchase_count=2,
                dispute_count=3,
                dispute_rate=0.6,
                avg_order_value=79.0,
                return_rate=0.5,
                malicious_flags=2,
                credit_level="low",
            ),
            "buyer_loyal": BuyerProfile(
                buyer_id=normalized_buyer_id,
                purchase_count=18,
                dispute_count=1,
                dispute_rate=0.06,
                avg_order_value=135.0,
                return_rate=0.08,
                malicious_flags=0,
                credit_level="high",
            ),
        }
        if normalized_buyer_id in mock_profiles:
            profile = mock_profiles[normalized_buyer_id]
            logger.info(
                "%s 命中内置画像，credit_level=%s",
                AGENT2_LOG_PREFIX,
                profile.credit_level,
            )
            return profile

        default_profile = _build_default_buyer_profile(normalized_buyer_id)
        if not normalized_merchant_id or not normalized_buyer_id:
            logger.info("%s merchant_id/buyer_id 为空，返回默认画像", AGENT2_LOG_PREFIX)
            return default_profile

        engine = get_engine()
        with Session(bind=engine) as session:
            record = session.execute(
                select(BuyerProfileRecord).where(
                    BuyerProfileRecord.merchant_id == normalized_merchant_id,
                    BuyerProfileRecord.buyer_hash == normalized_buyer_id,
                )
            ).scalar_one_or_none()
            if record is None:
                logger.info("%s 未命中数据库画像，返回默认画像", AGENT2_LOG_PREFIX)
                return default_profile
            profile = _profile_from_db_json(normalized_buyer_id, record.profile_json)
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


# ---------- 客户价值：LLM 推断四字段 + 双维评分（完整工具链，供 Agent2 / 智能模式 Controller 复用） ----------


def _build_customer_value_infer_messages(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    构建客户价值字段推断的 LLM messages（规则化判定说明 + 跨品类 few-shot）。

    返回:
        OpenAI 兼容 messages 列表，供 chat_completion 使用。
    """
    system_prompt = (
        "你是电商售后策略分析器。任务是从输入事实中推断 4 个结构化字段，"
        "用于后续客户价值评估。禁止假设固定品类；必须遵循以下判定规则。\n"
        "\n"
        "【字段1：defect_severity】\n"
        "- severe：核心功能不可用/影响安全/无法正常履约，或损坏程度显著。\n"
        "- moderate：存在明确问题并影响体验，但不构成完全不可用。\n"
        "- minor：轻微瑕疵或主观体验差异，基本功能可用。\n"
        "优先看事实证据（facts）中的问题描述、证据质量、使用影响，不要看品类名。\n"
        "\n"
        "【字段2：goods_recoverability】\n"
        "- unrecoverable：退回后基本无法二次销售，或修复成本显著不经济。\n"
        "- repairable：可修复后再处理，但存在明确损失。\n"
        "- resalable：可直接二次销售或轻微处理即可再次流转。\n"
        "优先看损坏可逆性与再销售可能性，不依赖类目经验。\n"
        "\n"
        "【字段3：buyer_cooperation】\n"
        "- good：愿意配合补充证据、反馈及时、沟通一致。\n"
        "- neutral：部分配合或信息不完整，但可继续推进。\n"
        "- poor：明显拒绝配合、前后矛盾、反复施压且缺乏有效信息。\n"
        "优先看聊天行为和证据配合度。\n"
        "\n"
        "【字段4：demand_reasonableness】\n"
        "- reasonable：诉求与事实证据、平台常规规则基本一致。\n"
        "- borderline：诉求有部分合理性，但金额或方式偏激进。\n"
        "- unreasonable：诉求明显超出事实支撑或违背规则边界。\n"
        "优先看诉求-证据一致性，再看金额与处理方式是否成比例。\n"
        "\n"
        "输出要求：\n"
        "1) 只输出 JSON 对象，不输出解释文本。\n"
        "2) JSON 严格包含且仅包含 4 个键：\n"
        '{"defect_severity":"minor|moderate|severe","goods_recoverability":"resalable|repairable|unrecoverable",'
        '"buyer_cooperation":"good|neutral|poor","demand_reasonableness":"reasonable|borderline|unreasonable"}\n'
        "3) 不允许返回 null、空字符串或中文枚举。"
    )

    few_shot_user_1 = (
        "示例输入1："
        '{"facts":{"defect_type":"污渍","evidence_quality":"high","missing_evidence":[],"red_flags":[]},'
        '"buyer_profile":{"purchase_count":6},"order_amount":159.0,'
        '"chat_behavior":"买家上传清晰图片并同意补充细节，诉求为部分退款"}'
    )
    few_shot_assistant_1 = (
        '{"defect_severity":"moderate","goods_recoverability":"repairable",'
        '"buyer_cooperation":"good","demand_reasonableness":"reasonable"}'
    )

    few_shot_user_2 = (
        "示例输入2："
        '{"facts":{"defect_type":"功能故障","evidence_quality":"high","missing_evidence":[],"red_flags":[]},'
        '"buyer_profile":{"purchase_count":2},"order_amount":899.0,'
        '"chat_behavior":"买家提供故障视频，诉求全额退款"}'
    )
    few_shot_assistant_2 = (
        '{"defect_severity":"severe","goods_recoverability":"unrecoverable",'
        '"buyer_cooperation":"good","demand_reasonableness":"reasonable"}'
    )

    few_shot_user_3 = (
        "示例输入3："
        '{"facts":{"defect_type":"无瑕疵","evidence_quality":"low","missing_evidence":["清晰照片"],"red_flags":["前后说法不一致"]},'
        '"buyer_profile":{"purchase_count":1},"order_amount":299.0,'
        '"chat_behavior":"拒绝补证，坚持仅退款并威胁差评"}'
    )
    few_shot_assistant_3 = (
        '{"defect_severity":"minor","goods_recoverability":"resalable",'
        '"buyer_cooperation":"poor","demand_reasonableness":"unreasonable"}'
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": few_shot_user_1},
        {"role": "assistant", "content": few_shot_assistant_1},
        {"role": "user", "content": few_shot_user_2},
        {"role": "assistant", "content": few_shot_assistant_2},
        {"role": "user", "content": few_shot_user_3},
        {"role": "assistant", "content": few_shot_assistant_3},
        {"role": "user", "content": f"请按相同规则输出当前输入的 JSON：{json.dumps(payload, ensure_ascii=False)}"},
    ]


def _strip_markdown_json(raw_text: str) -> str:
    """
    去除 LLM 可能返回的 markdown 代码块包裹，便于 JSON 解析。
    """
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return text


def _require_customer_value_field(value: str, valid_values: set[str], field_name: str) -> str:
    """
    校验 LLM 推断字段值；不合法时抛错，由上层捕获并记录。
    """
    normalized = (value or "").strip().lower()
    if normalized not in valid_values:
        raise ValueError(f"字段 {field_name} 返回非法值：{value}")
    return normalized


def infer_customer_value_fields(input_data: StrategyInput) -> Dict[str, str]:
    """
    通过 LLM 推断客户价值评估所需的四个结构化字段（全品类泛化）。

    参数:
        input_data: Agent2 标准输入，含 facts、buyer_profile、order_amount。

    返回:
        defect_severity 等四键字典；LLM 不可用或解析失败时抛出 RuntimeError。

    说明:
        仅负责推断，不计算分值；打分请使用 evaluate_customer_value 或 run_customer_value_analysis。
    """
    payload: Dict[str, Any] = {
        "facts": input_data.facts.model_dump(),
        "buyer_profile": input_data.buyer_profile.model_dump(),
        "order_amount": input_data.order_amount,
    }
    logger.info("%s 开始调用 LLM 推断客户价值字段", AGENT2_LOG_PREFIX)
    try:
        llm_text = chat_completion(
            messages=_build_customer_value_infer_messages(payload),
            model_env_key="AGENT2_LLM_MODEL",
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 客户价值字段推断调用 LLM 失败：%s", AGENT2_LOG_PREFIX, exc)
        raise RuntimeError(f"客户价值字段推断失败：LLM 调用异常，原因：{exc}") from exc

    if not llm_text:
        logger.error("%s 客户价值字段推断失败：LLM 无返回内容", AGENT2_LOG_PREFIX)
        raise RuntimeError("客户价值字段推断失败：LLM 无返回内容")

    try:
        parsed = json.loads(_strip_markdown_json(llm_text))
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 客户价值字段推断解析 JSON 失败：%s", AGENT2_LOG_PREFIX, exc)
        raise RuntimeError(f"客户价值字段推断失败：LLM 输出解析异常，原因：{exc}") from exc

    return {
        "defect_severity": _require_customer_value_field(
            str(parsed.get("defect_severity", "")),
            {"minor", "moderate", "severe"},
            "defect_severity",
        ),
        "goods_recoverability": _require_customer_value_field(
            str(parsed.get("goods_recoverability", "")),
            {"resalable", "repairable", "unrecoverable"},
            "goods_recoverability",
        ),
        "buyer_cooperation": _require_customer_value_field(
            str(parsed.get("buyer_cooperation", "")),
            {"good", "neutral", "poor"},
            "buyer_cooperation",
        ),
        "demand_reasonableness": _require_customer_value_field(
            str(parsed.get("demand_reasonableness", "")),
            {"reasonable", "borderline", "unreasonable"},
            "demand_reasonableness",
        ),
    }


# ---------- 客户价值评估：双维评分（长期价值 + 本单价值） ----------
def _build_score_item(dimension: str, score: int, max_score: int, reason: str) -> CustomerValueScoreItem:
    """
    构建统一分项结构，确保输出格式稳定。
    """
    return CustomerValueScoreItem(dimension=dimension, score=score, max_score=max_score, reason=reason)


def evaluate_customer_value(input_data: CustomerValueInput) -> CustomerValueOutput:
    """
    评估客户长期价值与本单处理价值，输出结构化评分与通道触发标记。

    设计约束：
    1. 双维独立评分，避免一维高分被另一维低分稀释。
    2. 非均权重：长期维度和本单维度均按业务重要性分配权重。
    3. 纯函数无状态：不访问数据库，不发起外部调用。
    """
    profile = input_data.buyer_profile

    # ----- 长期价值分（满分 100）-----
    total_spend = max(0.0, profile.avg_order_value) * max(0, profile.purchase_count)
    if total_spend >= 2000:
        spend_score, spend_reason = 30, "累计消费金额高，长期贡献强"
    elif total_spend >= 500:
        spend_score, spend_reason = 20, "累计消费金额中高，对店铺有稳定贡献"
    elif total_spend >= 100:
        spend_score, spend_reason = 10, "累计消费金额一般，具备基础价值"
    else:
        spend_score, spend_reason = 3, "累计消费金额偏低，长期贡献有限"

    purchase_count = max(0, profile.purchase_count)
    if purchase_count >= 20:
        order_count_score, order_count_reason = 25, "复购频次高，客户关系稳定"
    elif purchase_count >= 10:
        order_count_score, order_count_reason = 18, "多次复购，黏性较高"
    elif purchase_count >= 3:
        order_count_score, order_count_reason = 10, "已有复购行为，具备维护价值"
    else:
        order_count_score, order_count_reason = 3, "复购行为较少，关系尚浅"

    dispute_rate = max(0.0, min(1.0, profile.dispute_rate))
    if dispute_rate <= 0.05:
        dispute_score, dispute_reason = 20, "历史纠纷率低，合作顺畅"
    elif dispute_rate <= 0.15:
        dispute_score, dispute_reason = 14, "历史纠纷率可控，合作总体稳定"
    elif dispute_rate <= 0.3:
        dispute_score, dispute_reason = 7, "历史纠纷率偏高，维护成本上升"
    else:
        dispute_score, dispute_reason = 1, "历史纠纷率高，长期合作风险大"

    positive_review_count = max(0, profile.positive_review_count)
    if positive_review_count >= 5:
        review_score, review_reason = 15, "好评/带图反馈多，正向口碑价值高"
    elif positive_review_count >= 2:
        review_score, review_reason = 10, "存在稳定正向反馈，口碑贡献较好"
    elif positive_review_count >= 1:
        review_score, review_reason = 6, "已有正向反馈记录，具备口碑潜力"
    else:
        review_score, review_reason = 0, "暂无好评/带图沉淀，口碑贡献有限"

    if purchase_count >= 5:
        repurchase_score, repurchase_reason = 10, "复购行为稳定，消费规律性较好"
    elif purchase_count >= 2:
        repurchase_score, repurchase_reason = 6, "已形成复购习惯，规律性初步建立"
    elif purchase_count == 1:
        repurchase_score, repurchase_reason = 2, "仅有单次购买，规律性不足"
    else:
        repurchase_score, repurchase_reason = 0, "无有效复购记录"

    long_term_breakdown = [
        _build_score_item("累计消费金额", spend_score, 30, spend_reason),
        _build_score_item("累计订单数/复购行为", order_count_score, 25, order_count_reason),
        _build_score_item("历史纠纷率", dispute_score, 20, dispute_reason),
        _build_score_item("好评/带图记录", review_score, 15, review_reason),
        _build_score_item("复购间隔规律性", repurchase_score, 10, repurchase_reason),
    ]
    long_term_score = sum(item.score for item in long_term_breakdown)

    # ----- 本单价值分（满分 100）-----
    order_amount = max(0.0, input_data.order_amount)
    if order_amount >= 500:
        amount_score, amount_reason = 40, "本单金额高，处理影响大"
    elif order_amount >= 200:
        amount_score, amount_reason = 28, "本单金额中高，需要兼顾体验与成本"
    elif order_amount >= 50:
        amount_score, amount_reason = 14, "本单金额中等，建议稳妥处理"
    else:
        amount_score, amount_reason = 4, "本单金额较低，优先控制处理成本"

    severity_key = input_data.defect_severity.strip().lower()
    severity_score_map = {
        "severe": (25, "问题严重，处理不当易升级"),
        "moderate": (15, "问题中等，需给出明确方案"),
        "minor": (5, "问题较轻，可在规则内快速收敛"),
    }
    severity_score, severity_reason = severity_score_map.get(severity_key, (15, "严重性未明确，按中等严重处理"))

    recoverability_key = input_data.goods_recoverability.strip().lower()
    recoverability_score_map = {
        "unrecoverable": (20, "商品不可挽回，商家损失大，需重点处理"),
        "repairable": (12, "商品可修复，存在一定损失与处理空间"),
        "resalable": (4, "商品可二次销售，实际损失相对可控"),
    }
    recoverability_score, recoverability_reason = recoverability_score_map.get(
        recoverability_key,
        (12, "可挽回性未明确，按可修复处理"),
    )

    cooperation_key = input_data.buyer_cooperation.strip().lower()
    cooperation_score_map = {
        "good": (10, "买家配合度高，沟通成本低"),
        "neutral": (6, "买家配合度一般，需持续引导"),
        "poor": (2, "买家配合度低，处理阻力较大"),
    }
    cooperation_score, cooperation_reason = cooperation_score_map.get(cooperation_key, (6, "配合度未明确，按一般处理"))

    reasonableness_key = input_data.demand_reasonableness.strip().lower()
    reasonableness_score_map = {
        "reasonable": (5, "诉求合理，协商成功概率更高"),
        "borderline": (3, "诉求部分合理，需要边界沟通"),
        "unreasonable": (1, "诉求偏离规则，需谨慎让步"),
    }
    reasonableness_score, reasonableness_reason = reasonableness_score_map.get(
        reasonableness_key,
        (3, "诉求合理性未明确，按边界诉求处理"),
    )

    order_breakdown = [
        _build_score_item("本单金额", amount_score, 40, amount_reason),
        _build_score_item("售后问题严重性", severity_score, 25, severity_reason),
        _build_score_item("商品可挽回性（越差分越高）", recoverability_score, 20, recoverability_reason),
        _build_score_item("买家配合度", cooperation_score, 10, cooperation_reason),
        _build_score_item("诉求合理性", reasonableness_score, 5, reasonableness_reason),
    ]
    order_score = sum(item.score for item in order_breakdown)

    # ----- 通道触发与建议输出 -----
    long_term_triggered = long_term_score >= 60
    order_triggered = order_score >= 60

    if long_term_triggered:
        channel = "long_term"
        compensation_uplift = "+10%~20%"
        tone_suggestion = "偏暖，珍惜老客"
    elif order_triggered:
        channel = "order"
        compensation_uplift = "+10%~20%"
        tone_suggestion = "快速响应，妥善处理"
    else:
        channel = "none"
        compensation_uplift = None
        tone_suggestion = None

    return CustomerValueOutput(
        long_term_score=long_term_score,
        order_score=order_score,
        long_term_breakdown=long_term_breakdown,
        order_breakdown=order_breakdown,
        long_term_triggered=long_term_triggered,
        order_triggered=order_triggered,
        channel=channel,
        compensation_uplift=compensation_uplift,
        tone_suggestion=tone_suggestion,
    )


def run_customer_value_analysis(input_data: StrategyInput) -> CustomerValueOutput:
    """
    完整客户价值分析：LLM 推断四字段 + 双维评分与通道判定。

    供 recommend 与智能模式 Controller 直接调用，避免在 Agent 文件重复编排逻辑。

    参数:
        input_data: Agent2 标准策略输入。

    返回:
        CustomerValueOutput。
    """
    inferred_fields = infer_customer_value_fields(input_data)
    logger.info("%s 客户价值推断字段完成：%s", AGENT2_LOG_PREFIX, inferred_fields)
    customer_value_input = CustomerValueInput(
        buyer_profile=input_data.buyer_profile,
        order_amount=input_data.order_amount,
        defect_severity=inferred_fields["defect_severity"],
        goods_recoverability=inferred_fields["goods_recoverability"],
        buyer_cooperation=inferred_fields["buyer_cooperation"],
        demand_reasonableness=inferred_fields["demand_reasonableness"],
        emotion_note=input_data.emotion_note,
    )
    return evaluate_customer_value(customer_value_input)


# ---------- 恶意行为检测：第一层硬规则 + 第二层语义分析 ----------
def _make_malicious_signal(signal_type: str, description: str, score: int, source: str) -> MaliciousSignal:
    """
    统一构造恶意信号对象，避免不同分支输出字段不一致。
    """
    return MaliciousSignal(
        signal_type=signal_type,
        description=description,
        score=max(0, score),
        source=source,
    )


def _run_hard_rules(
    input_data: MaliciousDetectionInput,
    *,
    refund_only_count_threshold: int = 3,
    return_rate_multiple_threshold: float = 2.0,
    high_return_rate_multiple_for_insurance: float = 3.0,
    batch_order_purchase_threshold: int = 5,
    batch_order_dispute_rate_threshold: float = 0.5,
    swap_flag_threshold: int = 2,
    related_account_threshold: int = 3,
) -> List[MaliciousSignal]:
    """
    第一层硬规则匹配：纯代码判定，可解释、可配置。
    """
    signals: List[MaliciousSignal] = []
    facts = input_data.facts
    profile = input_data.buyer_profile
    category_avg = max(0.0001, input_data.return_rate_category_avg)

    if facts.evidence_quality.lower().strip() == "low" and len(facts.red_flags) > 0:
        signals.append(
            _make_malicious_signal(
                signal_type="fake_evidence",
                description="证据质量低且存在疑点，疑似虚假凭证骗退款",
                score=20,
                source="hard_rule",
            )
        )

    if (
        input_data.recent_refund_only_count >= refund_only_count_threshold
        or profile.return_rate >= category_avg * return_rate_multiple_threshold
    ):
        signals.append(
            _make_malicious_signal(
                signal_type="abuse_refund_only",
                description=(
                    f"仅退款频次或退货率异常（仅退款{input_data.recent_refund_only_count}次，"
                    f"退货率{profile.return_rate:.2f}，类目均值{category_avg:.2f}）"
                ),
                score=20,
                source="hard_rule",
            )
        )

    if profile.purchase_count >= batch_order_purchase_threshold and profile.dispute_rate > batch_order_dispute_rate_threshold:
        signals.append(
            _make_malicious_signal(
                signal_type="batch_malicious_orders",
                description="购买频次高且纠纷率异常，疑似批量恶意下单",
                score=20,
                source="hard_rule",
            )
        )

    if input_data.freight_insurance_used and profile.return_rate >= category_avg * high_return_rate_multiple_for_insurance:
        signals.append(
            _make_malicious_signal(
                signal_type="freight_insurance_abuse",
                description="运费险使用与高退货率叠加，疑似骗取运费险",
                score=20,
                source="hard_rule",
            )
        )

    if input_data.swap_flag_count >= swap_flag_threshold:
        signals.append(
            _make_malicious_signal(
                signal_type="swap_or_missing_items",
                description=f"历史调包/少件标记达到{input_data.swap_flag_count}次",
                score=20,
                source="hard_rule",
            )
        )

    if input_data.order_address and facts.logistics_normal is False:
        signals.append(
            _make_malicious_signal(
                signal_type="abnormal_return_address",
                description="存在地址信息且物流状态异常，疑似退货地址异常",
                score=20,
                source="hard_rule",
            )
        )

    if input_data.related_account_count >= related_account_threshold:
        signals.append(
            _make_malicious_signal(
                signal_type="related_accounts",
                description=f"关联账号数量达到{input_data.related_account_count}，疑似多账号协同",
                score=20,
                source="hard_rule",
            )
        )

    return signals


def _build_hard_rule_summary(hard_signals: List[MaliciousSignal]) -> str:
    """
    构建硬规则层摘要，供 LLM 二层校验与日志复用。
    """
    if not hard_signals:
        return "硬规则层未命中异常项。"
    return "；".join([f"{item.signal_type}:{item.description}" for item in hard_signals])


# ---------- 恶意信号类型 → 中文短名（风险提示区与日志可读性） ----------
_MALICIOUS_SIGNAL_TYPE_CN: dict[str, str] = {
    "fake_evidence": "疑似虚假凭证（硬规则）",
    "abuse_refund_only": "滥用仅退款",
    "batch_malicious_orders": "批量恶意下单",
    "freight_insurance_abuse": "疑似骗取运费险",
    "swap_or_missing_items": "退货调包/少件",
    "abnormal_return_address": "退货地址异常",
    "related_accounts": "关联账户异常",
    "review_blackmail": "差评/投诉勒索",
    "identity_impersonation": "冒充身份施压",
    "evidence_contradiction": "话术与证据矛盾",
    "professional_claim_pattern": "职业索赔话术",
    "fake_credential_web_image": "举证疑似网图/非实拍",
    "abuse_refund_intent_chat": "聊天暴露高频套利/仅退意图",
}


def _format_malicious_risk_hints(signals: List[MaliciousSignal]) -> str:
    """
    将硬规则与语义层全部命中信号格式化为「风险提示」多行文案。

    参数:
        signals: 已合并的恶意信号列表。

    返回:
        面向商家的中文说明；无命中时返回固定提示句。
    """
    if not signals:
        return "当前未命中明确恶意行为信号。"
    lines: List[str] = []
    for item in signals:
        label = _MALICIOUS_SIGNAL_TYPE_CN.get(item.signal_type, item.signal_type.replace("_", " "))
        layer = "硬规则" if item.source == "hard_rule" else "语义层"
        lines.append(f"【{label}】{item.description}（{item.score}分，{layer}）")
    return "\n".join(lines)


def _strip_markdown_json(text: str) -> str:
    """
    清理 markdown 代码块外壳，提升 JSON 解析稳定性。
    """
    content = text.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3:
            content = "\n".join(lines[1:-1]).strip()
    return content


def _build_malicious_semantic_messages(
    input_data: MaliciousDetectionInput,
    hard_rule_summary: str,
) -> List[Dict[str, str]]:
    """
    构造语义层提示词：对齐补充设计中的恶意分类边界 + 跨品类 few-shot（不少于多例）。
    """
    taxonomy_block = (
        "【恶意类型参考（判断边界，全品类适用）】\n"
        "A. 利用规则/凭证获利：滥用仅退款、虚假或网络图片举证、运费险套利、恶意差价退款、知假买假式高额索赔。\n"
        "B. 退货欺诈：调包、买真退假、少件、恶意拒收等。\n"
        "C. 攻击店铺运营：差评/投诉要挟赔偿、炸店、有组织差评退款。\n"
        "D. 黑灰产：多账号薅羊毛、职业索赔模板化话术、骗取补贴等。\n"
        "若聊天为空，仍须结合 facts（含 issue_summary、red_flags、visual_observations、evidence_quality）与硬规则摘要识别举证类风险。\n"
    )
    system_prompt = (
        "你是电商恶意行为语义分析器。任务：识别材料中的恶意语义信号，并校验硬规则提示是否在聊天或陈述中有呼应。\n"
        f"{taxonomy_block}\n"
        "输出 signal_type 必须是下列英文枚举之一（禁止自造新枚举名）：\n"
        "- review_blackmail：差评/投诉/曝光勒索（须同时出现威胁词与条件交换，否则不输出）。\n"
        "- identity_impersonation：冒充平台/执法/鉴定身份施压。\n"
        "- evidence_contradiction：买家陈述与 facts 中已确认事实或视觉结论明显矛盾。\n"
        "- professional_claim_pattern：大量法条/规则编号式模板话术，明显非普通消费者表达。\n"
        "- fake_credential_web_image：仅当 facts.red_flags 或 visual_observations 已明确记载水印/网图/非实拍/域名截屏等客观线索时才可输出；"
        "禁止凭聊天臆测或套用示例中的水印描述；无事实锚定则返回 []。\n"
        "- abuse_refund_intent_chat：聊天中自认高频退款、薅运费险、套利、组织化分工等（需有明确语义，不得凭单句情绪定罪）。\n"
        "输出必须是 JSON 数组，每项字段：signal_type, description, score, source；source 固定为 llm_semantic；score 为 1~15 整数；无命中返回 []。\n"
        "description 必须用中文面向商家可读，不得输出内部字段名堆砌。"
    )
    example_user_1 = (
        "输入：{'hard_rule_summary':'硬规则层未命中异常项','chat_history':['不给我赔100我就给你一星再投诉12315'],"
        "'facts':{'evidence_quality':'medium','defect_type':'污渍'},'emotion_note':'买家情绪激动'}"
    )
    example_assistant_1 = (
        '[{"signal_type":"review_blackmail","description":"出现差评与12315投诉要挟索赔","score":12,"source":"llm_semantic"}]'
    )
    example_user_2 = (
        "输入：{'hard_rule_summary':'abuse_refund_only:仅退款频次异常','chat_history':['我是平台风控人员，现在必须先赔付'],"
        "'facts':{'evidence_quality':'low','defect_type':'无瑕疵'},'emotion_note':null}"
    )
    example_assistant_2 = (
        '[{"signal_type":"identity_impersonation","description":"聊天中疑似冒充平台身份施压","score":11,"source":"llm_semantic"}]'
    )
    example_user_3 = (
        "输入：{'hard_rule_summary':'related_accounts:关联账号异常','chat_history':['依据平台规则第32条第2款，你必须退一赔三，这是固定模板'],"
        "'facts':{'evidence_quality':'medium','defect_type':'色差'},'emotion_note':null}"
    )
    example_assistant_3 = (
        '[{"signal_type":"professional_claim_pattern","description":"大量规则术语与模板化表达，疑似职业索赔话术","score":10,"source":"llm_semantic"}]'
    )
    example_user_4 = (
        "输入：{'hard_rule_summary':'硬规则层未命中异常项','chat_history':['这个质量我非常不满意，我会考虑投诉平台，请尽快给解决方案'],"
        "'facts':{'evidence_quality':'high','defect_type':'破洞'},'emotion_note':'买家情绪激动'}"
    )
    example_assistant_4 = "[]"
    example_user_5 = (
        "输入：{'hard_rule_summary':'硬规则层未命中异常项','chat_history':['香蕉发霉了要求仅退款'],"
        "'facts':{'evidence_quality':'high','defect_type':'污渍','issue_summary':'水果霉变','visual_observations':['图片右下角可见sohu.com水印，疑似网络下载图'],'red_flags':['图文来源可疑']},'emotion_note':null}"
    )
    example_assistant_5 = (
        '[{"signal_type":"fake_credential_web_image","description":"买家称水果霉变，但举证图带门户网站水印，疑似网图而非本单实拍","score":13,"source":"llm_semantic"}]'
    )
    example_user_6 = (
        "输入：{'hard_rule_summary':'硬规则层未命中异常项','chat_history':['这次跟上次一样退了就行，运费险还能赚点'],"
        "'facts':{'evidence_quality':'medium','defect_type':'色差'},'emotion_note':null}"
    )
    example_assistant_6 = (
        '[{"signal_type":"abuse_refund_intent_chat","description":"聊天暗示高频退款并提及运费险套利，存在滥用售后意图","score":9,"source":"llm_semantic"}]'
    )

    chat_for_prompt = list(input_data.chat_history) if input_data.chat_history else [
        "（无独立聊天文本：请仅依据 facts、issue_summary、red_flags、visual_observations 与硬规则摘要识别举证型恶意。）"
    ]
    user_payload = {
        "hard_rule_summary": hard_rule_summary,
        "chat_history": chat_for_prompt,
        "facts": input_data.facts.model_dump(),
        "emotion_note": input_data.emotion_note,
    }

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": example_user_1},
        {"role": "assistant", "content": example_assistant_1},
        {"role": "user", "content": example_user_2},
        {"role": "assistant", "content": example_assistant_2},
        {"role": "user", "content": example_user_3},
        {"role": "assistant", "content": example_assistant_3},
        {"role": "user", "content": example_user_4},
        {"role": "assistant", "content": example_assistant_4},
        {"role": "user", "content": example_user_5},
        {"role": "assistant", "content": example_assistant_5},
        {"role": "user", "content": example_user_6},
        {"role": "assistant", "content": example_assistant_6},
        {"role": "user", "content": f"输入：{json.dumps(user_payload, ensure_ascii=False)}"},
    ]


def _facts_anchor_supports_web_image_suspicion(facts) -> bool:
    """
    校验 Agent1 事实中是否已有「网图/水印/非实拍」类客观线索，供语义层 fake_credential 锚定。

    参数:
        facts: FactOutput 或等价 dict。

    返回:
        True 表示事实层已记载可疑图源，语义层方可输出 fake_credential_web_image。
    """
    anchor_keywords = (
        "水印",
        "网图",
        "网址",
        "域名",
        "截屏",
        "非实拍",
        "下载图",
        "公开图",
        "来源可疑",
        "图文来源",
        ".com",
        ".cn",
        "http",
    )

    def _iter_text_blobs() -> List[str]:
        blobs: List[str] = []
        if hasattr(facts, "red_flags"):
            blobs.extend(str(x) for x in (facts.red_flags or []))
            blobs.extend(str(x) for x in (facts.visual_observations or []))
            if getattr(facts, "issue_summary", None):
                blobs.append(str(facts.issue_summary))
        elif isinstance(facts, dict):
            blobs.extend(str(x) for x in (facts.get("red_flags") or []))
            blobs.extend(str(x) for x in (facts.get("visual_observations") or []))
            if facts.get("issue_summary"):
                blobs.append(str(facts["issue_summary"]))
        return blobs

    corpus = " ".join(_iter_text_blobs()).lower()
    return any(keyword in corpus for keyword in anchor_keywords)


def _is_review_blackmail_chat(chat_history: List[str]) -> bool:
    """
    review_blackmail 双条件校验：威胁词 + 条件交换词同时存在才算勒索。
    """
    merged = " ".join(chat_history)
    threat_keywords = ("差评", "投诉", "12315", "曝光", "举报")
    exchange_keywords = ("不给", "不赔", "否则", "不然", "就", "先赔", "赔我", "转账")
    has_threat = any(word in merged for word in threat_keywords)
    has_exchange = any(word in merged for word in exchange_keywords)
    return has_threat and has_exchange


def _run_llm_semantic(input_data: MaliciousDetectionInput, hard_signals: List[MaliciousSignal]) -> List[MaliciousSignal]:
    """
    第二层语义分析：在硬规则结果基础上补充威胁与矛盾类风险信号。
    """
    if not os.getenv("AGENT2_LLM_MODEL", "").strip():
        logger.info("%s 未配置 AGENT2_LLM_MODEL，语义层跳过，仅保留硬规则层结果", AGENT2_LOG_PREFIX)
        return []

    semantic_allowed = frozenset(
        {
            "review_blackmail",
            "identity_impersonation",
            "evidence_contradiction",
            "professional_claim_pattern",
            "fake_credential_web_image",
            "abuse_refund_intent_chat",
        }
    )

    hard_rule_summary = _build_hard_rule_summary(hard_signals)
    llm_text = chat_completion(
        messages=_build_malicious_semantic_messages(input_data=input_data, hard_rule_summary=hard_rule_summary),
        model_env_key="AGENT2_LLM_MODEL",
        temperature=0.0,
    )
    if not llm_text:
        raise RuntimeError("恶意语义分析失败：LLM 无返回内容")

    try:
        parsed = json.loads(_strip_markdown_json(llm_text))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"恶意语义分析失败：JSON 解析异常，原因：{exc}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError("恶意语义分析失败：输出不是 JSON 数组")

    signals: List[MaliciousSignal] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        signal_type = str(item.get("signal_type", "")).strip()
        description = str(item.get("description", "")).strip()
        score_raw = item.get("score", 0)
        source = str(item.get("source", "llm_semantic")).strip()
        if not signal_type or not description or source != "llm_semantic":
            continue
        if signal_type not in semantic_allowed:
            logger.info("%s 语义层忽略未授权 signal_type=%s", AGENT2_LOG_PREFIX, signal_type)
            continue
        try:
            score = int(score_raw)
        except Exception:  # noqa: BLE001
            continue
        if score < 1:
            continue
        if signal_type == "review_blackmail" and not _is_review_blackmail_chat(input_data.chat_history):
            logger.info("%s review_blackmail 未通过双条件校验，按情绪激动处理，不计入恶意分", AGENT2_LOG_PREFIX)
            continue
        if signal_type == "fake_credential_web_image" and not _facts_anchor_supports_web_image_suspicion(
            input_data.facts
        ):
            logger.info(
                "%s fake_credential_web_image 未通过事实锚定校验（facts 无水印/网图类记录），忽略该语义信号",
                AGENT2_LOG_PREFIX,
            )
            continue
        signals.append(_make_malicious_signal(signal_type, description, min(15, score), "llm_semantic"))
    return signals


def _risk_level_from_score(risk_score: int) -> str:
    """
    根据综合分映射风险等级。
    """
    if risk_score >= 60:
        return "high"
    if risk_score >= 30:
        return "medium"
    return "low"


def _disposition_advice_from_level(risk_level: str) -> str:
    """
    根据风险等级生成处置建议。
    """
    if risk_level == "high":
        return "建议优先抗辩并准备平台介入材料，固定完整证据链后再沟通。"
    if risk_level == "medium":
        return "建议谨慎协商并加强举证要求，控制补偿上限。"
    return "建议按常规流程处理，持续观察风险信号变化。"


def detect_malicious_behavior(input_data: MaliciousDetectionInput) -> MaliciousDetectionOutput:
    """
    恶意行为检测统一入口：硬规则层 + 语义层融合输出。
    """
    logger.info("%s 开始执行恶意行为检测", AGENT2_LOG_PREFIX)
    hard_signals = _run_hard_rules(input_data=input_data)
    semantic_signals = _run_llm_semantic(input_data=input_data, hard_signals=hard_signals)
    all_signals = hard_signals + semantic_signals

    risk_total_score = min(100, sum(signal.score for signal in all_signals))
    risk_level = _risk_level_from_score(risk_total_score)
    hard_rule_summary = _build_hard_rule_summary(hard_signals)
    malicious_risk_hints = _format_malicious_risk_hints(all_signals)
    disposition_advice = _disposition_advice_from_level(risk_level)

    logger.info(
        "%s 恶意行为检测完成 risk_score=%s risk_level=%s signal_count=%s",
        AGENT2_LOG_PREFIX,
        risk_total_score,
        risk_level,
        len(all_signals),
    )
    return MaliciousDetectionOutput(
        risk_score=risk_total_score,
        risk_level=risk_level,
        triggered_signals=all_signals,
        hard_rule_summary=hard_rule_summary,
        malicious_risk_hints=malicious_risk_hints,
        disposition_advice=disposition_advice,
    )
