"""把旧页面保存的优化参数同步到新库。

旧页面把每个品种的参数存在 Worker D1：
    GET https://api.starrysay.com/api/params?symbol=TA

旧库字段是 snake_case，百分比存“百分数”（min_pct=0.4 表示 0.4%）；
新库统一用 camelCase，百分比存小数（0.004）。这里负责两项转换，
写入本地 strategy_params 表，之后扫描会优先使用这些参数。

用法（在 app/backend 目录下）：
    python -m scripts.sync_legacy_params              # 只补本地没有的品种
    python -m scripts.sync_legacy_params --overwrite  # 覆盖本地已保存参数

如果公网接口不稳定，可以先用 wrangler 导出再导入：
    cd ../worker
    npx wrangler d1 execute maxpain-db --remote \
        --command "SELECT * FROM backtest_params" --json > /tmp/params.json
    cd ../app/backend
    python -m scripts.sync_legacy_params --from-json /tmp/params.json --overwrite
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import StrategyParam
from app.services.data_source import load_symbols

LEGACY_URL = "https://api.starrysay.com/api/params?symbol={symbol}"
STRATEGY = "maxpain_v5"


def _fetch(symbol: str, retries: int = 3) -> dict | None:
    """读取旧库参数；接口偶发空响应，失败重试。"""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                LEGACY_URL.format(symbol=symbol), timeout=15
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("params")
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(0.5 * (attempt + 1))
    return None


def _load_dump(path: str) -> dict[str, dict]:
    """读取 wrangler --json 导出文件，返回 {symbol: 参数行}。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload[0]["results"] if payload and "results" in payload[0] else payload
    return {
        row["symbol"]: row
        for row in rows
        if isinstance(row, dict) and row.get("symbol")
    }


def _convert(row: dict) -> dict:
    """旧字段 → 新字段，百分比 → 小数；None 不写入，让默认值生效。"""

    def pct(key: str) -> float | None:
        value = row.get(key)
        return None if value is None else value / 100

    converted = {
        "lookback": row.get("lookback"),
        "minPct": pct("min_pct"),
        "maxPos": pct("max_pos"),
        "margin": pct("margin"),
        "beTh": pct("be_th"),
        "entryAtrMult": row.get("entry_atr"),
        "atrPeriod": row.get("atr_period"),
        "atrMult": row.get("atr_mult"),
        "lockPct": pct("lock_pct"),
        "capital": row.get("capital"),
        "capLimit": row.get("cap_limit"),
        "skipCount": row.get("skip_count"),
        "volFilterLow": pct("vol_filter_low"),
        "volFilterHigh": pct("vol_filter_high"),
        "volTarget": row.get("vol_target"),
        "volTargetCap": row.get("vol_target_cap"),
        "ewmaFilterHigh": row.get("ewma_filter_high"),
        "ewmaFilterLow": row.get("ewma_filter_low"),
        "priceSlopeTh": row.get("price_slope_th"),
        "slopeDays": row.get("slope_days"),
        "pbSlopeTh": row.get("pb_slope_th"),
        "mpBand": row.get("mp_band"),
        "beBand": row.get("be_band"),
        "band": row.get("band"),
        "volWindow": row.get("vol_window"),
        "accWindow": row.get("acc_window"),
        "accStreak": row.get("acc_streak"),
        "accMode": row.get("acc_mode"),
    }
    return {key: value for key, value in converted.items() if value is not None}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖本地已保存参数（默认只补缺失品种）",
    )
    parser.add_argument(
        "--from-json",
        metavar="PATH",
        help="读取 wrangler D1 导出的 JSON，跳过公网 API",
    )
    args = parser.parse_args()

    symbols = [info["symbol"] for info in load_symbols()]
    dump = _load_dump(args.from_json) if args.from_json else None

    with SessionLocal() as db:
        existing = {
            row.symbol: row
            for row in db.scalars(
                select(StrategyParam).where(
                    StrategyParam.strategy == STRATEGY
                )
            )
        }

        updated = skipped = missing = 0
        for symbol in symbols:
            row = dump.get(symbol) if dump is not None else _fetch(symbol)
            if not row:
                missing += 1
                continue

            current = existing.get(symbol)
            if current is not None and not args.overwrite:
                skipped += 1
                continue

            if current is None:
                current = StrategyParam(strategy=STRATEGY, symbol=symbol)
                db.add(current)
            current.params = _convert(row)
            updated += 1
            print(f"{symbol}: 已同步", flush=True)

        db.commit()

    print(
        f"完成：同步 {updated}，跳过已存在 {skipped}，旧库无参数 {missing}"
    )


if __name__ == "__main__":
    main()
