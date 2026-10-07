"""Research-only harness for the four-signal PIT DFM experiment.

This module deliberately has no database client, persistence, or production
pipeline hooks. Callers supply already-loaded macro_timeseries records. The
real historical experiment is opt-in through ``run_experiment_a`` and is not
run by importing this module or by its tests.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
import warnings
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ
from statsmodels.tools.sm_exceptions import ConvergenceWarning

from fit_real_data import (
    FOUR_SIGNAL_EXPERIMENT_INDICATORS,
    _aggregate_fred_daily_fx_to_monthly,
    transform_wide_frame,
)
from point_in_time_dataset import build_point_in_time_dataset, build_point_in_time_panel


SIGNALS = tuple(FOUR_SIGNAL_EXPERIMENT_INDICATORS)
ACTIVITY_SIGNAL = "industrial_production_growth"
EXPERIMENT_A_ID = "experiment_1_realtime_pit_v1"
EXPERIMENT_B_ID = "experiment_1_hindsight_current_asof_v1"
DEFAULT_START_AS_OF = "2018-07-31"
MINIMUM_COMPLETE_MONTHS = 60
ROBUSTNESS_COMPLETE_MONTHS = 84
ACTIVITY_LOADING_ZERO_THRESHOLD = 1e-8
MODEL_MAXITER = 500
MODEL_TOLERANCE = 1e-6


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    mode: str
    signals: tuple[str, ...] = SIGNALS
    factors: int = 1
    factor_orders: int = 1
    idiosyncratic_ar1: bool = True
    diagonal_idiosyncratic_innovations: bool = True
    minimum_complete_months: int = MINIMUM_COMPLETE_MONTHS
    robustness_complete_months: int = ROBUSTNESS_COMPLETE_MONTHS
    start_as_of: str = DEFAULT_START_AS_OF
    snapshot_frequency: str = "month_end"
    as_of_time_convention: str = "23:59:59.999999 UTC on month end"
    normalization_rule: str = "frozen_population_zscore_from_first_60_complete_rows_at_initial_fit"
    factor_extraction: str = "filtered_endpoint"
    orientation_rule: str = "positive_industrial_production_growth_loading"
    activity_loading_zero_threshold: float = ACTIVITY_LOADING_ZERO_THRESHOLD
    model_standardize: bool = False
    init_t0: bool = False
    model_fit_method: str = "em"
    em_initialization: bool = True
    model_maxiter: int = MODEL_MAXITER
    model_tolerance: float = MODEL_TOLERANCE


EXPERIMENT_A = ExperimentConfig(experiment_id=EXPERIMENT_A_ID, mode="REAL_TIME_PIT")
EXPERIMENT_B = ExperimentConfig(
    experiment_id=EXPERIMENT_B_ID,
    mode="HINDSIGHT_CURRENT_AS_OF",
    normalization_rule="separate_current_asof_benchmark_scaling",
    factor_extraction="smoothed_full_sample_benchmark",
)


@dataclass(frozen=True)
class FrozenScaler:
    means: Mapping[str, float]
    stds: Mapping[str, float]
    fitted_as_of: str
    fit_start: str
    fit_end: str
    complete_rows_used: int
    ddof: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "means": dict(self.means),
            "stds": dict(self.stds),
            "fitted_as_of": self.fitted_as_of,
            "fit_start": self.fit_start,
            "fit_end": self.fit_end,
            "complete_rows_used": self.complete_rows_used,
            "ddof": self.ddof,
        }


@dataclass
class Snapshot:
    as_of: str
    raw_panel: pd.DataFrame
    transformed_panel: pd.DataFrame
    diagnostics: dict[str, Any]


def _parse_as_of(value: str | datetime | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp


def month_end_schedule(
    start_as_of: str | datetime | pd.Timestamp,
    end_as_of: str | datetime | pd.Timestamp,
) -> list[pd.Timestamp]:
    """Return month-end timestamps at 23:59:59.999999 UTC, bounded by end."""
    start = _parse_as_of(start_as_of)
    end = _parse_as_of(end_as_of)
    # A date-only end argument is inclusive through that calendar day.
    if isinstance(end_as_of, str) and len(end_as_of) == 10:
        end = end.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    if end < start:
        raise ValueError("end_as_of must not precede start_as_of")
    first = start.tz_localize(None).to_period("M")
    last = end.tz_localize(None).to_period("M")
    result: list[pd.Timestamp] = []
    for period in pd.period_range(first, last, freq="M"):
        day = period.to_timestamp(how="end").date()
        snapshot = pd.Timestamp(datetime(
            day.year, day.month, day.day, 23, 59, 59, 999999,
            tzinfo=timezone.utc,
        ))
        if start <= snapshot <= end:
            result.append(snapshot)
    return result


def _provenance_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_signal: dict[str, int] = {signal: 0 for signal in SIGNALS}
    vintage_dates: list[str] = []
    vintage_ids: set[str] = set()
    qualities: dict[str, int] = {}
    for record in records:
        signal = str(record.get("indicator") or "")
        if signal in by_signal:
            by_signal[signal] += 1
        date = record.get("provider_vintage_date")
        if date:
            vintage_dates.append(str(date)[:10])
        vintage_id = record.get("vintage_id")
        if vintage_id:
            vintage_ids.add(str(vintage_id))
        quality = str(record.get("availability_quality") or "UNKNOWN")
        qualities[quality] = qualities.get(quality, 0) + 1
    unique_dates = sorted(set(vintage_dates))
    return {
        "selected_rows_by_signal": by_signal,
        "selected_record_count": len(records),
        "selected_vintage_id_count": len(vintage_ids),
        "provider_vintage_date_count": len(unique_dates),
        "provider_vintage_date_start": unique_dates[0] if unique_dates else None,
        "provider_vintage_date_end": unique_dates[-1] if unique_dates else None,
        "availability_quality_counts": qualities,
    }


def build_pit_snapshot(
    records: Sequence[dict[str, Any]],
    as_of: str | datetime | pd.Timestamp,
) -> Snapshot:
    """Build one strict PIT panel using EconIQ's shared PIT and transform code."""
    as_of_stamp = _parse_as_of(as_of)
    as_of_text = as_of_stamp.isoformat()
    selected = build_point_in_time_dataset(
        records, as_of=as_of_text, indicators=SIGNALS
    )
    # The PIT selector gates on information availability. Also reject an
    # observation period later than the as-of date, so malformed/future rows
    # cannot leak even if their availability metadata is inconsistent.
    dated_records = [
        record for record in selected["records"]
        if pd.Timestamp(record["period_date"]).date() <= as_of_stamp.date()
    ]
    eligible = _aggregate_fred_daily_fx_to_monthly(dated_records, as_of_text)
    panel = build_point_in_time_panel(
        eligible, as_of=as_of_text, indicators=SIGNALS
    )
    panel = panel.reindex(columns=list(SIGNALS))
    transformed = transform_wide_frame(panel) if not panel.empty else pd.DataFrame(columns=SIGNALS)
    transformed = transformed.reindex(columns=list(SIGNALS))

    if not transformed.empty:
        complete = transformed.notna().all(axis=1)
        if complete.any():
            # Exclude leading single-series history before the common training
            # span; retain every subsequent ragged-edge row for Kalman use.
            transformed = transformed.loc[complete[complete].index[0]:].copy()
            panel = panel.loc[transformed.index].copy()

    complete_rows = transformed.notna().all(axis=1) if not transformed.empty else pd.Series(dtype=bool)
    missing = {
        signal: int(transformed[signal].isna().sum()) if signal in transformed else 0
        for signal in SIGNALS
    }
    available_cells = transformed.notna().any(axis=1) if not transformed.empty else pd.Series(dtype=bool)
    latest = transformed.index[available_cells][-1].date().isoformat() if available_cells.any() else None
    training_start = transformed.index[0].date().isoformat() if not transformed.empty else None
    diagnostics = {
        "as_of": as_of_text,
        "training_start": training_start,
        "latest_observation_available": latest,
        "total_months": int(len(transformed)),
        "complete_rows": int(complete_rows.sum()) if len(complete_rows) else 0,
        "missing_by_signal": missing,
        "robustness_84_month_threshold_met": bool(complete_rows.sum() >= ROBUSTNESS_COMPLETE_MONTHS),
        "pit_manifest": selected["manifest"],
        "provenance_summary": _provenance_summary(eligible),
    }
    return Snapshot(as_of_text, panel, transformed, diagnostics)


