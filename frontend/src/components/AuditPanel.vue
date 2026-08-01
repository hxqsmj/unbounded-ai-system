<script setup>
import { ref, watch, computed, nextTick } from 'vue'
import { fetchTraceDetail, confirmSend, recordOperation } from '../api/chat.js'
import { playSuccess, playReject } from '../composables/useAudio.js'

// ── Props & Emits ──────────────────────────
const props = defineProps({
  trace: { type: Object, default: null },
})
const emit = defineEmits(['done'])

// ── 状态 ───────────────────────────────────
const detail = ref(null)
const loading = ref(false)
const editedText = ref('')
const submitting = ref(null) // 'ACCEPT' | 'MODIFY' | 'REJECT' | null
const submitResult = ref(null)
const showRagContext = ref(false)
const editorRef = ref(null)

// ── 快捷话术 (localStorage 持久化) — 地坪销售专属模板 ──
const STORAGE_KEY = 'wujie_quick_phrases'
const defaultPhrases = [
  { label: '💬 加上礼貌用语', append: '，感谢您的咨询，如有其他问题随时联系我！' },
  { label: '💰 补充报价说明', append: '\n\n具体报价需要根据现场基层情况（平整度、厚度、面积）来核算，咱们提供免费上门勘测，技术员看完现场给您出详细方案和报价清单。' },
  { label: '📞 预约上门勘测', append: '\n\n我安排技术员免费上门勘测、带样打样，您看这周什么时间方便？也可以直接拨18606859158预约。' },
  { label: '⏱️ 补充施工工期', append: '，环氧地坪施工工期一般为3-5天，具体视面积和基层条件而定，工期和质保都会写进合同。' },
  { label: '🛡️ 补充质保说明', append: '，咱们嘉兴晨邦包工包料一站式服务，环氧地坪质保期一般2-3年，后期有养护指导，售后有保障。' },
]

const quickPhrases = ref(loadPhrases())
const showPhraseEditor = ref(false)
const newPhrase = ref({ label: '', append: '' })

function loadPhrases() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    return saved ? JSON.parse(saved) : [...defaultPhrases]
  } catch { return [...defaultPhrases] }
}

function savePhrases() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(quickPhrases.value))
}

function addPhrase() {
  if (!newPhrase.value.label.trim() || !newPhrase.value.append.trim()) return
  quickPhrases.value.push({ ...newPhrase.value })
  newPhrase.value = { label: '', append: '' }
  savePhrases()
}

function removePhrase(idx) {
  quickPhrases.value.splice(idx, 1)
  savePhrases()
}

function resetPhrases() {
  quickPhrases.value = [...defaultPhrases]
  savePhrases()
}

function appendPhrase(text) {
  editedText.value = (editedText.value || '') + text
  nextTick(() => { editorRef.value?.focus?.() })
}

// ── 加载详情 ────────────────────────────────
async function loadDetail(trace) {
  if (!trace?.trace_id) return
  loading.value = true
  submitResult.value = null
  showRagContext.value = false
  try {
    const data = await fetchTraceDetail(trace.trace_id)
    detail.value = data
    editedText.value = data.generated_text || ''
  } catch {
    detail.value = trace
    editedText.value = trace.generated_text || ''
  } finally {
    loading.value = false
  }
}

watch(() => props.trace, (t) => {
  if (t) loadDetail(t)
  else { detail.value = null; editedText.value = ''; submitResult.value = null }
}, { immediate: true })

// ── 是否修改 ───────────────────────────────
const isModified = computed(() =>
  editedText.value !== (detail.value?.generated_text || '')
)

// ── 提交操作 ───────────────────────────────
async function doAction(action) {
  if (!detail.value || submitting.value) return
  submitting.value = action
  submitResult.value = null

  try {
    const result = await confirmSend({
      trace_id: detail.value.trace_id,
      final_text: action === 'REJECT' ? '' : editedText.value,
      is_modified: action === 'MODIFY',
      action,
    })
    if (action === 'REJECT') playReject()
    else playSuccess()

    // 记录操作日志 (operator 取自登录时的操作员名称，不再硬编码 'admin')
    recordOperation({
      trace_id: detail.value.trace_id,
      action,
      operator: localStorage.getItem('wujie_operator_name') || '操作员',
      detail: detail.value.user_input?.substring(0, 50) || '',
    }).catch(() => {})

    submitResult.value = { success: true, message: actionLabel(action) + ' — ' + result.message }
    setTimeout(() => emit('done'), 600)
  } catch (e) {
    submitResult.value = { success: false, message: '失败: ' + (e.response?.data?.detail || e.message) }
  } finally {
    submitting.value = null
  }
}

