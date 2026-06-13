"""
商家应诉助手 - 全局数据结构定义
所有 Agent 的输入输出必须引用此文件，禁止自行发明字段。

版本：v1.0
"""

from pydantic import BaseModel, Field
from typing import Any, Optional, List


# ============================================================
# 一、基础枚举
# ============================================================

# 处置方向枚举（内部英文）
DISPOSITION_DEFEND = "defend"
DISPOSITION_NEGOTIATE = "negotiate"
DISPOSITION_COMPENSATE = "compensate"
VALID_DISPOSITIONS = [DISPOSITION_DEFEND, DISPOSITION_NEGOTIATE, DISPOSITION_COMPENSATE]

# 证据质量枚举（内部英文）
EVIDENCE_HIGH = "high"
EVIDENCE_MEDIUM = "medium"
EVIDENCE_LOW = "low"
VALID_EVIDENCE_QUALITY = [EVIDENCE_HIGH, EVIDENCE_MEDIUM, EVIDENCE_LOW]

# 举证可信度（仅 Agent1 视觉链路写入；下游只读此字段，不再用关键词复判网图）
CREDENTIAL_TRUST_UNKNOWN = "unknown"
CREDENTIAL_TRUST_TRUSTED = "trusted"
CREDENTIAL_TRUST_SUSPECT = "suspect"
VALID_CREDENTIAL_TRUST = [
    CREDENTIAL_TRUST_UNKNOWN,
    CREDENTIAL_TRUST_TRUSTED,
    CREDENTIAL_TRUST_SUSPECT,
]

# 争议主框架（Agent1 单点判定，下游只读）
DISPUTE_FRAME_UNKNOWN = "unknown"
DISPUTE_FRAME_SEVEN_DAY_RETURN = "seven_day_return"
DISPUTE_FRAME_QUALITY_DEFECT = "quality_defect"
DISPUTE_FRAME_DESCRIPTION_MISMATCH = "description_mismatch"
DISPUTE_FRAME_LOGISTICS = "logistics"
VALID_DISPUTE_FRAMES = [
    DISPUTE_FRAME_UNKNOWN,
    DISPUTE_FRAME_SEVEN_DAY_RETURN,
    DISPUTE_FRAME_QUALITY_DEFECT,
    DISPUTE_FRAME_DESCRIPTION_MISMATCH,
    DISPUTE_FRAME_LOGISTICS,
]

# 无质量主张时，不因缺证清单直接进入 quality 举证阶段；且非商责时禁止主动金额和解（可扩展）
FRAMES_SKIP_QUALITY_EVIDENCE_GATE = frozenset({DISPUTE_FRAME_SEVEN_DAY_RETURN})
FRAMES_NO_MONETARY_SETTLE = FRAMES_SKIP_QUALITY_EVIDENCE_GATE

# 话术应对思想（Agent 3 输出）
RESPONSE_MODE_MERCHANT_FAULT = "merchant_fault"
RESPONSE_MODE_MALICIOUS_RISK = "malicious_risk"
RESPONSE_MODE_NEUTRAL_NEGOTIATE = "neutral_negotiate"
VALID_RESPONSE_MODES = [
    RESPONSE_MODE_MERCHANT_FAULT,
    RESPONSE_MODE_MALICIOUS_RISK,
    RESPONSE_MODE_NEUTRAL_NEGOTIATE,
]

# 策略阶段（Agent 2 输出；Agent 3 仅用于 usage_tip 与举证阶段金额门禁）
STRATEGY_STAGE_EVIDENCE_FIRST = "evidence_first"
STRATEGY_STAGE_NEGOTIATE_SETTLE = "negotiate_settle"
STRATEGY_STAGE_COMPENSATE_CLOSE = "compensate_close"
STRATEGY_STAGE_DEFEND_PLATFORM = "defend_platform"
VALID_STRATEGY_STAGES = [
    STRATEGY_STAGE_EVIDENCE_FIRST,
    STRATEGY_STAGE_NEGOTIATE_SETTLE,
    STRATEGY_STAGE_COMPENSATE_CLOSE,
    STRATEGY_STAGE_DEFEND_PLATFORM,
]

# 当前动作类型（Agent 2 _infer_action_contract 产出，Agent 3 严格执行）
ACTION_RULE_EXPLAIN = "rule_explain"
ACTION_EVIDENCE_REQUEST = "evidence_request"
ACTION_RETURN_INSPECTION = "return_inspection"
ACTION_MERCHANT_REMEDY = "merchant_remedy"
ACTION_MONETARY_SETTLE = "monetary_settle"
ACTION_DEFEND_PREPARE = "defend_prepare"
VALID_ACTION_TYPES = [
    ACTION_RULE_EXPLAIN,
    ACTION_EVIDENCE_REQUEST,
    ACTION_RETURN_INSPECTION,
    ACTION_MERCHANT_REMEDY,
    ACTION_MONETARY_SETTLE,
    ACTION_DEFEND_PREPARE,
]

