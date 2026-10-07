"""
Roadmap Task 6 — the actual go/no-go checkpoint. Pulls the real backfilled
India data from macro_timeseries, fits a 1-factor DynamicFactorMQ, and prints
the extracted factor around a handful of known India macro events so you can
eyeball whether it tracks anything real.

This is NOT the production regime_cache pipeline (roadmap Phase 1) — it's a
one-off validation script. Run it, look at the output, decide go/no-go,
THEN build the batch-job version if it passes.

Usage:
    python fit_real_data.py
"""
from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from supabase import create_client

from point_in_time_dataset import build_point_in_time_dataset, build_point_in_time_panel
from transforms import apply_transform
from dfm_model import fit_dfm


REAL_DFM_INDICATOR_METADATA = {
    "cpi_inflation": {
        "indicator": "cpi_inflation",
        "economic_meaning": "India CPI inflation, expressed as YoY inflation percentage using the CPI index level.",
        "provider": "FRED",
        "provider_series": "INDCPIALLMINMEI",
        "frequency": "monthly",
        "observation_date_semantics": "monthly CPI observation date for the month in question",
        "transform": "yoy_from_index(periods_per_year=12)",
        "availability_method": "observation_date only; the current FRED path does not preserve a separate official publication timestamp",
        "availability_quality": "UNKNOWN",
        "release_lag": None,
        "notes": "Current pipeline stores only raw sampled observations; no explicit publication timestamp is preserved by default.",
    },
    "gdp_growth": {
        "indicator": "gdp_growth",
        "economic_meaning": "Annual real GDP growth, sourced from World Bank as a YoY growth series.",
        "provider": "World Bank",
        "provider_series": "NY.GDP.MKTP.KD.ZG",
        "frequency": "annual",
        "observation_date_semantics": "annual observation tagged to the year start date (YYYY-01-01)",
        "transform": "already_stationary_passthrough",
        "availability_method": "year-level observation record; no explicit release timestamp is stored by the current source path",
        "availability_quality": "UNKNOWN",
        "release_lag": None,
        "notes": "World Bank annual series can be delayed and revised; the repository does not currently preserve a publication timestamp.",
    },
    "policy_rate": {
        "indicator": "policy_rate",
        "economic_meaning": "India 10Y government bond yield / long-term rate proxy, not the RBI repo rate.",
        "provider": "FRED",
        "provider_series": "INDIRLTLT01STM",
        "frequency": "monthly",
        "observation_date_semantics": "monthly yield observation date",
        "transform": "first_difference",
        "availability_method": "observation_date only; current path does not preserve an exact official publication timestamp",
        "availability_quality": "UNKNOWN",
        "release_lag": None,
        "notes": "Compatibility decision: keep the existing economic series name for backward compatibility, while documenting the true meaning and the need for separate repo-rate metadata in a later phase.",
    },
    "currency_inr_usd": {
        "indicator": "currency_inr_usd",
        "economic_meaning": "INR per USD spot exchange rate, converted to log-difference monthly movement.",
        "provider": "FRED",
        "provider_series": "DEXINUS",
        "frequency": "monthly after daily resampling",
        "observation_date_semantics": "last trading day of the month mapped to monthly period",
        "transform": "mom_log_diff",
        "availability_method": "resampled monthly observation date; no explicit publication timestamp is stored in the current source path",
        "availability_quality": "UNKNOWN",
        "release_lag": None,
        "notes": "The current path resamples daily data to monthly values, but does not retain a vintage/publication timestamp for those resampled rows.",
    },
    "unemployment_rate": {
        "indicator": "unemployment_rate",
        "economic_meaning": "World Bank unemployment rate share of labor force, annual.",
        "provider": "World Bank",
        "provider_series": "SL.UEM.TOTL.ZS",
        "frequency": "annual",
        "observation_date_semantics": "annual observation tagged to the year start date",
        "transform": "first_difference",
        "availability_method": "year-level observation record; no explicit release timestamp is stored by the current source path",
        "availability_quality": "UNKNOWN",
        "release_lag": None,
        "notes": "Only annual release data is preserved in the repo; not enough metadata is available to claim a publication-vintage boundary.",
    },
    "stock_market_growth": {
        "indicator": "stock_market_growth",
        "economic_meaning": "India equity market growth rate, already expressed as a growth series rather than a raw level.",
        "provider": "FRED",
        "provider_series": "SPASTT01INM657N",
        "frequency": "monthly",
        "observation_date_semantics": "monthly observation date for the change in the stock-market index",
        "transform": "already_stationary_passthrough",
        "availability_method": "monthly observation date only; no explicit publication timestamp is preserved",
        "availability_quality": "UNKNOWN",
        "release_lag": None,
        "notes": "The provider series is already a growth rate, but the repo still lacks an authoritative publication-vintage label for each monthly observation.",
    },
    "exports_value": {
        "indicator": "exports_value",
        "economic_meaning": "India export value in USD, converted to YoY export growth to control for seasonality.",
        "provider": "FRED",
        "provider_series": "VALEXPINM052N",
        "frequency": "monthly",
        "observation_date_semantics": "monthly observation date for export value",
        "transform": "yoy_from_index(periods_per_year=12)",
        "availability_method": "monthly observation date only; the current path does not preserve a separate official publication timestamp",
        "availability_quality": "UNKNOWN",
        "release_lag": None,
        "notes": "Exports are a raw value series, but without vintage metadata there is no defensible historical publication boundary.",
    },
    "industrial_production_growth": {
        "indicator": "industrial_production_growth",
        "economic_meaning": "India manufacturing production growth, already reported as seasonally adjusted year-over-year growth.",
        "provider": "FRED",
        "provider_series": "INDPRMNTO01GYSAM",
        "frequency": "monthly",
        "observation_date_semantics": "monthly observation date for manufacturing production growth",
        "transform": "already_stationary_passthrough",
        "availability_method": "FRED/ALFRED vintage date at date-only precision (ESTIMATED); no source publication timestamp is inferred",
        "availability_quality": "ESTIMATED",
        "release_lag": None,
        "notes": "Provider values are seasonally adjusted YoY growth rates; do not apply yoy_from_index.",
    },
}

