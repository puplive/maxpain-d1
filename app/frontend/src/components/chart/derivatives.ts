/**
 * 衍生指标线定义与绘制。
 *
 * 后端已从 oc_chain + 显示带宽算好逐日值，这里只负责颜色、开关分组和画阶梯线。
 * 开关按“组”管理：Wall 同时控制 Call/Put，OI 重心同时控制合计/Call/Put。
 */
import type { DerivativePoint } from '../../api/client'
import { drawStepSeries, type SeriesFrame } from './draw'

export type DerivativeKey = Exclude<keyof DerivativePoint, 'd'>

export interface DerivativeLine {
  key: DerivativeKey
  name: string
  color: string
  width: number
  dash?: number[]
}

export interface DerivativeGroup {
  key: string
  label: string
  color: string
  lines: DerivativeLine[]
}

// 颜色沿用旧回测页
export const DERIVATIVE_GROUPS: DerivativeGroup[] = [
  {
    key: 'gpk',
    label: 'Gamma峰值',
    color: '#ff6b81',
    lines: [{ key: 'gpk', name: 'Gamma', color: '#ff6b81', width: 0.6 }],
  },
  {
    key: 'wall',
    label: 'Call/Put Wall',
    color: '#ff8c42',
    lines: [
      { key: 'cw', name: 'Call', color: '#ff8c42', width: 0.6 },
      { key: 'pw', name: 'Put', color: '#2ecc71', width: 0.6 },
    ],
  },
  {
    key: 'zg',
    label: 'ZeroGamma',
    color: '#b388ff',
    lines: [
      { key: 'zg', name: 'ZG', color: '#b388ff', width: 0.6, dash: [4, 2] },
    ],
  },
  {
    key: 'mag',
    label: 'Magnet/Pin',
    color: '#74b9ff',
    lines: [
      { key: 'magnet', name: 'Magnet', color: '#74b9ff', width: 0.6 },
      { key: 'pin', name: 'Pin', color: '#fdcb6e', width: 0.6, dash: [2, 2] },
    ],
  },
  {
    key: 'd25',
    label: 'Δ25',
    color: '#5cd85a',
    lines: [
      { key: 'd25c', name: 'Call', color: '#5cd85a', width: 0.6 },
      { key: 'd25p', name: 'Put', color: '#ff6b6b', width: 0.6 },
    ],
  },
  {
    key: 'oic',
    label: 'OI重心',
    color: '#00d2d3',
    lines: [
      { key: 'oicc', name: 'Call', color: '#00d2d3', width: 0.6 },
      { key: 'oicp', name: 'Put', color: '#e056fd', width: 0.6 },
      { key: 'oic', name: '合计', color: '#ffffff', width: 0.6 },
    ],
  },
]

/** 把开启的衍生线并入价格轴范围，隐藏的线不撑大坐标。 */
export function expandPriceRange(
  points: readonly DerivativePoint[],
  start: number,
  end: number,
  show: Record<string, boolean>,
  lo: number,
  hi: number,
): [number, number] {
  for (const group of DERIVATIVE_GROUPS) {
    if (!show[group.key]) continue
    for (let i = start; i < end; i += 1) {
      const point = points[i]
      if (!point) continue
      for (const line of group.lines) {
        const value = point[line.key]
        if (value == null || !Number.isFinite(value)) continue
        lo = Math.min(lo, value)
        hi = Math.max(hi, value)
      }
    }
  }
  return [lo, hi]
}

export function drawDerivatives(
  ctx: CanvasRenderingContext2D,
  points: readonly DerivativePoint[],
  show: Record<string, boolean>,
  frame: SeriesFrame,
  slot: number,
) {
  for (const group of DERIVATIVE_GROUPS) {
    if (!show[group.key]) continue
    for (const line of group.lines) {
      drawStepSeries(
        ctx,
        points,
        (point) => point[line.key],
        line.color,
        line.width,
        frame,
        slot,
        line.dash ?? [],
      )
    }
  }
}
