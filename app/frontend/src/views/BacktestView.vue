<script setup lang="ts">
/**
 * 回测工作区。
 * 数据由后端逐日撮合，和每日扫描共用同一份策略核心；
 * 参数优先取“策略参数”页保存的优化值，这里负责选品种/区间、
 * 复盘图表、实际参数展示、参数网格优化和蒙特卡洛重采样。
 */
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NInputNumber,
  NSelect,
  NSpin,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import BacktestChart from '../components/BacktestChart.vue'
import {
  api,
  type BacktestResult,
  type BacktestTrade,
  type OptimizeResult,
  type OptimizeRow,
} from '../api/client'
import { useMarketStore } from '../stores/market'

const STRATEGY = 'maxpain_v5'

const store = useMarketStore()
const message = useMessage()
// 选中的品种存 store（并落 localStorage），刷新后沿用
const symbol = computed({
  get: () => store.selectedSymbol,
  set: (value: string) => {
    store.selectedSymbol = value
  },
})
// daterange 模式下实际是 [start, end] 时间戳，类型仍按 Naive UI 的联合类型收窄
const dateRange = ref<number | [number, number] | null>(null)
const loading = ref(false)
const result = ref<BacktestResult | null>(null)

// 信息栏算浮动盈亏要用合约乘数，从品种列表里取
const resultMult = computed(
  () =>
    store.symbols.find((item) => item.symbol === result.value?.symbol)?.mult ??
    1,
)

const symbolOptions = computed(() =>
  store.symbols.map((item) => ({
    label: `${item.symbol} ${item.name}`,
    value: item.symbol,
  })),
)

