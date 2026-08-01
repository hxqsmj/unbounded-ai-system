<script setup>
import { ref, computed, provide, onMounted, onUnmounted, watch, nextTick } from 'vue'
import QueuePanel from './components/QueuePanel.vue'
import AuditPanel from './components/AuditPanel.vue'
import { useWebSocket } from './composables/useWebSocket'
import { useKeyboard } from './composables/useKeyboard'
import { playDing } from './composables/useAudio'
import { fetchPendingQueue, fetchDashboard, fetchOperationLogs, getToken, setAuth } from './api/chat.js'

// ── 登录状态 (Token 鉴权) ────────────────────
const isAuthed = ref(!!getToken())
const loginToken = ref('')
const loginOperator = ref(localStorage.getItem('wujie_operator_name') || '')
const loginError = ref('')
const loginLoading = ref(false)

// 防浏览器自动填充: 输入框初始 readonly, 聚焦时才解锁
// (autocomplete=off 对移动端 Chrome 常被无视, readonly 是可靠拦截)
const tokenReadonly = ref(true)
const operatorReadonly = ref(true)
function unlockField(field) {
  if (field === 'token') tokenReadonly.value = false
  else operatorReadonly.value = false
}

function doLogin() {
  if (!loginToken.value.trim()) {
    loginError.value = '请输入 API Token'
    return
  }
  loginLoading.value = true
  loginError.value = ''
  // 用 /pending 验证 token 有效性
  fetchPendingQueue({ limit: 1 })
    .then(() => {
      setAuth(loginToken.value.trim(), loginOperator.value.trim() || '操作员')
      window.location.reload() // 刷新以建立带 token 的 WebSocket
    })
    .catch((e) => {
      loginError.value = (e.response?.status === 401)
        ? 'Token 无效，请确认使用半角英文输入（不要有空格、不要中文输入法全角）'
        : '无法连接服务器，请检查网络'
    })
    .finally(() => { loginLoading.value = false })
}

function doLogout() {
  localStorage.removeItem('wujie_api_token')
  window.location.reload()
}

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

// ── 标签页
const activeTab = ref('review') // 'review' | 'dashboard' | 'logs'

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

// ── 看板 & 日志 ────────────────────────────
const dashboard = ref(null)
const opsLogs = ref([])
const dashLoading = ref(false)

async function loadDashboard() {
  dashLoading.value = true
  try { dashboard.value = await fetchDashboard() } catch (e) { console.error(e) }
  finally { dashLoading.value = false }
}

async function loadLogs() {
  try { const d = await fetchOperationLogs(50); opsLogs.value = d.items } catch (e) { console.error(e) }
}

// tab 切换自动加载
watch(activeTab, (tab) => {
  if (tab === 'dashboard') loadDashboard()
  if (tab === 'logs') loadLogs()
})

