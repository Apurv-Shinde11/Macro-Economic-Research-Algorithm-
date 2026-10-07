from datetime import date

from econiq_macro_regime.macro_regime import ingest


def test_fred_realtime_query_fields_are_preserved_without_claiming_release_time(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"observations": [
                {"date": "2024-01-31", "value": "100", "realtime_start": "2024-02-01", "realtime_end": "2024-02-01"},
                {"date": "2024-02-29", "value": "101", "realtime_start": "2024-03-01", "realtime_end": "2024-03-01"},
            ]}

    monkeypatch.setattr(ingest.requests, "get", lambda *_args, **_kwargs: Response())
    rows = ingest.fetch_fred_series("TEST", "key")
    assert rows[0].period_date == date(2024, 1, 31)
    assert rows[0].provider_metadata == {
        "realtime_start": "2024-02-01", "realtime_end": "2024-02-01",
    }
    assert not hasattr(rows[0], "published_at")


def test_daily_month_resampling_retains_last_source_date_and_metadata():
    rows = [
        ingest.ProviderObservation(date(2024, 1, 30), 82.0, {"realtime_start": "2024-02-01"}),
        ingest.ProviderObservation(date(2024, 1, 31), 82.5, {"realtime_start": "2024-02-01"}),
        ingest.ProviderObservation(date(2024, 2, 1), 82.7, {"realtime_start": "2024-02-02"}),
    ]
    monthly = ingest.resample_daily_to_monthly(rows)
    assert [(row.period_date, row.value) for row in monthly] == [
        (date(2024, 1, 1), 82.5), (date(2024, 2, 1), 82.7),
    ]
    assert monthly[0].provider_metadata["source_observation_date"] == "2024-01-31"
    assert monthly[0].provider_metadata["realtime_start"] == "2024-02-01"


def test_insert_rows_persists_lineage_and_does_not_invent_publication_time():
    inserted = []

    class Query:
        def insert(self, rows):
            inserted.extend(rows)
            return self

        def execute(self):
            return None

    class Client:
        def table(self, name):
            assert name == "macro_timeseries"
            return Query()

    observation = ingest.ProviderObservation(date(2024, 1, 1), 5.0, {"status": "observed"})
    ingest.insert_rows(Client(), "gdp_growth", "WORLD_BANK", [observation])
    row = inserted[0]
    assert row["provider_series"] == ingest.WORLD_BANK_SERIES["gdp_growth"]
    assert row["metadata"]["provider_response"] == {"status": "observed"}
    assert "published_at" not in row
    assert "vintage_id" not in row


def test_industrial_production_growth_current_ingestion_uses_its_fred_series():
    inserted = []

    class Query:
        def insert(self, rows):
            inserted.extend(rows)
            return self

        def execute(self):
            return None

    class Client:
        def table(self, name):
            assert name == "macro_timeseries"
            return Query()

    indicator = "industrial_production_growth"
    series_id = "INDPRMNTO01GYSAM"
    assert ingest.FRED_SERIES[indicator] == series_id
    ingest.insert_rows(
        Client(), indicator, "FRED", [ingest.ProviderObservation(date(2026, 6, 1), 7.60216)]
    )
    assert inserted[0]["indicator"] == indicator
    assert inserted[0]["provider_series"] == series_id
    assert inserted[0]["source"] == "FRED"
