from datetime import date

import pytest
import requests

from econiq_macro_regime.macro_regime import fred_vintages as fv
from econiq_macro_regime.macro_regime.point_in_time_dataset import build_point_in_time_dataset


def _response(payload):
    class Response:
        status_code = 200

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
    assert calls == [(0, fv.FRED_REQUEST_TIMEOUT), (2, fv.FRED_REQUEST_TIMEOUT)]


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


def test_output_type_3_empty_page_with_positive_count_is_valid_sparse_result():
    calls = []

    def get(_url, params, timeout):
        calls.append(params)
        return _response({"count": 261, "observations": []})

    stats = {}
    parsed = fv.fetch_vintage_observations(
        "DEXINUS", "hidden-key", [date(2016, 2, 16)],
        observation_start="1973-01-02", observation_end="1974-01-01",
        get=get, _request_stats=stats,
    )

    assert parsed == []
    assert calls[0]["output_type"] == 3
    assert stats["requests"] == 1
    assert stats["raw_provider_rows"] == 0
    assert stats["_last_request_diagnostic"]["reported_count"] == 261
    assert stats["_last_request_diagnostic"]["rows_returned"] == 0


def test_empty_type_3_chunk_completes_with_count_not_matching_returned_rows():
    def get(_url, params, timeout):
        return _response({"count": 261, "observations": []})

    observations, report = fv.fetch_vintage_observations_chunked(
        "DEXINUS", "hidden-key", [date(2016, 2, 16)],
        date(1973, 1, 2), date(1974, 1, 1), get=get,
    )

    assert observations == []
    assert report["complete"] is True
    assert report["total_chunks"] == 1
    assert report["completed_chunks"] == 1
    assert report["failed_chunks"] == []


def test_premature_short_nonempty_page_has_structured_chunk_diagnostics(monkeypatch):
    monkeypatch.setattr(fv, "OBSERVATION_PAGE_SIZE", 2)

    def get(_url, params, timeout):
        return _response({"count": 3, "observations": [
            {"date": "2020-01-01", "TEST_20200102": "1.0"}
        ]})

    with pytest.raises(fv.FredVintageAcquisitionError) as raised:
        fv.fetch_vintage_observations_chunked(
            "TEST", "hidden-key", [date(2020, 1, 2)],
            date(2020, 1, 1), date(2020, 1, 1), get=get,
        )

    report = raised.value.report
    assert report["complete"] is False
    assert report["total_chunks"] == 1
    assert report["completed_chunks"] == 0
    failure = report["failed_chunks"][0]
    assert failure["vintage_batch_index"] == 0
    assert failure["vintage_batch_size"] == 1
    assert failure["first_vintage"] == failure["last_vintage"] == "2020-01-02"
    assert failure["observation_window_index"] == 0
    assert failure["failed_page"]["page_number"] == 1
    assert failure["failed_page"]["offset"] == 0
    assert failure["failed_page"]["limit"] == 2
    assert failure["failed_page"]["http_status"] == 200
    assert failure["failed_page"]["reported_count"] == 3
    assert failure["failed_page"]["rows_returned"] == 1
    assert failure["failed_page"]["cumulative_rows"] == 1
    assert failure["failed_page"]["retry_number"] == 1
    assert failure["failed_page"]["request_duration_seconds"] >= 0
    assert failure["elapsed_seconds"] >= 0


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


def test_observation_date_windows_and_vintage_batches_merge_completely():
    dates = [date(2020, 1, 1), date(2020, 1, 3)]
    calls = []

    def get(_url, params, timeout):
        calls.append(params)
        row = {"date": params["observation_start"]}
        for vintage in dates:
            row[f"TEST_{vintage:%Y%m%d}"] = str(10 + vintage.day)
        return _response({"count": 1, "observations": [row]})

    observations, report = fv.fetch_vintage_observations_chunked(
        "TEST", "hidden-key", dates, date(2020, 1, 1), date(2020, 1, 4),
        vintage_batch_size=2, observation_window_days=2, get=get,
    )

    assert [(item.period_date, item.provider_vintage_date) for item in observations] == [
        (date(2020, 1, 1), dates[0]),
        (date(2020, 1, 1), dates[1]),
        (date(2020, 1, 3), dates[0]),
        (date(2020, 1, 3), dates[1]),
    ]
    assert len(calls) == 2
    assert all(call["output_type"] == 3 for call in calls)
    assert all(call["observation_start"] and call["observation_end"] for call in calls)
    assert report["complete"] is True
    assert report["requested_vintage_date_count"] == 2
    assert report["successfully_processed_vintage_date_count"] == 2
    assert report["requested_observation_windows"] == 2
    assert report["successfully_processed_observation_windows"] == 2
    assert report["failed_chunks"] == []
    assert report["canonical_observations"] == 4


