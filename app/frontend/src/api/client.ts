/**
 * 后端 API 封装。
 *
 * 开发环境默认走 /api，由 vite.config.ts 代理到 http://127.0.0.1:8000；
 * 部署到其他地址时可用环境变量 VITE_API_BASE 覆盖。
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })

  // FastAPI 的错误信息在 { detail } 里，取出来直接显示给用户
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      detail = body.detail ?? detail
    } catch {
      // 响应不是 JSON 时保留 statusText
    }
    throw new Error(`请求失败 ${response.status}: ${detail}`)
  }
  return (await response.json()) as T
}

/** 品种基础信息，来自 web/data/index.json。 */
export interface SymbolInfo {
  symbol: string
  name: string
  /** 交易所代码：czce/dce/shfe/ine/cffex/gfex，未知时为空串 */
  exchange: string
  mult: number
}

/** 轻量日线；后端已去掉 oc_chain 等大字段。 */
export interface BarPoint {
  d: string
  o: number
  c: number
  h: number
  l: number
  mp: number
  /** Gamma 峰值行权价，SHFE 等无 IV 数据时可能为 null */
  gpk: number | null
}

/** 期权到期日历：历史与当前近月合约的到期记录。 */
export interface ExpiryEvent {
  symbol: string
  name: string
  exchange: string
  expiry: string
  /** 当日最近到期的期权合约码，如 m2612 */
  contract: string | null
  /** 该到期日被记录时的交易日 */
  last_date: string | null
}

/** 筛选下拉选项。 */
export interface ScreenOption {
  id: string
  label: string
}

/** 可用指标；range 为价格区间，value 为单值指标。 */
export interface ScreenIndicator extends ScreenOption {
  kind: 'range' | 'value'
}

/** 可用行为；kinds 表示能搭配哪类指标。 */
export interface ScreenBehavior extends ScreenOption {
  kinds: string[]
}

/** 条件构建器选项。 */
export interface ScreenMeta {
  windows: number[]
  aggregates: ScreenOption[]
  indicators: ScreenIndicator[]
  behaviors: ScreenBehavior[]
}

/** 原子条件：窗口 + 左指标 + 行为 + 目标 + 聚合方式。 */
export interface ScreenCondition {
  window: number
  left: string
  behavior: string
  target: { type: 'indicator' | 'value'; value: string | number }
  aggregate: 'any' | 'last' | 'all'
}

/** 筛选请求；条件按 combine 组合，symbols 为空表示全部品种。 */
export interface ScreenRequest {
  conditions: ScreenCondition[]
  combine: 'and' | 'or'
  /** 只在这个品种范围内筛选 */
  symbols: string[]
}

/** 命中条件的品种；note 是触发依据（日期/指标/价位）。 */
export interface ScreenMatch {
  symbol: string
  name: string
  date: string
  note: string
}

/** 研究笔记类型：知识点 / 案例 / 经验。 */
export type NoteKind = 'knowledge' | 'case' | 'experience'

/** 笔记状态：待验证 / 已验证 / 已失效。 */
export type NoteStatus = 'pending' | 'verified' | 'invalid'

/** 验证记录：pass 通过 / fail 未通过 / observe 待观察。 */
export interface NoteCheck {
  id: number
  result: 'pass' | 'fail' | 'observe'
  comment: string
  checked_at: string
}

/** 研究笔记；next_review 是下次回顾日期（YYYY-MM-DD）。 */
export interface Note {
  id: number
  title: string
  kind: NoteKind
  content: string
  tags: string[]
  symbol: string | null
  start_date: string | null
  end_date: string | null
  status: NoteStatus
  /** 间隔回顾档位，对应 1/3/7/30 天 */
  review_stage: number
  next_review: string | null
  created_at: string
  updated_at: string
  checks: NoteCheck[]
}

/** 新建/编辑笔记的字段。 */
export interface NoteInput {
  title: string
  kind: NoteKind
  content: string
  tags: string[]
  symbol: string | null
  start_date: string | null
  end_date: string | null
  status: NoteStatus
}

/** 每日扫描结果，对应 daily_signals 表。 */
export interface SignalRow {
  id: number
  trade_date: string
  strategy: string
  symbol: string
  direction: 'long' | 'short'
  price: number | null
  mp: number | null
  stop: number | null
  qty: number | null
  reason: string | null
}

