"""
Stationarity transforms for macro_timeseries data, ahead of DFM/BVAR fitting.

DynamicFactorMQ (like any DFM/VAR) assumes stationary inputs. Feeding it raw
levels (raw CPI index, raw GDP index) produces a model that looks like it fit
fine but outputs garbage — this is the failure mode Gemini flagged, and it's
silent unless you specifically test for it. Every series must go through one
of these transforms before touching the model, with no exceptions and no
"just this once, raw" shortcuts.

Convention used across this module and enforced by the tests below:
  - Price/level indices (CPI, price index)      -> month-over-month log-difference
  - Growth-rate series already expressed as %    -> used as-is (already stationary
                                                     by construction, e.g. World Bank's
                                                     NY.GDP.MKTP.KD.ZG is already a YoY %)
  - Rate series (policy rate, yields)            -> first difference (levels are
                                                     highly persistent/non-stationary,
                                                     but the *changes* are stationary)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def mom_log_diff(levels: pd.Series) -> pd.Series:
    """
    Month-over-month log difference: ln(x_t) - ln(x_t-1).

    Use for price/level indices (CPI index, not CPI already expressed as a
    YoY % — see yoy_from_index for that case). Approximates the MoM
    percentage change for small changes, and is the standard transform for
    price series in DFM/VAR work because it's symmetric and additive across
    periods (unlike simple percentage change).

    Raises if any value is <= 0, since log is undefined there and a silent
    NaN would otherwise propagate invisibly into the model.
    """
    if (levels <= 0).any():
        bad = levels[levels <= 0]
        raise ValueError(
            f"mom_log_diff received non-positive level(s), cannot take log: "
            f"{bad.to_dict()}"
        )
    return np.log(levels).diff()


def yoy_from_index(levels: pd.Series, periods_per_year: int) -> pd.Series:
    """
    Year-over-year percentage change from a level index, e.g. monthly CPI
    index -> YoY inflation %. periods_per_year=12 for monthly data, 4 for
    quarterly.
    """
    if (levels <= 0).any():
        bad = levels[levels <= 0]
        raise ValueError(
            f"yoy_from_index received non-positive level(s): {bad.to_dict()}"
        )
    return levels.pct_change(periods=periods_per_year, fill_method=None) * 100.0


def first_difference(levels: pd.Series) -> pd.Series:
    """
    Simple first difference: x_t - x_t-1.

    Use for rate series (policy rate, bond yields) where the level is
    highly persistent (non-stationary) but period-over-period changes are
    stationary. Also the right choice for a series already expressed as a
    percentage where a log transform doesn't make sense (e.g. a rate that
    can legitimately be at or near zero).
    """
    return levels.diff()


def already_stationary_passthrough(rate_series: pd.Series) -> pd.Series:
    """
    For series that are already stationary by construction — e.g. World
    Bank's NY.GDP.MKTP.KD.ZG (annual GDP growth %) or FP.CPI.TOTL.ZG
    (annual inflation %) are already YoY percentage changes, not levels.

    This function exists so every series in the pipeline is *explicitly*
    routed through a named transform function — including "no transform" —
    rather than some series silently skipping the transform step because a
    caller forgot to call anything. See test_all_indicators_have_an_explicit_transform.
    """
    return rate_series.copy()


# Explicit registry: every indicator EconIQ currently tracks must have an
# entry here. This is deliberately a hard requirement (see the test below)
# so a newly-added indicator can't silently reach the model untransformed.
TRANSFORM_REGISTRY = {
    # CONFIRMED 2026-09-29 against FRED directly: INDCPIALLMINMEI is a raw
    # index level (2015=100, monthly, Jan 1957-present), NOT already a rate.
    # Must go through yoy_from_index (periods_per_year=12), not passthrough.
    "cpi_inflation": yoy_from_index,
    "gdp_growth": already_stationary_passthrough,      # World Bank NY.GDP.MKTP.KD.ZG — confirmed already YoY %
    # CONFIRMED 2026-09-29: the FRED series wired up for this (INDIRLTLT01STM)
    # is India's 10Y government bond yield, NOT RBI's repo rate — FRED has no
    # direct repo-rate series for India. Using the yield as a policy-stance
    # PROXY for Phase 1, first-differenced since the level is non-stationary.
    # Replace with the real repo rate later if/when the Raylight
    # investigation resolves. Do not present this to advisors as "the repo
    # rate" without that caveat.
    "policy_rate": first_difference,
    # CONFIRMED 2026-09-29: DEXINUS is a raw price level (INR per USD, daily,
    # resampled to monthly in ingest.py). Same treatment as a price index —
    # mom_log_diff, not passthrough.
    "currency_inr_usd": mom_log_diff,
    # CONFIRMED 2026-09-29: World Bank's unemployment series is a persistent
    # LEVEL (a %), not already a rate of change — same situation as
    # policy_rate, needs first_difference, not passthrough.
    "unemployment_rate": first_difference,
    # CONFIRMED 2026-09-29 against FRED directly: SPASTT01INM657N is already
    # expressed as "growth rate previous period" (a MoM % change), not a raw
    # index level — passthrough, same reasoning as gdp_growth.
    "stock_market_growth": already_stationary_passthrough,
    # CONFIRMED 2026-09-29: VALEXPINM052N is a raw, not-seasonally-adjusted
    # USD export value — needs yoy_from_index (compare to the same month a
    # year ago) so ordinary seasonal export patterns don't get mistaken for
    # a regime shift. Same treatment as cpi_inflation.
    "exports_value": yoy_from_index,
}


def apply_transform(indicator: str, series: pd.Series, **kwargs) -> pd.Series:
    """Look up and apply the registered transform for `indicator` by name."""
    if indicator not in TRANSFORM_REGISTRY:
        raise KeyError(
            f"No transform registered for indicator '{indicator}'. "
            f"Add it to TRANSFORM_REGISTRY explicitly — do not guess a default."
        )
    return TRANSFORM_REGISTRY[indicator](series, **kwargs)
