CREATE TABLE IF NOT EXISTS daily_data (
  symbol TEXT NOT NULL,
  date TEXT NOT NULL,
  open REAL NOT NULL,
  close REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  mp INTEGER NOT NULL,
  co REAL DEFAULT 0,
  po REAL DEFAULT 0,
  bec REAL,
  bep REAL,
  vr REAL,
  ivs REAL,
  gex REAL,
  expiry TEXT,
  dte INTEGER,
  nc REAL,
  oc REAL,
  updated_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_data_symbol ON daily_data(symbol);
CREATE INDEX IF NOT EXISTS idx_daily_data_date ON daily_data(date);

CREATE TABLE IF NOT EXISTS backtest_params (
  symbol TEXT PRIMARY KEY,
  lookback INTEGER DEFAULT 5,
  min_pct REAL DEFAULT 0.1,
  max_pos REAL DEFAULT 75,
  margin REAL DEFAULT 15,
  be_th REAL DEFAULT 0.9,
  entry_stop REAL DEFAULT 2.5,
  atr_period INTEGER DEFAULT 28,
  atr_mult REAL DEFAULT 1.5,
  lock_pct REAL DEFAULT 50,
  capital REAL DEFAULT 100000,
  cap_limit REAL DEFAULT 0,
  skip_count INTEGER DEFAULT 0,
  mom_days INTEGER DEFAULT 3,
  start_date TEXT,
  end_date TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);
