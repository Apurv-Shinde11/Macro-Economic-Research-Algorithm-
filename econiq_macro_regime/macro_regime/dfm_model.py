"""
Thin wrapper around statsmodels' DynamicFactorMQ — the mixed-frequency DFM
implementation Gemini correctly recommended over a hand-rolled BVAR for a
solo-maintained pipeline.

This module deliberately does NOT know about Supabase, FRED, or World Bank.
It takes a clean, already-transformed, already-stationary pandas DataFrame
(wide format: DatetimeIndex, one column per indicator) and does the model
fit/nowcast. Keeping it pure makes it independently testable, which is what
sanity_check_fit() below actually does in this sandbox.
"""
from __future__ import annotations

import pandas as pd
from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ


def fit_dfm(wide_df: pd.DataFrame, k_factors: int = 1, factor_orders: int = 1):
    """
    Fit a DynamicFactorMQ model.

    wide_df: DataFrame with a DatetimeIndex and one column per (already
        stationarity-transformed) indicator. Missing values (the "ragged
        edge" — e.g. this month's policy rate is in but GDP for the quarter
        isn't out yet) should be left as NaN, NOT dropped and NOT
        forward-filled. The Kalman filter underlying DynamicFactorMQ handles
        NaN natively; dropna() or ffill() here would silently discard the
        exact information the mixed-frequency handling exists to use.

    k_factors: number of latent factors. Start at 1 for the Phase 1 MVP
        (single "regime" factor) — only increase once a 1-factor model is
        validated and you have a concrete reason (e.g. separating a growth
        factor from an inflation factor).

    Returns the fitted results object. Callers persist this (e.g. via
    `.save()`/`DynamicFactorMQResults.load()`) rather than refitting on
    every request — see the roadmap's "batch refit, not per-request" cadence.
    """
    if wide_df.isna().all(axis=None):
        raise ValueError("wide_df is entirely NaN — nothing to fit against.")

    model = DynamicFactorMQ(
        wide_df,
        factors=k_factors,
        factor_orders=factor_orders,
    )
    return model.fit(disp=False)


def nowcast(fitted_results, new_partial_row: pd.Series) -> float:
    """
    Cheap Kalman-filter update with one new (possibly partial/ragged) row of
    data, returning the latest factor estimate WITHOUT a full refit.

    This is what should run frequently (e.g. daily, as new prints land)
    between the infrequent full refits — see roadmap Phase 1 cadence.
    """
    appended = fitted_results.append(new_partial_row.to_frame().T, refit=False)
    return float(appended.factors.filtered.iloc[-1, 0])


def sanity_check_fit() -> dict:
    """
    Self-contained sanity check, no external data or credentials required.
    Confirms DynamicFactorMQ imports correctly and converges on a small
    synthetic mixed-frequency panel with a deliberately planted common
    factor (a sine-wave cycle) plus noise, contaminated with ragged-edge
    NaNs the way real vintage data would be.

    This is NOT a validation that the model works on real India data — it's
    a check that the tooling itself (statsmodels version, environment,
    NaN handling) works before wiring in real data. Roadmap step 6 (the
    go/no-go checkpoint) still needs to run against real backfilled data.
    """
    import numpy as np

    rng = np.random.default_rng(42)
    n = 120  # 10 years of monthly data
    dates = pd.date_range("2016-01-01", periods=n, freq="MS")

    common_factor = np.sin(np.linspace(0, 6 * np.pi, n))  # planted shared cycle
    monthly_a = common_factor + rng.normal(scale=0.2, size=n)
    monthly_b = 0.8 * common_factor + rng.normal(scale=0.2, size=n)

    # Quarterly series: only has a real value every 3rd month, NaN elsewhere —
    # this mimics GDP's real release cadence and the ragged edge at the end.
    quarterly = np.full(n, np.nan)
    quarterly[2::3] = (0.6 * common_factor + rng.normal(scale=0.2, size=n))[2::3]
    quarterly[-2:] = np.nan  # ragged edge: not yet released for latest 2 months

    wide_df = pd.DataFrame(
        {"monthly_a": monthly_a, "monthly_b": monthly_b, "quarterly_c": quarterly},
        index=dates,
    )

    # mle_retvals has no reliable 'converged' key in the installed statsmodels
    # version (it's silently absent, not False) — a naive .get(..., True)
    # would report "converged: True" even when EM hit max iterations without
    # meeting tolerance. Capture the real ConvergenceWarning instead, since
    # that's the only trustworthy signal statsmodels actually emits here.
    import warnings
    from statsmodels.tools.sm_exceptions import ConvergenceWarning

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        results = fit_dfm(wide_df, k_factors=1)
        hit_convergence_warning = any(
            issubclass(w.category, ConvergenceWarning) for w in caught
        )

    extracted_factor = results.factors.smoothed.iloc[:, 0].values

    # The extracted factor should correlate strongly with the planted common
    # factor (sign is arbitrary in factor models, so take abs of correlation).
    correlation = float(np.corrcoef(extracted_factor, common_factor)[0, 1])

    return {
        "converged": not hit_convergence_warning,
        "abs_correlation_with_planted_factor": abs(correlation),
        "n_observations": n,
        # Deliberately pass/fail on correlation, NOT on convergence — EM can
        # hit max-iterations without meeting its tolerance while already
        # having recovered a near-correct factor (that's what happens here).
        # A real production fit should still raise em_maxiter if this
        # matters, but "did it converge to machine tolerance" and "is the
        # factor useful" are different questions; don't conflate them.
        "pass": abs(correlation) > 0.7,
    }


if __name__ == "__main__":
    result = sanity_check_fit()
    print(result)
    assert result["pass"], (
        f"DFM sanity check failed: extracted factor only correlates at "
        f"{result['abs_correlation_with_planted_factor']:.3f} with the "
        f"planted common factor (expected > 0.7). Check the statsmodels "
        f"environment before trusting any real fit."
    )
    print("PASS: DynamicFactorMQ correctly recovers a planted common factor "
          "from a ragged-edge mixed-frequency panel.")
