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

    <!-- 关键依据区：疑点 / 规则 / 恶意 / 客户价值 -->
    <div class="zone">
      <h3 class="zone-title">关键依据区</h3>

      <div class="text-block">
        <h4>疑点列表</h4>
        <el-empty v-if="red_flag_items.length === 0" description="暂无疑点" :image-size="60" />
        <el-tag
          v-for="item in red_flag_items"
          :key="item"
          class="tag-gap"
          type="danger"
        >{{ item }}</el-tag>
      </div>

      <div class="text-block">
        <h4>平台规则依据</h4>
        <template v-if="matched_rules_list.length > 0">
          <div
            v-for="(r, idx) in matched_rules_list"
            :key="(r.rule_id || 'rule') + '-' + idx"
            class="rule-block"
          >
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="规则编号">{{ r.rule_id }}</el-descriptions-item>
              <el-descriptions-item label="规则摘要">{{ r.rule_summary }}</el-descriptions-item>
              <el-descriptions-item label="匹配说明">{{ r.condition_result }}</el-descriptions-item>
            </el-descriptions>
          </div>
        </template>
        <p v-else-if="policy_ref_fallback" class="plain-text">{{ policy_ref_fallback }}</p>
        <el-empty v-else description="暂无规则命中" :image-size="60" />
      </div>

      <div class="text-block">
        <h4>恶意风险提示</h4>
        <el-empty
          v-if="risk_factors.length === 0 && !strategy?.malicious_detection"
          description="暂无风险提示"
          :image-size="60"
        />
        <template v-else>
          <el-tag
            v-for="item in risk_factors"
            :key="item"
            class="tag-gap"
            type="warning"
          >{{ item }}</el-tag>
          <div v-if="strategy?.malicious_detection" class="sub-block">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="恶意风险等级">
                <el-tag :type="malicious_level_type">{{ strategy.malicious_detection.risk_level }}</el-tag>
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
              >{{ malicious_signal_label(sig.signal_type) }}：{{ sig.description }}（{{ sig.score }}分，{{ signal_source_label(sig.source) }}）</el-tag>
            </div>
          </div>
        </template>
      </div>

      <div v-if="strategy?.customer_value" class="text-block">
        <h4>客户价值提示</h4>
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
      </div>
    </div>

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
import { getDispositionLabel } from '../../utils/enums'

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

// ---------- 工具函数：比率格式化 ----------
function format_percent(value) {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

// ---------- 工具函数：疑点枚举键前缀转可读文案（与旧 FactCard 逻辑一致） ----------
function format_red_flag_item(raw) {
  const s = String(raw || '').trim()
  if (!s) return ''
  const sepIndex = s.includes('：') ? s.indexOf('：') : s.indexOf(':')
  if (sepIndex <= 0) return s
  const key = s.slice(0, sepIndex).trim().toLowerCase()
  const body = s.slice(sepIndex + 1).trim()
  const label_map = {
    evidence_contradiction: '证据矛盾',
    fake_evidence: '疑似虚假举证',
    logistics_mismatch: '物流信息与描述不符',
  }
  const label = label_map[key] || key.replace(/_/g, ' ')
  return body ? `${label}：${body}` : label
}

// ---------- 工具函数：恶意信号类型中文（与后端 agent2_tools 映射一致） ----------
const MALICIOUS_SIGNAL_TYPE_CN = {
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
  abuse_refund_intent_chat: '聊天暴露套利/仅退意图',
}

function malicious_signal_label(type) {
  const key = String(type || '').trim()
  return MALICIOUS_SIGNAL_TYPE_CN[key] || key.replace(/_/g, ' ')
}

// ---------- 工具函数：恶意信号来源展示文案 ----------
function signal_source_label(source) {
  if (source === 'llm_semantic') return '语义层'
  if (source === 'hard_rule') return '硬规则'
  return source || '未知'
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

// ---------- 派生状态：风险项列表 ----------
const risk_factors = computed(() => {
  return Array.isArray(props.strategy?.risk_factors) ? props.strategy.risk_factors : []
})

// ---------- 派生状态：疑点列表（来自 Agent1 事实） ----------
const red_flag_items = computed(() => {
  const raw_flags = Array.isArray(props.facts?.red_flags) ? props.facts.red_flags : []
  const normalized = raw_flags.map((item) => format_red_flag_item(item)).filter((item) => Boolean(item))
  return [...new Set(normalized)]
})

// ---------- 派生状态：结构化规则命中列表 ----------
const matched_rules_list = computed(() => {
  return Array.isArray(props.matched_rules) ? props.matched_rules : []
})

// ---------- 派生状态：无结构化规则时的 policy_ref 兜底 ----------
const policy_ref_fallback = computed(() => {
  const raw = props.strategy?.policy_ref
  return raw ? String(raw).trim() : ''
})

// ---------- 派生状态：恶意风险等级对应标签颜色 ----------
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

.text-block {
  margin-top: 12px;
}

.text-block h4 {
  margin: 0 0 8px;
  font-size: 14px;
}

.text-block h5 {
  margin: 8px 0 6px;
  font-size: 13px;
  color: #606266;
}

.plain-text {
  margin: 0;
  color: #606266;
  line-height: 1.6;
  white-space: pre-wrap;
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

.ref-collapse {
  margin-top: 12px;
}

.ref-zone-collapse {
  contain: content;
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

.rule-block {
  margin-bottom: 10px;
}

.break-row {
  margin-bottom: 6px;
}
</style>