# The database schema supplies a database-assigned ingested_at timestamp for
# stored observations. This is a runtime knowledge proxy, not an official
# provider publication timestamp; PIT selection must preserve that distinction.
for _series_metadata in REAL_DFM_INDICATOR_METADATA.values():
    _provider = _series_metadata["provider"].upper().replace(" ", "_")
    _series_metadata["publication_timestamp_capability"] = "not_preserved_by_current_ingestion_job"
    _series_metadata["vintage_metadata_capability"] = (
        "fred_alfr_vintage_acquisition_available_series_depth_unverified"
        if _provider == "FRED"
        else "not_collected_by_current_indicator_request"
    )
    _series_metadata["availability_policy"] = (
        "FRED provider vintage date ESTIMATED when acquired; otherwise database ingestion proxy"
        if _provider == "FRED"
        else "database ingestion proxy; no provider vintage date collected"
    )
    _series_metadata["availability_method"] = (
        "FRED vintage-date backfill uses provider information-set dates at date-only "
        "precision (ESTIMATED); normal ingestion uses macro_timeseries.ingested_at "
        "(INGESTION_PROXY); source-agency publication time is not inferred"
        if _provider == "FRED"
        else "macro_timeseries.ingested_at database timestamp (INGESTION_PROXY); "
        "provider publication time is not preserved"
    )
    _series_metadata["notes"] += (
        " FRED vintages, when acquired, represent the FRED/ALFRED real-time "
        "information set and do not establish an original source-agency release time."
        if _provider == "FRED"
        else " Stored rows can be filtered by actual database ingestion time, but "
        "this is an INGESTION_PROXY and does not establish provider release time."
    )


def _as_record_list(data: pd.DataFrame | list[dict], indicator_metadata: dict | None = None) -> list[dict]:
    if isinstance(data, list):
        return [dict(record) for record in data]

    # load_wide_frame keeps the original vintage rows here. Do not reconstruct
    # availability from the period date or from the already-collapsed wide view.
    raw_records = data.attrs.get("raw_records")
    if raw_records is not None:
        return [dict(record) for record in raw_records]

    metadata_map = indicator_metadata or REAL_DFM_INDICATOR_METADATA
    rows: list[dict] = []
    for column in data.columns:
        series_meta = metadata_map.get(column, {})
        for period in data.index:
            value = data.loc[period, column]
            if pd.isna(value):
                continue
            rows.append({
                "indicator": column,
                "period_date": pd.Timestamp(period).strftime("%Y-%m-%d"),
                "value": float(value),
                "source": series_meta.get("provider", "UNKNOWN"),
                "provider_series": series_meta.get("provider_series"),
                "available_at": None,
                "published_at": None,
                "ingested_at": None,
                "retrieved_at": None,
                "availability_quality": series_meta.get("availability_quality", "UNKNOWN"),
            })
    return rows


