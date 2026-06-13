import { computed, ref } from 'vue'
import {
  sendIntelligentMessage,
  intelligentTakeover,
  fetchIntelligentStatus,
  fetchIntelligentSimulation,
  saveIntelligentSimulation
} from '../api'

// ---------- 模块级状态：跨路由切换保留智能模式对话 ----------
const messages = ref([])
const agent_state = ref(null)
const loading = ref(false)
const input_text = ref('')
const error_message = ref('')
const dispute_id = ref('')
const order_id = ref('')
const order_amount = ref(0)
const buyer_id = ref('')
const merchant_id = ref('')
const max_compensation = ref(0)
const handoff_info = ref(null)
const handoff_suggestion = ref(null)
const dismiss_round_handoff = ref(false)
const message_id_seed = ref(1)
const round_count = ref(0)
const simulation_enabled = ref(true)
const simulation_json = ref('')
const simulation_loading = ref(false)

// ---------- 回复拆句：将 LLM 回复按句号/感叹号/问号拆成多个气泡 ----------
function split_reply_to_chunks(text) {
  const raw = String(text || '').trim()
  if (!raw) return []
  const parts = raw.split(/(?<=[。！？!?])/)
  const chunks = parts.map(s => s.trim()).filter(Boolean)
  return chunks.length > 0 ? chunks : [raw]
}

// ---------- 文件读取：将图片转换为 data URL ----------
function read_file_as_data_url(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('读取图片失败，请重试'))
    reader.readAsDataURL(file)
  })
}