# 补偿门禁（Agent 2 动作契约产出，Agent 3 直接消费）
COMPENSATION_POLICY_FORBID = "forbid"
COMPENSATION_POLICY_NONE = "none"
COMPENSATION_POLICY_SOFT_NO_AMOUNT = "soft_no_amount"
COMPENSATION_POLICY_EXPLICIT_AMOUNT = "explicit_amount"
VALID_COMPENSATION_POLICIES = [
    COMPENSATION_POLICY_FORBID,
    COMPENSATION_POLICY_NONE,
    COMPENSATION_POLICY_SOFT_NO_AMOUNT,
    COMPENSATION_POLICY_EXPLICIT_AMOUNT,
]

# 责任归属枚举
RESPONSIBILITY_MERCHANT = "merchant_fault"
RESPONSIBILITY_BUYER = "buyer_fault"
RESPONSIBILITY_UNCLEAR = "unclear"
RESPONSIBILITY_MIXED = "mixed"
VALID_RESPONSIBILITIES = [
    RESPONSIBILITY_MERCHANT,
    RESPONSIBILITY_BUYER,
    RESPONSIBILITY_UNCLEAR,
    RESPONSIBILITY_MIXED,
]


# ============================================================
# 二、共享数据结构
# ============================================================

class BuyerProfile(BaseModel):
    """买家画像 — 来自商家自有订单数据"""
    buyer_id: str = Field(..., description="买家脱敏ID（手机号SHA256哈希）")
    purchase_count: int = Field(default=0, description="在本店累计购买次数")
    dispute_count: int = Field(default=0, description="在本店历史纠纷次数")
    dispute_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="本店纠纷率")
    avg_order_value: float = Field(default=0.0, description="本店平均客单价")
    return_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="本店退货率")
    malicious_flags: int = Field(default=0, description="被标记恶意次数（脱敏计数）")
    positive_review_count: int = Field(default=0, description="在本店累计好评/带图评价次数")
    credit_level: Optional[str] = Field(default=None, description="平台信誉等级")


class LogisticsInfo(BaseModel):
    """物流信息 — 来自平台API"""
    is_shipped: bool = Field(default=False, description="是否已发货")
    is_signed: bool = Field(default=False, description="是否已签收")
    stagnant_days: int = Field(default=0, description="物流停滞天数")
    is_abnormal: bool = Field(default=False, description="物流是否异常")


# 规则匹配相关常量
RULE_RELEVANCE_MUST = "must"
RULE_RELEVANCE_SHOULD = "should"
RULE_RELEVANCE_WEAK = "weak"
VALID_RULE_RELEVANCE = [RULE_RELEVANCE_MUST, RULE_RELEVANCE_SHOULD, RULE_RELEVANCE_WEAK]

RULE_STANCE_MERCHANT = "merchant"
RULE_STANCE_BUYER = "buyer"
RULE_STANCE_NEUTRAL = "neutral"

RULE_CONSTRAINT_TIMING = "timing"
RULE_CONSTRAINT_EVIDENCE = "evidence"
RULE_CONSTRAINT_RATIO_LIMIT = "ratio_limit"
RULE_CONSTRAINT_PROCESS = "process"
RULE_CONSTRAINT_NO_PROMISE = "no_promise"
RULE_CONSTRAINT_OTHER = "other"
VALID_RULE_CONSTRAINT_TYPES = [
    RULE_CONSTRAINT_TIMING,
    RULE_CONSTRAINT_EVIDENCE,
    RULE_CONSTRAINT_RATIO_LIMIT,
    RULE_CONSTRAINT_PROCESS,
    RULE_CONSTRAINT_NO_PROMISE,
    RULE_CONSTRAINT_OTHER,
]

RULE_CONSTRAINT_APPLIES = "applies"
RULE_CONSTRAINT_VIOLATED = "violated"
RULE_CONSTRAINT_MISSING_FACT = "missing_fact"
RULE_CONSTRAINT_CONFLICT = "conflict"
VALID_RULE_CONSTRAINT_STATUSES = [
    RULE_CONSTRAINT_APPLIES,
    RULE_CONSTRAINT_VIOLATED,
    RULE_CONSTRAINT_MISSING_FACT,
    RULE_CONSTRAINT_CONFLICT,
]


# ============================================================
# 三、Agent 1 — 事实还原员
# ============================================================

class RuleSearchTerms(BaseModel):
    """规则篇内检索词（规则词为主，案情词为辅）"""
    must_terms: List[str] = Field(default_factory=list, description="规则正文用语，篇内匹配主力")
    should_terms: List[str] = Field(default_factory=list, description="补充规则词，权重较低")
    case_terms: List[str] = Field(default_factory=list, description="买家案情用语，仅辅助")
    exclude_terms: List[str] = Field(default_factory=list, description="命中则剔除该条")


