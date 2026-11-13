<script setup lang="ts">
/**
 * 期权到期日历。
 * 数据来自 /api/expiries（build_web_data.py 生成的 web/data/expiries.json）：
 * 每个品种记录历史至今出现过的「最近到期日」，近月合约逐日滚动，
 * 所以最新一条就是该品种的下一个到期日。
 * 月份切换用 Naive UI 自带的 NCalendar，单元格里直接标出当天到期的品种。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCalendar, NCard, NSpin, NTag, useMessage } from 'naive-ui'
import { api, type ExpiryEvent } from '../api/client'
import { useMarketStore } from '../stores/market'

const message = useMessage()
const router = useRouter()
const store = useMarketStore()

const items = ref<ExpiryEvent[]>([])
const loading = ref(true)
// 选中日期（毫秒时间戳），默认今天
const selectedTs = ref(Date.now())

// 交易所简称和配色，日历色块与图例共用
const EXCHANGES: Record<string, { label: string; color: string }> = {
  dce: { label: '大商所', color: '#67c23a' },
  czce: { label: '郑商所', color: '#e6a23c' },
  shfe: { label: '上期所', color: '#4c8dff' },
  ine: { label: '能源中心', color: '#b37feb' },
  cffex: { label: '中金所', color: '#ff7875' },
  gfex: { label: '广期所', color: '#36cfc9' },
}

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function pad2(value: number) {
  return String(value).padStart(2, '0')
}

/** 把年月日拼成 YYYY-MM-DD，避免 Date.toISOString 的时区偏移。 */
function makeKey(year: number, month: number, date: number) {
  return `${year}-${pad2(month)}-${pad2(date)}`
}

/** 毫秒时间戳 → 本地日期键。 */
function keyOf(ts: number) {
  const d = new Date(ts)
  return makeKey(d.getFullYear(), d.getMonth() + 1, d.getDate())
}

// 按到期日分组，日历单元格和右侧明细都查这一份
const byDate = computed(() => {
  const map = new Map<string, ExpiryEvent[]>()
  for (const item of items.value) {
    const list = map.get(item.expiry)
    if (list) list.push(item)
    else map.set(item.expiry, [item])
  }
  for (const list of map.values()) list.sort((a, b) => a.symbol.localeCompare(b.symbol))
  return map
})

function eventsOn(year: number, month: number, date: number) {
  return byDate.value.get(makeKey(year, month, date)) ?? []
}

const todayKey = keyOf(Date.now())

// 未来还没到期的记录数（历史记录也保留在日历里，方便回看）
const upcomingCount = computed(
  () => items.value.filter((item) => item.expiry >= todayKey).length,
)

const selectedDate = computed(() => new Date(selectedTs.value))
const selectedKey = computed(() => keyOf(selectedTs.value))
const selectedEvents = computed(() => byDate.value.get(selectedKey.value) ?? [])
const selectedTitle = computed(() => {
  const d = selectedDate.value
  return `${d.getMonth() + 1}月${d.getDate()}日 ${WEEKDAYS[d.getDay()]}`
})

/** 距今天数：负数表示已经到期。 */
function daysFromToday(expiry: string) {
  const [year, month, date] = expiry.split('-').map(Number)
  const target = new Date(year, month - 1, date)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((target.getTime() - today.getTime()) / 86400000)
}

function daysText(expiry: string) {
  const days = daysFromToday(expiry)
  if (days === 0) return '今天到期'
  if (days > 0) return `${days} 天后`
  return `已到期 ${-days} 天`
}

function exchangeLabel(exchange: string) {
  return EXCHANGES[exchange]?.label ?? exchange
}

function exchangeColor(exchange: string) {
  return EXCHANGES[exchange]?.color ?? '#8b97a6'
}

function tagType(exchange: string): 'success' | 'warning' | 'info' | 'default' {
  if (exchange === 'dce') return 'success'
  if (exchange === 'czce') return 'warning'
  if (exchange === 'shfe') return 'info'
  return 'default'
}

/** 点击品种跳到回测页，复用回测页记住的选中品种。 */
function openBacktest(symbol: string) {
  store.selectedSymbol = symbol
  void router.push({ name: 'backtest' })
}

