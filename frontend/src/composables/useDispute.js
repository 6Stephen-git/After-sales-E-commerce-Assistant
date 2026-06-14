import { computed, ref } from 'vue'
import { analyzeDispute, analyzeDisputeStream } from '../api'

const ENABLE_ANALYZE_STREAM = String(import.meta.env.VITE_ENABLE_ANALYZE_STREAM || '0') === '1'

// ---------- 模块级状态：跨路由切换保留对话和分析结果 ----------
const messages = ref([])
const report = ref(null)
const loading = ref(false)
const input_text = ref('')
const sender_role = ref('merchant')
const error_message = ref('')
const progress_message = ref('')
const message_id_seed = ref(messages.value.length + 1)

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
        })

        if (stream_error) {
          const message = String(stream_error.message || '')
          const is_stream_abort = /aborted|BodyStreamBuffer/i.test(message)
          const can_fallback =
            message.includes('流式分析未启用') ||
            message.includes('404') ||
            (is_stream_abort && !report.value?.strategy)
          if (can_fallback) {
            progress_message.value = is_stream_abort
              ? '流式连接中断，正在拉取完整报告...'
              : '流式不可用，已回退普通分析...'
            report.value = await analyzeDispute(payload)
            progress_message.value = '分析完成'
          } else {
            throw stream_error
          }
        }
      } else {
        report.value = await analyzeDispute(payload)
        progress_message.value = '分析完成'
      }
    } catch (error) {
      error_message.value = error.message || '请求 AI 分析失败'
      progress_message.value = ''
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
    progress_message,
    has_report,
    append_message,
    send_message,
    send_image,
    apply_script,
    request_ai_help
  }
}
