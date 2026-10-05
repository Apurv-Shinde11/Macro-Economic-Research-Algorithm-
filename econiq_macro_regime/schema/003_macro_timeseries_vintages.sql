-- Additive vintage/provenance fields for existing macro_timeseries installs.
-- Existing rows remain intact; legacy rows retain UNKNOWN publication quality
-- and their database-assigned ingested_at remains usable as an availability proxy.
alter table macro_timeseries
    add column if not exists provider_series text,
    add column if not exists provider_vintage_date date,
    add column if not exists published_at timestamptz,
    add column if not exists availability_timestamp timestamptz,
    add column if not exists availability_basis text,
    add column if not exists availability_quality text not null default 'UNKNOWN',
    add column if not exists vintage_id text,
    add column if not exists revision_number integer,
    add column if not exists metadata jsonb not null default '{}'::jsonb;

-- Defaults apply to future inserts only; legacy rows are not retroactively
-- assigned a fabricated observation availability time.
alter table macro_timeseries
    alter column availability_timestamp set default now(),
    alter column availability_quality set default 'INGESTION_PROXY';

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'macro_timeseries_availability_quality_check'
          and conrelid = 'macro_timeseries'::regclass
    ) then
        alter table macro_timeseries
            add constraint macro_timeseries_availability_quality_check
            check (availability_quality in ('EXACT', 'INGESTION_PROXY', 'ESTIMATED', 'UNKNOWN'));
    end if;
    if not exists (
        select 1 from pg_constraint
        where conname = 'macro_timeseries_revision_number_check'
          and conrelid = 'macro_timeseries'::regclass
    ) then
        alter table macro_timeseries
            add constraint macro_timeseries_revision_number_check
            check (revision_number is null or revision_number >= 0);
    end if;
end $$;

-- Include provider vintage identity while preserving a deterministic fallback
-- for legacy/null vintage IDs. Existing uniqueness guarantees no old collisions.
alter table macro_timeseries drop constraint if exists macro_timeseries_no_dupes;
create unique index if not exists macro_timeseries_vintage_unique
    on macro_timeseries (
        economy, indicator, period_date, source, ingested_at,
        (coalesce(vintage_id, ''))
    );

-- Null vintage IDs remain unconstrained for legacy/current rows. Provider
-- vintages have stable non-null IDs and therefore conflict deterministically.
create unique index if not exists macro_timeseries_provider_vintage_key
    on macro_timeseries (
        economy, indicator, period_date, source, provider_series, vintage_id
    );

create index if not exists idx_macro_timeseries_availability
    on macro_timeseries (economy, indicator, period_date, availability_timestamp);
