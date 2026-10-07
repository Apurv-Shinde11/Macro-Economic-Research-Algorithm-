from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import Mock
import warnings

import numpy as np
import pandas as pd

import fit_real_data
from fit_real_data import CURRENT_DFM_FIT_INDICATORS
from pit_dfm_experiment import (
    ACTIVITY_LOADING_ZERO_THRESHOLD,
    ACTIVITY_SIGNAL,
    EXPERIMENT_A,
    EXPERIMENT_B,
    SIGNALS,
    FrozenScaler,
    apply_frozen_scaler,
    build_hindsight_benchmark_panel,
    build_pit_snapshot,
    experiment_manifest,
    fit_experiment_a_snapshot,
    fit_frozen_scaler,
    month_end_schedule,
    orient_factor,
    run_experiment_a,
)
from point_in_time_dataset import build_point_in_time_dataset
from transforms import TRANSFORM_REGISTRY, already_stationary_passthrough
from statsmodels.tools.sm_exceptions import ConvergenceWarning


def _available_record(indicator, period_date, value, vintage_date="2018-07-17", *, metadata=None, vintage_id=None):
    return {
        "indicator": indicator,
        "period_date": period_date,
        "value": value,
        "source": "FRED",
        "provider": "FRED",
        "provider_series": {
            "cpi_inflation": "INDCPIALLMINMEI",
            "policy_rate": "INDIRLTLT01STM",
            "currency_inr_usd": "DEXINUS",
            "industrial_production_growth": "INDPRMNTO01GYSAM",
        }[indicator],
        "provider_vintage_date": vintage_date,
        "availability_basis": "FRED_VINTAGE_DATE",
        "availability_quality": "ESTIMATED",
        "availability_timestamp": f"{vintage_date}T00:00:00+00:00",
        "published_at": None,
        "ingested_at": "2026-10-01T00:00:00+00:00",
        "vintage_id": vintage_id or f"{indicator}:{period_date}:{vintage_date}",
        "metadata": metadata or {},
    }


def _history_records(months=120, *, revised_activity=False):
    records = []
    dates = pd.date_range("2009-01-01", periods=months, freq="MS")
    for i, date in enumerate(dates):
        month = date.to_period("M")
        end_day = month.to_timestamp(how="end").date().isoformat()
        records.extend([
            _available_record("cpi_inflation", date.date().isoformat(), 90 + i * 0.2 + np.sin(i / 5)),
            _available_record("policy_rate", date.date().isoformat(), 5 + np.sin(i / 9) + i * 0.002),
            _available_record(
                "currency_inr_usd", end_day, 45 + i * 0.08 + np.cos(i / 7),
                metadata={"provider_frequency": "daily"},
            ),
            _available_record(
                "industrial_production_growth", date.date().isoformat(), 3 + np.sin(i / 6) + i * 0.01
            ),
        ])
    if revised_activity:
        records.append(_available_record(
            ACTIVITY_SIGNAL, "2015-01-01", 77.0, "2018-08-15", vintage_id="activity-revision-2018-08"
        ))
    return records


def _complete_test_panel(rows=90):
    index = pd.date_range("2012-01-01", periods=rows, freq="MS")
    x = np.arange(rows, dtype=float)
    return pd.DataFrame({
        "cpi_inflation": np.sin(x / 4) + x * 0.01,
        "policy_rate": np.cos(x / 7) + x * 0.02,
        "currency_inr_usd": np.sin(x / 9) - x * 0.01,
        ACTIVITY_SIGNAL: np.cos(x / 5) + x * 0.015,
    }, index=index).loc[:, list(SIGNALS)]


