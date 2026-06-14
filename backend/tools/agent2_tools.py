"""
Agent 2 工具集：规则匹配、买家画像查询、相似判例检索、恶意行为检测、客户价值完整分析。

约束：文件路径从环境变量读取；异常时按 Tools.md 约定记录日志或抛出由 Controller 捕获。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.db.connection import get_engine  # noqa: E402
from backend.db.models import BuyerProfileRecord  # noqa: E402
from backend.tools.llm_client import chat_completion  # noqa: E402
from backend.tools.text_signals import (  # noqa: E402
    contains_any,
    facts_has_deceptive_credential_clues,
    pick_balanced_visual_observations,
    signal_group,
)
from schemas import (  # noqa: E402
    BuyerProfile,
    CustomerValueInput,
    CustomerValueOutput,
    CustomerValueScoreItem,
    EVIDENCE_LOW,
    FactOutput,
    MaliciousDetectionInput,
    MaliciousDetectionOutput,
    MaliciousSignal,
    MatchedRule,
    RuleMatchResult,
    SimilarCase,
    StrategyInput,
)


logger = logging.getLogger(__name__)
AGENT2_LOG_PREFIX = "[Agent2]"


def _rule_plan_has_category_or_service_lane(facts: FactOutput) -> bool:
    """
    判定 Agent1 导航是否已锁定特殊品类（C）或专项服务（E）通道。

    只要 plan 中激活了 C/E 或 target_doc_ids 含对应专项 doc，即应跑条文匹配。
    """
    plan = facts.rule_match_plan
    activated = set(plan.activated_lanes or [])
    if "C" in activated or "E" in activated:
        return True

    from backend.tools.rule_lexicon import is_category_doc, load_lexicon

    service_doc_ids = set(
        ((load_lexicon().get("lanes") or {}).get("E_service_tag_to_doc_id") or {}).values()
    )
    for doc_id in plan.target_doc_ids or []:
        if is_category_doc(doc_id) or doc_id in service_doc_ids:
            return True
    return False


def needs_rule_match(
    facts: FactOutput,
    malicious_result: MaliciousDetectionOutput,
    customer_value: CustomerValueOutput,
) -> bool:
    """
    判定是否启动条文匹配。

    触发：特殊品类/专项服务通道已锁定；恶意 medium+；价值通道；多诉求标签。
    不含举证未闭环（补证属流程动作，不必查平台条文）。
    """
    if _rule_plan_has_category_or_service_lane(facts):
        return True
    if malicious_result.risk_level in {"medium", "high"}:
        return True
    if customer_value.channel in {"long_term", "order"}:
        return True
    intent_tags = [str(t).strip() for t in (facts.intent_tags or []) if str(t).strip()]
    if len(intent_tags) > 1:
        return True
    return False


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
        # 本地模拟画像优先（智能模式联调）
        from backend.tools.simulation_fixture import get_simulated_buyer_profile

        simulated_profile = get_simulated_buyer_profile(normalized_buyer_id)
        if simulated_profile is not None:
            logger.info("%s 使用模拟买家画像 buyer_id=%s", AGENT2_LOG_PREFIX, normalized_buyer_id)
            return simulated_profile

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


# ---------- 对外工具：相似判例检索 ----------
def search_similar_cases(dispute_desc: str, top_k: int = 3) -> List[SimilarCase]:
    """
    按纠纷描述检索相似历史判例。

    参数:
        dispute_desc: 纠纷自然语言描述。
        top_k: 返回条数上限，须为正整数。

    返回:
        SimilarCase 列表。

    异常:
        ValueError: top_k <= 0。
    """
    _ = dispute_desc
    logger.info("%s 开始检索相似判例，top_k=%s", AGENT2_LOG_PREFIX, top_k)

    if top_k <= 0:
        raise ValueError("top_k 必须大于 0")

    return []


# ---------- 客户价值：视觉损失暴露 + 双维评分（无专用 LLM） ----------
ORDER_VALUE_SCORE_THRESHOLD = 60
ORDER_VALUE_AMOUNT_ONLY_THRESHOLD = 500.0
# 长期优待通道仅由 long_term_score 触发，累计金额阈值不得单独开通老客通道。


def _facts_has_visual_loss_exposure(facts: FactOutput) -> bool:
    """两枚举均非空时视为已有视觉损失暴露评估。"""
    severity = str(facts.visual_defect_severity or "").strip().lower()
    recoverability = str(facts.visual_goods_recoverability or "").strip().lower()
    return bool(severity and recoverability)


def _should_block_order_value_channel(facts: FactOutput) -> bool:
    """本单优待通道门槛：疑点或低证据且仍缺证时不触发。"""
    if facts.red_flags:
        return True
    if facts.evidence_quality == EVIDENCE_LOW and facts.missing_evidence:
        return True
    return False


def _build_customer_value_input_from_strategy(input_data: StrategyInput) -> CustomerValueInput:
    """从策略输入构造客户价值评估参数（严重度/可挽回性仅来自视觉）。"""
    facts = input_data.facts
    has_loss = _facts_has_visual_loss_exposure(facts)
    return CustomerValueInput(
        buyer_profile=input_data.buyer_profile,
        order_amount=input_data.order_amount,
        defect_severity=facts.visual_defect_severity if has_loss else None,
        goods_recoverability=facts.visual_goods_recoverability if has_loss else None,
        has_visual_loss_exposure=has_loss,
        block_order_channel=_should_block_order_value_channel(facts),
        emotion_note=input_data.emotion_note,
    )
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

    # ----- 本单价值分（满分 85，触发阈值 60）-----
    order_amount = max(0.0, input_data.order_amount)
    if order_amount >= 500:
        amount_score, amount_reason = 40, "本单金额高，处理影响大"
    elif order_amount >= 200:
        amount_score, amount_reason = 25, "本单金额中高，需要兼顾体验与成本"
    elif order_amount >= 50:
        amount_score, amount_reason = 15, "本单金额中等，建议稳妥处理"
    else:
        amount_score, amount_reason = 5, "本单金额较低，优先控制处理成本"

    order_breakdown = [_build_score_item("本单金额", amount_score, 40, amount_reason)]

    severity_key = str(input_data.defect_severity or "").strip().lower()
    if input_data.has_visual_loss_exposure and severity_key:
        severity_score_map = {
            "severe": (25, "问题严重，处理不当易升级"),
            "moderate": (15, "问题中等，需给出明确方案"),
            "minor": (5, "问题较轻，可在规则内快速收敛"),
        }
        severity_score, severity_reason = severity_score_map.get(severity_key, (0, "视觉严重度未识别，暂不计入"))
        order_breakdown.append(_build_score_item("售后问题严重性（视觉）", severity_score, 25, severity_reason))
    else:
        order_breakdown.append(
            _build_score_item(
                "售后问题严重性（视觉）",
                0,
                25,
                "无有效举证图，损失暴露未评估，请先补图",
            )
        )

    recoverability_key = str(input_data.goods_recoverability or "").strip().lower()
    if input_data.has_visual_loss_exposure and recoverability_key:
        recoverability_score_map = {
            "unrecoverable": (20, "商品不可挽回，商家损失大，需重点处理"),
            "repairable": (15, "商品可修复，存在一定损失与处理空间"),
            "resalable": (5, "商品可二次销售，实际损失相对可控"),
        }
        recoverability_score, recoverability_reason = recoverability_score_map.get(
            recoverability_key,
            (0, "视觉可挽回性未识别，暂不计入"),
        )
        order_breakdown.append(
            _build_score_item("商品可挽回性（视觉，越差分越高）", recoverability_score, 20, recoverability_reason)
        )
    else:
        order_breakdown.append(
            _build_score_item(
                "商品可挽回性（视觉，越差分越高）",
                0,
                20,
                "无有效举证图，损失暴露未评估，请先补图",
            )
        )

    order_score = sum(item.score for item in order_breakdown)

    # ----- 通道触发与建议输出 -----
    long_term_triggered = long_term_score >= ORDER_VALUE_SCORE_THRESHOLD
    if input_data.block_order_channel:
        order_triggered = False
    elif order_score >= ORDER_VALUE_SCORE_THRESHOLD:
        order_triggered = True
    elif (
        not input_data.has_visual_loss_exposure
        and order_amount >= ORDER_VALUE_AMOUNT_ONLY_THRESHOLD
    ):
        order_triggered = True
    else:
        order_triggered = False

    if long_term_triggered:
        channel = "long_term"
        compensation_uplift = "+10%~20%"
        tone_suggestion = "偏暖，珍惜老客"
    elif order_triggered:
        channel = "order"
        compensation_uplift = None
        tone_suggestion = "快速响应，先核实事实并控制本单损失"
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
    完整客户价值分析：读取 Agent1 视觉损失暴露 + 双维评分与通道判定。

    供 recommend 与智能模式 Controller 直接调用，避免在 Agent 文件重复编排逻辑。
    """
    customer_value_input = _build_customer_value_input_from_strategy(input_data)
    logger.info(
        "%s 客户价值评估开始 has_visual_loss=%s block_order=%s",
        AGENT2_LOG_PREFIX,
        customer_value_input.has_visual_loss_exposure,
        customer_value_input.block_order_channel,
    )
    return evaluate_customer_value(customer_value_input)


