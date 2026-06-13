<template>
  <div class="intelligent-layout">
    <!-- 左栏：对话主区域，占满高度 -->
    <section class="chat-column">
      <el-card class="chat-panel">
        <template #header>
          <div class="chat-header">
            <span>
              智能对话
              <el-tag v-if="round_count > 0" size="small" type="info" class="round-tag">
                第 {{ round_count }} 轮
              </el-tag>
            </span>
            <div class="chat-header-actions">
              <el-button size="small" @click="reset_conversation">重置对话</el-button>
              <el-button size="small" type="warning" :loading="loading" @click="do_takeover">
                商家接管
              </el-button>
            </div>
          </div>
        </template>

        <div ref="messages_container_ref" class="messages-container">
          <div v-if="messages.length === 0" class="empty-hint">
            在右侧填写纠纷编号后，以买家身份发送消息开始对话
          </div>
          <div
            v-for="msg in messages"
            :key="msg.id"
            class="message-row"
            :class="message_row_class(msg)"
          >
            <div class="message-bubble">
              <span class="role-label">{{ message_role_label(msg) }}</span>
              <p v-if="msg.content" class="message-text">{{ msg.content }}</p>
              <el-image
                v-if="msg.image_url"
                class="message-image"
                :src="msg.image_url"
                :preview-src-list="[msg.image_url]"
                fit="cover"
                preview-teleported
              />
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
            :disabled="is_input_locked"
            @keydown.enter.exact.prevent="() => send_buyer_message()"
          />
          <div class="input-actions">
            <div class="input-left">
              <input
                ref="image_input_ref"
                class="hidden-input"
                type="file"
                accept="image/*"
                :disabled="is_input_locked"
                @change="handle_image_change"
              />
              <el-button :disabled="is_input_locked" @click="open_image_picker">发送图片</el-button>
              <span class="input-hint">Enter 发送，Shift+Enter 换行</span>
            </div>
            <el-button
              type="primary"
              :loading="loading"
              :disabled="!can_send_message"
              @click="() => send_buyer_message()"
            >
              发送
            </el-button>
          </div>
          <p v-if="is_input_locked" class="input-lock-hint">已转人工，点击「重置对话」后可继续测试</p>
          <p v-else-if="!dispute_id.trim()" class="input-lock-hint">请先在右侧填写纠纷编号</p>
        </div>
      </el-card>
    </section>

    <!-- 右栏：配置、模拟、状态 -->
    <section class="side-column">
      <el-alert
        v-if="error_message"
        type="error"
        :title="error_message"
        :closable="false"
        show-icon
        class="side-alert"
      />

      <el-alert
        v-if="handoff_suggestion && !is_input_locked"
        type="warning"
        :closable="false"
        show-icon
        class="side-alert"
      >
        <template #title>
          <span>建议转人工：{{ handoff_suggestion.reason }}</span>
        </template>
        <div class="handoff-actions">
          <el-button size="small" type="warning" @click="accept_handoff_suggestion">转人工</el-button>
          <el-button size="small" @click="continue_conversation">继续对话</el-button>
        </div>
      </el-alert>

      <el-alert
        v-if="handoff_info"
        type="warning"
        :closable="false"
        show-icon
        class="side-alert"
      >
        <template #title>
          <span>已触发转人工：{{ handoff_info.reason }}</span>
        </template>
      </el-alert>

      <el-card class="config-card">
        <template #header>
          <span>案件配置</span>
        </template>
        <el-form label-width="72px" size="small" class="config-form">
          <el-form-item label="纠纷编号">
            <el-input v-model="dispute_id" placeholder="必填" />
          </el-form-item>
          <el-form-item label="订单号">
            <el-input v-model="order_id" placeholder="如 9999" />
            <p class="field-remark">对应 orders 里的键名，触发物流查询</p>
          </el-form-item>
          <el-form-item label="订单金额">
            <el-input-number
              v-model="order_amount"
              :min="0"
              :step="10"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="买家ID">
            <el-input v-model="buyer_id" placeholder="可选" />
            <p class="field-remark">对应 buyers 里的键名，触发买家画像查询</p>
          </el-form-item>
          <el-form-item label="赔偿上限">
            <el-input-number
              v-model="max_compensation"
              :min="0"
              :step="10"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
        </el-form>
      </el-card>

      <el-collapse v-model="simulation_expanded" class="simulation-collapse">
        <el-collapse-item name="simulation">
          <template #title>
            <span class="simulation-title">测试模拟数据</span>
            <el-tag v-if="simulation_enabled" size="small" type="success" class="simulation-tag">已启用</el-tag>
          </template>
          <div class="simulation-body">
            <div class="simulation-toolbar">
              <el-switch v-model="simulation_enabled" active-text="启用" inactive-text="关闭" />
              <el-button size="small" :loading="simulation_loading" @click="load_simulation">刷新</el-button>
              <el-button size="small" type="primary" :loading="simulation_loading" @click="save_simulation">
                保存
              </el-button>
            </div>
            <p class="simulation-hint">
              左侧「订单号」「买家ID」与下方 JSON 的键名对应；保存后 Agent 调物流/画像工具时走模拟数据。
            </p>
            <div class="simulation-legend">
              <p class="legend-block-title">orders — 按订单号索引，对应工具 query_logistics</p>
              <ul class="legend-list">
                <li><code>order_amount</code> 订单金额（配置区未填时自动补全）</li>
                <li><code>buyer_id</code> 关联下方 buyers 的键名</li>
                <li><code>product_name</code> 商品名（仅备注，Agent 不直接读）</li>
                <li><code>logistics.is_shipped</code> 是否已发货</li>
                <li><code>logistics.is_signed</code> 是否已签收</li>
                <li><code>logistics.stagnant_days</code> 物流停滞天数</li>
                <li><code>logistics.status_text</code> 物流状态描述（给 Agent 看）</li>
              </ul>
              <p class="legend-block-title">buyers — 按买家ID索引，对应工具 query_buyer_profile</p>
              <ul class="legend-list">
                <li><code>purchase_count</code> 本店购买次数</li>
                <li><code>dispute_count</code> / <code>dispute_rate</code> 纠纷次数与纠纷率</li>
                <li><code>credit_level</code> 信誉：low / medium / high</li>
                <li><code>malicious_flags</code> 被标恶意次数</li>
              </ul>
            </div>
            <p class="simulation-example">示例：订单号填 <strong>9999</strong> → 读 orders["9999"]；买家ID可留空，由订单里的 buyer_id 带出。</p>
            <el-input
              v-model="simulation_json"
              type="textarea"
              :rows="6"
              placeholder="orders 与 buyers 的 JSON"
              class="simulation-editor"
            />
          </div>
        </el-collapse-item>
      </el-collapse>

      <div class="state-wrap">
        <StatePanel :state="agent_state" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted } from 'vue'
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
  max_compensation,
  handoff_info,
  handoff_suggestion,
  round_count,
  simulation_enabled,
  simulation_json,
  simulation_loading,
  is_input_locked,
  can_send_message,
  send_buyer_message,
  send_buyer_image,
  continue_conversation,
  accept_handoff_suggestion,
  do_takeover,
  reset_conversation,
  load_simulation,
  save_simulation
} = use_intelligent()

