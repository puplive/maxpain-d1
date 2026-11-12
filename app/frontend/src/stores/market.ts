/**
 * 行情基础状态。
 * 品种列表全局只拉一次，看板、参数页共用，避免重复请求。
 */
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { api, type SymbolInfo } from '../api/client'

const SELECTED_KEY = 'maxpain.selectedSymbol'

export const useMarketStore = defineStore('market', () => {
  const symbols = ref<SymbolInfo[]>([])
  const loaded = ref(false)
  // 回测页选中的品种，写 localStorage，刷新后仍保留
  const selectedSymbol = ref(localStorage.getItem(SELECTED_KEY) ?? '')

  watch(selectedSymbol, (value) => {
    if (value) localStorage.setItem(SELECTED_KEY, value)
    else localStorage.removeItem(SELECTED_KEY)
  })

  /** 首次调用时请求后端，之后直接返回缓存。 */
  async function ensureSymbols() {
    if (!loaded.value) {
      symbols.value = await api.symbols()
      loaded.value = true
    }
    return symbols.value
  }

  return { symbols, loaded, selectedSymbol, ensureSymbols }
})
