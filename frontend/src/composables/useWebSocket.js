/**
 * 无界AI — WebSocket 实时推送 Composable
 *
 * 连接 ws://localhost:8001/ws (通过 Vite proxy)
 * 接收新消息推送，触发前端列表刷新。
 * 内置断线重连 + 心跳保活。
 */

import { ref, onMounted, onUnmounted } from 'vue'
import { getToken } from '../api/chat.js'

/**
 * @param {Object} options
 * @param {Function} options.onNewMessage - 收到新消息回调
 * @returns {{ isConnected: Ref<boolean>, sendPing: Function }}
 */
export function useWebSocket(options = {}) {
  const { onNewMessage } = options

  const isConnected = ref(false)

  // 修复: ws/wss 随页面协议自适应（HTTPS 下用 wss，否则被浏览器拦截混合内容）
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const token = getToken()
  // 鉴权: 连接时携带 API Token（后端 /ws 校验 ?token=）
  const wsUrl = `${protocol}://${window.location.host}/ws?token=${encodeURIComponent(token)}`

  let ws = null
  let reconnectTimer = null
  let heartbeatTimer = null
  let reconnectAttempts = 0
  const maxReconnectAttempts = 10

  // ── 连接 WebSocket ──────────────────────────
  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return
    }

    // 修复: 未登录(无 token)时不发起连接，避免空 token 反复 403
    if (!getToken()) {
      console.warn('[WS] 未登录，跳过 WebSocket 连接')
      return
    }

    try {
      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        isConnected.value = true
        reconnectAttempts = 0
        console.log('[WS] Connected')
        startHeartbeat()
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'new_message' && onNewMessage) {
            onNewMessage(msg.data)
          } else if (msg.type === 'pong') {
            // 心跳响应，无需处理
          }
        } catch (e) {
          console.warn('[WS] Failed to parse message:', e)
        }
      }

      ws.onclose = () => {
        isConnected.value = false
        stopHeartbeat()
        console.log('[WS] Disconnected')
        scheduleReconnect()
      }

      ws.onerror = (e) => {
        console.error('[WS] Error:', e)
        ws?.close()
      }
    } catch (e) {
      console.error('[WS] Connection failed:', e)
      scheduleReconnect()
    }
  }

  // ── 断线重连 ────────────────────────────────
  function scheduleReconnect() {
    if (reconnectTimer) return
    if (reconnectAttempts >= maxReconnectAttempts) {
      console.warn('[WS] Max reconnect attempts reached')
      return
    }

    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000)
    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${reconnectAttempts + 1}/${maxReconnectAttempts})`)

    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      reconnectAttempts++
      connect()
    }, delay)
  }

  // ── 心跳 ────────────────────────────────────
  function startHeartbeat() {
    stopHeartbeat()
    heartbeatTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 25000) // 每 25 秒发一次 ping
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  // ── 公开方法 ────────────────────────────────
  function sendPing() {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping' }))
    }
  }

  // ── 生命周期 ────────────────────────────────
  onMounted(() => {
    connect()
  })

  onUnmounted(() => {
    stopHeartbeat()
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      ws.onclose = null // 禁止重连
      ws.close()
      ws = null
    }
  })

  return {
    isConnected,
    sendPing,
  }
}
