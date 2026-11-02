-- Append-only market events; idempotent installation. No production order tables.
CREATE TABLE IF NOT EXISTS market_ticks(
  trade_id TEXT PRIMARY KEY,
  trade_at TIMESTAMPTZ NOT NULL,
  price NUMERIC(24,8) NOT NULL CHECK(price>0),
  amount NUMERIC(24,8) NOT NULL CHECK(amount>0),
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ticks_time ON market_ticks(trade_at);
CREATE TABLE IF NOT EXISTS candles(
  timeframe_min INTEGER NOT NULL CHECK(timeframe_min IN (120,240)),
  bar_start TIMESTAMPTZ NOT NULL,
  open NUMERIC(24,8) NOT NULL,
  high NUMERIC(24,8) NOT NULL,
  low NUMERIC(24,8) NOT NULL,
  close NUMERIC(24,8) NOT NULL,
  volume NUMERIC(24,8) NOT NULL,
  trade_count INTEGER NOT NULL,
  first_at TIMESTAMPTZ NOT NULL,
  last_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(timeframe_min,bar_start)
);
CREATE TABLE IF NOT EXISTS feed_sessions(
  id TEXT PRIMARY KEY,
  connected_at TIMESTAMPTZ NOT NULL,
  disconnected_at TIMESTAMPTZ,
  last_trade_at TIMESTAMPTZ,
  error_text TEXT
);
CREATE TABLE IF NOT EXISTS control_state(
  id INTEGER PRIMARY KEY CHECK(id=1),
  paused BOOLEAN NOT NULL DEFAULT false,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO control_state(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS portfolios(
  name TEXT PRIMARY KEY CHECK(name IN ('baseline','news_gate')),
  cash_jpy NUMERIC(24,8) NOT NULL,
  btc_qty NUMERIC(24,8) NOT NULL DEFAULT 0,
  entry_cost_jpy NUMERIC(24,8) NOT NULL DEFAULT 0,
  entry_price NUMERIC(24,8),
  stop_price NUMERIC(24,8),
  target_price NUMERIC(24,8),
  entry_at TIMESTAMPTZ,
  realized_jpy NUMERIC(24,8) NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO portfolios(name,cash_jpy) VALUES ('baseline',100000),('news_gate',100000) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS decisions(
  id BIGSERIAL PRIMARY KEY,
  portfolio TEXT NOT NULL REFERENCES portfolios(name),
  bar_start TIMESTAMPTZ NOT NULL,
  decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  signal BOOLEAN NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(portfolio,bar_start)
);
CREATE TABLE IF NOT EXISTS paper_fills(
  id BIGSERIAL PRIMARY KEY,
  portfolio TEXT NOT NULL REFERENCES portfolios(name),
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
  price NUMERIC(24,8) NOT NULL,
  qty NUMERIC(24,8) NOT NULL,
  total_jpy NUMERIC(24,8) NOT NULL,
  pnl_jpy NUMERIC(24,8),
  reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news_assessments(
  id BIGSERIAL PRIMARY KEY,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  state TEXT NOT NULL CHECK(state IN ('clear','block','unknown')),
  summary TEXT NOT NULL,
  sources JSONB NOT NULL DEFAULT '[]'::jsonb,
  expires_at TIMESTAMPTZ NOT NULL,
  model TEXT NOT NULL,
  usage JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS errors(
  id BIGSERIAL PRIMARY KEY,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  subsystem TEXT NOT NULL,
  message TEXT NOT NULL
);