def fit_frozen_scaler(
    panel: pd.DataFrame,
    *,
    as_of: str | datetime | pd.Timestamp,
    minimum_complete_rows: int = MINIMUM_COMPLETE_MONTHS,
) -> FrozenScaler:
    """Fit population z-score parameters on the first N complete PIT rows."""
    missing_signals = [name for name in SIGNALS if name not in panel.columns]
    if missing_signals:
        raise ValueError(f"Scaler input is missing required signals: {missing_signals}")
    complete = panel.loc[:, list(SIGNALS)].dropna(how="any")
    if len(complete) < minimum_complete_rows:
        raise ValueError(
            f"Need {minimum_complete_rows} complete rows to initialize scaler; found {len(complete)}."
        )
    sample = complete.iloc[:minimum_complete_rows]
    nonfinite_columns = [
        signal for signal in SIGNALS
        if not np.isfinite(sample[signal].to_numpy(dtype=float)).all()
    ]
    if nonfinite_columns:
        raise ValueError(
            f"Frozen scaler has zero or nonfinite standard deviation / observations: {nonfinite_columns}"
        )
    means_series = sample.mean(axis=0)
    stds_series = sample.std(axis=0, ddof=0)
    invalid = [
        signal for signal in SIGNALS
        if not np.isfinite(means_series[signal])
        or not np.isfinite(stds_series[signal])
        or stds_series[signal] <= 0
    ]
    if invalid:
        raise ValueError(f"Frozen scaler has zero or nonfinite standard deviation: {invalid}")
    return FrozenScaler(
        means={signal: float(means_series[signal]) for signal in SIGNALS},
        stds={signal: float(stds_series[signal]) for signal in SIGNALS},
        fitted_as_of=_parse_as_of(as_of).isoformat(),
        fit_start=sample.index[0].date().isoformat(),
        fit_end=sample.index[-1].date().isoformat(),
        complete_rows_used=minimum_complete_rows,
    )