class SectionSelection(BaseModel):
    """单文档内选中的节/条块"""
    doc_id: str = Field(..., description="平台规则文档 doc_id")
    section_keys: List[str] = Field(default_factory=list, description="lexicon section_key 列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="本节选择置信度")
    reason: str = Field(default="", description="选择理由（简短）")


class RuleMatchPlan(BaseModel):
    """Agent1 输出的规则导航计划，供 match_rules 执行"""
    activated_lanes: List[str] = Field(default_factory=list, description="激活通道：A/B/C/D/E/F/G/H/I")
    target_doc_ids: List[str] = Field(default_factory=list, description="待检索的规范 doc_id 列表")
    section_selections: List[SectionSelection] = Field(default_factory=list, description="各 doc 选中的节")
    search_terms: RuleSearchTerms = Field(default_factory=RuleSearchTerms, description="篇内检索词")
    category_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="品类规范激活置信度")
    service_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="服务保障激活置信度")


class FactOutput(BaseModel):
    """Agent 1 输出：结构化事实"""
    issue_summary: Optional[str] = Field(default=None, description="买家核心诉求摘要（面向多模态分析的锚点）")
    intent_tags: List[str] = Field(default_factory=list, description="诉求标签（如：质量问题/物流异常/补偿诉求）")
    visual_observations: List[str] = Field(default_factory=list, description="视觉观察结论列表（自然语言短句）")
    visual_defect_severity: Optional[str] = Field(
        default=None,
        description="视觉损失暴露：问题严重度 minor/moderate/severe；无有效举证图为 null",
    )
    visual_goods_recoverability: Optional[str] = Field(
        default=None,
        description="视觉损失暴露：商品可挽回性 resalable/repairable/unrecoverable；无有效举证图为 null",
    )
    attributes: dict[str, Any] = Field(default_factory=dict, description="可扩展属性容器，存放品类相关细节")
    evidence_items: List[dict[str, Any]] = Field(default_factory=list, description="证据项列表（文本/图片/视频等）")
    goods_received: Optional[bool] = Field(default=None, description="买家是否收到货")
    defect_type: Optional[str] = Field(default=None, description="瑕疵类型：破洞/污渍/色差/线头/功能故障/无瑕疵")
    defect_location: Optional[str] = Field(default=None, description="瑕疵位置描述")
    defect_edge: Optional[str] = Field(default=None, description="兼容字段：破损边缘形态")
    has_tag_visible: Optional[bool] = Field(default=None, description="兼容字段：吊牌是否可见")
    photo_background: Optional[str] = Field(default=None, description="兼容字段：拍摄背景环境")
    wear_signs: Optional[str] = Field(default=None, description="兼容字段：穿着/使用痕迹描述")
    logistics_normal: Optional[bool] = Field(default=None, description="物流是否正常")
    missing_evidence: List[str] = Field(default_factory=list, description="缺失的证据项")
    red_flags: List[str] = Field(default_factory=list, description="发现的疑点")
    credential_trust: str = Field(
        default=CREDENTIAL_TRUST_UNKNOWN,
        description="举证图片可信度：unknown 无图或未判；trusted 视觉判定像本单实拍；suspect 视觉判定像网图/非实拍/伪造",
    )
    credential_trust_note: Optional[str] = Field(
        default=None,
        description="视觉模型对 credential_trust 的一句理由，供商家与策略参考",
    )
    primary_dispute_frame: str = Field(
        default=DISPUTE_FRAME_UNKNOWN,
        description="主争议框架：seven_day_return/quality_defect/description_mismatch/logistics/unknown",
    )
    evidence_quality: str = Field(default=EVIDENCE_MEDIUM, description="证据覆盖度：high/medium/low。文本+图+物流三类材料有几类，不直接代表可决策程度")
    decision_readiness: str = Field(
        default=EVIDENCE_LOW,
        description="可决策度：high/medium/low。综合视觉结论、举证可信度、缺证、缺陷严重度，判断事实是否足以支撑终局决策（退款/补偿/拒赔）",
    )
    decision_readiness_note: Optional[str] = Field(
        default=None,
        description="可决策度判定理由，一句自然语言",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="事实提取置信度")
    uncertainty_note: Optional[str] = Field(default=None, description="当某些事实无法确定时，用自然语言说明原因")
    rule_match_plan: RuleMatchPlan = Field(default_factory=RuleMatchPlan, description="规则匹配导航计划")

# ============================================================
# 四、Agent 2 — 策略参谋员
# ============================================================

