-- EconIQ regime_cache: derived DFM factor-score cache, written by
-- macro_regime/regime_pipeline.py.
--
-- UNLIKE macro_timeseries (insert-only, full history), this table IS
-- upserted. It holds computed output, not raw source data, so re-running
-- the pipeline should safely overwrite a prior score for the same
-- (economy, period_date, model_version) rather than accumulate duplicates.

create table if not exists regime_cache (
    id            bigint generated always as identity primary key,
    economy       text        not null,               -- e.g. 'IN'
    period_date   date        not null,                -- the period the score covers
    factor_score  numeric     not null,
    model_version text        not null,                -- e.g. 'dfm_v1_6indicator'
    computed_at   timestamptz not null default now(),  -- when this score was last (re)computed

    constraint regime_cache_unique_score
        unique (economy, period_date, model_version)
);

create index if not exists idx_regime_cache_lookup
    on regime_cache (economy, period_date);

comment on table regime_cache is
    'Derived DFM regime factor scores computed by macro_regime/regime_pipeline.py. '
    'UPSERTED on (economy, period_date, model_version) -- unlike macro_timeseries, this is a '
    'cache of computed output, not raw source data.';
