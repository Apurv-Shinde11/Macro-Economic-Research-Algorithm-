from collections import Counter
from datetime import datetime, timezone

import pandas as pd
import pytest

from econiq_macro_regime.macro_regime.fit_real_data import (
    FOUR_SIGNAL_EXPERIMENT_INDICATORS,
    REAL_DFM_INDICATOR_METADATA,
    prepare_real_dfm_data,
)
from econiq_macro_regime.macro_regime.fred_vintages import (
    FredVintageObservation,
    build_vintage_rows,
)
from econiq_macro_regime.macro_regime.point_in_time_dataset import (
    build_point_in_time_dataset,
    build_point_in_time_panel,
)


@pytest.fixture
def sample_observations():
    return [
        {
            "indicator": "cpi_inflation",
            "period_date": "2024-01-01",
            "value": 2.0,
            "source": "FRED",
            "ingested_at": "2024-02-01T00:00:00Z",
        },
        {
            "indicator": "cpi_inflation",
            "period_date": "2024-01-01",
            "value": 2.3,
            "source": "FRED",
            "ingested_at": "2024-03-01T00:00:00Z",
        },
        {
            "indicator": "gdp_growth",
            "period_date": "2024-01-01",
            "value": 6.1,
            "source": "WORLD_BANK",
            "ingested_at": "2024-04-01T00:00:00Z",
        },
        {
            "indicator": "gdp_growth",
            "period_date": "2024-01-01",
            "value": 6.4,
            "source": "WORLD_BANK",
            "ingested_at": "2024-07-01T00:00:00Z",
        },
        {
            "indicator": "currency_inr_usd",
            "period_date": "2024-02-01",
            "value": 83.5,
            "source": "FRED",
            "ingested_at": "2024-02-29T00:00:00Z",
        },
    ]


def test_future_revision_is_excluded_before_as_of(sample_observations):
    dataset = build_point_in_time_dataset(sample_observations, as_of="2024-02-15T12:00:00Z")
    records = dataset["records"]

    cpi = [r for r in records if r["indicator"] == "cpi_inflation"]
    assert len(cpi) == 1
    assert cpi[0]["value"] == 2.0
    assert cpi[0]["availability_quality"] == "INGESTION_PROXY"


def test_revision_becomes_available_after_publication(sample_observations):
    dataset = build_point_in_time_dataset(sample_observations, as_of="2024-03-15T12:00:00Z")
    cpi = [r for r in dataset["records"] if r["indicator"] == "cpi_inflation"]
    assert len(cpi) == 1
    assert cpi[0]["value"] == 2.3


def test_panel_preserves_ragged_edges_and_missing_values(sample_observations):
    panel = build_point_in_time_panel(
        sample_observations,
        as_of="2024-07-15T12:00:00Z",
        indicators=["cpi_inflation", "gdp_growth", "currency_inr_usd"],
    )

    idx = pd.to_datetime(panel.index)
    assert panel.loc["2024-01-01", "cpi_inflation"] == 2.3
    assert panel.loc["2024-02-01", "currency_inr_usd"] == 83.5
    assert pd.isna(panel.loc["2024-03-01", "gdp_growth"])


def test_dataset_manifest_is_reproducible_and_clear(sample_observations):
    dataset = build_point_in_time_dataset(sample_observations, as_of="2024-07-15T12:00:00Z")
    manifest = dataset["manifest"]

    assert manifest["as_of"] == "2024-07-15T12:00:00Z"
    assert manifest["indicator_count"] == 3
    assert manifest["observation_count"] == 3
    assert manifest["availability_quality"]["cpi_inflation"] == "INGESTION_PROXY"


def test_future_ingestion_does_not_create_historical_availability():
    records = [
        {
            "indicator": "cpi_inflation",
            "period_date": "2023-01-31",
            "value": 2.5,
            "source": "FRED",
            "ingested_at": "2024-01-15T00:00:00Z",
        }
    ]

    dataset = build_point_in_time_dataset(records, as_of="2023-06-30T00:00:00Z")
    assert dataset["records"] == []


