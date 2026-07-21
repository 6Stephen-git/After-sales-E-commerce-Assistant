import { computed, ref } from 'vue'
import { createAnalysisJob, monitorSellerEmotion, subscribeAnalysisJob } from '../api'
import { should_block_seller_text_locally } from '../utils/sellerEmotionLocal'

const ANALYSIS_JOB_STORAGE_KEY = 'ecommerce_assistant_active_analysis_job'
const DEMO_MERCHANT_ID = String(import.meta.env.VITE_DEMO_MERCHANT_ID || 'demo-merchant')

// ---------- 模块级状态：跨路由切换保留对话和分析结果 ----------
const messages = ref([])
const report = ref(null)
const loading = ref(false)
const input_text = ref('')
const sender_role = ref('merchant')
const pending_images = ref([])
const error_message = ref('')
const progress_message = ref('')
const seller_emotion_alert = ref(null)
const show_seller_emotion_dialog = ref(false)
const emotion_dialog_mode = ref('block')
const emotion_watch_active = ref(false)
const emotion_mild_notice_acknowledged = ref(false)
const emotion_checking = ref(false)
const pending_send = ref(null)
const message_id_seed = ref(messages.value.length + 1)
const pending_image_id_seed = ref(1)
let analyze_abort_controller = null
let pagehide_abort_registered = false
let resume_attempted = false

// ---------- 会话标识：页面刷新后保留 job_id，浏览器可重新订阅尚未结束的任务 ----------
function get_or_create_session_value(key, prefix) {
  /**
   * 从 sessionStorage 读取或生成当前页面会话标识。
   *
   * 后端正式接入后 merchant_id 应来自登录态；当前演示项目使用固定商家和会话级纠纷号，
   * 避免多个浏览器页无意共享默认纠纷。
   */
  if (typeof window === 'undefined') {
    return `${prefix}-server`
  }
  const existing = window.sessionStorage.getItem(key)
  if (existing) {
    return existing
  }
  const suffix = typeof window.crypto?.randomUUID === 'function'
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  const value = `${prefix}-${suffix}`
  window.sessionStorage.setItem(key, value)
  return value
}

function get_active_analysis_job_id() {
  /** 读取刷新后可恢复订阅的任务 ID。 */
  return typeof window === 'undefined'
    ? ''
    : String(window.sessionStorage.getItem(ANALYSIS_JOB_STORAGE_KEY) || '')
}

function save_active_analysis_job_id(jobId) {
  /** 在连接 SSE 前保存 job_id，避免连接中断导致前端丢失恢复锚点。 */
  if (typeof window !== 'undefined') {
    window.sessionStorage.setItem(ANALYSIS_JOB_STORAGE_KEY, jobId)
  }
}

