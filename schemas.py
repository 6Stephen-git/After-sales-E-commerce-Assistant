"""
商家应诉助手 - 全局数据结构定义
所有 Agent 的输入输出必须引用此文件，禁止自行发明字段。

版本：v1.0
"""

from pydantic import BaseModel, Field
from typing import Optional, List


# ============================================================
# 一、基础枚举
# ============================================================

# 策略枚举（内部英文）
STRATEGY_DEFEND = "defend"
STRATEGY_NEGOTIATE = "negotiate"
STRATEGY_COMPENSATE = "compensate"
VALID_STRATEGIES = [STRATEGY_DEFEND, STRATEGY_NEGOTIATE, STRATEGY_COMPENSATE]

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
    goods_received: Optional[bool] = Field(default=None, description="买家是否收到货")
    defect_type: Optional[str] = Field(default=None, description="瑕疵类型：破洞/污渍/色差/线头/功能故障/无瑕疵")
    defect_location: Optional[str] = Field(default=None, description="瑕疵位置描述")
    defect_edge: Optional[str] = Field(default=None, description="破损边缘形态：整齐/毛糙/无法判断")
    has_tag_visible: Optional[bool] = Field(default=None, description="吊牌是否可见")
    photo_background: Optional[str] = Field(default=None, description="拍摄背景环境")
    wear_signs: Optional[str] = Field(default=None, description="穿着/使用痕迹描述")
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


class StrategyOutput(BaseModel):
    """Agent 2 输出：策略建议"""
    strategy: str = Field(..., description=f"策略方向：{'/'.join(VALID_STRATEGIES)}")
    estimated_win_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="预估胜率")
    policy_ref: Optional[str] = Field(default=None, description="引用的平台规则条款")
    reasoning: str = Field(default="", description="推理依据说明")
    risk_factors: List[str] = Field(default_factory=list, description="风险因素列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="策略置信度")


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
    compensate_version: str = Field(default="", description="认赔版话术")
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
    strategy: StrategyOutput = Field(..., description="策略建议")
    scripts: ScriptOutput = Field(..., description="生成话术")
    emotion_alert: Optional[EmotionOutput] = Field(default=None, description="情绪预警（如有）")