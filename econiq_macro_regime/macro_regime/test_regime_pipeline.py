"""
Unit tests for regime_pipeline.py's upsert logic and its refit/nowcast
modes. Mocks the Supabase client throughout -- never touches a real
database, same discipline as the rest of this project's test suite.
"""
from unittest.mock import MagicMock

import pandas as pd

import regime_pipeline as rp


def _make_fake_supabase():
    """A MagicMock shaped like the supabase-py client, just enough to
    record what regime_pipeline.py sends to .table('regime_cache').upsert(...)."""
    fake = MagicMock()
    fake.table.return_value.upsert.return_value.execute.return_value = None
    return fake


# --- _upsert_rows ------------------------------------------------------

def test_upsert_rows_sends_correct_on_conflict_key():
    fake = _make_fake_supabase()
    rows = [{
        "economy": "IN", "period_date": "2020-01-01", "factor_score": 0.5,
        "model_version": "dfm_v1_6indicator", "computed_at": "2026-09-29T00:00:00+00:00",
    }]

    rp._upsert_rows(fake, rows)

    fake.table.assert_called_with("regime_cache")
    fake.table.return_value.upsert.assert_called_once_with(
        rows, on_conflict="economy,period_date,model_version"
    )
    fake.table.return_value.upsert.return_value.execute.assert_called_once()


def test_upsert_rows_chunks_at_500():
    fake = _make_fake_supabase()
    rows = [
        {
            "economy": "IN", "period_date": f"2020-01-{i:02d}" if i < 28 else "2020-01-01",
            "factor_score": float(i), "model_version": "dfm_v1_6indicator",
            "computed_at": "2026-09-29T00:00:00+00:00",
        }
        for i in range(1200)
    ]

    rp._upsert_rows(fake, rows)

    assert fake.table.return_value.upsert.call_count == 3
    chunk_sizes = [len(call.args[0]) for call in fake.table.return_value.upsert.call_args_list]
    assert chunk_sizes == [500, 500, 200]
    for call in fake.table.return_value.upsert.call_args_list:
        assert call.kwargs["on_conflict"] == "economy,period_date,model_version"


def test_upsert_rows_no_op_on_empty_list():
    fake = _make_fake_supabase()
    rp._upsert_rows(fake, [])
    fake.table.assert_not_called()


def test_prepare_transformed_frame_uses_pit_selected_observations(monkeypatch):
    import fit_real_data

    wide = pd.DataFrame(
        {"cpi_inflation": [100.0]},
        index=pd.to_datetime(["2024-01-01"]),
    )
    wide.attrs["raw_records"] = [
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 100.0,
         "source": "FRED", "published_at": "2024-02-15T00:00:00Z"},
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 110.0,
         "source": "FRED", "published_at": "2024-08-30T00:00:00Z"},
    ]
    monkeypatch.setattr(rp, "SINCE", "2024-01-01")
    monkeypatch.setattr(rp, "load_wide_frame", lambda economy: wide)
    monkeypatch.setattr(
        rp,
        "prepare_real_dfm_data",
        lambda data: fit_real_data.prepare_real_dfm_data(data, as_of="2024-06-30T00:00:00Z"),
    )
    transformed_inputs = []

    def capture_transform(panel):
        transformed_inputs.append(panel.copy())
        return panel.copy()

    monkeypatch.setattr(fit_real_data, "transform_wide_frame", capture_transform)
    transformed = rp._prepare_transformed_frame()

    assert transformed_inputs[0].loc["2024-01-01", "cpi_inflation"] == 100.0
    assert transformed.loc["2024-01-01", "cpi_inflation"] == 100.0


# --- run_refit -----------------------------------------------------------

def test_run_refit_writes_full_factor_series(monkeypatch):
    idx = pd.date_range("2020-01-01", periods=3, freq="MS")
    transformed = pd.DataFrame({"cpi_inflation": [1.0, 2.0, 3.0]}, index=idx)
    monkeypatch.setattr(rp, "_prepare_transformed_frame", lambda: transformed)

    fake_results = type("FakeResults", (), {})()
    fake_results.factors = type("FakeFactors", (), {})()
    fake_results.factors.smoothed = pd.DataFrame({0: [0.1, 0.2, 0.3]})
    monkeypatch.setattr(rp, "fit_dfm", lambda df, k_factors: fake_results)

    fake_supabase = _make_fake_supabase()
    monkeypatch.setattr(rp, "_get_supabase", lambda: fake_supabase)

    n_written = rp.run_refit()

    assert n_written == 3
    sent_rows = fake_supabase.table.return_value.upsert.call_args.args[0]
    assert len(sent_rows) == 3
    assert [r["period_date"] for r in sent_rows] == ["2020-01-01", "2020-02-01", "2020-03-01"]
    assert [r["factor_score"] for r in sent_rows] == [0.1, 0.2, 0.3]
    assert all(r["economy"] == "IN" for r in sent_rows)
    assert all(r["model_version"] == "dfm_v1_6indicator" for r in sent_rows)
    assert all("computed_at" in r for r in sent_rows)


# --- run_nowcast ---------------------------------------------------------

def test_run_nowcast_fits_on_history_minus_latest_and_writes_one_row(monkeypatch):
    idx = pd.date_range("2020-01-01", periods=4, freq="MS")
    transformed = pd.DataFrame({"cpi_inflation": [1.0, 2.0, 3.0, 4.0]}, index=idx)
    monkeypatch.setattr(rp, "_prepare_transformed_frame", lambda: transformed)

    captured_fit_arg = {}
    fake_results = object()

    def fake_fit_dfm(df, k_factors):
        captured_fit_arg["history"] = df
        return fake_results

    captured_nowcast_args = {}

    def fake_nowcast(results, latest_row):
        captured_nowcast_args["results"] = results
        captured_nowcast_args["latest_row"] = latest_row
        return 0.42

    monkeypatch.setattr(rp, "fit_dfm", fake_fit_dfm)
    monkeypatch.setattr(rp, "nowcast", fake_nowcast)

    fake_supabase = _make_fake_supabase()
    monkeypatch.setattr(rp, "_get_supabase", lambda: fake_supabase)

    n_written = rp.run_nowcast()

    assert n_written == 1
    # history passed to fit_dfm must exclude the latest (4th) row
    assert len(captured_fit_arg["history"]) == 3
    assert captured_fit_arg["history"].index[-1] == idx[2]
    # nowcast() must be called with the fitted results and the latest row
    assert captured_nowcast_args["results"] is fake_results
    assert captured_nowcast_args["latest_row"]["cpi_inflation"] == 4.0

    sent_rows = fake_supabase.table.return_value.upsert.call_args.args[0]
    assert len(sent_rows) == 1
    assert sent_rows[0]["period_date"] == "2020-04-01"
    assert sent_rows[0]["factor_score"] == 0.42
    assert sent_rows[0]["model_version"] == "dfm_v1_6indicator"


def test_run_nowcast_aborts_with_fewer_than_two_months(monkeypatch):
    idx = pd.date_range("2020-01-01", periods=1, freq="MS")
    transformed = pd.DataFrame({"cpi_inflation": [1.0]}, index=idx)
    monkeypatch.setattr(rp, "_prepare_transformed_frame", lambda: transformed)

    fake_supabase = _make_fake_supabase()
    monkeypatch.setattr(rp, "_get_supabase", lambda: fake_supabase)

    n_written = rp.run_nowcast()

    assert n_written == 0
    fake_supabase.table.assert_not_called()
