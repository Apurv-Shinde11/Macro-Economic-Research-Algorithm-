from datetime import date

import pytest
import requests

from econiq_macro_regime.macro_regime import fred_vintages as fv
from econiq_macro_regime.macro_regime.point_in_time_dataset import build_point_in_time_dataset


def _response(payload):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    return Response()


def test_vintage_dates_are_parsed_and_pagination_is_followed(monkeypatch):
    monkeypatch.setattr(fv, "VINTAGE_DATE_PAGE_SIZE", 2)
    calls = []

    def get(_url, params, timeout):
        calls.append((params["offset"], timeout))
        if params["offset"] == 0:
            return _response({"count": 3, "vintage_dates": ["2020-01-15", "2020-02-15"]})
        return _response({"count": 3, "vintage_dates": ["2020-03-15"]})

    dates = fv.fetch_vintage_dates("TEST", "hidden-key", get=get)
    assert dates == [date(2020, 1, 15), date(2020, 2, 15), date(2020, 3, 15)]
    assert calls == [(0, 30), (2, 30)]


def test_vintage_observations_parse_versions_and_skip_fred_missing_values(monkeypatch):
    monkeypatch.setattr(fv, "OBSERVATION_PAGE_SIZE", 2)
    calls = []

    def get(_url, params, timeout):
        calls.append(params)
        rows = [
            {"date": "2019-04-01", "value": "100.0", "realtime_start": "2020-01-15", "realtime_end": "2020-02-14"},
            {"date": "2019-04-01", "value": ".", "realtime_start": "2020-02-15", "realtime_end": "2020-03-14"},
            {"date": "2019-04-01", "value": "102.0", "realtime_start": "2020-03-15", "realtime_end": "9999-12-31"},
        ]
        start = params["offset"]
        page = rows[start : start + params["limit"]]
        return _response({"count": len(rows), "observations": page})

    parsed = fv.fetch_vintage_observations(
        "TEST", "hidden-key", [date(2020, 1, 15), date(2020, 2, 15), date(2020, 3, 15)],
        vintage_batch_size=3, get=get,
    )
    assert [(row.period_date, row.value, row.provider_vintage_date) for row in parsed] == [
        (date(2019, 4, 1), 100.0, date(2020, 1, 15)),
        (date(2019, 4, 1), 102.0, date(2020, 3, 15)),
    ]
    assert len(calls) == 2
    assert calls[0]["output_type"] == 3
    assert calls[0]["vintage_dates"] == "2020-01-15,2020-02-15,2020-03-15"


def test_vintage_rows_preserve_fred_semantics_and_pit_selects_revision():
    observations = [
        fv.FredVintageObservation(date(2019, 4, 1), 100.0, date(2020, 1, 15), date(2020, 1, 15), date(2020, 2, 14)),
        fv.FredVintageObservation(date(2019, 4, 1), 102.0, date(2020, 3, 15), date(2020, 3, 15), None),
    ]
    rows = fv.build_vintage_rows("cpi_inflation", "INDCPIALLMINMEI", observations)
    for row in rows:
        row["ingested_at"] = "2026-10-05T00:00:00Z"
    assert len({row["vintage_id"] for row in rows}) == 2
    assert rows[0]["provider_series"] == "INDCPIALLMINMEI"
    assert rows[0]["provider_vintage_date"] == "2020-01-15"
    assert rows[0]["published_at"] is None
    assert rows[0]["availability_quality"] == "ESTIMATED"
    assert rows[0]["availability_basis"] == "FRED_VINTAGE_DATE"
    assert rows[0]["metadata"]["published_at_semantics"].startswith("source_agency")

    early = build_point_in_time_dataset(rows, as_of="2020-02-01T23:59:59Z")
    later = build_point_in_time_dataset(rows, as_of="2020-04-01T23:59:59Z")
    assert early["records"][0]["value"] == 100.0
    assert later["records"][0]["value"] == 102.0
    assert early["records"][0]["availability_quality"] == "ESTIMATED"