class MatchedRule(BaseModel):
    """匹配到的平台规则"""
    rule_id: str = Field(..., description="规则编号（doc_id::article_no）")
    rule_summary: str = Field(..., description="规则摘要")
    condition_result: str = Field(..., description="条件匹配结果描述")
    relevance: str = Field(default=RULE_RELEVANCE_SHOULD, description="must/should/weak")
    doc_id: str = Field(default="", description="来源文档 doc_id")
    section_key: str = Field(default="", description="来源节 section_key")
    article_no: str = Field(default="", description="条号")
    stance_hint: str = Field(default=RULE_STANCE_NEUTRAL, description="merchant/buyer/neutral")
    matched_conditions: List[str] = Field(default_factory=list, description="已满足或直接命中的规则条件")
    violated_or_missing_conditions: List[str] = Field(default_factory=list, description="未满足或仍缺事实核验的规则条件")
    strategy_constraints: List[str] = Field(default_factory=list, description="由该条规则推导出的策略硬约束")


class RuleConstraint(BaseModel):
    """规则匹配后传递给策略/话术的结构化约束"""
    constraint_type: str = Field(
        default=RULE_CONSTRAINT_OTHER,
        description=f"约束类型：{'/'.join(VALID_RULE_CONSTRAINT_TYPES)}",
    )
    text: str = Field(default="", description="面向策略和话术的可执行约束文本")
    status: str = Field(
        default=RULE_CONSTRAINT_APPLIES,
        description=f"约束状态：{'/'.join(VALID_RULE_CONSTRAINT_STATUSES)}",
    )
    source_rule_id: str = Field(default="", description="来源规则 rule_id")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="约束提取置信度")


class RuleBrief(BaseModel):
    """供策略 LLM 使用的规则 brief"""
    article_ref: str = Field(..., description="条号引用，如 第六十五条")
    brief: str = Field(..., description="1～2 句要点")
    relevance: str = Field(default=RULE_RELEVANCE_SHOULD, description="must/should")
    stance_hint: str = Field(default=RULE_STANCE_NEUTRAL, description="merchant/buyer/neutral")
    strategy_constraints: List[str] = Field(default_factory=list, description="该 brief 对应的策略约束")


class RuleMatchResult(BaseModel):
    """match_rules 完整输出"""
    matched_rules: List[MatchedRule] = Field(default_factory=list, description="命中池（最多 cap）")
    rule_briefs: List[RuleBrief] = Field(default_factory=list, description="策略 LLM 用 brief 列表")
    display_rules: List[MatchedRule] = Field(default_factory=list, description="前端代表条 3～5 条")
    rule_constraints: List[RuleConstraint] = Field(default_factory=list, description="结构化规则约束，供策略与话术执行")


class SimilarCase(BaseModel):
    """相似历史判例"""
    case_id: str = Field(..., description="案例编号")
    similarity: float = Field(default=0.0, ge=0.0, le=1.0, description="相似度")
    merchant_action: str = Field(..., description="当时商家采取的行动")
    outcome: str = Field(..., description="当时的结果")
    lesson: str = Field(..., description="提炼的经验")


class ChatTurn(BaseModel):
    """单条聊天轮次 — 供策略/话术续写"""
    role: str = Field(..., description="buyer 或 merchant")
    content: str = Field(default="", description="消息正文")


class DialogueContext(BaseModel):
    """对话语境 — 由 Agent2 策略 LLM 输出，供 Agent3 话术续写"""
    dialogue_mode: str = Field(default="cold_start", description="continue 或 cold_start")
    blocked_evidence_requests: List[str] = Field(
        default_factory=list,
        description="买家已明确无法/不愿提供的举证，后续禁止再提",
    )
    actionable_evidence_requests: List[str] = Field(
        default_factory=list,
        description="仍可向买家请求的替代举证方向",
    )
    fallback_script: str = Field(default="", description="话术 LLM 失败时的备用话术")


class StrategyInput(BaseModel):
    """Agent 2 输入"""
    facts: FactOutput = Field(..., description="Agent 1 的输出")
    buyer_profile: BuyerProfile = Field(..., description="买家画像")
    matched_rules: List[MatchedRule] = Field(default_factory=list, description="匹配到的规则（代表条或全量）")
    rule_briefs: List[RuleBrief] = Field(default_factory=list, description="规则 brief，供策略 LLM")
    rule_constraints: List[RuleConstraint] = Field(default_factory=list, description="结构化规则约束，优先于文本摘要驱动策略")
    similar_cases: List[SimilarCase] = Field(default_factory=list, description="相似历史判例")
    order_amount: float = Field(default=0.0, description="纠纷订单金额")
    chat_history: List[str] = Field(default_factory=list, description="聊天记录文本列表（用于语义分析）")
    chat_turns: List[ChatTurn] = Field(default_factory=list, description="带角色的近期对话")
    emotion_note: Optional[str] = Field(default=None, description="Agent 4 情绪摘要（可选）")
    precomputed_customer_value: Optional["CustomerValueOutput"] = Field(
        default=None,
        description="Controller 预计算的客户价值结果（可选，传入则跳过 Agent2 内重复调用）",
    )
    precomputed_malicious_detection: Optional["MaliciousDetectionOutput"] = Field(
        default=None,
        description="Controller 预计算的恶意检测结果（可选，传入则跳过 Agent2 内重复调用）",
    )
    rule_match_skipped: bool = Field(
        default=False,
        description="简单案门控未触发条文匹配时为 true，置信度规则维按「刻意无规则」计分",
    )