def apply_frozen_scaler(panel: pd.DataFrame, scaler: FrozenScaler) -> pd.DataFrame:
    """Apply exactly the frozen z-scores while preserving missing cells."""
    missing_signals = [name for name in SIGNALS if name not in panel.columns]
    if missing_signals:
        raise ValueError(f"Scaler input is missing required signals: {missing_signals}")
    out = panel.loc[:, list(SIGNALS)].copy()
    for signal in SIGNALS:
        out[signal] = (out[signal] - scaler.means[signal]) / scaler.stds[signal]
    return out


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _extract_parameters(results: Any) -> tuple[dict[str, float | None], dict[str, float | None]]:
    raw = getattr(results, "params", {})
    names = list(getattr(getattr(results, "model", None), "param_names", []) or [])
    if hasattr(raw, "to_dict"):
        params = raw.to_dict()
    elif isinstance(raw, Mapping):
        params = dict(raw)
    else:
        values = np.asarray(raw).reshape(-1)
        params = {names[i] if i < len(names) else f"parameter_{i}": value for i, value in enumerate(values)}
    parameters = {str(name): _finite_or_none(value) for name, value in params.items()}
    loadings = {
        name: value for name, value in parameters.items()
        if "loading" in name.lower()
    }
    return parameters, loadings


