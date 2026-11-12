<script setup lang="ts">
/**
 * 回测复盘图。
 *
 * 主图：K 线叠加 MP / BEC / BEP / 止损、权益线和可开关的衍生指标线；
 * 主图下方是日期轴，再往下是附图（GEX/OI/ATR/对冲流等，与主图共用 x 轴）
 * 和附图2（OI 分布，横轴是行权价，跟随悬停日切换）。
 * 拖动平移；按住+滚轮、Ctrl+滚轮或触摸板/触屏双指捏合缩放；
 * 触摸板双指横滑平移，纵向仍滚页面；单击锁定选中的 K 线、再点解除，双击复位。
 * 悬停显示当天全部指标和信号。
 * 绘制细节拆到 ./chart/：
 * - draw.ts       通用折线/阶梯线
 * - derivatives.ts 主图衍生指标线定义、显隐和绘制
 * - subchart.ts   附图指标定义、刻度、显隐和绘制
 * - oichart.ts    附图2 OI 分布：持仓柱、Δ 曲线、IV 微笑和行权价悬停
 * - markers.ts    到期倒计时、换月区间/标记、交易和告警标记
 * 这里只负责布局、交互和 tooltip。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api/client'
import type {
  BacktestPosition,
  BacktestTrade,
  ChartBar,
  DerivativePoint,
  IndicatorPoint,
  OiChainPoint,
  StopPoint,
  SubchartPoint,
} from '../api/client'
import {
  DERIVATIVE_GROUPS,
  drawDerivatives,
  expandPriceRange,
} from './chart/derivatives'
import { drawLineSeries, type SeriesFrame } from './chart/draw'
import { drawOiChart, hasHedgeData, hasIvSmile } from './chart/oichart'
import {
  computeRollRegions,
  drawExpiryCountdown,
  drawRollMarkers,
  drawRollRegions,
  drawTradeMarkers,
  drawWarnMarks,
} from './chart/markers'
import {
  DEFAULT_SUB_VISIBILITY,
  drawSubchart,
  SUB_GROUPS,
  subchartReadouts,
} from './chart/subchart'

const props = defineProps<{
  symbol: string
  bars: ChartBar[]
  stops: StopPoint[]
  equity: { d: string; eq: number }[]
  trades: BacktestTrade[]
  indicators: IndicatorPoint[]
  derivatives: DerivativePoint[]
  subchart: SubchartPoint[]
  capital: number
  band?: number
  /** 期末未平仓，信息栏在持仓区间内显示浮动盈亏 */
  openPosition?: BacktestPosition | null
  mult?: number
}>()

const wrapRef = ref<HTMLDivElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const width = ref(0)
const height = ref(0)

const viewStart = ref(0)
const viewCount = ref(0)
const hoverIndex = ref<number | null>(null)
const mouseX = ref(0)
const mouseY = ref(0)
// 单击锁定：锁定时十字线和 tooltip 停在点击的 K 线上，再点一下解除
const pinned = ref(false)
const pinnedY = ref(0)

// 颜色沿用旧回测页，方便对照
const COLORS = {
  up: '#5cd85a',
  down: '#ff6b6b',
  mp: '#ffd93d',
  bec: '#e74c3c',
  bep: '#a55eea',
  stop: '#ff9f43',
  eq: '#48dbfb',
  grid: '#1c2833',
  axis: '#5c6b7a',
  cross: 'rgba(255,255,255,0.35)',
}

const PAD_LEFT = 8
const PAD_RIGHT = 64
const PAD_TOP = 14
const PAD_BOTTOM = 6
// 主图与附图之间的间距（放日期轴）、附图顶部开关行、附图 / OI 面板固定高度
const GAP = 24
const SUB_HEADER = 16
const SUB_H = 120
const SUB_GAP = 10
const OI_H = 110
const MIN_COUNT = 40
const DEFAULT_COUNT = 260

// 主图指标线显隐；默认只开常用线，其余按需勾选
const show = reactive<Record<string, boolean>>({
  mp: true,
  be: false,
  stop: true,
  trade: true,
  eq: true,
  roll: true,
  expiry: true,
  gpk: true,
  wall: false,
  zg: false,
  mag: false,
  d25: false,
  oic: false,
})

const TOGGLES = [
  { key: 'mp', label: 'MP', color: COLORS.mp },
  { key: 'be', label: 'BE', color: COLORS.bep },
  { key: 'stop', label: '止损', color: COLORS.stop },
  { key: 'trade', label: '开平仓', color: '#ffffff' },
  { key: 'eq', label: '收益', color: COLORS.eq },
  { key: 'roll', label: '换月', color: '#e8c84a' },
  { key: 'expiry', label: '到期', color: '#54a0ff' },
  ...DERIVATIVE_GROUPS.map((group) => ({
    key: group.key,
    label: group.label,
    color: group.color,
  })),
]

// 附图指标显隐，默认值沿用旧页；开关是附图顶部的一行文字
const showSub = reactive<Record<string, boolean>>({
  ...DEFAULT_SUB_VISIBILITY,
})

// 附图2：OI 分布跟随悬停日，期权链按天拉取并缓存
const oiHedge = ref(true)
const oiHoverX = ref<number | null>(null)
const chain = ref<OiChainPoint[] | null>(null)
const chainCache = new Map<string, OiChainPoint[]>()
const chainPending = new Set<string>()
let chainKey = ''
let chainTimer: ReturnType<typeof setTimeout> | null = null

const oiIndex = computed(() =>
  hoverIndex.value ?? Math.max(0, props.bars.length - 1),
)
const oiBar = computed(() => props.bars[oiIndex.value] ?? null)

