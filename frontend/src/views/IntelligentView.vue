<template>
  <div class="intelligent-layout">
    <!-- 左栏：配置 + 对话 -->
    <section class="chat-column">
      <el-card class="config-card">
        <template #header>
          <div class="config-header">
            <span>案件配置</span>
            <div class="config-actions">
              <el-button size="small" @click="reset_conversation">重置对话</el-button>
              <el-button size="small" type="warning" :loading="loading" @click="do_takeover">
                商家接管
              </el-button>
            </div>
          </div>
        </template>
        <el-form :model="{}" label-width="80px" size="small" class="config-form">
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="纠纷编号">
                <el-input v-model="dispute_id" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="订单号">
                <el-input v-model="order_id" placeholder="可选" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="12">
            <el-col :span="8">
              <el-form-item label="订单金额">
                <el-input-number v-model="order_amount" :min="0" :step="10" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="买家ID">
                <el-input v-model="buyer_id" placeholder="可选" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="赔偿上限">
                <el-input-number v-model="max_compensation" :min="0" :step="10" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
          </el-row>
        </el-form>
      </el-card>

      <!-- 对话区域 -->
      <el-card class="chat-card">
        <template #header>
          <span>智能对话 <el-tag v-if="round_count > 0" size="small" type="info">第 {{ round_count }} 轮</el-tag></span>
        </template>

        <div ref="messages_container_ref" class="messages-container">
          <div v-if="messages.length === 0" class="empty-hint">
            填写纠纷编号后，以买家身份发送消息开始对话
          </div>
          <div
            v-for="msg in messages"
            :key="msg.id"
            class="message-row"
            :class="message_row_class(msg)"
          >
            <div class="message-bubble">
              <span class="role-label">{{ message_role_label(msg) }}</span>
              <p class="message-text">{{ msg.content }}</p>
              <span v-if="msg.timestamp" class="time-label">{{ msg.timestamp }}</span>
            </div>
          </div>
          <div v-if="loading" class="message-row is-agent">
            <div class="message-bubble typing">
              <span class="role-label">客服</span>
              <p class="message-text">正在思考中...</p>
            </div>
          </div>
        </div>

        <div class="input-area">
          <el-input
            v-model="input_text"
            type="textarea"
            :rows="3"
            placeholder="以买家身份输入消息..."
            :disabled="is_handoff"
            @keydown.enter.ctrl="send_buyer_message"
          />
          <div class="input-actions">
            <span class="input-hint">Ctrl+Enter 发送</span>
            <el-button
              type="primary"
              :loading="loading"
              :disabled="is_handoff"
              @click="send_buyer_message"
            >
              发送
            </el-button>
          </div>
        </div>
      </el-card>
    </section>

    <!-- 右栏：状态面板 -->
    <section class="state-column">
      <el-alert
        v-if="error_message"
        type="error"
        :title="error_message"
        :closable="false"
        show-icon
        class="error-alert"
      />

      <!-- 转人工提示 -->
      <el-alert
        v-if="handoff_info"
        type="warning"
        :closable="false"
        show-icon
        class="handoff-alert"
      >
        <template #title>
          <span>已触发转人工：{{ handoff_info.reason }}</span>
        </template>
      </el-alert>

      <!-- 案件状态卡片 -->
      <StatePanel :state="agent_state" />
    </section>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import StatePanel from '../components/intelligent/StatePanel.vue'
import { use_intelligent } from '../composables/useIntelligent'

const {
  messages,
  agent_state,
  loading,
  input_text,
  error_message,
  dispute_id,
  order_id,
  order_amount,
  buyer_id,
  merchant_id,
  max_compensation,
  handoff_info,
  round_count,
  is_handoff,
  send_buyer_message,
  do_takeover,
  reset_conversation
} = use_intelligent()

const messages_container_ref = ref(null)

// ---------- 自动滚底：新消息出现后滚动到对话底部 ----------
watch(
  () => messages.value.length,
  async () => {
    await nextTick()
    const container = messages_container_ref.value
    if (container) {
      container.scrollTop = container.scrollHeight
    }
  }
)

// ---------- 消息样式：根据角色决定对齐方向 ----------
function message_row_class(msg) {
  if (msg.role === 'buyer') return 'is-buyer'
  if (msg.role === 'system') return 'is-system'
  return 'is-agent'
}

// ---------- 角色标签：中文化 ----------
function message_role_label(msg) {
  if (msg.role === 'buyer') return '买家'
  if (msg.role === 'system') return '系统'
  return '客服'
}
</script>

<style scoped>
.intelligent-layout {
  height: 100%;
  display: grid;
  grid-template-columns: 1fr 380px;
  gap: 12px;
}

.chat-column {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.state-column {
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.config-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.config-actions {
  display: flex;
  gap: 8px;
}

.config-form {
  padding-top: 4px;
}

.config-form :deep(.el-form-item) {
  margin-bottom: 8px;
}

.chat-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.chat-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 4px 8px 4px 0;
}

.empty-hint {
  text-align: center;
  color: #909399;
  padding: 40px 0;
  font-size: 14px;
}

.message-row {
  display: flex;
  margin-bottom: 12px;
}

.message-row.is-buyer {
  justify-content: flex-start;
}

.message-row.is-agent {
  justify-content: flex-end;
}

.message-row.is-system {
  justify-content: center;
}

.message-bubble {
  max-width: 80%;
  border-radius: 8px;
  padding: 10px 12px;
  background-color: #f2f6fc;
}

.message-row.is-agent .message-bubble {
  background-color: #ecf5ff;
}

.message-row.is-system .message-bubble {
  background-color: #fdf6ec;
  max-width: 90%;
  text-align: center;
  font-size: 13px;
}

.message-bubble.typing {
  opacity: 0.7;
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
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.time-label {
  display: block;
  font-size: 11px;
  color: #c0c4cc;
  margin-top: 4px;
  text-align: right;
}

.input-area {
  border-top: 1px solid #ebeef5;
  margin-top: 12px;
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.input-hint {
  font-size: 12px;
  color: #c0c4cc;
}

.error-alert {
  margin-bottom: 0;
}

.handoff-alert {
  margin-bottom: 0;
}
</style>
