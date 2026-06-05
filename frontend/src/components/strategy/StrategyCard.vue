<template>
  <el-card>
    <template #header>
      <span>策略建议</span>
    </template>

    <!-- 核心结论区：客户意图 / 策略方向 / 胜率 / 置信度 -->
    <div class="zone">
      <h3 class="zone-title">核心结论区</h3>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="客户意图分析">
          {{ strategy?.customer_intent_analysis || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="策略方向">
          <p class="direction-text">{{ strategy?.strategy_direction_summary || '—' }}</p>
          <el-tag v-if="strategy?.disposition" type="info" size="small" class="disp-tag">{{ strategy_label }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="推理理由">
          <p class="direction-text">{{ strategy?.strategy_direction_rationale || '—' }}</p>
        </el-descriptions-item>
        <el-descriptions-item label="预估胜率">
          <el-progress v-if="show_win_rate" :percentage="win_rate_percent" :stroke-width="14" />
          <span v-else>—</span>
        </el-descriptions-item>
        <el-descriptions-item label="策略置信度">
          {{ confidence_percent }}%
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <!-- 关键依据区：默认折叠，子模块同样折叠 -->
    <el-collapse class="evidence-collapse">
      <el-collapse-item title="关键依据区" name="evidence">
        <el-collapse class="inner-collapse">
          <el-collapse-item title="疑点列表" name="red-flags">
            <el-empty v-if="red_flag_items.length === 0" description="暂无疑点" :image-size="60" />
            <el-tag
              v-for="item in red_flag_items"
              :key="item"
              class="tag-gap"
              type="danger"
            >{{ item }}</el-tag>
          </el-collapse-item>

          <el-collapse-item title="平台规则依据" name="rules">
            <ol v-if="platform_rule_lines.length > 0" class="rule-list">
              <li
                v-for="(line, idx) in platform_rule_lines"
                :key="'rule-line-' + idx"
                class="rule-item"
              >
                {{ line }}
              </li>
            </ol>
            <el-empty v-else description="暂无规则命中" :image-size="60" />
          </el-collapse-item>

          <el-collapse-item title="恶意风险提示" name="malicious">
            <el-empty
              v-if="!strategy?.malicious_detection"
              description="暂无恶意风险信号"
              :image-size="60"
            />
            <div v-else class="sub-block">
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item label="恶意风险等级">
                  <el-tag :type="malicious_level_type">{{ malicious_level_label }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="风险评分">
                  {{ strategy.malicious_detection.risk_score }}
                </el-descriptions-item>
                <el-descriptions-item label="聚合说明" :span="2">
                  <p class="risk-hints">{{ malicious_risk_hints_text }}</p>
                </el-descriptions-item>
                <el-descriptions-item label="处置建议" :span="2">
                  {{ strategy.malicious_detection.disposition_advice || '—' }}
                </el-descriptions-item>
              </el-descriptions>
              <div v-if="strategy.malicious_detection.triggered_signals?.length" class="signal-list">
                <el-tag
                  v-for="sig in strategy.malicious_detection.triggered_signals"
                  :key="sig.signal_type + (sig.description || '')"
                  class="tag-gap"
                  type="danger"
                  size="small"
                >{{ getMaliciousSignalLabel(sig.signal_type) }}：{{ sig.description }}（{{ sig.score }}分，{{ getMaliciousSignalSourceLabel(sig.source) }}）</el-tag>
              </div>
            </div>
          </el-collapse-item>

          <el-collapse-item
            v-if="strategy?.customer_value"
            title="客户价值提示"
            name="customer-value"
          >
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="长期价值分">
                {{ strategy.customer_value.long_term_score }}
              </el-descriptions-item>
              <el-descriptions-item label="本单价值分">
                {{ strategy.customer_value.order_score }}
              </el-descriptions-item>
              <el-descriptions-item label="触发通道" :span="2">
                <el-tag :type="channel_type">{{ channel_summary }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="话术温度建议" :span="2">
                {{ strategy.customer_value.tone_suggestion || '—' }}
              </el-descriptions-item>
            </el-descriptions>
          </el-collapse-item>
        </el-collapse>
      </el-collapse-item>
    </el-collapse>

    <!-- 参考信息区：仅买家画像 + 相似判例 -->
    <el-collapse class="ref-collapse ref-zone-collapse">
      <el-collapse-item title="参考信息区" name="ref">
        <div v-if="buyer_profile" class="sub-block">
          <h4>买家画像摘要</h4>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="购买次数">
              {{ buyer_profile.purchase_count }}
            </el-descriptions-item>
            <el-descriptions-item label="退货率">
              {{ format_percent(buyer_profile.return_rate) }}
            </el-descriptions-item>
            <el-descriptions-item label="纠纷次数">
              {{ buyer_profile.dispute_count }}
            </el-descriptions-item>
            <el-descriptions-item label="纠纷率">
              {{ format_percent(buyer_profile.dispute_rate) }}
            </el-descriptions-item>
            <el-descriptions-item label="平均客单价">
              {{ buyer_profile.avg_order_value?.toFixed(2) || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="信誉等级">
              {{ buyer_profile.credit_level || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="恶意标记次数">
              {{ buyer_profile.malicious_flags }}
            </el-descriptions-item>
            <el-descriptions-item label="好评次数">
              {{ buyer_profile.positive_review_count }}
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <div v-if="similar_cases?.length" class="sub-block">
          <h4>相似判例（Top {{ similar_cases.length }}）</h4>
          <div v-for="(c, idx) in similar_cases" :key="c.case_id" class="case-item">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="判例">
                #{{ idx + 1 }} {{ c.case_id }}（相似度 {{ (c.similarity * 100).toFixed(0) }}%）
              </el-descriptions-item>
              <el-descriptions-item label="当时商家行动">
                {{ c.merchant_action }}
              </el-descriptions-item>
              <el-descriptions-item label="结果">
                {{ c.outcome }}
              </el-descriptions-item>
              <el-descriptions-item label="经验">
                {{ c.lesson }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </div>

        <el-empty
          v-if="!buyer_profile && !similar_cases?.length"
          description="暂无参考信息"
          :image-size="60"
        />
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import {
  formatRedFlagItem,
  getDispositionLabel,
  getMaliciousSignalLabel,
  getMaliciousSignalSourceLabel,
  getRiskLevelLabel,
  polishRuleLine
} from '../../utils/enums'

// ---------- 组件输入：策略、事实疑点、命中规则、参考信息、话术使用提示 ----------
const props = defineProps({
  strategy: {
    type: Object,
    default: null
  },
  facts: {
    type: Object,
    default: null
  },
  matched_rules: {
    type: Array,
    default: () => []
  },
  buyer_profile: {
    type: Object,
    default: null
  },
  similar_cases: {
    type: Array,
    default: () => []
  }
})

// ---------- 工具函数：比率格式化 ----------
function format_percent(value) {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

// ---------- 派生状态：恶意风险提示聚合（新字段优先，兼容旧报告） ----------
const malicious_risk_hints_text = computed(() => {
  const md = props.strategy?.malicious_detection
  if (!md) return '—'
  const hints = String(md.malicious_risk_hints || '').trim()
  if (hints) return hints
  return String(md.hard_rule_summary || '').trim() || '—'
})
const strategy_label = computed(() => getDispositionLabel(props.strategy?.disposition))

// ---------- 派生状态：胜率百分比 ----------
const win_rate_percent = computed(() => {
  const value = Number(props.strategy?.estimated_win_rate || 0)
  return Math.max(0, Math.min(100, Math.round(value * 100)))
})

// ---------- 派生状态：仅抗辩方向展示胜率 ----------
const show_win_rate = computed(() => {
  return props.strategy?.disposition === 'defend'
    && props.strategy?.estimated_win_rate !== null
    && props.strategy?.estimated_win_rate !== undefined
})

// ---------- 派生状态：策略置信度百分比 ----------
const confidence_percent = computed(() => {
  const value = Number(props.strategy?.confidence || 0)
  return Math.max(0, Math.min(100, Math.round(value * 100)))
})

// ---------- 派生状态：疑点列表（来自 Agent1 事实） ----------
const red_flag_items = computed(() => {
  const raw_flags = Array.isArray(props.facts?.red_flags) ? props.facts.red_flags : []
  const normalized = raw_flags.map((item) => formatRedFlagItem(item)).filter((item) => Boolean(item))
  return [...new Set(normalized)]
})

// ---------- 派生状态：平台规则依据（仅法条摘要；优先 strategy.platform_rule_basis，否则 matched_rules） ----------
const platform_rule_lines = computed(() => {
  const from_strategy = props.strategy?.platform_rule_basis
  if (Array.isArray(from_strategy) && from_strategy.length > 0) {
    return from_strategy.map((item) => polishRuleLine(item)).filter(Boolean)
  }
  return matched_rules_list.value
    .map((rule) => polishRuleLine(rule?.rule_summary))
    .filter(Boolean)
})

// ---------- 派生状态：前端代表规则（后端 display_rules 已做争点过滤，此处仅排序展示） ----------
const matched_rules_list = computed(() => {
  const raw = Array.isArray(props.matched_rules) ? props.matched_rules : []
  const rank = { must: 0, should: 1, weak: 2 }
  return [...raw]
    .sort((a, b) => (rank[a?.relevance] ?? 9) - (rank[b?.relevance] ?? 9))
    .slice(0, 5)
})

// ---------- 派生状态：恶意风险等级对应标签颜色与中文 ----------
const malicious_level_label = computed(() => {
  return getRiskLevelLabel(props.strategy?.malicious_detection?.risk_level)
})

const malicious_level_type = computed(() => {
  const level = props.strategy?.malicious_detection?.risk_level
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  return 'success'
})

// ---------- 派生状态：客户价值触发通道一句话 ----------
const channel_summary = computed(() => {
  const cv = props.strategy?.customer_value
  if (!cv) return '—'
  if (cv.channel === 'long_term') return '长期客户优待'
  if (cv.channel === 'order') return '本单重点处理'
  return '未触发优待通道'
})

// ---------- 派生状态：通道标签颜色 ----------
const channel_type = computed(() => {
  const ch = props.strategy?.customer_value?.channel
  if (ch === 'long_term') return 'success'
  if (ch === 'order') return 'warning'
  return 'info'
})
</script>

<style scoped>
.zone {
  margin-bottom: 16px;
}

.zone-title {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.evidence-collapse {
  margin-top: 12px;
}

.inner-collapse {
  contain: content;
}

.inner-collapse :deep(.el-collapse-item__header) {
  font-size: 14px;
  font-weight: 500;
}

.ref-collapse {  margin-top: 12px;
}

.ref-zone-collapse {
  contain: content;
}

.tag-gap {
  margin: 0 8px 8px 0;
}

.sub-block {
  margin-top: 8px;
}

.signal-list {
  margin-top: 8px;
}

.direction-text {
  margin: 0 0 6px;
  color: #303133;
  line-height: 1.65;
  white-space: pre-wrap;
}

.disp-tag {
  vertical-align: middle;
}

.risk-hints {
  margin: 0;
  color: #606266;
  line-height: 1.65;
  white-space: pre-wrap;
}

.case-item {
  margin-bottom: 8px;
}

.rule-list {
  margin: 0;
  padding-left: 20px;
  color: #606266;
  line-height: 1.7;
}

.rule-item {
  margin-bottom: 8px;
}

.rule-item:last-child {
  margin-bottom: 0;
}
</style>
