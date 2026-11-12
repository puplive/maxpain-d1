/**
 * 复盘图标记层：
 * - 期权到期倒计时（到期前 10 天在 K 线顶部标数字）
 * - 期货换月高峰期色带（主力合约交割月前一个月）和换月点三角
 * - 开仓/平仓标记、准确率告警红 X
 */
import type {
  BacktestPosition,
  BacktestTrade,
  ChartBar,
  IndicatorPoint,
} from '../../api/client'
import type { SeriesFrame } from './draw'

export interface RollRegion {
  start: number
  end: number
  contract: string
}

/** 换月高峰期按日历月份判断：交割月前一个月，如 TA701 → 12 月。 */
export function computeRollRegions(bars: readonly ChartBar[]): RollRegion[] {
  const regions: RollRegion[] = []
  let runStart = -1
  for (let i = 0; i <= bars.length; i += 1) {
    const contract = i < bars.length ? bars[i].fut_contract : null
    let inPeak = false
    if (contract) {
      const deliveryMonth = Number.parseInt(contract.slice(-2), 10)
      if (deliveryMonth >= 1 && deliveryMonth <= 12) {
        const month = Number.parseInt(bars[i].d.slice(5, 7), 10)
        inPeak = month === (deliveryMonth === 1 ? 12 : deliveryMonth - 1)
      }
    }
    if (inPeak && runStart < 0) runStart = i
    if (!inPeak && runStart >= 0) {
      regions.push({
        start: runStart,
        end: i - 1,
        contract: bars[runStart].fut_contract ?? '',
      })
      runStart = -1
    }
  }
  return regions
}

/** 换月高峰期整段压暗，并在左上角标出即将切换到的合约。 */
export function drawRollRegions(
  ctx: CanvasRenderingContext2D,
  regions: readonly RollRegion[],
  frame: SeriesFrame,
  priceTop: number,
  priceBottom: number,
  slot: number,
) {
  const half = Math.max(slot, 2) / 2
  ctx.save()
  ctx.font = 'lighter 10px ui-monospace, Menlo, monospace'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'alphabetic'
  for (const region of regions) {
    const start = Math.max(frame.start, region.start)
    const end = Math.min(frame.end - 1, region.end)
    if (start > end) continue
    const x0 = frame.xAt(start) - half
    const x1 = frame.xAt(end) + half
    ctx.fillStyle = 'rgba(0, 0, 0, 0.4)'
    ctx.fillRect(x0, priceTop, x1 - x0, priceBottom - priceTop)
    if (x0 >= 8) {
      ctx.fillStyle = '#e8c84a'
      ctx.fillText(`${region.contract} 换月`, x0 + 3, priceTop + 10)
    }
  }
  ctx.restore()
}

/** 期货主力合约变化当天，在价格面板顶部画空心三角 + “换”。 */
export function drawRollMarkers(
  ctx: CanvasRenderingContext2D,
  bars: readonly ChartBar[],
  frame: SeriesFrame,
  priceTop: number,
) {
  ctx.save()
  ctx.strokeStyle = '#fdcb6e'
  ctx.fillStyle = '#fdcb6e'
  ctx.lineWidth = 0.4
  ctx.font = 'lighter 7px ui-monospace, Menlo, monospace'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (let i = Math.max(1, frame.start); i < frame.end; i += 1) {
    const contract = bars[i].fut_contract
    const prev = bars[i - 1].fut_contract
    if (!contract || !prev || contract === prev) continue
    const x = frame.xAt(i)
    ctx.beginPath()
    ctx.moveTo(x, priceTop + 12)
    ctx.lineTo(x - 6, priceTop + 2)
    ctx.lineTo(x + 6, priceTop + 2)
    ctx.closePath()
    ctx.stroke()
    ctx.fillText('换', x, priceTop + 5.5)
  }
  ctx.restore()
}

/** 期权到期倒计时：越临近到期颜色越红。 */
export function drawExpiryCountdown(
  ctx: CanvasRenderingContext2D,
  bars: readonly ChartBar[],
  frame: SeriesFrame,
  priceTop: number,
) {
  ctx.save()
  ctx.font = 'bold 7px ui-monospace, Menlo, monospace'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (let i = frame.start; i < frame.end; i += 1) {
    const dte = bars[i].dte
    if (dte == null || dte < 0 || dte > 10) continue
    ctx.fillStyle = dte <= 2 ? '#ff6b6b' : dte <= 5 ? '#fdcb6e' : '#54a0ff'
    ctx.fillText(String(dte), frame.xAt(i), priceTop - 6)
  }
  ctx.restore()
}