def test_quality_semantics_distinguish_exact_and_proxy():
    exact = build_point_in_time_dataset(
        [{"indicator": "gdp_growth", "period_date": "2024-01-01", "value": 6.1, "source": "WB", "published_at": "2024-04-15T00:00:00Z"}],
        as_of="2024-04-20T00:00:00Z",
    )
    proxy = build_point_in_time_dataset(
        [{"indicator": "gdp_growth", "period_date": "2024-01-01", "value": 6.1, "source": "WB", "ingested_at": "2024-04-15T00:00:00Z"}],
        as_of="2024-04-20T00:00:00Z",
    )
    canonical_proxy = build_point_in_time_dataset(
        [{"indicator": "gdp_growth", "period_date": "2024-01-01", "value": 6.1, "source": "WB",
          "availability_timestamp": "2024-04-15T00:00:00Z", "availability_quality": "INGESTION_PROXY"}],
        as_of="2024-04-20T00:00:00Z",
    )

    assert exact["records"][0]["availability_quality"] == "EXACT"
    assert proxy["records"][0]["availability_quality"] == "INGESTION_PROXY"
    assert canonical_proxy["records"][0]["availability_quality"] == "INGESTION_PROXY"


def test_retrieval_or_period_date_is_never_treated_as_publication_time():
    records = [
        {"indicator": "x", "period_date": "2024-01-01", "value": 1,
         "source": "FRED", "retrieved_at": "2024-01-02T00:00:00Z"},
        {"indicator": "y", "period_date": "2024-01-01", "value": 2,
         "source": "FRED"},
    ]
    dataset = build_point_in_time_dataset(records, as_of="2024-02-01T00:00:00Z")
    assert dataset["records"] == []
    assert dataset["manifest"]["excluded_unknown_availability"] == 2


def test_explicit_published_at_is_canonical_and_manifest_counts_revisions():
    records = [
        {"indicator": "x", "period_date": "2024-01-01", "value": 1,
         "source": "FRED", "published_at": "2024-02-01T00:00:00Z",
         "ingested_at": "2024-02-03T00:00:00Z"},
        {"indicator": "x", "period_date": "2024-01-01", "value": 2,
         "source": "FRED", "published_at": "2024-03-01T00:00:00Z",
         "ingested_at": "2024-03-02T00:00:00Z"},
        {"indicator": "x", "period_date": "2024-02-01", "value": 3,
         "source": "FRED", "ingested_at": "2024-05-01T00:00:00Z"},
        {"indicator": "x", "period_date": "2024-03-01", "value": 4,
         "source": "FRED"},
    ]
    dataset = build_point_in_time_dataset(records, as_of="2024-02-15T00:00:00Z")
    assert [(row["value"], row["availability_timestamp"], row["availability_quality"])
            for row in dataset["records"]] == [(1, "2024-02-01T00:00:00Z", "EXACT")]
    manifest = dataset["manifest"]
    assert manifest["availability_quality_counts"] == {
        "EXACT": 1, "INGESTION_PROXY": 0, "ESTIMATED": 0, "UNKNOWN": 0,
    }
    assert manifest["input_availability_quality_counts"] == {
        "EXACT": 2, "INGESTION_PROXY": 1, "ESTIMATED": 0, "UNKNOWN": 1,
    }
    assert manifest["excluded_unknown_availability"] == 1
    assert manifest["excluded_future_observations"] == 2
    assert manifest["revision_rows_considered"] == 1
    assert manifest["revisions_selected"] == 0