def test_overlapping_chunks_deduplicate_identical_logical_observations():
    item = fv.FredVintageObservation(
        date(2020, 1, 2), 10.0, date(2020, 2, 1), None, None
    )
    report = {"duplicate_logical_identities": 0, "conflicting_logical_identities": 0}
    merged = {}

    fv._merge_vintage_observations(merged, [item], report)
    fv._merge_vintage_observations(merged, [item], report)

    assert list(merged.values()) == [item]
    assert report["duplicate_logical_identities"] == 1
    assert report["conflicting_logical_identities"] == 0


def test_overlapping_chunks_with_different_values_fail():
    first = fv.FredVintageObservation(
        date(2020, 1, 2), 10.0, date(2020, 2, 1), None, None
    )
    conflicting = fv.FredVintageObservation(
        date(2020, 1, 2), 11.0, date(2020, 2, 1), None, None
    )
    report = {"duplicate_logical_identities": 0, "conflicting_logical_identities": 0}
    merged = {(first.period_date, first.provider_vintage_date): first}

    with pytest.raises(fv.FredVintagePayloadError, match="Conflicting values"):
        fv._merge_vintage_observations(merged, [conflicting], report)
    assert report["conflicting_logical_identities"] == 1


def test_transient_timeout_retries_and_succeeds(monkeypatch):
    monkeypatch.setattr(fv.time_module, "sleep", lambda _seconds: None)
    calls = []

    def get(_url, params, timeout):
        calls.append((params, timeout))
        if len(calls) == 1:
            raise requests.Timeout("temporary timeout")
        return _response({"count": 1, "observations": [
            {"date": "2020-01-02", "TEST_20200201": "10.0"}
        ]})

    stats = {}
    parsed = fv.fetch_vintage_observations(
        "TEST", "hidden-key", [date(2020, 2, 1)], get=get, _request_stats=stats
    )

    assert len(parsed) == 1
    assert len(calls) == 2
    assert stats["requests"] == 2
    assert stats["successful_requests"] == 1
    assert stats["failed_requests"] == 1
    assert stats["retries"] == 1


def test_retry_exhaustion_fails_loudly(monkeypatch):
    monkeypatch.setattr(fv.time_module, "sleep", lambda _seconds: None)
    calls = []

    def get(*_args, **_kwargs):
        calls.append(1)
        raise requests.Timeout("still unavailable")

    stats = {}
    with pytest.raises(fv.FredVintageError, match="failed after 3 attempts"):
        fv.fetch_vintage_observations(
            "TEST", "hidden-key", [date(2020, 2, 1)], get=get, _request_stats=stats
        )
    assert len(calls) == 3
    assert stats["retries"] == 2
    assert stats["failed_requests"] == 3


