<script setup>
import { ref, computed, provide, onMounted, onUnmounted, watch, nextTick } from 'vue'
import QueuePanel from './components/QueuePanel.vue'
import AuditPanel from './components/AuditPanel.vue'
import { useWebSocket } from './composables/useWebSocket'
import { useKeyboard } from './composables/useKeyboard'
import { playDing } from './composables/useAudio'
import { fetchPendingQueue } from './api/chat.js'

// ── 暗黑模式 ────────────────────────────────
const isDark = ref(localStorage.getItem('theme') === 'dark')

function toggleTheme() {
  isDark.value = !isDark.value
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
  document.documentElement.classList.toggle('dark', isDark.value)
}

onMounted(() => {
  document.documentElement.classList.toggle('dark', isDark.value)
})

// ── 全局状态 ────────────────────────────────
const selectedTrace = ref(null)
const selectedIndex = ref(-1)
const refreshTrigger = ref(0)
const queue = ref([])
const queueLoading = ref(false)

// 多账号
const ACCOUNTS = [
  { id: 'sales_01', label: '晨邦小黄', color: '#5b5cff' },
  { id: 'sales_02', label: '销售二组', color: '#22c55e' },
  { id: 'sales_03', label: '销售三组', color: '#f59e0b' },
]
const selectedAccount = ref('all') // 'all' | account_id

const accountList = computed(() => {
  const counts = {}
  for (const item of queue.value) {
    counts[item.account_id] = (counts[item.account_id] || 0) + 1
  }
  return ACCOUNTS.map(a => ({
    ...a,
    pending: counts[a.id] || 0,
  }))
})

const totalPending = computed(() => queue.value.length)

// 搜索与筛选
const searchQuery = ref('')
const filterMode = ref('all') // 'all' | 'low_confidence'

const filteredQueue = computed(() => {
  let items = queue.value
  // 账号筛选
  if (selectedAccount.value !== 'all') {
    items = items.filter(t => t.account_id === selectedAccount.value)
  }
  // 置信度筛选
  if (filterMode.value === 'low_confidence') {
    items = items.filter(t => (t.max_score || 0) < 0.65)
  }
  // 搜索
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    items = items.filter(t =>
      t.account_id?.toLowerCase().includes(q) ||
      t.customer_id?.toLowerCase().includes(q)
    )
  }
  return items
})

// ── 数据加载 ────────────────────────────────
async function loadQueue() {
  queueLoading.value = true
  try {
    const data = await fetchPendingQueue({ limit: 50 })
    queue.value = data.items
  } catch (e) {
    console.error('[App] Queue load failed:', e)
  } finally {
    queueLoading.value = false
  }
}

// ── WebSocket + 新消息音效 ───────────────────
const { isConnected } = useWebSocket({
  onNewMessage: () => {
    playDing()
    refreshTrigger.value++
    loadQueue()
  },
})

// ── 选中逻辑 ────────────────────────────────
function selectTrace(trace, index) {
  selectedTrace.value = trace
  selectedIndex.value = index
}

function selectNext() {
  const items = filteredQueue.value
  if (items.length === 0) return
  const next = (selectedIndex.value + 1) % items.length
  selectedTrace.value = items[next]
  selectedIndex.value = next
  nextTick(() => {
    document.querySelector('.queue-card.active')?.scrollIntoView({ block: 'nearest' })
  })
}

function selectPrev() {
  const items = filteredQueue.value
  if (items.length === 0) return
  const prev = (selectedIndex.value - 1 + items.length) % items.length
  selectedTrace.value = items[prev]
  selectedIndex.value = prev
  nextTick(() => {
    document.querySelector('.queue-card.active')?.scrollIntoView({ block: 'nearest' })
  })
}

function clearSelection() {
  selectedTrace.value = null
  selectedIndex.value = -1
}

function onDone() {
  clearSelection()
  loadQueue()
  // 自动跳到下一条
  nextTick(() => {
    if (filteredQueue.value.length > 0) {
      selectedTrace.value = filteredQueue.value[0]
      selectedIndex.value = 0
    }
  })
}

// ── 键盘快捷键 ──────────────────────────────
const auditRef = ref(null)

