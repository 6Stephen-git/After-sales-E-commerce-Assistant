<template>
  <el-card class="chat-panel">
    <template #header>
      <div class="chat-header">
        <span>聊天窗口</span>
        <el-button type="primary" :loading="loading" @click="emit_request_ai_help">
          分析对话
        </el-button>
      </div>
    </template>

    <div class="messages-container">
      <MessageItem
        v-for="message in messages"
        :key="message.id"
        :message="message"
        @recall="emit_recall_message"
      />
    </div>

    <div class="input-container">
      <div class="sender-row">
        <el-radio-group
          :model-value="sender_role"
          size="small"
          @update:model-value="emit_update_sender_role"
        >
          <el-radio-button label="merchant">商家</el-radio-button>
          <el-radio-button label="buyer">买家</el-radio-button>
        </el-radio-group>
      </div>

      <!-- 待发图片预览条 -->
      <div v-if="pending_images.length > 0" class="pending-images">
        <div v-for="img in pending_images" :key="img.id" class="pending-thumb-wrap">
          <el-image class="pending-thumb" :src="img.data_url" fit="cover" />
          <button
            type="button"
            class="pending-remove"
            aria-label="移除图片"
            @click="emit_remove_pending_image(img.id)"
          >×</button>
        </div>
      </div>

      <el-input
        :model-value="input_text"
        type="textarea"
        :rows="3"
        :placeholder="input_placeholder"
        @update:model-value="emit_update_input_text"
        @keydown="handle_input_keydown"
      />
      <div class="action-row">
        <input
          ref="image_input_ref"
          class="hidden-input"
          type="file"
          accept="image/*"
          @change="handle_image_change"
        />
        <el-button class="btn-pick-image" :icon="Picture" circle @click="open_image_picker" />
        <el-button type="success" @click="emit_send_message">发送</el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Picture } from '@element-plus/icons-vue'
import MessageItem from './MessageItem.vue'

// ---------- 组件输入：聊天数据、待发图片、发送身份与加载状态 ----------
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
  pending_images: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  }
})

// ---------- 组件输出：上抛输入、身份、待发图与发送事件 ----------
const emit = defineEmits([
  'update:input_text',
  'update:sender_role',
  'send_message',
  'add_pending_image',
  'remove_pending_image',
  'recall_message',
  'request_ai_help'
])
const image_input_ref = ref(null)

// ---------- 输入框占位：随商家/买家身份切换提示文案 ----------
const input_placeholder = computed(() => {
  const role_hint = props.sender_role === 'buyer'
    ? '输入买家对话内容（模拟买家发言）'
    : '输入要发送给买家的内容'
  return `${role_hint}，Enter 发送，Shift+Enter 换行`
})

// ---------- 输入同步：把文本变化同步到上层状态 ----------
function emit_update_input_text(value) {
  emit('update:input_text', value)
}

// ---------- 发送身份：同步到父组件（商家 / 买家） ----------
function emit_update_sender_role(value) {
  emit('update:sender_role', value)
}

// ---------- 键盘：Enter 发送，Shift+Enter 换行 ----------
function handle_input_keydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) {
    return
  }
  event.preventDefault()
  emit_send_message()
}

// ---------- 上抛发送：由父组件统一写入消息 ----------
function emit_send_message() {
  emit('send_message')
}

// ---------- 待发图：选择本地图片后加入待发列表 ----------
function open_image_picker() {
  image_input_ref.value?.click()
}

function handle_image_change(event) {
  const file = event?.target?.files?.[0]
  if (!file) {
    return
  }
  emit('add_pending_image', file)
  event.target.value = ''
}

function emit_remove_pending_image(image_id) {
  emit('remove_pending_image', image_id)
}

function emit_recall_message(message_id) {
  emit('recall_message', message_id)
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
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
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
  justify-content: flex-end;
}

.pending-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pending-thumb-wrap {
  position: relative;
  width: 64px;
  height: 64px;
}

.pending-thumb {
  width: 64px;
  height: 64px;
  border-radius: 6px;
  border: 1px solid #ebeef5;
}

.pending-remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 50%;
  background: #f56c6c;
  color: #fff;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
}

.action-row {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
}

.btn-pick-image {
  color: #d48806;
  border-color: #f0d78c;
  background-color: #fffbe6;
}

.btn-pick-image:hover {
  color: #ffffff;
  background-color: #e6a23c;
  border-color: #e6a23c;
}

.hidden-input {
  display: none;
}
</style>
