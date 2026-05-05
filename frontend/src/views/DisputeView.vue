<template>
  <div class="dispute-layout">
    <section class="chat-column">
      <ChatPanel
        :messages="messages"
        :input_text="input_text"
        :loading="loading"
        @update:input_text="update_input_text"
        @send_message="send_message"
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
      <StrategyPanel :report="report" :loading="loading" @use_script="apply_script" />
    </section>
  </div>
</template>

<script setup>
import ChatPanel from '../components/chat/ChatPanel.vue'
import StrategyPanel from '../components/strategy/StrategyPanel.vue'
import { use_dispute } from '../composables/useDispute'

// ---------- 组合式状态：管理纠纷对话与分析请求 ----------
const { messages, report, loading, input_text, error_message, send_message, apply_script, request_ai_help } =
  use_dispute()

// ---------- 输入同步：承接 ChatPanel 的双向绑定事件 ----------
function update_input_text(value) {
  input_text.value = value
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
</style>