// 概览条：最高柱、卖方在痛点/现价能收到的内在价值，随 Δ对冲模式切换口径
const oiSummary = computed(() => {
  const list = chain.value
  const bar = oiBar.value
  if (!list || list.length === 0 || !bar) return ''
  const useDw = oiHedge.value && hasHedgeData(list)
  const callOf = (item: OiChainPoint) =>
    useDw ? Math.abs(item.cdw ?? 0) : (item.co ?? 0)
  const putOf = (item: OiChainPoint) =>
    useDw ? Math.abs(item.pdw ?? 0) : (item.po ?? 0)
  let peakStrike = list[0].s
  let peakOi = 0
  let mpLoss = 0
  let pxLoss = 0
  for (const item of list) {
    const total = callOf(item) + putOf(item)
    if (total > peakOi) {
      peakOi = total
      peakStrike = item.s
    }
    // 预期亏损 = 买方在痛点/现价处的总内在价值，也就是卖方收到的权利金
    if (item.s < bar.mp) mpLoss += (bar.mp - item.s) * (item.co ?? 0)
    if (item.s > bar.mp) mpLoss += (item.s - bar.mp) * (item.po ?? 0)
    if (item.s < bar.c) pxLoss += (bar.c - item.s) * (item.co ?? 0)
    if (item.s > bar.c) pxLoss += (item.s - bar.c) * (item.po ?? 0)
  }
  const money = (value: number) =>
    value >= 10000 ? `${(value / 10000).toFixed(0)}万` : value.toFixed(0)
  let text = `最高柱 ${peakStrike} (${Math.round(peakOi).toLocaleString()}手) · 权利金 痛点 ${money(mpLoss)} / 现价 ${money(pxLoss)}`
  if (useDw) text += ' · 柱=Δ×持仓'
  else if (oiHedge.value) text += ' · 该日无Δ数据'
  if (hasIvSmile(list)) text += ' · IV微笑 蓝Call/橙Put'
  return text
})

function fetchChain(symbol: string, day: string) {
  const key = `${symbol}|${day}`
  if (chainPending.has(key)) return
  chainPending.add(key)
  api
    .chain(symbol, day)
    .then((list) => {
      chainCache.set(key, list)
      if (chainKey === key) chain.value = list
    })
    .catch(() => {
      chainCache.set(key, [])
      if (chainKey === key) chain.value = []
    })
    .finally(() => chainPending.delete(key))
}

// 快速扫过 K 线时只请求停留后的那一天，避免每个 hover 都打后端
watch(
  () => [props.symbol, oiBar.value?.d] as const,
  () => {
    if (chainTimer != null) {
      clearTimeout(chainTimer)
      chainTimer = null
    }
    const bar = oiBar.value
    if (!props.symbol || !bar) {
      chainKey = ''
      chain.value = null
      return
    }
    chainKey = `${props.symbol}|${bar.d}`
    const cached = chainCache.get(chainKey)
    if (cached) {
      chain.value = cached
      return
    }
    // 换日先清空，避免短暂画出上一天再跳变
    chain.value = null
    chainTimer = setTimeout(() => {
      chainTimer = null
      fetchChain(props.symbol, bar.d)
    }, 60)
  },
  { immediate: true },
)

watch(chain, () => draw())
watch(oiHedge, () => draw())

const mpSeries = computed(() => props.bars.map((bar) => bar.mp))
const becSeries = computed(() => props.bars.map((bar) => bar.bec))
const bepSeries = computed(() => props.bars.map((bar) => bar.bep))
const stopSeries = computed(() => props.stops.map((item) => item.stop))
const rollRegions = computed(() => computeRollRegions(props.bars))

const indexByDate = computed(() => {
  const map = new Map<string, number>()
  props.bars.forEach((bar, index) => map.set(bar.d, index))
  return map
})

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function resetView() {
  const total = props.bars.length
  viewCount.value = Math.min(total, DEFAULT_COUNT)
  viewStart.value = Math.max(0, total - viewCount.value)
}

function plotGeometry() {
  const w = width.value
  const h = height.value
  const plotW = Math.max(1, w - PAD_LEFT - PAD_RIGHT)
  // 从下往上排：OI 分布、附图、日期轴、主图
  const oiBottom = h - PAD_BOTTOM
  const oiTop = oiBottom - OI_H
  const subBottom = oiTop - SUB_GAP
  const subTop = subBottom - SUB_H
  const priceTop = PAD_TOP
  const priceBottom = Math.max(priceTop + 80, subTop - GAP)
  return { plotW, priceTop, priceBottom, subTop, subBottom, oiTop, oiBottom }
}

// 附图开关和 OI 概览条是 HTML 覆盖层，位置跟随画布布局
const subLegendStyle = computed(() => {
  if (!height.value) return { top: '0px', left: '0px', right: '0px' }
  const { subTop } = plotGeometry()
  return {
    top: `${subTop + 2}px`,
    left: `${PAD_LEFT + 4}px`,
    right: `${PAD_RIGHT}px`,
  }
})

const oiHeaderStyle = computed(() => {
  if (!height.value) return { top: '0px', left: '0px', right: '0px' }
  const { oiTop } = plotGeometry()
  return {
    top: `${oiTop + 2}px`,
    left: `${PAD_LEFT + 4}px`,
    right: `${PAD_RIGHT}px`,
  }
})

function xAt(index: number) {
  const { plotW } = plotGeometry()
  const slot = plotW / Math.max(1, viewCount.value)
  return PAD_LEFT + (index - viewStart.value + 0.5) * slot
}

function fmtPrice(value: number) {
  return Math.round(value).toString()
}

