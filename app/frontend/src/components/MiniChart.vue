<script setup lang="ts">
/**
 * 看板用迷你图：K 线 + MP 虚线 + Gamma 峰值实线。
 *
 * 绘制流程：
 * 1. 按品种拉最近 90 根日线（含 gpk）
 * 2. 容器尺寸变化时重绘（ResizeObserver）
 * 3. 根据 devicePixelRatio 放大 canvas 像素，保证高分屏不模糊
 * 4. 先画背景网格，再把价格线性映射到画布坐标画 K 线和指标线
 *
 * 后续完整图表组件会复用同样的尺寸/DPR/坐标处理方式。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, type BarPoint } from '../api/client'

const props = defineProps<{ symbol: string }>()
// 最新收盘价交给卡片标题显示，画在图里会压住 K 线
const emit = defineEmits<{ (event: 'latest', price: number): void }>()
const canvasRef = ref<HTMLCanvasElement | null>(null)
let bars: BarPoint[] = []
let observer: ResizeObserver | null = null

function draw() {
  const canvas = canvasRef.value
  if (!canvas || bars.length === 0) return

  // CSS 尺寸用于布局，canvas 实际像素要乘以 DPR 才清晰
  const rect = canvas.getBoundingClientRect()
  const width = Math.max(1, Math.floor(rect.width))
  const height = Math.max(1, Math.floor(rect.height))
  const dpr = window.devicePixelRatio || 1
  if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
    canvas.width = width * dpr
    canvas.height = height * dpr
  }

  const ctx = canvas.getContext('2d')
  if (!ctx) return
  // 把坐标系缩放到 CSS 像素，后面按 0..width / 0..height 画即可
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)

  // K 线高低点和两条指标线一起定纵轴范围
  const values = bars
    .flatMap((bar) => [bar.l, bar.h, bar.mp, bar.gpk])
    .filter(
      (value): value is number =>
        typeof value === 'number' && Number.isFinite(value),
    )
  if (values.length === 0) return

  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 6
  // 按等宽槽位定位，K 线以槽位中心为基准，首尾不会被裁掉
  const slot = (width - pad * 2) / Math.max(1, bars.length)
  const xAt = (index: number) => pad + (index + 0.5) * slot
  const yAt = (value: number) =>
    pad + (1 - (value - min) / span) * (height - pad * 2)

  // 背景横向网格，给迷你图一点纵深感
  ctx.save()
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)'
  ctx.lineWidth = 1
  for (let i = 0; i < 4; i++) {
    const y = pad + ((height - pad * 2) / 3) * i
    ctx.beginPath()
    ctx.moveTo(pad, y)
    ctx.lineTo(width - pad, y)
    ctx.stroke()
  }
  ctx.restore()

  // K 线：涨绿跌红，和回测图配色一致
  const bodyWidth = Math.max(1, Math.min(5, slot * 0.6))
  bars.forEach((bar, index) => {
    const x = xAt(index)
    const color = bar.c >= bar.o ? '#5cd85a' : '#ff6b6b'
    ctx.strokeStyle = color
    ctx.fillStyle = color
    // 上下影线
    ctx.beginPath()
    ctx.moveTo(x, yAt(bar.h))
    ctx.lineTo(x, yAt(bar.l))
    ctx.stroke()
    // 实体至少 1px，避免十字星消失
    const top = yAt(Math.max(bar.o, bar.c))
    const bottom = yAt(Math.min(bar.o, bar.c))
    ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, Math.max(1, bottom - top))
  })

  // 指标线允许缺失，遇到 null 断开而不是连到 0
  // 用阶梯线：每天的值保持到下一根 K 线，再竖跳到新值
  const drawSeries = (
    getter: (bar: BarPoint) => number | null,
    color: string,
    dash: number[] = [],
  ) => {
    ctx.save()
    ctx.strokeStyle = color
    ctx.lineWidth = 0.6
    ctx.setLineDash(dash)
    ctx.beginPath()
    let started = false
    let prevY = 0
    bars.forEach((bar, index) => {
      const value = getter(bar)
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        started = false
        return
      }
      const x = xAt(index)
      const y = yAt(value)
      if (started) {
        ctx.lineTo(x, prevY)
        ctx.lineTo(x, y)
      } else {
        ctx.moveTo(x, y)
        started = true
      }
      prevY = y
    })
    ctx.stroke()
    ctx.restore()
  }

  // MP 橙色虚线阶梯，Gamma 峰值粉色实线阶梯
  drawSeries((bar) => bar.mp, '#ff9f43', [3, 3])
  drawSeries((bar) => bar.gpk, '#ff6b81')

  // 左上角图例
  ctx.save()
  ctx.font = '9px sans-serif'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  const legend = [
    { label: 'MP', color: '#ff9f43', dash: [2, 2] },
    { label: 'Γ峰', color: '#ff6b81', dash: [] },
  ]
  let legendX = pad + 2
  legend.forEach((item) => {
    ctx.strokeStyle = item.color
    ctx.setLineDash(item.dash)
    ctx.beginPath()
    ctx.moveTo(legendX, 9)
    ctx.lineTo(legendX + 10, 9)
    ctx.stroke()
    ctx.fillStyle = item.color
    ctx.fillText(item.label, legendX + 13, 9)
    legendX += 13 + ctx.measureText(item.label).width + 8
  })
  ctx.restore()
}

async function load() {
  bars = await api.bars(props.symbol, 90)
  draw()
  const last = bars[bars.length - 1]
  if (last) emit('latest', last.c)
}

onMounted(() => {
  void load()
  // 卡片宽度随窗口变化，容器尺寸一变就重绘
  observer = new ResizeObserver(() => draw())
  if (canvasRef.value) observer.observe(canvasRef.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
})

// 看板里同一个组件会切换品种，切换时重新拉数据
watch(
  () => props.symbol,
  () => {
    void load()
  },
)
</script>

<template>
  <canvas ref="canvasRef" class="mini-chart" />
</template>
