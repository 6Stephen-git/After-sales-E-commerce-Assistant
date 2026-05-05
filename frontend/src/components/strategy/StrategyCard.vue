<template>
  <el-card>
    <template #header>
      <span>策略建议</span>
    </template>

    <el-descriptions :column="1" border>
      <el-descriptions-item label="策略方向">
        <el-tag type="primary">{{ strategy_label }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="预估胜率">
        <el-progress :percentage="win_rate_percent" :stroke-width="14" />
      </el-descriptions-item>
      <el-descriptions-item label="策略置信度">
        {{ confidence_percent }}%
      </el-descriptions-item>
    </el-descriptions>

    <div class="text-block">
      <h4>推理依据</h4>
      <p>{{ strategy?.reasoning || '暂无推理内容' }}</p>
    </div>

    <div class="text-block">
      <h4>风险因素</h4>
      <el-empty v-if="risk_factors.length === 0" description="暂无风险提示" :image-size="60" />
      <el-tag v-for="item in risk_factors" :key="item" class="risk-tag" type="warning">{{ item }}</el-tag>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { getStrategyLabel } from '../../utils/enums'

// ---------- 组件输入：策略分析结果 ----------
const props = defineProps({
  strategy: {
    type: Object,
    default: null
  }
})

// ---------- 派生状态：策略方向中文化 ----------
const strategy_label = computed(() => {
  return getStrategyLabel(props.strategy?.strategy)
})

// ---------- 派生状态：胜率百分比 ----------
const win_rate_percent = computed(() => {
  const value = Number(props.strategy?.estimated_win_rate || 0)
  return Math.max(0, Math.min(100, Math.round(value * 100)))
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
</script>

<style scoped>
.text-block {
  margin-top: 12px;
}

.text-block h4 {
  margin: 0 0 8px;
  font-size: 14px;
}

.text-block p {
  margin: 0;
  color: #606266;
  line-height: 1.6;
  white-space: pre-wrap;
}

.risk-tag {
  margin: 0 8px 8px 0;
}
</style>