function draw() {
  const canvas = canvasRef.value
  const wrap = wrapRef.value
  if (!canvas || !wrap) return
  const w = wrap.clientWidth
  const h = wrap.clientHeight
  if (w <= 0 || h <= 0 || viewCount.value <= 0) return
  width.value = w
  height.value = h

  const dpr = window.devicePixelRatio || 1
  canvas.width = Math.round(w * dpr)
  canvas.height = Math.round(h * dpr)
  canvas.style.width = `${w}px`
  canvas.style.height = `${h}px`
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)
  ctx.font = '10px ui-monospace, Menlo, monospace'

  const { plotW, priceTop, priceBottom, subTop, subBottom, oiTop, oiBottom } =
    plotGeometry()
  // viewStart 允许拖到数据两侧留白；绘制循环限制在数据范围内
  const start = Math.max(0, viewStart.value)
  const end = Math.min(props.bars.length, viewStart.value + viewCount.value)
  const slot = plotW / Math.max(1, viewCount.value)

  // 价格轴范围：K 线高低点 + 当前开启的线，留 4% 边距
  let lo = Infinity
  let hi = -Infinity
  for (let i = start; i < end; i += 1) {
    const bar = props.bars[i]
    lo = Math.min(lo, bar.l)
    hi = Math.max(hi, bar.h)
    if (show.mp) {
      lo = Math.min(lo, bar.mp)
      hi = Math.max(hi, bar.mp)
    }
    if (show.be) {
      for (const value of [bar.bec, bar.bep]) {
        if (value == null || !Number.isFinite(value)) continue
        lo = Math.min(lo, value)
        hi = Math.max(hi, value)
      }
    }
    if (show.stop) {
      const value = props.stops[i]?.stop
      if (value != null && Number.isFinite(value)) {
        lo = Math.min(lo, value)
        hi = Math.max(hi, value)
      }
      // 最后一根 K 线右侧还会延伸一格画明日止损，算进范围免得被裁掉
      if (i === props.bars.length - 1) {
        const next = props.stops[i]?.next
        if (next != null && Number.isFinite(next)) {
          lo = Math.min(lo, next)
          hi = Math.max(hi, next)
        }
      }
    }
  }
  if (props.derivatives.length) {
    const range = expandPriceRange(props.derivatives, start, end, show, lo, hi)
    lo = range[0]
    hi = range[1]
  }
  // 视图完全拖出数据时，用最近一根 K 线的收盘价 ± ATR 维持价格轴
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    const refIndex =
      viewStart.value >= props.bars.length ? props.bars.length - 1 : 0
    const ref = props.bars[refIndex]
    if (!ref) return
    const atr = props.indicators[refIndex]?.atr
    const span =
      atr != null && Number.isFinite(atr) && atr > 0
        ? atr
        : Math.max(ref.h - ref.l, ref.c * 0.01)
    lo = ref.c - span
    hi = ref.c + span
  }
  if (hi === lo) {
    hi += 1
    lo -= 1
  }
  const pricePad = (hi - lo) * 0.04
  lo -= pricePad
  hi += pricePad
  const yPrice = (value: number) =>
    priceBottom - ((value - lo) / (hi - lo)) * (priceBottom - priceTop)
  const frame: SeriesFrame = { start, end, xAt, yAt: yPrice }

  // 价格网格与右侧刻度
  ctx.lineWidth = 1
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  for (let g = 0; g <= 4; g += 1) {
    const y = priceTop + ((priceBottom - priceTop) * g) / 4
    ctx.strokeStyle = COLORS.grid
    ctx.beginPath()
    ctx.moveTo(PAD_LEFT, y)
    ctx.lineTo(w - PAD_RIGHT, y)
    ctx.stroke()
    ctx.fillStyle = COLORS.axis
    ctx.fillText(fmtPrice(hi - ((hi - lo) * g) / 4), w - 6, y)
  }

  // 换月高峰期色带：整段压暗 + 合约标签
  if (show.roll) {
    drawRollRegions(ctx, rollRegions.value, frame, priceTop, priceBottom, slot)
  }

  if (show.be) {
    drawLineSeries(ctx, becSeries.value, COLORS.bec, 0.6, frame)
    drawLineSeries(ctx, bepSeries.value, COLORS.bep, 0.6, frame)
  }
  if (show.mp) {
    drawLineSeries(ctx, mpSeries.value, COLORS.mp, 1, frame)
  }
  // 止损线：只画当日止损（虚线）；最后一根 K 线收盘仍持仓时，再向右延伸一格画明日止损
  if (show.stop) {
    drawLineSeries(ctx, stopSeries.value, COLORS.stop, 0.6, frame, [4, 3])
    const last = props.bars.length - 1
    if (end === props.bars.length && last >= start) {
      const today = props.stops[last]?.stop
      const tomorrow = props.stops[last]?.next
      if (
        today != null &&
        Number.isFinite(today) &&
        tomorrow != null &&
        Number.isFinite(tomorrow)
      ) {
        ctx.beginPath()
        ctx.strokeStyle = COLORS.stop
        ctx.lineWidth = 0.6
        ctx.setLineDash([4, 3])
        ctx.moveTo(xAt(last), yPrice(today))
        ctx.lineTo(xAt(last + 1), yPrice(tomorrow))
        ctx.stroke()
        ctx.setLineDash([])
      }
    }
  }

  // 衍生指标线单独成层，开关按组控制
  if (props.derivatives.length) {
    drawDerivatives(ctx, props.derivatives, show, frame, slot)
  }

  // K 线
  const bodyW = Math.max(1, slot * 0.62)
  for (let i = start; i < end; i += 1) {
    const bar = props.bars[i]
    const x = xAt(i)
    const up = bar.c >= bar.o
    ctx.strokeStyle = up ? COLORS.up : COLORS.down
    ctx.fillStyle = up ? COLORS.up : COLORS.down
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, yPrice(bar.h))
    ctx.lineTo(x, yPrice(bar.l))
    ctx.stroke()
    const top = yPrice(Math.max(bar.o, bar.c))
    const bottom = yPrice(Math.min(bar.o, bar.c))
    ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(1, bottom - top))
  }

  // 权益曲线：主图内用独立刻度叠加，不参与价格轴范围
  if (show.eq && props.equity.length) {
    let eqLo = Infinity
    let eqHi = -Infinity
    for (let i = start; i < end; i += 1) {
      const value = props.equity[i]?.eq
      if (value == null || !Number.isFinite(value)) continue
      eqLo = Math.min(eqLo, value)
      eqHi = Math.max(eqHi, value)
    }
    if (Number.isFinite(eqLo) && Number.isFinite(eqHi)) {
      // 把本金纳入刻度，保证基准线可见
      eqLo = Math.min(eqLo, props.capital)
      eqHi = Math.max(eqHi, props.capital)
      if (eqHi === eqLo) eqHi = eqLo + 1
      const eqPad = (eqHi - eqLo) * 0.1
      eqLo -= eqPad
      eqHi += eqPad
      const yEq = (value: number) =>
        priceBottom -
        ((value - eqLo) / (eqHi - eqLo)) * (priceBottom - priceTop)

      ctx.save()
      const yCap = yEq(props.capital)
      ctx.beginPath()
      ctx.setLineDash([4, 4])
      ctx.strokeStyle = '#8395a7'
      ctx.globalAlpha = 0.8
      ctx.lineWidth = 0.6
      ctx.moveTo(PAD_LEFT, yCap)
      ctx.lineTo(w - PAD_RIGHT, yCap)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = '#8395a7'
      ctx.textAlign = 'right'
      ctx.textBaseline = 'bottom'
      ctx.fillText('本金', w - PAD_RIGHT - 2, yCap - 2)

      ctx.beginPath()
      let eqStarted = false
      for (let i = start; i < end; i += 1) {
        const value = props.equity[i]?.eq
        if (value == null || !Number.isFinite(value)) {
          eqStarted = false
          continue
        }
        const x = xAt(i)
        const y = yEq(value)
        if (!eqStarted) {
          ctx.moveTo(x, y)
          eqStarted = true
        } else {
          ctx.lineTo(x, y)
        }
      }
      ctx.strokeStyle = COLORS.eq
      ctx.globalAlpha = 0.9
      ctx.lineWidth = 0.6
      ctx.stroke()
      ctx.restore()
    }
  }

  if (show.trade) {
    drawTradeMarkers(
      ctx,
      props.trades,
      frame,
      indexByDate.value,
      slot,
      props.openPosition,
    )
  }
  drawWarnMarks(ctx, props.indicators, frame, priceBottom - 10, slot)
  if (show.roll) {
    drawRollMarkers(ctx, props.bars, frame, priceTop)
  }
  if (show.expiry) {
    drawExpiryCountdown(ctx, props.bars, frame, priceTop)
  }

  // 附图：顶部一行放指标开关（HTML 覆盖层），下面才是绘制区
  ctx.fillStyle = '#09131c'
  ctx.fillRect(PAD_LEFT, subTop, w - PAD_LEFT - PAD_RIGHT, subBottom - subTop)
  drawSubchart(ctx, props.subchart ?? [], showSub, {
    start,
    end,
    left: PAD_LEFT,
    right: w - PAD_RIGHT,
    top: subTop + SUB_HEADER,
    bottom: subBottom,
    slot,
    xAt,
  })

  // 日期轴放在主图 K 线正下方，不再放在整张画布最底部
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.fillStyle = COLORS.axis
  const labelStep = Math.max(1, Math.ceil(84 / Math.max(slot, 0.001)))
  for (let i = start; i < end; i += labelStep) {
    ctx.fillText(props.bars[i].d.slice(2), xAt(i), priceBottom + 4)
  }

  // 明日临时 K 线：开收盘都用最后收盘价，占一格承载收盘后算出的明日信号
  if (end === props.bars.length && props.bars.length > 0) {
    const lastIdx = props.bars.length - 1
    const lastBar = props.bars[lastIdx]
    const x = xAt(props.bars.length)
    const y = yPrice(lastBar.c)
    ctx.save()
    ctx.strokeStyle = '#576574'
    ctx.fillStyle = '#576574'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x - bodyW / 2, y)
    ctx.lineTo(x + bodyW / 2, y)
    ctx.stroke()
    ctx.font = '9px sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.fillText('明日', x, priceBottom + 4)
    // 与回测实际执行口径一致，画门控后的 trade_pred
    const signal = props.indicators[lastIdx]?.trade_pred ?? null
    if (signal) {
      const color = signal === 'long' ? COLORS.up : COLORS.down
      const dir = signal === 'long' ? 1 : -1
      const my = y + dir * 10
      ctx.fillStyle = color
      ctx.beginPath()
      if (signal === 'long') {
        ctx.moveTo(x, my - 4)
        ctx.lineTo(x - 4, my + 3)
        ctx.lineTo(x + 4, my + 3)
      } else {
        ctx.moveTo(x, my + 4)
        ctx.lineTo(x - 4, my - 3)
        ctx.lineTo(x + 4, my - 3)
      }
      ctx.closePath()
      ctx.fill()
      ctx.font = 'bold 9px ui-monospace, Menlo, monospace'
      ctx.textAlign = 'left'
      ctx.textBaseline = 'middle'
      ctx.fillText(signal === 'long' ? '多' : '空', x + 6, my)
    }
    ctx.restore()
  }

  // 附图2：OI 分布，横轴是行权价，跟随当前悬停日
  const oiRef = oiBar.value
  drawOiChart(
    ctx,
    chain.value,
    { left: PAD_LEFT, right: w - PAD_RIGHT, top: oiTop, bottom: oiBottom },
    {
      price: oiRef?.c ?? null,
      mp: oiRef?.mp ?? null,
      hedge: oiHedge.value,
      hoverX: oiHoverX.value,
    },
  )

  // 十字光标
  const idx = hoverIndex.value
  if (idx != null && idx >= start && idx < end) {
    const x = xAt(idx)
    ctx.setLineDash([3, 3])
    ctx.strokeStyle = COLORS.cross
    ctx.beginPath()
    ctx.moveTo(x, priceTop)
    ctx.lineTo(x, subBottom)
    ctx.stroke()
    // 锁定时横线停在点击高度，不再跟随鼠标
    const lineY = pinned.value ? pinnedY.value : mouseY.value
    if (lineY >= priceTop && lineY <= priceBottom) {
      ctx.beginPath()
      ctx.moveTo(PAD_LEFT, lineY)
      ctx.lineTo(w - PAD_RIGHT, lineY)
      ctx.stroke()
      ctx.setLineDash([])
      const value =
        hi - ((lineY - priceTop) / (priceBottom - priceTop)) * (hi - lo)
      ctx.fillStyle = '#2a3a4a'
      ctx.fillRect(w - PAD_RIGHT + 2, lineY - 8, PAD_RIGHT - 6, 16)
      ctx.fillStyle = '#dbe4ee'
      ctx.textAlign = 'right'
      ctx.textBaseline = 'middle'
      ctx.fillText(fmtPrice(value), w - 6, lineY)
    }
    ctx.setLineDash([])
  }
}

