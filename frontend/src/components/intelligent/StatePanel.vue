<template>
  <div class="state-panel">
    <!-- 无状态时的提示 -->
    <el-card v-if="!state" class="empty-card">
      <div class="empty-text">对话开始后将展示案件状态</div>
    </el-card>

    <!-- 案件状态卡片 -->
    <template v-if="state">
      <!-- 核心状态 -->
      <el-card class="state-card">
        <template #header>
          <span>案件状态</span>
          <el-tag v-if="state.phase" size="small" :type="phase_tag_type" class="phase-tag">
            {{ getIntelPhaseLabel(state.phase) }}
          </el-tag>
        </template>
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="责任归属">
            <el-tag :type="responsibility_tag_type" size="small">
              {{ getResponsibilityLabel(state.responsibility) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="当前策略">
            <el-tag type="info" size="small">
              {{ getIntelStrategyLabel(state.current_strategy) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="买家类型">
            <el-tag :type="buyer_type_tag_type" size="small">
              {{ getBuyerTypeLabel(state.buyer_type) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="风险等级">
            <el-tag :type="risk_tag_type" size="small">
              {{ getRiskLevelLabel(state.risk_level) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="state.strategy_rationale" label="策略理由">
            {{ state.strategy_rationale }}
          </el-descriptions-item>
          <el-descriptions-item v-if="state.last_update_reason" label="最近更新">
            {{ state.last_update_reason }}
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- 证据摘要 -->
      <el-card v-if="has_evidence" class="state-card">
        <template #header>
          <span>证据摘要</span>
        </template>
        <div v-if="state.evidence_summary.collected.length > 0" class="evidence-section">
          <div class="evidence-label">已收集：</div>
          <el-tag
            v-for="item in state.evidence_summary.collected"
            :key="item"
            type="success"
            size="small"
            class="evidence-tag"
          >
            {{ item }}
          </el-tag>
        </div>
        <div v-if="state.evidence_summary.missing.length > 0" class="evidence-section">
          <div class="evidence-label">待补充：</div>
          <el-tag
            v-for="item in state.evidence_summary.missing"
            :key="item"
            type="warning"
            size="small"
            class="evidence-tag"
          >
            {{ item }}
          </el-tag>
        </div>
        <div v-if="state.evidence_summary.quality" class="evidence-section">
          <div class="evidence-label">证据质量：</div>
          <el-tag :type="quality_tag_type" size="small">
            {{ getEvidenceQualityLabel(state.evidence_summary.quality) }}
          </el-tag>
        </div>
      </el-card>

      <!-- 风险信号 -->
      <el-card v-if="state.risk_signals && state.risk_signals.length > 0" class="state-card">
        <template #header>
          <span>风险信号</span>
        </template>
        <div v-for="signal in state.risk_signals" :key="signal" class="risk-item">
          <el-icon color="#E6A23C"><WarningFilled /></el-icon>
          <span>{{ signal }}</span>
        </div>
      </el-card>

      <!-- 关键决策历史 -->
      <el-card v-if="state.key_decisions && state.key_decisions.length > 0" class="state-card">
        <template #header>
          <span>关键决策</span>
        </template>
        <el-timeline>
          <el-timeline-item
            v-for="decision in state.key_decisions"
            :key="decision.turn + decision.decision"
            :timestamp="'第' + decision.turn + '轮'"
            placement="top"
          >
            <div class="decision-text">{{ decision.decision }}</div>
            <div v-if="decision.reason" class="decision-reason">{{ decision.reason }}</div>
          </el-timeline-item>
        </el-timeline>
      </el-card>

      <!-- 工具调用记录 -->
      <el-card v-if="state.tool_calls_log && state.tool_calls_log.length > 0" class="state-card">
        <template #header>
          <span>工具调用记录</span>
        </template>
        <div v-for="log in state.tool_calls_log" :key="log.tool + log.turn" class="tool-log-item">
          <el-tag size="small" type="info">{{ log.tool }}</el-tag>
          <span class="tool-log-summary">{{ log.result_summary || '完成' }}</span>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { WarningFilled } from '@element-plus/icons-vue'
import {
  getIntelPhaseLabel,
  getIntelStrategyLabel,
  getResponsibilityLabel,
  getBuyerTypeLabel,
  getRiskLevelLabel,
  getEvidenceQualityLabel
} from '../../utils/enums'

// ---------- 组件输入：案件状态对象 ----------
const props = defineProps({
  state: {
    type: Object,
    default: null
  }
})

// ---------- 证据摘要是否存在 ----------
const has_evidence = computed(() => {
  if (!props.state?.evidence_summary) return false
  const es = props.state.evidence_summary
  return (es.collected && es.collected.length > 0) ||
    (es.missing && es.missing.length > 0) ||
    Boolean(es.quality)
})

// ---------- 标签颜色映射 ----------
const phase_tag_type = computed(() => {
  const map = {
    evidence_collection: 'info',
    strategy_negotiation: '',
    settlement: 'success',
    defense: 'warning',
    handoff: 'danger'
  }
  return map[props.state?.phase] || 'info'
})

const responsibility_tag_type = computed(() => {
  const map = {
    merchant_fault: 'danger',
    buyer_fault: 'warning',
    unclear: 'info',
    mixed: ''
  }
  return map[props.state?.responsibility] || 'info'
})

const buyer_type_tag_type = computed(() => {
  const map = {
    high_value_old: 'success',
    normal: 'info',
    first_time: '',
    suspicious: 'warning',
    malicious: 'danger'
  }
  return map[props.state?.buyer_type] || 'info'
})

const risk_tag_type = computed(() => {
  const map = {
    low: 'success',
    medium: 'warning',
    high: 'danger'
  }
  return map[props.state?.risk_level] || 'info'
})

const quality_tag_type = computed(() => {
  const map = {
    high: 'success',
    medium: '',
    low: 'danger'
  }
  return map[props.state?.evidence_summary?.quality] || 'info'
})
</script>

<style scoped>
.state-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.empty-card {
  text-align: center;
}

.empty-text {
  color: #909399;
  font-size: 14px;
  padding: 20px 0;
}

.state-card :deep(.el-card__header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
}

.phase-tag {
  margin-left: 8px;
}

.evidence-section {
  margin-bottom: 8px;
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 6px;
}

.evidence-label {
  font-size: 13px;
  color: #606266;
  white-space: nowrap;
  line-height: 24px;
}

.evidence-tag {
  margin: 0;
}

.risk-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #606266;
  margin-bottom: 6px;
}

.decision-text {
  font-size: 14px;
  color: #303133;
}

.decision-reason {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.tool-log-item {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.tool-log-summary {
  font-size: 12px;
  color: #909399;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
