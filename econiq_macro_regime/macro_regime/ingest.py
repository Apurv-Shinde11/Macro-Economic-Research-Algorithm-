"""
One-time backfill + ongoing monthly append: pulls full historical series from
FRED and World Bank directly (NOT from global_macro_cache, which holds no
history — see the roadmap doc) and inserts rows into macro_timeseries.

Credentials are loaded from .streamlit/secrets.toml, the same file and the
same pattern test_coalescing.py already uses — no separate env-var setup
needed if you run this from inside the project directory (or point
SECRETS_TOML_PATH at it, see below).

Usage:
    python ingest.py --backfill          # full history, run once
    python ingest.py --append-latest     # just the newest release(s), run monthly

This script is intentionally NOT wired into main_api.py or any request path.
It's a standalone batch job — run it manually, via a Render cron job, or
whatever scheduling roadmap step "where does the batch job run" resolves to.
"""
from __future__ import annotations

import argparse
import os
import sys
import tomllib
from datetime import date
from pathlib import Path

import requests
from supabase import create_client

try:
    import pandas as pd
except ImportError:
    pd = None  # only needed for FRED_RESAMPLE_TO_MONTHLY series; see resample_daily_to_monthly


def _load_secrets():
    """
    Mirrors test_coalescing.py's own bootstrap exactly: reads
    .streamlit/secrets.toml and sets the three env vars this script needs.
    Looks in the current working directory first, then falls back to
    SECRETS_TOML_PATH if set — so this works whether you run
    `python ingest.py` from the project root or from wherever this file
    happens to live.
    """
    candidates = [
        Path.cwd() / ".streamlit" / "secrets.toml",
        Path(os.environ.get("SECRETS_TOML_PATH", "")) if os.environ.get("SECRETS_TOML_PATH") else None,
    ]
    secrets_path = next((p for p in candidates if p and p.exists()), None)
    if secrets_path is None:
        print(
            "[INGEST] Could not find .streamlit/secrets.toml in the current "
            "directory. Either run this script from your project root (the "
            "same place you run test_coalescing.py from), or set "
            "SECRETS_TOML_PATH to the full path of secrets.toml.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(secrets_path, "rb") as f:
        secrets = tomllib.load(f)

    required = ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "FRED_API_KEY"]
    missing = [k for k in required if k not in secrets]
    if missing:
        print(f"[INGEST] secrets.toml is missing required key(s): {missing}", file=sys.stderr)
        sys.exit(1)

    for k in required:
        os.environ[k] = str(secrets[k])
    print(f"[INGEST] Loaded credentials from {secrets_path}", flush=True)


# --- indicator registry -----------------------------------------------------
# Phase 1 MVP scope per the roadmap: India only, 3 indicators. Deliberately
# small and explicit — do not add more economies/indicators until this list
# is validated end-to-end (roadmap step 6, the go/no-go checkpoint).
FRED_SERIES = {
    # CONFIRMED 2026-09-29 on fred.stlouisfed.org: "Consumer Price Index:
    # Total for India", Index 2015=100, Not Seasonally Adjusted, monthly,
    # Jan 1957-present. This is a raw INDEX LEVEL, not a rate — it goes
    # through transforms.yoy_from_index(periods_per_year=12) before the DFM,
    # never passthrough. See transforms.py's TRANSFORM_REGISTRY comment.
    "cpi_inflation": "INDCPIALLMINMEI",
    # CONFIRMED 2026-09-29: this is "Interest Rates: Long-Term Government
    # Bond Yields: 10-Year ... for India" — a 10Y sovereign bond yield,
    # NOT the RBI repo rate. FRED has no direct India repo-rate series.
    # Used here as a policy-stance PROXY for the Phase 1 MVP only. If the
    # real repo rate becomes available (see the deferred RBI/Raylight
    # investigation), swap it in and re-backfill — don't quietly keep
    # calling this "policy_rate" in anything advisor-facing without the
    # proxy caveat.
    "policy_rate": "INDIRLTLT01STM",
    # CONFIRMED 2026-09-29: "Indian Rupees to U.S. Dollar Spot Exchange Rate",
    # DAILY, Jan 1973-present, units = INR per 1 USD (a raw price level, not
    # a rate — goes through transforms.mom_log_diff, same treatment as CPI).
    # Added specifically because it's frequent (daily->monthly) and has long
    # history, to give the DFM more to work with than gdp_growth's once-a-
    # year signal alone. fetch_fred_series() resamples this one to monthly
    # (last trading day of each month) before it's inserted — see backfill().
    "currency_inr_usd": "DEXINUS",
    # CONFIRMED 2026-09-29: "Share Prices: All Shares/Broad: Total for India"
    # (OECD MEI). Units are already "Growth rate previous period" — i.e.
    # already a MoM % change, NOT a raw index level. So this one is a
    # passthrough, unlike the other price-type series above. Monthly,
    # Feb 1957-present (still actively updated, checked directly).
    "stock_market_growth": "SPASTT01INM657N",
    # CONFIRMED 2026-09-29: "Goods, Value of Exports for India" (IMF IFS),
    # raw USD value, NOT seasonally adjusted, monthly, Jan 2006-present
    # (still actively updated). Because it's not seasonally adjusted, a
    # plain month-over-month change would be dominated by ordinary seasonal
    # swings (exports are always higher in some months than others) rather
    # than actual regime shifts — so this uses yoy_from_index (compare to
    # the same month last year), same as cpi_inflation, which cancels out
    # the seasonal pattern.
    "exports_value": "VALEXPINM052N",
}

