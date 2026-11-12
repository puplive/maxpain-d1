/**
 * 复盘图下方 OI 分布（旧页附图2 #oiChart）。
 *
 * 数据来自 /backtest/{symbol}/chain?date=，只画当前悬停那天的期权链：
 * 上半区绿色柱=Call 持仓，下半区红色柱=Put 持仓；打开 Δ 对冲后柱高换成
 * |Δ×OI|，即做市商需要买卖的期货手数。叠加现价/痛点虚线、Δ 曲线和 IV 微笑。
 * 面板横轴是行权价而不是日期，所以不跟主图共用 x 轴；鼠标在面板内按行权价
 * 找最近一档，显示该行权价的持仓与对冲明细。
 */
import type { OiChainPoint } from '../../api/client'

/** OI 面板的绘制区域；顶部 OI_HEADER 留给 HTML 概览条。 */
export interface OiFrame {
  left: number
  right: number
  top: number
  bottom: number
}

export interface OiOptions {
  /** 当日收盘价，画现价虚线并决定 IV 微笑取哪一侧 */
  price: number | null
  /** 当日痛点，画紫色虚线 */
  mp: number | null
  /** Δ加权模式：柱高用 cdw/pdw 而不是原始持仓 */
  hedge: boolean
  /** 鼠标在画布上的横坐标；不在面板内传 null */
  hoverX: number | null
}

/** 顶部留给 HTML 概览条（标的作用说明 + Δ对冲开关）的高度 */
export const OI_HEADER = 14

/** 该日链里是否有 Δ 加权字段，决定 Δ对冲开关是否有效。 */
export function hasHedgeData(chain: readonly OiChainPoint[] | null | undefined): boolean {
  return !!chain?.some((item) => item.cdw != null || item.pdw != null)
}

/** 该日链里是否有 IV 数据，用于概览条提示 CallIV/PutIV 配色。 */
export function hasIvSmile(chain: readonly OiChainPoint[] | null | undefined): boolean {
  return (
    !!chain?.some(
      (item) => (item.civ != null && item.civ > 0) || (item.piv != null && item.piv > 0),
    )
  )
}

