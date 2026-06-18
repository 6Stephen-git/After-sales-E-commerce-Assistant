<template>
  <el-alert
    v-if="should_show"
    title="情绪提醒"
    :type="alert_type"
    :description="alert_description"
    :closable="false"
    show-icon
  />
</template>

<script setup>
import { computed } from 'vue'

// ---------- 组件输入：情绪预警对象（侧栏展示，不含弹窗级分析数据） ----------
const props = defineProps({
  emotion_alert: {
    type: Object,
    default: null
  }
})

const should_show = computed(() => {
  const alert = props.emotion_alert
  return Boolean(alert?.early_warn_triggered || alert?.alert_triggered)
})

const alert_type = computed(() => (
  props.emotion_alert?.alert_triggered ? 'warning' : 'info'
))

const alert_description = computed(() => {
  const alert = props.emotion_alert
  if (!alert) {
    return ''
  }
  if (alert.early_warn_triggered && !alert.alert_triggered) {
    return alert.early_warn_message || '您最近语气偏硬，建议放慢节奏、先确认事实再表态。'
  }
  return alert.emotion_note || alert.early_warn_message || '请注意沟通措辞，避免激化纠纷。'
})
</script>
