import { computed, ref, nextTick } from 'vue'
import { sendIntelligentMessage, intelligentTakeover, fetchIntelligentStatus } from '../api'

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
const message_id_seed = ref(1)
const round_count = ref(0)

// ---------- 回复拆句：将 LLM 回复按句号/感叹号/问号拆成多个气泡 ----------
function split_reply_to_chunks(text) {
  const raw = String(text || '').trim()
  if (!raw) return []
  // 按中文标点或英文标点作为句末断句，保留标点在句子末尾
  const parts = raw.split(/(?<=[。！？!?])/)
  const chunks = parts.map(s => s.trim()).filter(Boolean)
  // 如果没有句末标点（一整句话没断句），直接返回原文
  return chunks.length > 0 ? chunks : [raw]
}

// ---------- 智能模式对话管理：买家消息发送、Agent 回复、状态追踪 ----------
export function use_intelligent() {
  const has_state = computed(() => agent_state.value !== null)
  const is_handoff = computed(() => agent_state.value?.phase === 'handoff')

  // ---------- 消息追加：将一条对话记录追加到本地会话 ----------
  function append_message(role, content) {
    const normalized = String(content || '').trim()
    if (!normalized) {
      return
    }
    messages.value.push({
      id: message_id_seed.value++,
      role,
      content: normalized,
      timestamp: new Date().toLocaleTimeString()
    })
  }

  // ---------- 发送买家消息：调用智能模式 API 并展示 Agent 回复 ----------
  async function send_buyer_message() {
    const raw_text = input_text.value.trim()
    if (!raw_text || loading.value) {
      return
    }
    if (!dispute_id.value.trim()) {
      error_message.value = '请先填写纠纷编号'
      return
    }

    append_message('buyer', raw_text)
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
        round_count: round_count.value
      }

      const reply = await sendIntelligentMessage(payload)

      // 转人工处理
      if (reply.handoff) {
        handoff_info.value = {
          reason: reply.handoff_reason || '触发转人工',
          summary: reply.handoff_summary || ''
        }
        agent_state.value = reply.state
        append_message('system', `【转人工】${reply.handoff_reason || '需人工介入'}`)
        if (reply.handoff_summary) {
          append_message('system', `【交接摘要】${reply.handoff_summary}`)
        }
        return
      }

      // 正常回复：按句子拆成多个气泡
      if (reply.reply_text) {
        const chunks = split_reply_to_chunks(reply.reply_text)
        for (const chunk of chunks) {
          append_message('agent', chunk)
        }
      }
      agent_state.value = reply.state
      round_count.value += 1
    } catch (error) {
      error_message.value = error.message || '智能模式请求失败'
    } finally {
      loading.value = false
    }
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
    // 返回除最后一条（刚发的买家消息）外的历史
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
      // 静默处理：状态查询失败不影响主流程
    }
  }

  // ---------- 重置对话：清空所有状态 ----------
  function reset_conversation() {
    messages.value = []
    agent_state.value = null
    handoff_info.value = null
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
    round_count,
    has_state,
    is_handoff,
    send_buyer_message,
    do_takeover,
    refresh_status,
    reset_conversation,
    append_message
  }
}