/** 已保存的策略参数，对应 strategy_params 表。 */
export interface ParamRecord {
  strategy: string
  symbol: string
  params: Record<string, unknown>
  updated_at: string
}

/** 回测成交记录；pnl 已扣除双边手续费。 */
export interface BacktestTrade {
  entry_date: string
  exit_date: string
  entry_px: number
  exit_px: number
  direction: 'long' | 'short'
  qty: number
  pnl: number
  reason: string
  entry_reason: string
  hold: number
}

/** 回测汇总指标；profit_factor 为 null 表示没有亏损单。 */
export interface BacktestSummary {
  capital: number
  end_equity: number
  total_return: number
  max_dd: number
  trades: number
  win_rate: number
  profit_factor: number | null
  avg_hold: number
  coverage: number
  avg_pnl: number
}

/** 回测期末仍持有的仓位。 */
export interface BacktestPosition {
  direction: 'long' | 'short'
  entry_date: string
  entry_px: number
  qty: number
  entry_reason: string
  entry_stop: number
  entry_trend: string
  peak_px: number
  be_active: boolean
  reversed: boolean
}

/** 复盘图表用的 K 线；BEC/BEP 旧数据可能缺失。 */
export interface ChartBar {
  d: string
  o: number
  h: number
  l: number
  c: number
  mp: number
  bec: number | null
  bep: number | null
  /** 最近到期日期和剩余天数，用于图上倒计时和悬停信息 */
  expiry: string | null
  dte: number | null
  /** 期货主力合约，变化的那天画换月标记 */
  fut_contract: string | null
}

/** 复盘图衍生指标线；没有 IV/Delta 的品种对应字段为 null。 */
export interface DerivativePoint {
  d: string
  /** Gamma 峰值、Call/Put Wall、ZeroGamma、Magnet/Pin、Δ25 行权价 */
  gpk: number | null
  cw: number | null
  pw: number | null
  zg: number | null
  magnet: number | null
  pin: number | null
  d25c: number | null
  d25p: number | null
  /** OI 重心：合计 / Call / Put */
  oic: number | null
  oicc: number | null
  oicp: number | null
}

/** 复盘图下方附图指标；缺失值后端统一给 0。 */
export interface SubchartPoint {
  d: string
  gex: number
  gex_norm: number
  /** OI 偏度 (Call-Put)/(Call+Put) 及其日变化 */
  skew: number
  skew_delta: number
  /** Call 总持仓 - Put 总持仓（手） */
  oi_net: number
  vr: number
  iv: number
  fut_vol: number
  fut_oi: number
  atr: number
  tr: number
  /** MP 价位持仓占比、ΣΔ×OI、OI 加权平均 Δ */
  mp_conf: number
  hdg: number
  dex: number
  vanna: number
  charm: number
  gam: number
  veg: number
  /** lookback 日多头/空头噪音百分比 */
  noise_l: number
  noise_s: number
}

/** OI 分布图用的单条期权链；CZCE 没有 Delta 字段，cd/pd 为 null。 */
export interface OiChainPoint {
  /** 行权价 */
  s: number
  co: number
  po: number
  /** 近月 Call/Put Delta */
  cd: number | null
  pd: number | null
  /** Δ×OI，即做市商对冲需要买卖的期货手数 */
  cdw: number | null
  pdw: number | null
  civ: number | null
  piv: number | null
}

/** 每日止损：stop 是当日实际使用值，next 是收盘后算好的明日止损。 */
export interface StopPoint {
  d: string
  stop: number | null
  next: number | null
  start: boolean
}

/** 每日复盘指标；pred 是当日收盘信号（次日开盘执行），trade_pred 是门控后信号。 */
export interface IndicatorPoint {
  d: string
  atr: number | null
  atr_ratio: number | null
  ewma_ratio: number | null
  slope: number
  mp_dev: number | null
  pred: 'long' | 'short' | null
  trade_pred: 'long' | 'short' | null
  warn: boolean
  /** 持仓中且明日开盘会因方向反转离场 */
  rev_exit: boolean
}

