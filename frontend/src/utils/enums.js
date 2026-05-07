// ---------- 枚举映射表：内部英文值统一映射为中文展示 ----------
const STRATEGY_LABEL_MAP = {
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

const SCRIPT_VERSION_LABEL_MAP = {
  defense_version: '抗辩版',
  negotiate_version: '协商版',
  compensate_version: '善后版'
}

// ---------- 映射函数：策略方向 ----------
export function getStrategyLabel(value) {
  return STRATEGY_LABEL_MAP[value] || '未知策略'
}

// ---------- 映射函数：证据质量 ----------
export function getEvidenceQualityLabel(value) {
  return EVIDENCE_QUALITY_LABEL_MAP[value] || '未知等级'
}

// ---------- 映射函数：情绪标签 ----------
export function getSentimentLabel(value) {
  return SENTIMENT_LABEL_MAP[value] || '未知情绪'
}

// ---------- 映射函数：话术版本 ----------
export function getScriptVersionLabel(value) {
  return SCRIPT_VERSION_LABEL_MAP[value] || '未知版本'
}