def test_same_logical_vintage_is_idempotent_on_repeated_persistence():
    stored = {}
    requests_seen = []

    class Query:
        def upsert(self, rows, *, on_conflict, ignore_duplicates):
            requests_seen.append((on_conflict, ignore_duplicates))
            self.rows = rows
            return self

        def execute(self):
            for row in self.rows:
                stored.setdefault(row["vintage_id"], row)

    class Client:
        def table(self, table_name):
            assert table_name == "macro_timeseries"
            return Query()

    observation = fv.FredVintageObservation(date(2019, 4, 1), 100.0, date(2020, 1, 15), date(2020, 1, 15), None)
    rows = fv.build_vintage_rows("cpi_inflation", "INDCPIALLMINMEI", [observation])
    first = fv.persist_vintage_rows(Client(), rows)
    second = fv.persist_vintage_rows(Client(), rows)
    assert first["rows_submitted"] == second["rows_submitted"] == 1
    assert len(stored) == 1
    assert requests_seen == [(fv.FRED_VINTAGE_CONFLICT_TARGET, True)] * 2


def test_dry_run_reports_candidates_without_database_write():
    observation = fv.FredVintageObservation(date(2019, 4, 1), 100.0, date(2020, 1, 15), date(2020, 1, 15), None)
    rows = fv.build_vintage_rows("cpi_inflation", "INDCPIALLMINMEI", [observation])
    report = fv.persist_vintage_rows(None, rows, dry_run=True)
    assert report["rows_parsed"] == 1
    assert report["rows_would_upsert"] == 1
    assert report["rows_submitted"] == 0
    assert report["dry_run_duplicate_check"] == "not_checked_without_database"
    assert report["preview"][0]["availability_quality"] == "ESTIMATED"


def test_dry_run_acquires_and_previews_without_a_supabase_client():
    def get(url, params, timeout):
        if url.endswith("/vintagedates"):
            return _response({"count": 1, "vintage_dates": ["2020-01-15"]})
        return _response({"count": 1, "observations": [
            {"date": "2019-04-01", "value": "100", "realtime_start": "2020-01-15", "realtime_end": "9999-12-31"}
        ]})

    report = fv.acquire_and_persist_vintages(
        "cpi_inflation", "INDCPIALLMINMEI", "hidden-key", dry_run=True, get=get,
    )
    assert report["vintage_dates_fetched"] == 1
    assert report["rows_parsed"] == 1
    assert report["rows_would_upsert"] == 1
    assert report["rows_submitted"] == 0


def test_invalid_numeric_values_fail_and_missing_marker_is_not_a_row():
    requested = {date(2020, 1, 15)}
    missing = fv._parse_observation(
        {"date": "2019-04-01", "value": ".", "realtime_start": "2020-01-15"},
        "TEST", requested,
    )
    assert missing is None
    with pytest.raises(fv.FredVintagePayloadError):
        fv._parse_observation(
            {"date": "2019-04-01", "value": "not-a-number", "realtime_start": "2020-01-15"},
            "TEST", requested,
        )


def test_output_type_three_live_discovered_vintage_column_shape():
    parsed = fv._parse_observation_row(
        {"date": "1957-01-01", "INDCPIALLMINMEI_20240515": "1.471388"},
        "INDCPIALLMINMEI",
        {date(2024, 5, 15)},
    )
    assert len(parsed) == 1
    observation = parsed[0]
    assert observation.period_date == date(1957, 1, 1)
    assert observation.value == 1.471388
    assert observation.provider_vintage_date == date(2024, 5, 15)
    assert observation.realtime_start is None
    assert observation.realtime_end is None


def test_output_type_three_row_expands_multiple_vintage_columns():
    parsed = fv._parse_observation_row(
        {
            "date": "2019-04-01",
            "SERIES_WITH_UNDERSCORE_20240101": "100.0",
            "SERIES_WITH_UNDERSCORE_20240201": "101.0",
        },
        "SERIES_WITH_UNDERSCORE",
        {date(2024, 1, 1), date(2024, 2, 1)},
    )
    assert [(item.value, item.provider_vintage_date) for item in parsed] == [
        (100.0, date(2024, 1, 1)),
        (101.0, date(2024, 2, 1)),
    ]


@pytest.mark.parametrize("cell", [".", None, ""])
def test_output_type_three_missing_cells_are_skipped(cell):
    parsed = fv._parse_observation_row(
        {"date": "2019-04-01", "TEST_20240101": cell},
        "TEST",
        {date(2024, 1, 1)},
    )
    assert parsed == []