# ---------- 恶意评分与分级常量（分数展示 + 信号下限双轨） ----------
HARD_RULE_SCORE = 20
SEMANTIC_SCORE_ALLOWED = frozenset({10, 20, 30})
MALICIOUS_SEMANTIC_SIGNAL_TYPES = frozenset(
    {
        "review_blackmail",
        "identity_impersonation",
        "evidence_contradiction",
        "professional_claim_pattern",
        "abuse_refund_intent_chat",
    }
)
RISK_SCORE_CAP = 100
RISK_LEVEL_SCORE_MEDIUM = 15
RISK_LEVEL_SCORE_HIGH = 35
STRONG_MALICIOUS_SIGNAL_TYPES = frozenset(
    {
        "deceptive_credential",
        "statement_evidence_mismatch",
        "review_blackmail",
        "identity_impersonation",
        "evidence_contradiction",
    }
)
_RISK_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


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


def _facts_has_statement_contradiction_clues(facts: FactOutput) -> bool:
    """硬规则：结构化物流/收货状态与陈述不一致。"""
    if facts.logistics_normal is False and facts.goods_received is True:
        return True
    return False


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
                score=HARD_RULE_SCORE,
                source="hard_rule",
            )
        )

    if profile.purchase_count >= batch_order_purchase_threshold and profile.dispute_rate > batch_order_dispute_rate_threshold:
        signals.append(
            _make_malicious_signal(
                signal_type="batch_malicious_orders",
                description="购买频次高且纠纷率异常，疑似批量恶意下单",
                score=HARD_RULE_SCORE,
                source="hard_rule",
            )
        )

    if input_data.freight_insurance_used and profile.return_rate >= category_avg * high_return_rate_multiple_for_insurance:
        signals.append(
            _make_malicious_signal(
                signal_type="freight_insurance_abuse",
                description="运费险使用与高退货率叠加，疑似骗取运费险",
                score=HARD_RULE_SCORE,
                source="hard_rule",
            )
        )

    if input_data.swap_flag_count >= swap_flag_threshold:
        signals.append(
            _make_malicious_signal(
                signal_type="swap_or_missing_items",
                description=f"历史调包/少件标记达到{input_data.swap_flag_count}次",
                score=HARD_RULE_SCORE,
                source="hard_rule",
            )
        )

    if input_data.order_address and facts.logistics_normal is False:
        signals.append(
            _make_malicious_signal(
                signal_type="abnormal_return_address",
                description="存在地址信息且物流状态异常，疑似退货地址异常",
                score=HARD_RULE_SCORE,
                source="hard_rule",
            )
        )

    if input_data.related_account_count >= related_account_threshold:
        signals.append(
            _make_malicious_signal(
                signal_type="related_accounts",
                description=f"关联账号数量达到{input_data.related_account_count}，疑似多账号协同",
                score=HARD_RULE_SCORE,
                source="hard_rule",
            )
        )

    # 本单举证欺骗硬规则：仅消费 Agent1 视觉写入的 credential_trust=suspect。
    if facts_has_deceptive_credential_clues(facts):
        note = str(getattr(facts, "credential_trust_note", None) or "").strip()
        description = note or "视觉模型判定买家举证图片来源可疑（网图/非实拍/伪造等）"
        signals.append(
            _make_malicious_signal(
                signal_type="deceptive_credential",
                description=description,
                score=HARD_RULE_SCORE,
                source="hard_rule",
            )
        )

    if _facts_has_statement_contradiction_clues(facts):
        signals.append(
            _make_malicious_signal(
                signal_type="statement_evidence_mismatch",
                description="事实层存在陈述与物流/收货/举证结论不一致线索",
                score=HARD_RULE_SCORE,
                source="hard_rule",
            )
        )

    return signals