// ---------- 智能模式对话管理：买家消息发送、Agent 回复、状态追踪 ----------
export function use_intelligent() {
  const has_state = computed(() => agent_state.value !== null)
  const is_input_locked = computed(() => Boolean(handoff_info.value))
  const can_send_message = computed(() => !loading.value && !is_input_locked.value)

  // ---------- 消息追加：支持纯文本或附图 ----------
  function append_message(role, content, image_url = '') {
    const normalized = String(content || '').trim()
    const normalized_image = String(image_url || '').trim()
    if (!normalized && !normalized_image) {
      return
    }
    messages.value.push({
      id: message_id_seed.value++,
      role,
      content: normalized || (normalized_image ? '[图片]' : ''),
      image_url: normalized_image || undefined,
      timestamp: new Date().toLocaleTimeString()
    })
  }

  // ---------- 处理 Agent 回复：拆句、转人工建议、状态更新 ----------
  function handle_agent_reply(reply) {
    if (reply.handoff) {
      handoff_info.value = {
        reason: reply.handoff_reason || '触发转人工',
        summary: reply.handoff_summary || ''
      }
      handoff_suggestion.value = null
      agent_state.value = reply.state
      append_message('system', `【转人工】${reply.handoff_reason || '需人工介入'}`)
      if (reply.handoff_summary) {
        append_message('system', `【交接摘要】${reply.handoff_summary}`)
      }
      return
    }

    if (reply.handoff_suggested) {
      handoff_suggestion.value = {
        reason: reply.handoff_reason || '建议转人工'
      }
    } else {
      handoff_suggestion.value = null
    }

    if (reply.reply_text) {
      const chunks = split_reply_to_chunks(reply.reply_text)
      for (const chunk of chunks) {
        append_message('agent', chunk)
      }
    }
    agent_state.value = reply.state
    round_count.value += 1
  }

  // ---------- 发送买家消息：调用智能模式 API ----------
  async function send_buyer_message(extra = {}) {
    const options = extra && typeof extra === 'object' && !('target' in extra) ? extra : {}
    const raw_text = input_text.value.trim()
    const image_urls = Array.isArray(options.image_urls) ? options.image_urls : []
    if ((!raw_text && image_urls.length === 0) || loading.value || is_input_locked.value) {
      return
    }
    if (!dispute_id.value.trim()) {
      error_message.value = '请先在右侧填写纠纷编号'
      return
    }

    append_message('buyer', raw_text || '[图片]', image_urls[0] || '')
    input_text.value = ''
    loading.value = true
    error_message.value = ''

    try {
      const payload = {
        dispute_id: dispute_id.value.trim(),
        buyer_message: raw_text,
        order_id: order_id.value.trim(),
        order_amount: Number(order_amount.value) || 0,
        buyer_id: buyer_id.value.trim(),
        merchant_id: merchant_id.value.trim(),
        max_compensation: Number(max_compensation.value) || 0,
        chat_history: build_chat_history(),
        round_count: round_count.value,
        image_urls,
        dismiss_round_handoff: dismiss_round_handoff.value
      }

      const reply = await sendIntelligentMessage(payload)
      handle_agent_reply(reply)
    } catch (error) {
      error_message.value = error.message || '智能模式请求失败'
    } finally {
      loading.value = false
    }
  }

  // ---------- 发送图片：读取本地文件后以 data URL 发送 ----------
  async function send_buyer_image(file) {
    if (!(file instanceof File)) {
      return
    }
    if (!file.type?.startsWith('image/')) {
      error_message.value = '仅支持发送图片文件'
      return
    }
    try {
      const image_data_url = await read_file_as_data_url(file)
      await send_buyer_message({ image_urls: [image_data_url] })
      error_message.value = ''
    } catch (error) {
      error_message.value = error.message || '发送图片失败，请重试'
    }
  }

  // ---------- 用户选择继续对话：忽略轮次转人工建议 ----------
  function continue_conversation() {
    dismiss_round_handoff.value = true
    handoff_suggestion.value = null
    append_message('system', '【继续对话】已选择继续由智能客服处理')
  }

  // ---------- 用户接受转人工建议 ----------
  async function accept_handoff_suggestion() {
    handoff_suggestion.value = null
    await do_takeover()
  }

  // ---------- 构建聊天历史：转换为后端需要的格式 ----------
  function build_chat_history() {
    const history = []
    for (const msg of messages.value) {
      if (msg.role === 'buyer' || msg.role === 'agent') {
        history.push({
          role: msg.role === 'buyer' ? 'buyer' : 'merchant',
          content: msg.content
        })
      }
    }
    return history.slice(0, -1)
  }

  // ---------- 商家接管：调用接管 API ----------
  async function do_takeover() {
    if (!dispute_id.value.trim()) {
      error_message.value = '请先填写纠纷编号'
      return
    }
    loading.value = true
    error_message.value = ''
    try {
      const result = await intelligentTakeover(dispute_id.value.trim())
      if (result.status === 'taken_over') {
        handoff_info.value = {
          reason: '商家主动接管',
          summary: result.summary || ''
        }
        handoff_suggestion.value = null
        agent_state.value = result.state
        append_message('system', '【商家接管】已转为人工处理')
        if (result.summary) {
          append_message('system', `【交接摘要】${result.summary}`)
        }
      } else {
        error_message.value = result.message || '未找到案件状态'
      }
    } catch (error) {
      error_message.value = error.message || '接管请求失败'
    } finally {
      loading.value = false
    }
  }

  // ---------- 查询状态：获取当前案件状态 ----------
  async function refresh_status() {
    if (!dispute_id.value.trim()) {
      return
    }
    try {
      const result = await fetchIntelligentStatus(dispute_id.value.trim())
      if (result.status === 'active' && result.state) {
        agent_state.value = result.state
      }
    } catch {
      // 静默处理
    }
  }

  // ---------- 加载模拟配置 ----------
  async function load_simulation() {
    simulation_loading.value = true
    try {
      const data = await fetchIntelligentSimulation()
      simulation_enabled.value = Boolean(data.enabled)
      const { path: _path, enabled: _enabled, ...rest } = data
      simulation_json.value = JSON.stringify(rest, null, 2)
    } catch (error) {
      error_message.value = error.message || '读取模拟配置失败'
    } finally {
      simulation_loading.value = false
    }
  }

  // ---------- 保存模拟配置 ----------
  async function save_simulation() {
    simulation_loading.value = true
    error_message.value = ''
    try {
      const parsed = JSON.parse(simulation_json.value || '{}')
      const payload = {
        enabled: simulation_enabled.value,
        orders: parsed.orders || {},
        buyers: parsed.buyers || {}
      }
      await saveIntelligentSimulation(payload)
    } catch (error) {
      error_message.value = error.message || '保存模拟配置失败（请检查 JSON 格式）'
    } finally {
      simulation_loading.value = false
    }
  }

  // ---------- 重置对话：清空所有状态 ----------
  function reset_conversation() {
    messages.value = []
    agent_state.value = null
    handoff_info.value = null
    handoff_suggestion.value = null
    dismiss_round_handoff.value = false
    error_message.value = ''
    round_count.value = 0
    message_id_seed.value = 1
  }

  return {
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
    handoff_suggestion,
    round_count,
    simulation_enabled,
    simulation_json,
    simulation_loading,
    has_state,
    is_input_locked,
    can_send_message,
    send_buyer_message,
    send_buyer_image,
    continue_conversation,
    accept_handoff_suggestion,
    do_takeover,
    refresh_status,
    load_simulation,
    save_simulation,
    reset_conversation,
    append_message
  }
}
