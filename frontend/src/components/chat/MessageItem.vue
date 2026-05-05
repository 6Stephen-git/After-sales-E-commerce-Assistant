<template>
  <div class="message-row" :class="row_class">
    <div class="message-bubble">
      <span class="role-label">{{ role_label }}</span>
      <p class="message-text">{{ message.content }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

// ---------- 组件输入：单条消息 ----------
const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

// ---------- 样式状态：根据角色决定消息左右对齐 ----------
const row_class = computed(() => {
  return props.message.role === 'merchant' ? 'is-merchant' : 'is-buyer'
})

// ---------- 展示文本：角色标签中文化 ----------
const role_label = computed(() => {
  return props.message.role === 'merchant' ? '商家' : '买家'
})
</script>

<style scoped>
.message-row {
  display: flex;
  margin-bottom: 12px;
}

.message-row.is-buyer {
  justify-content: flex-start;
}

.message-row.is-merchant {
  justify-content: flex-end;
}

.message-bubble {
  max-width: 80%;
  border-radius: 8px;
  padding: 10px 12px;
  background-color: #f2f6fc;
}

.message-row.is-merchant .message-bubble {
  background-color: #ecf5ff;
}

.role-label {
  display: inline-block;
  font-size: 12px;
  color: #606266;
  margin-bottom: 6px;
}

.message-text {
  margin: 0;
  color: #303133;
  line-height: 1.5;
  white-space: pre-wrap;
}
</style>