def prepare_real_dfm_data(
    data: pd.DataFrame | list[dict],
    as_of: str | None = None,
    indicator_metadata: dict | None = None,
    indicators: Sequence[str] | None = None,
):
    """Select a strict point-in-time vintage before transforming any real DFM data."""
    if as_of is None:
        as_of = datetime.now(timezone.utc).isoformat()
    records = _as_record_list(data, indicator_metadata or REAL_DFM_INDICATOR_METADATA)
    indicator_names = (
        list(dict.fromkeys(indicators))
        if indicators is not None
        else list({record["indicator"] for record in records})
    )
    if not records:
        empty_panel = pd.DataFrame()
        return {
            "panel": empty_panel,
            "transformed_panel": empty_panel,
            "manifest": {
                "as_of": as_of,
                "dataset_version": "point_in_time_real_dfm_v1",
                "indicator_count": 0,
                "observation_count": 0,
                "warnings": ["No observations available for the given real DFM inputs."],
            },
            "metadata": indicator_metadata or REAL_DFM_INDICATOR_METADATA,
            "strict_point_in_time": True,
            "warnings": ["No observations available for the given real DFM inputs."],
        }

    dataset = build_point_in_time_dataset(records, as_of=as_of, indicators=indicator_names)
    selected = _aggregate_fred_daily_fx_to_monthly(dataset["records"], as_of)
    manifest = dataset["manifest"]
    warnings = list(manifest.get("warnings", []))
    filtered_panel = build_point_in_time_panel(selected, as_of=as_of, indicators=indicator_names)
    manifest["panel_observation_count"] = int(filtered_panel.notna().sum().sum())

    transformed_panel = transform_wide_frame(filtered_panel) if not filtered_panel.empty else pd.DataFrame()
    return {
        "panel": filtered_panel,
        "transformed_panel": transformed_panel,
        "manifest": manifest,
        "metadata": indicator_metadata or REAL_DFM_INDICATOR_METADATA,
        "strict_point_in_time": True,
        "warnings": warnings,
    }


def _aggregate_fred_daily_fx_to_monthly(records: list[dict], as_of: str) -> list[dict]:
    """After PIT selection, map daily DEXINUS vintages to monthly EOM values."""
    daily: dict[pd.Timestamp, list[dict]] = {}
    other_records: list[dict] = []
    for record in records:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        is_daily_fx = (
            record.get("indicator") == "currency_inr_usd"
            and record.get("provider_series") == "DEXINUS"
            and metadata.get("provider_frequency") == "daily"
        )
        if not is_daily_fx:
            other_records.append(record)
            continue
        day = pd.Timestamp(record["period_date"])
        month = day.to_period("M").to_timestamp()
        daily.setdefault(month, []).append(record)

    as_of_timestamp = pd.Timestamp(as_of)
    if as_of_timestamp.tzinfo is not None:
        as_of_timestamp = as_of_timestamp.tz_convert("UTC").tz_localize(None)
    as_of_month = as_of_timestamp.to_period("M").to_timestamp()
    monthly_records = list(other_records)
    for month, month_records in daily.items():
        # A monthly factor must not use a partial current month. The latest
        # daily observation for a completed month is selected only after PIT.
        if month >= as_of_month:
            continue
        last_day = max(pd.Timestamp(record["period_date"]) for record in month_records)
        chosen = max(
            (
                record for record in month_records
                if pd.Timestamp(record["period_date"]) == last_day
            ),
            key=lambda record: (
                record.get("_available_dt") or datetime.min.replace(tzinfo=timezone.utc),
                str(record.get("vintage_id") or ""),
            ),
        )
        monthly = dict(chosen)
        monthly["period_date"] = month.strftime("%Y-%m-%d")
        monthly_meta = dict(chosen.get("metadata") or {})
        monthly_meta["monthly_aggregation"] = "last_eligible_daily_observation_after_pit"
        monthly_meta["source_observation_date"] = last_day.strftime("%Y-%m-%d")
        monthly["metadata"] = monthly_meta
        monthly_records.append(monthly)

    # Existing current-value monthly records and acquired daily vintage rows
    # can map to the same period. Preserve the one latest eligible timestamp.
    selected_by_period: dict[tuple[str, str], dict] = {}
    for record in monthly_records:
        key = (record["indicator"], str(record["period_date"]))
        current = selected_by_period.get(key)
        record_time = record.get("_available_dt") or datetime.min.replace(tzinfo=timezone.utc)
        current_time = (
            current.get("_available_dt") or datetime.min.replace(tzinfo=timezone.utc)
            if current else datetime.min.replace(tzinfo=timezone.utc)
        )
        if current is None or (record_time, str(record.get("vintage_id") or "")) > (
            current_time,
            str(current.get("vintage_id") or ""),
        ):
            selected_by_period[key] = record
    return sorted(
        selected_by_period.values(),
        key=lambda record: (record["indicator"], str(record["period_date"])),
    )