def test_output_type_three_malformed_numeric_value_fails_clearly():
    with pytest.raises(fv.FredVintagePayloadError, match="not numeric"):
        fv._parse_observation_row(
            {"date": "2019-04-01", "TEST_20240101": "not-a-number"},
            "TEST",
            {date(2024, 1, 1)},
        )


def test_output_type_three_wrong_series_prefix_is_rejected():
    with pytest.raises(fv.FredVintagePayloadError, match="does not match requested series"):
        fv._parse_observation_row(
            {"date": "2019-04-01", "OTHER_20240101": "100"},
            "TEST",
            {date(2024, 1, 1)},
        )


@pytest.mark.parametrize("column", ["TEST_bad", "TEST_20241340"])
def test_output_type_three_malformed_vintage_suffix_is_rejected(column):
    with pytest.raises(fv.FredVintagePayloadError, match="(Malformed|Invalid date)"):
        fv._parse_observation_row(
            {"date": "2019-04-01", column: "100"},
            "TEST",
            {date(2024, 1, 1)},
        )


def test_output_type_three_vintage_id_is_deterministic_without_realtime_fields():
    row = {"date": "1957-01-01", "INDCPIALLMINMEI_20240515": "1.471388"}
    requested = {date(2024, 5, 15)}
    first = fv._parse_observation_row(row, "INDCPIALLMINMEI", requested)[0]
    second = fv._parse_observation_row(row, "INDCPIALLMINMEI", requested)[0]
    assert first.vintage_id("INDCPIALLMINMEI") == second.vintage_id("INDCPIALLMINMEI")


def test_output_type_three_canonical_row_keeps_conservative_availability_semantics():
    observation = fv._parse_observation_row(
        {"date": "1957-01-01", "INDCPIALLMINMEI_20240515": "1.471388"},
        "INDCPIALLMINMEI",
        {date(2024, 5, 15)},
    )[0]
    row = fv.build_vintage_rows("cpi_inflation", "INDCPIALLMINMEI", [observation])[0]
    assert row["provider_vintage_date"] == "2024-05-15"
    assert row["published_at"] is None
    assert row["availability_quality"] == "ESTIMATED"
    assert row["availability_basis"] == "FRED_VINTAGE_DATE"
    assert row["metadata"]["provider_realtime_start"] is None
    assert row["metadata"]["provider_realtime_end"] is None


def test_output_type_three_row_without_vintage_cells_is_omitted():
    assert fv._parse_observation_row(
        {"date": "2019-04-01"}, "TEST", {date(2024, 1, 1)}
    ) == []


def test_empty_history_is_explicit_and_does_not_touch_database():
    def get(_url, params, timeout):
        return _response({"count": 0, "vintage_dates": []})

    report = fv.acquire_and_persist_vintages("cpi_inflation", "TEST", "hidden-key", get=get)
    assert report["status"] == "empty_vintage_history"
    assert report["rows_submitted"] == 0


def test_missing_api_key_fails_before_provider_request():
    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("missing-key validation must happen before HTTP")

    with pytest.raises(fv.FredVintageError, match="API key is required"):
        fv.fetch_vintage_dates("TEST", "", get=unexpected_request)


def test_provider_failure_is_not_substituted_with_latest_data():
    class Client:
        def table(self, *_args):
            raise AssertionError("provider failure must occur before persistence")

    def get(*_args, **_kwargs):
        raise requests.HTTPError("fixture failure")

    with pytest.raises(fv.FredVintageError):
        fv.acquire_and_persist_vintages(
            "cpi_inflation", "TEST", "hidden-key", supabase=Client(), get=get,
        )


def test_duplicate_provider_rows_with_conflicting_values_are_rejected():
    first = {"date": "2019-04-01", "value": "100", "realtime_start": "2020-01-15", "realtime_end": "9999-12-31"}
    second = {**first, "value": "101"}

    def get(_url, params, timeout):
        return _response({"count": 2, "observations": [first, second]})

    with pytest.raises(fv.FredVintagePayloadError, match="Conflicting values"):
        fv.fetch_vintage_observations("TEST", "hidden-key", [date(2020, 1, 15)], get=get)
