import axios from 'axios'

// ---------- 分析链路耗时常超过普通接口：后端多 Agent + 外部 LLM，需单独拉长等待时间 ----------
const ANALYZE_HTTP_TIMEOUT_MS = 180000
const ANALYZE_STREAM_TIMEOUT_MS = 180000

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

// ---------- 分析请求：调用辅助模式分析接口 ----------
export async function analyzeDispute(payload) {
  try {
    const response = await httpClient.post('/analyze', payload, {
      timeout: ANALYZE_HTTP_TIMEOUT_MS
    })
    return response.data
  } catch (error) {
    throw new Error(`请求分析失败：${error.message}`)
  }
}

// ---------- 流式分析请求：按阶段消费 SSE 事件，支持先展示部分结果 ----------
export async function analyzeDisputeStream(payload, handlers = {}) {
  const { on_event, on_done, on_error } = handlers
  const controller = new AbortController()
  const timeout_id = setTimeout(() => controller.abort(), ANALYZE_STREAM_TIMEOUT_MS)
  try {
    const response = await fetch('/api/analyze/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal
    })
    if (!response.ok) {
      let detail = `HTTP ${response.status}`
      try {
        const body = await response.json()
        detail = body?.detail || detail
      } catch {
        // 忽略非 JSON 错误体
      }
      throw new Error(detail)
    }

    if (!response.body) {
      throw new Error('流式响应不可用')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) {
        break
      }
      buffer += decoder.decode(value, { stream: true })

      while (buffer.includes('\n\n')) {
        const frame_end = buffer.indexOf('\n\n')
        const frame = buffer.slice(0, frame_end)
        buffer = buffer.slice(frame_end + 2)

        let event_type = 'message'
        let event_data = {}
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) {
            event_type = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            const raw = line.slice(5).trim()
            try {
              event_data = JSON.parse(raw)
            } catch {
              event_data = { raw }
            }
          }
        }

        if (typeof on_event === 'function') {
          on_event(event_type, event_data)
        }
      }
    }
    if (typeof on_done === 'function') {
      on_done()
    }
  } catch (error) {
    if (typeof on_error === 'function') {
      on_error(error)
    } else {
      throw new Error(`请求分析失败：${error.message || '未知错误'}`)
    }
  } finally {
    clearTimeout(timeout_id)
  }
}

// ---------- 智能模式：发送买家消息，返回 Agent 回复 ----------
export async function sendIntelligentMessage(payload) {
  try {
    const response = await httpClient.post('/intelligent/message', payload, {
      timeout: ANALYZE_HTTP_TIMEOUT_MS
    })
    return response.data
  } catch (error) {
    throw new Error(`智能模式请求失败：${error.message}`)
  }
}

// ---------- 智能模式：商家接管 ----------
export async function intelligentTakeover(dispute_id) {
  try {
    const response = await httpClient.post('/intelligent/takeover', { dispute_id })
    return response.data
  } catch (error) {
    throw new Error(`接管请求失败：${error.message}`)
  }
}

// ---------- 智能模式：查询案件状态 ----------
export async function fetchIntelligentStatus(dispute_id) {
  try {
    const response = httpClient.get(`/intelligent/status/${dispute_id}`)
    return (await response).data
  } catch (error) {
    throw new Error(`查询状态失败：${error.message}`)
  }
}

// ---------- 智能模式：读取测试模拟配置 ----------
export async function fetchIntelligentSimulation() {
  try {
    const response = await httpClient.get('/intelligent/simulation')
    return response.data
  } catch (error) {
    throw new Error(`读取模拟配置失败：${error.message}`)
  }
}

// ---------- 智能模式：保存测试模拟配置 ----------
export async function saveIntelligentSimulation(payload) {
  try {
    const response = await httpClient.put('/intelligent/simulation', payload)
    return response.data
  } catch (error) {
    throw new Error(`保存模拟配置失败：${error.message}`)
  }
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
