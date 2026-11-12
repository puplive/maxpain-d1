<script setup lang="ts">
/**
 * 策略参数编辑。
 *
 * 单位约定：
 * - 后端和策略核心存“原始值”，百分比字段是小数（0.001 = 0.1%）
 * - 表单展示“百分比值”（0.1），所以读写时要乘/除 100
 * - PARAM_GROUPS 同时负责表单分组和字段的显示范围/默认值
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInputNumber,
  NSelect,
  NSpin,
  useMessage,
} from 'naive-ui'
import { api } from '../api/client'
import { useMarketStore } from '../stores/market'

// 目前只有一个策略；以后从后端策略列表接口获取
const STRATEGY = 'maxpain_v5'
const STRATEGY_NAME = 'MaxPain 趋势跟踪 v5'

interface ParamDef {
  key: string
  label: string
  defRaw: number // 默认原始值（百分比字段为小数）
  min: number // 表单显示范围
  max: number
  step: number
  pct?: boolean // 是否按百分比展示
}

interface ParamGroup {
  title: string
  desc: string
  defs: ParamDef[]
  withAccMode?: boolean // 组内额外放准确率门控下拉框
}

// 按用途分组，表单按组渲染，读起来更清楚
const PARAM_GROUPS: ParamGroup[] = [
  {
    title: '趋势',
    desc: 'MP 方向判断',
    defs: [
      { key: 'lookback', label: '趋势窗口', defRaw: 10, min: 5, max: 60, step: 1 },
      { key: 'minPct', label: '趋势阈值(%)', defRaw: 0.002, min: 0, max: 10, step: 0.1, pct: true },
    ],
  },
  {
    title: '仓位',
    desc: '资金占用与单笔上限',
    defs: [
      { key: 'maxPos', label: '最大仓位(%)', defRaw: 1, min: 5, max: 100, step: 1, pct: true },
      { key: 'margin', label: '保证金率(%)', defRaw: 0.15, min: 5, max: 50, step: 1, pct: true },
      { key: 'capital', label: '初始权益(元)', defRaw: 100000, min: 10000, max: 10000000, step: 10000 },
      { key: 'capLimit', label: '单笔上限(万元)', defRaw: 200, min: 0, max: 1000, step: 10 },
    ],
  },
  {
    title: '止损与保本',
    desc: '入场止损与追踪锁定',
    defs: [
      { key: 'entryAtrMult', label: '入场止损ATR', defRaw: 0.5, min: 0.5, max: 5, step: 0.1 },
      { key: 'atrPeriod', label: 'ATR周期', defRaw: 5, min: 5, max: 250, step: 1 },
      { key: 'atrMult', label: '追踪止损ATR', defRaw: 0.5, min: 0.5, max: 5, step: 0.1 },
      { key: 'beTh', label: '保本激活(%)', defRaw: 0.01, min: 0, max: 10, step: 0.1, pct: true },
      { key: 'lockPct', label: '锁定利润(%)', defRaw: 0.5, min: 0, max: 100, step: 1, pct: true },
    ],
  },
  {
    title: '过滤',
    desc: '波动率分位 / EWMA 波动比 / 价格斜率',
    defs: [
      { key: 'volFilterLow', label: '波动率下限(%)', defRaw: 0, min: 0, max: 50, step: 1, pct: true },
      { key: 'volFilterHigh', label: '波动率上限(%)', defRaw: 1, min: 50, max: 100, step: 1, pct: true },
      { key: 'volWindow', label: '分位窗口(天)', defRaw: 0, min: 0, max: 500, step: 10 },
      { key: 'ewmaFilterHigh', label: 'EWMA比上限', defRaw: 1.2, min: 0, max: 3, step: 0.1 },
      { key: 'ewmaFilterLow', label: 'EWMA比下限', defRaw: 0, min: 0, max: 1, step: 0.05 },
      { key: 'priceSlopeTh', label: '逆势斜率阈值', defRaw: 0, min: 0, max: 0.01, step: 0.001 },
      { key: 'slopeDays', label: '斜率窗口(天)', defRaw: 30, min: 5, max: 60, step: 5 },
      { key: 'volTarget', label: '波动率目标化', defRaw: 1, min: 0, max: 1, step: 1 },
      { key: 'volTargetCap', label: '缩放上限(倍)', defRaw: 2, min: 1, max: 5, step: 0.5 },
      { key: 'pbSlopeTh', label: '回踩挂单阈值(%)', defRaw: 0, min: 0, max: 5, step: 0.5 },
    ],
  },
  {
    title: '准确率门控',
    desc: '连续跑输后拦截或反向信号',
    withAccMode: true,
    defs: [
      { key: 'skipCount', label: '盈利后跳过(次)', defRaw: 0, min: 0, max: 10, step: 1 },
      { key: 'accWindow', label: '标记窗口(天)', defRaw: 3, min: 1, max: 60, step: 1 },
      { key: 'accStreak', label: '连续告警(天)', defRaw: 3, min: 1, max: 30, step: 1 },
    ],
  },
]

// 准确率门控是字符串枚举，单独存放，不参与百分比换算
const ACC_MODES = [
  { label: '正常', value: 'off' },
  { label: '拦截', value: 'skip' },
  { label: '反向', value: 'rev' },
]

// 读写参数时需要遍历所有字段
const PARAM_DEFS = PARAM_GROUPS.flatMap((group) => group.defs)

const store = useMarketStore()
const message = useMessage()
const symbol = ref('')
const loading = ref(false)
const saving = ref(false)
// 表单模型里存的是“显示值”
const model = reactive<Record<string, number>>({})
const accMode = ref('off')

const symbolOptions = computed(() =>
  store.symbols.map((item) => ({
    label: `${item.symbol} ${item.name}`,
    value: item.symbol,
  })),
)

/** 原始值 -> 表单显示值（百分比字段乘 100）。 */
function toDisplay(def: ParamDef, raw: number) {
  return def.pct ? Number((raw * 100).toFixed(4)) : raw
}

