<script setup lang="ts">
/**
 * 多品种看板。
 * 顶部先选交易所，品种下拉只列该交易所的品种并自动全选。
 * 两种模式：
 * - 手动：默认展示用户选择的品种（首屏前 6 个）
 * - 筛选：用条件构建器添加原子条件（窗口 + 指标 + 行为 + 目标 + 聚合），
 *   多个条件按「同时满足 / 任一满足」组合，在上方所选品种范围内筛选。
 * 指标与行为的白名单统一维护在 backend services/screener.py。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NInputNumber,
  NSelect,
  NSpin,
  useMessage,
} from 'naive-ui'
import MiniChart from '../components/MiniChart.vue'
import {
  api,
  type ScreenCondition,
  type ScreenMatch,
  type ScreenMeta,
} from '../api/client'
import { useDashboardStore } from '../stores/dashboard'
import { useMarketStore } from '../stores/market'

/** 条件行在界面上的可编辑结构；提交时再转成接口需要的 target 形式。 */
interface UiCondition {
  window: number
  left: string
  behavior: string
  targetKind: 'indicator' | 'value'
  targetId: string
  targetValue: number | null
  aggregate: 'any' | 'last' | 'all'
}

const store = useMarketStore()
const dashboard = useDashboardStore()
// 交易所和品种由 dashboard store 持久化，刷新后恢复
const { exchange, selected } = storeToRefs(dashboard)
const router = useRouter()
const message = useMessage()
const loading = ref(true)

// 条件构建器
const meta = ref<ScreenMeta | null>(null)
const conditions = ref<UiCondition[]>([])
const combine = ref<'and' | 'or'>('and')

// 筛选条件和组合方式存 localStorage，刷新后还原
const SCREEN_KEY = 'maxpain.screenFilter'

function normalizeCondition(raw: unknown): UiCondition | null {
  if (!raw || typeof raw !== 'object') return null
  const item = raw as Partial<UiCondition>
  if (
    typeof item.window !== 'number' ||
    typeof item.left !== 'string' ||
    typeof item.behavior !== 'string'
  ) {
    return null
  }
  return {
    window: item.window,
    left: item.left,
    behavior: item.behavior,
    targetKind: item.targetKind === 'value' ? 'value' : 'indicator',
    targetId: typeof item.targetId === 'string' ? item.targetId : 'gpk',
    targetValue:
      typeof item.targetValue === 'number' ? item.targetValue : null,
    aggregate:
      item.aggregate === 'last' || item.aggregate === 'all'
        ? item.aggregate
        : 'any',
  }
}

function loadSavedScreen() {
  try {
    const raw = localStorage.getItem(SCREEN_KEY)
    if (!raw) return
    const saved = JSON.parse(raw) as {
      conditions?: unknown
      combine?: string
    }
    if (Array.isArray(saved.conditions)) {
      conditions.value = saved.conditions
        .map(normalizeCondition)
        .filter((item): item is UiCondition => item !== null)
    }
    if (saved.combine === 'and' || saved.combine === 'or') {
      combine.value = saved.combine
    }
  } catch {
    // 本地数据损坏时按无筛选启动
  }
}

loadSavedScreen()

// 筛选结果
const matches = ref<ScreenMatch[]>([])
const screening = ref(false)
// 快速改动条件时只采用最后一次请求的结果
let screenSeq = 0
let screenTimer: ReturnType<typeof setTimeout> | undefined

// 交易所代码 → 中文名
const EXCHANGE_LABELS: Record<string, string> = {
  dce: '大商所',
  czce: '郑商所',
  shfe: '上期所',
  ine: '能源中心',
  cffex: '中金所',
  gfex: '广期所',
}

// 交易所筛选：先选交易所，品种下拉只列该交易所的品种
const exchangeOptions = computed(() => {
  const codes = Array.from(
    new Set(store.symbols.map((item) => item.exchange).filter(Boolean)),
  )
  return [
    { label: '全部交易所', value: 'all' },
    ...codes.map((code) => ({ label: EXCHANGE_LABELS[code] ?? code, value: code })),
  ]
})