function updateHoverFromMouse(x: number, y: number) {
  const { plotW, priceTop, priceBottom, subTop, subBottom } = plotGeometry()
  if (x < PAD_LEFT || x > PAD_LEFT + plotW) {
    hoverIndex.value = null
    return
  }
  const slot = plotW / Math.max(1, viewCount.value)
  const index = viewStart.value + Math.floor((x - PAD_LEFT) / slot)
  const inPanels =
    (y >= priceTop && y <= priceBottom) || (y >= subTop && y <= subBottom)
  hoverIndex.value =
    inPanels && index >= 0 && index < props.bars.length ? index : null
}

/** 按鼠标位置刷新悬停；OI 面板里按行权价定位，不改变当前 K 线悬停日。 */
function refreshHover(x: number, y: number) {
  const { oiTop, oiBottom } = plotGeometry()
  if (y >= oiTop && y <= oiBottom) {
    oiHoverX.value = x
    return
  }
  oiHoverX.value = null
  updateHoverFromMouse(x, y)
}

let dragLastX: number | null = null
let dragAcc = 0
// 点击判定：按下后位移小于阈值、不是捏合、且落在主图/附图上才算单击
let downX = 0
let downY = 0
let downInChart = false
let tapMoved = false
let suppressTap = false
let pointerActive = false

