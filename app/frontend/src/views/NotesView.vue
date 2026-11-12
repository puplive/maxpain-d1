<script setup lang="ts">
/**
 * 研究笔记。
 * 知识点 / 案例 / 经验统一模板：内容 + 标签 + 可选品种日期区间 + 状态。
 * 状态流转：待验证 → 已验证 / 已失效，验证记录只追加不覆盖。
 * 间隔回顾按 1/3/7/30 天推进，用来强化记忆。
 */
import { computed, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDatePicker,
  NDivider,
  NDrawer,
  NDrawerContent,
  NDynamicTags,
  NInput,
  NPopconfirm,
  NSelect,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'
import {
  api,
  type Note,
  type NoteInput,
  type NoteKind,
  type NoteStatus,
} from '../api/client'
import { useMarketStore } from '../stores/market'
import { useRouter } from 'vue-router'

const store = useMarketStore()
const router = useRouter()
const message = useMessage()

const notes = ref<Note[]>([])
const loading = ref(true)

// 列表筛选（笔记量不大，全部在前端过滤）
const keyword = ref('')
const kindFilter = ref<'all' | NoteKind>('all')
const statusFilter = ref<'all' | NoteStatus>('all')
const tagFilter = ref<string | null>(null)
const dueOnly = ref(false)

// 抽屉：查看 / 编辑共用，activeId 指向 notes 里的最新对象
const drawerOpen = ref(false)
const activeId = ref<number | null>(null)
const editing = ref(false)
const saving = ref(false)
const form = ref<NoteInput>(emptyForm())

// 新增验证记录
const checkResult = ref<'pass' | 'fail' | 'observe'>('observe')
const checkComment = ref('')

const KIND_OPTIONS = [
  { label: '知识点', value: 'knowledge' },
  { label: '案例', value: 'case' },
  { label: '经验', value: 'experience' },
]
const STATUS_OPTIONS = [
  { label: '待验证', value: 'pending' },
  { label: '已验证', value: 'verified' },
  { label: '已失效', value: 'invalid' },
]
const CHECK_OPTIONS = [
  { label: '通过', value: 'pass' },
  { label: '未通过', value: 'fail' },
  { label: '待观察', value: 'observe' },
]

const KIND_LABEL: Record<NoteKind, string> = {
  knowledge: '知识点',
  case: '案例',
  experience: '经验',
}
const STATUS_LABEL: Record<NoteStatus, string> = {
  pending: '待验证',
  verified: '已验证',
  invalid: '已失效',
}
const CHECK_LABEL: Record<string, string> = {
  pass: '通过',
  fail: '未通过',
  observe: '待观察',
}
// 回顾间隔与后端 REVIEW_INTERVALS 一致
const REVIEW_INTERVALS = [1, 3, 7, 30]

function emptyForm(): NoteInput {
  return {
    title: '',
    kind: 'knowledge',
    content: '',
    tags: [],
    symbol: null,
    start_date: null,
    end_date: null,
    status: 'pending',
  }
}

function todayStr() {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// NDatePicker 用毫秒时间戳，后端存 YYYY-MM-DD 字符串
function dateToTs(value: string | null) {
  return value ? new Date(`${value}T00:00:00`).getTime() : null
}

function tsToDate(ts: number | null) {
  if (!ts) return null
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const kindType = (kind: NoteKind) =>
  kind === 'case' ? 'warning' : kind === 'experience' ? 'success' : 'info'
const statusType = (status: NoteStatus) =>
  status === 'verified' ? 'success' : status === 'invalid' ? 'error' : 'warning'
const checkType = (result: string) =>
  result === 'pass' ? 'success' : result === 'fail' ? 'error' : 'default'

const symbolOptions = computed(() =>
  store.symbols.map((item) => ({
    label: `${item.symbol} ${item.name}`,
    value: item.symbol,
  })),
)

const tagOptions = computed(() =>
  Array.from(new Set(notes.value.flatMap((note) => note.tags)))
    .sort()
    .map((tag) => ({ label: `#${tag}`, value: tag })),
)

const dueCount = computed(
  () =>
    notes.value.filter(
      (note) => note.next_review != null && note.next_review <= todayStr(),
    ).length,
)

const filteredNotes = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  const today = todayStr()
  return notes.value.filter((note) => {
    if (kindFilter.value !== 'all' && note.kind !== kindFilter.value) return false
    if (statusFilter.value !== 'all' && note.status !== statusFilter.value) {
      return false
    }
    if (tagFilter.value && !note.tags.includes(tagFilter.value)) return false
    if (
      dueOnly.value &&
      !(note.next_review != null && note.next_review <= today)
    ) {
      return false
    }
    if (text) {
      const hay = `${note.title}\n${note.content}\n${note.tags.join(' ')}`
      if (!hay.toLowerCase().includes(text)) return false
    }
    return true
  })
})

const current = computed(
  () => notes.value.find((note) => note.id === activeId.value) ?? null,
)

const drawerTitle = computed(() => {
  if (editing.value) {
    return activeId.value == null ? '新建笔记' : '编辑笔记'
  }
  return current.value?.title ?? '笔记'
})

function isDue(note: Note) {
  return note.next_review != null && note.next_review <= todayStr()
}

function dateRange(note: Note) {
  if (note.start_date && note.end_date) {
    return `${note.start_date} ~ ${note.end_date}`
  }
  return note.start_date ?? note.end_date ?? ''
}

function excerpt(note: Note) {
  return note.content.replace(/\s+/g, ' ').slice(0, 90)
}

function applyNote(note: Note) {
  notes.value = notes.value.map((item) => (item.id === note.id ? note : item))
}

async function loadNotes() {
  loading.value = true
  try {
    notes.value = await api.notes()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载笔记失败')
  } finally {
    loading.value = false
  }
}

function openNew() {
  form.value = emptyForm()
  activeId.value = null
  editing.value = true
  drawerOpen.value = true
}

function openNote(note: Note) {
  activeId.value = note.id
  editing.value = false
  drawerOpen.value = true
}

function startEdit() {
  const note = current.value
  if (!note) return
  form.value = {
    title: note.title,
    kind: note.kind,
    content: note.content,
    tags: [...note.tags],
    symbol: note.symbol,
    start_date: note.start_date,
    end_date: note.end_date,
    status: note.status,
  }
  editing.value = true
}

function cancelEdit() {
  if (activeId.value == null) {
    drawerOpen.value = false
  } else {
    editing.value = false
  }
}

async function save() {
  const title = form.value.title.trim()
  if (!title) {
    message.warning('标题不能为空')
    return
  }
  saving.value = true
  try {
    const payload = { ...form.value, title }
    if (activeId.value == null) {
      const created = await api.createNote(payload)
      notes.value = [created, ...notes.value]
      activeId.value = created.id
    } else {
      const updated = await api.updateNote(activeId.value, payload)
      applyNote(updated)
    }
    editing.value = false
    message.success('已保存')
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeNote() {
  if (activeId.value == null) return
  try {
    await api.deleteNote(activeId.value)
    notes.value = notes.value.filter((note) => note.id !== activeId.value)
    activeId.value = null
    drawerOpen.value = false
    message.success('已删除')
  } catch (error) {
    message.error(error instanceof Error ? error.message : '删除失败')
  }
}

async function addCheck() {
  if (activeId.value == null) return
  try {
    const updated = await api.addNoteCheck(activeId.value, {
      result: checkResult.value,
      comment: checkComment.value.trim(),
    })
    applyNote(updated)
    checkComment.value = ''
    message.success('已记录验证')
  } catch (error) {
    message.error(error instanceof Error ? error.message : '记录验证失败')
  }
}

async function review(remembered: boolean) {
  if (activeId.value == null) return
  try {
    const updated = await api.reviewNote(activeId.value, remembered)
    applyNote(updated)
    message.success(
      remembered
        ? `下次回顾：${updated.next_review}`
        : `已重置，明天再回顾`,
    )
  } catch (error) {
    message.error(error instanceof Error ? error.message : '回顾失败')
  }
}

function openBacktest(symbol: string) {
  store.selectedSymbol = symbol
  router.push({ name: 'backtest' })
}

onMounted(() => {
  void loadNotes()
  void store.ensureSymbols()
})
</script>

<template>
  <div class="toolbar">
    <n-input
      v-model:value="keyword"
      size="small"
      clearable
      placeholder="搜索标题 / 内容 / 标签"
      style="width: 220px"
    />
    <n-select
      v-model:value="kindFilter"
      size="small"
      :options="[{ label: '全部类型', value: 'all' }, ...KIND_OPTIONS]"
      style="width: 120px"
    />
    <n-select
      v-model:value="statusFilter"
      size="small"
      :options="[{ label: '全部状态', value: 'all' }, ...STATUS_OPTIONS]"
      style="width: 120px"
    />
    <n-select
      v-model:value="tagFilter"
      size="small"
      clearable
      :options="tagOptions"
      placeholder="标签"
      style="width: 130px"
    />
    <n-button
      size="small"
      :type="dueOnly ? 'primary' : 'default'"
      @click="dueOnly = !dueOnly"
    >
      今日回顾{{ dueCount ? ` (${dueCount})` : '' }}
    </n-button>
    <n-button size="small" type="primary" ghost @click="openNew">
      + 新建笔记
    </n-button>
    <span class="hint">共 {{ filteredNotes.length }} 条</span>
  </div>

  <n-spin :show="loading">
    <div v-if="filteredNotes.length" class="note-list">
      <n-card
        v-for="note in filteredNotes"
        :key="note.id"
        size="small"
        class="note-card"
        @click="openNote(note)"
      >
        <div class="note-row">
          <div class="note-main">
            <div class="note-title">
              {{ note.title }}
              <span v-if="isDue(note)" class="due">回顾</span>
            </div>
            <div class="note-meta">
              <n-tag size="tiny" :type="kindType(note.kind)">
                {{ KIND_LABEL[note.kind] }}
              </n-tag>
              <n-tag size="tiny" :type="statusType(note.status)">
                {{ STATUS_LABEL[note.status] }}
              </n-tag>
              <span v-if="note.symbol" class="hint">{{ note.symbol }}</span>
              <span v-for="tag in note.tags" :key="tag" class="note-tag">
                #{{ tag }}
              </span>
            </div>
            <div v-if="note.content" class="note-excerpt">
              {{ excerpt(note) }}
            </div>
          </div>
          <div class="note-side">
            <span class="hint">{{ note.updated_at.slice(0, 10) }}</span>
            <span v-if="dateRange(note)" class="hint">
              {{ dateRange(note) }}
            </span>
          </div>
        </div>
      </n-card>
    </div>
    <div v-else-if="!loading" class="placeholder">
      {{ notes.length ? '没有符合筛选的笔记' : '还没有笔记，点右上角新建' }}
    </div>
  </n-spin>

  <n-drawer v-model:show="drawerOpen" :width="560" placement="right">
    <n-drawer-content :title="drawerTitle" closable>
      <template v-if="editing">
        <div class="form-row">
          <n-input
            v-model:value="form.title"
            size="small"
            placeholder="标题"
          />
        </div>
        <div class="form-row">
          <n-select
            v-model:value="form.kind"
            size="small"
            :options="KIND_OPTIONS"
            style="width: 130px"
          />
          <n-select
            v-model:value="form.status"
            size="small"
            :options="STATUS_OPTIONS"
            style="width: 130px"
          />
          <n-select
            v-model:value="form.symbol"
            size="small"
            clearable
            filterable
            :options="symbolOptions"
            placeholder="关联品种"
            style="width: 160px"
          />
        </div>
        <div class="form-row">
          <n-date-picker
            size="small"
            type="date"
            placeholder="开始日期"
            :value="dateToTs(form.start_date)"
            style="width: 150px"
            @update:value="form.start_date = tsToDate($event)"
          />
          <n-date-picker
            size="small"
            type="date"
            placeholder="结束日期"
            :value="dateToTs(form.end_date)"
            style="width: 150px"
            @update:value="form.end_date = tsToDate($event)"
          />
        </div>
        <div class="form-row">
          <n-dynamic-tags v-model:value="form.tags" size="small" />
        </div>
        <div class="form-row">
          <n-input
            v-model:value="form.content"
            type="textarea"
            :rows="14"
            placeholder="结论、依据、适用条件、失效条件……"
          />
        </div>
        <div class="form-actions">
          <n-button size="small" type="primary" :loading="saving" @click="save">
            保存
          </n-button>
          <n-button size="small" quaternary @click="cancelEdit">取消</n-button>
        </div>
      </template>

      <template v-else-if="current">
        <div class="note-meta">
          <n-tag size="small" :type="kindType(current.kind)">
            {{ KIND_LABEL[current.kind] }}
          </n-tag>
          <n-tag size="small" :type="statusType(current.status)">
            {{ STATUS_LABEL[current.status] }}
          </n-tag>
          <span v-if="current.symbol" class="hint">{{ current.symbol }}</span>
          <span v-if="dateRange(current)" class="hint">
            {{ dateRange(current) }}
          </span>
          <span v-for="tag in current.tags" :key="tag" class="note-tag">
            #{{ tag }}
          </span>
        </div>

        <div class="detail-actions">
          <n-button size="tiny" @click="startEdit">编辑</n-button>
          <n-button
            v-if="current.symbol"
            size="tiny"
            quaternary
            @click="openBacktest(current.symbol)"
          >
            查看回测
          </n-button>
          <n-popconfirm @positive-click="removeNote">
            <template #trigger>
              <n-button size="tiny" quaternary type="error">删除</n-button>
            </template>
            删除后无法恢复，确定吗？
          </n-popconfirm>
        </div>

        <div class="note-content">
          {{ current.content || '（暂无内容）' }}
        </div>

        <n-divider title-placement="left">验证记录</n-divider>
        <div class="check-list">
          <div v-for="check in current.checks" :key="check.id" class="check-row">
            <n-tag size="tiny" :type="checkType(check.result)">
              {{ CHECK_LABEL[check.result] }}
            </n-tag>
            <span class="hint">{{ check.checked_at.slice(0, 10) }}</span>
            <span class="check-comment">{{ check.comment }}</span>
          </div>
          <div v-if="!current.checks.length" class="hint">
            还没有验证记录；验证结论会同步笔记状态。
          </div>
        </div>
        <div class="check-form">
          <n-select
            v-model:value="checkResult"
            size="small"
            :options="CHECK_OPTIONS"
            style="width: 110px"
          />
          <n-input
            v-model:value="checkComment"
            size="small"
            placeholder="验证说明"
            @keyup.enter="addCheck"
          />
          <n-button size="small" @click="addCheck">记录验证</n-button>
        </div>

        <n-divider title-placement="left">回顾</n-divider>
        <div class="review-row">
          <span class="hint">
            下次回顾：{{ current.next_review ?? '—' }}（间隔
            {{ REVIEW_INTERVALS[current.review_stage] }} 天）
          </span>
          <n-button size="tiny" type="primary" ghost @click="review(true)">
            记住了
          </n-button>
          <n-button size="tiny" quaternary @click="review(false)">
            模糊
          </n-button>
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.note-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.note-card {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.note-card:hover {
  border-color: #2c4763 !important;
}

.note-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.note-main {
  min-width: 0;
}

.note-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 6px;
}

.due {
  margin-left: 8px;
  padding: 0 6px;
  border-radius: 8px;
  font-size: 11px;
  color: #0b0f14;
  background: #f0a020;
}

.note-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.note-tag {
  color: #7fb0ff;
  font-size: 12px;
}

.note-excerpt {
  margin-top: 6px;
  color: var(--text-dim);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.note-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  flex-shrink: 0;
}

.form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.form-actions,
.detail-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 0;
}

.note-content {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #0f161d;
  white-space: pre-wrap;
  line-height: 1.7;
  font-size: 13px;
}

.check-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.check-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.check-comment {
  font-size: 13px;
  white-space: pre-wrap;
  min-width: 0;
  flex: 1;
}

.check-form {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.review-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
