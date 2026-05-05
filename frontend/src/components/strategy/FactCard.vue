<template>
  <el-card>
    <template #header>
      <span>事实摘要</span>
    </template>

    <el-descriptions :column="1" border>
      <el-descriptions-item label="证据质量">
        <el-tag :type="quality_tag_type">{{ evidence_quality_label }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="瑕疵类型">
        {{ facts?.defect_type || '未识别' }}
      </el-descriptions-item>
      <el-descriptions-item label="疑点数量">
        {{ red_flags.length }}
      </el-descriptions-item>
      <el-descriptions-item label="缺失证据项">
        {{ missing_evidence.length }}
      </el-descriptions-item>
    </el-descriptions>

    <div class="list-block">
      <h4>疑点列表</h4>
      <el-empty v-if="red_flags.length === 0" description="暂无疑点" :image-size="60" />
      <el-tag v-for="item in red_flags" :key="item" class="list-tag" type="danger">{{ item }}</el-tag>
    </div>

    <div class="list-block">
      <h4>缺失证据</h4>
      <el-empty v-if="missing_evidence.length === 0" description="证据完整" :image-size="60" />
      <el-tag v-for="item in missing_evidence" :key="item" class="list-tag" type="warning">{{ item }}</el-tag>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { getEvidenceQualityLabel } from '../../utils/enums'

// ---------- 组件输入：事实分析结果 ----------
const props = defineProps({
  facts: {
    type: Object,
    default: null
  }
})

// ---------- 派生状态：证据质量中文标签 ----------
const evidence_quality_label = computed(() => {
  return getEvidenceQualityLabel(props.facts?.evidence_quality)
})

// ---------- 派生状态：证据质量标签颜色 ----------
const quality_tag_type = computed(() => {
  const quality = props.facts?.evidence_quality
  if (quality === 'high') {
    return 'success'
  }
  if (quality === 'low') {
    return 'danger'
  }
  return 'info'
})

// ---------- 派生状态：疑点数组 ----------
const red_flags = computed(() => {
  return Array.isArray(props.facts?.red_flags) ? props.facts.red_flags : []
})

// ---------- 派生状态：缺失证据数组 ----------
const missing_evidence = computed(() => {
  return Array.isArray(props.facts?.missing_evidence) ? props.facts.missing_evidence : []
})
</script>

<style scoped>
.list-block {
  margin-top: 12px;
}

.list-block h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #303133;
}

.list-tag {
  margin: 0 8px 8px 0;
}
</style>