useKeyboard({
  onAccept: () => auditRef.value?.doAccept?.(),
  onModify: () => auditRef.value?.doModify?.(),
  onReject: () => auditRef.value?.doReject?.(),
  onUp: selectPrev,
  onDown: selectNext,
})

// ── 提供给子组件 ────────────────────────────
provide('refreshTrigger', refreshTrigger)
provide('isDark', isDark)

// ── 初始化 ──────────────────────────────────
let pollTimer = null
onMounted(() => {
  loadQueue()
  pollTimer = setInterval(loadQueue, 8000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

import { Search, Moon, Sunny } from '@element-plus/icons-vue'
</script>

<template>
  <div class="flex flex-col h-screen w-screen overflow-hidden" :class="{ dark: isDark }">

    <!-- ═══════════════ 顶部全局控制栏 ═══════════════ -->
    <header class="flex-shrink-0 px-6 py-3 border-b flex items-center justify-between"
      style="background: var(--bg-surface); border-color: var(--border); transition: all 0.3s ease;">

      <!-- 品牌 + 状态 -->
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg flex items-center justify-center"
          style="background: linear-gradient(135deg, #5b5cff, #8b5cf6);">
          <span class="text-white text-sm font-bold tracking-wide">无</span>
        </div>
        <div>
          <h1 class="text-sm font-semibold tracking-tight" style="color: var(--text-primary);">
            无界AI 审核工作台
          </h1>
          <div class="flex items-center gap-1.5 mt-0.5">
            <span class="pulse-connected"></span>
            <span class="text-[11px]" style="color: var(--text-tertiary);">
              {{ isConnected ? '实时连接' : '离线重连中...' }}
            </span>
          </div>
        </div>
      </div>

      <!-- 搜索 + 筛选 + 账号 -->
      <div class="flex items-center gap-2">
        <!-- 账号切换 -->
        <el-select v-model="selectedAccount" size="small" style="width: 160px;" class="account-select">
          <el-option label="全部账号" value="all">
            <span>全部账号</span>
            <el-badge :value="totalPending" :max="99" class="ml-2" />
          </el-option>
          <el-option
            v-for="acc in accountList"
            :key="acc.id"
            :label="acc.label"
            :value="acc.id"
          >
            <div class="flex items-center justify-between w-full">
              <div class="flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full flex-shrink-0" :style="{ background: acc.color }"></span>
                <span>{{ acc.label }}</span>
              </div>
              <el-badge :value="acc.pending" :max="99" v-if="acc.pending > 0" />
            </div>
          </el-option>
        </el-select>

        <div class="relative">
          <el-input
            v-model="searchQuery"
            placeholder="搜索账号/客户..."
            size="small"
            clearable
            :prefix-icon="Search"
            class="search-input"
            style="width: 200px;"
          />
        </div>
        <el-select v-model="filterMode" size="small" style="width: 130px;">
          <el-option label="全部消息" value="all" />
          <el-option label="⚠️ 仅低置信度" value="low_confidence" />
        </el-select>
        <el-button
          size="small"
          :icon="isDark ? Sunny : Moon"
          circle
          @click="toggleTheme"
          style="background: var(--bg-hover); border-color: var(--border); color: var(--text-secondary);"
        />
      </div>
    </header>

    <!-- ═══════════════ 主内容区 (左右分栏) ═══════════════ -->
    <div class="flex flex-1 overflow-hidden">
      <!-- 左侧队列 (35%) -->
      <aside class="w-[35%] min-w-[360px] border-r flex flex-col"
        style="background: var(--bg-surface); border-color: var(--border);">
        <QueuePanel
          :queue="filteredQueue"
          :loading="queueLoading"
          :selectedIndex="selectedIndex"
          @select="(t, i) => selectTrace(t, i)"
          @refresh="loadQueue"
        />
      </aside>

      <!-- 右侧审核区 (65%) -->
      <main class="flex-1 flex flex-col" style="background: var(--bg-app);">
        <AuditPanel
          ref="auditRef"
          :trace="selectedTrace"
          @done="onDone"
        />
      </main>
    </div>
  </div>
</template>

<style scoped>
.search-input :deep(.el-input__wrapper) {
  background: var(--bg-app);
  border-color: var(--border);
  box-shadow: none;
  border-radius: var(--radius-sm);
}
.search-input :deep(.el-input__wrapper:hover) {
  border-color: var(--accent);
}
</style>
