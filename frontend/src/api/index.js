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

// ---------- 分析请求：调用辅助模式分析接口 ----------
export async function analyzeDispute(payload) {
  try {
    const response = await httpClient.post('/analyze', payload)
    return response.data
  } catch (error) {
    throw new Error(`请求分析失败：${error.message}`)
  }
}

// ---------- 配置查询：获取商家模式和自动化阈值 ----------
export async function fetchMerchantConfig(merchantId) {
  try {
    const response = await httpClient.get(`/merchants/${merchantId}/config`)
    return response.data
  } catch (error) {
    throw new Error(`读取商家配置失败：${error.message}`)
  }
}

// ---------- 配置更新：设置商家模式和自动化阈值 ----------
export async function updateMerchantConfig(merchantId, payload) {
  try {
    const response = await httpClient.put(`/merchants/${merchantId}/config`, payload)
    return response.data
  } catch (error) {
    throw new Error(`更新商家配置失败：${error.message}`)
  }
}