const scopedSymbols = computed(() =>
  exchange.value === 'all'
    ? store.symbols
    : store.symbols.filter((item) => item.exchange === exchange.value),
)

// 下拉选项：代码 + 中文名
const options = computed(() =>
  scopedSymbols.value.map((item) => ({
    label: `${item.symbol} ${item.name}`,
    value: item.symbol,
  })),
)

// 卡片标题里按代码查中文名
const nameMap = computed(() =>
  Object.fromEntries(store.symbols.map((item) => [item.symbol, item.name])),
)

// 品种 -> 命中说明，用于卡片右上角展示触发依据
const matchBySymbol = computed(() =>
  Object.fromEntries(matches.value.map((item) => [item.symbol, item])),
)

// 各卡片最新收盘价，由 MiniChart 拉完日线后回传，显示在品种名称后面
const priceMap = ref<Record<string, number>>({})

function onLatestPrice(symbol: string, price: number) {
  priceMap.value[symbol] = price
}

const filtering = computed(() => conditions.value.length > 0)
const visibleSymbols = computed(() =>
  filtering.value ? matches.value.map((item) => item.symbol) : selected.value,
)

const windowOptions = computed(() =>
  (meta.value?.windows ?? []).map((days) => ({
    label: `近${days}日`,
    value: days,
  })),
)

const indicatorOptions = computed(() =>
  (meta.value?.indicators ?? []).map((item) => ({
    label: item.label,
    value: item.id,
  })),
)

// 目标只能选单值指标（价格区间不能作为对比目标）
const targetOptions = computed(() =>
  (meta.value?.indicators ?? [])
    .filter((item) => item.kind === 'value')
    .map((item) => ({ label: item.label, value: item.id })),
)

const aggregateOptions = computed(() =>
  (meta.value?.aggregates ?? []).map((item) => ({
    label: item.label,
    value: item.id,
  })),
)

const combineOptions = [
  { label: '同时满足', value: 'and' },
  { label: '任一满足', value: 'or' },
]

const targetKindOptions = [
  { label: '对比指标', value: 'indicator' },
  { label: '固定值', value: 'value' },
]

/** 行为选项随左指标类型变化（价格区间 / 单值指标）。 */
function behaviorOptions(left: string) {
  const kind =
    meta.value?.indicators.find((item) => item.id === left)?.kind ?? 'value'
  return (meta.value?.behaviors ?? [])
    .filter((item) => item.kinds.includes(kind))
    .map((item) => ({ label: item.label, value: item.id }))
}

function defaultCondition(): UiCondition {
  return {
    window: 3,
    left: 'price',
    behavior: 'break',
    targetKind: 'indicator',
    targetId: 'gpk',
    targetValue: null,
    aggregate: 'any',
  }
}

function addCondition() {
  conditions.value.push(defaultCondition())
}

function removeCondition(index: number) {
  conditions.value.splice(index, 1)
}

function clearConditions() {
  conditions.value = []
}

/** 切换左指标后，如果当前行为不适用就换成该类型的第一个行为。 */
function onLeftChange(cond: UiCondition) {
  const valid = behaviorOptions(cond.left).some(
    (option) => option.value === cond.behavior,
  )
  if (!valid) {
    cond.behavior = behaviorOptions(cond.left)[0]?.value ?? 'above'
  }
}

function onTargetKindChange(cond: UiCondition) {
  if (cond.targetKind === 'value' && cond.targetValue == null) {
    cond.targetValue = 0
  }
}

function toCondition(cond: UiCondition): ScreenCondition {
  return {
    window: cond.window,
    left: cond.left,
    behavior: cond.behavior,
    target:
      cond.targetKind === 'value'
        ? { type: 'value', value: cond.targetValue ?? 0 }
        : { type: 'indicator', value: cond.targetId },
    aggregate: cond.aggregate,
  }
}

