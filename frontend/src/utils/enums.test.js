import { describe, expect, it } from 'vitest'
import {
  getDispositionLabel,
  getEvidenceQualityLabel,
  getScriptVersionLabel,
  getSentimentLabel
} from './enums'

// ---------- 处置方向枚举：合法值映射为约定中文 ----------
describe('getDispositionLabel', () => {
  it('将内部处置方向值映射为中文展示', () => {
    expect(getDispositionLabel('defend')).toBe('抗辩')
    expect(getDispositionLabel('negotiate')).toBe('协商')
    expect(getDispositionLabel('compensate')).toBe('体面善后')
  })

  it('未知值回退为占位文案', () => {
    expect(getDispositionLabel('unknown')).toBe('未知处置方向')
  })
})

// ---------- 证据质量枚举：合法值映射 ----------
describe('getEvidenceQualityLabel', () => {
  it('将证据质量映射为中文档位', () => {
    expect(getEvidenceQualityLabel('high')).toBe('高')
    expect(getEvidenceQualityLabel('medium')).toBe('中')
    expect(getEvidenceQualityLabel('low')).toBe('低')
  })
})

// ---------- 情绪标签枚举：合法值映射 ----------
describe('getSentimentLabel', () => {
  it('将情绪标签映射为中文', () => {
    expect(getSentimentLabel('negative')).toBe('负面')
    expect(getSentimentLabel('neutral')).toBe('中性')
    expect(getSentimentLabel('positive')).toBe('正面')
  })
})

// ---------- 话术版本标识：合法值映射 ----------
describe('getScriptVersionLabel', () => {
  it('将话术版本键映射为中文名称', () => {
    expect(getScriptVersionLabel('defense_version')).toBe('抗辩版')
    expect(getScriptVersionLabel('negotiate_version')).toBe('协商版')
    expect(getScriptVersionLabel('compensate_version')).toBe('善后版')
  })
})
