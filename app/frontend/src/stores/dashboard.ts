/**
 * 看板筛选范围。
 * Pinia 管响应式，localStorage 管持久化：交易所和已选品种刷新后恢复。
 * 与 market.selectedSymbol 一样，只在 store 里读写 localStorage。
 */
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const EXCHANGE_KEY = 'maxpain.dashboardExchange'
const SYMBOLS_KEY = 'maxpain.dashboardSymbols'

function readSymbols(): string[] {
  try {
    const raw = localStorage.getItem(SYMBOLS_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === 'string')
      : []
  } catch {
    // 本地数据损坏时按未选品种启动
    return []
  }
}

export const useDashboardStore = defineStore('dashboard', () => {
  const exchange = ref(localStorage.getItem(EXCHANGE_KEY) ?? 'all')
  const selected = ref<string[]>(readSymbols())

  watch(exchange, (value) => localStorage.setItem(EXCHANGE_KEY, value))
  watch(
    selected,
    (value) => localStorage.setItem(SYMBOLS_KEY, JSON.stringify(value)),
    { deep: true },
  )

  return { exchange, selected }
})
