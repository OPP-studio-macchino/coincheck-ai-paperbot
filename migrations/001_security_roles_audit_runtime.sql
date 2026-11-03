-- Version 001: additive security migration; never truncate or reset existing paper positions.
CREATE TABLE IF NOT EXISTS public.admin_audit(
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id TEXT NOT NULL,
    request_id UUID NOT NULL UNIQUE,
    action TEXT NOT NULL,
    previous JSONB NOT NULL,
    next_state JSONB NOT NULL,
    result TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS public.runtime_status(
    id INTEGER PRIMARY KEY CHECK(id=1),
    git_sha TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    buy_budget_jpy NUMERIC(24,8) NOT NULL CHECK(buy_budget_jpy>0 AND buy_budget_jpy<=50000),
    initial_config_jpy NUMERIC(24,8) NOT NULL,
    fee_bps NUMERIC(8,2) NOT NULL CHECK(fee_bps>=0),
    news_mode TEXT NOT NULL,
    market_at TIMESTAMPTZ,
    exit_eval_at TIMESTAMPTZ,
    exit_reason TEXT,
    last_signal_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.news_attempts(
    id BIGSERIAL PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL CHECK(status IN ('reserved','succeeded','failed')),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_news_attempts_utc_day ON public.news_attempts(requested_at);
CREATE TABLE IF NOT EXISTS public.schema_migrations(
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL
);
INSERT INTO public.schema_migrations(version,status) VALUES ('001_security_roles_audit_runtime','applied')
ON CONFLICT(version) DO UPDATE SET applied_at=now(),status='applied';