class CustomerValueInput(BaseModel):
    """客户价值评估输入"""
    buyer_profile: BuyerProfile = Field(..., description="买家画像，长期价值评估主输入")
    order_amount: float = Field(default=0.0, description="当前纠纷订单金额")
    defect_severity: Optional[str] = Field(
        default=None,
        description="本单问题严重度（来自 Agent1 视觉 visual_defect_severity；无图则为 null）",
    )
    goods_recoverability: Optional[str] = Field(
        default=None,
        description="本单商品可挽回性（来自 Agent1 视觉 visual_goods_recoverability；无图则为 null）",
    )
    has_visual_loss_exposure: bool = Field(
        default=False,
        description="是否已有视觉损失暴露评估（两枚举均非空）",
    )
    block_order_channel: bool = Field(
        default=False,
        description="本单优待通道门槛：red_flags 或低证据缺证时为 true",
    )
    emotion_note: Optional[str] = Field(default=None, description="Agent 4 输出的情绪描述（可选）")


class CustomerValueScoreItem(BaseModel):
    """客户价值评估分项"""
    dimension: str = Field(..., description="评估维度名称")
    score: int = Field(default=0, description="该维度得分")
    max_score: int = Field(default=0, description="该维度满分")
    reason: str = Field(default="", description="该维度打分原因")


class CustomerValueOutput(BaseModel):
    """客户价值评估输出"""
    long_term_score: int = Field(default=0, description="长期价值总分（0-100）")
    order_score: int = Field(default=0, description="本单价值总分（0-85，满分 85 触发阈值 60）")
    long_term_breakdown: List[CustomerValueScoreItem] = Field(default_factory=list, description="长期价值分项")
    order_breakdown: List[CustomerValueScoreItem] = Field(default_factory=list, description="本单价值分项")
    long_term_triggered: bool = Field(default=False, description="是否触发长期客户优待通道")
    order_triggered: bool = Field(default=False, description="是否触发本单重点处理通道")
    channel: str = Field(default="none", description="触发通道：long_term/order/none")
    compensation_uplift: Optional[str] = Field(default=None, description="补偿上限提升幅度建议")
    tone_suggestion: Optional[str] = Field(default=None, description="话术温度建议")


class MaliciousSignal(BaseModel):
    """恶意行为信号分项"""
    signal_type: str = Field(..., description="信号类型标识，如 deceptive_credential / abuse_refund_only")
    description: str = Field(default="", description="触发描述（面向商家可读）")
    score: int = Field(default=0, description="本信号贡献分值")
    source: str = Field(default="hard_rule", description="信号来源：hard_rule / llm_semantic")


class MaliciousDetectionInput(BaseModel):
    """恶意行为检测输入"""
    buyer_profile: BuyerProfile = Field(..., description="买家画像")
    facts: FactOutput = Field(..., description="事实输出")
    order_amount: float = Field(default=0.0, description="本单金额")
    order_address: Optional[str] = Field(default=None, description="收货地址（可选）")
    recent_refund_only_count: int = Field(default=0, description="近期仅退款次数")
    return_rate_category_avg: float = Field(default=0.16, description="类目退货率均值")
    freight_insurance_used: bool = Field(default=False, description="是否使用运费险")
    swap_flag_count: int = Field(default=0, description="历史调包/少件标记次数")
    related_account_count: int = Field(default=0, description="关联账号数量")
    chat_history: List[str] = Field(default_factory=list, description="聊天记录文本列表")
    emotion_note: Optional[str] = Field(default=None, description="情绪摘要（可选）")


class MaliciousDetectionOutput(BaseModel):
    """恶意行为检测输出"""
    risk_score: int = Field(default=0, description="综合风险评分（0-100）")
    risk_level: str = Field(default="low", description="风险等级：low/medium/high")
    triggered_signals: List[MaliciousSignal] = Field(default_factory=list, description="触发信号明细")
    hard_rule_summary: str = Field(default="", description="硬规则层摘要（仅硬规则命中，供日志与兼容）")
    malicious_risk_hints: str = Field(
        default="",
        description="恶意风险提示聚合文案（硬规则+语义全部命中项，供前端「风险提示」区）",
    )
    disposition_advice: str = Field(default="", description="处置建议方向")


