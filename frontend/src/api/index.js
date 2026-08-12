import axios from 'axios'

// ---------- Axios 实例：统一管理前端到后端的 HTTP 请求 ----------
const httpClient = axios.create({
  baseURL: '/api',
  timeout: 10000
})

// ---------- 响应拦截：统一转中文错误，便于页面提示 ----------
httpClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const detailMessage = error?.response?.data?.detail
    const fallbackMessage = error?.message || '请求失败，请稍后重试'
    return Promise.reject(new Error(detailMessage || fallbackMessage))
  }
)

// ---------- 分析请求：调用 /analyze 并刷新策略面板 ----------
export async function monitorSellerEmotion(payload) {
  try {
    const response = await httpClient.post('/emotion/monitor', payload, { timeout: 60000 })
    return response.data
  } catch (error) {
    throw new Error(`卖家情绪监控失败：${error.message}`)
  }
}

// ---------- 结束处理：可选触发 Agent5 复盘 ----------
export async function submitDisputeReview(payload) {
  try {
    const response = await httpClient.post('/review', payload, { timeout: 30000 })
    return response.data
  } catch (error) {
    throw new Error(`结束处理失败：${error.message}`)
  }
}

// ---------- 创建分析任务：请求快速返回 job_id，耗时 Agent 链路由后端 worker 执行 ----------
export async function createAnalysisJob(payload) {
  try {
    const response = await httpClient.post('/analyze', payload, {
      timeout: 30000
    })
    return response.data
  } catch (error) {
    throw new Error(`创建分析任务失败：${error.message}`)
  }
}

// ---------- 状态查询：恢复订阅前确认任务是否仍在执行 ----------
export async function getAnalysisJob(jobId) {
  try {
    const response = await httpClient.get(`/analyze/${encodeURIComponent(jobId)}`)
    return response.data
  } catch (error) {
    throw new Error(`查询分析任务失败：${error.message}`)
  }
}

// ---------- SSE 订阅：浏览器凭事件 id 自动重连，并携带 Last-Event-ID 补发遗漏事件 ----------
export function subscribeAnalysisJob(jobId, handlers = {}, options = {}) {
  /**
   * 订阅任务的阶段事件直至完成。
   *
   * EventSource 在网络中断后会自动重连，服务端按 Last-Event-ID 回放缺失事件。
   * 调用方传入 AbortSignal 时只关闭订阅，不取消后端任务。
   */
  const { on_event } = handlers
  const signal = options.signal
  const eventTypes = [
    'job_started',
    'pipeline_start',
    'stage_start',
    'stage_done',
    'stage_delta',
    'final_report',
    'pipeline_done',
    'pipeline_error',
    'job_completed',
    'job_failed',
    'cancel_requested',
    'cancelled'
  ]

  return new Promise((resolve, reject) => {
    const source = new EventSource(`/api/analyze/${encodeURIComponent(jobId)}/events`)
    let settled = false

    const finish = (callback, value) => {
      if (settled) {
        return
      }
      settled = true
      source.close()
      signal?.removeEventListener('abort', onAbort)
      callback(value)
    }
    const onAbort = () => finish(reject, new DOMException('分析订阅已取消', 'AbortError'))
    const notify = (event) => {
      let payload = {}
      try {
        payload = JSON.parse(event.data || '{}')
      } catch {
        payload = { raw: event.data }
      }
      if (typeof on_event === 'function') {
        on_event(event.type, payload)
      }
      if (event.type === 'pipeline_error' || event.type === 'job_failed') {
        finish(reject, new Error(payload.message || '分析任务执行失败'))
      } else if (event.type === 'cancelled') {
        finish(reject, new DOMException('分析任务已取消', 'AbortError'))
      } else if (event.type === 'job_completed') {
        finish(resolve)
      }
    }

    eventTypes.forEach((eventType) => source.addEventListener(eventType, notify))
    source.onerror = () => {
      // EventSource 自动重连；不能在这里 reject，否则会把瞬时网络波动误判为任务失败。
    }
    if (signal) {
      if (signal.aborted) {
        onAbort()
      } else {
        signal.addEventListener('abort', onAbort, { once: true })
      }
    }
  })
}

// ---------- 配置查询：获取默认商家模式和自动化阈值 ----------
export async function fetchMerchantConfig() {
  try {
    const response = await httpClient.get('/merchants/config')
    return response.data
  } catch (error) {
    throw new Error(`读取商家配置失败：${error.message}`)
  }
}

// ---------- 配置更新：设置默认商家模式和自动化阈值 ----------
export async function updateMerchantConfig(payload) {
  try {
    const response = await httpClient.put('/merchants/config', payload)
    return response.data
  } catch (error) {
    throw new Error(`更新商家配置失败：${error.message}`)
  }
}
