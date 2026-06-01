// ---------- 枚举映射表：内部英文值统一映射为中文展示 ----------
const DISPOSITION_LABEL_MAP = {
  defend: '抗辩',
  negotiate: '协商',
  compensate: '体面善后'
}

const EVIDENCE_QUALITY_LABEL_MAP = {
  high: '高',
  medium: '中',
  low: '低'
}

const SENTIMENT_LABEL_MAP = {
  negative: '负面',
  neutral: '中性',
  positive: '正面'
}

const RESPONSE_MODE_LABEL_MAP = {
  merchant_fault: '主动担责',
  malicious_risk: '依据应对',
  neutral_negotiate: '协商沟通'
}

const RISK_LEVEL_LABEL_MAP = {
  low: '低',
  medium: '中',
  high: '高'
}

// ---------- 疑点枚举键前缀（与 Agent1 red_flags 约定一致） ----------
const RED_FLAG_KEY_LABEL_MAP = {
  evidence_contradiction: '证据矛盾',
  fake_evidence: '疑似虚假举证',
  logistics_mismatch: '物流信息与描述不符'
}

// ---------- 恶意信号类型（与后端 agent2_tools 映射一致） ----------
const MALICIOUS_SIGNAL_TYPE_LABEL_MAP = {
  fake_evidence: '疑似虚假凭证（硬规则）',
  abuse_refund_only: '滥用仅退款',
  batch_malicious_orders: '批量恶意下单',
  freight_insurance_abuse: '疑似骗取运费险',
  swap_or_missing_items: '退货调包/少件',
  abnormal_return_address: '退货地址异常',
  related_accounts: '关联账户异常',
  review_blackmail: '差评/投诉勒索',
  identity_impersonation: '冒充身份施压',
  evidence_contradiction: '话术与证据矛盾',
  professional_claim_pattern: '职业索赔话术',
  fake_credential_web_image: '举证疑似网图/非实拍',
  abuse_refund_intent_chat: '聊天暴露套利/仅退意图'
}

// ---------- 映射函数：处置方向 ----------
export function getDispositionLabel(value) {
  return DISPOSITION_LABEL_MAP[value] || '未知处置方向'
}

// ---------- 映射函数：证据质量 ----------
export function getEvidenceQualityLabel(value) {
  return EVIDENCE_QUALITY_LABEL_MAP[value] || '未知等级'
}

// ---------- 映射函数：情绪标签 ----------
export function getSentimentLabel(value) {
  return SENTIMENT_LABEL_MAP[value] || '未知情绪'
}

// ---------- 映射函数：话术应对思想 ----------
export function getResponseModeLabel(value) {
  return RESPONSE_MODE_LABEL_MAP[value] || '未知应对模式'
}

// ---------- 映射函数：恶意风险等级 ----------
export function getRiskLevelLabel(value) {
  return RISK_LEVEL_LABEL_MAP[value] || '未知等级'
}

// ---------- 映射函数：疑点项（enum_key：正文） ----------
export function formatRedFlagItem(raw) {
  const s = String(raw || '').trim()
  if (!s) return ''
  const sepIndex = s.includes('：') ? s.indexOf('：') : s.indexOf(':')
  if (sepIndex <= 0) return s
  const key = s.slice(0, sepIndex).trim().toLowerCase()
  const body = s.slice(sepIndex + 1).trim()
  const label = RED_FLAG_KEY_LABEL_MAP[key] || key.replace(/_/g, ' ')
  return body ? `${label}：${body}` : label
}

// ---------- 映射函数：恶意信号类型 ----------
export function getMaliciousSignalLabel(type) {
  const key = String(type || '').trim()
  return MALICIOUS_SIGNAL_TYPE_LABEL_MAP[key] || key.replace(/_/g, ' ')
}

// ---------- 映射函数：恶意信号来源 ----------
export function getMaliciousSignalSourceLabel(source) {
  if (source === 'llm_semantic') return '语义层'
  if (source === 'hard_rule') return '硬规则'
  return source || '未知'
}

// ---------- 工具函数：平台规则展示文案（去条号/章节/内部 doc 引用） ----------
export function polishRuleLine(text) {
  const raw = String(text || '').trim()
  if (!raw) return ''
  return raw
    .replace(/第[一二三四五六七八九十百千零\d]+条/g, '')
    .replace(/第[一二三四五六七八九十]+节[^，。；]*/g, '')
    .replace(/特殊品类争议处理_[^:：]+::/g, '')
    .replace(/争议处理基本规则_[^:：]+::/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}