def _build_hard_rule_summary(hard_signals: List[MaliciousSignal]) -> str:
    """构建硬规则层摘要，供语义层校验与日志复用。"""
    if not hard_signals:
        return "硬规则层未命中异常项。"
    return "；".join([f"{item.signal_type}:{item.description}" for item in hard_signals])


# ---------- 恶意信号类型 → 中文短名（风险提示区与日志可读性） ----------
_MALICIOUS_SIGNAL_TYPE_CN: dict[str, str] = {
    "deceptive_credential": "举证来源可疑（本单事实）",
    "statement_evidence_mismatch": "陈述与事实矛盾（本单）",
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
    "abuse_refund_intent_chat": "聊天暴露高频套利/仅退意图",
}


def _format_malicious_risk_hints(signals: List[MaliciousSignal]) -> str:
    """
    将硬规则与语义层命中信号格式化为「风险提示」多行文案。

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


# ---------- 恶意语义 LLM：跳过条件、facts 摘要与 prompt 截断 ----------
MALICIOUS_CHAT_HISTORY_MAX = 6
MALICIOUS_SEMANTIC_CHAT_MIN_CHARS = 12
MALICIOUS_FACT_RED_FLAGS_MAX = 4
MALICIOUS_FACT_VISUAL_OBS_MAX = 3


def _build_malicious_facts_summary(facts: FactOutput) -> dict[str, Any]:
    """构造恶意语义层用 facts 摘要，去掉 evidence_items / rule_match_plan。"""
    summary: dict[str, Any] = {
        "issue_summary": facts.issue_summary,
        "defect_type": facts.defect_type,
        "evidence_quality": facts.evidence_quality,
        "credential_trust": facts.credential_trust,
        "red_flags": list((facts.red_flags or [])[:MALICIOUS_FACT_RED_FLAGS_MAX]),
        "visual_observations": pick_balanced_visual_observations(
            facts,
            max_items=MALICIOUS_FACT_VISUAL_OBS_MAX,
        ),
    }
    if facts.credential_trust_note:
        summary["credential_trust_note"] = facts.credential_trust_note
    if facts.goods_received is not None:
        summary["goods_received"] = facts.goods_received
    return summary


def _should_skip_malicious_semantic_llm(
    input_data: MaliciousDetectionInput,
    hard_signals: List[MaliciousSignal],
) -> bool:
    """
    低材料 case 跳过语义 LLM：无硬规则、无事实疑点、且无足够聊天文本时跳过。
    """
    if hard_signals:
        return False
    facts = input_data.facts
    if facts_has_deceptive_credential_clues(facts):
        return False
    if _facts_has_statement_contradiction_clues(facts):
        return False
    chat_lines = [str(item).strip() for item in (input_data.chat_history or []) if str(item).strip()]
    if not chat_lines:
        return True
    merged = " ".join(chat_lines)
    if len(merged) >= MALICIOUS_SEMANTIC_CHAT_MIN_CHARS:
        return False
    return True


def _strip_markdown_json(text: str) -> str:
    """清理 markdown 代码块外壳，提升 JSON 解析稳定性。"""
    content = text.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3:
            content = "\n".join(lines[1:-1]).strip()
    return content


def _parse_llm_json_array(llm_text: str) -> list[Any]:
    """
    解析 LLM 返回的 JSON 数组；容忍数组后附带解释文字（Extra data）。

    恶意语义层要求根节点为数组，仅取首个完整 JSON 值。
    """
    content = _strip_markdown_json(llm_text)
    decoder = json.JSONDecoder()
    start = content.find("[")
    if start < 0:
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            raise ValueError("根节点不是 JSON 数组")
        return parsed
    parsed, _end = decoder.raw_decode(content[start:])
    if not isinstance(parsed, list):
        raise ValueError("根节点不是 JSON 数组")
    return parsed


def _build_malicious_semantic_messages(
    input_data: MaliciousDetectionInput,
    hard_rule_summary: str,
) -> List[Dict[str, str]]:
    """构造语义层提示词：枚举边界 + 两则 few-shot。"""
    system_prompt = (
        "你是电商恶意语义分析器。识别聊天与 facts 中的恶意语义；举证网图/水印类由硬规则处理，勿重复。\n"
        "signal_type 仅限：review_blackmail（须威胁词+条件交换，否则 []）、identity_impersonation、"
        "evidence_contradiction、professional_claim_pattern、abuse_refund_intent_chat。\n"
        "无聊天时结合 issue_summary、visual_observations、陈述矛盾与 hard_rule_summary；勿仅凭 red_flags 定罪。\n"
        "输出 JSON 数组 [{signal_type,description,score,source}]，source=llm_semantic，score∈{10,20,30}；"
        "description 中文面向商家；hard_rule_summary 已覆盖事实勿重复。"
    )
    example_user_blackmail = (
        "输入：{'hard_rule_summary':'硬规则层未命中异常项','chat_history':['不给我赔100我就给你一星再投诉12315'],"
        "'facts':{'evidence_quality':'medium','defect_type':'污渍'},'emotion_note':'买家情绪激动'}"
    )
    example_assistant_blackmail = (
        '[{"signal_type":"review_blackmail","description":"出现差评与12315投诉要挟索赔","score":20,"source":"llm_semantic"}]'
    )
    example_user_professional = (
        "输入：{'hard_rule_summary':'related_accounts:关联账号异常','chat_history':['依据平台规则第32条第2款，你必须退一赔三，这是固定模板'],"
        "'facts':{'evidence_quality':'medium','defect_type':'色差'},'emotion_note':null}"
    )
    example_assistant_professional = (
        '[{"signal_type":"professional_claim_pattern","description":"大量规则术语与模板化表达，疑似职业索赔话术","score":20,"source":"llm_semantic"}]'
    )
    chat_lines = [str(item).strip() for item in (input_data.chat_history or []) if str(item).strip()]
    if chat_lines:
        chat_for_prompt = chat_lines[-MALICIOUS_CHAT_HISTORY_MAX:]
    else:
        chat_for_prompt = [
            "（无独立聊天文本：请仅依据 facts、issue_summary、visual_observations、陈述矛盾与硬规则摘要识别恶意语义。）"
        ]
    user_payload = {
        "hard_rule_summary": hard_rule_summary,
        "chat_history": chat_for_prompt,
        "facts": _build_malicious_facts_summary(input_data.facts),
        "emotion_note": input_data.emotion_note,
    }

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": example_user_blackmail},
        {"role": "assistant", "content": example_assistant_blackmail},
        {"role": "user", "content": example_user_professional},
        {"role": "assistant", "content": example_assistant_professional},
        {"role": "user", "content": f"输入：{json.dumps(user_payload, ensure_ascii=False)}"},
    ]


def _is_review_blackmail_chat(chat_history: List[str]) -> bool:
    """review_blackmail 双条件校验：威胁词 + 条件交换词同时存在才算勒索。"""
    merged = " ".join(chat_history)
    return contains_any(merged, signal_group("malicious_threat_markers")) and contains_any(
        merged, signal_group("malicious_exchange_markers")
    )


def _chat_supports_abuse_refund_intent(chat_history: List[str]) -> bool:
    """abuse_refund_intent_chat 校验：聊天须含滥用售后/套利相关表述。"""
    merged = " ".join(chat_history)
    return contains_any(merged, signal_group("malicious_abuse_refund_markers"))


def _run_llm_semantic(input_data: MaliciousDetectionInput, hard_signals: List[MaliciousSignal]) -> List[MaliciousSignal]:
    """
    第二层语义分析：在硬规则结果基础上补充威胁与矛盾类风险信号。
    """
    if not os.getenv("AGENT2_LLM_MODEL_MALICIOUS", "").strip() and not os.getenv("AGENT2_LLM_MODEL", "").strip():
        logger.info("%s 未配置 AGENT2_LLM_MODEL_MALICIOUS，语义层跳过，仅保留硬规则层结果", AGENT2_LOG_PREFIX)
        return []

    if _should_skip_malicious_semantic_llm(input_data=input_data, hard_signals=hard_signals):
        logger.info("%s 恶意语义层跳过：低材料 case 无需 LLM", AGENT2_LOG_PREFIX)
        return []

    hard_rule_summary = _build_hard_rule_summary(hard_signals)
    messages = _build_malicious_semantic_messages(input_data=input_data, hard_rule_summary=hard_rule_summary)
    call_start = time.perf_counter()
    llm_text = chat_completion(
        messages=messages,
        model_env_key="AGENT2_LLM_MODEL_MALICIOUS",
        fallback_model_env_key="AGENT2_LLM_MODEL",
        temperature=0.0,
    )
    prompt_chars = sum(len(str(item.get("content", ""))) for item in messages)
    logger.info(
        "%s 恶意语义 LLM 完成 elapsed_ms=%s prompt_chars=%s",
        AGENT2_LOG_PREFIX,
        int((time.perf_counter() - call_start) * 1000),
        prompt_chars,
    )
    if not llm_text:
        raise RuntimeError("恶意语义分析失败：LLM 无返回内容")

    try:
        parsed = _parse_llm_json_array(llm_text)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"恶意语义分析失败：JSON 解析异常，原因：{exc}") from exc

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
        if signal_type not in MALICIOUS_SEMANTIC_SIGNAL_TYPES:
            logger.info("%s 语义层忽略未授权 signal_type=%s", AGENT2_LOG_PREFIX, signal_type)
            continue
        try:
            score = int(score_raw)
        except Exception:  # noqa: BLE001
            continue
        if score not in SEMANTIC_SCORE_ALLOWED:
            logger.info("%s 语义层忽略非法 score=%s（仅允许 10/20/30）", AGENT2_LOG_PREFIX, score_raw)
            continue
        if signal_type == "review_blackmail" and not _is_review_blackmail_chat(input_data.chat_history):
            logger.info("%s review_blackmail 未通过双条件校验，按情绪激动处理，不计入恶意分", AGENT2_LOG_PREFIX)
            continue
        if signal_type == "abuse_refund_intent_chat" and not _chat_supports_abuse_refund_intent(
            input_data.chat_history or []
        ):
            logger.info(
                "%s abuse_refund_intent_chat 未通过聊天原文校验（无滥用售后/套利表述），忽略该语义信号",
                AGENT2_LOG_PREFIX,
            )
            continue
        signals.append(_make_malicious_signal(signal_type, description, score, "llm_semantic"))
    return signals


def _risk_level_from_score(risk_score: int) -> str:
    """
    根据综合分映射风险等级（分数轨）。
    """
    if risk_score >= RISK_LEVEL_SCORE_HIGH:
        return "high"
    if risk_score >= RISK_LEVEL_SCORE_MEDIUM:
        return "medium"
    return "low"


def _max_risk_level(level_a: str, level_b: str) -> str:
    """取两等级中较高者。"""
    if _RISK_LEVEL_ORDER.get(level_a, 0) >= _RISK_LEVEL_ORDER.get(level_b, 0):
        return level_a
    return level_b


def _risk_level_floor_from_signals(signals: List[MaliciousSignal]) -> str:
    """
    信号轨：强恶意类型命中时抬高等级下限，避免「单条满分仍 low」。
    """
    if not signals:
        return "low"

    def _max_score_for(signal_type: str) -> int:
        return max((item.score for item in signals if item.signal_type == signal_type), default=0)

    strong_hits = [
        item
        for item in signals
        if item.signal_type in STRONG_MALICIOUS_SIGNAL_TYPES and item.score >= 10
    ]
    very_strong_hits = [item for item in strong_hits if item.score >= 20]

    if _max_score_for("identity_impersonation") >= 10:
        return "high"
    if len(very_strong_hits) >= 2:
        return "high"
    if _max_score_for("review_blackmail") >= 20:
        return "high"
    if _max_score_for("deceptive_credential") >= HARD_RULE_SCORE:
        return "high"
    if any(item.score >= 30 for item in strong_hits):
        return "high"

    if strong_hits:
        return "medium"
    if _max_score_for("professional_claim_pattern") >= 20:
        return "medium"
    if _max_score_for("abuse_refund_intent_chat") >= 20:
        return "medium"
    return "low"


def _resolve_risk_level(risk_score: int, signals: List[MaliciousSignal]) -> str:
    """综合分轨与信号轨取较高等级。"""
    return _max_risk_level(_risk_level_from_score(risk_score), _risk_level_floor_from_signals(signals))


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

    risk_total_score = min(RISK_SCORE_CAP, sum(signal.score for signal in all_signals))
    risk_level = _resolve_risk_level(risk_total_score, all_signals)
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


# ---------- 智能模式轻量入口：供 conversation_agent function calling 使用 ----------

def _build_default_facts(description: str) -> FactOutput:
    """从纠纷描述文本构建最小 FactOutput，仅供轻量入口内部使用。"""
    return FactOutput(
        issue_summary=description,
        intent_tags=[],
        evidence_quality=EVIDENCE_LOW,
        confidence=0.3,
        missing_evidence=["买家举证图片", "商品实物照片"],
    )


def _build_default_buyer_profile_simple(buyer_id: str) -> BuyerProfile:
    """轻量入口用的默认画像。"""
    return _build_default_buyer_profile(buyer_id)


def evaluate_customer_value_simple(
    buyer_id: str,
    merchant_id: str = "",
    order_amount: float = 0.0,
) -> CustomerValueOutput:
    """
    轻量客户价值评估：仅需 buyer_id + merchant_id + order_amount，内部构建完整输入。

    参数:
        buyer_id: 买家脱敏ID。
        merchant_id: 商家ID。
        order_amount: 订单金额。

    返回:
        CustomerValueOutput 评分结果。
    """
    profile = query_buyer_profile(buyer_id=buyer_id, merchant_id=merchant_id)
    cv_input = CustomerValueInput(
        buyer_profile=profile,
        order_amount=max(0.0, order_amount),
        has_visual_loss_exposure=False,
        block_order_channel=False,
    )
    logger.info("%s 轻量客户价值评估开始 buyer_id=%s order_amount=%s", AGENT2_LOG_PREFIX, buyer_id, order_amount)
    return evaluate_customer_value(cv_input)


def detect_malicious_simple(
    chat_history: List[str],
    buyer_id: str = "",
    merchant_id: str = "",
    order_amount: float = 0.0,
    description: str = "",
    facts: FactOutput | None = None,
) -> MaliciousDetectionOutput:
    """
    轻量恶意检测：从聊天记录与描述构建最小输入，内部调用完整检测链路。

    参数:
        chat_history: 聊天记录文本列表。
        buyer_id: 买家脱敏ID（可选，用于查画像）。
        merchant_id: 商家ID（可选）。
        order_amount: 订单金额。
        description: 纠纷描述。
        facts: 上游已有的 FactOutput（可选）。传入时优先使用，避免用默认空事实
               导致 credential_trust / logistics_normal / red_flags 等字段全部丢失。

    返回:
        MaliciousDetectionOutput。
    """
    profile = query_buyer_profile(buyer_id=buyer_id, merchant_id=merchant_id) if buyer_id else _build_default_buyer_profile("")
    resolved_facts = facts if facts is not None else _build_default_facts(description or "（无描述）")
    md_input = MaliciousDetectionInput(
        buyer_profile=profile,
        facts=resolved_facts,
        order_amount=max(0.0, order_amount),
        chat_history=[str(s).strip() for s in (chat_history or []) if str(s).strip()],
    )
    logger.info("%s 轻量恶意检测开始 buyer_id=%s", AGENT2_LOG_PREFIX, buyer_id)
    return detect_malicious_behavior(md_input)


def search_similar_cases_simple(description: str, top_k: int = 3) -> List[SimilarCase]:
    """
    轻量相似判例检索入口，直接透传。

    参数:
        description: 纠纷描述文本。
        top_k: 返回条数上限。

    返回:
        SimilarCase 列表。
    """
    return search_similar_cases(dispute_desc=description, top_k=top_k)