/** 单击切换锁定；解锁时按当前点击位置恢复跟随。 */
function togglePin(x: number, y: number) {
  if (pinned.value) {
    pinned.value = false
    updateHoverFromMouse(x, y)
    draw()
    return
  }
  const { plotW } = plotGeometry()
  // 点右侧刻度区或画布外不锁定
  if (x < PAD_LEFT || x > PAD_LEFT + plotW) return
  const slot = plotW / Math.max(1, viewCount.value)
  const index = viewStart.value + Math.floor((x - PAD_LEFT) / slot)
  if (index < 0 || index >= props.bars.length) return
  hoverIndex.value = index
  pinnedY.value = y
  pinned.value = true
  draw()
}

// 触屏双指捏合：记录两指落下时的间距和锚点，按比例缩放可见根数
const touchPoints = new Map<number, { x: number; y: number }>()
let pinchStartDist = 0
let pinchStartCount = 0
let pinchStartView = 0
let pinchRatio = 0

function touchDistance() {
  const points = [...touchPoints.values()]
  if (points.length < 2) return 0
  return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y)
}

function touchAnchorX() {
  const points = [...touchPoints.values()]
  if (points.length < 2) return PAD_LEFT
  return (points[0].x + points[1].x) / 2
}

/** 以画布横坐标 x 为锚点缩放；factor < 1 放大，> 1 缩小。 */
function zoomAt(x: number, factor: number) {
  const { plotW } = plotGeometry()
  if (x < PAD_LEFT || x > PAD_LEFT + plotW) return false
  const total = props.bars.length
  const nextCount = clamp(
    Math.round(viewCount.value * factor),
    Math.min(MIN_COUNT, total),
    total,
  )
  if (nextCount === viewCount.value) return false
  const ratio = (x - PAD_LEFT) / plotW
  const anchor = viewStart.value + ratio * viewCount.value
  viewCount.value = nextCount
  // 保持锚点位置对应的数据不改，同样不在两端夹紧
  viewStart.value = Math.round(anchor - ratio * nextCount)
  return true
}

function onPointerDown(event: PointerEvent) {
  const canvas = canvasRef.value
  if (!canvas) return
  canvas.setPointerCapture(event.pointerId)
  const wrap = wrapRef.value
  if (!wrap) return
  const rect = wrap.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  downX = x
  downY = y
  tapMoved = false
  pointerActive = true
  const { priceTop, priceBottom, subTop, subBottom } = plotGeometry()
  downInChart =
    (y >= priceTop && y <= priceBottom) || (y >= subTop && y <= subBottom)

  if (event.pointerType === 'touch') {
    touchPoints.set(event.pointerId, { x, y })
    if (touchPoints.size >= 2) {
      // 第二根手指落下：结束单指拖动，进入捏合缩放，并取消本次点击
      suppressTap = true
      pinchStartDist = touchDistance()
      pinchStartCount = viewCount.value
      pinchStartView = viewStart.value
      pinchRatio = clamp(
        (touchAnchorX() - PAD_LEFT) / Math.max(1, plotGeometry().plotW),
        0,
        1,
      )
      dragLastX = null
      dragAcc = 0
      return
    }
    // 新的单指手势，前面捏合留下的取消标记失效
    suppressTap = false
  } else {
    suppressTap = false
  }
  dragLastX = event.clientX
  dragAcc = 0
}