/** 后端逐日撮合回测的完整结果。 */
export interface BacktestResult {
  symbol: string
  strategy: string
  start: string
  end: string
  summary: BacktestSummary
  trades: BacktestTrade[]
  equity_curve: { d: string; eq: number }[]
  stop_curve: StopPoint[]
  open_position: BacktestPosition | null
  dir_accuracy: Record<string, number | null>
  /** 复盘用：全量 K 线、每日指标、衍生线和本次实际生效参数 */
  bars: ChartBar[]
  indicators: IndicatorPoint[]
  derivatives: DerivativePoint[]
  subchart: SubchartPoint[]
  params: Record<string, unknown>
}

/** 参数网格优化的单组结果。 */
export interface OptimizeRow {
  values: Record<string, number>
  end_equity: number
  total_return: number
  trades: number
  coverage: number
  win_rate: number
  profit_factor: number | null
  max_dd: number
  avg_pnl: number
}

/** 参数网格优化返回，results 已按期末权益降序。 */
export interface OptimizeResult {
  symbol: string
  combos: number
  tested: number
  results: OptimizeRow[]
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  symbols: () => request<SymbolInfo[]>('/symbols'),

  /** 全部品种的期权到期日（含历史），日历页使用。 */
  expiries: () => request<ExpiryEvent[]>('/expiries'),

  /** 条件构建器的窗口/指标/行为选项。 */
  screenMeta: () => request<ScreenMeta>('/screen/meta'),

  /** 按原子条件筛选全部品种；条件之间按 combine 组合。 */
  screen: (payload: ScreenRequest) =>
    request<ScreenMatch[]>('/screen', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  bars: (symbol: string, limit = 300) =>
    request<BarPoint[]>(`/symbols/${symbol}/bars?limit=${limit}`),

  /** 单品种参数；没保存过时后端返回 null。 */
  params: (strategy: string, symbol: string) =>
    request<ParamRecord | null>(`/params/${strategy}/${symbol}`),

  paramsByStrategy: (strategy: string) =>
    request<ParamRecord[]>(`/params/${strategy}`),

  saveParams: (strategy: string, symbol: string, params: Record<string, unknown>) =>
    request<ParamRecord>(`/params/${strategy}/${symbol}`, {
      method: 'PUT',
      body: JSON.stringify({ params }),
    }),

  signals: (tradeDate?: string) =>
    request<SignalRow[]>(`/signals${tradeDate ? `?date=${tradeDate}` : ''}`),

  /** 扫描全部品种，结果写入 daily_signals。 */
  scan: () => request<{ message: string }>('/scan', { method: 'POST' }),

  /**
   * 单品种逐日回测；start/end 只限制开仓区间。
   * params 用于回测页手动微调，覆盖保存值且只对本次请求生效。
   */
  backtest: (
    symbol: string,
    start?: string,
    end?: string,
    params?: Record<string, number | string>,
  ) =>
    request<BacktestResult>(`/backtest/${symbol}`, {
      method: 'POST',
      body: JSON.stringify({ start, end, params }),
    }),

  /** 某天的期权链，OI 分布图用；按需单日拉取，避免整份链走回测响应。 */
  chain: (symbol: string, date: string) =>
    request<OiChainPoint[]>(`/backtest/${symbol}/chain?date=${date}`),

  /** 研究笔记：列表、增删改、验证记录和回顾。 */
  notes: () => request<Note[]>('/notes'),

  createNote: (payload: NoteInput) =>
    request<Note>('/notes', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateNote: (id: number, payload: NoteInput) =>
    request<Note>(`/notes/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  deleteNote: (id: number) =>
    request<{ ok: boolean }>(`/notes/${id}`, { method: 'DELETE' }),

  addNoteCheck: (
    id: number,
    payload: { result: NoteCheck['result']; comment: string },
  ) =>
    request<Note>(`/notes/${id}/checks`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  reviewNote: (id: number, remembered: boolean) =>
    request<Note>(`/notes/${id}/review`, {
      method: 'POST',
      body: JSON.stringify({ remembered }),
    }),

  /** 参数网格扫描；grid 的值是原始单位（百分比字段为小数）。 */
  optimize: (
    symbol: string,
    body: {
      start?: string
      end?: string
      grid: Record<string, number[]>
    },
  ) =>
    request<OptimizeResult>(`/optimize/${symbol}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}