def _load_secrets():
    candidates = [
        Path.cwd() / ".streamlit" / "secrets.toml",
        Path(os.environ.get("SECRETS_TOML_PATH", "")) if os.environ.get("SECRETS_TOML_PATH") else None,
    ]
    secrets_path = next((p for p in candidates if p and p.exists()), None)
    if secrets_path is None:
        print("[FIT] Could not find .streamlit/secrets.toml — run from the "
              "project root or set SECRETS_TOML_PATH.", file=sys.stderr)
        sys.exit(1)
    with open(secrets_path, "rb") as f:
        secrets = tomllib.load(f)
    for k in ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"]:
        if k not in secrets:
            print(f"[FIT] secrets.toml missing {k}", file=sys.stderr)
            sys.exit(1)
        os.environ[k] = str(secrets[k])


def load_wide_frame(economy: str = "IN") -> pd.DataFrame:
    """
    Pulls every row for `economy` out of macro_timeseries and pivots into a
    wide DataFrame: monthly DatetimeIndex spanning the full range present in
    the data, one column per indicator, with NaN wherever that indicator has
    no observation for that month (this is the ragged edge / mixed-frequency
    structure DynamicFactorMQ is built to consume directly).
    """
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    rows = []
    page_size = 1000
    offset = 0
    while True:
        resp = (
            supabase.table("macro_timeseries")
            .select(
                "indicator, period_date, value, source, ingested_at, provider_series, "
                "provider_vintage_date, published_at, availability_timestamp, "
                "availability_basis, availability_quality, vintage_id, revision_number, metadata"
            )
            .eq("economy", economy)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        if not resp.data:
            break
        rows.extend(resp.data)
        if len(resp.data) < page_size:
            break
        offset += page_size

    if not rows:
        raise RuntimeError(
            f"No macro_timeseries observations were returned for economy={economy!r}."
        )

    long_df = pd.DataFrame(rows)
    long_df["period_date"] = pd.to_datetime(long_df["period_date"])
    raw_records = long_df.to_dict(orient="records")

    # Pivot long -> wide. If a (indicator, period_date) pair ever has more
    # than one row (e.g. a re-pull/vintage revision — see the schema's
    # comment on why duplicates are allowed), take the most recently
    # ingested one rather than erroring or silently averaging.
    long_df = long_df.sort_values(["period_date", "ingested_at"])
    wide = long_df.pivot_table(
        index="period_date", columns="indicator", values="value", aggfunc="last"
    )

    # Reindex onto a complete monthly grid spanning the full observed range,
    # so annual gdp_growth observations (tagged at a single month each year)
    # sit correctly among 11 NaN months, and DynamicFactorMQ sees a proper
    # ragged-edge panel rather than a frame with irregular gaps.
    full_index = pd.date_range(wide.index.min(), wide.index.max(), freq="MS")
    wide = wide.reindex(full_index)
    wide.attrs["raw_records"] = raw_records
    return wide


def restrict_wide_frame_since(wide: pd.DataFrame, since: str | None) -> pd.DataFrame:
    """Restrict both the display frame and its preserved raw vintage rows."""
    if not since:
        return wide
    restricted = wide.loc[since:].copy()
    cutoff = pd.Timestamp(since).date()
    raw_records = wide.attrs.get("raw_records")
    if raw_records is not None:
        restricted.attrs["raw_records"] = [
            record for record in raw_records
            if pd.Timestamp(record["period_date"]).date() >= cutoff
        ]
    return restricted


def transform_wide_frame(wide: pd.DataFrame) -> pd.DataFrame:
    """Applies the correct, now-confirmed transform to each column."""
    out = pd.DataFrame(index=wide.index)
    if "cpi_inflation" in wide.columns:
        out["cpi_inflation"] = apply_transform(
            "cpi_inflation", wide["cpi_inflation"], periods_per_year=12
        )
    if "gdp_growth" in wide.columns:
        out["gdp_growth"] = apply_transform("gdp_growth", wide["gdp_growth"])
    if "policy_rate" in wide.columns:
        out["policy_rate"] = apply_transform("policy_rate", wide["policy_rate"])
    if "currency_inr_usd" in wide.columns:
        out["currency_inr_usd"] = apply_transform("currency_inr_usd", wide["currency_inr_usd"])
    if "unemployment_rate" in wide.columns:
        out["unemployment_rate"] = apply_transform("unemployment_rate", wide["unemployment_rate"])
    if "stock_market_growth" in wide.columns:
        out["stock_market_growth"] = apply_transform("stock_market_growth", wide["stock_market_growth"])
    if "exports_value" in wide.columns:
        out["exports_value"] = apply_transform(
            "exports_value", wide["exports_value"], periods_per_year=12
        )
    if "industrial_production_growth" in wide.columns:
        out["industrial_production_growth"] = apply_transform(
            "industrial_production_growth", wide["industrial_production_growth"]
        )
    return out


# Confirmed 2026-09-29 by direct testing: DynamicFactorMQ becomes numerically
# unstable (overflows during EM, or crashes in its initial PCA step) when TWO
# independently-sparse annual series are both included — it's built to
# handle one "non-monthly" tier cleanly, not two. gdp_growth and
# unemployment_rate are both annual, so only one can be in the same fit.
# unemployment_rate is still backfilled into macro_timeseries by ingest.py
# (harmless, may be useful for a future/different model) — it's just
# excluded from THIS fit, not from data collection.
EXCLUDE_FROM_DFM_FIT = {"unemployment_rate"}

# The established fit remains six signals. A separate explicit panel is
# available for the future monthly real-activity experiment; adding that
# series to ingestion must not silently alter current production fits.
CURRENT_DFM_FIT_INDICATORS = (
    "cpi_inflation",
    "gdp_growth",
    "policy_rate",
    "currency_inr_usd",
    "stock_market_growth",
    "exports_value",
)
FOUR_SIGNAL_EXPERIMENT_INDICATORS = (
    "cpi_inflation",
    "policy_rate",
    "currency_inr_usd",
    "industrial_production_growth",
)


# Known India macro stress periods, for the eyeball check — NOT ground truth
# labels for scoring, just reference points a real regime factor ought to be
# able to move around visibly.
REFERENCE_EVENTS = {
    "2008-09 Global Financial Crisis": ("2008-08-01", "2009-06-01"),
    "2013 Taper Tantrum (INR crisis)": ("2013-05-01", "2013-09-01"),
    "2016-17 Demonetization": ("2016-11-01", "2017-03-01"),
    "2020 COVID crash": ("2020-02-01", "2020-06-01"),
    "2022 inflation spike": ("2022-01-01", "2022-12-01"),
}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since", default=None,
        help="Optional start date (YYYY-MM-DD) to restrict the fit to — e.g. "
             "--since 2011-12-01 to fit only on the period where cpi_inflation, "
             "gdp_growth, and policy_rate all genuinely overlap, instead of the "
             "full history where policy_rate is missing before then."
    )
    parser.add_argument(
        "--as-of", default=None,
        help="Optional historical timestamp (ISO-8601) to build the real DFM input panel using only data available at that time.",
    )
    args = parser.parse_args()

    _load_secrets()
    wide = load_wide_frame()
    if args.since:
        wide = restrict_wide_frame_since(wide, args.since)
        print(f"[FIT] Restricted to data from {args.since} onward "
              f"({wide.shape[0]} months) per --since flag\n")
    print(f"[FIT] Loaded wide frame: {wide.shape[0]} months, columns={list(wide.columns)}")
    print(f"[FIT] Date range: {wide.index.min().date()} to {wide.index.max().date()}")
    print(f"[FIT] Non-null counts per column:\n{wide.count()}\n")

    prepared = prepare_real_dfm_data(
        wide,
        as_of=args.as_of,
        indicator_metadata=REAL_DFM_INDICATOR_METADATA,
        indicators=CURRENT_DFM_FIT_INDICATORS,
    )
    print(f"[FIT] Point-in-time preparation manifest: {prepared['manifest']}")
    if prepared["warnings"]:
        print(f"[FIT] warnings: {prepared['warnings']}")
    transformed = prepared["transformed_panel"]
    if transformed.empty or transformed.dropna(how="all").empty:
        raise RuntimeError(
            "Point-in-time selection left no usable real DFM observations. "
            "Check source ingested_at metadata; no values were admitted without it."
        )

    excluded_present = [c for c in EXCLUDE_FROM_DFM_FIT if c in transformed.columns]
    if excluded_present:
        transformed = transformed.drop(columns=excluded_present)
        print(f"[FIT] Excluding {excluded_present} from this fit — see "
              f"EXCLUDE_FROM_DFM_FIT comment (numerical instability when "
              f"combined with gdp_growth, both being annual). Still stored "
              f"in Supabase for later use.\n")

    # k_factors=1 per the roadmap's Phase 1 MVP scope — a single "regime"
    # factor, not separated growth/inflation factors.
    results = fit_dfm(transformed, k_factors=1)
    factor = results.factors.smoothed.iloc[:, 0]
    factor.index = transformed.index

    out_path = Path("dfm_factor_score.csv")
    factor.to_frame("factor_score").to_csv(out_path)
    print(f"[FIT] Full factor series written to {out_path.resolve()}\n")

    # Ragged-edge DFMs occasionally produce a wild single-point spike (a
    # numerical artifact, not a real regime shift) — often landing right on
    # a sparse series' rare observation dates (e.g. gdp_growth's once-a-year
    # point). A plain std dev is badly distorted by even one such outlier,
    # so use a median-absolute-deviation-based threshold instead, which is
    # robust to a handful of extreme points, and flag them explicitly rather
    # than silently including them in the "is this event significant" check.
    median = factor.median()
    mad = (factor - median).abs().median()
    robust_threshold = median + 15 * mad * 1.4826  # 1.4826 makes MAD ~comparable to std under normality
    outliers = factor[(factor - median).abs() > robust_threshold]
    if not outliers.empty:
        print(f"[FIT] WARNING: {len(outliers)} likely numerical-artifact outlier(s) "
              f"detected (>15 robust-MAD from the median) — excluding these from "
              f"the event comparison below, but they're still in the saved CSV:")
        for dt, val in outliers.items():
            print(f"    {dt.date()}: {val:.3f}")
        print()
    clean_factor = factor.drop(outliers.index)

    print("[FIT] Factor score around known reference events (eyeball check — "
          "does the factor move visibly around these dates, not necessarily "
          "in any particular direction, since factor sign is arbitrary):\n")
    for label, (start, end) in REFERENCE_EVENTS.items():
        window = clean_factor.loc[start:end]
        if window.empty:
            print(f"  {label}: no data in this window (outside backfilled range)")
            continue
        print(f"  {label} ({start} to {end}):")
        print(f"    min={window.min():.3f}  max={window.max():.3f}  "
              f"range={window.max()-window.min():.3f}")

    overall_std = clean_factor.std()
    print(f"\n[FIT] Overall factor std dev (outliers excluded): {overall_std:.3f} "
          f"— reference events with a range well above this (roughly >1.5x) "
          f"suggest the factor is actually reacting to those periods, not just noise.")
    if not outliers.empty:
        print(f"\n[FIT] Note: {len(outliers)} outlier point(s) were excluded from "
              f"this analysis but remain in dfm_factor_score.csv — if MANY points "
              f"look like outliers (not just one or two), that's a sign the model "
              f"needs more overlapping data before it's trustworthy, not just a "
              f"quirk to filter around. Use judgment here.")


if __name__ == "__main__":
    main()
