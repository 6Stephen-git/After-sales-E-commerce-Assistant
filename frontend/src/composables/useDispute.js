import { computed, ref } from 'vue'
import { analyzeDispute, analyzeDisputeStream } from '../api'

const ENABLE_ANALYZE_STREAM = String(import.meta.env.VITE_ENABLE_ANALYZE_STREAM || '0') === '1'

// ---------- 模块级状态：跨路由切换保留对话和分析结果 ----------
const messages = ref([])
const report = ref(null)
const loading = ref(false)
const input_text = ref('')
const sender_role = ref('merchant')
const pending_images = ref([])
const error_message = ref('')
const progress_message = ref('')
const message_id_seed = ref(messages.value.length + 1)
const pending_image_id_seed = ref(1)
let analyze_abort_controller = null
let pagehide_abort_registered = false

// ---------- 进行中的分析：页面卸载时主动中断 HTTP ----------
function abort_analyze_in_flight() {
  if (analyze_abort_controller) {
    analyze_abort_controller.abort()
    analyze_abort_controller = null
  }
  loading.value = false
  progress_message.value = ''
}

function register_pagehide_abort() {
  if (pagehide_abort_registered || typeof window === 'undefined') {
    return
  }
  pagehide_abort_registered = true
  window.addEventListener('pagehide', abort_analyze_in_flight)
}

function is_request_aborted(error) {
  const name = String(error?.name || '')
  const code = String(error?.code || '')
  const message = String(error?.message || '')
  return (
    name === 'AbortError' ||
    name === 'CanceledError' ||
    code === 'ERR_CANCELED' ||
    /aborted|cancel/i.test(message)
  )
}

// ---------- 状态管理：纠纷消息、分析报告与交互状态 ----------
export function use_dispute() {
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

  // ---------- 待发图片：加入预览列表，不立即写入消息 ----------
  async function add_pending_image(file) {
    if (!(file instanceof File)) {
      return
    }
    if (!file.type?.startsWith('image/')) {
      error_message.value = '仅支持发送图片文件'
      return
    }
    try {
      const image_data_url = await read_file_as_data_url(file)
      pending_images.value.push({
        id: pending_image_id_seed.value,
        data_url: image_data_url,
        name: file.name
      })
      pending_image_id_seed.value += 1
      error_message.value = ''
    } catch (error) {
      error_message.value = error.message || '读取图片失败，请重试'
    }
  }

  // ---------- 待发图片：从预览列表移除 ----------
  function remove_pending_image(image_id) {
    pending_images.value = pending_images.value.filter((item) => item.id !== image_id)
  }

  // ---------- 统一发送：文字 + 待发图片一并写入消息 ----------
  function send_message() {
    const raw_text = input_text.value.trim()
    const images = [...pending_images.value]
    if (!raw_text && images.length === 0) {
      return
    }

    const role = sender_role.value === 'buyer' ? 'buyer' : 'merchant'

    if (images.length === 0) {
      append_message(role, raw_text)
    } else if (!raw_text) {
      images.forEach((img) => {
        append_message(role, '', img.data_url, img.name)
      })
    } else {
      append_message(role, raw_text, images[0].data_url, images[0].name)
      images.slice(1).forEach((img) => {
        append_message(role, '', img.data_url, img.name)
      })
    }

    input_text.value = ''
    pending_images.value = []
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
    abort_analyze_in_flight()
    register_pagehide_abort()
    analyze_abort_controller = new AbortController()
    const request_signal = analyze_abort_controller.signal

    loading.value = true
    error_message.value = ''
    report.value = null
    progress_message.value = '正在提交分析请求...'
    try {
      const payload = {
        messages: messages.value.map((item) => ({
          role: item.role,
          content: item.content
        })),
        reset_context: false,
        image_urls: messages.value
          .map((item) => String(item.image_url || '').trim())
          .filter((url) => Boolean(url))
      }
      const stage_messages = {
        merge: '正在合并上下文...',
        agent1: '正在提取事实...',
        agent2_tools: '正在检索规则、画像与判例...',
        agent2: '正在生成策略建议...',
        agent3: '正在生成推荐话术...'
      }
      const merge_partial_report = (partial) => {
        report.value = {
          ...(report.value || {}),
          ...(partial || {})
        }
      }
      const append_reasoning_delta = (delta_text) => {
        const normalized = String(delta_text || '')
        if (!normalized) {
          return
        }
        const current_report = report.value || {}
        const current_strategy = current_report.strategy || {}
        const current_reasoning = String(current_strategy.reasoning || '')
        report.value = {
          ...current_report,
          strategy: {
            ...current_strategy,
            reasoning: current_reasoning + normalized
          }
        }
      }

      if (ENABLE_ANALYZE_STREAM) {
        let stream_error = null
        await analyzeDisputeStream(payload, {
          on_event: (event_type, event_data) => {
            if (event_type === 'stage_start') {
              const stage_key = String(event_data?.stage || '')
              progress_message.value = stage_messages[stage_key] || '分析进行中...'
              return
            }
            if (event_type === 'stage_done' && event_data?.partial_report) {
              merge_partial_report(event_data.partial_report)
              return
            }
            if (event_type === 'stage_delta' && event_data?.stage === 'agent2' && event_data?.field === 'reasoning') {
              append_reasoning_delta(event_data.delta)
              return
            }
            if (event_type === 'final_report' && event_data?.report) {
              report.value = event_data.report
              progress_message.value = '分析完成'
              return
            }
            if (event_type === 'pipeline_error') {
              stream_error = new Error(event_data?.message || '流式分析失败')
            }
          },
          on_done: () => {
            if (!stream_error && report.value) {
              progress_message.value = '分析完成'
            }
          },
          on_error: (error) => {
            stream_error = error
          }
        }, { signal: request_signal })

        if (stream_error) {
          if (is_request_aborted(stream_error) || request_signal.aborted) {
            return
          }
          const message = String(stream_error.message || '')
          const is_stream_abort = /aborted|BodyStreamBuffer/i.test(message)
          const can_fallback =
            !request_signal.aborted &&
            (message.includes('流式分析未启用') ||
            message.includes('404') ||
            (is_stream_abort && !report.value?.strategy))
          if (can_fallback) {
            progress_message.value = is_stream_abort
              ? '流式连接中断，正在拉取完整报告...'
              : '流式不可用，已回退普通分析...'
            report.value = await analyzeDispute(payload, { signal: request_signal })
            progress_message.value = '分析完成'
          } else {
            throw stream_error
          }
        }
      } else {
        report.value = await analyzeDispute(payload, { signal: request_signal })
        progress_message.value = '分析完成'
      }
    } catch (error) {
      if (is_request_aborted(error) || request_signal.aborted) {
        return
      }
      error_message.value = error.message || '请求 AI 分析失败'
      progress_message.value = ''
    } finally {
      if (analyze_abort_controller?.signal === request_signal) {
        analyze_abort_controller = null
      }
      loading.value = false
    }
  }

  // ---------- 消息撤回：从会话中移除指定消息，并清空已过期分析结果 ----------
  function recall_message(message_id) {
    const target_id = Number(message_id)
    const before_len = messages.value.length
    messages.value = messages.value.filter((item) => item.id !== target_id)
    if (messages.value.length < before_len) {
      report.value = null
      error_message.value = ''
      progress_message.value = ''
    }
  }

  return {
    messages,
    report,
    loading,
    input_text,
    sender_role,
    pending_images,
    error_message,
    progress_message,
    has_report,
    append_message,
    send_message,
    add_pending_image,
    remove_pending_image,
    recall_message,
    apply_script,
    request_ai_help
  }
}