function onPointerMove(event: PointerEvent) {
  const wrap = wrapRef.value
  if (!wrap) return
  const rect = wrap.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top

  if (event.pointerType === 'touch') {
    const tracked = touchPoints.get(event.pointerId)
    if (tracked) {
      tracked.x = x
      tracked.y = y
      // 双指捏合：按当前间距相对落下时的比例缩放，锚点固定在两指中点
      if (touchPoints.size >= 2 && pinchStartDist > 0) {
        const dist = touchDistance()
        if (dist > 0) {
          const total = props.bars.length
          const nextCount = clamp(
            Math.round((pinchStartCount * pinchStartDist) / dist),
            Math.min(MIN_COUNT, total),
            total,
          )
          viewCount.value = nextCount
          viewStart.value = Math.round(
            pinchStartView + pinchRatio * (pinchStartCount - nextCount),
          )
        }
        mouseX.value = x
        mouseY.value = y
        draw()
        return
      }
    }
  }

  if (dragLastX != null) {
    dragAcc += event.clientX - dragLastX
    dragLastX = event.clientX
    const { plotW } = plotGeometry()
    const perBar = plotW / Math.max(1, viewCount.value)
    const steps = Math.trunc(dragAcc / perBar)
    if (steps !== 0) {
      dragAcc -= steps * perBar
      // 不限制两端：数据拖完后继续留白，方便看边缘
      viewStart.value -= steps
    }
  }

  if (Math.hypot(x - downX, y - downY) > 4) tapMoved = true
  mouseX.value = x
  mouseY.value = y
  if (pinned.value) {
    // 锁定时十字线不跟随，只保留 OI 面板的行权价悬停
    const { oiTop, oiBottom } = plotGeometry()
    oiHoverX.value = y >= oiTop && y <= oiBottom ? x : null
  } else {
    refreshHover(x, y)
  }
  draw()
}

function onPointerUp(event: PointerEvent) {
  const canvas = canvasRef.value
  const wrap = wrapRef.value
  const rect = wrap?.getBoundingClientRect()
  const x = rect ? event.clientX - rect.left : 0
  const y = rect ? event.clientY - rect.top : 0

  // 没拖动、没捏合、点在主图/附图上：切换锁定
  if (pointerActive && !tapMoved && downInChart && !suppressTap) {
    togglePin(x, y)
  }

  if (event.pointerType === 'touch') {
    touchPoints.delete(event.pointerId)
    if (touchPoints.size < 2) pinchStartDist = 0
    // 捏合结束还剩一根手指时，继续按拖动处理
    const rest = [...touchPoints.values()][0]
    dragLastX = rest && rect ? rect.left + rest.x : null
    dragAcc = 0
    pointerActive = touchPoints.size > 0
  } else {
    dragLastX = null
    dragAcc = 0
    pointerActive = false
  }
  if (canvas?.hasPointerCapture(event.pointerId)) {
    canvas.releasePointerCapture(event.pointerId)
  }
}

function onPointerCancel(event: PointerEvent) {
  if (event.pointerType === 'touch') {
    touchPoints.delete(event.pointerId)
    if (touchPoints.size < 2) pinchStartDist = 0
  }
  dragLastX = null
  dragAcc = 0
  pointerActive = false
}

function onPointerLeave() {
  touchPoints.clear()
  pinchStartDist = 0
  dragLastX = null
  dragAcc = 0
  // 锁定时保留选中的 K 线，鼠标移出图表也不清除
  if (!pinned.value) hoverIndex.value = null
  oiHoverX.value = null
  draw()
}

let wheelPanAcc = 0

function onWheel(event: WheelEvent) {
  const wrap = wrapRef.value
  if (!wrap || viewCount.value <= 0) return

  // 触摸板捏合在浏览器里表现为 ctrl+wheel，统一当作缩放
  const pinch = event.ctrlKey || event.metaKey
  // deltaMode: 0=像素, 1=行, 2=页；统一折算成像素再算步长
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 120 : 1
  const deltaX = event.deltaX * unit
  const deltaY = event.deltaY * unit
  const horizontal = Math.abs(deltaX) > Math.abs(deltaY) && deltaX !== 0

  // 触摸板双指横滑：平移图表；纵向两指滑动留给页面滚动
  if (!pinch && horizontal) {
    event.preventDefault()
    const { plotW } = plotGeometry()
    const perBar = plotW / Math.max(1, viewCount.value)
    wheelPanAcc += deltaX
    const steps = Math.trunc(wheelPanAcc / perBar)
    if (steps !== 0) {
      wheelPanAcc -= steps * perBar
      viewStart.value += steps
      if (!pinned.value) refreshHover(mouseX.value, mouseY.value)
      draw()
    }
    return
  }

  if (deltaY === 0) return
  // 缩放：按住鼠标滚轮、Ctrl/Cmd+滚轮（含触摸板捏合）；普通滚轮让页面滚动
  if (!pinch && dragLastX == null) return
  event.preventDefault()
  const rect = wrap.getBoundingClientRect()
  const x = event.clientX - rect.left
  // 指数平滑：高分辨率滚轮/捏合的小 delta 也能平顺缩放，单步最多 1.4 倍
  const factor = clamp(
    Math.exp(deltaY * (pinch ? 0.01 : 0.002)),
    0.7,
    1.4,
  )
  if (!zoomAt(x, factor)) return
  mouseX.value = x
  mouseY.value = event.clientY - rect.top
  if (!pinned.value) refreshHover(x, mouseY.value)
  draw()
}

function onDoubleClick() {
  // 双击同时解除锁定并复位视图
  pinned.value = false
  resetView()
  draw()
}

let observer: ResizeObserver | null = null

onMounted(() => {
  resetView()
  observer = new ResizeObserver(() => draw())
  if (wrapRef.value) observer.observe(wrapRef.value)
  draw()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  if (chainTimer != null) {
    clearTimeout(chainTimer)
    chainTimer = null
  }
})

watch(
  () => props.bars,
  () => {
    resetView()
    pinned.value = false
    hoverIndex.value = null
    chainCache.clear()
    draw()
  },
)

// 勾选/取消指标线后立即重绘
watch(show, () => draw(), { deep: true })
watch(showSub, () => draw(), { deep: true })

function fmt(value: number | null | undefined, digits = 2) {
  if (value == null || !Number.isFinite(value)) return '-'
  return value.toFixed(digits)
}

function predLabel(value: 'long' | 'short' | null) {
  return value === 'long' ? '多' : value === 'short' ? '空' : '震荡'
}

const hoverData = computed(() => {
  const index = hoverIndex.value
  const bar = index == null ? null : props.bars[index]
  if (index == null || !bar) return null
  return {
    bar,
    indicator: props.indicators[index],
    stop: props.stops[index],
    equity: props.equity[index]?.eq ?? null,
    derivative: props.derivatives[index] ?? null,
    sub: props.subchart?.[index] ?? null,
    dayTrades: props.trades.filter(
      (trade) =>
        trade.entry_date === bar.d || trade.exit_date === bar.d,
    ),
  }
})