def test_deterministic_result_is_stable_for_same_input():
    records = [
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 2.0, "source": "FRED", "ingested_at": "2024-02-01T00:00:00Z"},
        {"indicator": "currency_inr_usd", "period_date": "2024-02-01", "value": 83.5, "source": "FRED", "ingested_at": "2024-02-29T00:00:00Z"},
        {"indicator": "gdp_growth", "period_date": "2024-01-01", "value": 6.1, "source": "WB", "published_at": "2024-04-01T00:00:00Z"},
    ]

    first = build_point_in_time_dataset(records, as_of="2024-04-15T00:00:00Z")
    second = build_point_in_time_dataset(records, as_of="2024-04-15T00:00:00Z")
    assert [r["indicator"] for r in first["records"]] == [r["indicator"] for r in second["records"]]
    assert [r["value"] for r in first["records"]] == [r["value"] for r in second["records"]]
    assert first["manifest"]["indicator_count"] == second["manifest"]["indicator_count"]


def test_mixed_frequency_panel_keeps_gap_without_forward_fill():
    records = [
        {"indicator": "monthly_data", "period_date": "2024-01-01", "value": 1.0, "source": "FRED", "published_at": "2024-01-10T00:00:00Z"},
        {"indicator": "monthly_data", "period_date": "2024-02-01", "value": 1.2, "source": "FRED", "published_at": "2024-02-10T00:00:00Z"},
        {"indicator": "quarterly_data", "period_date": "2024-01-01", "value": 5.0, "source": "WB", "published_at": "2024-02-15T00:00:00Z"},
        {"indicator": "quarterly_data", "period_date": "2024-04-01", "value": 5.8, "source": "WB", "published_at": "2024-05-15T00:00:00Z"},
    ]

    panel = build_point_in_time_panel(records, as_of="2024-05-31T00:00:00Z", indicators=["monthly_data", "quarterly_data"])
    assert panel.loc["2024-01-01", "monthly_data"] == 1.0
    assert panel.loc["2024-03-01", "monthly_data"] is pd.NA or pd.isna(panel.loc["2024-03-01", "monthly_data"])
    assert panel.loc["2024-03-01", "quarterly_data"] is pd.NA or pd.isna(panel.loc["2024-03-01", "quarterly_data"])


def test_real_dfm_pit_gate_runs_before_transform():
    records = [
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 100.0, "source": "FRED", "published_at": "2024-02-15T00:00:00Z"},
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 110.0, "source": "FRED", "published_at": "2024-08-30T00:00:00Z"},
    ]

    prepared = prepare_real_dfm_data(records, as_of="2024-06-30T00:00:00Z")
    assert prepared["panel"].shape[1] == 1
    assert prepared["panel"].iloc[0, 0] == 100.0


def test_real_dfm_default_as_of_uses_same_pit_selector(sample_observations):
    from econiq_macro_regime.macro_regime.fit_real_data import prepare_real_dfm_data

    prepared = prepare_real_dfm_data(sample_observations)
    assert prepared["strict_point_in_time"] is True
    assert prepared["manifest"]["as_of"]
    assert prepared["manifest"]["availability_quality"]["cpi_inflation"] == "INGESTION_PROXY"
    assert prepared["panel"].loc["2024-01-01", "cpi_inflation"] == 2.3


def test_activity_vintage_revision_is_pit_selected_before_identity_transform():
    indicator = "industrial_production_growth"
    vintages = build_vintage_rows(
        indicator,
        "INDPRMNTO01GYSAM",
        [
            FredVintageObservation(
                pd.Timestamp("2016-01-01").date(), 4.58331953637605,
                pd.Timestamp("2018-07-17").date(), pd.Timestamp("2018-07-17").date(), None,
            ),
            FredVintageObservation(
                pd.Timestamp("2016-01-01").date(), 4.39379748743394,
                pd.Timestamp("2023-11-10").date(), pd.Timestamp("2023-11-10").date(), None,
            ),
        ],
    )

    before = prepare_real_dfm_data(
        vintages, as_of="2023-11-10T23:59:59+00:00", indicators=[indicator]
    )
    after = prepare_real_dfm_data(
        vintages, as_of="2023-11-11T00:00:00+00:00", indicators=[indicator]
    )

    assert before["manifest"]["observation_count"] == 1
    assert before["panel"][indicator].notna().sum() == 1
    assert before["panel"].loc["2016-01-01", indicator] == 4.58331953637605
    assert before["transformed_panel"].loc["2016-01-01", indicator] == 4.58331953637605
    assert before["manifest"]["excluded_future_observations"] == 1
    assert after["manifest"]["observation_count"] == 1
    assert after["panel"][indicator].notna().sum() == 1
    assert after["panel"].loc["2016-01-01", indicator] == 4.39379748743394
    assert after["transformed_panel"].loc["2016-01-01", indicator] == 4.39379748743394