function clear_active_analysis_job_id(jobId) {
  /** 仅清理当前已完成任务，避免旧订阅误删新任务 ID。 */
  if (typeof window !== 'undefined' && get_active_analysis_job_id() === jobId) {
    window.sessionStorage.removeItem(ANALYSIS_JOB_STORAGE_KEY)
  }
}

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

  // ---------- 情绪升级态：语气恢复平静后重置，允许下一轮硬语气提示 ----------
  function reset_emotion_escalation() {
    emotion_watch_active.value = false
    emotion_mild_notice_acknowledged.value = false
  }

  // ---------- 卖家情绪：发送后异步检测，硬语气提示仅弹一次 ----------
  async function check_seller_emotion_after_send(merchant_text) {
    const normalized_text = String(merchant_text || '').trim()
    if (!normalized_text) {
      return
    }
    try {
      const chat_history = messages.value.map((item) => ({
        role: item.role,
        content: item.content
      }))
      const result = await monitorSellerEmotion({
        text: normalized_text,
        chat_history
      })
      seller_emotion_alert.value = result
      if (result?.early_warn_triggered || result?.alert_triggered) {
        emotion_watch_active.value = true
        if (!emotion_mild_notice_acknowledged.value) {
          emotion_dialog_mode.value = 'notice'
          show_seller_emotion_dialog.value = true
          emotion_mild_notice_acknowledged.value = true
        }
        return
      }
      reset_emotion_escalation()
    } catch (error) {
      console.warn('[useDispute] 卖家情绪监控失败', error)
    }
  }

  // ---------- 卖家情绪：预警态下一条发送前复检待发内容 ----------
  async function check_seller_emotion_before_send(merchant_text) {
    const normalized_text = String(merchant_text || '').trim()
    if (!normalized_text) {
      return null
    }
    const chat_history = messages.value.map((item) => ({
      role: item.role,
      content: item.content
    }))
    const result = await monitorSellerEmotion({
      text: normalized_text,
      chat_history
    })
    seller_emotion_alert.value = result
    return result
  }

  // ---------- 打开情绪弹窗：本地秒拦或轻度预警确认 ----------
  function open_emotion_dialog(payload, mode) {
    pending_send.value = payload
    emotion_dialog_mode.value = mode
    show_seller_emotion_dialog.value = true
  }

  // ---------- 写入会话：将待发文字与图片落盘到消息列表 ----------
  function commit_send({ raw_text, images, role }) {
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
    pending_send.value = null
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

  // ---------- 统一发送：本地秒拦；预警态下条发送前复检；其余即时发出 ----------
  async function send_message() {
    const raw_text = input_text.value.trim()
    const images = [...pending_images.value]
    if (!raw_text && images.length === 0) {
      return
    }

    const role = sender_role.value === 'buyer' ? 'buyer' : 'merchant'
    const payload = { raw_text, images, role }
    let emotion_prechecked = false

    if (role === 'merchant' && raw_text) {
      if (should_block_seller_text_locally(raw_text)) {
        open_emotion_dialog(payload, 'block')
        return
      }
      if (emotion_watch_active.value) {
        emotion_checking.value = true
        try {
          const result = await check_seller_emotion_before_send(raw_text)
          if (result?.early_warn_triggered || result?.alert_triggered) {
            open_emotion_dialog(payload, 'intercept')
            return
          }
          // 下一条情绪正常：解除预警态，静默放行，不再弹窗拦截
          reset_emotion_escalation()
          seller_emotion_alert.value = result
          emotion_prechecked = true
        } catch (error) {
          console.warn('[useDispute] 卖家情绪发送前复检失败，跳过拦截', error)
        } finally {
          emotion_checking.value = false
        }
      }
    }

    commit_send(payload)
    if (role === 'merchant' && raw_text && !emotion_prechecked) {
      void check_seller_emotion_after_send(raw_text)
    }
  }

  // ---------- 情绪拦截：用户选择修改内容，保留输入框待发 ----------
  function cancel_emotion_block() {
    pending_send.value = null
    show_seller_emotion_dialog.value = false
  }

  // ---------- 情绪拦截：用户确认仍要发送 ----------
  function confirm_send_despite_emotion() {
    const payload = pending_send.value
    if (!payload) {
      show_seller_emotion_dialog.value = false
      return
    }
    pending_send.value = null
    show_seller_emotion_dialog.value = false
    emotion_mild_notice_acknowledged.value = true
    commit_send(payload)
    if (payload.role === 'merchant' && payload.raw_text) {
      void check_seller_emotion_after_send(payload.raw_text)
    }
  }

  // ---------- 情绪提醒：硬语气当场提示已知晓，后续仅走发送前拦截 ----------
  function dismiss_emotion_notice() {
    emotion_mild_notice_acknowledged.value = true
    show_seller_emotion_dialog.value = false
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

  // ---------- 阶段展示：将可补发 SSE 事件合并到策略面板 ----------
  function apply_analysis_event(event_type, event_data) {
    /**
     * 消费单个分析事件。
     *
     * 同一事件可能在网络边界重传，局部对象合并和最终报告覆盖均保持幂等。
     */
    const stage_messages = {
      merge: '正在合并上下文...',
      agent1: '正在提取事实与参考信息...',
      agent2_tools: '正在分析风险信号与匹配规则...',
      agent2: '正在生成策略建议...',
      agent3: '正在生成推荐话术...'
    }
    if (event_type === 'stage_start') {
      const stage_key = String(event_data?.stage || '')
      progress_message.value = stage_messages[stage_key] || '分析进行中...'
      return
    }
    if (event_type === 'stage_done' && event_data?.partial_report) {
      const current = report.value || {}
      const partial = event_data.partial_report
      report.value = {
        ...current,
        ...partial,
        strategy: partial.strategy ? { ...(current.strategy || {}), ...partial.strategy } : current.strategy
      }
      return
    }
    if (event_type === 'stage_delta' && event_data?.stage === 'agent2' && event_data?.field === 'reasoning') {
      const current = report.value || {}
      const strategy = current.strategy || {}
      report.value = {
        ...current,
        strategy: {
          ...strategy,
          reasoning: `${String(strategy.reasoning || '')}${String(event_data.delta || '')}`
        }
      }
      return
    }
    if (event_type === 'final_report' && event_data?.report) {
      report.value = event_data.report
      progress_message.value = '分析完成'
    }
  }

  // ---------- 任务订阅：EventSource 自动重连，后端按 Last-Event-ID 补发事件 ----------
  async function follow_analysis_job(job_id, request_signal) {
    /**
     * 持续订阅已有任务直至成功、失败或取消。
     *
     * 订阅中断只关闭浏览器连接；任务和最终报告仍由 MySQL/Celery 维护。
     */
    await subscribeAnalysisJob(job_id, {
      on_event: apply_analysis_event
    }, { signal: request_signal })
    progress_message.value = '分析完成'
    clear_active_analysis_job_id(job_id)
  }

  // ---------- 刷新恢复：从 sessionStorage 找回未完成任务并重新订阅 ----------
  async function resume_pending_analysis_job() {
    /** 页面刷新后恢复现有 job_id 的 SSE 订阅，不重新提交分析。 */
    const job_id = get_active_analysis_job_id()
    if (!job_id || loading.value || analyze_abort_controller) {
      return
    }
    analyze_abort_controller = new AbortController()
    const request_signal = analyze_abort_controller.signal
    loading.value = true
    error_message.value = ''
    progress_message.value = '正在恢复分析任务...'
    try {
      await follow_analysis_job(job_id, request_signal)
    } catch (error) {
      if (!is_request_aborted(error) && !request_signal.aborted) {
        error_message.value = error.message || '恢复分析任务失败'
        progress_message.value = ''
      }
    } finally {
      if (analyze_abort_controller?.signal === request_signal) {
        analyze_abort_controller = null
      }
      loading.value = false
    }
  }

  // ---------- 分析请求：先创建任务，再订阅可重连的 SSE 事件 ----------
  async function request_ai_help() {
    abort_analyze_in_flight()
    register_pagehide_abort()
    analyze_abort_controller = new AbortController()
    const request_signal = analyze_abort_controller.signal
    const dispute_id = get_or_create_session_value('ecommerce_assistant_dispute_id', 'dispute')

    loading.value = true
    error_message.value = ''
    report.value = null
    progress_message.value = '正在创建分析任务...'
    try {
      const payload = {
        merchant_id: DEMO_MERCHANT_ID,
        dispute_id,
        messages: messages.value.map((item) => ({
          role: item.role,
          content: item.content
        })),
        reset_context: false,
        image_urls: messages.value
          .map((item) => String(item.image_url || '').trim())
          .filter((url) => Boolean(url))
      }
      const job = await createAnalysisJob(payload)
      save_active_analysis_job_id(job.job_id)
      progress_message.value = job.reused ? '正在恢复已有分析任务...' : '分析任务已提交，正在等待处理...'
      await follow_analysis_job(job.job_id, request_signal)
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

  // ---------- 组件首次挂载：若页面刷新前有未结束任务，自动恢复订阅 ----------
  if (!resume_attempted) {
    resume_attempted = true
    register_pagehide_abort()
    void resume_pending_analysis_job()
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
    seller_emotion_alert,
    show_seller_emotion_dialog,
    emotion_dialog_mode,
    emotion_checking,
    has_report,
    append_message,
    send_message,
    add_pending_image,
    remove_pending_image,
    recall_message,
    apply_script,
    request_ai_help,
    cancel_emotion_block,
    confirm_send_despite_emotion,
    dismiss_emotion_notice
  }
}