/** 表单显示值 -> 原始值（百分比字段除 100）。 */
function toRaw(def: ParamDef, display: number) {
  return def.pct ? display / 100 : display
}

async function loadParams(current: string) {
  if (!current) return
  loading.value = true
  try {
    const record = await api.params(STRATEGY, current)
    const raw = record?.params ?? {}
    // 没保存过的字段回退到默认值
    PARAM_DEFS.forEach((def) => {
      const stored = raw[def.key]
      model[def.key] = toDisplay(
        def,
        typeof stored === 'number' ? stored : def.defRaw,
      )
    })
    accMode.value = typeof raw.accMode === 'string' ? raw.accMode : 'off'
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载参数失败')
  } finally {
    loading.value = false
  }
}

async function saveParams() {
  if (!symbol.value) return
  saving.value = true
  try {
    const payload: Record<string, number | string> = {}
    // 提交前统一换算回原始值
    PARAM_DEFS.forEach((def) => {
      payload[def.key] = toRaw(def, model[def.key] ?? def.defRaw)
    })
    payload.accMode = accMode.value
    await api.saveParams(STRATEGY, symbol.value, payload)
    message.success(`${STRATEGY} / ${symbol.value} 参数已保存`)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

/** 把表单恢复为策略默认值；点“保存参数”后才写入数据库。 */
function resetDefaults() {
  PARAM_DEFS.forEach((def) => {
    model[def.key] = toDisplay(def, def.defRaw)
  })
  accMode.value = 'off'
  message.info('已恢复默认值，点击“保存参数”后生效')
}

// 切换品种时自动读取该品种已保存的参数
watch(symbol, (value) => {
  void loadParams(value)
})

onMounted(async () => {
  try {
    const symbols = await store.ensureSymbols()
    // 优先选 TA，方便对照旧回测页
    const target = symbols.find((item) => item.symbol === 'TA') ?? symbols[0]
    symbol.value = target?.symbol ?? ''
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载品种失败')
  }
})
</script>

<template>
  <div class="toolbar">
    <n-select
      v-model:value="symbol"
      filterable
      size="small"
      :options="symbolOptions"
      style="min-width: 240px"
      placeholder="选择品种"
    />
    <n-button size="small" type="primary" :loading="saving" @click="saveParams">
      保存参数
    </n-button>
    <n-button size="small" @click="resetDefaults">重置默认值</n-button>
    <span class="hint">{{ STRATEGY_NAME }} · 参数保存在本地 SQLite</span>
  </div>

  <n-card size="small">
    <template #header>
      <div class="card-title">
        <span>{{ STRATEGY }}</span>
        <span class="hint">百分比字段按 % 显示，保存时自动换算</span>
      </div>
    </template>
    <template #header-extra>
      <span class="hint">{{ symbol || '未选择品种' }}</span>
    </template>

    <n-spin :show="loading">
      <n-form label-placement="top" :show-feedback="false">
        <section
          v-for="group in PARAM_GROUPS"
          :key="group.title"
          class="param-section"
        >
          <div class="section-head">
            <span class="section-title">{{ group.title }}</span>
            <span class="hint">{{ group.desc }}</span>
          </div>
          <div class="param-grid">
            <n-form-item
              v-for="def in group.defs"
              :key="def.key"
              :label="def.label"
            >
              <n-input-number
                v-model:value="model[def.key]"
                size="small"
                :min="def.min"
                :max="def.max"
                :step="def.step"
                style="width: 100%"
              />
            </n-form-item>
            <n-form-item v-if="group.withAccMode" label="标记模式">
              <n-select
                v-model:value="accMode"
                size="small"
                :options="ACC_MODES"
                style="width: 100%"
              />
            </n-form-item>
          </div>
        </section>
      </n-form>
    </n-spin>
  </n-card>
</template>

<style scoped>
.card-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.param-section + .param-section {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid #1a2530;
}

.section-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}

.section-title {
  font-weight: 600;
  color: #dbe4ee;
}
</style>