// 收盘仍持仓：开仓日 ≤ 当天 < 平仓日；期末未平仓的区间用 open_position 兜底
const hoverPosition = computed(() => {
  const data = hoverData.value
  if (!data) return null
  const day = data.bar.d
  const trade = props.trades.find(
    (item) => item.entry_date <= day && day < item.exit_date,
  )
  const open = trade ?? (
    props.openPosition && props.openPosition.entry_date <= day
      ? props.openPosition
      : null
  )
  if (!open) return null
  const diff =
    open.direction === 'long'
      ? data.bar.c - open.entry_px
      : open.entry_px - data.bar.c
  return { ...open, pnl: diff * open.qty * (props.mult ?? 1) }
})

// 悬停时只展示当前开启且有值的衍生线
const hoverDerivatives = computed(() => {
  const point = hoverData.value?.derivative
  if (!point) return []
  return DERIVATIVE_GROUPS.filter(
    (group) =>
      show[group.key] && group.lines.some((line) => point[line.key] != null),
  ).map((group) => ({
    label: group.label,
    value: group.lines
      .map((line) => `${line.name} ${fmt(point[line.key])}`)
      .join(' / '),
  }))
})

// 附图悬停：只展示当前开启且有数据的指标
const hoverSubchart = computed(() => {
  const point = hoverData.value?.sub
  if (!point) return []
  return subchartReadouts(point, showSub)
})

const signalText = computed(() => {
  const indicator = hoverData.value?.indicator
  if (!indicator) return '-'
  let text = predLabel(indicator.pred)
  if (indicator.warn) text += ' · 准确率告警'
  if (indicator.trade_pred !== indicator.pred) {
    text +=
      indicator.trade_pred == null
        ? ' · 门控跳过'
        : ` · 门控反向为${predLabel(indicator.trade_pred)}`
  }
  if (indicator.rev_exit) text += ' · 持仓方向反转，次日开盘离场'
  return text
})

const tooltipStyle = computed<Record<string, string>>(() => {
  // 锁定后信息栏固定到右上角，跟选中的 K 线分开，不遮挡看盘
  if (pinned.value) return { right: '8px', top: '8px' }
  const left = clamp(
    mouseX.value > width.value - 320 ? mouseX.value - 300 : mouseX.value + 14,
    4,
    Math.max(4, width.value - 300),
  )
  const top = clamp(mouseY.value + 14, 6, Math.max(6, height.value - 380))
  return { left: `${left}px`, top: `${top}px` }
})

function tradeParts(trade: BacktestTrade, day: string) {
  const dir = trade.direction === 'long' ? '多' : '空'
  const open = `${dir}开 ${fmt(trade.entry_px)} ×${trade.qty}`
  const close = `${dir}平 ${fmt(trade.exit_px)} ×${trade.qty}`
  // 按悬停当天显示当时发生的动作：开仓日写开，平仓日写平
  const side =
    trade.entry_date === trade.exit_date
      ? `${open} / ${close}`
      : trade.exit_date === day
        ? close
        : open
  return {
    side,
    amount: `${trade.pnl >= 0 ? '+' : ''}${fmt(trade.pnl, 0)}`,
    loss: trade.pnl < 0,
    reason: trade.reason,
  }
}

// 信息栏里的当日成交，金额单独标色
const hoverTrades = computed(() => {
  const day = hoverData.value?.bar.d
  if (day == null) return []
  return (hoverData.value?.dayTrades ?? []).map((trade) =>
    tradeParts(trade, day),
  )
})
</script>

