<template>
  <el-card>
    <template #header>
      <span>策略建议</span>
    </template>

    <!-- 核心结论区 -->
    <div class="conclusion-block">
      <h3 class="zone-title">核心结论区</h3>

      <!-- 胜率与置信度独立指标条 -->
      <div class="strategy-metrics-row">
        <div class="strategy-metric-card">
          <div class="strategy-metric-label">预估胜率</div>
          <div class="strategy-metric-value">
            <el-progress
              v-if="show_win_rate"
              :percentage="win_rate_percent"
              :stroke-width="10"
              class="win-rate-bar"
            />
            <span v-else>—</span>
          </div>
        </div>
        <div class="strategy-metric-card">
          <div class="strategy-metric-label">策略置信度</div>
          <div class="strategy-metric-value">{{ confidence_percent }}%</div>
        </div>
      </div>

      <dl class="conclusion-dl">
        <div class="conclusion-row">
          <dt class="conclusion-dt">客户意图分析</dt>
          <dd class="conclusion-dd">{{ strategy?.customer_intent_analysis || '—' }}</dd>
        </div>
        <div class="conclusion-row">
          <dt class="conclusion-dt">策略方向</dt>
          <dd class="conclusion-dd">{{ strategy?.strategy_direction_summary || '—' }}</dd>
        </div>
        <div class="conclusion-row">
          <dt class="conclusion-dt">推理理由</dt>
          <dd class="conclusion-dd">{{ strategy?.strategy_direction_rationale || '—' }}</dd>
        </div>
      </dl>
    </div>

    <!-- 关键依据区 -->
    <el-collapse v-model="evidence_active" class="evidence-collapse" @change="on_evidence_change">
      <el-collapse-item title="关键依据区" name="evidence">
        <el-collapse v-model="inner_active" class="inner-collapse evidence-rail">
          <el-collapse-item title="疑点列表" name="red-flags">
            <div class="evidence-panel">
              <el-empty v-if="red_flag_items.length === 0" description="暂无疑点" :image-size="60" />
              <template v-else>
                <p class="evidence-panel-title">已识别疑点</p>
                <div class="red-flag-list">
                  <el-tag
                    v-for="item in red_flag_items"
                    :key="item"
                    class="tag-gap red-flag-tag"
                    type="danger"
                  >{{ item }}</el-tag>
                </div>
              </template>
            </div>
          </el-collapse-item>

          <el-collapse-item title="平台规则依据" name="rules">
            <div class="evidence-panel">
              <ol v-if="platform_rule_lines.length > 0" class="rule-list-styled">
                <li
                  v-for="(line, idx) in platform_rule_lines"
                  :key="'rule-line-' + idx"
                  class="rule-list-item"
                >
                  <span class="rule-badge">{{ idx + 1 }}</span>
                  <span>{{ line }}</span>
                </li>
              </ol>
              <el-empty v-else description="暂无规则命中" :image-size="60" />
            </div>
          </el-collapse-item>

          <el-collapse-item title="恶意风险提示" name="malicious">
            <el-empty
              v-if="!strategy?.malicious_detection"
              description="暂无恶意风险信号"
              :image-size="60"
            />
            <div v-else class="evidence-panel">
              <div class="risk-score-card">
                <div class="risk-score-value" :class="risk_score_class">
                  {{ strategy.malicious_detection.risk_score }}
                </div>
                <div class="risk-score-meta">
                  <div class="risk-score-label">恶意风险评分</div>
                  <el-tag :type="malicious_level_type" size="small">{{ malicious_level_label }}风险</el-tag>
                </div>
              </div>

              <div class="risk-detail-block">
                <div class="risk-detail-label">处置建议</div>
                <p class="risk-detail-text">{{ strategy.malicious_detection.disposition_advice || '—' }}</p>
              </div>

              <div v-if="triggered_signals.length" class="risk-detail-block">
                <div class="risk-detail-label">触发信号</div>
                <div
                  v-for="sig in triggered_signals"
                  :key="sig.signal_type + (sig.description || '')"
                  class="signal-list-item"
                >
                  {{ getMaliciousSignalLabel(sig.signal_type) }}：{{ sig.description }}
                  （{{ sig.score }}分，{{ getMaliciousSignalSourceLabel(sig.source) }}）
                </div>
              </div>
              <div v-else-if="malicious_risk_fallback" class="risk-detail-block">
                <div class="risk-detail-label">风险提示</div>
                <p class="risk-detail-text">{{ malicious_risk_fallback }}</p>
              </div>
            </div>
          </el-collapse-item>

          <el-collapse-item
            v-if="strategy?.customer_value"
            title="客户价值提示"
            name="customer-value"
          >
            <div class="evidence-panel">
              <div class="metric-grid">
                <div class="metric-cell">
                  <div class="metric-cell-label">长期价值分</div>
                  <div class="metric-cell-value">{{ strategy.customer_value.long_term_score }}</div>
                </div>
                <div class="metric-cell">
                  <div class="metric-cell-label">本单价值分</div>
                  <div class="metric-cell-value">{{ strategy.customer_value.order_score }}</div>
                </div>
                <div class="metric-cell">
                  <div class="metric-cell-label">触发通道</div>
                  <div class="metric-cell-value">
                    <el-tag :type="channel_type" size="small">{{ channel_summary }}</el-tag>
                  </div>
                </div>
                <div class="metric-cell">
                  <div class="metric-cell-label">话术温度建议</div>
                  <div class="metric-cell-value metric-cell-value--text">
                    {{ strategy.customer_value.tone_suggestion || '—' }}
                  </div>
                </div>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-collapse-item>
    </el-collapse>

    <!-- 参考信息区 -->
    <el-collapse v-model="ref_active" class="ref-collapse ref-zone-collapse">
      <el-collapse-item title="参考信息区" name="ref">
        <div v-if="buyer_profile" class="ref-sub-block">
          <h4 class="sub-block-title">买家画像摘要</h4>
          <div class="profile-grid-unified">
            <div class="profile-stat">
              <div class="profile-stat-label">购买次数</div>
              <div class="profile-stat-value">{{ buyer_profile.purchase_count }}</div>
            </div>
            <div class="profile-stat">
              <div class="profile-stat-label">退货率</div>
              <div class="profile-stat-value">{{ format_percent(buyer_profile.return_rate) }}</div>
            </div>
            <div class="profile-stat">
              <div class="profile-stat-label">纠纷次数</div>
              <div class="profile-stat-value">{{ buyer_profile.dispute_count }}</div>
            </div>
            <div class="profile-stat">
              <div class="profile-stat-label">纠纷率</div>
              <div class="profile-stat-value">{{ format_percent(buyer_profile.dispute_rate) }}</div>
            </div>
            <div class="profile-stat">
              <div class="profile-stat-label">平均客单价</div>
              <div class="profile-stat-value">{{ buyer_profile.avg_order_value?.toFixed(2) || '—' }}</div>
            </div>
            <div class="profile-stat">
              <div class="profile-stat-label">信誉等级</div>
              <div class="profile-stat-value">{{ buyer_profile.credit_level || '—' }}</div>
            </div>
            <div class="profile-stat">
              <div class="profile-stat-label">恶意标记次数</div>
              <div class="profile-stat-value">{{ buyer_profile.malicious_flags }}</div>
            </div>
            <div class="profile-stat">
              <div class="profile-stat-label">好评次数</div>
              <div class="profile-stat-value">{{ buyer_profile.positive_review_count }}</div>
            </div>
          </div>
        </div>

        <div v-if="similar_cases?.length" class="ref-sub-block">
          <h4 class="sub-block-title">相似判例（Top {{ similar_cases.length }}）</h4>
          <div v-for="(c, idx) in similar_cases" :key="c.case_id" class="case-card">
            <div class="case-card-header">
              #{{ idx + 1 }} {{ c.case_id }}（相似度 {{ (c.similarity * 100).toFixed(0) }}%）
            </div>
            <div class="case-card-row"><strong>当时商家行动：</strong>{{ c.merchant_action }}</div>
            <div class="case-card-row"><strong>结果：</strong>{{ c.outcome }}</div>
            <div class="case-card-row"><strong>经验：</strong>{{ c.lesson }}</div>
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
import { computed, ref } from 'vue'
import {
  formatRedFlagItem,
  getMaliciousSignalLabel,
  getMaliciousSignalSourceLabel,
  getRiskLevelLabel,
  polishRuleLine
} from '../../utils/enums'