async function load() {
  loading.value = true
  try {
    items.value = await api.expiries()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载到期日历失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="toolbar">
    <div class="toolbar-meta">
      <span class="metric">{{ items.length }}</span>
      <span class="hint">条到期记录 · 待到期 {{ upcomingCount }}</span>
    </div>
    <div class="legend">
      <span v-for="(info, key) in EXCHANGES" :key="key" class="legend-item">
        <i class="legend-dot" :style="{ background: info.color }" />
        <span class="hint">{{ info.label }}</span>
      </span>
    </div>
    <span class="hint">最新交易日之后按近月合约滚动，数据更新后自动顺延</span>
  </div>

  <n-spin :show="loading">
    <div class="calendar-layout">
      <div class="calendar-panel">
        <n-calendar v-model:value="selectedTs">
          <template #default="{ year, month, date }">
            <div v-if="eventsOn(year, month, date).length" class="day-events">
              <span
                v-for="event in eventsOn(year, month, date).slice(0, 4)"
                :key="event.symbol"
                class="day-chip"
                :style="{
                  color: exchangeColor(event.exchange),
                  borderColor: exchangeColor(event.exchange),
                }"
              >
                {{ event.symbol }}
              </span>
              <span
                v-if="eventsOn(year, month, date).length > 4"
                class="day-more"
              >
                +{{ eventsOn(year, month, date).length - 4 }}
              </span>
            </div>
          </template>
        </n-calendar>
      </div>

      <n-card size="small" class="day-panel">
        <template #header>
          <div class="card-title">
            <span class="symbol">{{ selectedTitle }}</span>
            <span class="hint">到期 {{ selectedEvents.length }}</span>
          </div>
        </template>

        <div v-if="selectedEvents.length" class="event-list">
          <div
            v-for="event in selectedEvents"
            :key="event.symbol"
            class="event-row"
            title="查看该品种回测"
            @click="openBacktest(event.symbol)"
          >
            <div class="event-main">
              <span class="event-symbol">{{ event.symbol }}</span>
              <span class="hint">{{ event.name }}</span>
            </div>
            <div class="event-meta">
              <n-tag size="tiny" :type="tagType(event.exchange)">
                {{ exchangeLabel(event.exchange) }}
              </n-tag>
              <span class="contract">{{ event.contract ?? '-' }}</span>
              <span
                class="days"
                :class="{ past: daysFromToday(event.expiry) < 0 }"
              >
                {{ daysText(event.expiry) }}
              </span>
            </div>
          </div>
        </div>
        <div v-else class="placeholder">当日无期权到期</div>
      </n-card>
    </div>
  </n-spin>
</template>

<style scoped>
.legend {
  display: flex;
  align-items: center;
  gap: 12px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 5px;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.calendar-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 16px;
  align-items: start;
}

.calendar-panel {
  min-width: 0;
}

/* 单元格默认内边距 10px，收窄一点给品种标签留空间 */
.calendar-panel :deep(.n-calendar-cell) {
  padding: 6px 8px;
}

/* 今天的格子铺一层浅白透明背景，日期数字保持原样 */
.calendar-panel :deep(.n-calendar-cell--current),
.calendar-panel :deep(.n-calendar-cell--current:hover) {
  background-color: rgba(255, 255, 255, 0.07);
}

.day-events {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-top: 4px;
}

.day-chip {
  padding: 0 4px;
  border: 1px solid;
  border-radius: 3px;
  font-size: 10px;
  line-height: 15px;
  font-weight: 600;
}

.day-more {
  color: var(--text-dim);
  font-size: 10px;
  line-height: 15px;
}

.day-panel {
  position: sticky;
  top: 0;
}

.event-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.event-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #0f161d;
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.event-row:hover {
  border-color: #2c4763;
}

.event-main {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.event-symbol {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.4px;
}

.event-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.contract {
  color: #b7c2cf;
  font-size: 11px;
}

.days {
  margin-left: auto;
  color: #7fb0ff;
  font-size: 11px;
}

.days.past {
  color: var(--text-dim);
}

@media (max-width: 1100px) {
  .calendar-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .day-panel {
    position: static;
  }
}
</style>