<template>
  <div class="chart-shell">
    <div class="chart-toolbar">
      <label v-for="item in TOGGLES" :key="item.key" class="toggle">
        <input v-model="show[item.key]" type="checkbox" />
        <span :style="{ color: item.color }">{{ item.label }}</span>
      </label>
      <span class="toolbar-hint">
        衍生线带宽 ±{{ band ?? 20 }}% · 红X 准确率告警 · 拖动平移 ·
        单击锁定/再点解除 · 按住+滚轮 / Ctrl+滚轮缩放 · 触摸板双指横滑平移、捏合缩放 ·
        双击复位
      </span>
    </div>
    <div ref="wrapRef" class="chart-wrap">
      <canvas
        ref="canvasRef"
        class="chart-canvas"
        @wheel="onWheel"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerCancel"
        @pointerleave="onPointerLeave"
        @dblclick="onDoubleClick"
      />

      <!-- 附图开关铺在附图顶部：只留文字，开启时用指标颜色，关闭灰色 -->
      <div class="sub-legend" :style="subLegendStyle">
        <span
          v-for="group in SUB_GROUPS"
          :key="group.key"
          class="sub-legend-item"
          :class="{ off: !showSub[group.key] }"
          :style="showSub[group.key] ? { color: group.color } : undefined"
          @click="showSub[group.key] = !showSub[group.key]"
        >
          {{ group.label }}
        </span>
      </div>

      <!-- OI 分布概览与 Δ对冲开关，画在面板顶部预留行里 -->
      <div class="oi-header" :style="oiHeaderStyle">
        <span class="oi-title">OI分布</span>
        <span
          class="oi-toggle"
          :class="{ off: !oiHedge }"
          @click="oiHedge = !oiHedge"
        >
          Δ对冲
        </span>
        <span class="oi-summary">{{ oiSummary }}</span>
      </div>

      <div v-if="hoverData" class="tooltip" :style="tooltipStyle">
        <div class="head">{{ hoverData.bar.d }}</div>
        <div class="row">
          <span class="label">开/高</span>
          <span>{{ fmt(hoverData.bar.o) }} / {{ fmt(hoverData.bar.h) }}</span>
        </div>
        <div class="row">
          <span class="label">低/收</span>
          <span>{{ fmt(hoverData.bar.l) }} / {{ fmt(hoverData.bar.c) }}</span>
        </div>
        <div class="row">
          <span class="label">MP</span>
          <span>{{ fmt(hoverData.bar.mp) }}</span>
        </div>
        <div class="row">
          <span class="label">BEC/BEP</span>
          <span>{{ fmt(hoverData.bar.bec) }} / {{ fmt(hoverData.bar.bep) }}</span>
        </div>
        <div class="row">
          <span class="label">到期/天数</span>
          <span>
            {{ hoverData.bar.expiry ?? '-' }} /
            {{ hoverData.bar.dte ?? '-' }}天
          </span>
        </div>
        <div class="row">
          <span class="label">期货合约</span>
          <span>{{ hoverData.bar.fut_contract ?? '-' }}</span>
        </div>
        <div
          v-for="item in hoverDerivatives"
          :key="item.label"
          class="row"
        >
          <span class="label">{{ item.label }}</span>
          <span>{{ item.value }}</span>
        </div>
        <div v-for="item in hoverSubchart" :key="item.label" class="row">
          <span class="label" :style="{ color: item.color }">{{ item.label }}</span>
          <span>{{ item.value }}</span>
        </div>
        <div class="row">
          <span class="label">止损/明日</span>
          <span>
            {{ fmt(hoverData.stop?.stop) }} / {{ fmt(hoverData.stop?.next) }}
          </span>
        </div>
        <template v-if="hoverData.indicator">
          <div class="row">
            <span class="label">ATR/占价</span>
            <span>
              {{ fmt(hoverData.indicator.atr) }} /
              {{ fmt((hoverData.indicator.atr_ratio ?? 0) * 100) }}%
            </span>
          </div>
          <div class="row">
            <span class="label">EWMA 快/慢</span>
            <span>{{ fmt(hoverData.indicator.ewma_ratio) }}</span>
          </div>
          <div class="row">
            <span class="label">斜率(日)</span>
            <span>{{ fmt(hoverData.indicator.slope * 100, 3) }}%</span>
          </div>
          <div class="row">
            <span class="label">MP 偏离 EMA</span>
            <span>{{ fmt(hoverData.indicator.mp_dev) }}%</span>
          </div>
        </template>
        <div class="row">
          <span class="label">权益</span>
          <span>{{ fmt(hoverData.equity, 0) }}</span>
        </div>
        <div v-if="hoverPosition" class="pos">
          <span class="label">持仓</span>
          {{ hoverPosition.direction === 'long' ? '多' : '空' }}
          {{ hoverPosition.qty }}手 · 入场 {{ fmt(hoverPosition.entry_px) }} · 浮动
          <span :class="hoverPosition.pnl < 0 ? 'loss' : 'gain'">
            {{ hoverPosition.pnl >= 0 ? '+' : '' }}{{ fmt(hoverPosition.pnl, 0) }}
          </span>
        </div>
        <div class="sig" :class="{ warn: hoverData.indicator?.warn }">
          次日信号：{{ signalText }}
        </div>
        <div v-for="(item, index) in hoverTrades" :key="index" class="trade">
          {{ item.side }} ·
          <span :class="{ loss: item.loss }">{{ item.amount }}</span> ·
          {{ item.reason }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chart-shell {
  width: 100%;
}

.chart-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  padding: 0 0 6px;
  font-size: 11px;
  line-height: 16px;
}

.toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  user-select: none;
}

.toggle input {
  margin: 0;
  accent-color: #54a0ff;
}

.toolbar-hint {
  color: #576574;
}

/* 附图顶部的指标开关：纯文字，开=指标色，关=灰色 */
.sub-legend {
  position: absolute;
  z-index: 4;
  display: flex;
  gap: 0 10px;
  overflow: hidden;
  font-size: 10px;
  line-height: 14px;
  white-space: nowrap;
  pointer-events: none;
  user-select: none;
}

.sub-legend-item {
  color: #576574;
  cursor: pointer;
  pointer-events: auto;
}

/* OI 分布面板顶部概览行 */
.oi-header {
  position: absolute;
  z-index: 4;
  display: flex;
  gap: 10px;
  overflow: hidden;
  font-size: 10px;
  line-height: 14px;
  white-space: nowrap;
  pointer-events: none;
  user-select: none;
}

.oi-title {
  color: #7b8a99;
  font-weight: 600;
}

.oi-toggle {
  color: #00d2d3;
  cursor: pointer;
  pointer-events: auto;
}

.oi-toggle.off {
  color: #576574;
}

.oi-summary {
  overflow: hidden;
  color: #576574;
  text-overflow: ellipsis;
}

.chart-wrap {
  position: relative;
  width: 100%;
  height: 660px;
  touch-action: none;
  user-select: none;
}

.chart-canvas {
  display: block;
  width: 100%;
  height: 100%;
  cursor: crosshair;
}

.tooltip {
  position: absolute;
  z-index: 5;
  width: 280px;
  padding: 8px 10px;
  border: 1px solid #2d3436;
  border-radius: 6px;
  background: rgba(15, 22, 29, 0.95);
  font-size: 11px;
  line-height: 17px;
  color: #c8d6e5;
  pointer-events: none;
  font-variant-numeric: tabular-nums;
}

.tooltip .head {
  margin-bottom: 4px;
  font-weight: 600;
  color: #dbe4ee;
}

.tooltip .row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.tooltip .label {
  color: #7b8a99;
}

.tooltip .pos {
  margin-top: 4px;
  padding: 2px 4px;
  border: 1px solid #54a0ff33;
  border-radius: 3px;
  background: #54a0ff15;
  color: #dbe4ee;
}

.tooltip .pos .loss {
  color: #ff8787;
}

.tooltip .pos .gain {
  color: #63e6be;
}

.tooltip .sig {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid #1a2530;
  color: #ffd93d;
}

.tooltip .sig.warn {
  color: #ff8787;
}

.tooltip .trade {
  margin-top: 2px;
  color: #63e6be;
}

.tooltip .trade .loss {
  color: #ff8787;
}
</style>
