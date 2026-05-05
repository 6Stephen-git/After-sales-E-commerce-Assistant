<template>
  <el-card class="chat-panel">
    <template #header>
      <div class="chat-header">
        <span>聊天窗口</span>
        <el-button type="primary" :loading="loading" @click="emit_request_ai_help">
          请求 AI 帮助
        </el-button>
      </div>
    </template>

    <div class="messages-container">
      <MessageItem v-for="message in messages" :key="message.id" :message="message" />
    </div>

    <div class="input-container">
      <el-input
        :model-value="input_text"
        type="textarea"
        :rows="3"
        placeholder="输入要发送给买家的内容"
        @update:model-value="emit_update_input_text"
      />
      <el-button type="success" @click="emit_send_message">发送</el-button>
    </div>
  </el-card>
</template>

<script setup>
import MessageItem from './MessageItem.vue'

// ---------- 组件输入：聊天数据与加载状态 ----------
defineProps({
  messages: {
    type: Array,
    required: true
  },
  input_text: {
    type: String,
    default: ''
  },
  loading: {
    type: Boolean,
    default: false
  }
})

// ---------- 组件输出：上抛输入、发送与分析触发事件 ----------
const emit = defineEmits(['update:input_text', 'send_message', 'request_ai_help'])

// ---------- 输入同步：把文本变化同步到上层状态 ----------
function emit_update_input_text(value) {
  emit('update:input_text', value)
}

// ---------- 手动发送：保持商家主动发送控制权 ----------
function emit_send_message() {
  emit('send_message')
}

// ---------- AI 触发：由商家主动点击请求分析 ----------
function emit_request_ai_help() {
  emit('request_ai_help')
}
</script>

<style scoped>
.chat-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chat-panel :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding-right: 8px;
}

.input-container {
  border-top: 1px solid #ebeef5;
  margin-top: 12px;
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
</style>
