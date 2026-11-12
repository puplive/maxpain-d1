/**
 * 复盘图下方附图指标。
 *
 * 指标定义集中在 SUB_GROUPS：颜色、线型、刻度方式、悬停格式和默认显隐都从这里读。
 * 多条指标共用一块画布，每条按自身可见范围内的极值映射到同一高度，
 * 和旧 web/index.html #subChart 的叠加方式一致；0 值由 zeroGap 决定是否视为缺失。
 */
import type { SubchartPoint } from '../../api/client'

export interface SubSeries {
  color: string
  value: (point: SubchartPoint) => number
  width?: number
  dash?: number[]
  alpha?: number
  /** bar 画柱状，line 画折线 */
  kind?: 'line' | 'bar'
  /** 柱状按正负着色（绿正红负） */
  sign?: boolean
  /** 刻度方式：auto 按极值，symmetric 以 0 对称，positive 从 0 起 */
  scale?: 'auto' | 'symmetric' | 'positive'
  /** 共用刻度的标识；不填默认用 group.key */
  scaleId?: string
  /** 0 视为缺失并断开（旧页对大部分期权字段这样处理） */
  zeroGap?: boolean
}

export interface SubGroup {
  key: string
  label: string
  color: string
  /** 悬停显示文案 */
  format: (point: SubchartPoint) => string
  series: SubSeries[]
}

export type SubVisibility = Record<string, boolean>

const UP = '#5cd85a'
const DOWN = '#ff6b6b'

function signed(value: number, digits = 0) {
  const text = value.toFixed(digits)
  return value > 0 ? `+${text}` : text
}

function hand(value: number) {
  return `${signed(Math.round(value))}手`
}

