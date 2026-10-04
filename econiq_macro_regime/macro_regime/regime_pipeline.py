"""
Production regime-scoring pipeline (roadmap Phase 1). Fits the same
validated DFM config fit_real_data.py proved out -- k_factors=1, data
restricted to SINCE (2011-12-01, where policy_rate actually has data),
EXCLUDE_FROM_DFM_FIT applied -- and writes results into regime_cache, a
derived, UPSERTED cache table (see schema/002_regime_cache.sql). This is
NOT a re-validation of the modeling choices; those are settled. This just
turns the one-off script into something you can run repeatedly and trust
the output of.

Two modes:
  --mode refit    Full EM re-fit over the whole validated history, writes
                   every resulting factor-score row via upsert. Run this
                   monthly-ish -- it's the expensive path.
  --mode nowcast  Fits on history through the second-to-latest month, then
                   uses dfm_model.nowcast() to append just the latest
                   (possibly ragged/partial) row via a cheap Kalman update
                   (refit=False), and writes ONLY that one row. Run this
                   daily/frequently.

KNOWN LIMITATION (flagged, not fixed here): nowcast mode still calls
fit_dfm() once per invocation, because no fitted DynamicFactorMQResults
state is persisted between runs -- there's nothing to hand dfm_model's
nowcast() except a freshly fit model. What's actually cheap about this mode
relative to --mode refit is that it only writes one row instead of
rewriting the whole historical series. A genuinely cheap daily update needs
the fitted model state saved (e.g. pickled) after each refit and loaded
here instead of re-fit. Follow-up, not done in this pass.

Usage:
    python regime_pipeline.py --mode refit
    python regime_pipeline.py --mode nowcast
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from supabase import create_client

from fit_real_data import (
    _load_secrets,
    load_wide_frame,
    prepare_real_dfm_data,
    restrict_wide_frame_since,
    EXCLUDE_FROM_DFM_FIT,
)
from dfm_model import fit_dfm, nowcast

MODEL_VERSION = "dfm_v1_6indicator"
SINCE = "2011-12-01"  # validated fit window -- see fit_real_data.py's --since comment
ECONOMY = "IN"


def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _prepare_transformed_frame():
    """Load, PIT-select, then transform the validated real-data fit window."""
    wide = load_wide_frame(ECONOMY)
    wide = restrict_wide_frame_since(wide, SINCE)
    prepared = prepare_real_dfm_data(wide)
    transformed = prepared["transformed_panel"]
    if transformed.empty or transformed.dropna(how="all").empty:
        raise RuntimeError(
            "Point-in-time selection left no usable regime pipeline observations. "
            "Check source ingested_at metadata; no values were admitted without it."
        )
    excluded_present = [c for c in EXCLUDE_FROM_DFM_FIT if c in transformed.columns]
    if excluded_present:
        transformed = transformed.drop(columns=excluded_present)
    return transformed


def _upsert_rows(supabase, rows: list[dict]) -> None:
    """Upserts rows into regime_cache on (economy, period_date,
    model_version). Unlike macro_timeseries' insert-only rule, this
    deliberately overwrites any prior score for the same key -- regime_cache
    is a derived cache, not a history store. Batches in chunks of 500, same
    convention as ingest.py's insert_rows."""
    if not rows:
        return
    for i in range(0, len(rows), 500):
        chunk = rows[i : i + 500]
        supabase.table("regime_cache").upsert(
            chunk, on_conflict="economy,period_date,model_version"
        ).execute()


def run_refit() -> int:
    """Full re-fit over the whole validated history. Writes every resulting
    factor-score row via upsert. Returns the number of rows written."""
    transformed = _prepare_transformed_frame()
    print(
        f"[REGIME_PIPELINE] refit: fitting on {transformed.shape[0]} months, "
        f"columns={list(transformed.columns)}",
        flush=True,
    )

    results = fit_dfm(transformed, k_factors=1)
    factor = results.factors.smoothed.iloc[:, 0]
    factor.index = transformed.index
    factor = factor.dropna()

    computed_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "economy": ECONOMY,
            "period_date": dt.date().isoformat(),
            "factor_score": float(val),
            "model_version": MODEL_VERSION,
            "computed_at": computed_at,
        }
        for dt, val in factor.items()
    ]

    supabase = _get_supabase()
    _upsert_rows(supabase, rows)
    print(
        f"[REGIME_PIPELINE] MODE=refit: wrote {len(rows)} rows to regime_cache "
        f"(model_version={MODEL_VERSION})",
        flush=True,
    )
    return len(rows)


def run_nowcast() -> int:
    """Fits on history through the second-to-latest month, then uses
    dfm_model.nowcast() to cheaply append just the latest (possibly partial)
    row. Writes exactly one row. Returns the number of rows written (0 or 1)."""
    transformed = _prepare_transformed_frame()
    if transformed.shape[0] < 2:
        print(
            "[REGIME_PIPELINE] nowcast: fewer than 2 months of data available "
            "-- can't fit history-minus-latest, aborting.",
            file=sys.stderr,
        )
        return 0

    history = transformed.iloc[:-1]
    latest_row = transformed.iloc[-1]
    latest_date = transformed.index[-1]

    print(
        f"[REGIME_PIPELINE] nowcast: fitting on {history.shape[0]} months "
        f"(through {history.index[-1].date()}), then nowcasting {latest_date.date()}",
        flush=True,
    )

    fitted_results = fit_dfm(history, k_factors=1)
    latest_score = nowcast(fitted_results, latest_row)

    computed_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "economy": ECONOMY,
            "period_date": latest_date.date().isoformat(),
            "factor_score": float(latest_score),
            "model_version": MODEL_VERSION,
            "computed_at": computed_at,
        }
    ]

    supabase = _get_supabase()
    _upsert_rows(supabase, rows)
    print(
        f"[REGIME_PIPELINE] MODE=nowcast: wrote 1 row to regime_cache for "
        f"{latest_date.date()} (model_version={MODEL_VERSION})",
        flush=True,
    )
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=["refit", "nowcast"],
        help="'refit' = full re-fit + write the whole factor series (run monthly-ish). "
             "'nowcast' = cheap single-row update via dfm_model.nowcast() (run daily).",
    )
    args = parser.parse_args()

    _load_secrets()

    if args.mode == "refit":
        run_refit()
    else:
        run_nowcast()


if __name__ == "__main__":
    main()
