<template>
  <el-alert
    v-if="emotion_alert?.alert_triggered"
    title="情绪预警"
    type="warning"
    :description="alert_description"
    :closable="false"
    show-icon
  />
</template>

<script setup>
import { computed } from 'vue'
import { getSentimentLabel } from '../../utils/enums'

// ---------- 组件输入：情绪预警对象 ----------
const props = defineProps({
  emotion_alert: {
    type: Object,
    default: null
  }
})

// ---------- 展示文本：合并预警信息与情绪标签 ----------
const alert_description = computed(() => {
  if (!props.emotion_alert) {
    return ''
  }
  const sentiment_label = getSentimentLabel(props.emotion_alert.sentiment)
  const reason_text = props.emotion_alert.alert_reason || '检测到潜在情绪风险'
  const message_text = props.emotion_alert.alert_message || ''
  return `${reason_text}（情绪：${sentiment_label}）${message_text ? `；建议：${message_text}` : ''}`
})
</script>