def test_four_signal_experimental_panel_can_be_selected_without_replacing_gdp():
    period = "2024-01-01"
    records = [
        {"indicator": "cpi_inflation", "period_date": period, "value": 100.0,
         "source": "FRED", "published_at": "2024-02-01T00:00:00Z"},
        {"indicator": "policy_rate", "period_date": period, "value": 7.0,
         "source": "FRED", "published_at": "2024-02-01T00:00:00Z"},
        {"indicator": "currency_inr_usd", "period_date": period, "value": 83.0,
         "source": "FRED", "published_at": "2024-02-01T00:00:00Z"},
        {"indicator": "industrial_production_growth", "period_date": period, "value": 4.39379748743394,
         "source": "FRED", "provider_series": "INDPRMNTO01GYSAM",
         "published_at": "2024-02-01T00:00:00Z"},
        {"indicator": "gdp_growth", "period_date": period, "value": 6.1,
         "source": "WORLD_BANK", "published_at": "2024-02-01T00:00:00Z"},
    ]

    prepared = prepare_real_dfm_data(
        records,
        as_of="2024-03-01T00:00:00Z",
        indicators=FOUR_SIGNAL_EXPERIMENT_INDICATORS,
    )

    assert set(prepared["panel"].columns) == set(FOUR_SIGNAL_EXPERIMENT_INDICATORS)
    assert "gdp_growth" not in prepared["panel"].columns
    assert prepared["panel"].loc[period, "industrial_production_growth"] == 4.39379748743394


def test_database_loader_preserves_ingestion_timestamps_and_revisions(monkeypatch):
    import econiq_macro_regime.macro_regime.fit_real_data as fit_real_data

    rows = [
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 100.0,
         "source": "FRED", "ingested_at": "2024-02-01T00:00:00Z"},
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 110.0,
         "source": "FRED", "ingested_at": "2024-08-01T00:00:00Z"},
    ]

    class Response:
        data = rows

    class Query:
        def select(self, columns):
            assert "ingested_at" in columns
            assert "provider_vintage_date" in columns
            assert "availability_basis" in columns
            return self

        def eq(self, *_args):
            return self

        def order(self, column):
            assert column == "id"
            return self

        def range(self, *_args):
            return self

        def execute(self):
            return Response()

    class Client:
        def table(self, name):
            assert name == "macro_timeseries"
            return Query()

    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-only")
    monkeypatch.setattr(fit_real_data, "create_client", lambda *_args: Client())

    wide = fit_real_data.load_wide_frame()
    raw = wide.attrs["raw_records"]
    assert [record["value"] for record in raw] == [100.0, 110.0]
    assert [record["ingested_at"] for record in raw] == [
        "2024-02-01T00:00:00Z", "2024-08-01T00:00:00Z",
    ]


