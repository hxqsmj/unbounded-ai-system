/**
 * Web Audio API 音效生成器 — 零外部依赖
 */

let audioCtx = null

function getCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)()
  }
  return audioCtx
}

/** 新消息提醒 — 清脆叮咚 (双音叠加: 800Hz + 1200Hz, 衰减) */
export function playDing() {
  try {
    const ctx = getCtx()
    const now = ctx.currentTime

    const freqs = [800, 1200]
    freqs.forEach((freq, i) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.15, now + i * 0.06)
      gain.gain.exponentialRampToValueAtTime(0.01, now + i * 0.06 + 0.25)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(now + i * 0.06)
      osc.stop(now + i * 0.06 + 0.25)
    })
  } catch {
    // 静默降级
  }
}

/** 操作成功 — 上升三音 */
export function playSuccess() {
  try {
    const ctx = getCtx()
    const now = ctx.currentTime
    ;[523, 659, 784].forEach((freq, i) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.1, now + i * 0.08)
      gain.gain.exponentialRampToValueAtTime(0.01, now + i * 0.08 + 0.2)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(now + i * 0.08)
      osc.stop(now + i * 0.08 + 0.2)
    })
  } catch { /* silent */ }
}

/** 拒绝操作 — 低沉短音 */
export function playReject() {
  try {
    const ctx = getCtx()
    const now = ctx.currentTime
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'triangle'
    osc.frequency.value = 250
    gain.gain.setValueAtTime(0.1, now)
    gain.gain.exponentialRampToValueAtTime(0.01, now + 0.3)
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start(now)
    osc.stop(now + 0.3)
  } catch { /* silent */ }
}