class TestMonthEndSchedule(unittest.TestCase):
    def test_schedule_is_deterministic_month_end_utc_and_bounded(self):
        expected = [
            pd.Timestamp("2018-07-31T23:59:59.999999Z"),
            pd.Timestamp("2018-08-31T23:59:59.999999Z"),
            pd.Timestamp("2018-09-30T23:59:59.999999Z"),
        ]
        self.assertEqual(month_end_schedule("2018-07-31", "2018-09-30"), expected)
        self.assertEqual(
            month_end_schedule("2018-07-31", "2018-09-15"), expected[:2]
        )
        self.assertEqual(month_end_schedule("2018-08-01", "2018-08-31"), expected[1:2])

    def test_end_before_start_rejected(self):
        with self.assertRaises(ValueError):
            month_end_schedule("2018-08-31", "2018-07-31")


class TestPITPanel(unittest.TestCase):
    def test_future_vintage_is_excluded_and_eligible_revision_is_selected(self):
        records = [
            _available_record(ACTIVITY_SIGNAL, "2020-01-01", 1.0, "2020-02-01", vintage_id="old"),
            _available_record(ACTIVITY_SIGNAL, "2020-01-01", 2.0, "2020-06-01", vintage_id="revision"),
        ]
        early = build_point_in_time_dataset(records, as_of="2020-03-31T23:59:59Z", indicators=[ACTIVITY_SIGNAL])
        late = build_point_in_time_dataset(records, as_of="2020-06-30T23:59:59Z", indicators=[ACTIVITY_SIGNAL])
        self.assertEqual(early["records"][0]["value"], 1.0)
        self.assertEqual(late["records"][0]["value"], 2.0)
        self.assertEqual(early["manifest"]["excluded_future_observations"], 1)

    def test_future_observation_period_is_excluded_even_if_metadata_claims_early_availability(self):
        records = [
            _available_record(ACTIVITY_SIGNAL, "2021-01-01", 9.0, "2020-02-01", vintage_id="impossible-future-period"),
        ]
        snapshot = build_pit_snapshot(records, "2020-12-31T23:59:59.999999Z")
        self.assertTrue(snapshot.transformed_panel.empty)
        self.assertEqual(snapshot.diagnostics["provenance_summary"]["selected_rows_by_signal"][ACTIVITY_SIGNAL], 0)

    def test_historical_snapshot_changes_only_when_revision_becomes_eligible(self):
        records = _history_records(120, revised_activity=True)
        early = build_pit_snapshot(records, "2018-07-31T23:59:59.999999Z")
        late = build_pit_snapshot(records, "2018-08-31T23:59:59.999999Z")
        date = pd.Timestamp("2015-01-01")
        self.assertNotEqual(
            early.transformed_panel.loc[date, ACTIVITY_SIGNAL],
            late.transformed_panel.loc[date, ACTIVITY_SIGNAL],
        )
        self.assertEqual(early.diagnostics["as_of"], "2018-07-31T23:59:59.999999+00:00")

    def test_activity_uses_passthrough_and_fx_aggregation_is_reused(self):
        self.assertIs(TRANSFORM_REGISTRY[ACTIVITY_SIGNAL], already_stationary_passthrough)
        original = fit_real_data._aggregate_fred_daily_fx_to_monthly
        import pit_dfm_experiment
        self.assertIs(pit_dfm_experiment._aggregate_fred_daily_fx_to_monthly, original)
        records = _history_records(48)
        snapshot = build_pit_snapshot(records, "2018-07-31T23:59:59.999999Z")
        self.assertEqual(list(snapshot.transformed_panel.columns), list(SIGNALS))
        # The activity observations pass through unchanged after PIT selection;
        # in particular no additional 12-month percentage change is applied.
        raw_activity = next(r["value"] for r in records if r["indicator"] == ACTIVITY_SIGNAL and r["period_date"] == "2012-01-01")
        self.assertAlmostEqual(snapshot.transformed_panel.loc["2012-01-01", ACTIVITY_SIGNAL], raw_activity)

    def test_missing_cells_are_not_forward_filled(self):
        records = _history_records(120)
        records = [r for r in records if not (r["indicator"] == "policy_rate" and r["period_date"] == "2013-04-01")]
        snapshot = build_pit_snapshot(records, "2018-07-31T23:59:59.999999Z")
        self.assertTrue(pd.isna(snapshot.transformed_panel.loc["2013-04-01", "policy_rate"]))


