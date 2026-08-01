/**
 * 无界AI — 人机协作 API 层
 *
 * 后端地址通过 Vite proxy 代理，开发时所有 /api 请求自动转发到 localhost:8001。
 */

import axios from 'axios'

export const TOKEN_KEY = 'wujie_api_token'
export const OPERATOR_KEY = 'wujie_operator_name'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setAuth(token, operatorName) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(OPERATOR_KEY, operatorName)
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(OPERATOR_KEY)
}

const http = axios.create({
  baseURL: '/api/v1/chat',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截: 自动携带 API Token
http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers['X-API-Token'] = token
  return config
})

// 响应拦截: 401 → 清除本地凭证并刷新页面（回到登录界面）
// 修复: 防无限刷新死循环 — token 无效时 reload 后仍 401，若每次立即 reload
// 会形成死循环打满服务器限流(429)。3 秒内只允许 reload 一次，页面停在登录界面。
let lastReloadTime = 0
http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response?.status === 401) {
      clearAuth()
      const now = Date.now()
      if (now - lastReloadTime > 3000) {
        lastReloadTime = now
        window.location.reload()
      }
    }
    return Promise.reject(error)
  }
)

// ═══════════════════════════════════════════════
// 待审核队列
// ═══════════════════════════════════════════════

/**
 * 获取待审核消息列表
 * @param {Object} params - { limit?: number, offset?: number }
 * @returns {{ total: number, items: Array }}
 */
export async function fetchPendingQueue(params = {}) {
  const { data } = await http.get('/pending', { params })
  return data
}

// ═══════════════════════════════════════════════
// 审核详情
// ═══════════════════════════════════════════════

/**
 * 获取单条 trace 的完整审核详情 (含 RAG 上下文)
 * @param {string} traceId
 * @returns {Object} trace detail
 */
export async function fetchTraceDetail(traceId) {
  const { data } = await http.get(`/trace/${traceId}`)
  return data
}

// ═══════════════════════════════════════════════
// AI 生成 (用于手动触发)
// ═══════════════════════════════════════════════

/**
 * 调用 AI 生成回复
 * @param {Object} params - { account_id, customer_id, user_message, history? }
 * @returns {Object} { trace_id, generated_text, is_fallback, max_score, status }
 */
export async function generateReply(params) {
  const { data } = await http.post('/generate', params)
  return data
}

// ═══════════════════════════════════════════════
// 人工确认/修改/拒绝
// ═══════════════════════════════════════════════

/**
 * 提交人工审核结果
 * @param {Object} params - { trace_id, final_text, is_modified, action }
 * @returns {Object} { trace_id, status, message }
 */
export async function confirmSend(params) {
  const { data } = await http.post('/confirm_send', params)
  return data
}

// ═══════════════════════════════════════════════
// 操作日志
// ═══════════════════════════════════════════════

export async function recordOperation(params) {
  const { data } = await http.post('/operation_log', params)
  return data
}

export async function fetchOperationLogs(limit = 50) {
  const { data } = await http.get('/operation_log', { params: { limit } })
  return data
}

// ═══════════════════════════════════════════════
// 数据看板
// ═══════════════════════════════════════════════

export async function fetchDashboard() {
  const { data } = await http.get('/dashboard')
  return data
}

export default http
