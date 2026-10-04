"""
Second, separate validation script — a longer-history (1990-present) version of
fit_real_data.py, built specifically to sanity-check the regime factor against
the 2008-09 Global Financial Crisis, which the main 2011-present model can't
reach (policy_rate and exports_value simply have no data before 2011/2006).

This is NOT a replacement for fit_real_data.py / the 6-indicator 2011-present
model — that one stays as-is, it's already validated (3 of 4 reference events
clearly react). This is a second, independent check using only the 4
indicators that genuinely have data back to 1990:
    cpi_inflation, currency_inr_usd, stock_market_growth, gdp_growth
policy_rate, exports_value, and unemployment_rate are deliberately excluded
here — not as a numerical-stability workaround, but because they simply don't
exist for most of this window (policy_rate starts Dec 2011, exports_value
starts Jan 2006, unemployment_rate starts 1991 and is annual). Feeding a
model a column that's 100% empty for 20+ years isn't "ragged edge", it's
zero information, and it's the kind of thing that broke the fit before.

Usage:
    python fit_long_history.py                    # defaults to --since 1990-01-01
    python fit_long_history.py --since 1985-01-01  # override if you want to push further
"""
from __future__ import annotations

from pathlib import Path

from dfm_model import fit_dfm
from fit_real_data import (
    _load_secrets,
    load_wide_frame,
    prepare_real_dfm_data,
    restrict_wide_frame_since,
    REFERENCE_EVENTS,
)

# Only these 4 have real (non-empty) data back to 1990 — confirmed against
# FRED/World Bank directly when each was added to ingest.py's registries.
LONG_HISTORY_INDICATORS = [
    "cpi_inflation", "currency_inr_usd", "stock_market_growth", "gdp_growth",
]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since", default="1990-01-01",
        help="Start date (YYYY-MM-DD). Defaults to 1990-01-01, per the "
             "explicit requirement to reach that far back. Can't usefully go "
             "much earlier than 1957 (cpi_inflation/stock_market_growth's "
             "own start) or 1961 (gdp_growth's start) regardless of what "
             "you pass here."
    )
    parser.add_argument(
        "--as-of", default=None,
        help="Optional historical timestamp (ISO-8601); defaults to the current UTC timestamp.",
    )
    args = parser.parse_args()

    _load_secrets()
    wide = load_wide_frame()
    wide = restrict_wide_frame_since(wide, args.since)
    print(f"[FIT-LONG] Restricted to data from {args.since} onward "
          f"({wide.shape[0]} months)\n")
    print(f"[FIT-LONG] Using indicators: {LONG_HISTORY_INDICATORS} "
          f"(policy_rate, exports_value, unemployment_rate excluded — see "
          f"module docstring for why)\n")
    print(f"[FIT-LONG] Non-null counts per column:\n{wide[[c for c in LONG_HISTORY_INDICATORS if c in wide.columns]].count()}\n")

    prepared = prepare_real_dfm_data(wide, as_of=args.as_of)
    transformed = prepared["transformed_panel"].reindex(columns=LONG_HISTORY_INDICATORS)
    if transformed.empty or transformed.dropna(how="all").empty:
        raise RuntimeError(
            "Point-in-time selection left no usable long-history DFM observations. "
            "Check source ingested_at metadata; no values were admitted without it."
        )

    results = fit_dfm(transformed, k_factors=1)
    factor = results.factors.smoothed.iloc[:, 0]
    factor.index = transformed.index

    out_path = Path("dfm_factor_score_long_history.csv")
    factor.to_frame("factor_score").to_csv(out_path)
    print(f"[FIT-LONG] Full factor series written to {out_path.resolve()}\n")

    # Same MAD-based outlier guard as fit_real_data.py, same reasoning: a
    # ragged-edge DFM can produce a wild single-point spike that isn't a real
    # regime shift, and a plain std dev is badly distorted by even one.
    median = factor.median()
    mad = (factor - median).abs().median()
    robust_threshold = median + 15 * mad * 1.4826
    outliers = factor[(factor - median).abs() > robust_threshold]
    if not outliers.empty:
        print(f"[FIT-LONG] WARNING: {len(outliers)} likely numerical-artifact "
              f"outlier(s) detected (>15 robust-MAD from the median) — "
              f"excluding from the event comparison, still in the saved CSV:")
        for dt, val in outliers.items():
            print(f"    {dt.date()}: {val:.3f}")
        print()
    clean_factor = factor.drop(outliers.index)

    print("[FIT-LONG] Factor score around known reference events (this run "
          "can reach the 2008-09 GFC, which the 2011-present model can't):\n")
    for label, (start, end) in REFERENCE_EVENTS.items():
        window = clean_factor.loc[start:end]
        if window.empty:
            print(f"  {label}: no data in this window (outside backfilled/requested range)")
            continue
        print(f"  {label} ({start} to {end}):")
        print(f"    min={window.min():.3f}  max={window.max():.3f}  "
              f"range={window.max()-window.min():.3f}")

    overall_std = clean_factor.std()
    print(f"\n[FIT-LONG] Overall factor std dev (outliers excluded): {overall_std:.3f} "
          f"— reference events with a range well above this (roughly >1.5x) "
          f"suggest the factor is actually reacting to those periods, not just noise.")
    print(f"\n[FIT-LONG] Reminder: this run only has 4 indicators (vs. 6 in the "
          f"main model), so treat it as a sanity check against 2008, not a "
          f"replacement for the validated 2011-present model.")


if __name__ == "__main__":
    main()