class TestFrozenNormalization(unittest.TestCase):
    def test_first_60_complete_rows_only_and_population_std(self):
        panel = _complete_test_panel(90)
        scaler = fit_frozen_scaler(panel, as_of="2018-07-31")
        expected = panel.iloc[:60]
        for signal in SIGNALS:
            self.assertAlmostEqual(scaler.means[signal], expected[signal].mean())
            self.assertAlmostEqual(scaler.stds[signal], expected[signal].std(ddof=0))
        changed_future = panel.copy()
        changed_future.iloc[60:] = changed_future.iloc[60:] * 1000
        scaler_after_future_change = fit_frozen_scaler(changed_future, as_of="2018-07-31")
        self.assertEqual(scaler.to_dict(), scaler_after_future_change.to_dict())

    def test_nan_preserved_and_scaler_parameters_reused(self):
        panel = _complete_test_panel(90)
        scaler = fit_frozen_scaler(panel, as_of="2018-07-31")
        ragged = panel.copy()
        ragged.iloc[-1, 0] = np.nan
        normalized = apply_frozen_scaler(ragged, scaler)
        self.assertTrue(pd.isna(normalized.iloc[-1, 0]))
        self.assertEqual(scaler.stds, fit_frozen_scaler(panel, as_of="2018-08-31").stds)

    def test_zero_or_nonfinite_std_rejected(self):
        panel = _complete_test_panel(70)
        panel["policy_rate"] = 1.0
        with self.assertRaisesRegex(ValueError, "zero or nonfinite"):
            fit_frozen_scaler(panel, as_of="2018-07-31")
        panel = _complete_test_panel(70)
        panel.loc[panel.index[0], "policy_rate"] = np.inf
        with self.assertRaisesRegex(ValueError, "zero or nonfinite"):
            fit_frozen_scaler(panel, as_of="2018-07-31")

    def test_revised_values_unavailable_at_initial_asof_do_not_change_frozen_scaler(self):
        records = _history_records(120)
        records.append(_available_record(
            ACTIVITY_SIGNAL, "2012-01-01", 77.0, "2018-08-15", vintage_id="activity-revision-in-scaler-window"
        ))
        first = build_pit_snapshot(records, "2018-07-31T23:59:59.999999Z")
        first_scaler = fit_frozen_scaler(first.transformed_panel, as_of=first.as_of)
        captures = []

        def fake_fit(normalized, *, as_of, data_diagnostics):
            captures.append(normalized.copy())
            return ({"as_of": as_of, "fit_attempted": True, "fit_success": True}, {"as_of": as_of})

        result = run_experiment_a(records, end_as_of="2018-08-31", fit_fn=fake_fit)
        self.assertEqual(result["manifest"]["normalization"]["means"], dict(first_scaler.means))
        self.assertEqual(result["manifest"]["as_of_schedule"], [
            "2018-07-31T23:59:59.999999+00:00",
            "2018-08-31T23:59:59.999999+00:00",
        ])
        self.assertEqual(len(captures), 2)
        self.assertEqual(result["manifest"]["normalization"]["stds"], dict(first_scaler.stds))
        # Later PIT values reflect the revision, while the initial scaler is unchanged.
        early = build_pit_snapshot(records, "2018-07-31T23:59:59.999999Z")
        late = build_pit_snapshot(records, "2018-08-31T23:59:59.999999Z")
        self.assertNotEqual(early.transformed_panel.loc["2012-01-01", ACTIVITY_SIGNAL], late.transformed_panel.loc["2012-01-01", ACTIVITY_SIGNAL])
        self.assertEqual(result["manifest"]["normalization"]["fitted_as_of"], first.as_of)