const messages_container_ref = ref(null)
const image_input_ref = ref(null)
const simulation_expanded = ref([])

onMounted(() => {
  if (!dispute_id.value.trim()) {
    dispute_id.value = 'demo-001'
  }
  load_simulation()
})

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

function message_row_class(msg) {
  if (msg.role === 'buyer') return 'is-buyer'
  if (msg.role === 'system') return 'is-system'
  return 'is-agent'
}

function message_role_label(msg) {
  if (msg.role === 'buyer') return '买家'
  if (msg.role === 'system') return '系统'
  return '客服'
}

function open_image_picker() {
  image_input_ref.value?.click()
}

function handle_image_change(event) {
  const file = event?.target?.files?.[0]
  if (!file) {
    return
  }
  send_buyer_image(file)
  event.target.value = ''
}
</script>

<style scoped>
.intelligent-layout {
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 12px;
}

.chat-column {
  min-height: 0;
  height: 100%;
}

.chat-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chat-panel :deep(.el-card__body) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.chat-header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.round-tag {
  margin-left: 8px;
}

.messages-container {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 8px;
  display: flex;
  flex-direction: column;
}

.empty-hint {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  padding: 16px;
  font-size: 14px;
  text-align: center;
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

.message-image {
  margin-top: 8px;
  width: 180px;
  max-width: 100%;
  border-radius: 6px;
}

.time-label {
  display: block;
  font-size: 11px;
  color: #c0c4cc;
  margin-top: 4px;
  text-align: right;
}

.input-area {
  flex-shrink: 0;
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

.input-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.input-hint {
  font-size: 12px;
  color: #c0c4cc;
}

.input-lock-hint {
  margin: 0;
  font-size: 12px;
  color: #e6a23c;
}

.hidden-input {
  display: none;
}

.side-column {
  min-height: 0;
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 2px;
}

.side-alert {
  flex-shrink: 0;
}

.config-card {
  flex-shrink: 0;
}

.config-form :deep(.el-form-item) {
  margin-bottom: 8px;
}

.field-remark {
  margin: 4px 0 0;
  font-size: 11px;
  color: #909399;
  line-height: 1.4;
}

.simulation-collapse {
  flex-shrink: 0;
}

.simulation-collapse :deep(.el-collapse-item__header) {
  height: 40px;
  line-height: 40px;
  padding-left: 12px;
  background: #fff;
  border-radius: 4px;
}

.simulation-collapse :deep(.el-collapse-item__wrap) {
  background: #fff;
  border-radius: 0 0 4px 4px;
}

.simulation-title {
  font-size: 14px;
  font-weight: 500;
}

.simulation-tag {
  margin-left: 8px;
}

.simulation-body {
  padding: 0 12px 12px;
}

.simulation-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.simulation-hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}

.simulation-legend {
  margin-bottom: 8px;
  padding: 8px 10px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}

.legend-block-title {
  margin: 0 0 4px;
  font-weight: 600;
  color: #303133;
}

.legend-block-title + .legend-list {
  margin-top: 0;
}

.legend-list {
  margin: 0 0 8px 0;
  padding-left: 18px;
}

.legend-list li {
  margin-bottom: 2px;
}

.legend-list code {
  font-size: 11px;
  color: #409eff;
  background: #ecf5ff;
  padding: 0 4px;
  border-radius: 2px;
}

.simulation-example {
  margin: 0 0 8px;
  font-size: 12px;
  color: #909399;
}

.simulation-editor :deep(textarea) {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.state-wrap {
  flex: 1;
  min-height: 200px;
}

.handoff-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}
</style>
