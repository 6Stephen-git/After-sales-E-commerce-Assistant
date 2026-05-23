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

# 话术版本标识
SCRIPT_DEFENSE = "defense_version"
SCRIPT_NEGOTIATE = "negotiate_version"
SCRIPT_COMPENSATE = "compensate_version"


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


# ============================================================
# 三、Agent 1 — 事实还原员
# ============================================================

class FactOutput(BaseModel):
    """Agent 1 输出：结构化事实"""
    issue_summary: Optional[str] = Field(default=None, description="买家核心诉求摘要（面向多模态分析的锚点）")
    intent_tags: List[str] = Field(default_factory=list, description="诉求标签（如：质量问题/物流异常/补偿诉求）")
    visual_observations: List[str] = Field(default_factory=list, description="视觉观察结论列表（自然语言短句）")
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
    evidence_quality: str = Field(default=EVIDENCE_MEDIUM, description="证据质量：high/medium/low")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="事实提取置信度")
    uncertainty_note: Optional[str] = Field(default=None, description="当某些事实无法确定时，用自然语言说明原因")

# ============================================================
# 四、Agent 2 — 策略参谋员
# ============================================================

class MatchedRule(BaseModel):
    """匹配到的平台规则"""
    rule_id: str = Field(..., description="规则编号")
    rule_summary: str = Field(..., description="规则摘要")
    condition_result: str = Field(..., description="条件匹配结果描述")


class SimilarCase(BaseModel):
    """相似历史判例"""
    case_id: str = Field(..., description="案例编号")
    similarity: float = Field(default=0.0, ge=0.0, le=1.0, description="相似度")
    merchant_action: str = Field(..., description="当时商家采取的行动")
    outcome: str = Field(..., description="当时的结果")
    lesson: str = Field(..., description="提炼的经验")


class StrategyInput(BaseModel):
    """Agent 2 输入"""
    facts: FactOutput = Field(..., description="Agent 1 的输出")
    buyer_profile: BuyerProfile = Field(..., description="买家画像")
    matched_rules: List[MatchedRule] = Field(default_factory=list, description="匹配到的规则")
    similar_cases: List[SimilarCase] = Field(default_factory=list, description="相似历史判例")
    order_amount: float = Field(default=0.0, description="纠纷订单金额")
    chat_history: List[str] = Field(default_factory=list, description="聊天记录文本列表（用于语义分析）")
    emotion_note: Optional[str] = Field(default=None, description="Agent 4 情绪摘要（可选）")


class CustomerValueInput(BaseModel):
    """客户价值评估输入"""
    buyer_profile: BuyerProfile = Field(..., description="买家画像，长期价值评估主输入")
    order_amount: float = Field(default=0.0, description="当前纠纷订单金额")
    defect_severity: str = Field(default="moderate", description="问题严重性：minor/moderate/severe")
    goods_recoverability: str = Field(
        default="repairable",
        description="商品可挽回性：resalable/repairable/unrecoverable（越不可挽回分越高）",
    )
    buyer_cooperation: str = Field(default="neutral", description="买家配合度：good/neutral/poor")
    demand_reasonableness: str = Field(
        default="borderline",
        description="诉求合理性：reasonable/borderline/unreasonable",
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
    order_score: int = Field(default=0, description="本单价值总分（0-100）")
    long_term_breakdown: List[CustomerValueScoreItem] = Field(default_factory=list, description="长期价值分项")
    order_breakdown: List[CustomerValueScoreItem] = Field(default_factory=list, description="本单价值分项")
    long_term_triggered: bool = Field(default=False, description="是否触发长期客户优待通道")
    order_triggered: bool = Field(default=False, description="是否触发本单重点处理通道")
    channel: str = Field(default="none", description="触发通道：long_term/order/none")
    compensation_uplift: Optional[str] = Field(default=None, description="补偿上限提升幅度建议")
    tone_suggestion: Optional[str] = Field(default=None, description="话术温度建议")


class MaliciousSignal(BaseModel):
    """恶意行为信号分项"""
    signal_type: str = Field(..., description="信号类型标识，如 fake_evidence / abuse_refund_only")
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
    reasoning: str = Field(default="", description="完整策略说明（含客户意图/风险点/建议动作/推理理由四段，供流式与话术引用）")
    risk_factors: List[str] = Field(default_factory=list, description="风险因素列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="策略置信度")
    customer_value: Optional[CustomerValueOutput] = Field(default=None, description="客户价值评估结果")
    malicious_detection: Optional[MaliciousDetectionOutput] = Field(default=None, description="恶意行为检测结果")


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

class ScriptOutput(BaseModel):
    """Agent 3 输出：多版本话术"""
    defense_version: str = Field(default="", description="抗辩版话术")
    negotiate_version: str = Field(default="", description="协商版话术")
    compensate_version: str = Field(default="", description="善后版话术（主动体面收尾）")
    recommended_version: str = Field(default="", description="推荐版本标识")
    usage_tip: Optional[str] = Field(default=None, description="话术使用提示")


# ============================================================
# 六、Agent 4 — 情绪监控员
# ============================================================

class EmotionInput(BaseModel):
    """Agent 4 输入"""
    text: str = Field(..., description="待分析的文本（商家输入或买家消息）")
    context: dict = Field(default_factory=dict, description="纠纷上下文快照")


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