class _FakeResults:
    def __init__(self, activity_loading=0.5):
        self.params = pd.Series({
            "loading.f1.cpi_inflation": 0.4,
            "loading.f1.policy_rate": -0.2,
            "loading.f1.currency_inr_usd": 0.1,
            f"loading.f1.{ACTIVITY_SIGNAL}": activity_loading,
            "L1.f1.f1": 0.7,
            "sigma2.cpi_inflation": 0.3,
        })
        self.model = Mock(param_names=list(self.params.index))
        self.factors = Mock()
        self.factors.filtered = pd.DataFrame({"factor.1": [10.0, 11.0]}, index=pd.date_range("2018-03-01", periods=2, freq="MS"))
        self.factors.smoothed = pd.DataFrame({"factor.1": [900.0, 999.0]}, index=self.factors.filtered.index)
        self.mle_retvals = {"iter": 12}
        self.llf = -12.5
        self.aic = 42.0
        self.bic = 57.0


class _FakeModel:
    last_kwargs = None
    last_panel = None
    result = _FakeResults()

    def __init__(self, panel, **kwargs):
        type(self).last_panel = panel
        type(self).last_kwargs = kwargs

    def fit(self, **kwargs):
        type(self).fit_kwargs = kwargs
        return type(self).result


class TestFitExtractionOrientation(unittest.TestCase):
    def test_model_uses_required_spec_and_filtered_endpoint_not_smoothed(self):
        _FakeModel.result = _FakeResults()
        panel = _complete_test_panel(90)
        fit, factor = fit_experiment_a_snapshot(
            panel, as_of="2018-07-31T23:59:59.999999+00:00",
            data_diagnostics={"complete_rows": 90}, model_class=_FakeModel,
        )
        self.assertEqual(_FakeModel.last_kwargs, {
            "factors": 1, "factor_orders": 1,
            "idiosyncratic_ar1": True, "standardize": False, "init_t0": False,
        })
        self.assertEqual(_FakeModel.fit_kwargs["method"], "em")
        self.assertTrue(_FakeModel.fit_kwargs["em_initialization"])
        self.assertEqual(factor["raw_filtered_factor"], 11.0)
        self.assertNotEqual(factor["raw_filtered_factor"], 999.0)
        self.assertEqual(factor["factor_observation_date"], "2018-04-01")
        self.assertEqual(fit["convergence_status"], "NOT_REPORTED")
        self.assertTrue(fit["questionable"])
        self.assertEqual(fit["status"], "QUESTIONABLE")
        self.assertIn("log_likelihood", fit)
        self.assertIn("parameters", fit)
        self.assertIn("factor_ar_parameters", fit)
        self.assertIn("idiosyncratic_parameters", fit)
        self.assertEqual(list(fit["factor_ar_parameters"]), ["L1.f1.f1"])
        self.assertTrue(fit["idiosyncratic_parameters"])

    def test_convergence_warning_and_iteration_limit_are_questionable_and_unsuccessful(self):
        class WarningModel(_FakeModel):
            def fit(self, **kwargs):
                warnings.warn("EM did not converge", ConvergenceWarning)
                result = _FakeResults()
                result.mle_retvals = {"iter": 500}
                return result

        fit, _ = fit_experiment_a_snapshot(
            _complete_test_panel(90), as_of="2018-07-31",
            data_diagnostics={}, model_class=WarningModel,
        )
        self.assertFalse(fit["fit_success"])
        self.assertTrue(fit["questionable"])
        self.assertEqual(fit["convergence_status"], "WARNING")
        self.assertEqual(fit["iterations"], 500)
        self.assertTrue(fit["warnings"])

    def test_insufficient_history_skips_without_model_call(self):
        panel = _complete_test_panel(59)
        fit, factor = fit_experiment_a_snapshot(
            panel, as_of="2018-07-31", data_diagnostics={}, model_class=Mock()
        )
        self.assertFalse(fit["fit_attempted"])
        self.assertEqual(fit["status"], "SKIPPED_INSUFFICIENT_HISTORY")
        self.assertEqual(factor["orientation_status"], "NOT_RUN")

    def test_60_eligible_84_threshold_flag(self):
        snapshot = build_pit_snapshot(_history_records(120), "2018-07-31T23:59:59.999999Z")
        self.assertGreaterEqual(snapshot.diagnostics["complete_rows"], 60)
        short_snapshot = build_pit_snapshot(_history_records(80), "2018-07-31T23:59:59.999999Z")
        self.assertGreaterEqual(short_snapshot.diagnostics["complete_rows"], 60)
        self.assertFalse(short_snapshot.diagnostics["robustness_84_month_threshold_met"])
        panel = _complete_test_panel(84)
        self.assertGreaterEqual(panel.notna().all(axis=1).sum(), 84)
        self.assertTrue(snapshot.diagnostics["robustness_84_month_threshold_met"])

    def test_orientation_positive_negative_zero_and_nonfinite(self):
        key = f"loading.f1.{ACTIVITY_SIGNAL}"
        positive = orient_factor(2.0, {key: 0.4, "loading.f1.x": -1.0})
        self.assertEqual(positive["orientation_multiplier"], 1)
        self.assertEqual(positive["oriented_filtered_factor"], 2.0)
        self.assertEqual(positive["oriented_activity_loading"], 0.4)

        negative = orient_factor(2.0, {key: -0.4, "loading.f1.x": 1.0})
        self.assertEqual(negative["orientation_multiplier"], -1)
        self.assertEqual(negative["oriented_filtered_factor"], -2.0)
        self.assertEqual(negative["oriented_loadings"]["loading.f1.x"], -1.0)

        zero = orient_factor(2.0, {key: ACTIVITY_LOADING_ZERO_THRESHOLD / 2})
        self.assertEqual(zero["orientation_status"], "UNSTABLE")
        self.assertIsNone(zero["oriented_filtered_factor"])

        nonfinite = orient_factor(2.0, {key: float("nan")})
        self.assertEqual(nonfinite["orientation_status"], "UNSTABLE")