/** 日期选择器返回毫秒时间戳，转成后端要的 YYYY-MM-DD。 */
function toDateString(ms: number) {
  const d = new Date(ms)
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${month}-${day}`
}

function fmtNumber(value: number) {
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

// 汇总指标平铺成小卡片，和参数页的分组风格保持一致
const metrics = computed(() => {
  const summary = result.value?.summary
  if (!summary) return []
  return [
    { label: '期末权益', value: fmtNumber(summary.end_equity), tone: '' },
    {
      label: '总收益',
      value: `${summary.total_return}%`,
      tone: summary.total_return >= 0 ? 'pos' : 'neg',
    },
    { label: '最大回撤', value: `${summary.max_dd}%`, tone: 'neg' },
    { label: '交易次数', value: String(summary.trades), tone: '' },
    { label: '胜率', value: `${summary.win_rate}%`, tone: '' },
    {
      label: '盈亏比',
      value:
        summary.profit_factor == null ? '∞' : String(summary.profit_factor),
      tone: '',
    },
    { label: '平均持仓', value: `${summary.avg_hold}天`, tone: '' },
    { label: '持仓覆盖率', value: `${summary.coverage}%`, tone: '' },
    {
      label: '单笔均值',
      value: fmtNumber(summary.avg_pnl),
      tone: summary.avg_pnl >= 0 ? 'pos' : 'neg',
    },
  ]
})

const accuracyText = computed(() => {
  const accuracy = result.value?.dir_accuracy
  if (!accuracy) return ''
  return Object.entries(accuracy)
    .map(([days, value]) => `${days}天 ${value == null ? '-' : `${value}%`}`)
    .join(' | ')
})

// 复盘图衍生线的显示带宽（百分数），后端默认 20
const band = computed(() => Number(result.value?.params.band ?? 20))

/* ---- 本次生效参数展示 ---- */

interface ParamMeta {
  key: string
  label: string
  pct?: boolean
  switch?: boolean
  mode?: boolean
  /** 输入框步长（显示单位） */
  step?: number
}

// 和后端策略参数一一对应，单位与“策略参数”页一致
const PARAM_ITEMS: ParamMeta[] = [
  { key: 'lookback', label: '趋势窗口', step: 1 },
  { key: 'minPct', label: '趋势阈值(%)', pct: true, step: 0.1 },
  { key: 'maxPos', label: '最大仓位(%)', pct: true, step: 1 },
  { key: 'margin', label: '保证金率(%)', pct: true, step: 1 },
  { key: 'capital', label: '初始权益(元)', step: 10000 },
  { key: 'capLimit', label: '单笔上限(万元)', step: 10 },
  { key: 'beTh', label: '保本激活(%)', pct: true, step: 0.1 },
  { key: 'lockPct', label: '锁定利润(%)', pct: true, step: 1 },
  { key: 'atrPeriod', label: 'ATR 周期', step: 1 },
  { key: 'entryAtrMult', label: '入场止损 ATR×', step: 0.1 },
  { key: 'atrMult', label: '追踪止损 ATR×', step: 0.1 },
  { key: 'skipCount', label: '盈利后跳过', step: 1 },
  { key: 'volFilterLow', label: '波动分位下限(%)', pct: true, step: 1 },
  { key: 'volFilterHigh', label: '波动分位上限(%)', pct: true, step: 1 },
  { key: 'volWindow', label: '分位窗口', step: 10 },
  { key: 'volTarget', label: '波动率目标化', switch: true },
  { key: 'volTargetCap', label: '仓位缩放上限', step: 0.5 },
  { key: 'ewmaFilterHigh', label: 'EWMA 比值上限', step: 0.1 },
  { key: 'ewmaFilterLow', label: 'EWMA 比值下限', step: 0.1 },
  { key: 'priceSlopeTh', label: '价格斜率阈值', step: 0.001 },
  { key: 'slopeDays', label: '斜率窗口', step: 5 },
  { key: 'pbSlopeTh', label: '回踩挂单阈值(%)', step: 0.5 },
  { key: 'accWindow', label: '准确率窗口', step: 1 },
  { key: 'accStreak', label: '连续告警天数', step: 1 },
  { key: 'accMode', label: '准确率门控', mode: true },
  { key: 'band', label: '衍生线带宽(%)', step: 1 },
]

function trim(value: number, digits = 2) {
  return String(Number(value.toFixed(digits)))
}

const ACC_MODE_OPTIONS = [
  { label: '正常', value: 'off' },
  { label: '拦截', value: 'skip' },
  { label: '反向', value: 'rev' },
]

const SWITCH_OPTIONS = [
  { label: '关闭', value: 0 },
  { label: '开启', value: 1 },
]

// 微调输入框：数字字段存显示值（百分比字段为 %），null 表示清空后按保存值处理
const editNums = ref<Record<string, number | null>>({})
const editMode = ref('')
const savingParams = ref(false)

/** 原始值 -> 输入框显示值。 */
function toDisplayParam(meta: ParamMeta, raw: unknown) {
  if (typeof raw !== 'number') return null
  return meta.pct ? Number((raw * 100).toFixed(4)) : raw
}

/** 用后端返回的实际参数初始化输入框；手动改过的字段保留。 */
function fillEdits(params: Record<string, unknown>) {
  for (const meta of PARAM_ITEMS) {
    if (meta.mode) continue
    // 已填写的值保留；清空的输入框回填本次实际生效值
    if (editNums.value[meta.key] != null) continue
    editNums.value[meta.key] = toDisplayParam(meta, params[meta.key])
  }
  if (!editMode.value) {
    editMode.value =
      typeof params.accMode === 'string' ? params.accMode : 'off'
  }
}

/** 输入框显示值还原成后端原始值；清空的字段不提交。 */
function buildOverrides() {
  const overrides: Record<string, number | string> = {}
  for (const meta of PARAM_ITEMS) {
    if (meta.mode) {
      if (editMode.value) overrides[meta.key] = editMode.value
      continue
    }
    const value = editNums.value[meta.key]
    if (value == null) continue
    overrides[meta.key] = meta.pct ? value / 100 : value
  }
  return overrides
}

/** 把顶部表单当前值写回 strategy_params；运行回测不会自动保存。 */
async function saveParams() {
  if (!symbol.value) return
  savingParams.value = true
  try {
    await api.saveParams(STRATEGY, symbol.value, buildOverrides())
    message.success(`${symbol.value} 参数已保存`)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存参数失败')
  } finally {
    savingParams.value = false
  }
}

/* ---- 参数网格优化 ---- */

interface OptDef {
  key: string
  label: string
  min: number
  max: number
  step: number
  pct?: boolean // 表单按百分数，提交前转小数
  digits?: number
}

// 范围取自旧页面 OPT_PARAMS，保持两边优化口径一致
const OPT_DEFS: OptDef[] = [
  { key: 'lookback', label: 'Lookback', min: 5, max: 60, step: 1 },
  { key: 'minPct', label: 'Min%', min: 0.1, max: 2, step: 0.1, pct: true },
  { key: 'maxPos', label: '仓位%', min: 5, max: 100, step: 5, pct: true },
  { key: 'beTh', label: '保本%', min: 0.5, max: 2, step: 0.1, pct: true },
  { key: 'lockPct', label: '锁定%', min: 10, max: 75, step: 5, pct: true },
  { key: 'atrPeriod', label: 'ATR周期', min: 5, max: 120, step: 1 },
  { key: 'entryAtrMult', label: '入场ATR倍', min: 0.3, max: 2, step: 0.1 },
  { key: 'atrMult', label: '追踪ATR倍', min: 0.3, max: 2, step: 0.1 },
  { key: 'skipCount', label: '跳过次', min: 0, max: 5, step: 1 },
  { key: 'volWindow', label: '分位窗口', min: 0, max: 200, step: 10 },
  { key: 'priceSlopeTh', label: '逆势挡', min: 0, max: 0.01, step: 0.001, digits: 3 },
  { key: 'slopeDays', label: '逆势窗', min: 10, max: 60, step: 5 },
  { key: 'pbSlopeTh', label: '回踩斜率%', min: 0, max: 3, step: 0.5 },
  { key: 'ewmaFilterHigh', label: 'EWMA挡高', min: 0, max: 2, step: 0.1 },
  { key: 'ewmaFilterLow', label: 'EWMA挡低', min: 0, max: 1, step: 0.1 },
]

const OPT_MAX_COMBOS = 100000
const optSelection = ref<string[]>(['lookback'])
const optimizing = ref(false)
const optResult = ref<OptimizeResult | null>(null)
const applying = ref(false)

function buildValues(def: OptDef) {
  const values: number[] = []
  for (
    let value = def.min;
    value <= def.max + 1e-10;
    value = Math.round((value + def.step) * 1000) / 1000
  ) {
    values.push(value)
  }
  return values
}

function optValues(def: OptDef) {
  const values = buildValues(def)
  return `${values[0]}~${values[values.length - 1]} x${values.length}`
}

// 多选下拉选项，直接把候选范围写进选项文字
const optOptions = OPT_DEFS.map((def) => ({
  label: `${def.label}（${optValues(def)}）`,
  value: def.key,
}))

const optCombos = computed(() => {
  if (!optSelection.value.length) return 0
  return optSelection.value.reduce((total, key) => {
    const def = OPT_DEFS.find((item) => item.key === key)
    return def ? total * buildValues(def).length : total
  }, 1)
})

async function runOptimize() {
  if (!symbol.value) return
  if (!optSelection.value.length) {
    message.warning('请先选择要优化的参数')
    return
  }
  if (optCombos.value > OPT_MAX_COMBOS) {
    message.error(
      `组合数 ${optCombos.value} 超过上限 ${OPT_MAX_COMBOS}，请减少优化参数`,
    )
    return
  }
  optimizing.value = true
  optResult.value = null
  try {
    const range = Array.isArray(dateRange.value) ? dateRange.value : null
    const grid: Record<string, number[]> = {}
    for (const key of optSelection.value) {
      const def = OPT_DEFS.find((item) => item.key === key)
      if (!def) continue
      grid[key] = buildValues(def).map((value) =>
        def.pct ? value / 100 : value,
      )
    }
    const response = await api.optimize(symbol.value, {
      start: range?.[0] ? toDateString(range[0]) : undefined,
      end: range?.[1] ? toDateString(range[1]) : undefined,
      grid,
    })
    optResult.value = response
    if (!response.results.length) {
      message.warning('所有组合均无交易')
    } else {
      message.success(
        `扫描 ${response.combos} 组，有交易 ${response.tested} 组，显示前 ${response.results.length} 组`,
      )
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : '优化失败')
  } finally {
    optimizing.value = false
  }
}

function formatOptValue(def: OptDef, value: number) {
  if (def.pct) return `${trim(value * 100, 2)}%`
  if (def.digits) return value.toFixed(def.digits)
  return String(value)
}

const optColumns = computed<DataTableColumns<OptimizeRow>>(() => {
  const selected = OPT_DEFS.filter((def) =>
    optSelection.value.includes(def.key),
  )
  const valueColumns: DataTableColumns<OptimizeRow> = selected.map((def) => ({
    title: def.label,
    key: `opt_${def.key}`,
    width: 92,
    render: (row) => formatOptValue(def, row.values[def.key]),
  }))
  return [
    ...valueColumns,
    {
      title: '最终权益',
      key: 'end_equity',
      width: 110,
      render: (row) => fmtNumber(row.end_equity),
    },
    {
      title: '收益',
      key: 'total_return',
      width: 80,
      render: (row) => `${row.total_return}%`,
    },
    { title: '笔数', key: 'trades', width: 64 },
    {
      title: '覆盖率',
      key: 'coverage',
      width: 82,
      render: (row) => `${row.coverage}%`,
    },
    {
      title: '胜率',
      key: 'win_rate',
      width: 72,
      render: (row) => `${row.win_rate}%`,
    },
    {
      title: '盈亏比',
      key: 'profit_factor',
      width: 78,
      render: (row) =>
        row.profit_factor == null ? '∞' : String(row.profit_factor),
    },
    {
      title: '回撤',
      key: 'max_dd',
      width: 72,
      render: (row) => `${row.max_dd}%`,
    },
    {
      title: '均PnL',
      key: 'avg_pnl',
      width: 92,
      render: (row) => fmtNumber(row.avg_pnl),
    },
  ]
})

function optRowClass(row: OptimizeRow) {
  return row === optResult.value?.results[0] ? 'best-row' : ''
}

/** 把最优组合的参数填进顶部表单，不保存、不运行，由用户决定测试或保存。 */
function fillBest() {
  const best = optResult.value?.results[0]
  if (!best) return
  for (const meta of PARAM_ITEMS) {
    if (meta.mode) continue
    const raw = best.values[meta.key]
    if (raw == null) continue
    editNums.value[meta.key] = toDisplayParam(meta, raw)
  }
  message.info('已填充最优参数，可运行回测或保存参数')
}

async function applyBest() {
  const best = optResult.value?.results[0]
  if (!best || !symbol.value) return
  applying.value = true
  try {
    // 只提交被优化的字段，PUT 会与已有参数合并
    await api.saveParams(STRATEGY, symbol.value, best.values)
    // 清掉微调，避免旧覆盖值盖住刚保存的最优参数
    editNums.value = {}
    editMode.value = ''
    message.success('最优参数已保存，正在重新回测')
    await run()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存参数失败')
  } finally {
    applying.value = false
  }
}

/* ---- 蒙特卡洛：对交易盈亏做有放回重采样 ---- */

interface MonteCarloBin {
  from: number
  to: number
  count: number
  actual: boolean
}

interface MonteCarloResult {
  n: number
  actual: number
  percentile: number
  median: number
  mean: number
  p05: number
  p95: number
  maxCount: number
  bins: MonteCarloBin[]
}

const mcRunning = ref(false)
const mcResult = ref<MonteCarloResult | null>(null)

function runMonteCarlo() {
  const trades = result.value?.trades
  if (!trades || trades.length < 10) {
    message.warning('交易笔数不足（至少 10 笔）')
    return
  }
  mcRunning.value = true
  // 延迟一帧，让按钮先进入 loading 状态
  window.setTimeout(() => {
    const pnls = trades.map((trade) => trade.pnl)
    const total = pnls.reduce((sum, value) => sum + value, 0)
    const n = 10000
    const outcomes = new Float64Array(n)
    for (let iter = 0; iter < n; iter += 1) {
      let sum = 0
      for (let i = 0; i < pnls.length; i += 1) {
        sum += pnls[Math.floor(Math.random() * pnls.length)]
      }
      outcomes[iter] = sum
    }
    outcomes.sort()

    let rank = 0
    while (rank < n && outcomes[rank] < total) rank += 1

    const minValue = outcomes[0]
    const maxValue = outcomes[n - 1]
    const binsCount = 40
    const scale = maxValue - minValue || 1
    const bucket = new Int32Array(binsCount)
    for (const value of outcomes) {
      const bin = Math.min(
        binsCount - 1,
        Math.floor(((value - minValue) / scale) * binsCount),
      )
      bucket[bin] += 1
    }
    const actualBin = Math.min(
      binsCount - 1,
      Math.floor(((total - minValue) / scale) * binsCount),
    )

    let firstBin = 0
    let lastBin = binsCount - 1
    while (firstBin < binsCount && bucket[firstBin] === 0) firstBin += 1
    while (lastBin >= 0 && bucket[lastBin] === 0) lastBin -= 1

    const binWidth = scale / binsCount
    const bins: MonteCarloBin[] = []
    let maxCount = 1
    for (let bin = firstBin; bin <= lastBin; bin += 1) {
      bins.push({
        from: Math.round(minValue + bin * binWidth),
        to: Math.round(minValue + (bin + 1) * binWidth),
        count: bucket[bin],
        actual: bin === actualBin,
      })
      maxCount = Math.max(maxCount, bucket[bin])
    }

    let sumOutcomes = 0
    for (const value of outcomes) sumOutcomes += value

    mcResult.value = {
      n,
      actual: Math.round(total),
      percentile: Number(((rank / n) * 100).toFixed(1)),
      median: Math.round(outcomes[n >> 1]),
      mean: Math.round(sumOutcomes / n),
      p05: Math.round(outcomes[Math.floor(n * 0.05)]),
      p95: Math.round(outcomes[Math.floor(n * 0.95)]),
      maxCount,
      bins,
    }
    mcRunning.value = false
  }, 30)
}

// 交易明细：盈亏用颜色区分，出场原因用标签展示
const columns: DataTableColumns<BacktestTrade> = [
  { title: '开仓日', key: 'entry_date', width: 110 },
  { title: '平仓日', key: 'exit_date', width: 110 },
  {
    title: '方向',
    key: 'direction',
    width: 72,
    render: (row) =>
      h(
        NTag,
        {
          size: 'small',
          type: row.direction === 'long' ? 'success' : 'error',
        },
        { default: () => (row.direction === 'long' ? '多' : '空') },
      ),
  },
  { title: '开仓价', key: 'entry_px', width: 90 },
  { title: '平仓价', key: 'exit_px', width: 90 },
  { title: '手数', key: 'qty', width: 70 },
  {
    title: '盈亏',
    key: 'pnl',
    width: 100,
    render: (row) =>
      h(
        'span',
        { style: { color: row.pnl >= 0 ? '#63e6be' : '#ff8787' } },
        fmtNumber(row.pnl),
      ),
  },
  {
    title: '持有',
    key: 'hold',
    width: 70,
    render: (row) => `${row.hold}天`,
  },
  {
    title: '出场原因',
    key: 'reason',
    width: 100,
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false },
        { default: () => row.reason },
      ),
  },
  { title: '入场', key: 'entry_reason', width: 80 },
]

async function run() {
  if (!symbol.value) return
  loading.value = true
  try {
    const range = Array.isArray(dateRange.value) ? dateRange.value : null
    const start = range?.[0]
    const end = range?.[1]
    const response = await api.backtest(
      symbol.value,
      start ? toDateString(start) : undefined,
      end ? toDateString(end) : undefined,
      // 参数卡片里的微调随本次回测一起提交，不写回保存值
      buildOverrides(),
    )
    result.value = response
    fillEdits(response.params)
    mcResult.value = null
  } catch (error) {
    message.error(error instanceof Error ? error.message : '回测失败')
  } finally {
    loading.value = false
  }
}

// 首次选择由 onMounted 负责运行，之后切换品种自动重跑刷新参数表单
let ready = false

watch(symbol, (value, prev) => {
  result.value = null
  optResult.value = null
  mcResult.value = null
  editNums.value = {}
  editMode.value = ''
  if (ready && value && prev) void run()
})

onMounted(async () => {
  try {
    const symbols = await store.ensureSymbols()
    // 优先上次选的品种，其次 TA，方便和旧回测页对照
    const target =
      symbols.find((item) => item.symbol === store.selectedSymbol) ??
      symbols.find((item) => item.symbol === 'TA') ??
      symbols[0]
    symbol.value = target?.symbol ?? ''
    await run()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载品种失败')
  } finally {
    ready = true
  }
})
</script>

<template>
  <n-card size="small" class="param-card">
    <div class="param-toolbar">
      <n-select
        v-model:value="symbol"
        filterable
        size="small"
        :options="symbolOptions"
        style="width: 160px"
        placeholder="选择品种"
      />
      <n-date-picker
        v-model:value="dateRange"
        type="daterange"
        size="small"
        clearable
        style="width: 260px"
      />
      <div class="param-actions">
        <n-button size="small" type="primary" :loading="loading" @click="run">
          运行回测
        </n-button>
        <n-button size="small" :loading="savingParams" @click="saveParams">
          保存参数
        </n-button>
        
      </div>
    </div>
    <div class="param-groups">
      <div v-for="meta in PARAM_ITEMS" :key="meta.key" class="param-group">
        <span class="param-label">{{ meta.label }}</span>
        <n-select
          v-if="meta.mode"
          :value="editMode"
          size="small"
          :options="ACC_MODE_OPTIONS"
          class="param-control"
          @update:value="(value) => (editMode = String(value))"
        />
        <n-select
          v-else-if="meta.switch"
          :value="editNums[meta.key]"
          size="small"
          :options="SWITCH_OPTIONS"
          class="param-control"
          @update:value="(value) => (editNums[meta.key] = Number(value))"
        />
        <n-input-number
          v-else
          v-model:value="editNums[meta.key]"
          size="small"
          :step="meta.step"
          :show-button="false"
          class="param-control"
        />
      </div>
    </div>
  </n-card>

  <n-spin :show="loading">
    <n-card v-if="result" size="small"  class="opt-card">
      <template #header>
        <div class="card-title">
          <span>{{ result.symbol }} 回测结果</span>
          <span class="hint">{{ result.start }} ~ {{ result.end }}</span>
        </div>
      </template>
      <template #header-extra>
        <span class="hint">方向准确率：{{ accuracyText }}</span>
      </template>

      <div class="metric-grid">
        <div v-for="item in metrics" :key="item.label" class="metric">
          <div class="metric-label">{{ item.label }}</div>
          <div class="metric-value" :class="item.tone">{{ item.value }}</div>
        </div>
      </div>

      <div v-if="result.open_position" class="hint open-pos">
        期末仍有持仓：{{
          result.open_position.direction === 'long' ? '多' : '空'
        }}
        {{ result.open_position.qty }} 手，已按最后收盘价计入权益
      </div>
    </n-card>

    <n-card v-if="result" size="small" class="chart-card">
      <template #header>
        <div class="card-title">
          <span>K 线 / 指标复盘</span>
          <!-- <span class="hint">
            ▲▼ 三角标记开/平仓（平仓方向相反） · 权益线叠加在主图，虚线为本金基准 ·
            附图顶部点指标文字开关，OI 分布随悬停日切换
          </span> -->
        </div>
      </template>
      <BacktestChart
        :symbol="result.symbol"
        :bars="result.bars"
        :stops="result.stop_curve"
        :equity="result.equity_curve"
        :trades="result.trades"
        :indicators="result.indicators"
        :derivatives="result.derivatives"
        :subchart="result.subchart"
        :band="band"
        :capital="result.summary.capital"
        :open-position="result.open_position"
        :mult="resultMult"
      />
    </n-card>

    <n-card size="small" class="opt-card">
      <template #header>
        <div class="card-title">
          <span>参数网格优化</span>
          <span class="hint">
            在保存参数基础上联合扫描，按期末权益排序；组合数上限
            {{ OPT_MAX_COMBOS }}
          </span>
        </div>
      </template>

      <div class="opt-select">
        <n-select
          v-model:value="optSelection"
          multiple
          filterable
          size="small"
          max-tag-count="responsive"
          :options="optOptions"
          placeholder="选择要优化的参数（可多选），括号内为候选范围"
        />
      </div>

      <div class="opt-actions">
        <n-button
          size="small"
          type="primary"
          :loading="optimizing"
          @click="runOptimize"
        >
          开始优化
        </n-button>
        <n-button
          size="small"
          :disabled="!optResult?.results.length"
          @click="fillBest"
        >
          填充最优值
        </n-button>
        <n-button
          size="small"
          :disabled="!optResult?.results.length"
          :loading="applying"
          @click="applyBest"
        >
          保存最优参数并回测
        </n-button>
        <span class="hint">
          已选 {{ optSelection.length }} 项 · 组合数 {{ optCombos }}
        </span>
      </div>

      <template v-if="optResult">
        <div class="opt-summary">
          <span class="hint">
            扫描 {{ optResult.combos }} 组，有交易 {{ optResult.tested }} 组，按最终权益降序显示前
            {{ optResult.results.length }} 组
          </span>
          <n-button size="tiny" quaternary @click="optResult = null">
            关闭
          </n-button>
        </div>
        <n-data-table
          v-if="optResult.results.length"
          :columns="optColumns"
          :data="optResult.results"
          :row-class-name="optRowClass"
          :bordered="false"
          :scroll-x="900"
          size="small"
        />
        <div v-else class="hint">所有组合均无交易</div>
      </template>
    </n-card>

    <n-card v-if="result" size="small" class="mc-card">
      <template #header>
        <div class="card-title">
          <span>蒙特卡洛模拟</span>
          <span class="hint">
            对交易盈亏做 10000 次有放回重采样，看实际结果在分布中的位置
          </span>
        </div>
      </template>
      <template #header-extra>
        <n-button size="small" :loading="mcRunning" @click="runMonteCarlo">
          运行模拟
        </n-button>
      </template>

      <template v-if="mcResult">
        <div class="mc-stats">
          <span>
            实际总PnL
            <b :class="mcResult.actual >= 0 ? 'pos' : 'neg'">
              {{ fmtNumber(mcResult.actual) }}
            </b>
          </span>
          <span>排名 <b>{{ mcResult.percentile }}%</b></span>
          <span>中位数 <b>{{ fmtNumber(mcResult.median) }}</b></span>
          <span>均值 <b>{{ fmtNumber(mcResult.mean) }}</b></span>
          <span>P05 <b>{{ fmtNumber(mcResult.p05) }}</b></span>
          <span>P95 <b>{{ fmtNumber(mcResult.p95) }}</b></span>
          <span>次数 <b>{{ mcResult.n.toLocaleString('zh-CN') }}</b></span>
        </div>
        <div class="mc-bars">
          <div v-for="(bin, index) in mcResult.bins" :key="index" class="mc-row">
            <span class="mc-range">
              {{ fmtNumber(bin.from) }}~{{ fmtNumber(bin.to) }}
            </span>
            <div class="mc-track">
              <div
                class="mc-bar"
                :class="{ actual: bin.actual }"
                :style="{
                  width: `${Math.max(1, (bin.count / mcResult.maxCount) * 100)}%`,
                }"
              />
            </div>
            <span class="mc-count">
              {{ bin.count }}{{ bin.actual ? ' ◀ 实际' : '' }}
            </span>
          </div>
        </div>
      </template>
      <span v-else class="hint">点击“运行模拟”查看重采样分布</span>
    </n-card>

    <n-card v-if="result" size="small" class="trade-card">
      <template #header>
        <div class="card-title">
          <span>交易明细</span>
          <span class="hint">
            共 {{ result.trades.length }} 笔，盈亏已扣双边手续费
          </span>
        </div>
      </template>
      <n-data-table
        :columns="columns"
        :data="result.trades"
        :bordered="false"
        size="small"
      />
    </n-card>

    <n-card v-if="!result && !loading" size="small">
      <span class="hint">选择品种后运行回测</span>
    </n-card>
  </n-spin>
</template>

<style scoped>
.card-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

/* 回测结果栏做紧凑；宽度随数字长度撑开，不截断 */
.metric-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.metric {
  flex: 1 1 auto;
  min-width: 92px;
  border: 1px solid #1a2530;
  border-radius: 6px;
  padding: 5px 8px;
  background: #0f161d;
}

.metric-label {
  margin-bottom: 2px;
  font-size: 10px;
  color: #7b8a99;
  white-space: nowrap;
}

.metric-value {
  font-size: 14px;
  font-weight: 600;
  color: #dbe4ee;
  white-space: nowrap;
}

.metric-value.pos {
  color: #63e6be;
}

.metric-value.neg {
  color: #ff8787;
}

.chart-card,
.opt-card,
.mc-card,
.trade-card {
  margin-top: 12px;
}

.open-pos {
  margin-top: 12px;
}

/* 顶部参数卡片：品种/日期和参数放在同一张卡里，和小面卡片同款 */
.param-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.param-groups {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 14px;
}

.param-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

.param-label {
  font-size: 12px;
  color: #8395a7;
  white-space: nowrap;
}

.param-control {
  width: 84px;
}

/* 参数输入框、品种/日期选择、优化参数下拉的底色都压深，和卡片底色拉开层次。
   n-select 的背景画在内部 label（单选）/ tags（多选）上，根节点没有背景，
   激活态会改用 --n-color-active，所以变量也要一起覆盖。 */
.param-control :deep(.n-input),
.param-toolbar :deep(.n-input) {
  --n-color: #0a1016;
  --n-color-active: #0a1016;
  background-color: #0a1016;
}

.param-control :deep(.n-base-selection),
.param-toolbar :deep(.n-base-selection),
.opt-select :deep(.n-base-selection) {
  --n-color: #0a1016;
  --n-color-active: #0a1016;
}

.param-control :deep(.n-base-selection-label),
.param-control :deep(.n-base-selection-tags),
.param-toolbar :deep(.n-base-selection-label),
.param-toolbar :deep(.n-base-selection-tags),
.opt-select :deep(.n-base-selection-label),
.opt-select :deep(.n-base-selection-tags) {
  background-color: #0a1016;
}

.param-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.param-hint {
  display: block;
  margin-top: 6px;
}

.opt-select {
  margin-bottom: 10px;
}

.opt-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.opt-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.opt-card :deep(.best-row td) {
  background: rgba(232, 200, 74, 0.08);
  font-weight: 600;
}

.mc-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-bottom: 10px;
  font-size: 12px;
  color: #8395a7;
}

.mc-stats b {
  color: #dbe4ee;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.mc-stats b.pos {
  color: #63e6be;
}

.mc-stats b.neg {
  color: #ff8787;
}

.mc-bars {
  max-height: 320px;
  overflow-y: auto;
}

.mc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
  font-size: 11px;
  color: #8395a7;
}

.mc-range {
  width: 120px;
  flex-shrink: 0;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.mc-track {
  flex: 1;
}

.mc-bar {
  height: 12px;
  min-width: 1px;
  border-radius: 2px;
  background: #54a0ff;
}

.mc-bar.actual {
  background: #e8c84a;
}

.mc-count {
  width: 84px;
  flex-shrink: 0;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
