/**
 * 复盘图通用绘制工具。
 *
 * 所有序列都按 bars / derivatives 的相同下标取值，null 处断开；
 * 行权价类指标用阶梯线，价格类指标用普通折线。
 */

export interface SeriesFrame {
  start: number
  end: number
  xAt: (index: number) => number
  yAt: (value: number) => number
}

/** 普通折线，空值断开。 */
export function drawLineSeries(
  ctx: CanvasRenderingContext2D,
  values: readonly (number | null | undefined)[],
  color: string,
  lineWidth: number,
  frame: SeriesFrame,
  dash: number[] = [],
) {
  ctx.beginPath()
  ctx.strokeStyle = color
  ctx.lineWidth = lineWidth
  ctx.setLineDash(dash)
  let started = false
  for (let i = frame.start; i < frame.end; i += 1) {
    const value = values[i]
    if (value == null || !Number.isFinite(value)) {
      started = false
      continue
    }
    const x = frame.xAt(i)
    const y = frame.yAt(value)
    if (!started) {
      ctx.moveTo(x, y)
      started = true
    } else {
      ctx.lineTo(x, y)
    }
  }
  ctx.stroke()
  ctx.setLineDash([])
}

/** 阶梯线：每天画一段短横线，相邻日之间补竖线，和旧页衍生线画法一致。 */
export function drawStepSeries<T>(
  ctx: CanvasRenderingContext2D,
  items: readonly T[],
  valueOf: (item: T) => number | null | undefined,
  color: string,
  lineWidth: number,
  frame: SeriesFrame,
  slot: number,
  dash: number[] = [],
) {
  ctx.save()
  ctx.strokeStyle = color
  ctx.lineWidth = lineWidth
  ctx.setLineDash(dash)
  const half = Math.max(slot, 2) / 2
  let prevY: number | null = null
  let prevIndex = -2
  for (let i = frame.start; i < frame.end; i += 1) {
    const value = valueOf(items[i])
    if (value == null || !Number.isFinite(value)) {
      prevY = null
      continue
    }
    const x = frame.xAt(i)
    const y = frame.yAt(value)
    ctx.beginPath()
    ctx.moveTo(x - half, y)
    ctx.lineTo(x + half, y)
    ctx.stroke()
    if (prevY !== null && i === prevIndex + 1) {
      ctx.beginPath()
      ctx.moveTo(x - half, prevY)
      ctx.lineTo(x - half, y)
      ctx.stroke()
    }
    prevY = y
    prevIndex = i
  }
  ctx.restore()
}
