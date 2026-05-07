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
  // ---------- 发送身份：商家侧或用户扮演买家，决定 append_message 的 role ----------
  const sender_role = ref('merchant')
  const error_message = ref('')
  const message_id_seed = ref(messages.value.length + 1)

  // ---------- 派生状态：当前是否已有分析报告 ----------
  const has_report = computed(() => report.value !== null)

  // ---------- 消息追加：将一条对话记录追加到本地会话 ----------
  function append_message(role, content, image_url = '', image_name = '') {
    const normalized_content = String(content || '').trim()
    const normalized_image_url = String(image_url || '').trim()
    const normalized_image_name = String(image_name || '').trim()
    if (!normalized_content && !normalized_image_url) {
      return
    }

    messages.value.push({
      id: message_id_seed.value,
      role,
      content: normalized_content,
      image_url: normalized_image_url || undefined,
      image_name: normalized_image_name || undefined
    })
    message_id_seed.value += 1
  }

  // ---------- 消息发送：按当前 sender_role 写入 buyer 或 merchant ----------
  function send_message() {
    const raw_text = input_text.value.trim()
    if (!raw_text) {
      return
    }
    const role = sender_role.value === 'buyer' ? 'buyer' : 'merchant'
    append_message(role, raw_text)
    input_text.value = ''
  }

  // ---------- 图片发送：读取本地图片为 data URL 并写入消息 ----------
  async function send_image(file) {
    if (!(file instanceof File)) {
      return
    }
    if (!file.type?.startsWith('image/')) {
      error_message.value = '仅支持发送图片文件'
      return
    }
    try {
      const role = sender_role.value === 'buyer' ? 'buyer' : 'merchant'
      const image_data_url = await read_file_as_data_url(file)
      append_message(role, input_text.value.trim() || '[图片]', image_data_url, file.name)
      input_text.value = ''
      error_message.value = ''
    } catch (error) {
      error_message.value = error.message || '发送图片失败，请重试'
    }
  }

  // ---------- 文件读取：将图片转换为可预览与可传输的 data URL ----------
  function read_file_as_data_url(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result || ''))
      reader.onerror = () => reject(new Error('读取图片失败，请重试'))
      reader.readAsDataURL(file)
    })
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
        image_urls: messages.value
          .map((item) => String(item.image_url || '').trim())
          .filter((url) => Boolean(url))
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
    sender_role,
    error_message,
    has_report,
    append_message,
    send_message,
    send_image,
    apply_script,
    request_ai_help
  }
}
