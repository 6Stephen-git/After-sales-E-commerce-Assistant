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
