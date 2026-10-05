-- EconIQ macro_timeseries: insert-only historical store for macro model training.
--
-- Does NOT replace or migrate global_macro_cache. That table stays exactly as-is
-- and keeps serving the dashboard's "today's snapshot" need at low latency.
-- This table exists purely to accumulate history for DFM/BVAR/spillover-network
-- fitting, which global_macro_cache structurally cannot provide (it upserts on
-- economy and overwrites the previous value on every write).
--
-- RULE: never overwrite vintages. INSERT ... ON CONFLICT DO NOTHING is allowed
-- only for deterministic provider-vintage keys to make repeated acquisitions
-- idempotent. Never use conflict-update behavior or delete historical rows.

create table if not exists macro_timeseries (
    id            bigint generated always as identity primary key,
    economy       text        not null,               -- e.g. 'IN'
    indicator     text        not null,               -- e.g. 'cpi_inflation', 'policy_rate', 'gdp_growth'
    period_date   date        not null,                -- the period the observation covers
    value         numeric     not null,
    source        text        not null,                -- 'FRED' | 'WORLD_BANK'
    ingested_at   timestamptz not null default now(),   -- when *we* pulled it, not the period date
    provider_series text,
    provider_vintage_date date,
    published_at  timestamptz,
    availability_timestamp timestamptz default now(),
    availability_basis text,
    availability_quality text not null default 'INGESTION_PROXY'
        check (availability_quality in ('EXACT', 'INGESTION_PROXY', 'ESTIMATED', 'UNKNOWN')),
    vintage_id    text,
    revision_number integer check (revision_number is null or revision_number >= 0),
    metadata      jsonb not null default '{}'::jsonb

    -- Never unique on (economy, indicator, period_date) alone: revisions remain
    -- separate immutable rows and are distinguished by provider vintage identity.
);

create unique index if not exists macro_timeseries_vintage_unique
    on macro_timeseries (
        economy, indicator, period_date, source, ingested_at,
        (coalesce(vintage_id, ''))
    );

-- Stable provider identity supports insert-only idempotent vintage backfills.
create unique index if not exists macro_timeseries_provider_vintage_key
    on macro_timeseries (
        economy, indicator, period_date, source, provider_series, vintage_id
    );

create index if not exists idx_macro_timeseries_lookup
    on macro_timeseries (economy, indicator, period_date);

create index if not exists idx_macro_timeseries_latest
    on macro_timeseries (economy, indicator, ingested_at desc);

create index if not exists idx_macro_timeseries_availability
    on macro_timeseries (economy, indicator, period_date, availability_timestamp);

comment on table macro_timeseries is
    'Insert-only historical macro series for DFM/BVAR/spillover model training. '
    'Provider vintage duplicates are ignored by stable identity; stored rows are never overwritten. '
    'See global_macro_cache for the current-snapshot table used by the dashboard.';
