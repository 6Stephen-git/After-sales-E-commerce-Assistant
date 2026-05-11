<template>
  <el-card>
    <template #header>
      <span>事实摘要</span>
    </template>

    <div class="section-block">
      <h4>用户诉求</h4>
      <p class="plain-text">{{ issue_summary }}</p>
    </div>

    <div class="section-block">
      <h4>用户反馈的图片/视频描述</h4>
      <el-empty v-if="visual_observations.length === 0" description="暂无可用的视觉描述" :image-size="60" />
      <el-tag v-for="item in visual_observations" :key="item" class="list-tag" type="info">{{ item }}</el-tag>
    </div>

    <div class="section-block">
      <h4>物流情况</h4>
      <p class="plain-text">{{ logistics_summary }}</p>
    </div>

    <div class="section-block">
      <h4>疑点列表</h4>
      <el-empty v-if="red_flags.length === 0" description="暂无疑点" :image-size="60" />
      <el-tag v-for="item in red_flags" :key="item" class="list-tag" type="danger">{{ item }}</el-tag>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'

// ---------- 组件输入：事实分析结果 ----------
const props = defineProps({
  facts: {
    type: Object,
    default: null
  }
})

// ---------- 派生状态：用户诉求摘要，优先展示 Agent1 的核心诉求 ----------
const issue_summary = computed(() => {
  const summary = String(props.facts?.issue_summary || '').trim()
  if (summary) {
    return summary
  }
  return '暂未提取到明确诉求'
})

// ---------- 派生状态：图片/视频描述，优先取新结构，旧字段仅作为兜底 ----------
const visual_observations = computed(() => {
  const observations = Array.isArray(props.facts?.visual_observations) ? props.facts.visual_observations : []
  const normalized = observations.map((item) => String(item || '').trim()).filter((item) => Boolean(item))
  if (normalized.length > 0) {
    return [...new Set(normalized)]
  }

  const fallback_parts = []
  const defect_type = String(props.facts?.defect_type || '').trim()
  const defect_location = String(props.facts?.defect_location || '').trim()
  if (defect_type) {
    fallback_parts.push(`识别类型：${defect_type}`)
  }
  if (defect_location) {
    fallback_parts.push(`位置：${defect_location}`)
  }
  if (fallback_parts.length > 0) {
    return [fallback_parts.join('，')]
  }
  return []
})

// ---------- 派生状态：物流摘要，优先读取 evidence_items 中的物流证据 ----------
const logistics_summary = computed(() => {
  const evidence_items = Array.isArray(props.facts?.evidence_items) ? props.facts.evidence_items : []
  const logistics_item = evidence_items.find((item) => item && item.type === 'logistics')
  if (logistics_item) {
    const signed_text = logistics_item.is_signed ? '已签收' : '未签收'
    const abnormal_text = logistics_item.is_abnormal ? '异常' : '正常'
    const stagnant_days = Number(logistics_item.stagnant_days || 0)
    if (stagnant_days > 0) {
      return `${signed_text}，物流${abnormal_text}，停滞 ${stagnant_days} 天`
    }
    return `${signed_text}，物流${abnormal_text}`
  }

  if (props.facts?.logistics_normal === true) {
    return '物流状态正常'
  }
  if (props.facts?.logistics_normal === false) {
    return '物流状态异常'
  }
  return '暂无物流信息'
})

// ---------- 派生状态：疑点列表，前端统一只展示这一份风险信息 ----------
const red_flags = computed(() => {
  const raw_flags = Array.isArray(props.facts?.red_flags) ? props.facts.red_flags : []
  const normalized = raw_flags.map((item) => String(item || '').trim()).filter((item) => Boolean(item))
  return [...new Set(normalized)]
})
</script>

<style scoped>
.section-block {
  margin-top: 12px;
}

.section-block h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #303133;
}

.plain-text {
  margin: 0;
  color: #606266;
  line-height: 1.6;
  white-space: pre-wrap;
}

.list-tag {
  margin: 0 8px 8px 0;
}
</style>