/**
 * 开平仓标记：价位虚线 + 空心三角，文字居中放在三角外侧。
 * 开仓 多=尖朝上、空=尖朝下；平仓方向与开仓相反，沿用旧页口径。
 */
export function drawTradeMarkers(
  ctx: CanvasRenderingContext2D,
  trades: readonly BacktestTrade[],
  frame: SeriesFrame,
  indexByDate: Map<string, number>,
  slot: number,
  openPosition?: BacktestPosition | null,
) {
  const hw = Math.max(10, Math.min(32, slot * 0.9))
  const tipSz = Math.max(5, Math.min(10, slot * 0.55))
  const labelOff = tipSz * 1.6 + 11
  const drawMark = (
    x: number,
    priceY: number,
    up: boolean,
    label: string,
  ) => {
    ctx.strokeStyle = 'rgba(255,255,255,0.3)'
    ctx.lineWidth = 0.4
    ctx.setLineDash([3, 3])
    ctx.beginPath()
    ctx.moveTo(x - hw, priceY)
    ctx.lineTo(x + hw, priceY)
    ctx.stroke()
    ctx.setLineDash([])

    // 尖头顶在价位线上，底边朝外
    ctx.strokeStyle = 'rgba(255,255,255,0.8)'
    ctx.lineJoin = 'round'
    ctx.beginPath()
    if (up) {
      ctx.moveTo(x, priceY - 1)
      ctx.lineTo(x - tipSz, priceY + tipSz * 1.6)
      ctx.lineTo(x + tipSz, priceY + tipSz * 1.6)
    } else {
      ctx.moveTo(x, priceY + 1)
      ctx.lineTo(x - tipSz, priceY - tipSz * 1.6)
      ctx.lineTo(x + tipSz, priceY - tipSz * 1.6)
    }
    ctx.closePath()
    ctx.stroke()

    ctx.fillStyle = 'rgba(255,255,255,0.8)'
    ctx.fillText(label, x, priceY + (up ? labelOff : -labelOff + 6))
  }

  ctx.save()
  ctx.font = `bold ${Math.max(9, Math.min(12, slot * 0.7))}px sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'alphabetic'
  for (const trade of trades) {
    const entryIndex = indexByDate.get(trade.entry_date)
    if (entryIndex != null && entryIndex >= frame.start && entryIndex < frame.end) {
      drawMark(
        frame.xAt(entryIndex),
        frame.yAt(trade.entry_px),
        trade.direction === 'long',
        trade.direction === 'long' ? '多' : '空',
      )
    }
    const exitIndex = indexByDate.get(trade.exit_date)
    if (exitIndex != null && exitIndex >= frame.start && exitIndex < frame.end) {
      drawMark(
        frame.xAt(exitIndex),
        frame.yAt(trade.exit_px),
        trade.direction !== 'long',
        '平',
      )
    }
  }
  // 期末未平仓不在 trades 里，单独补开仓标记
  if (openPosition) {
    const entryIndex = indexByDate.get(openPosition.entry_date)
    if (entryIndex != null && entryIndex >= frame.start && entryIndex < frame.end) {
      drawMark(
        frame.xAt(entryIndex),
        frame.yAt(openPosition.entry_px),
        openPosition.direction === 'long',
        openPosition.direction === 'long' ? '多' : '空',
      )
    }
  }
  ctx.restore()
}

/** 准确率告警：价格面板底部红色小 X。 */
export function drawWarnMarks(
  ctx: CanvasRenderingContext2D,
  indicators: readonly IndicatorPoint[],
  frame: SeriesFrame,
  y: number,
  slot: number,
) {
  const half = Math.max(2, Math.min(3.5, slot * 0.35))
  ctx.save()
  ctx.strokeStyle = '#e74c3c'
  ctx.lineWidth = 0.4
  for (let i = frame.start; i < frame.end; i += 1) {
    if (!indicators[i]?.warn) continue
    const x = frame.xAt(i)
    ctx.beginPath()
    ctx.moveTo(x - half, y - half)
    ctx.lineTo(x + half, y + half)
    ctx.moveTo(x + half, y - half)
    ctx.lineTo(x - half, y + half)
    ctx.stroke()
  }
  ctx.restore()
}
