import { computed, ref } from 'vue'
import { analyzeDispute } from '../api'

// ---------- 默认商家与纠纷上下文：用于辅助模式页面演示 ----------
const default_context = {
  dispute_id: 'DISPUTE_DEMO_001',
  merchant_id: 'MERCHANT_DEMO_001',
  order_id: 'ORDER_DEMO_001',
  order_amount: 199.0,
  buyer_id: 'BUYER_HASH_DEMO_001'
}

// ---------- 状态管理：纠纷消息、分析报告与交互状态 ----------
export function use_dispute() {
  const messages = ref([
    { id: 1, role: 'buyer', content: '衣服收到后有破洞，我要退款。' },
    { id: 2, role: 'merchant', content: '您好，麻烦您先提供下破损位置的清晰照片。' }
  ])
  const report = ref(null)
  const loading = ref(false)
  const input_text = ref('')
  const error_message = ref('')
  const message_id_seed = ref(messages.value.length + 1)

  // ---------- 派生状态：当前是否已有分析报告 ----------
  const has_report = computed(() => report.value !== null)

  // ---------- 消息追加：将一条对话记录追加到本地会话 ----------
  function append_message(role, content) {
    const normalized_content = String(content || '').trim()
    if (!normalized_content) {
      return
    }

    messages.value.push({
      id: message_id_seed.value,
      role,
      content: normalized_content
    })
    message_id_seed.value += 1
  }

  // ---------- 消息发送：商家手动发送，保持发送权 ----------
  function send_message() {
    const merchant_text = input_text.value.trim()
    if (!merchant_text) {
      return
    }
    append_message('merchant', merchant_text)
    input_text.value = ''
  }

  // ---------- 话术应用：把选中的 AI 话术填入输入框 ----------
  function apply_script(script_text) {
    input_text.value = String(script_text || '').trim()
  }

  // ---------- 分析请求：调用 /analyze 并刷新策略面板 ----------
  async function request_ai_help() {
    loading.value = true
    error_message.value = ''
    try {
      const payload = {
        dispute_id: default_context.dispute_id,
        merchant_id: default_context.merchant_id,
        messages: messages.value.map((item) => ({
          role: item.role,
          content: item.content
        })),
        order_id: default_context.order_id,
        order_amount: default_context.order_amount,
        buyer_id: default_context.buyer_id,
        image_urls: []
      }
      report.value = await analyzeDispute(payload)
    } catch (error) {
      error_message.value = error.message || '请求 AI 分析失败'
    } finally {
      loading.value = false
    }
  }

  return {
    messages,
    report,
    loading,
    input_text,
    error_message,
    has_report,
    append_message,
    send_message,
    apply_script,
    request_ai_help
  }
}