def orient_factor(
    raw_filtered_factor: float,
    raw_loadings: Mapping[str, float | None],
    *,
    threshold: float = ACTIVITY_LOADING_ZERO_THRESHOLD,
) -> dict[str, Any]:
    """Orient common factor by activity loading; do not fall back if unstable."""
    matches = [
        value for name, value in raw_loadings.items()
        if ACTIVITY_SIGNAL in name and "loading" in name.lower()
    ]
    activity_loading = matches[0] if len(matches) == 1 else None
    stable = activity_loading is not None and math.isfinite(activity_loading) and abs(activity_loading) > threshold
    if not stable:
        return {
            "raw_activity_loading": activity_loading,
            "orientation_multiplier": None,
            "oriented_activity_loading": None,
            "raw_filtered_factor": _finite_or_none(raw_filtered_factor),
            "oriented_filtered_factor": None,
            "oriented_loadings": {},
            "orientation_status": "UNSTABLE",
        }
    multiplier = 1 if activity_loading > 0 else -1
    oriented_loadings = {
        name: (value * multiplier if value is not None else None)
        for name, value in raw_loadings.items()
    }
    return {
        "raw_activity_loading": float(activity_loading),
        "orientation_multiplier": multiplier,
        "oriented_activity_loading": float(activity_loading * multiplier),
        "raw_filtered_factor": _finite_or_none(raw_filtered_factor),
        "oriented_filtered_factor": _finite_or_none(raw_filtered_factor * multiplier),
        "oriented_loadings": oriented_loadings,
        "orientation_status": "STABLE",
    }


def _result_scalar(results: Any, name: str) -> float | None:
    try:
        return _finite_or_none(getattr(results, name))
    except Exception:
        return None


