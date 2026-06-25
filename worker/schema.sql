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
  expiry TEXT,
  dte INTEGER,
  updated_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_data_symbol ON daily_data(symbol);
CREATE INDEX IF NOT EXISTS idx_daily_data_date ON daily_data(date);