function actionLabel(a) {
  return { ACCEPT: '✅ 已采纳', MODIFY: '✏️ 已修改并发送', REJECT: '❌ 已拒绝' }[a] || ''
}

// ── 暴露快捷键方法 ─────────────────────────
defineExpose({
  doAccept: () => doAction('ACCEPT'),
  doModify: () => { if (isModified.value) doAction('MODIFY') },
  doReject: () => doAction('REJECT'),
})

// ── 账号色 ────────────────────────────────
const accMap = {
  sales_01: { color: '#5b5cff', bg: 'rgba(91,92,255,0.1)' },
  sales_02: { color: '#22c55e', bg: 'rgba(34,197,94,0.1)' },
  sales_03: { color: '#f59e0b', bg: 'rgba(245,158,11,0.1)' },
}
function accColor(aid) { return accMap[aid]?.color || '#909399' }
function accBg(aid) { return accMap[aid]?.bg || 'var(--bg-hover)' }

// ── RAG 得分色 ─────────────────────────────
function scoreLevel(s) {
  if (!s && s !== 0) return ''
  if (s >= 0.8) return 'border-l-green-500'
  if (s >= 0.65) return 'border-l-amber-500'
  return 'border-l-red-400'
}

function scoreBg(s) {
  if (!s && s !== 0) return { background: 'var(--bg-hover)' }
  if (s >= 0.8) return { background: 'var(--green-bg)', color: 'var(--green)' }
  if (s >= 0.65) return { background: 'var(--amber-bg)', color: 'var(--amber)' }
  return { background: 'var(--red-bg)', color: 'var(--red)' }
}
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- ── 空状态 ──────────────────────────── -->
    <div v-if="!trace" class="flex flex-col items-center justify-center flex-1 gap-5 px-8 text-center">
      <div class="w-24 h-24 rounded-full flex items-center justify-center"
        style="background: var(--bg-hover);">
        <el-icon :size="42"><ChatDotRound /></el-icon>
      </div>
      <div>
        <p class="text-base font-medium" style="color: var(--text-secondary);">
          选择左侧消息开始审核
        </p>
        <p class="text-xs mt-1.5" style="color: var(--text-tertiary);">
          可使用 ↑ ↓ 键快速切换，Ctrl+Enter 一键采纳
        </p>
      </div>
    </div>

    <template v-else>
      <!-- ── 头部标签 ──────────────────────────── -->
      <div class="px-5 py-3 border-b flex items-center justify-between"
        style="background: var(--bg-surface); border-color: var(--border);">
        <div class="flex items-center gap-2">
          <span class="w-2 h-2 rounded-full" :style="{ background: accColor(trace.account_id) }"></span>
          <span class="text-xs px-1.5 py-0.5 rounded font-medium" :style="{ background: accBg(trace.account_id), color: accColor(trace.account_id) }">
            {{ trace.account_id }}
          </span>
          <span style="color: var(--text-tertiary);">→</span>
          <span class="text-sm font-medium" style="color: var(--accent);">
            {{ trace.customer_id }}
          </span>
        </div>
        <span class="text-[10px] font-mono px-2 py-0.5 rounded-full"
          style="background: var(--bg-hover); color: var(--text-tertiary);">
          {{ trace.trace_id }}
        </span>
      </div>

      <!-- ── 滚动内容区 ──────────────────────────── -->
      <div class="flex-1 overflow-y-auto px-5 py-4 space-y-4">

        <!-- 1. 客户消息气泡 -->
        <div class="flex gap-3 animate-fade-in-up">
          <div class="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center text-white text-xs font-bold mt-1"
            style="background: linear-gradient(135deg, #a0a0b0, #707090);">
            {{ (trace.customer_id || '?')[0].toUpperCase() }}
          </div>
          <div class="flex-1 min-w-0">
            <div class="inline-block max-w-[85%] rounded-2xl rounded-tl-sm px-4 py-2.5"
              style="background: var(--bg-bubble-customer);">
              <p class="text-[13px] leading-relaxed" style="color: var(--text-primary);">
                {{ detail?.user_input || trace.user_input }}
              </p>
            </div>
            <div class="text-[10px] mt-1 ml-1" style="color: var(--text-tertiary);">
              {{ detail?.timestamp ? new Date(detail.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '' }}
            </div>
          </div>
        </div>

        <!-- 2. AI 建议回复气泡 -->
        <div class="flex gap-3 justify-end animate-fade-in-up" v-if="!detail?.is_fallback">
          <div class="min-w-0">
            <div class="inline-block max-w-[85%] rounded-2xl rounded-tr-sm px-4 py-2.5 border"
              style="background: var(--bg-bubble-ai); border-color: var(--accent-light);">
              <div class="flex items-center gap-1.5 mb-1">
                <span class="animate-sparkle text-xs">✨</span>
                <span class="text-[11px] font-medium" style="color: var(--accent);">AI 建议</span>
                <span v-if="detail?.max_score" class="text-[10px] ml-auto px-1.5 py-0.5 rounded-full font-mono"
                  :style="scoreBg(detail.max_score)">
                  {{ detail.max_score.toFixed(2) }}
                </span>
              </div>
              <p class="text-[13px] leading-relaxed" style="color: var(--text-primary);">
                {{ detail?.generated_text }}
              </p>
            </div>
          </div>
          <div class="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center mt-1"
            style="background: linear-gradient(135deg, var(--accent), #8b5cf6);">
            <el-icon :size="14" color="white"><MagicStick /></el-icon>
          </div>
        </div>

        <!-- 兜底拒答提示 -->
        <div v-if="detail?.is_fallback" class="flex items-center gap-2 px-3 py-2 rounded-lg animate-fade-in-up"
          style="background: var(--amber-bg);">
          <span class="text-sm">⚠️</span>
          <span class="text-[12px]" style="color: var(--amber);">
            置信度不足 ({{ detail?.max_score?.toFixed(2) }} &lt; 0.65)，AI 已触发兜底拒答。
          </span>
        </div>

        <!-- 3. 智能编辑框 -->
        <div class="space-y-2">
          <!-- 快捷话术 Tag -->
          <div class="flex flex-wrap gap-1.5 items-center">
            <button
              v-for="(phrase, idx) in quickPhrases"
              :key="idx"
              @click="appendPhrase(phrase.append)"
              class="text-[11px] px-2.5 py-1 rounded-full border transition-all hover:opacity-80 active:scale-95 group relative"
              style="background: var(--bg-surface); border-color: var(--border); color: var(--text-secondary);"
            >
              {{ phrase.label }}
              <span @click.stop="removePhrase(idx)" class="ml-1 opacity-0 group-hover:opacity-100 text-red-400 cursor-pointer">×</span>
            </button>
            <button
              @click="showPhraseEditor = !showPhraseEditor"
              class="text-[11px] px-2 py-1 rounded-full border-dashed border transition-all hover:opacity-80"
              style="background: transparent; border-color: var(--accent); color: var(--accent);"
            >+ 管理</button>
          </div>

          <!-- 话术编辑器 -->
          <div v-if="showPhraseEditor" class="p-3 rounded-lg border space-y-2" style="background: var(--bg-surface); border-color: var(--border);">
            <div class="flex gap-2">
              <input v-model="newPhrase.label" placeholder="标签 (如: 📞 预约勘测)" class="flex-1 text-[12px] px-2 py-1 rounded border" style="background: var(--bg-app); border-color: var(--border); color: var(--text-primary);" @keyup.enter="addPhrase" />
              <input v-model="newPhrase.append" placeholder="追加文本" class="flex-[2] text-[12px] px-2 py-1 rounded border" style="background: var(--bg-app); border-color: var(--border); color: var(--text-primary);" @keyup.enter="addPhrase" />
              <button @click="addPhrase" class="text-[11px] px-3 py-1 rounded text-white" style="background: var(--accent);">添加</button>
            </div>
            <div class="flex justify-between">
              <span class="text-[10px]" style="color: var(--text-tertiary);">hover 话术标签可删除 × | 自动保存到浏览器</span>
              <button @click="resetPhrases" class="text-[10px]" style="color: var(--text-tertiary);">恢复默认</button>
            </div>
          </div>

          <!-- 编辑框 -->
          <div class="animate-glow rounded-lg transition-shadow duration-300"
            style="background: var(--bg-surface); border: 1px solid var(--border);">
            <el-input
              ref="editorRef"
              v-model="editedText"
              type="textarea"
              :rows="editedText ? Math.min(Math.ceil(editedText.length / 55), 8) : 3"
              :disabled="submitting !== null"
              placeholder="编辑回复内容... Ctrl+Enter 采纳  |  Alt+Enter 修改  |  Esc 拒绝"
              class="editor-area"
            />
          </div>
          <div v-if="isModified" class="flex items-center gap-1 text-[11px] animate-fade-in-up"
            style="color: var(--amber);">
            <span>✏️</span> 已人工修改，将触发数据飞轮反馈
          </div>
        </div>

        <!-- 4. RAG 引用折叠 -->
        <div v-if="detail?.retrieved_docs?.length" class="rounded-lg overflow-hidden border"
          style="background: var(--bg-surface); border-color: var(--border);">
          <button
            @click="showRagContext = !showRagContext"
            class="w-full px-4 py-2.5 flex items-center justify-between text-left hover:opacity-80 transition-opacity"
            style="background: var(--bg-hover);"
          >
            <div class="flex items-center gap-2">
              <el-icon :size="14" color="var(--accent)"><Search /></el-icon>
              <span class="text-[12px] font-medium" style="color: var(--text-primary);">
                RAG 检索依据 (Top-{{ detail.retrieved_docs.length }})
              </span>
            </div>
            <el-icon :size="14" :style="{ transform: showRagContext ? 'rotate(180deg)' : '', transition: 'transform 0.2s' }">
              <ArrowDown />
            </el-icon>
          </button>
          <div v-show="showRagContext" class="divide-y" style="border-color: var(--border);">
            <div
              v-for="(doc, i) in detail.retrieved_docs"
              :key="i"
              class="px-4 py-3 border-l-4"
              :class="scoreLevel(doc.score)"
              style="border-left-width: 4px; border-left-style: solid;"
            >
              <div class="flex items-center justify-between mb-1.5">
                <span class="text-[11px] font-medium" style="color: var(--text-secondary);">
                  来源 #{{ i + 1 }}
                </span>
                <span class="text-[10px] px-1.5 py-0.5 rounded-full font-mono"
                  :style="scoreBg(doc.score)">
                  匹配 {{ doc.score?.toFixed(2) }}
                </span>
              </div>
              <p class="text-[12px] leading-relaxed" style="color: var(--text-secondary);">
                {{ doc.text }}
              </p>
              <div v-if="doc.metadata && Object.keys(doc.metadata).length" class="flex flex-wrap gap-1 mt-2">
                <span
                  v-for="(v, k) in doc.metadata"
                  :key="k"
                  class="text-[10px] px-1.5 py-0.5 rounded"
                  style="background: var(--bg-hover); color: var(--text-tertiary);">
                  {{ k }}: {{ v }}
                </span>
              </div>
            </div>
          </div>
        </div>

      </div>

      <!-- ── 底部悬浮操作栏 ──────────────────────────── -->
      <div class="flex-shrink-0 px-5 py-3 border-t"
        style="background: var(--bg-surface); border-color: var(--border); box-shadow: 0 -4px 12px rgba(0,0,0,0.04);">
        <!-- 结果提示 -->
        <div v-if="submitResult" class="mb-2">
          <el-alert
            :title="submitResult.message"
            :type="submitResult.success ? 'success' : 'error'"
            :closable="false"
            show-icon
          />
        </div>

        <div class="flex items-center gap-3">
          <!-- 采纳 -->
          <el-button
            type="primary"
            size="large"
            :loading="submitting === 'ACCEPT'"
            :disabled="submitting !== null"
            @click="doAction('ACCEPT')"
            class="action-btn-accept"
          >
            <el-icon class="mr-1"><Check /></el-icon>
            一键采纳
            <kbd class="ml-2 px-1.5 py-0.5 text-[10px] rounded border opacity-70"
              style="background: rgba(255,255,255,0.15); border-color: rgba(255,255,255,0.2);">
              ⌃↵
            </kbd>
          </el-button>

          <!-- 修改 -->
          <el-button
            type="warning"
            size="large"
            :loading="submitting === 'MODIFY'"
            :disabled="submitting !== null || !isModified"
            @click="doAction('MODIFY')"
          >
            <el-icon class="mr-1"><Edit /></el-icon>
            修改并发送
            <kbd class="ml-2 px-1.5 py-0.5 text-[10px] rounded border opacity-70"
              style="background: rgba(0,0,0,0.06); border-color: rgba(0,0,0,0.1);">
              ⌥↵
            </kbd>
          </el-button>

          <!-- 拒绝 -->
          <el-button
            type="danger"
            size="large"
            :loading="submitting === 'REJECT'"
            :disabled="submitting !== null"
            plain
            @click="doAction('REJECT')"
          >
            <el-icon class="mr-1"><Close /></el-icon>
            拒绝
            <kbd class="ml-2 px-1.5 py-0.5 text-[10px] rounded border opacity-70"
              style="background: rgba(0,0,0,0.06); border-color: rgba(0,0,0,0.1);">
              Esc
            </kbd>
          </el-button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.editor-area :deep(.el-textarea__inner) {
  background: transparent;
  border: none !important;
  box-shadow: none !important;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary);
  resize: vertical;
}
.editor-area :deep(.el-textarea__inner):focus {
  border: none !important;
  box-shadow: none !important;
}

.action-btn-accept {
  background: linear-gradient(135deg, #5b5cff, #6366f1) !important;
  border-color: transparent !important;
}
.action-btn-accept:hover {
  background: linear-gradient(135deg, #4b4cee, #5558e8) !important;
}
</style>