WORLD_BANK_SERIES = {
    # Already YoY % at source (see transforms.py's already_stationary_passthrough).
    # World Bank's own data is annual only — confirm this is an acceptable
    # frequency for gdp_growth in the DFM, or find a quarterly alternative,
    # before backfilling at scale.
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",
    # CONFIRMED 2026-09-29: "Unemployment, total (% of total labor force,
    # modeled ILO estimate)", ANNUAL, 1991-2025. This is a LEVEL (a
    # persistent %), not already a rate of change — same situation as
    # policy_rate, not the same as gdp_growth/cpi_inflation. Goes through
    # transforms.first_difference. Also annual-only like gdp_growth, so it
    # shares that same sparsity limitation — added anyway since more
    # correlated series generally helps the DFM, but don't expect this one
    # alone to fix the "too sparse" problem; currency_inr_usd above is the
    # indicator actually meant to fix that.
    "unemployment_rate": "SL.UEM.TOTL.ZS",
}

# Series that need resampling from their native frequency down to monthly
# before insertion, so every row in macro_timeseries represents "the value
# for this month" consistently across indicators. Currently just the daily
# FX rate; add here if a future indicator has the same issue.
FRED_RESAMPLE_TO_MONTHLY = {"currency_inr_usd"}

ECONOMY = "IN"


def _get_supabase():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


def fetch_fred_series(series_id: str, api_key: str) -> list[tuple[date, float]]:
    """Full history for one FRED series. Returns (period_date, value) pairs,
    skipping FRED's '.' missing-value sentinel rather than inserting garbage."""
    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for obs in resp.json().get("observations", []):
        if obs["value"] == ".":
            continue  # FRED's missing-value sentinel — do not insert as 0 or NaN-as-string
        out.append((date.fromisoformat(obs["date"]), float(obs["value"])))
    return out