class StrategyOutput(BaseModel):
    """Agent 2 输出：策略建议"""
    disposition: str = Field(..., description=f"处置方向：{'/'.join(VALID_DISPOSITIONS)}")
    responsibility: str = Field(
        default=RESPONSIBILITY_UNCLEAR,
        description=f"责任归属判定：{'/'.join(VALID_RESPONSIBILITIES)}。由策略 LLM 综合规则、证据、画像、恶意信号等维度判定",
    )
    responsibility_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="责任判定置信度",
    )
    responsibility_rationale: str = Field(
        default="",
        description="责任判定理由，面向商家可读（2～3句）",
    )
    estimated_win_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="抗辩胜率（仅抗辩方向返回）")
    policy_ref: Optional[str] = Field(default=None, description="引用的平台规则条款")
    customer_intent_analysis: str = Field(
        default="",
        description="客户意图分析（核心结论区展示，可与 reasoning 中「客户意图」段对齐）",
    )
    strategy_direction_summary: str = Field(
        default="",
        description="策略方向：基于当前事实的单一路径局部动作（1～2句，禁止罗列多套备选方案）",
    )
    strategy_direction_rationale: str = Field(
        default="",
        description="推理理由：核心结论区展示，简述为何采取该策略方向（2～4句）",
    )
    platform_rule_basis: List[str] = Field(
        default_factory=list,
        description="平台规则依据：仅条文匹配 display_rules 的法条摘要列表（无条号/章节，不含程序性约束句）",
    )
    reasoning: str = Field(default="", description="完整策略说明（含客户意图/风险点/建议动作/推理理由四段，供流式与话术引用）")
    risk_factors: List[str] = Field(default_factory=list, description="风险因素列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="策略置信度")
    strategy_stage: str = Field(
        default=STRATEGY_STAGE_NEGOTIATE_SETTLE,
        description=f"策略阶段：{'/'.join(VALID_STRATEGY_STAGES)}",
    )
    action_type: str = Field(
        default=ACTION_RULE_EXPLAIN,
        description=f"当前动作类型：{'/'.join(VALID_ACTION_TYPES)}",
    )
    compensation_policy: str = Field(
        default=COMPENSATION_POLICY_NONE,
        description=f"补偿门禁：{'/'.join(VALID_COMPENSATION_POLICIES)}",
    )
    rule_constraints: List[str] = Field(
        default_factory=list,
        description="话术必须遵守的规则边界，例如未验收前不承诺退款或补偿",
    )
    structured_rule_constraints: List[RuleConstraint] = Field(
        default_factory=list,
        description="策略采用的结构化规则约束，供调试和下游消费",
    )
    next_step: str = Field(default="", description="买家或商家的下一步动作")
    customer_value: Optional[CustomerValueOutput] = Field(default=None, description="客户价值评估结果")
    malicious_detection: Optional[MaliciousDetectionOutput] = Field(default=None, description="恶意行为检测结果")
    dialogue_context: Optional[DialogueContext] = Field(
        default=None,
        description="对话语境（blocked/actionable 举证与 fallback 话术，供 Agent3 消费）",
    )


# ============================================================
# 五、Agent 3 — 话术生成员
# ============================================================

class ScriptInput(BaseModel):
    """Agent 3 输入"""
    strategy_output: StrategyOutput = Field(..., description="Agent 2 的输出")
    facts: FactOutput = Field(..., description="Agent 1 的输出")
    order_id: str = Field(..., description="订单号")
    order_amount: float = Field(default=0.0, description="订单金额")
    emotion_note: Optional[str] = Field(default=None, description="来自 Agent 4 的细腻情绪描述，用于优化话术语气")
    chat_history: List[ChatTurn] = Field(default_factory=list, description="近期对话轮次，供话术嵌入当下语境")

class ScriptOutput(BaseModel):
    """Agent 3 输出：单一推荐话术"""
    script: str = Field(default="", description="面向买家的推荐话术正文")
    response_mode: str = Field(
        default=RESPONSE_MODE_NEUTRAL_NEGOTIATE,
        description=f"应对思想：{'/'.join(VALID_RESPONSE_MODES)}",
    )
    usage_tip: Optional[str] = Field(default=None, description="话术使用提示")


# ============================================================
# 六、Agent 4 — 情绪监控员
# ============================================================

class EmotionOutput(BaseModel):
    alert_triggered: bool = Field(default=False, description="是否触发预警")
    alert_message: str = Field(default="", description="预警提示文本")
    alert_reason: str = Field(default="", description="触发原因")
    sentiment: str = Field(default="neutral", description="情绪标签：negative/neutral/positive")
    intensity: float = Field(default=0.0, ge=0.0, le=1.0, description="情绪强度")
    emotion_note: Optional[str] = Field(default=None, description="对当前情绪状态的细腻描述，例如：'顾客虽然同意方案，但仍有些勉强，希望尽快解决问题'")

# ============================================================
# 七、Agent 5 — 复盘分析师
# ============================================================

class ReviewInput(BaseModel):
    """Agent 5 输入"""
    dispute_id: str = Field(..., description="纠纷编号")
    full_timeline: dict = Field(..., description="完整纠纷轨迹（对话记录+AI各阶段建议+商家操作）")
    final_outcome: str = Field(..., description="最终结果：胜/败/和解/升级")
    ai_strategy_adopted: bool = Field(default=False, description="商家是否采纳AI建议")


class ReviewOutput(BaseModel):
    """Agent 5 输出：经验卡片"""
    case_type: str = Field(..., description="纠纷类型标签")
    key_facts: str = Field(..., description="关键事实摘要（一句话）")
    merchant_action_taken: str = Field(..., description="商家实际采取的行动")
    outcome: str = Field(..., description="最终结果")
    lesson_text: str = Field(..., description="经验教训（自然语言）")
    tags: List[str] = Field(default_factory=list, description="可复用策略标签")


# ============================================================
# 八、聚合结构
# ============================================================

class AnalysisReport(BaseModel):
    """Controller 返回的完整分析报告"""
    dispute_id: str = Field(..., description="纠纷编号")
    facts: FactOutput = Field(..., description="事实分析结果")
    strategy: StrategyOutput = Field(..., description="策略分析结果（含处置方向、胜率、置信度、客户价值、恶意检测）")
    scripts: ScriptOutput = Field(..., description="生成话术")
    emotion_alert: Optional[EmotionOutput] = Field(default=None, description="情绪预警（如有）")
    buyer_profile: Optional[BuyerProfile] = Field(default=None, description="买家画像摘要（供前端参考信息区展示）")
    similar_cases: List[SimilarCase] = Field(default_factory=list, description="相似历史判例（供前端参考信息区展示）")
    matched_rules: List[MatchedRule] = Field(
        default_factory=list,
        description="本次命中的平台规则（结构化，供前端平台规则依据区）",
    )


# ============================================================
# 九、智能模式数据结构
# ============================================================

# 智能模式阶段枚举
INTEL_PHASE_EVIDENCE = "evidence_collection"
INTEL_PHASE_STRATEGY = "strategy_negotiation"
INTEL_PHASE_SETTLE = "settlement"
INTEL_PHASE_DEFENSE = "defense"
INTEL_PHASE_HANDOFF = "handoff"
VALID_INTEL_PHASES = [
    INTEL_PHASE_EVIDENCE,
    INTEL_PHASE_STRATEGY,
    INTEL_PHASE_SETTLE,
    INTEL_PHASE_DEFENSE,
    INTEL_PHASE_HANDOFF,
]

# 买家类型枚举
BUYER_TYPE_HIGH_VALUE_OLD = "high_value_old"
BUYER_TYPE_NORMAL = "normal"
BUYER_TYPE_FIRST_TIME = "first_time"
BUYER_TYPE_SUSPICIOUS = "suspicious"
BUYER_TYPE_MALICIOUS = "malicious"
VALID_BUYER_TYPES = [
    BUYER_TYPE_HIGH_VALUE_OLD,
    BUYER_TYPE_NORMAL,
    BUYER_TYPE_FIRST_TIME,
    BUYER_TYPE_SUSPICIOUS,
    BUYER_TYPE_MALICIOUS,
]

# 当前策略枚举
INTEL_STRATEGY_COLLECT_EVIDENCE = "collect_evidence"
INTEL_STRATEGY_NEGOTIATE = "negotiate"
INTEL_STRATEGY_COMPENSATE = "compensate"
INTEL_STRATEGY_DEFEND = "defend"
VALID_INTEL_STRATEGIES = [
    INTEL_STRATEGY_COLLECT_EVIDENCE,
    INTEL_STRATEGY_NEGOTIATE,
    INTEL_STRATEGY_COMPENSATE,
    INTEL_STRATEGY_DEFEND,
]

# 风险等级枚举
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
VALID_RISK_LEVELS = [RISK_LOW, RISK_MEDIUM, RISK_HIGH]


class EvidenceSummary(BaseModel):
    """证据摘要"""
    collected: List[str] = Field(default_factory=list, description="已收集的证据")
    missing: List[str] = Field(default_factory=list, description="缺失的证据")
    quality: str = Field(default=EVIDENCE_MEDIUM, description=f"证据质量：{'/'.join(VALID_EVIDENCE_QUALITY)}")


class KeyDecision(BaseModel):
    """关键决策记录"""
    turn: int = Field(..., description="发生在第几轮")
    decision: str = Field(..., description="决策内容")
    reason: str = Field(default="", description="决策原因")


class ToolFinding(BaseModel):
    """工具结构化结论 — 跨轮注入 LLM 上下文，供策略分析使用"""
    tool: str = Field(..., description="工具名称")
    turn: int = Field(..., description="发生在第几轮")
    summary: str = Field(..., description="面向 LLM 的自然语言结论摘要")
    facts: dict[str, Any] = Field(default_factory=dict, description="结构化事实键值")


class IntelligentState(BaseModel):
    """
    智能模式案件状态 — 独立于对话历史，仅在案件发生实质性变化时更新。

    存储：Redis，按 dispute_id 为 key，TTL 24h。
    更新方式：LLM 通过 update_state 工具调用；工具结论通过 record_tool_finding 自动追加。
    """
    dispute_id: str = Field(..., description="纠纷编号")
    phase: str = Field(
        default=INTEL_PHASE_EVIDENCE,
        description=f"当前阶段：{'/'.join(VALID_INTEL_PHASES)}",
    )
    responsibility: str = Field(
        default=RESPONSIBILITY_UNCLEAR,
        description=f"责任归属：{'/'.join(VALID_RESPONSIBILITIES)}",
    )
    current_strategy: str = Field(
        default=INTEL_STRATEGY_COLLECT_EVIDENCE,
        description=f"当前策略：{'/'.join(VALID_INTEL_STRATEGIES)}",
    )
    strategy_rationale: str = Field(default="", description="策略理由")
    buyer_type: str = Field(
        default=BUYER_TYPE_NORMAL,
        description=f"买家类型：{'/'.join(VALID_BUYER_TYPES)}",
    )
    evidence_summary: EvidenceSummary = Field(default_factory=EvidenceSummary, description="证据摘要")
    risk_level: str = Field(
        default=RISK_LOW,
        description=f"风险等级：{'/'.join(VALID_RISK_LEVELS)}",
    )
    risk_signals: List[str] = Field(default_factory=list, description="风险信号列表")
    key_decisions: List[KeyDecision] = Field(default_factory=list, description="关键决策历史")
    tool_findings: List[ToolFinding] = Field(default_factory=list, description="工具结构化结论（跨轮记忆）")
    last_update_reason: str = Field(default="", description="最近一次更新原因")
    updated_at: Optional[str] = Field(default=None, description="最近更新时间（ISO 8601）")


class EvidenceSummaryInput(BaseModel):
    """证据摘要的简化输入"""
    collected: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    quality: str = Field(default=EVIDENCE_MEDIUM)


class UpdateStateInput(BaseModel):
    """
    update_state 工具的输入参数。
    仅在案件情况发生实质性变化时调用。
    """
    dispute_id: str = Field(..., description="纠纷编号")
    phase: Optional[str] = Field(default=None, description=f"新阶段：{'/'.join(VALID_INTEL_PHASES)}")
    responsibility: Optional[str] = Field(default=None, description=f"责任归属更新：{'/'.join(VALID_RESPONSIBILITIES)}")
    current_strategy: Optional[str] = Field(default=None, description=f"策略更新：{'/'.join(VALID_INTEL_STRATEGIES)}")
    strategy_rationale: Optional[str] = Field(default=None, description="策略理由")
    buyer_type: Optional[str] = Field(default=None, description=f"买家类型：{'/'.join(VALID_BUYER_TYPES)}")
    evidence_summary: Optional[EvidenceSummaryInput] = Field(default=None, description="证据摘要更新")
    risk_level: Optional[str] = Field(default=None, description=f"风险等级：{'/'.join(VALID_RISK_LEVELS)}")
    risk_signals: Optional[List[str]] = Field(default=None, description="风险信号")
    key_decision: Optional[KeyDecision] = Field(default=None, description="新增的关键决策")
    update_reason: str = Field(..., description="本次更新原因")


class IntelligentContext(BaseModel):
    """
    对话 Agent 的上下文 — 每次调用 conversation_agent.chat() 时传入。
    """
    dispute_id: str = Field(..., description="纠纷编号")
    chat_history: List[ChatTurn] = Field(default_factory=list, description="对话历史")
    order_id: str = Field(default="", description="订单号")
    order_amount: float = Field(default=0.0, description="订单金额")
    buyer_id: str = Field(default="", description="买家脱敏ID")
    merchant_id: str = Field(default="", description="商家ID")
    product_category_slug: str = Field(default="", description="商品品类 slug")
    platform_service_tags: List[str] = Field(default_factory=list, description="平台服务标标签")
    max_compensation: float = Field(default=0.0, description="商家个性化赔偿上限（元），0 表示不限制")
    current_state: IntelligentState = Field(
        default_factory=lambda: IntelligentState(dispute_id=""),
        description="当前案件状态快照",
    )


class AgentReply(BaseModel):
    """
    对话 Agent 返回的回复。
    """
    reply_text: str = Field(default="", description="面向买家的回复正文")
    state_updated: bool = Field(default=False, description="本轮是否更新了状态")
    state: IntelligentState = Field(
        default_factory=lambda: IntelligentState(dispute_id=""),
        description="最新状态（无论是否更新）",
    )
    handoff: bool = Field(default=False, description="是否强制转人工")
    handoff_reason: str = Field(default="", description="转人工原因（强制或建议）")
    handoff_summary: str = Field(default="", description="交接摘要（强制转人工时）")
    handoff_suggested: bool = Field(default=False, description="是否建议转人工（用户可选择继续）")
    tools_called: List[str] = Field(default_factory=list, description="本轮调用的工具列表")