class TestManifestsAndRegression(unittest.TestCase):
    def test_pit_and_hindsight_modes_are_separate_and_explicit(self):
        pit = experiment_manifest(EXPERIMENT_A, end_as_of="2020-01-31")
        hindsight = experiment_manifest(EXPERIMENT_B, end_as_of="2020-01-31")
        self.assertEqual(pit["information_set"], "strict_provider_vintage_PIT")
        self.assertEqual(hindsight["information_set"], "current_as_of_hindsight")
        self.assertNotEqual(pit["output_namespace"], hindsight["output_namespace"])
        self.assertEqual(pit["factor_extraction"], "filtered_endpoint")
        self.assertEqual(pit["normalization_rule"], EXPERIMENT_A.normalization_rule)
        self.assertEqual(pit["minimum_complete_months"], 60)
        self.assertEqual(pit["robustness_complete_months"], 84)

    def test_hindsight_panel_is_labeled_without_running_model(self):
        panel, manifest = build_hindsight_benchmark_panel(
            _history_records(120), as_of="2018-07-31T23:59:59.999999Z"
        )
        self.assertEqual(list(panel.columns), list(SIGNALS))
        self.assertEqual(manifest["experiment_type"], "HINDSIGHT_CURRENT_AS_OF")
        self.assertTrue(manifest["output_namespace"].startswith("experiment_1_hindsight"))

    def test_six_signal_production_selection_is_unchanged(self):
        self.assertEqual(len(CURRENT_DFM_FIT_INDICATORS), 6)
        self.assertNotIn(ACTIVITY_SIGNAL, CURRENT_DFM_FIT_INDICATORS)
        self.assertEqual(tuple(fit_real_data.CURRENT_DFM_FIT_INDICATORS), CURRENT_DFM_FIT_INDICATORS)


if __name__ == "__main__":
    unittest.main()
