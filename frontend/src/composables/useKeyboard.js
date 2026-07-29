/**
 * 全局快捷键系统
 *
 * 支持:
 *   Ctrl/Cmd + Enter → 采纳发送
 *   Alt + Enter       → 修改后发送
 *   Esc               → 拒绝
 *   ↑ / ↓             → 上下切换队列
 */

import { onMounted, onUnmounted } from 'vue'

export function useKeyboard(handlers = {}) {
  const {
    onAccept,    // () => void
    onModify,    // () => void
    onReject,    // () => void
    onUp,        // () => void
    onDown,      // () => void
  } = handlers

  function handler(e) {
    // 在输入框内时，不拦截方向键（允许正常移动光标）
    const tag = e.target?.tagName?.toLowerCase()
    const isInput = tag === 'input' || tag === 'textarea' || tag === 'select'

    // Ctrl/Cmd + Enter → 采纳
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      onAccept?.()
      return
    }

    // Alt + Enter → 修改后发送
    if (e.altKey && e.key === 'Enter') {
      e.preventDefault()
      onModify?.()
      return
    }

    // Esc → 拒绝
    if (e.key === 'Escape' && !isInput) {
      e.preventDefault()
      onReject?.()
      return
    }

    // ↑ / ↓ → 切换队列 (非输入框内)
    if (!isInput) {
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        onUp?.()
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        onDown?.()
      }
    }
  }

  onMounted(() => window.addEventListener('keydown', handler))
  onUnmounted(() => window.removeEventListener('keydown', handler))
}
