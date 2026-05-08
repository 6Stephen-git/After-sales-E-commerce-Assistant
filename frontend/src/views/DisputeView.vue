<template>
  <div class="dispute-layout">
    <section class="chat-column">
      <ChatPanel
        :messages="messages"
        :input_text="input_text"
        :sender_role="sender_role"
        :loading="loading"
        @update:input_text="update_input_text"
        @update:sender_role="update_sender_role"
        @send_message="send_message"
        @send_image="send_image"
        @request_ai_help="request_ai_help"
      />
    </section>

    <section class="strategy-column">
      <el-alert
        v-if="error_message"
        type="error"
        :title="error_message"
        :closable="false"
        show-icon
        class="error-alert"
      />
      <el-alert
        v-else-if="loading && progress_message"
        type="info"
        :title="progress_message"
        :closable="false"
        show-icon
        class="progress-alert"
      />
      <StrategyPanel :report="report" :loading="loading" @use_script="apply_script" />
    </section>
  </div>
</template>

<script setup>
import ChatPanel from '../components/chat/ChatPanel.vue'
import StrategyPanel from '../components/strategy/StrategyPanel.vue'
import { use_dispute } from '../composables/useDispute'

// ---------- 组合式状态：管理纠纷对话与分析请求 ----------
const {
  messages,
  report,
  loading,
  input_text,
  sender_role,
  error_message,
  progress_message,
  send_message,
  send_image,
  apply_script,
  request_ai_help
} = use_dispute()

// ---------- 输入同步：承接 ChatPanel 的双向绑定事件 ----------
function update_input_text(value) {
  input_text.value = value
}

// ---------- 发送身份同步：商家 / 买家切换由子组件回写 ----------
function update_sender_role(value) {
  sender_role.value = value
}
</script>

<style scoped>
.dispute-layout {
  height: 100%;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.chat-column,
.strategy-column {
  min-height: 0;
}

.strategy-column {
  display: flex;
  flex-direction: column;
}

.error-alert {
  margin-bottom: 10px;
}

.progress-alert {
  margin-bottom: 10px;
}
</style>