export function drawOiChart(
  ctx: CanvasRenderingContext2D,
  chain: readonly OiChainPoint[] | null | undefined,
  frame: OiFrame,
  options: OiOptions,
) {
  const panelW = frame.right - frame.left
  const panelH = frame.bottom - frame.top
  ctx.save()
  ctx.fillStyle = '#09131c'
  ctx.fillRect(frame.left, frame.top, panelW, panelH)
  ctx.font = '10px ui-monospace, Menlo, monospace'

  // chain 为 null 表示还在加载；空数组表示该日没有期权链
  if (!chain) {
    drawHint(ctx, 'OI 分布加载中…', frame)
    ctx.restore()
    return
  }
  if (chain.length === 0) {
    drawHint(ctx, '该日无期权链数据', frame)
    ctx.restore()
    return
  }

  const price = options.price
  const spot = price ?? chain[0].s
  const useDw = options.hedge && hasHedgeData(chain)
  const callValue = (item: OiChainPoint) =>
    useDw ? Math.abs(item.cdw ?? 0) : (item.co ?? 0)
  const putValue = (item: OiChainPoint) =>
    useDw ? Math.abs(item.pdw ?? 0) : (item.po ?? 0)
  const ivSmile = hasIvSmile(chain)
  const hasDelta = chain.some(
    (item) => Math.abs(item.cd ?? 0) > 0.05 || Math.abs(item.pd ?? 0) > 0.05,
  )

  // 上方留一行概览条，底部留行权价标签，有 IV 微笑时右侧留纵轴刻度
  const top = frame.top + OI_HEADER + 3
  const bottom = frame.bottom - 14
  const left = frame.left + 4
  const right = frame.right - (ivSmile ? 32 : 4)
  const chartH = Math.max(10, bottom - top)
  const midY = top + chartH / 2
  const minS = chain[0].s
  const maxS = chain[chain.length - 1].s
  const sSpan = maxS - minS || 1
  const s2x = (strike: number) => left + ((strike - minS) / sSpan) * (right - left)

  let maxOi = 1
  for (const item of chain) {
    maxOi = Math.max(maxOi, callValue(item), putValue(item))
  }

  // 中轴基线
  ctx.strokeStyle = '#2d3436'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(left, midY)
  ctx.lineTo(right, midY)
  ctx.stroke()

  // 现价虚线
  ctx.strokeStyle = '#e8c84a'
  ctx.setLineDash([3, 3])
  const pxX = s2x(spot)
  ctx.beginPath()
  ctx.moveTo(pxX, top)
  ctx.lineTo(pxX, bottom)
  ctx.stroke()
  ctx.setLineDash([])

  // 痛点虚线
  if (options.mp != null) {
    ctx.strokeStyle = '#a29bfe'
    ctx.lineWidth = 4
    ctx.setLineDash([5, 3])
    const mpX = s2x(options.mp)
    ctx.beginPath()
    ctx.moveTo(mpX, top)
    ctx.lineTo(mpX, bottom)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.lineWidth = 1
  }

  // Call 柱向上、Put 柱向下，共用同一最大柱高
  const barW = Math.max(2, ((right - left) / chain.length) * 0.6)
  for (const item of chain) {
    const bx = s2x(item.s)
    const callOi = callValue(item)
    const putOi = putValue(item)
    if (callOi > 0) {
      const bh = (callOi / maxOi) * (chartH / 2)
      ctx.fillStyle = 'rgba(92,216,90,0.55)'
      ctx.fillRect(bx - barW / 2, midY - bh, barW, bh)
    }
    if (putOi > 0) {
      const bh = (putOi / maxOi) * (chartH / 2)
      ctx.fillStyle = 'rgba(255,107,107,0.55)'
      ctx.fillRect(bx - barW / 2, midY, barW, bh)
    }
  }

  // Δ 曲线：CallΔ 画在上半区，PutΔ 画在下半区（0 在中轴）
  if (useDw && hasDelta) {
    const drawDelta = (key: 'cd' | 'pd', sign: number) => {
      ctx.beginPath()
      let started = false
      for (const item of chain) {
        const value = item[key]
        if (value == null) continue
        const a = Math.min(1, Math.max(0, sign > 0 ? -value : value))
        const x = s2x(item.s)
        const y = midY + sign * a * (chartH / 2)
        if (!started) {
          ctx.moveTo(x, y)
          started = true
        } else {
          ctx.lineTo(x, y)
        }
      }
      ctx.stroke()
    }
    // 先描深色边，压在柱子上仍可辨
    ctx.lineWidth = 1
    ctx.strokeStyle = 'rgba(10,22,40,0.85)'
    drawDelta('cd', -1)
    drawDelta('pd', 1)
    ctx.lineWidth = 0.8
    ctx.strokeStyle = 'rgba(92,216,90,0.7)'
    drawDelta('cd', -1)
    ctx.strokeStyle = 'rgba(255,107,107,0.7)'
    drawDelta('pd', 1)
  }

  // IV 微笑：Call 只画虚值侧(K≥现价)，Put 只画 K≤现价，右侧给刻度
  let ivMin = 1
  let ivMax = 0
  if (ivSmile) {
    for (const item of chain) {
      const civ = item.s >= spot ? item.civ : null
      const piv = item.s <= spot ? item.piv : null
      if (civ != null && civ > 0) {
        ivMin = Math.min(ivMin, civ)
        ivMax = Math.max(ivMax, civ)
      }
      if (piv != null && piv > 0) {
        ivMin = Math.min(ivMin, piv)
        ivMax = Math.max(ivMax, piv)
      }
    }
    if (ivMax > 0) {
      const ivSpan = ivMax - ivMin || 0.01
      const iv2y = (iv: number) => bottom - ((iv - ivMin) / ivSpan) * chartH
      const drawSmile = (key: 'civ' | 'piv', otm: (strike: number) => boolean) => {
        ctx.beginPath()
        let started = false
        for (const item of chain) {
          if (!otm(item.s)) continue
          const value = item[key]
          if (value == null || value <= 0) continue
          const x = s2x(item.s)
          const y = iv2y(value)
          if (!started) {
            ctx.moveTo(x, y)
            started = true
          } else {
            ctx.lineTo(x, y)
          }
        }
        ctx.stroke()
      }
      ctx.lineWidth = 1
      ctx.strokeStyle = '#3498db'
      drawSmile('civ', (strike) => strike >= spot)
      ctx.strokeStyle = '#e67e22'
      drawSmile('piv', (strike) => strike <= spot)

      ctx.fillStyle = '#576574'
      ctx.font = '8px sans-serif'
      ctx.textAlign = 'right'
      ctx.textBaseline = 'middle'
      for (let i = 0; i <= 4; i += 1) {
        const iv = ivMin + (ivSpan * i) / 4
        const y = iv2y(iv)
        if (y >= top && y <= bottom) {
          ctx.fillText(`${(iv * 100).toFixed(1)}%`, frame.right - 2, y)
        }
      }
      // 右上角标出两条曲线各自颜色
      ctx.textAlign = 'left'
      ctx.fillStyle = '#3498db'
      ctx.fillText('CallIV', left + 2, top + 3)
      ctx.fillStyle = '#e67e22'
      ctx.fillText('PutIV', left + 2, top + 13)
    }
  }

  // 行权价标签，等距抽 10 个左右
  ctx.fillStyle = '#576574'
  ctx.font = '9px sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'bottom'
  const sStep = Math.max(1, Math.floor(chain.length / 10))
  for (let i = 0; i < chain.length; i += sStep) {
    ctx.fillText(String(chain[i].s), s2x(chain[i].s), frame.bottom - 2)
  }

  // Δ 加权模式标出 ±0.25/±0.10 分界，实线=0.25，虚线=0.10（更虚值）
  if (useDw && hasDelta) {
    const findStrike = (key: 'cd' | 'pd', target: number) => {
      let best: number | null = null
      let bestDist = Infinity
      for (const item of chain) {
        const value = item[key]
        if (value == null) continue
        const dist = Math.abs(value - target)
        if (dist < bestDist) {
          bestDist = dist
          best = item.s
        }
      }
      return best
    }
    const marks: [number | null, string, number, boolean, string][] = [
      [findStrike('cd', 0.25), '#5cd85a', 2, false, 'CΔ.25'],
      [findStrike('cd', 0.1), 'rgba(92,216,90,0.85)', 1.5, true, 'CΔ.10'],
      [findStrike('pd', -0.25), '#ff6b6b', 2, false, 'PΔ-.25'],
      [findStrike('pd', -0.1), 'rgba(255,107,107,0.85)', 1.5, true, 'PΔ-.10'],
    ]
    marks.forEach(([strike, color, lineWidth, dashed, tag], index) => {
      if (strike == null) return
      const x = s2x(strike)
      ctx.strokeStyle = color
      ctx.lineWidth = lineWidth
      ctx.setLineDash(dashed ? [4, 2] : [])
      ctx.beginPath()
      ctx.moveTo(x, top)
      ctx.lineTo(x, bottom)
      ctx.stroke()
      ctx.setLineDash([])
      // Call 标记放顶部，Put 标记放底部
      ctx.font = '9px sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      const textW = ctx.measureText(tag).width + 6
      const y = index < 2 ? top + 7 : bottom - 7
      ctx.fillStyle = 'rgba(10,22,40,0.9)'
      ctx.fillRect(x - textW / 2, y - 6, textW, 12)
      ctx.fillStyle = color
      ctx.fillText(tag, x, y)
    })
    ctx.textAlign = 'left'
    ctx.textBaseline = 'alphabetic'
    ctx.lineWidth = 1
  }

  // 悬停：按横坐标找最近行权价，高亮并显示该档明细（概览条会被标签盖住）
  if (options.hoverX != null) {
    const strikeAt = minS + ((options.hoverX - left) / (right - left)) * sSpan
    let hoverIdx = 0
    let hoverDist = Infinity
    for (let i = 0; i < chain.length; i += 1) {
      const dist = Math.abs(chain[i].s - strikeAt)
      if (dist < hoverDist) {
        hoverDist = dist
        hoverIdx = i
      }
    }
    const item = chain[hoverIdx]
    const x = s2x(item.s)
    ctx.save()
    ctx.strokeStyle = 'rgba(255,255,255,0.15)'
    ctx.lineWidth = 1
    ctx.setLineDash([2, 2])
    ctx.beginPath()
    ctx.moveTo(x, top)
    ctx.lineTo(x, bottom)
    ctx.stroke()
    ctx.setLineDash([])

    let label = `${item.s}  Call:${Math.round(item.co).toLocaleString()}`
    if (item.cd != null) label += `(Δ${item.cd.toFixed(2)})`
    label += `  Put:${Math.round(item.po).toLocaleString()}`
    if (item.pd != null) label += `(Δ${item.pd.toFixed(2)})`
    if (item.cdw != null || item.pdw != null) {
      const net = Math.round((item.cdw ?? 0) + (item.pdw ?? 0))
      label += `  做市商对冲: 买${Math.round(item.cdw ?? 0).toLocaleString()}手 卖${Math.abs(Math.round(item.pdw ?? 0)).toLocaleString()}手 净${net < 0 ? '卖' : '买'}${Math.abs(net).toLocaleString()}手`
    } else {
      label += `  合计:${Math.round(item.co + item.po).toLocaleString()}`
    }
    if (useDw) label += ' [对冲手数]'

    ctx.font = '10px ui-monospace, Menlo, monospace'
    const textW = ctx.measureText(label).width
    let lx = x - textW / 2 - 6
    lx = Math.max(2, Math.min(lx, frame.right - textW - 14))
    ctx.fillStyle = 'rgba(10,22,40,0.85)'
    ctx.fillRect(lx, frame.top + OI_HEADER + 2, textW + 12, 18)
    ctx.strokeStyle = '#576574'
    ctx.strokeRect(lx, frame.top + OI_HEADER + 2, textW + 12, 18)
    ctx.fillStyle = '#fff'
    ctx.textAlign = 'left'
    ctx.textBaseline = 'alphabetic'
    ctx.fillText(label, lx + 6, frame.top + OI_HEADER + 15)
    ctx.restore()
  }

  ctx.restore()
}

function drawHint(
  ctx: CanvasRenderingContext2D,
  text: string,
  frame: OiFrame,
) {
  ctx.fillStyle = '#576574'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(
    text,
    frame.left + (frame.right - frame.left) / 2,
    frame.top + (frame.bottom - frame.top) / 2,
  )
}
