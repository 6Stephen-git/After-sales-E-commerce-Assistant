import { describe, expect, it } from 'vitest'
import {
  getDispositionLabel,
  getEvidenceQualityLabel,
  getResponseModeLabel,
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

// ---------- 应对思想枚举：合法值映射 ----------
describe('getResponseModeLabel', () => {
  it('将应对思想键映射为中文名称', () => {
    expect(getResponseModeLabel('merchant_fault')).toBe('主动担责')
    expect(getResponseModeLabel('malicious_risk')).toBe('依据应对')
    expect(getResponseModeLabel('neutral_negotiate')).toBe('协商沟通')
  })
})
