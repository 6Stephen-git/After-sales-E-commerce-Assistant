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
      <div class="sender-row">
        <span class="sender-label">以身份发送</span>
        <el-radio-group
          :model-value="sender_role"
          size="small"
          @update:model-value="emit_update_sender_role"
        >
          <el-radio-button label="merchant">商家</el-radio-button>
          <el-radio-button label="buyer">买家（自测）</el-radio-button>
        </el-radio-group>
      </div>
      <el-input
        :model-value="input_text"
        type="textarea"
        :rows="3"
        :placeholder="input_placeholder"
        @update:model-value="emit_update_input_text"
      />
      <div class="action-row">
        <input
          ref="image_input_ref"
          class="hidden-input"
          type="file"
          accept="image/*"
          @change="handle_image_change"
        />
        <el-button @click="open_image_picker">发送图片</el-button>
        <el-button type="success" @click="emit_send_message">发送文字</el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import MessageItem from './MessageItem.vue'

// ---------- 组件输入：聊天数据、发送身份与加载状态 ----------
const props = defineProps({
  messages: {
    type: Array,
    required: true
  },
  input_text: {
    type: String,
    default: ''
  },
  sender_role: {
    type: String,
    default: 'merchant'
  },
  loading: {
    type: Boolean,
    default: false
  }
})

// ---------- 组件输出：上抛输入、身份、发送与分析触发事件 ----------
const emit = defineEmits([
  'update:input_text',
  'update:sender_role',
  'send_message',
  'send_image',
  'request_ai_help'
])
const image_input_ref = ref(null)

// ---------- 输入框占位：随商家/买家身份切换提示文案 ----------
const input_placeholder = computed(() =>
  props.sender_role === 'buyer'
    ? '以买家身份输入对话内容（用于自行模拟买家）'
    : '以商家身份输入要发送给买家的内容'
)

// ---------- 输入同步：把文本变化同步到上层状态 ----------
function emit_update_input_text(value) {
  emit('update:input_text', value)
}

// ---------- 发送身份：同步到父组件（商家 / 买家） ----------
function emit_update_sender_role(value) {
  emit('update:sender_role', value)
}

// ---------- 上抛发送：由父组件按当前 sender_role 写入消息 ----------
function emit_send_message() {
  emit('send_message')
}

// ---------- 图片发送：选择本地图片后上抛给父组件 ----------
function open_image_picker() {
  image_input_ref.value?.click()
}

function handle_image_change(event) {
  const file = event?.target?.files?.[0]
  if (!file) {
    return
  }
  emit('send_image', file)
  event.target.value = ''
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

.sender-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.sender-label {
  color: #606266;
  font-size: 13px;
}

.action-row {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.hidden-input {
  display: none;
}
</style>
