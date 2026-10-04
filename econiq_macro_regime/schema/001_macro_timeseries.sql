-- EconIQ macro_timeseries: insert-only historical store for macro model training.
--
-- Does NOT replace or migrate global_macro_cache. That table stays exactly as-is
-- and keeps serving the dashboard's "today's snapshot" need at low latency.
-- This table exists purely to accumulate history for DFM/BVAR/spillover-network
-- fitting, which global_macro_cache structurally cannot provide (it upserts on
-- economy and overwrites the previous value on every write).
--
-- RULE: application code must only INSERT into this table. Never UPSERT, never
-- UPDATE, never delete except for an explicit, deliberate data-correction pass.
-- A silent upsert here would quietly recreate the exact history-loss bug this
-- table exists to fix.

create table if not exists macro_timeseries (
    id            bigint generated always as identity primary key,
    economy       text        not null,               -- e.g. 'IN'
    indicator     text        not null,               -- e.g. 'cpi_inflation', 'policy_rate', 'gdp_growth'
    period_date   date        not null,                -- the period the observation covers
    value         numeric     not null,
    source        text        not null,                -- 'FRED' | 'WORLD_BANK'
    ingested_at   timestamptz not null default now(),   -- when *we* pulled it, not the period date

    -- One row per (economy, indicator, period, source, ingestion event) is allowed —
    -- deliberately NOT unique on (economy, indicator, period_date) alone, because a
    -- later re-pull of a revised historical value (e.g. GDP revisions) should be
    -- inserted as a new row, not overwrite the old one. This is what makes the table
    -- usable as a point-in-time / vintage store, per the "vintage revision" pitfall
    -- Gemini flagged: you can reconstruct what the model would have seen on any past
    -- date by filtering ingested_at <= that date and taking the latest row per period.
    constraint macro_timeseries_no_dupes
        unique (economy, indicator, period_date, source, ingested_at)
);

create index if not exists idx_macro_timeseries_lookup
    on macro_timeseries (economy, indicator, period_date);

create index if not exists idx_macro_timeseries_latest
    on macro_timeseries (economy, indicator, ingested_at desc);

comment on table macro_timeseries is
    'Insert-only historical macro series for DFM/BVAR/spillover model training. '
    'Never upserted. See global_macro_cache for the current-snapshot table used by the dashboard.';