def resample_daily_to_monthly(rows: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """
    Collapses daily (date, value) pairs down to one row per month — the
    value from the last trading day of that month — so a daily series like
    the FX rate lines up with everything else in macro_timeseries, which is
    monthly. Uses pandas purely as a grouping convenience here, not for any
    modeling; requires pandas to be installed (it already is, via dfm_model's
    dependencies).
    """
    if pd is None:
        raise RuntimeError("pandas is required to resample a daily series — pip install pandas")
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    monthly = df.resample("MS").last()  # last trading day's value, tagged to month start
    monthly = monthly.dropna()
    return [(d.date(), float(v)) for d, v in monthly["value"].items()]


def fetch_world_bank_series(country_code: str, indicator_code: str) -> list[tuple[date, float]]:
    """Full history for one World Bank indicator. No API key required."""
    out: list[tuple[date, float]] = []
    page = 1
    while True:
        resp = requests.get(
            f"https://api.worldbank.org/v2/country/{country_code}/indicator/{indicator_code}",
            params={"format": "json", "per_page": 200, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if len(payload) < 2:
            break
        meta, rows = payload[0], payload[1]
        for row in rows:
            if row["value"] is not None:
                out.append((date.fromisoformat(f"{row['date']}-01-01"), float(row["value"])))
        if page >= meta.get("pages", 1):
            break
        page += 1
    return out


def insert_rows(supabase, indicator: str, source: str, rows: list[tuple[date, float]]):
    """
    INSERT ONLY — see schema/001_macro_timeseries.sql. Never upsert here;
    that would recreate the exact history-loss bug macro_timeseries exists
    to avoid. Batches in chunks of 500 to stay well under typical request
    size limits.
    """
    if not rows:
        print(f"[INGEST] {indicator} ({source}): no rows returned, nothing to insert", flush=True)
        return
    payload = [
        {
            "economy": ECONOMY,
            "indicator": indicator,
            "period_date": d.isoformat(),
            "value": v,
            "source": source,
        }
        for d, v in rows
    ]
    for i in range(0, len(payload), 500):
        chunk = payload[i : i + 500]
        supabase.table("macro_timeseries").insert(chunk).execute()
    print(f"[INGEST] {indicator} ({source}): inserted {len(payload)} rows "
          f"({rows[0][0]} to {rows[-1][0]})", flush=True)


def _indicators_already_present(supabase) -> set[str]:
    """
    Returns the set of indicator names that already have at least one row
    in macro_timeseries for this economy. Used so re-running --backfill
    (e.g. after adding a new indicator to the registries) only fetches
    what's actually new, instead of inserting duplicate rows for indicators
    already backfilled — macro_timeseries is insert-only, so a naive re-run
    would otherwise double up every existing row.
    """
    resp = (
        supabase.table("macro_timeseries")
        .select("indicator")
        .eq("economy", ECONOMY)
        .execute()
    )
    return {row["indicator"] for row in (resp.data or [])}


def backfill(force: bool = False):
    supabase = _get_supabase()
    fred_key = os.environ["FRED_API_KEY"]

    already_present = set() if force else _indicators_already_present(supabase)
    if already_present:
        print(f"[INGEST] Already have data for: {sorted(already_present)} — "
              f"skipping these (use --force to re-fetch and duplicate them "
              f"anyway, not recommended).\n", flush=True)

    for indicator, series_id in FRED_SERIES.items():
        if indicator in already_present:
            print(f"[INGEST] {indicator}: skipped, already backfilled", flush=True)
            continue
        try:
            rows = fetch_fred_series(series_id, fred_key)
            if indicator in FRED_RESAMPLE_TO_MONTHLY:
                original_count = len(rows)
                rows = resample_daily_to_monthly(rows)
                print(f"[INGEST] {indicator}: resampled {original_count} daily rows "
                      f"down to {len(rows)} monthly rows", flush=True)
            insert_rows(supabase, indicator, "FRED", rows)
        except Exception as e:
            print(f"[INGEST] FAILED {indicator} ({series_id}): {e}", flush=True)

    for indicator, indicator_code in WORLD_BANK_SERIES.items():
        if indicator in already_present:
            print(f"[INGEST] {indicator}: skipped, already backfilled", flush=True)
            continue
        try:
            rows = fetch_world_bank_series(ECONOMY, indicator_code)
            insert_rows(supabase, indicator, "WORLD_BANK", rows)
        except Exception as e:
            print(f"[INGEST] FAILED {indicator} ({indicator_code}): {e}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="Full history, run once")
    parser.add_argument("--force", action="store_true", help="Re-fetch and re-insert indicators "
                         "that already have data in macro_timeseries, instead of skipping them. "
                         "This WILL create duplicate rows (the table is insert-only) — only use "
                         "this if you specifically want that, e.g. testing. Normally you don't "
                         "need this: --backfill already skips indicators it finds are present "
                         "and only fetches what's missing.")
    parser.add_argument("--append-latest", action="store_true", help="Not yet implemented — "
                         "backfill() is idempotent-safe to rerun since it's insert-only and "
                         "FRED/WB will just return the same history again, but that's wasteful "
                         "for a monthly cron. Implement a 'only insert rows newer than MAX(period_date) "
                         "already in macro_timeseries for this indicator' query before scheduling this.")
    args = parser.parse_args()

    _load_secrets()

    if args.backfill:
        backfill(force=args.force)
    elif args.append_latest:
        print("--append-latest is not implemented yet — see the argparse help text.", file=sys.stderr)
        sys.exit(1)
    else:
        parser.print_help()