def test_database_loader_uses_ordered_complete_multi_page_reads(monkeypatch):
    import econiq_macro_regime.macro_regime.fit_real_data as fit_real_data

    indicators = {
        "cpi_inflation": "INDCPIALLMINMEI",
        "policy_rate": "INDIRLTLT01STM",
        "industrial_production_growth": "INDPRMNTO01GYSAM",
    }
    rows = []
    for row_id in range(1, 2502):
        indicator = tuple(indicators)[(row_id - 1) % len(indicators)]
        is_provider_vintage = row_id % 3 != 0
        rows.append({
            "id": row_id,
            "indicator": indicator,
            "period_date": pd.Timestamp("2000-01-01") + pd.DateOffset(months=row_id - 1),
            "value": float(row_id),
            "source": "FRED",
            "ingested_at": f"2026-01-{(row_id % 28) + 1:02d}T00:00:00Z",
            "provider_series": indicators[indicator] if is_provider_vintage else None,
            "provider_vintage_date": "2026-01-01" if is_provider_vintage else None,
            "published_at": None,
            "availability_timestamp": "2026-01-01T23:59:59Z" if is_provider_vintage else None,
            "availability_basis": "FRED_VINTAGE_DATE" if is_provider_vintage else None,
            "availability_quality": "ESTIMATED" if is_provider_vintage else "UNKNOWN",
            "vintage_id": f"vintage-{row_id}" if is_provider_vintage else None,
            "revision_number": 0 if is_provider_vintage else None,
            "metadata": {},
        })

    class Response:
        def __init__(self, data):
            self.data = data

    class Query:
        def __init__(self):
            self.ordering = None
            self.offset = 0
            self.end = 0
            self.ranges = []
            self.events = []
            self.page_number = 0

        def select(self, columns):
            assert "id," in columns
            return self

        def eq(self, *_args):
            return self

        def order(self, column):
            self.events.append(("order", column))
            self.ordering = column
            return self

        def range(self, start, end):
            self.events.append(("range", start, end))
            self.offset, self.end = start, end
            self.ranges.append((start, end))
            return self

        def execute(self):
            self.page_number += 1
            if self.ordering == "id":
                source = sorted(rows, key=lambda record: record["id"])
            else:
                # Model an unordered backend whose row order changes between
                # requests, reproducing offset-page overlap and omission.
                source = rows if self.page_number % 2 else list(reversed(rows))
            return Response(source[self.offset : self.end + 1])

    query = Query()

    class Client:
        def table(self, name):
            assert name == "macro_timeseries"
            return query

    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-only")
    monkeypatch.setattr(fit_real_data, "create_client", lambda *_args: Client())

    wide = fit_real_data.load_wide_frame()
    loaded = wide.attrs["raw_records"]

    assert query.ordering == "id"
    assert query.ranges == [(0, 999), (1000, 1999), (2000, 2999)]
    assert query.events == [
        ("order", "id"), ("range", 0, 999),
        ("order", "id"), ("range", 1000, 1999),
        ("order", "id"), ("range", 2000, 2999),
    ]
    assert [record["id"] for record in loaded] == list(range(1, 2502))
    assert len({record["id"] for record in loaded}) == 2501
    assert Counter(record["indicator"] for record in loaded) == Counter(
        {"cpi_inflation": 834, "policy_rate": 834, "industrial_production_growth": 833}
    )
    assert sum(record["vintage_id"] is not None for record in loaded) == 1668
    assert sum(record["vintage_id"] is None for record in loaded) == 833