// ── 初始化 ──────────────────────────────────
let pollTimer = null
onMounted(() => {
  if (!isAuthed.value) return // 未登录不加载数据（避免 401 循环）
  loadQueue()
  pollTimer = setInterval(loadQueue, 8000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

import { Search, Moon, Sunny, SwitchButton } from '@element-plus/icons-vue'
</script>

<template>
  <div class="flex flex-col h-screen w-screen overflow-hidden" :class="{ dark: isDark }">

    <!-- ═══════════════ 登录界面 (Token 鉴权) ═══════════════ -->
    <div v-if="!isAuthed" class="fixed inset-0 z-50 flex items-center justify-center"
      style="background: var(--bg-app);">
      <div class="w-[380px] p-8 rounded-2xl border shadow-xl"
        style="background: var(--bg-surface); border-color: var(--border);">
        <div class="flex flex-col items-center gap-3 mb-6">
          <div class="w-14 h-14 rounded-xl flex items-center justify-center"
            style="background: linear-gradient(135deg, #5b5cff, #8b5cf6);">
            <span class="text-white text-xl font-bold">无</span>
          </div>
          <h1 class="text-base font-semibold" style="color: var(--text-primary);">无界AI 审核工作台</h1>
          <p class="text-[11px]" style="color: var(--text-tertiary);">请输入访问凭证以登录</p>
        </div>

        <div class="space-y-3">
          <el-input
            v-model="loginOperator"
            placeholder="操作员名称 (可选)"
            size="large"
            clearable
            autocomplete="off"
            name="api-operator-login"
            :readonly="operatorReadonly"
            @focus="unlockField('operator')"
          />
          <el-input
            v-model="loginToken"
            placeholder="API Token"
            size="large"
            show-password
            autocomplete="new-password"
            name="api-token-login"
            :readonly="tokenReadonly"
            @focus="unlockField('token')"
            @keyup.enter="doLogin"
          />
          <el-button
            type="primary"
            size="large"
            class="w-full"
            :loading="loginLoading"
            @click="doLogin"
            style="background: linear-gradient(135deg, #5b5cff, #6366f1); border-color: transparent;"
          >
            登 录
          </el-button>
          <p v-if="loginError" class="text-[12px] text-center" style="color: var(--red);">{{ loginError }}</p>
          <p class="text-[10px] leading-relaxed" style="color: var(--text-tertiary);">
            Token 由系统管理员在服务器 .env 的 API_TOKEN 中配置
          </p>
        </div>
      </div>
    </div>

    <!-- ═══════════════ 主界面 ═══════════════ -->
    <template v-else>

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
          <div class="flex items-center gap-3 mt-0.5">
            <span class="pulse-connected"></span>
            <span class="text-[11px]" style="color: var(--text-tertiary);">
              {{ isConnected ? '实时连接' : '离线重连中...' }}
            </span>
            <div class="flex gap-0.5 ml-2" style="background: var(--bg-app); border-radius: 6px; padding: 2px;">
              <button @click="activeTab = 'review'" class="text-[11px] px-2.5 py-1 rounded transition-colors"
                :style="activeTab === 'review' ? { background: 'var(--bg-surface)', color: 'var(--accent)', fontWeight: 600 } : { color: 'var(--text-tertiary)' }">审核</button>
              <button @click="activeTab = 'dashboard'" class="text-[11px] px-2.5 py-1 rounded transition-colors"
                :style="activeTab === 'dashboard' ? { background: 'var(--bg-surface)', color: 'var(--accent)', fontWeight: 600 } : { color: 'var(--text-tertiary)' }">看板</button>
              <button @click="activeTab = 'logs'" class="text-[11px] px-2.5 py-1 rounded transition-colors"
                :style="activeTab === 'logs' ? { background: 'var(--bg-surface)', color: 'var(--accent)', fontWeight: 600 } : { color: 'var(--text-tertiary)' }">日志</button>
            </div>
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
        <el-button
          size="small"
          circle
          title="退出登录"
          @click="doLogout"
          style="background: var(--bg-hover); border-color: var(--border); color: var(--text-secondary);"
        >
          <el-icon><SwitchButton /></el-icon>
        </el-button>
      </div>
    </header>

    <!-- ═══════════════ 主内容区 ═══════════════ -->
    <!-- 审核视图 -->
    <div v-if="activeTab === 'review'" class="flex flex-1 overflow-hidden">
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
      <main class="flex-1 flex flex-col" style="background: var(--bg-app);">
        <AuditPanel ref="auditRef" :trace="selectedTrace" @done="onDone" />
      </main>
    </div>

    <!-- 数据看板 -->
    <div v-if="activeTab === 'dashboard'" class="flex-1 overflow-y-auto p-6" style="background: var(--bg-app);">
      <div class="max-w-4xl mx-auto space-y-5">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-semibold" style="color: var(--text-primary);">📊 数据看板</h2>
          <button @click="loadDashboard" class="text-xs px-3 py-1 rounded" style="background: var(--accent); color: white;">刷新</button>
        </div>

        <!-- 核心指标 -->
        <div class="grid grid-cols-4 gap-4">
          <div class="p-4 rounded-lg" style="background: var(--bg-surface); border: 1px solid var(--border);">
            <div class="text-[11px] uppercase tracking-wide" style="color: var(--text-tertiary);">待审核</div>
            <div class="text-2xl font-bold mt-1" style="color: var(--accent);">{{ dashboard?.pending_count || 0 }}</div>
          </div>
          <div class="p-4 rounded-lg" style="background: var(--bg-surface); border: 1px solid var(--border);">
            <div class="text-[11px] uppercase tracking-wide" style="color: var(--text-tertiary);">今日处理</div>
            <div class="text-2xl font-bold mt-1" style="color: var(--green);">{{ dashboard?.processed_today || 0 }}</div>
          </div>
          <div class="p-4 rounded-lg" style="background: var(--bg-surface); border: 1px solid var(--border);">
            <div class="text-[11px] uppercase tracking-wide" style="color: var(--text-tertiary);">AI 采纳率</div>
            <div class="text-2xl font-bold mt-1" style="color: var(--accent);">{{ dashboard?.acceptance_rate || 0 }}%</div>
          </div>
          <div class="p-4 rounded-lg" style="background: var(--bg-surface); border: 1px solid var(--border);">
            <div class="text-[11px] uppercase tracking-wide" style="color: var(--text-tertiary);">今日操作</div>
            <div class="text-2xl font-bold mt-1" style="color: var(--amber);">{{ dashboard?.today_operations || 0 }}</div>
          </div>
        </div>

        <!-- 账号分布 -->
        <div class="p-4 rounded-lg" style="background: var(--bg-surface); border: 1px solid var(--border);">
          <h3 class="text-sm font-medium mb-3" style="color: var(--text-primary);">各账号处理量 (今日)</h3>
          <div class="space-y-2">
            <div v-for="(count, acc) in dashboard?.by_account || {}" :key="acc" class="flex items-center gap-3">
              <span class="text-xs w-20" style="color: var(--text-secondary);">{{ acc }}</span>
              <div class="flex-1 h-5 rounded" style="background: var(--bg-app);">
                <div class="h-full rounded transition-all" :style="{ width: Math.min(count/(dashboard?.processed_today||1)*100, 100) + '%', background: 'var(--accent)' }"></div>
              </div>
              <span class="text-xs font-mono" style="color: var(--text-secondary);">{{ count }}</span>
            </div>
          </div>
        </div>

        <!-- 7天趋势 -->
        <div class="p-4 rounded-lg" style="background: var(--bg-surface); border: 1px solid var(--border);">
          <h3 class="text-sm font-medium mb-3" style="color: var(--text-primary);">7天趋势</h3>
          <div class="flex items-end gap-1" style="height: 100px;">
            <div v-for="d in dashboard?.weekly_trend || []" :key="d.date" class="flex-1 flex flex-col items-center gap-1">
              <span class="text-[10px] font-mono" style="color: var(--text-secondary);">{{ d.total }}</span>
              <div class="w-full rounded-t" style="background: var(--accent); opacity: 0.7;"
                :style="{ height: Math.max(d.total / Math.max(...((dashboard?.weekly_trend||[]).map(x=>x.total)||[1])) * 80, 4) + 'px' }"></div>
              <span class="text-[9px]" style="color: var(--text-tertiary);">{{ d.date.slice(5) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 操作日志 -->
    <div v-if="activeTab === 'logs'" class="flex-1 overflow-y-auto p-6" style="background: var(--bg-app);">
      <div class="max-w-3xl mx-auto space-y-3">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-semibold" style="color: var(--text-primary);">📋 操作日志</h2>
          <button @click="loadLogs" class="text-xs px-3 py-1 rounded" style="background: var(--accent); color: white;">刷新</button>
        </div>
        <div v-if="!opsLogs.length" class="text-center py-8" style="color: var(--text-tertiary);">暂无操作记录</div>
        <div v-for="log in opsLogs" :key="log.timestamp" class="flex items-center gap-3 px-4 py-2.5 rounded-lg"
          style="background: var(--bg-surface); border: 1px solid var(--border);">
          <span class="text-[10px] px-1.5 py-0.5 rounded font-mono"
            :style="{ background: {'ACCEPT':'var(--green-bg)','MODIFY':'var(--amber-bg)','REJECT':'var(--red-bg)'}[log.action] || 'var(--bg-hover)', color: {'ACCEPT':'var(--green)','MODIFY':'var(--amber)','REJECT':'var(--red)'}[log.action] }">{{ log.action }}</span>
          <span class="text-xs flex-1 truncate" style="color: var(--text-primary);">{{ log.detail }}</span>
          <span class="text-[10px] flex-shrink-0" style="color: var(--text-tertiary);">{{ log.timestamp?.slice(11,19) }}</span>
          <span class="text-[10px] font-mono flex-shrink-0" style="color: var(--text-tertiary);">{{ log.trace_id?.slice(0,10) }}</span>
        </div>
      </div>
    </div>
    </template>
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