export const SUB_GROUPS: SubGroup[] = [
  {
    key: 'gex',
    label: 'GEX',
    color: '#a55eea',
    format: (point) =>
      `GEX ${signed(point.gex)} / 归一 ${point.gex_norm.toFixed(3)}`,
    series: [
      {
        color: UP,
        value: (point) => point.gex,
        kind: 'bar',
        sign: true,
        scale: 'auto',
        zeroGap: true,
      },
      {
        color: '#a55eea',
        value: (point) => point.gex_norm,
        width: 0.6,
        scale: 'auto',
        scaleId: 'gex_norm',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'skewVal',
    label: 'OI偏度',
    color: '#fdcb6e',
    format: (point) => `OI偏度 ${signed(point.skew * 100, 1)}%`,
    series: [
      {
        color: '#fdcb6e',
        value: (point) => point.skew,
        scale: 'symmetric',
        scaleId: 'skew',
      },
    ],
  },
  {
    key: 'oiNet',
    label: 'OI净差',
    color: UP,
    format: (point) => `OI净差 ${signed(point.oi_net)}`,
    series: [
      {
        color: UP,
        value: (point) => point.oi_net,
        kind: 'bar',
        sign: true,
        scale: 'symmetric',
        scaleId: 'oi_net',
      },
    ],
  },
  {
    key: 'skew',
    label: '偏度差',
    color: '#48dbfb',
    format: (point) => `偏度差 ${signed(point.skew_delta * 100, 2)}%`,
    series: [
      {
        color: '#48dbfb',
        value: (point) => point.skew_delta,
        dash: [2, 2],
        scale: 'auto',
        scaleId: 'skew_delta',
      },
    ],
  },
  {
    key: 'vr',
    label: '量比',
    color: '#ff9f43',
    format: (point) => `量比 ${point.vr.toFixed(2)}`,
    series: [
      {
        color: '#ff9f43',
        value: (point) => point.vr,
        dash: [2, 2],
        scale: 'auto',
        scaleId: 'vr',
      },
    ],
  },
  {
    key: 'iv',
    label: 'IV偏度',
    color: '#2ecc71',
    format: (point) => `IV偏度 ${signed(point.iv * 100, 2)}%`,
    series: [
      {
        color: '#2ecc71',
        value: (point) => point.iv,
        dash: [4, 3],
        scale: 'auto',
        scaleId: 'iv',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'futVol',
    label: '期货成交量',
    color: '#54a0ff',
    format: (point) => `期货成交量 ${Math.round(point.fut_vol).toLocaleString()}`,
    series: [
      {
        color: '#54a0ff',
        value: (point) => point.fut_vol,
        kind: 'bar',
        alpha: 0.7,
        scale: 'positive',
        scaleId: 'fut_vol',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'futOi',
    label: '期货持仓量',
    color: '#f368e0',
    format: (point) => `期货持仓量 ${Math.round(point.fut_oi).toLocaleString()}`,
    series: [
      {
        color: '#f368e0',
        value: (point) => point.fut_oi,
        scale: 'auto',
        scaleId: 'fut_oi',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'atr',
    label: 'ATR/TR',
    color: '#e84393',
    format: (point) => `ATR ${point.atr.toFixed(1)} / TR ${point.tr.toFixed(1)}`,
    series: [
      {
        color: '#ff9ecd',
        value: (point) => point.tr,
        width: 0.6,
        alpha: 0.2,
        scale: 'auto',
        scaleId: 'atr',
        zeroGap: true,
      },
      {
        color: '#e84393',
        value: (point) => point.atr,
        width: 1,
        scale: 'auto',
        scaleId: 'atr',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'mpConf',
    label: 'MP置信',
    color: '#00b894',
    format: (point) => `MP置信 ${(point.mp_conf * 100).toFixed(1)}%`,
    series: [
      {
        color: '#00b894',
        value: (point) => point.mp_conf,
        scale: 'auto',
        scaleId: 'mp_conf',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'noise',
    label: '噪音',
    color: '#0f9b8e',
    format: (point) =>
      `噪音 多 ${point.noise_l.toFixed(1)}% / 空 ${point.noise_s.toFixed(1)}%`,
    series: [
      {
        color: '#e74c3c',
        value: (point) => point.noise_l,
        scale: 'auto',
        scaleId: 'noise',
      },
      {
        color: '#27ae60',
        value: (point) => point.noise_s,
        scale: 'auto',
        scaleId: 'noise',
      },
    ],
  },
  {
    key: 'hdg',
    label: '净对冲',
    color: '#00d2d3',
    format: (point) => `净对冲 ${hand(point.hdg)}`,
    series: [
      {
        color: '#00d2d3',
        value: (point) => point.hdg,
        scale: 'symmetric',
        scaleId: 'hedge',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'dex',
    label: 'DEX方向',
    color: '#e17055',
    format: (point) => `DEX方向 ${signed(point.dex, 3)}`,
    series: [
      {
        color: '#e17055',
        value: (point) => point.dex,
        scale: 'symmetric',
        scaleId: 'dex',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'vanna',
    label: 'Vanna流',
    color: '#b388ff',
    format: (point) => `Vanna流 ${hand(point.vanna)}`,
    series: [
      {
        color: '#b388ff',
        value: (point) => point.vanna,
        scale: 'symmetric',
        scaleId: 'vanna',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'charm',
    label: 'Charm流',
    color: '#fd79a8',
    format: (point) => `Charm流 ${hand(point.charm)}`,
    series: [
      {
        color: '#fd79a8',
        value: (point) => point.charm,
        scale: 'symmetric',
        scaleId: 'charm',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'gam',
    label: 'Gamma场景',
    color: '#f6b93b',
    format: (point) => `Gamma场景 ±1% ${hand(point.gam)}`,
    series: [
      {
        color: '#f6b93b',
        value: (point) => point.gam,
        dash: [4, 2],
        scale: 'symmetric',
        scaleId: 'hedge',
        zeroGap: true,
      },
    ],
  },
  {
    key: 'veg',
    label: 'Vega',
    color: '#cddc39',
    format: (point) =>
      `Vega ${point.veg >= 10000 ? `${(point.veg / 10000).toFixed(1)}万` : Math.round(point.veg)} 元/1%IV`,
    series: [
      {
        color: '#cddc39',
        value: (point) => point.veg,
        scale: 'positive',
        scaleId: 'veg',
        zeroGap: true,
      },
    ],
  },
]

/** 旧页默认只开净对冲和 DEX，其余按需勾选。 */
export const DEFAULT_SUB_VISIBILITY: SubVisibility = Object.fromEntries(
  SUB_GROUPS.map((group) => [group.key, group.key === 'hdg' || group.key === 'dex']),
)

export interface SubFrame {
  start: number
  end: number
  left: number
  right: number
  top: number
  bottom: number
  slot: number
  xAt: (index: number) => number
}

interface ScaleRange {
  min: number
  max: number
  mode: 'auto' | 'symmetric' | 'positive'
}

function isValue(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value)
}

/** 汇总当前可见指标在可视范围内的刻度。 */
export function computeScales(
  points: readonly SubchartPoint[],
  show: SubVisibility,
  frame: SubFrame,
): Map<string, ScaleRange> {
  const scales = new Map<string, ScaleRange>()
  for (const group of SUB_GROUPS) {
    if (!show[group.key]) continue
    for (const series of group.series) {
      const id = series.scaleId ?? group.key
      for (let i = frame.start; i < frame.end; i += 1) {
        const point = points[i]
        if (!point) continue
        const value = series.value(point)
        if (!isValue(value)) continue
        if (series.zeroGap && value === 0) continue
        const current = scales.get(id)
        if (!current) {
          scales.set(id, {
            min: value,
            max: value,
            mode: series.scale ?? 'auto',
          })
        } else {
          current.min = Math.min(current.min, value)
          current.max = Math.max(current.max, value)
        }
      }
    }
  }
  // 统一加边距：对称刻度取绝对值最大，正值刻度从 0 起
  for (const range of scales.values()) {
    if (range.mode === 'symmetric') {
      const abs = Math.max(Math.abs(range.min), Math.abs(range.max)) * 1.15
      if (abs <= 0) {
        range.min = -1
        range.max = 1
      } else {
        range.min = -abs
        range.max = abs
      }
    } else if (range.mode === 'positive') {
      range.min = 0
      range.max = range.max > 0 ? range.max * 1.1 : 1
    } else {
      const pad = (range.max - range.min || 1) * 0.1
      range.min -= pad
      range.max += pad
    }
  }
  return scales
}

function drawSubLine(
  ctx: CanvasRenderingContext2D,
  points: readonly SubchartPoint[],
  series: SubSeries,
  frame: SubFrame,
  yAt: (value: number) => number,
) {
  ctx.save()
  ctx.beginPath()
  ctx.strokeStyle = series.color
  ctx.lineWidth = series.width ?? 0.6
  ctx.globalAlpha = series.alpha ?? 1
  ctx.setLineDash(series.dash ?? [])
  let started = false
  for (let i = frame.start; i < frame.end; i += 1) {
    const point = points[i]
    if (!point) {
      started = false
      continue
    }
    const value = series.value(point)
    if (!isValue(value) || (series.zeroGap && value === 0)) {
      started = false
      continue
    }
    const x = frame.xAt(i)
    const y = yAt(value)
    if (!started) {
      ctx.moveTo(x, y)
      started = true
    } else {
      ctx.lineTo(x, y)
    }
  }
  ctx.stroke()
  ctx.restore()
}

function drawSubBars(
  ctx: CanvasRenderingContext2D,
  points: readonly SubchartPoint[],
  series: SubSeries,
  frame: SubFrame,
  yAt: (value: number) => number,
) {
  const barW = Math.max(1, frame.slot * 0.5)
  ctx.save()
  ctx.globalAlpha = series.alpha ?? 0.6
  const y0 = yAt(0)
  for (let i = frame.start; i < frame.end; i += 1) {
    const point = points[i]
    if (!point) continue
    const value = series.value(point)
    if (!isValue(value) || (series.zeroGap && value === 0)) continue
    const x = frame.xAt(i)
    const y1 = yAt(value)
    ctx.fillStyle = series.sign
      ? value >= 0
        ? series.color
        : DOWN
      : series.color
    ctx.fillRect(x - barW / 2, Math.min(y0, y1), barW, Math.abs(y1 - y0))
  }
  ctx.restore()
}

export function drawSubchart(
  ctx: CanvasRenderingContext2D,
  points: readonly SubchartPoint[],
  show: SubVisibility,
  frame: SubFrame,
) {
  const height = frame.bottom - frame.top
  const scales = computeScales(points, show, frame)
  const yOf = (range: ScaleRange) => (value: number) =>
    frame.bottom - ((value - range.min) / (range.max - range.min || 1)) * height

  // 面板底色与横向网格
  ctx.save()
  ctx.fillStyle = '#09131c'
  ctx.fillRect(frame.left, frame.top, frame.right - frame.left, height)
  // 柱状指标从 0 起画，0 不在刻度内时不能溢出到日期轴
  ctx.beginPath()
  ctx.rect(frame.left, frame.top, frame.right - frame.left, height)
  ctx.clip()
  ctx.strokeStyle = '#1c2833'
  ctx.lineWidth = 1
  for (let g = 0; g <= 4; g += 1) {
    const y = frame.top + (height * g) / 4
    ctx.beginPath()
    ctx.moveTo(frame.left, y)
    ctx.lineTo(frame.right, y)
    ctx.stroke()
  }

  // 有正负刻度的指标画零轴，多条线重合时只画一次
  const zeroYs = new Set<number>()
  for (const range of scales.values()) {
    if (range.min >= 0 || range.max <= 0) continue
    const y = Math.round(yOf(range)(0))
    if (zeroYs.has(y)) continue
    zeroYs.add(y)
    ctx.setLineDash([3, 3])
    ctx.strokeStyle = 'rgba(255,255,255,0.14)'
    ctx.beginPath()
    ctx.moveTo(frame.left, y)
    ctx.lineTo(frame.right, y)
    ctx.stroke()
    ctx.setLineDash([])
  }

  for (const group of SUB_GROUPS) {
    if (!show[group.key]) continue
    for (const series of group.series) {
      const range = scales.get(series.scaleId ?? group.key)
      if (!range) continue
      const yAt = yOf(range)
      if (series.kind === 'bar') {
        drawSubBars(ctx, points, series, frame, yAt)
      } else {
        drawSubLine(ctx, points, series, frame, yAt)
      }
    }
  }
  ctx.restore()
}

/** 悬停面板用：只返回当前开启且有数据的指标。 */
export function subchartReadouts(
  point: SubchartPoint,
  show: SubVisibility,
): { label: string; color: string; value: string }[] {
  return SUB_GROUPS.filter((group) => {
    if (!show[group.key]) return false
    return group.series.some((series) => {
      const value = series.value(point)
      return isValue(value) && !(series.zeroGap && value === 0)
    })
  }).map((group) => ({
    label: group.label,
    color: group.color,
    value: group.format(point),
  }))
}
