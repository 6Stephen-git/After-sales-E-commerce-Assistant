import textSignals from '../../../data/text_signals.json'

// ---------- 卖家过激措辞：读取 data/text_signals.json 统一词表 ----------
const SELLER_NEGATIVE_KEYWORDS = textSignals.seller_negative_keywords || []

// ---------- 本地匹配：命中攻击性关键词则同步拦截，不等待 API ----------
export function should_block_seller_text_locally(text) {
  const normalized = String(text || '').trim()
  if (!normalized) {
    return false
  }
  return SELLER_NEGATIVE_KEYWORDS.some((keyword) => normalized.includes(keyword))
}
