<script setup>
import { computed } from 'vue'

const props = defineProps({
  queue: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectedIndex: { type: Number, default: -1 },
})
const emit = defineEmits(['select', 'refresh'])

// ── 相对时间 ────────────────────────────────
function relativeTime(ts) {
  if (!ts) return ''
  const diff = (Date.now() - new Date(ts).getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return Math.floor(diff / 60) + '分钟前'
  if (diff < 86400) return Math.floor(diff / 3600) + '小时前'
  return Math.floor(diff / 86400) + '天前'
}

// ── 头像渐变色 ──────────────────────────────
const avatarColors = [
  'linear-gradient(135deg, #667eea, #764ba2)',
  'linear-gradient(135deg, #f093fb, #f5576c)',
  'linear-gradient(135deg, #4facfe, #00f2fe)',
  'linear-gradient(135deg, #43e97b, #38f9d7)',
  'linear-gradient(135deg, #fa709a, #fee140)',
  'linear-gradient(135deg, #a18cd1, #fbc2eb)',
]

function avatarBg(id) {
  if (!id) return avatarColors[0]
  let hash = 0
  for (let i = 0; i < id.length; i++) hash = id.charCodeAt(i) + ((hash << 5) - hash)
  return avatarColors[Math.abs(hash) % avatarColors.length]
}

function avatarLetter(id) {
  return (id || '?')[0].toUpperCase()
}

const accountColors = {
  sales_01: '#5b5cff',
  sales_02: '#22c55e',
  sales_03: '#f59e0b',
}
function accountColor(aid) {
  return accountColors[aid] || '#909399'
}

// ── RAG 得分样式 ────────────────────────────
function scoreBadge(score) {
  if (!score && score !== 0) return { label: 'N/A', cls: '' }
  if (score >= 0.8) return { label: score.toFixed(2), cls: 'score-high' }
  if (score >= 0.65) return { label: score.toFixed(2), cls: 'score-mid' }
  return { label: score.toFixed(2) + ' ⚠', cls: 'score-low' }
}

function isSelected(idx) { return idx === props.selectedIndex }
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 头部统计 -->
    <div class="px-4 py-3 border-b flex items-center justify-between"
      style="border-color: var(--border);">
      <div class="flex items-center gap-2">
        <span class="text-xs font-semibold uppercase tracking-wider" style="color: var(--text-tertiary);">
          审核队列
        </span>
        <span class="text-xs px-2 py-0.5 rounded-full font-mono font-medium"
          style="background: var(--accent-light); color: var(--accent);">
          {{ queue.length }}
        </span>
      </div>
      <button
        @click="emit('refresh')"
        class="text-xs px-2 py-1 rounded-md transition-colors hover:opacity-80"
        style="color: var(--text-tertiary);"
        :disabled="loading"
      >
        {{ loading ? '刷新中...' : '刷新' }}
      </button>
    </div>

    <!-- 消息列表 -->
    <div class="flex-1 overflow-y-auto">
      <!-- 空状态 -->
      <div v-if="!loading && queue.length === 0" class="flex flex-col items-center justify-center h-full gap-4 px-8 text-center">
        <div class="w-20 h-20 rounded-full flex items-center justify-center"
          style="background: var(--bg-hover);">
          <el-icon :size="36" color="var(--text-tertiary)"><Select /></el-icon>
        </div>
        <div>
          <p class="text-sm font-medium" style="color: var(--text-secondary);">
            🎉 所有消息已审核完毕
          </p>
          <p class="text-xs mt-1" style="color: var(--text-tertiary);">
            系统运行正常，等待新消息中...
          </p>
        </div>
      </div>

      <!-- 队列卡片 -->
      <template v-else>
        <div
          v-for="(trace, idx) in queue"
          :key="trace.trace_id"
          :class="['queue-card', { active: isSelected(idx) }]"
          class="px-4 py-3.5 border-b cursor-pointer transition-all duration-200"
          style="border-color: var(--border);"
          @click="emit('select', trace, idx)"
        >
          <!-- 选中左边框 -->
          <div v-if="isSelected(idx)" class="absolute left-0 top-0 bottom-0 w-[3px]"
            style="background: var(--accent); border-radius: 0 3px 3px 0;"></div>

          <div class="relative">
            <!-- 头部：头像 + 账号 + 时间 -->
            <div class="flex items-center gap-2.5 mb-2">
              <div class="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center text-white text-xs font-bold shadow-sm"
                :style="{ background: avatarBg(trace.customer_id) }">
                {{ avatarLetter(trace.customer_id) }}
              </div>
              <div class="flex-1 min-w-0">
                <div class="text-[13px] font-medium truncate" style="color: var(--text-primary);">
                  {{ trace.customer_id }}
                </div>
                <div class="text-[11px] truncate flex items-center gap-1.5" style="color: var(--text-tertiary);">
                  <span class="w-1.5 h-1.5 rounded-full flex-shrink-0"
                    :style="{ background: accountColor(trace.account_id) }"></span>
                  {{ trace.account_id }}
                </div>
              </div>
              <span class="text-[10px] flex-shrink-0" style="color: var(--text-tertiary);">
                {{ relativeTime(trace.timestamp) }}
              </span>
            </div>

            <!-- 消息内容 -->
            <p class="text-[13px] leading-relaxed line-clamp-2 mb-2" style="color: var(--text-secondary);">
              {{ trace.user_input }}
            </p>

            <!-- 底部 Badge -->
            <div class="flex items-center gap-2">
              <span
                v-if="trace.max_score != null"
                class="text-[10px] px-1.5 py-0.5 rounded-full font-mono font-medium"
                :class="scoreBadge(trace.max_score).cls"
              >
                {{ scoreBadge(trace.max_score).label }}
              </span>
              <span
                v-if="trace.is_fallback"
                class="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                style="background: var(--red-bg); color: var(--red);">
                ⚠️ 低置信度
              </span>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.queue-card {
  position: relative;
  border-left: 3px solid transparent;
}
.queue-card:hover {
  background: var(--bg-hover);
}
.queue-card.active {
  background: var(--bg-selected);
  border-left-color: var(--accent);
}

/* RAG Score Badges */
.score-high {
  background: var(--green-bg);
  color: var(--green);
}
.score-mid {
  background: var(--amber-bg);
  color: var(--amber);
}
.score-low {
  background: var(--red-bg);
  color: var(--red);
}
</style>