def test_failed_observation_window_cannot_report_partial_acquisition_as_complete(monkeypatch):
    monkeypatch.setattr(fv, "FRED_MAX_RETRIES", 0)
    monkeypatch.setattr(fv.time_module, "sleep", lambda _seconds: None)
    calls = []

    def get(_url, params, timeout):
        calls.append(params)
        if params["observation_start"] == "2020-01-03":
            raise requests.Timeout("window unavailable")
        return _response({"count": 1, "observations": [
            {"date": "2020-01-01", "TEST_20200201": "10.0"}
        ]})

    with pytest.raises(fv.FredVintageAcquisitionError) as raised:
        fv.fetch_vintage_observations_chunked(
            "TEST", "hidden-key", [date(2020, 2, 1)],
            date(2020, 1, 1), date(2020, 1, 4), observation_window_days=2, get=get,
        )

    assert len(calls) == 2
    assert raised.value.report["complete"] is False
    assert raised.value.report["successfully_processed_observation_windows"] == 1
    assert raised.value.report["successfully_processed_vintage_date_count"] == 0
    assert raised.value.report["completed_chunks"] == 1
    assert raised.value.report["total_chunks"] == 2
    assert len(raised.value.report["failed_chunks"]) == 1
    failure = raised.value.report["failed_chunks"][0]
    assert failure["vintage_batch_index"] == 0
    assert failure["observation_window_index"] == 1
    assert failure["failed_page"]["page_number"] == 1
    assert failure["failed_page"]["offset"] == 0
    assert failure["failed_page"]["limit"] == fv.OBSERVATION_PAGE_SIZE
    assert failure["failed_page"]["retry_number"] == 1
    assert failure["retry_count"] == 0
    assert failure["elapsed_seconds"] >= 0


def test_vintage_observation_date_bounds_are_sent_and_missing_cells_skipped():
    calls = []

    def get(_url, params, timeout):
        calls.append(params)
        return _response({"count": 1, "observations": [
            {"date": "2020-01-02", "TEST_20200201": "."}
        ]})

    stats = {}
    parsed = fv.fetch_vintage_observations(
        "TEST", "hidden-key", [date(2020, 2, 1)],
        observation_start="2020-01-01", observation_end="2020-01-31",
        get=get, _request_stats=stats,
    )

    assert parsed == []
    assert calls[0]["observation_start"] == "2020-01-01"
    assert calls[0]["observation_end"] == "2020-01-31"
    assert stats["explicit_missing_cells"] == 1
    assert stats.get("parsed_observations", 0) == 0


def test_series_observation_range_is_parsed_from_provider_metadata():
    def get(_url, params, timeout):
        assert params["series_id"] == "TEST"
        return _response({"seriess": [{
            "observation_start": "1973-01-02",
            "observation_end": "2026-09-25",
        }]})

    assert fv.fetch_series_observation_range("TEST", "hidden-key", get=get) == (
        date(1973, 1, 2), date(2026, 9, 25)
    )


def test_dexinus_acquisition_uses_metadata_and_complete_date_chunking():
    calls = []

    def get(url, params, timeout):
        calls.append((url, params))
        if url.endswith("/vintagedates"):
            return _response({"count": 1, "vintage_dates": ["2020-02-01"]})
        if url.endswith("/series"):
            return _response({"seriess": [{
                "observation_start": "2020-01-01",
                "observation_end": "2020-12-30",
            }]})
        assert params["observation_start"] == "2020-01-01"
        assert params["observation_end"] == "2020-12-30"
        return _response({"count": 1, "observations": [
            {"date": "2020-01-02", "DEXINUS_20200201": "73.0"}
        ]})

    report = fv.acquire_and_persist_vintages(
        "currency_inr_usd", "DEXINUS", "hidden-key", dry_run=True, get=get,
    )

    assert report["rows_parsed"] == 1
    assert report["rows_submitted"] == 0
    assert report["acquisition"]["complete"] is True
    assert report["acquisition"]["requested_vintage_date_count"] == 1
    assert report["acquisition"]["successfully_processed_vintage_date_count"] == 1
    assert report["acquisition"]["successfully_processed_observation_windows"] == 1
    assert report["acquisition"]["failed_chunks"] == []
    assert report["preview"][0]["availability_quality"] == "ESTIMATED"
    assert len(calls) == 3


def test_india_10y_keeps_existing_unbounded_observation_path():
    observation_params = []

    def get(url, params, timeout):
        if url.endswith("/vintagedates"):
            return _response({"count": 1, "vintage_dates": ["2020-01-15"]})
        observation_params.append(params)
        return _response({"count": 1, "observations": [
            {"date": "2019-04-01", "INDIRLTLT01STM_20200115": "6.5"}
        ]})

    report = fv.acquire_and_persist_vintages(
        "policy_rate", "INDIRLTLT01STM", "hidden-key", dry_run=True, get=get,
    )

    assert report["rows_parsed"] == 1
    assert "acquisition" not in report
    assert "observation_start" not in observation_params[0]
    assert "observation_end" not in observation_params[0]