// ---------- 组件输入：策略、事实疑点、命中规则、参考信息 ----------
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

// ---------- 折叠状态：外层收起时联动清空内层 ----------
const evidence_active = ref([])
const inner_active = ref([])
const ref_active = ref([])

function on_evidence_change(active_names) {
  if (!active_names.includes('evidence')) {
    inner_active.value = []
  }
}

// ---------- 工具函数：比率格式化 ----------
function format_percent(value) {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

// ---------- 派生状态：触发信号列表 ----------
const triggered_signals = computed(() => {
  const raw = props.strategy?.malicious_detection?.triggered_signals
  return Array.isArray(raw) ? raw : []
})

// ---------- 派生状态：无结构化信号时的兜底文案 ----------
const malicious_risk_fallback = computed(() => {
  const md = props.strategy?.malicious_detection
  if (!md) return ''
  const hints = String(md.malicious_risk_hints || '').trim()
  if (hints) return hints
  return String(md.hard_rule_summary || '').trim()
})

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

// ---------- 派生状态：疑点列表 ----------
const red_flag_items = computed(() => {
  const raw_flags = Array.isArray(props.facts?.red_flags) ? props.facts.red_flags : []
  const normalized = raw_flags.map((item) => formatRedFlagItem(item)).filter((item) => Boolean(item))
  return [...new Set(normalized)]
})

// ---------- 派生状态：平台规则依据 ----------
const platform_rule_lines = computed(() => {
  const from_strategy = props.strategy?.platform_rule_basis
  if (Array.isArray(from_strategy) && from_strategy.length > 0) {
    return from_strategy.map((item) => polishRuleLine(item)).filter(Boolean)
  }
  return matched_rules_list.value
    .map((rule) => polishRuleLine(rule?.rule_summary))
    .filter(Boolean)
})

// ---------- 派生状态：前端代表规则 ----------
const matched_rules_list = computed(() => {
  const raw = Array.isArray(props.matched_rules) ? props.matched_rules : []
  const rank = { must: 0, should: 1, weak: 2 }
  return [...raw]
    .sort((a, b) => (rank[a?.relevance] ?? 9) - (rank[b?.relevance] ?? 9))
    .slice(0, 5)
})

// ---------- 派生状态：恶意风险等级 ----------
const malicious_level_label = computed(() => {
  return getRiskLevelLabel(props.strategy?.malicious_detection?.risk_level)
})

const malicious_level_type = computed(() => {
  const level = props.strategy?.malicious_detection?.risk_level
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  return 'success'
})

const risk_score_class = computed(() => {
  const level = props.strategy?.malicious_detection?.risk_level
  if (level === 'high') return 'is-high'
  if (level === 'medium') return 'is-medium'
  return 'is-low'
})

// ---------- 派生状态：客户价值通道 ----------
const channel_summary = computed(() => {
  const cv = props.strategy?.customer_value
  if (!cv) return '—'
  if (cv.channel === 'long_term') return '长期客户优待'
  if (cv.channel === 'order') return '本单重点处理'
  return '未触发优待通道'
})

const channel_type = computed(() => {
  const ch = props.strategy?.customer_value?.channel
  if (ch === 'long_term') return 'success'
  if (ch === 'order') return 'warning'
  return 'info'
})
</script>

<style scoped>
.evidence-collapse {
  margin-top: 12px;
}

.inner-collapse {
  contain: content;
}

.ref-collapse {
  margin-top: 12px;
}

.ref-zone-collapse {
  contain: content;
}

.win-rate-bar {
  max-width: 140px;
}

.metric-cell-value--text {
  font-size: 13px;
  font-weight: 400;
  line-height: 1.5;
}
</style>
