<script setup lang="ts">
/**
 * 今日机会。
 * 表格数据来自 daily_signals；扫描按钮会调用后端 /api/scan，
 * 后端遍历全部品种、用 MaxPain v5 生成次日信号后写入数据库。
 */
import { computed, h, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { api, type SignalRow } from '../api/client'

const message = useMessage()
const rows = ref<SignalRow[]>([])
const loading = ref(false)
const scanning = ref(false)

// 顶部用于快速看多空数量
const longCount = computed(
  () => rows.value.filter((row) => row.direction === 'long').length,
)
const shortCount = computed(
  () => rows.value.filter((row) => row.direction === 'short').length,
)

// 列定义：方向用颜色标签区分，数值列空值显示 "-"
const columns: DataTableColumns<SignalRow> = [
  { title: '交易日', key: 'trade_date', width: 110 },
  { title: '策略', key: 'strategy', width: 130 },
  { title: '品种', key: 'symbol', width: 80 },
  {
    title: '方向',
    key: 'direction',
    width: 80,
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
  {
    title: '现价',
    key: 'price',
    width: 100,
    render: (row) => (row.price == null ? '-' : row.price.toFixed(0)),
  },
  {
    title: 'MP',
    key: 'mp',
    width: 100,
    render: (row) => (row.mp == null ? '-' : row.mp.toFixed(0)),
  },
  {
    title: '止损',
    key: 'stop',
    width: 100,
    render: (row) => (row.stop == null ? '-' : row.stop.toFixed(0)),
  },
  { title: '手数', key: 'qty', width: 80 },
  { title: '说明', key: 'reason', minWidth: 160 },
]

async function load() {
  loading.value = true
  try {
    rows.value = await api.signals()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载信号失败')
  } finally {
    loading.value = false
  }
}

async function runScan() {
  scanning.value = true
  try {
    const result = await api.scan()
    message.success(result.message)
    // 扫描成功后再刷新表格
    await load()
  } catch (error) {
    message.warning(error instanceof Error ? error.message : '扫描失败')
  } finally {
    scanning.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="toolbar">
    <n-button size="small" :loading="scanning" @click="runScan">运行扫描</n-button>
    <div class="tag-row">
      <n-tag size="small" :bordered="false" type="success">多 {{ longCount }}</n-tag>
      <n-tag size="small" :bordered="false" type="error">空 {{ shortCount }}</n-tag>
    </div>
    <span class="hint">数据更新后扫描全部品种，列出明日可交易机会</span>
  </div>

  <n-card size="small">
    <template #header>
      <div class="card-title">
        <span>扫描结果</span>
        <span class="hint">共 {{ rows.length }} 条</span>
      </div>
    </template>
    <template #header-extra>
      <span class="hint">按交易日倒序</span>
    </template>
    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :bordered="false"
      size="small"
    />
  </n-card>
</template>

<style scoped>
.card-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
</style>