def test_fred_daily_fx_vintages_are_aggregated_only_after_pit_to_monthly():
    from econiq_macro_regime.macro_regime.fit_real_data import _aggregate_fred_daily_fx_to_monthly

    records = [
        {"indicator": "currency_inr_usd", "period_date": "2024-02-28", "value": 82.0,
         "provider_series": "DEXINUS", "vintage_id": "feb", "availability_quality": "ESTIMATED",
         "availability_basis": "FRED_VINTAGE_DATE", "availability_timestamp": "2024-03-01T23:59:59Z",
         "_available_dt": datetime.fromisoformat("2024-03-01T23:59:59+00:00"),
         "metadata": {"provider_frequency": "daily"}},
        {"indicator": "currency_inr_usd", "period_date": "2024-03-28", "value": 83.0,
         "provider_series": "DEXINUS", "vintage_id": "mar", "availability_quality": "ESTIMATED",
         "availability_basis": "FRED_VINTAGE_DATE", "availability_timestamp": "2024-03-29T23:59:59Z",
         "_available_dt": datetime.fromisoformat("2024-03-29T23:59:59+00:00"),
         "metadata": {"provider_frequency": "daily"}},
        {"indicator": "currency_inr_usd", "period_date": "2024-04-03", "value": 84.0,
         "provider_series": "DEXINUS", "vintage_id": "apr", "availability_quality": "ESTIMATED",
         "availability_basis": "FRED_VINTAGE_DATE", "availability_timestamp": "2024-04-04T23:59:59Z",
         "_available_dt": datetime.fromisoformat("2024-04-04T23:59:59+00:00"),
         "metadata": {"provider_frequency": "daily"}},
    ]

    monthly = _aggregate_fred_daily_fx_to_monthly(records, "2024-04-05T12:00:00Z")
    assert [(record["period_date"], record["value"]) for record in monthly] == [
        ("2024-02-01", 82.0), ("2024-03-01", 83.0),
    ]
    assert monthly[-1]["metadata"]["source_observation_date"] == "2024-03-28"


def test_real_dfm_historical_as_of_excludes_future_observations():
    records = [
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 100.0,
         "source": "FRED", "published_at": "2024-02-15T00:00:00Z"},
        {"indicator": "cpi_inflation", "period_date": "2024-02-01", "value": 110.0,
         "source": "FRED", "published_at": "2024-08-30T00:00:00Z"},
    ]
    prepared = prepare_real_dfm_data(records, as_of="2024-06-30T00:00:00Z")
    assert prepared["panel"]["cpi_inflation"].dropna().tolist() == [100.0]


def test_real_dfm_unknown_availability_is_not_admitted():
    data = pd.DataFrame(
        {"cpi_inflation": [100.0]},
        index=pd.to_datetime(["2024-01-01"]),
    )
    prepared = prepare_real_dfm_data(data, as_of="2024-06-30T00:00:00Z")
    assert prepared["panel"].empty
    assert prepared["transformed_panel"].empty
    assert any("no defensible availability timestamp" in warning for warning in prepared["warnings"])


def test_real_dfm_pit_selection_precedes_transform_and_preserves_values(monkeypatch):
    import econiq_macro_regime.macro_regime.fit_real_data as fit_real_data

    transformed_inputs = []

    def capture_transform(panel):
        transformed_inputs.append(panel.copy())
        return panel.copy()

    monkeypatch.setattr(fit_real_data, "transform_wide_frame", capture_transform)
    records = [
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 100.0,
         "source": "FRED", "published_at": "2024-02-15T00:00:00Z"},
        {"indicator": "cpi_inflation", "period_date": "2024-01-01", "value": 110.0,
         "source": "FRED", "published_at": "2024-08-30T00:00:00Z"},
    ]

    prepared = fit_real_data.prepare_real_dfm_data(records, as_of="2024-06-30T00:00:00Z")
    assert len(transformed_inputs) == 1
    assert transformed_inputs[0].loc["2024-01-01", "cpi_inflation"] == 100.0
    assert prepared["panel"].loc["2024-01-01", "cpi_inflation"] == 100.0
    assert prepared["transformed_panel"].loc["2024-01-01", "cpi_inflation"] == 100.0


def test_real_dfm_metadata_preserves_policy_rate_proxy_truth():
    policy = REAL_DFM_INDICATOR_METADATA["policy_rate"]
    assert "10Y government bond yield" in policy["economic_meaning"]
    assert "repo rate" in policy["economic_meaning"] or "RBI repo rate" in policy["notes"]


def test_real_dfm_strict_mode_rejects_unknown_historical_availability():
    wide = pd.DataFrame({"cpi_inflation": [100.0, 110.0]}, index=pd.to_datetime(["2024-01-01", "2024-02-01"]))
    prepared = prepare_real_dfm_data(wide, as_of="2024-06-30T00:00:00Z")
    assert prepared["panel"].empty