async function runScreen() {
  const seq = ++screenSeq
  screening.value = true
  try {
    const result = await api.screen({
      conditions: conditions.value.map(toCondition),
      combine: combine.value,
      symbols: selected.value,
    })
    if (seq === screenSeq) matches.value = result
  } catch (error) {
    if (seq === screenSeq) matches.value = []
    message.error(error instanceof Error ? error.message : '筛选失败')
  } finally {
    if (seq === screenSeq) screening.value = false
  }
}

// 条件或品种范围改动后防抖再请求，避免连点下拉框时重复计算
function scheduleScreen() {
  if (screenTimer) clearTimeout(screenTimer)
  screenSeq += 1
  // 没有条件时回到手动模式；筛选模式下没选品种就没有可筛范围
  if (!conditions.value.length || !selected.value.length) {
    matches.value = []
    screening.value = false
    return
  }
  screening.value = true
  screenTimer = setTimeout(() => {
    void runScreen()
  }, 250)
}

watch([conditions, combine], scheduleScreen, { deep: true })
watch(selected, () => {
  if (filtering.value) scheduleScreen()
})

// 条件变化同步到 localStorage；清空筛选时删掉记录
watch(
  [conditions, combine],
  () => {
    if (!conditions.value.length) {
      localStorage.removeItem(SCREEN_KEY)
      return
    }
    localStorage.setItem(
      SCREEN_KEY,
      JSON.stringify({
        conditions: conditions.value,
        combine: combine.value,
      }),
    )
  },
  { deep: true },
)

// 切换交易所后直接选中该所全部品种；切回「全部交易所」时保留当前已选
watch(exchange, () => {
  if (exchange.value === 'all') return
  selected.value = scopedSymbols.value.map((item) => item.symbol)
})

function cardNote(symbol: string) {
  return filtering.value ? matchBySymbol.value[symbol]?.note : undefined
}

/** 点卡片进回测页，品种通过全局状态传给回测页。 */
function openBacktest(symbol: string) {
  store.selectedSymbol = symbol
  router.push({ name: 'backtest' })
}

