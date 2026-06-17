// ---------- 话术拆句：按中文句末标点与换行切分 ----------

/**
 * 将整段话术拆成可独立点击的句子列表。
 * @param {string} text - 原始话术全文
 * @returns {string[]} 非空句子数组
 */
export function split_script_sentences(text) {
  const raw = String(text || '').trim()
  if (!raw) {
    return []
  }
  return raw
    .split(/(?<=[。！？；])\s*|\n+/)
    .map((item) => item.trim())
    .filter(Boolean)
}
