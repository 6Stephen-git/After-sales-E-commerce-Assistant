<template>
  <el-card>
    <template #header>
      <span>事实摘要</span>
    </template>

    <!-- 客户诉求 -->
    <div class="section-block">
      <h4 class="section-title">客户诉求</h4>
      <p class="plain-text">{{ issue_summary || '暂未提取到明确诉求' }}</p>
    </div>

    <!-- 事实还原：首条可见，其余折叠 -->
    <div class="section-block">
      <h4 class="section-title">事实还原</h4>
      <p v-if="primary_visual" class="plain-text">{{ primary_visual }}</p>
      <el-empty v-else description="暂无事实还原内容" :image-size="60" />

      <el-collapse v-if="folded_fact_count > 0" v-model="fact_more_active" class="fact-more-collapse">
        <el-collapse-item name="more">
          <template #title>
            <span>{{ fact_more_title }}</span>
          </template>
          <ul v-if="folded_visual_items.length > 0" class="folded-list">
            <li v-for="(item, idx) in folded_visual_items" :key="'fold-vis-' + idx" class="folded-item">
              {{ item }}
            </li>
          </ul>
          <div v-if="has_logistics_in_fold" class="folded-logistics">
            <span class="folded-label">物流情况</span>
            <p class="plain-text">{{ logistics_summary }}</p>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>
  </el-card>
</template>

<script setup>
import { computed, ref } from 'vue'

// ---------- 组件输入：事实分析结果 ----------
const props = defineProps({
  facts: {
    type: Object,
    default: null
  }
})

// ---------- 派生状态：客户诉求摘要 ----------
const issue_summary = computed(() => {
  return String(props.facts?.issue_summary || '').trim()
})

// ---------- 派生状态：图片/视频描述完整列表（去重归一） ----------
const visual_all = computed(() => {
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

// ---------- 派生状态：事实还原首条可见 ----------
const primary_visual = computed(() => visual_all.value[0] || '')

// ---------- 派生状态：折叠区视觉条目（第二条起） ----------
const folded_visual_items = computed(() => visual_all.value.slice(1))

// ---------- 派生状态：物流摘要 ----------
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

// ---------- 派生状态：物流是否放入折叠区 ----------
const has_logistics_in_fold = computed(() => {
  if (logistics_summary.value === '暂无物流信息') {
    return false
  }
  return Boolean(primary_visual.value)
})

// ---------- 折叠状态：展开后标题切换为「其他事实」 ----------
const fact_more_active = ref([])

const fact_more_title = computed(() => {
  if (fact_more_active.value.includes('more')) {
    return '其他事实'
  }
  return `展开更多事实（${folded_fact_count.value} 条）`
})

// ---------- 派生状态：折叠区条目总数 ----------
const folded_fact_count = computed(() => {
  let count = folded_visual_items.value.length
  if (has_logistics_in_fold.value) {
    count += 1
  }
  return count
})
</script>

<style scoped>
.section-block {
  margin-top: 12px;
}

.section-block:first-child {
  margin-top: 0;
}

.section-title {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.plain-text {
  margin: 0;
  color: #606266;
  line-height: 1.65;
  white-space: pre-wrap;
}

.fact-more-collapse {
  margin-top: 10px;
}

.folded-list {
  margin: 0;
  padding-left: 18px;
  color: #606266;
  line-height: 1.65;
}

.folded-item {
  margin-bottom: 6px;
}

.folded-item:last-child {
  margin-bottom: 0;
}

.folded-logistics {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #ebeef5;
}

.folded-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  margin-bottom: 4px;
}
</style>