onMounted(async () => {
  try {
    const [symbols, screenMeta] = await Promise.all([
      store.ensureSymbols(),
      api.screenMeta(),
    ])
    meta.value = screenMeta
    // 恢复上次的交易所和品种；数据里已不存在的先剔除
    const codes = new Set(
      symbols.map((item) => item.exchange).filter(Boolean),
    )
    if (exchange.value !== 'all' && !codes.has(exchange.value)) {
      exchange.value = 'all'
    }
    const allowed = new Set(
      scopedSymbols.value.map((item) => item.symbol),
    )
    selected.value = selected.value.filter((sym) => allowed.has(sym))
    // 没有已选品种时默认前 6 个，避免一次请求太多
    if (!selected.value.length) {
      selected.value = scopedSymbols.value
        .slice(0, 6)
        .map((item) => item.symbol)
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载看板数据失败')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="toolbar">
    <n-select
      v-model:value="exchange"
      size="small"
      :options="exchangeOptions"
      style="width: 130px"
    />
    <n-select
      v-model:value="selected"
      multiple
      filterable
      clearable
      size="small"
      :options="options"
      :max-tag-count="6"
      style="min-width: 300px; width: calc(100% - 142px)"
      placeholder="选择品种"
    />
    <n-button
      v-if="!filtering"
      size="small"
      type="primary"
      ghost
      @click="addCondition"
    >
      + 筛选条件
    </n-button>
    <template v-else>
      <n-select
        v-if="conditions.length > 1"
        v-model:value="combine"
        size="small"
        :options="combineOptions"
        style="width: 110px"
      />
      <n-button size="small" @click="addCondition">+ 条件</n-button>
      <n-button size="small" quaternary @click="clearConditions">
        清空筛选
      </n-button>
    </template>
    <div class="toolbar-meta">
      <span class="metric">
        {{ filtering ? matches.length : selected.length }}
      </span>
      <span class="hint">
        {{
          filtering
            ? '个品种符合条件'
            : `/ ${store.symbols.length} 个品种`
        }}
      </span>
    </div>
    <span class="hint">
      {{ filtering ? '在上方所选品种范围内筛选' : '图表后续接入信号标记与止损线' }}
    </span>
  </div>

  <div v-if="conditions.length" class="condition-panel">
    <div
      v-for="(cond, index) in conditions"
      :key="index"
      class="condition-row"
    >
      <span class="condition-index">{{ index + 1 }}</span>
      <n-select
        v-model:value="cond.window"
        size="small"
        :options="windowOptions"
        style="width: 96px"
      />
      <n-select
        v-model:value="cond.left"
        size="small"
        :options="indicatorOptions"
        style="width: 120px"
        @update:value="onLeftChange(cond)"
      />
      <n-select
        v-model:value="cond.behavior"
        size="small"
        :options="behaviorOptions(cond.left)"
        style="width: 180px"
      />
      <n-select
        v-model:value="cond.targetKind"
        size="small"
        :options="targetKindOptions"
        style="width: 100px"
        @update:value="onTargetKindChange(cond)"
      />
      <n-select
        v-if="cond.targetKind === 'indicator'"
        v-model:value="cond.targetId"
        size="small"
        :options="targetOptions"
        style="width: 120px"
      />
      <n-input-number
        v-else
        v-model:value="cond.targetValue"
        size="small"
        :show-button="false"
        placeholder="数值"
        style="width: 110px"
      />
      <n-select
        v-model:value="cond.aggregate"
        size="small"
        :options="aggregateOptions"
        style="width: 100px"
      />
      <n-button size="tiny" quaternary @click="removeCondition(index)">
        删除
      </n-button>
    </div>
    <div class="condition-footer">
      <n-button size="tiny" dashed @click="addCondition">+ 添加条件</n-button>
      <span class="hint">
        多个条件{{ combine === 'and' ? '同时满足' : '任一满足' }}
      </span>
    </div>
  </div>

  <n-spin :show="loading || screening">
    <div v-if="visibleSymbols.length" class="grid">
      <n-card
        v-for="symbol in visibleSymbols"
        :key="symbol"
        size="small"
        class="market-card"
        @click="openBacktest(symbol)"
      >
        <template #header>
          <div class="card-title">
            <span class="symbol">{{ symbol }}</span>
            <span class="hint">{{ nameMap[symbol] }}</span>
            <span class="price">{{ priceMap[symbol]?.toFixed(0) }}</span>
          </div>
        </template>
        <template #header-extra>
          <span class="hint note" :title="cardNote(symbol)">
            {{ cardNote(symbol) ?? '日线 · 90' }}
          </span>
        </template>
        <mini-chart
          :symbol="symbol"
          @latest="onLatestPrice(symbol, $event)"
        />
      </n-card>
    </div>
    <div v-else-if="!loading && !screening" class="placeholder">
      {{
        filtering
          ? selected.length
            ? '范围内没有符合条件的品种'
            : '请先在上方选择品种范围'
          : '暂无品种'
      }}
    </div>
  </n-spin>
</template>

<style scoped>
.market-card {
  cursor: pointer;
  transition: border-color 0.15s ease, transform 0.15s ease;
}

.market-card:hover {
  border-color: #2c4763 !important;
  transform: translateY(-1px);
}

.market-card :deep(.n-card__content) {
  padding: 8px 10px 10px;
}

.card-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.price {
  font-size: 12px;
  color: #e5e7eb;
  font-variant-numeric: tabular-nums;
}

/* 筛选说明可能较长，单行省略避免撑开卡片标题 */
.note {
  display: inline-block;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.condition-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #0f161d;
}

.condition-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.condition-index {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  color: #0b0f14;
  background: #4c8dff;
}

.condition-footer {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