def fit_experiment_a_snapshot(
    normalized_panel: pd.DataFrame,
    *,
    as_of: str,
    data_diagnostics: Mapping[str, Any],
    model_class: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit one PIT window and return separate fit and filtered-factor records."""
    attempted = len(normalized_panel.notna().all(axis=1).loc[lambda s: s].index) >= MINIMUM_COMPLETE_MONTHS
    if not attempted:
        return (
            {
                "as_of": as_of,
                "fit_attempted": False,
                "fit_success": False,
                "status": "SKIPPED_INSUFFICIENT_HISTORY",
                "skip_reason": f"fewer than {MINIMUM_COMPLETE_MONTHS} complete transformed months",
                "warnings": [],
                "convergence_status": "NOT_RUN",
            },
            {"as_of": as_of, "factor_observation_date": None, "orientation_status": "NOT_RUN"},
        )

    cls = model_class or DynamicFactorMQ
    caught: list[warnings.WarningMessage] = []
    try:
        # DynamicFactorMQ represents one idiosyncratic AR(1) state/shock per
        # observed series, so idiosyncratic innovations are diagonal by
        # construction. obs_cov_diag is a separate measurement-disturbance
        # stabilization option and is intentionally left at its default.
        model = cls(
            normalized_panel,
            factors=1,
            factor_orders=1,
            idiosyncratic_ar1=True,
            standardize=False,
            init_t0=False,
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            results = model.fit(
                method="em", maxiter=MODEL_MAXITER, tolerance=MODEL_TOLERANCE,
                em_initialization=True, disp=False,
            )
    except Exception as exc:
        return (
            {
                "as_of": as_of,
                "fit_attempted": True,
                "fit_success": False,
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
                "warnings": [str(item.message) for item in caught],
                "convergence_status": "FAILED",
                **dict(data_diagnostics),
            },
            {"as_of": as_of, "factor_observation_date": None, "orientation_status": "NOT_AVAILABLE"},
        )

    warning_records = [
        {"category": item.category.__name__, "message": str(item.message)}
        for item in caught
    ]
    parameters, loadings = _extract_parameters(results)
    retvals = getattr(results, "mle_retvals", {}) or {}
    if not isinstance(retvals, Mapping):
        retvals = {}
    iterations = next(
        (_finite_or_none(retvals.get(key)) for key in ("iter", "iterations", "em_iterations") if retvals.get(key) is not None),
        None,
    )
    reported_converged = retvals.get("converged")
    reported_converged_flag = bool(reported_converged) if reported_converged is not None else None
    convergence_warning = any(issubclass(item.category, ConvergenceWarning) for item in caught)
    exhausted = iterations is not None and iterations >= MODEL_MAXITER
    if convergence_warning:
        convergence_status = "WARNING"
    elif exhausted:
        convergence_status = "ITERATION_LIMIT"
    elif reported_converged_flag is True:
        convergence_status = "CONVERGED"
    elif reported_converged_flag is False:
        convergence_status = "NOT_CONVERGED"
    else:
        convergence_status = "NOT_REPORTED"

    filtered = getattr(getattr(results, "factors", None), "filtered", None)
    factor_date = None
    raw_factor = None
    if filtered is not None and len(filtered) > 0:
        factor_date = pd.Timestamp(filtered.index[-1]).date().isoformat()
        raw_factor = _finite_or_none(filtered.iloc[-1, 0])
    activity_matches = [value for name, value in loadings.items() if ACTIVITY_SIGNAL in name]
    orientation = orient_factor(
        raw_factor if raw_factor is not None else float("nan"), loadings,
        threshold=ACTIVITY_LOADING_ZERO_THRESHOLD,
    )
    critical_finite = raw_factor is not None and all(value is not None for value in parameters.values())
    fit_success = bool(
        critical_finite
        and len(activity_matches) == 1
        and not convergence_warning
        and not exhausted
        and reported_converged_flag is not False
    )
    questionable = bool(
        warning_records
        or exhausted
        or reported_converged_flag is False
        or reported_converged_flag is None
        or orientation["orientation_status"] != "STABLE"
    )
    fit_record = {
        "as_of": as_of,
        "fit_attempted": True,
        "fit_success": fit_success,
        "status": "SUCCESS" if fit_success and not questionable else "QUESTIONABLE" if fit_success else "FAILED",
        "questionable": questionable,
        "warnings": warning_records,
        "convergence_status": convergence_status,
        "iterations": iterations,
        "max_iterations": MODEL_MAXITER,
        "log_likelihood": _result_scalar(results, "llf"),
        "aic": _result_scalar(results, "aic"),
        "bic": _result_scalar(results, "bic"),
        "parameters": parameters,
        "raw_common_factor_loadings": loadings,
        "factor_ar_parameters": {
            k: v for k, v in parameters.items()
            if not k.lower().startswith("loading")
            and (
                ("factor" in k.lower() and "ar" in k.lower())
                or (k.lower().startswith("l") and ".f1." in k.lower())
                or (
                    k.lower().startswith("l")
                    and "->" in k.lower()
                    and "eps" not in k.lower()
                )
            )
        },
        "idiosyncratic_parameters": {
            k: v for k, v in parameters.items()
            if any(token in k.lower() for token in ("idio", "error", "sigma2", "eps_"))
        },
        "optimizer_em_info": {str(k): _finite_or_none(v) if isinstance(v, (int, float, np.number)) else str(v) for k, v in retvals.items()},
        **dict(data_diagnostics),
    }
    factor_record = {
        "as_of": as_of,
        "factor_observation_date": factor_date,
        **orientation,
    }
    return fit_record, factor_record


def experiment_manifest(
    config: ExperimentConfig = EXPERIMENT_A,
    *,
    end_as_of: str | None = None,
    scaler: FrozenScaler | None = None,
    schedule: Sequence[str | datetime | pd.Timestamp] | None = None,
) -> dict[str, Any]:
    """Serializable run manifest; no artifact or database writes are made."""
    if config.mode not in {"REAL_TIME_PIT", "HINDSIGHT_CURRENT_AS_OF"}:
        raise ValueError(f"Unsupported experiment mode: {config.mode}")
    manifest = asdict(config)
    manifest.update({
        "experiment_type": config.mode,
        "information_set": "strict_provider_vintage_PIT" if config.mode == "REAL_TIME_PIT" else "current_as_of_hindsight",
        "end_as_of": end_as_of,
        "as_of_schedule": [
            _parse_as_of(value).isoformat() for value in (schedule or [])
        ],
        "schedule_frequency": config.snapshot_frequency,
        "normalization": scaler.to_dict() if scaler else None,
        "output_namespace": config.experiment_id,
        "production_impact": "none",
    })
    return manifest


def build_hindsight_benchmark_panel(
    records: Sequence[dict[str, Any]],
    *,
    as_of: str | datetime | pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build only the explicitly labeled current-as-of benchmark input panel."""
    snapshot = build_pit_snapshot(records, as_of)
    manifest = experiment_manifest(EXPERIMENT_B, end_as_of=snapshot.as_of)
    manifest["panel_diagnostics"] = snapshot.diagnostics
    return snapshot.transformed_panel, manifest


def run_experiment_a(
    records: Sequence[dict[str, Any]],
    *,
    end_as_of: str | datetime | pd.Timestamp,
    fit_fn: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = fit_experiment_a_snapshot,
) -> dict[str, Any]:
    """Execute an explicitly requested Experiment A in memory only.

    This function is intentionally never called by module import or tests.
    It performs no persistence. The caller must provide an explicit end date.
    """
    schedule = month_end_schedule(EXPERIMENT_A.start_as_of, end_as_of)
    end_limit = _parse_as_of(end_as_of)
    if isinstance(end_as_of, str) and len(end_as_of) == 10:
        end_limit = end_limit.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    end_text = end_limit.isoformat()
    state: dict[str, Any] = {"initial_training_start": None, "scaler": None}
    snapshots: list[dict[str, Any]] = []
    fits: list[dict[str, Any]] = []
    factors: list[dict[str, Any]] = []
    for as_of in schedule:
        snapshot = build_pit_snapshot(records, as_of)
        data_diag = dict(snapshot.diagnostics)
        if state["scaler"] is None and data_diag["complete_rows"] >= MINIMUM_COMPLETE_MONTHS:
            scaler = fit_frozen_scaler(
                snapshot.transformed_panel, as_of=snapshot.as_of,
                minimum_complete_rows=MINIMUM_COMPLETE_MONTHS,
            )
            state["scaler"] = scaler
            state["initial_training_start"] = scaler.fit_start
        scaler = state["scaler"]
        if scaler is None:
            fit_record = {
                "as_of": snapshot.as_of,
                "fit_attempted": False,
                "fit_success": False,
                "status": "SKIPPED_INSUFFICIENT_HISTORY",
                "skip_reason": f"fewer than {MINIMUM_COMPLETE_MONTHS} complete transformed months",
                **data_diag,
            }
            factor_record = {"as_of": snapshot.as_of, "orientation_status": "NOT_RUN"}
        else:
            panel = snapshot.transformed_panel.loc[state["initial_training_start"]:]
            if data_diag["complete_rows"] < MINIMUM_COMPLETE_MONTHS:
                fit_record = {
                    "as_of": snapshot.as_of,
                    "fit_attempted": False,
                    "fit_success": False,
                    "status": "SKIPPED_INSUFFICIENT_HISTORY",
                    "skip_reason": f"fewer than {MINIMUM_COMPLETE_MONTHS} complete transformed months",
                    **data_diag,
                }
                factor_record = {"as_of": snapshot.as_of, "orientation_status": "NOT_RUN"}
            else:
                normalized = apply_frozen_scaler(panel, scaler)
                fit_record, factor_record = fit_fn(
                    normalized, as_of=snapshot.as_of, data_diagnostics=data_diag
                )
        snapshots.append(data_diag)
        fits.append(fit_record)
        factors.append(factor_record)
    previous_by_observation: dict[str, float] = {}
    previous_factor: dict[str, Any] | None = None
    for factor_record in factors:
        value = factor_record.get("oriented_filtered_factor")
        observation_date = factor_record.get("factor_observation_date")
        factor_record["month_over_month_movement"] = None
        factor_record["expanding_window_revision"] = None
        if value is not None and observation_date:
            period = pd.Timestamp(observation_date).to_period("M")
            if previous_factor is not None:
                previous_value = previous_factor.get("oriented_filtered_factor")
                previous_date = previous_factor.get("factor_observation_date")
                if previous_value is not None and previous_date:
                    previous_period = pd.Timestamp(previous_date).to_period("M")
                    if period.ordinal - previous_period.ordinal == 1:
                        factor_record["month_over_month_movement"] = float(value - previous_value)
            if observation_date in previous_by_observation:
                factor_record["expanding_window_revision"] = float(
                    value - previous_by_observation[observation_date]
                )
            previous_by_observation[observation_date] = float(value)
        previous_factor = factor_record
    manifest = experiment_manifest(
        EXPERIMENT_A, end_as_of=end_text, scaler=state["scaler"], schedule=schedule
    )
    manifest["initial_training_start"] = state["initial_training_start"]
    return {"manifest": manifest, "snapshots": snapshots, "fits": fits, "factors": factors}
