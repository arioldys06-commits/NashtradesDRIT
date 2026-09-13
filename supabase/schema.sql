-- Schema inicial NashtradesDRIT
-- Correr en el proyecto Supabase NUEVO (separado del de TradingProEA)

create table if not exists signals (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz default now(),
    symbol text not null default 'GOLD',
    strategy text not null,
    direction text not null check (direction in ('BUY', 'SELL')),
    entry_price numeric,
    sl numeric,
    tp1 numeric,
    tp2 numeric,
    score numeric,
    status text default 'PENDING',
    magic_number bigint
);

create table if not exists trades_ejecutados (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz default now(),
    signal_id uuid references signals(id),
    symbol text not null default 'GOLD',
    strategy text,
    direction text,
    origen text default 'BOT' check (origen in ('BOT', 'MANUAL', 'BOT_MANUAL')),
    entry_price numeric,
    exit_price numeric,
    sl numeric,
    tp numeric,
    profit numeric,
    opened_at timestamptz,
    closed_at timestamptz,
    magic_number bigint
);

create table if not exists ohlc_candles (
    id bigserial primary key,
    symbol text not null default 'GOLD',
    timeframe text not null,
    time timestamptz not null,
    open numeric,
    high numeric,
    low numeric,
    close numeric,
    volume numeric,
    unique (symbol, timeframe, time)
);

create table if not exists backtests (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz default now(),
    strategy text,
    params jsonb,
    winrate numeric,
    total_trades integer,
    net_profit numeric,
    notes text
);

create table if not exists news_events (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz default now(),
    dedup_key text unique,
    source text,
    raw_type text,
    event_time timestamptz,
    currency text,
    impact text,
    title text,
    summary text,
    url text,
    forecast text,
    previous_value text,
    actual_value text,
    ai_bias text,
    ai_confidence text,
    ai_reasoning text
);
