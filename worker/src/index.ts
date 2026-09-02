// MaxPain API Worker - Cloudflare Workers + D1
// 类型使用内联方式，避免 @cloudflare/workers-types 运行时问题
interface Env {
  DB: any; // D1Database
  API_KEY: string;
}

interface DailyRow {
  symbol: string;
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  mp: number;
  co: number;
  po: number;
  bec: number | null;
  bep: number | null;
  vr: number | null;
  ivs: number | null;
  gex: number | null;
  expiry: string;
  dte: number;
  nc: number | null;
  oc: number | null;
  oc_chain: string | null;
  oi_total: number;
  oi_pcr: number | null;
  oi_max_strike: number | null;
  vol_call: number;
  vol_put: number;
  vol_total: number;
  fut_vol: number;
  fut_oi: number;
  fut_turnover: number;
  atm_iv: number | null;
}

/** 将 DB 行转成前端兼容的短字段名 */
function toFrontend(row: DailyRow) {
  return {
    d: row.date,
    o: row.open,
    c: row.close,
    h: row.high,
    l: row.low,
    mp: row.mp,
    co: row.co,
    po: row.po,
    bec: row.bec,
    bep: row.bep,
    vr: row.vr,
    ivs: row.ivs,
    gex: row.gex,
    expiry: row.expiry,
    dte: row.dte,
    nc: row.nc,
    oc: row.oc,
    oc_chain: row.oc_chain,
    oi_total: row.oi_total,
    oi_pcr: row.oi_pcr,
    oi_max_strike: row.oi_max_strike,
    vol_call: row.vol_call,
    vol_put: row.vol_put,
    vol_total: row.vol_total,
    fut_vol: row.fut_vol,
    fut_oi: row.fut_oi,
    fut_turnover: row.fut_turnover,
    atm_iv: row.atm_iv,
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-GitHub-Token',
    };
    const cacheHeaders = {
      'Cache-Control': 'public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    // ── GET /api/data?symbol=TA ──
    if (request.method === 'GET' && path === '/api/data') {
      const symbol = (url.searchParams.get('symbol') || '').toUpperCase();
      if (!symbol) {
        return new Response(JSON.stringify({ error: '缺少 symbol 参数' }), {
          status: 400,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      const { results } = await env.DB.prepare(
        'SELECT date,open,close,high,low,mp,co,po,bec,bep,vr,ivs,gex,expiry,dte,nc,oc,oc_chain,oi_total,oi_pcr,oi_max_strike,vol_call,vol_put,vol_total,fut_vol,fut_oi,fut_turnover,atm_iv FROM daily_data WHERE symbol = ? ORDER BY date'
      ).bind(symbol).all<DailyRow>();
      const data = (results || []).map(toFrontend);
      return new Response(JSON.stringify({ data }), {
        headers: { ...corsHeaders, ...cacheHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ── POST /api/update ──
    if (request.method === 'POST' && path === '/api/update') {
      const auth = request.headers.get('Authorization') || '';
      const ghToken = request.headers.get('X-GitHub-Token') || '';
      if (auth !== `Bearer ${env.API_KEY}` && ghToken !== env.API_KEY) {
        return new Response(JSON.stringify({ error: '未授权' }), {
          status: 401,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      const body: { symbol: string; data: Array<{
        d: string; o: number; c: number; h: number; l: number;
        mp: number; co?: number; po?: number;
        bec?: number | null; bep?: number | null;
        vr?: number | null; ivs?: number | null;
        gex?: number | null;
        expiry?: string; dte?: number;
        nc?: number | null; oc?: number | null;
        oc_chain?: string | null;
        oi_total?: number; oi_pcr?: number | null; oi_max_strike?: number | null;
        vol_call?: number; vol_put?: number; vol_total?: number;
        fut_vol?: number; fut_oi?: number; fut_turnover?: number;
        atm_iv?: number | null;
      }> } = await request.json();

      const { symbol, data } = body;
      if (!symbol || !data || !data.length) {
        return new Response(JSON.stringify({ error: '数据为空' }), {
          status: 400,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }

      const stmt = env.DB.prepare(
        `INSERT OR REPLACE INTO daily_data
         (symbol, date, open, close, high, low, mp, co, po, bec, bep, vr, ivs, gex, expiry, dte, nc, oc, oc_chain,
          oi_total, oi_pcr, oi_max_strike, vol_call, vol_put, vol_total, fut_vol, fut_oi, fut_turnover, atm_iv)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      );

      const batch = data.map((r) =>
        stmt.bind(
          symbol, r.d, r.o, r.c, r.h, r.l, r.mp,
          r.co ?? 0, r.po ?? 0,
          r.bec ?? null, r.bep ?? null,
          r.vr ?? null, r.ivs ?? null,
          r.gex ?? null,
          r.expiry ?? '', r.dte ?? 0,
          r.nc ?? null, r.oc ?? null,
          r.oc_chain ?? null,
          r.oi_total ?? 0, r.oi_pcr ?? null, r.oi_max_strike ?? null,
          r.vol_call ?? 0, r.vol_put ?? 0, r.vol_total ?? 0,
          r.fut_vol ?? 0, r.fut_oi ?? 0, r.fut_turnover ?? 0,
          r.atm_iv ?? null
        )
      );

      await env.DB.batch(batch);
      return new Response(JSON.stringify({ ok: true, count: data.length }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ── GET /api/symbols ──
    if (request.method === 'GET' && path === '/api/symbols') {
      const { results } = await env.DB.prepare(
        'SELECT DISTINCT symbol FROM daily_data ORDER BY symbol'
      ).all<{ symbol: string }>();
      const symbols = (results || []).map(r => r.symbol);
      return new Response(JSON.stringify({ symbols }), {
        headers: { ...corsHeaders, ...cacheHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ── GET /api/stats ──
    if (request.method === 'GET' && path === '/api/stats') {
      const { results } = await env.DB.prepare(
        'SELECT symbol, COUNT(*) as count, MIN(date) as first, MAX(date) as last FROM daily_data GROUP BY symbol'
      ).all();
      return new Response(JSON.stringify({ stats: results }), {
        headers: { ...corsHeaders, ...cacheHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ── GET /api/params?symbol=TA ──
    if (request.method === 'GET' && path === '/api/params') {
      const symbol = (url.searchParams.get('symbol') || '').toUpperCase();
      if (!symbol) {
        return new Response(JSON.stringify({ error: '缺少 symbol 参数' }), {
          status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      const row = await env.DB.prepare('SELECT * FROM backtest_params WHERE symbol = ?').bind(symbol).first();
      if (!row) {
        return new Response(JSON.stringify({ params: null }), {
          headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({ params: row }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' },
      });
    }

    // ── POST /api/params ──
    if (request.method === 'POST' && path === '/api/params') {
      const body: {
        symbol: string;
        lookback?: number; min_pct?: number; max_pos?: number; margin?: number;
        be_th?: number; entry_stop?: number; entry_atr?: number; atr_period?: number; atr_mult?: number;
        lock_pct?: number; capital?: number; cap_limit?: number;
        skip_count?: number; mom_days?: number;
        vol_filter_low?: number; vol_filter_high?: number;
        vol_target?: number; vol_target_cap?: number;
        ewma_filter_high?: number; ewma_filter_low?: number;
        price_slope_th?: number; slope_days?: number; pb_slope_th?: number;
        vol_window?: number;
        acc_window?: number; acc_streak?: number; acc_mode?: string;
        start_date?: string; end_date?: string;
      } = await request.json();
      const { symbol } = body;
      if (!symbol) {
        return new Response(JSON.stringify({ error: '缺少 symbol' }), {
          status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      await env.DB.prepare(
        `INSERT OR REPLACE INTO backtest_params
         (symbol, lookback, min_pct, max_pos, margin, be_th, entry_stop, entry_atr,
          atr_period, atr_mult, lock_pct, capital, cap_limit, skip_count, mom_days,
          vol_filter_low, vol_filter_high, vol_target, vol_target_cap,
          ewma_filter_high, ewma_filter_low, price_slope_th, slope_days, pb_slope_th,
          vol_window, acc_window, acc_streak, acc_mode, start_date, end_date, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))`
      ).bind(
        symbol.toUpperCase(),
        body.lookback ?? null, body.min_pct ?? null, body.max_pos ?? null, body.margin ?? null,
        body.be_th ?? null, body.entry_stop ?? null, body.entry_atr ?? null,
        body.atr_period ?? null, body.atr_mult ?? null, body.lock_pct ?? null,
        body.capital ?? null, body.cap_limit ?? null, body.skip_count ?? null, body.mom_days ?? null,
        body.vol_filter_low ?? 0, body.vol_filter_high ?? 100,
        body.vol_target ?? 0, body.vol_target_cap ?? 2,
        body.ewma_filter_high ?? 0, body.ewma_filter_low ?? 0,
        body.price_slope_th ?? 0, body.slope_days ?? 30, body.pb_slope_th ?? 0,
        body.vol_window ?? 0,
        body.acc_window ?? 3, body.acc_streak ?? 3, body.acc_mode ?? 'off',
        body.start_date ?? null, body.end_date ?? null
      ).run();
      return new Response(JSON.stringify({ ok: true }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ── GET /api/orders?symbol=TA ──
    if (request.method === 'GET' && path === '/api/orders') {
      const symbol = (url.searchParams.get('symbol') || '').toUpperCase();
      if (!symbol) {
        return new Response(JSON.stringify({ error: '缺少 symbol 参数' }), {
          status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      const { results } = await env.DB.prepare(
        'SELECT id, symbol, date, direction, price, qty, stop_price, exit_date, exit_price, status, remark, risk_note FROM manual_orders WHERE symbol = ? ORDER BY date, id'
      ).bind(symbol).all();
      return new Response(JSON.stringify({ orders: results || [] }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' },
      });
    }

    // ── POST /api/orders (新建) ──
    if (request.method === 'POST' && path === '/api/orders') {
      const body: {
        symbol?: string; date?: string; direction?: string; price?: number;
        qty?: number; stop_price?: number | null;
        exit_date?: string | null; exit_price?: number | null;
        status?: string;
        remark?: string | null; risk_note?: string | null;
      } = await request.json();
      const { symbol, date, direction, price } = body;
      if (!symbol || !date || (direction !== 'long' && direction !== 'short') || price == null) {
        return new Response(JSON.stringify({ error: '参数不完整' }), {
          status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      const closed = body.status === 'closed' || body.exit_price != null;
      const { meta } = await env.DB.prepare(
        `INSERT INTO manual_orders (symbol, date, direction, price, qty, stop_price, exit_date, exit_price, status, remark, risk_note)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      ).bind(
        symbol.toUpperCase(), date, direction, price,
        body.qty ?? 1, body.stop_price ?? null,
        body.exit_date ?? null, body.exit_price ?? null,
        closed ? 'closed' : 'open',
        body.remark ?? null, body.risk_note ?? null
      ).run();
      return new Response(JSON.stringify({ ok: true, id: meta.last_row_id }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ── POST /api/orders/update (编辑) ──
    if (request.method === 'POST' && path === '/api/orders/update') {
      const body: {
        id?: number; date?: string; direction?: string; price?: number;
        qty?: number; stop_price?: number | null;
        exit_date?: string | null; exit_price?: number | null;
        status?: string;
        remark?: string | null; risk_note?: string | null;
      } = await request.json();
      if (body.id == null) {
        return new Response(JSON.stringify({ error: '缺少 id' }), {
          status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      const closed = body.status === 'closed' || body.exit_price != null;
      await env.DB.prepare(
        `UPDATE manual_orders SET date=?, direction=?, price=?, qty=?, stop_price=?, exit_date=?, exit_price=?, status=?, remark=?, risk_note=?, updated_at=datetime('now') WHERE id=?`
      ).bind(
        body.date, body.direction, body.price, body.qty ?? 1,
        body.stop_price ?? null,
        body.exit_date ?? null, body.exit_price ?? null,
        closed ? 'closed' : 'open',
        body.remark ?? null, body.risk_note ?? null,
        body.id
      ).run();
      return new Response(JSON.stringify({ ok: true }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ── POST /api/orders/delete (删除) ──
    if (request.method === 'POST' && path === '/api/orders/delete') {
      const body: { id?: number } = await request.json();
      if (body.id == null) {
        return new Response(JSON.stringify({ error: '缺少 id' }), {
          status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }
      await env.DB.prepare('DELETE FROM manual_orders WHERE id = ?').bind(body.id).run();
      return new Response(JSON.stringify({ ok: true }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    return new Response(JSON.stringify({ error: 'Not Found' }), {
      status: 404,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  },
};
