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
  expiry: string;
  dte: number;
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
    expiry: row.expiry,
    dte: row.dte,
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
        'SELECT * FROM daily_data WHERE symbol = ? ORDER BY date'
      ).bind(symbol).all<DailyRow>();
      const data = (results || []).map(toFrontend);
      return new Response(JSON.stringify({ data }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
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
        expiry?: string; dte?: number;
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
         (symbol, date, open, close, high, low, mp, co, po, bec, bep, vr, ivs, expiry, dte)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      );

      const batch = data.map((r) =>
        stmt.bind(
          symbol, r.d, r.o, r.c, r.h, r.l, r.mp,
          r.co ?? 0, r.po ?? 0,
          r.bec ?? null, r.bep ?? null,
          r.vr ?? null, r.ivs ?? null,
          r.expiry ?? '', r.dte ?? 0
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
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ── GET /api/stats ──
    if (request.method === 'GET' && path === '/api/stats') {
      const { results } = await env.DB.prepare(
        'SELECT symbol, COUNT(*) as count, MIN(date) as first, MAX(date) as last FROM daily_data GROUP BY symbol'
      ).all();
      return new Response(JSON.stringify({ stats: results }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    return new Response(JSON.stringify({ error: 'Not Found' }), {
      status: 404,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  },
};
