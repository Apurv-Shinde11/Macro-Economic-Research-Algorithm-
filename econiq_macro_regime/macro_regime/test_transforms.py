"""
Unit tests for transforms.py, run against known values so a broken transform
fails loudly in CI rather than silently corrupting a model fit months later.
"""
import numpy as np
import pandas as pd
import pytest

from transforms import (
    mom_log_diff,
    yoy_from_index,
    first_difference,
    already_stationary_passthrough,
    apply_transform,
    TRANSFORM_REGISTRY,
)


def test_mom_log_diff_known_value():
    # 2% MoM growth: ln(102/100) ≈ 0.019803
    levels = pd.Series([100.0, 102.0])
    result = mom_log_diff(levels)
    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(0.019803, abs=1e-5)


def test_mom_log_diff_rejects_non_positive():
    levels = pd.Series([100.0, -5.0, 102.0])
    with pytest.raises(ValueError, match="non-positive"):
        mom_log_diff(levels)


def test_mom_log_diff_rejects_zero():
    levels = pd.Series([100.0, 0.0])
    with pytest.raises(ValueError, match="non-positive"):
        mom_log_diff(levels)


def test_yoy_from_index_known_value():
    # 12 months of flat 100 then a 110 in month 13 -> 10% YoY at month 13
    levels = pd.Series([100.0] * 12 + [110.0])
    result = yoy_from_index(levels, periods_per_year=12)
    assert result.iloc[-1] == pytest.approx(10.0, abs=1e-6)
    # first 12 entries have no prior-year comparison -> NaN
    assert result.iloc[:12].isna().all()


def test_yoy_from_index_does_not_fill_missing_values_implicitly():
    levels = pd.Series([100.0, np.nan] + [100.0] * 11 + [110.0])
    result = yoy_from_index(levels, periods_per_year=12)
    assert np.isnan(result.iloc[-1])


def test_first_difference_known_value():
    levels = pd.Series([5.5, 5.75, 5.5])
    result = first_difference(levels)
    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(0.25, abs=1e-9)
    assert result.iloc[2] == pytest.approx(-0.25, abs=1e-9)


def test_already_stationary_passthrough_is_a_copy_not_same_object():
    series = pd.Series([1.0, 2.0, 3.0])
    result = already_stationary_passthrough(series)
    assert result.equals(series)
    assert result is not series  # mutating result must not mutate the caller's series


def test_apply_transform_dispatches_by_indicator_name():
    levels = pd.Series([5.5, 5.75])
    result = apply_transform("policy_rate", levels)
    assert result.iloc[1] == pytest.approx(0.25, abs=1e-9)


def test_apply_transform_rejects_unknown_indicator():
    with pytest.raises(KeyError, match="No transform registered"):
        apply_transform("some_new_indicator_nobody_registered", pd.Series([1.0, 2.0]))


def test_cpi_inflation_now_correctly_routes_through_yoy_from_index():
    # Regression test for the 2026-09-29 fix: INDCPIALLMINMEI is a raw index
    # (confirmed against FRED directly), so cpi_inflation must NOT be a
    # passthrough — it needs periods_per_year explicitly supplied.
    levels = pd.Series([100.0] * 12 + [110.0])
    result = apply_transform("cpi_inflation", levels, periods_per_year=12)
    assert result.iloc[-1] == pytest.approx(10.0, abs=1e-6)


def test_currency_inr_usd_routes_through_mom_log_diff():
    # DEXINUS is a raw price level (INR per USD) — needs mom_log_diff, not passthrough.
    levels = pd.Series([83.0, 83.5])
    result = apply_transform("currency_inr_usd", levels)
    assert np.isnan(result.iloc[0])
    assert result.iloc[1] > 0  # rupee weakened -> positive log-diff


def test_unemployment_rate_routes_through_first_difference():
    # World Bank unemployment % is a persistent LEVEL, not a rate of change — first_difference.
    levels = pd.Series([5.2, 5.5])
    result = apply_transform("unemployment_rate", levels)
    assert result.iloc[1] == pytest.approx(0.3, abs=1e-9)


def test_all_current_econiq_indicators_are_registered():
    # Guards against Phase 1 silently dropping an indicator's transform when
    # the real series are wired in.
    for indicator in (
        "cpi_inflation", "gdp_growth", "policy_rate",
        "currency_inr_usd", "unemployment_rate",
        "stock_market_growth", "exports_value",
    ):
        assert indicator in TRANSFORM_REGISTRY, (
            f"'{indicator}' must have a registered transform before it reaches "
            f"the DFM — see TRANSFORM_REGISTRY in transforms.py"
        )


def test_stock_market_growth_routes_through_passthrough():
    # SPASTT01INM657N is already a MoM growth rate, not a raw index — passthrough.
    values = pd.Series([1.2, -0.5, 3.1])
    result = apply_transform("stock_market_growth", values)
    assert result.tolist() == values.tolist()


def test_exports_value_routes_through_yoy_from_index():
    # VALEXPINM052N is a raw not-seasonally-adjusted USD level — needs YoY,
    # not a plain MoM change, so ordinary seasonal export swings don't get
    # mistaken for a regime shift.
    levels = pd.Series([1000.0] * 12 + [1100.0])
    result = apply_transform("exports_value", levels, periods_per_year=12)
    assert result.iloc[-1] == pytest.approx(10.0, abs=1e-6)
