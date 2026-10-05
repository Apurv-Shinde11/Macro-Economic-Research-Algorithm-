"""FRED/ALFRED vintage discovery, parsing, and idempotent persistence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import math
from typing import Any, Callable, Iterable

import requests


FRED_BASE_URL = "https://api.stlouisfed.org/fred/series"
VINTAGE_DATE_PAGE_SIZE = 10_000
OBSERVATION_PAGE_SIZE = 100_000
VINTAGE_DATE_BATCH_SIZE = 100
UPSERT_BATCH_SIZE = 500
FRED_VINTAGE_CONFLICT_TARGET = (
    "economy,indicator,period_date,source,provider_series,vintage_id"
)


class FredVintageError(RuntimeError):
    """Raised when FRED vintage data cannot be acquired or trusted."""


class FredVintagePayloadError(FredVintageError):
    """Raised when FRED returns malformed or internally inconsistent data."""


@dataclass(frozen=True)
class FredVintageObservation:
    period_date: date
    value: float
    provider_vintage_date: date
    realtime_start: date
    realtime_end: date | None

    def vintage_id(self, series_id: str) -> str:
        return (
            f"FRED:{series_id}:{self.period_date.isoformat()}:"
            f"{self.provider_vintage_date.isoformat()}"
        )

    @property
    def availability_timestamp(self) -> str:
        # FRED vintage dates have day precision, not an exact publication time.
        # Use the end of that UTC date so PIT cannot admit it earlier that day.
        return datetime.combine(
            self.provider_vintage_date, time.max, tzinfo=timezone.utc
        ).isoformat()


def _json_response(response: Any) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FredVintageError(f"FRED request failed: {exc}") from exc
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise FredVintagePayloadError("FRED returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise FredVintagePayloadError("FRED response must be a JSON object.")
    if "error_code" in payload or "error_message" in payload:
        raise FredVintageError(
            f"FRED API error {payload.get('error_code', '')}: "
            f"{payload.get('error_message', 'unspecified provider error')}"
        )
    return payload


def _get_json(get: Callable[..., Any], url: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = get(url, params=params, timeout=30)
    except requests.RequestException as exc:
        raise FredVintageError(f"FRED request failed: {exc}") from exc
    return _json_response(response)


def _parse_provider_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise FredVintagePayloadError(f"FRED {field} must be a YYYY-MM-DD string.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FredVintagePayloadError(f"Invalid FRED {field}: {value!r}") from exc


def fetch_vintage_dates(
    series_id: str,
    api_key: str,
    *,
    vintage_start: str | None = None,
    vintage_end: str | None = None,
    get: Callable[..., Any] = requests.get,
) -> list[date]:
    """Fetch every FRED vintage date, following the endpoint's offset pages."""
    if not api_key or not api_key.strip():
        raise FredVintageError("A FRED API key is required for vintage acquisition.")
    if not series_id or not series_id.strip():
        raise ValueError("series_id must be non-empty.")
    if vintage_start:
        _parse_provider_date(vintage_start, "vintage_start")
    if vintage_end:
        _parse_provider_date(vintage_end, "vintage_end")
    if vintage_start and vintage_end and vintage_start > vintage_end:
        raise ValueError("vintage_start must be on or before vintage_end.")

    dates: list[date] = []
    offset = 0
    while True:
        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "limit": VINTAGE_DATE_PAGE_SIZE,
            "offset": offset,
            "sort_order": "asc",
        }
        if vintage_start:
            params["realtime_start"] = vintage_start
        if vintage_end:
            params["realtime_end"] = vintage_end
        payload = _get_json(get, f"{FRED_BASE_URL}/vintagedates", params)
        page = payload.get("vintage_dates")
        if not isinstance(page, list):
            raise FredVintagePayloadError("FRED vintage-date response is missing vintage_dates.")
        parsed = [_parse_provider_date(item, "vintage_date") for item in page]
        dates.extend(parsed)
        count = payload.get("count")
        if count is not None:
            try:
                total_count = int(count)
            except (ValueError, TypeError) as exc:
                raise FredVintagePayloadError("FRED vintage-date count is invalid.") from exc
            if offset + len(page) >= total_count:
                break
            if not page or len(page) < VINTAGE_DATE_PAGE_SIZE:
                raise FredVintagePayloadError("FRED vintage-date pagination ended before the reported count.")
        elif len(page) < VINTAGE_DATE_PAGE_SIZE:
            break
        if not page:
            raise FredVintagePayloadError("FRED vintage-date pagination made no progress.")
        offset += len(page)
    return sorted(set(dates))


def _parse_observation(row: Any, series_id: str, requested_dates: set[date]) -> FredVintageObservation | None:
    if not isinstance(row, dict):
        raise FredVintagePayloadError("FRED observation must be a JSON object.")
    raw_value = row.get("value")
    if raw_value == ".":
        return None
    if raw_value is None or not isinstance(raw_value, (str, int, float)):
        raise FredVintagePayloadError("FRED observation has an invalid numeric value.")
    try:
        value = float(raw_value)
    except (ValueError, TypeError) as exc:
        raise FredVintagePayloadError(f"FRED value is not numeric: {raw_value!r}") from exc
    if not math.isfinite(value):
        raise FredVintagePayloadError(f"FRED value must be finite: {raw_value!r}")

    period = _parse_provider_date(row.get("date"), "observation date")
    realtime_start = _parse_provider_date(row.get("realtime_start"), "observation realtime_start")
    if realtime_start not in requested_dates:
        raise FredVintagePayloadError(
            f"Observation vintage {realtime_start} was not requested for series {series_id}."
        )
    raw_realtime_end = row.get("realtime_end")
    realtime_end = (
        _parse_provider_date(raw_realtime_end, "observation realtime_end")
        if raw_realtime_end not in (None, "") else None
    )
    return FredVintageObservation(period, value, realtime_start, realtime_start, realtime_end)


def fetch_vintage_observations(
    series_id: str,
    api_key: str,
    vintage_dates: Iterable[date],
    *,
    vintage_batch_size: int = VINTAGE_DATE_BATCH_SIZE,
    get: Callable[..., Any] = requests.get,
) -> list[FredVintageObservation]:
    """Fetch new/revised rows for vintage dates in bounded, paginated requests."""
    if not api_key or not api_key.strip():
        raise FredVintageError("A FRED API key is required for vintage acquisition.")
    if not 1 <= vintage_batch_size <= 2_000:
        raise ValueError("vintage_batch_size must be between 1 and 2000.")
    dates = sorted(set(vintage_dates))
    if not dates:
        return []

    parsed_by_key: dict[tuple[date, date], FredVintageObservation] = {}
    for start in range(0, len(dates), vintage_batch_size):
        batch = dates[start : start + vintage_batch_size]
        requested_dates = set(batch)
        offset = 0
        while True:
            params = {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "output_type": 3,
                "vintage_dates": ",".join(item.isoformat() for item in batch),
                "limit": OBSERVATION_PAGE_SIZE,
                "offset": offset,
                "sort_order": "asc",
            }
            payload = _get_json(get, f"{FRED_BASE_URL}/observations", params)
            page = payload.get("observations")
            if not isinstance(page, list):
                raise FredVintagePayloadError("FRED observations response is missing observations.")
            parsed_page = [
                observation
                for observation in (
                    _parse_observation(row, series_id, requested_dates) for row in page
                )
                if observation is not None
            ]
            for observation in parsed_page:
                key = (observation.period_date, observation.provider_vintage_date)
                previous = parsed_by_key.get(key)
                if previous and previous.value != observation.value:
                    raise FredVintagePayloadError(
                        f"Conflicting values for {key} in FRED response pages."
                    )
                parsed_by_key[key] = observation
            count = payload.get("count")
            if count is not None:
                try:
                    total_count = int(count)
                except (ValueError, TypeError) as exc:
                    raise FredVintagePayloadError("FRED observation count is invalid.") from exc
                if offset + len(page) >= total_count:
                    break
                if not page or len(page) < OBSERVATION_PAGE_SIZE:
                    raise FredVintagePayloadError("FRED observation pagination ended before the reported count.")
            elif len(page) < OBSERVATION_PAGE_SIZE:
                break
            if not page:
                raise FredVintagePayloadError("FRED observation pagination made no progress.")
            offset += len(page)

    return sorted(
        parsed_by_key.values(),
        key=lambda item: (item.period_date, item.provider_vintage_date),
    )


def build_vintage_rows(
    indicator: str,
    series_id: str,
    observations: Iterable[FredVintageObservation],
) -> list[dict[str, Any]]:
    """Map provider observations to the canonical, provenance-rich DB model."""
    observations = sorted(
        observations,
        key=lambda item: (item.period_date, item.provider_vintage_date),
    )
    rows: list[dict[str, Any]] = []
    for observation in observations:
        rows.append({
            "economy": "IN",
            "indicator": indicator,
            "period_date": observation.period_date.isoformat(),
            "value": observation.value,
            "source": "FRED",
            "provider_series": series_id,
            "published_at": None,
            "provider_vintage_date": observation.provider_vintage_date.isoformat(),
            "vintage_id": observation.vintage_id(series_id),
            # Provider vintage date is the stable revision order/identity;
            # do not invent a local ordinal that varies with bounded backfills.
            "revision_number": None,
            "availability_timestamp": observation.availability_timestamp,
            "availability_quality": "ESTIMATED",
            "availability_basis": "FRED_VINTAGE_DATE",
            "metadata": {
                "provider": "FRED/ALFRED",
                "provider_vintage_date": observation.provider_vintage_date.isoformat(),
                "provider_realtime_start": observation.realtime_start.isoformat(),
                "provider_realtime_end": (
                    observation.realtime_end.isoformat() if observation.realtime_end else None
                ),
                "provider_frequency": "daily" if series_id == "DEXINUS" else "monthly",
                "availability_basis": "FRED_VINTAGE_DATE",
                "availability_precision": "date",
                "availability_timestamp_rule": "end_of_vintage_date_UTC_conservative_day_boundary",
                "published_at_semantics": "source_agency_publication_time_not_provided_by_FRED_vintage_date",
            },
        })
    return rows


def persist_vintage_rows(
    supabase: Any,
    rows: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    batch_size: int = UPSERT_BATCH_SIZE,
) -> dict[str, Any]:
    """Persist stable vintage IDs with insert-only conflict handling."""
    if not 1 <= batch_size <= UPSERT_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {UPSERT_BATCH_SIZE}.")
    vintage_ids = [row.get("vintage_id") for row in rows]
    if any(not vintage_id for vintage_id in vintage_ids):
        raise ValueError("Every FRED vintage row requires a stable vintage_id.")
    if len(vintage_ids) != len(set(vintage_ids)):
        raise ValueError("Duplicate logical FRED vintage IDs are present in the input.")

    report = {
        "rows_parsed": len(rows),
        "rows_would_upsert": len(rows) if dry_run else None,
        "dry_run_duplicate_check": "not_checked_without_database" if dry_run else None,
        "rows_submitted": 0 if dry_run else len(rows),
        "dry_run": dry_run,
        "quality": "ESTIMATED" if rows else None,
        "date_range": (
            [min(row["period_date"] for row in rows), max(row["period_date"] for row in rows)]
            if rows else None
        ),
        "provider_vintage_date_range": (
            [min(row["provider_vintage_date"] for row in rows), max(row["provider_vintage_date"] for row in rows)]
            if rows else None
        ),
        "preview": [
            {
                "period_date": row["period_date"],
                "value": row["value"],
                "provider_vintage_date": row["provider_vintage_date"],
                "vintage_id": row["vintage_id"],
                "availability_quality": row["availability_quality"],
            }
            for row in rows[:10]
        ],
    }
    if dry_run or not rows:
        return report
    if supabase is None:
        raise ValueError("A Supabase client is required unless dry_run=True.")

    # A unique provider-vintage key plus ignore_duplicates makes repeated runs
    # idempotent without updating or overwriting prior vintage rows.
    for offset in range(0, len(rows), batch_size):
        try:
            supabase.table("macro_timeseries").upsert(
                rows[offset : offset + batch_size],
                on_conflict=FRED_VINTAGE_CONFLICT_TARGET,
                ignore_duplicates=True,
            ).execute()
        except Exception as exc:
            raise FredVintageError(
                "FRED vintage persistence failed. Confirm migration 003 has been "
                "applied and the provider-vintage unique index exists."
            ) from exc
    return report


def acquire_and_persist_vintages(
    indicator: str,
    series_id: str,
    api_key: str,
    *,
    supabase: Any = None,
    dry_run: bool = False,
    vintage_start: str | None = None,
    vintage_end: str | None = None,
    get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    vintage_dates = fetch_vintage_dates(
        series_id,
        api_key,
        vintage_start=vintage_start,
        vintage_end=vintage_end,
        get=get,
    )
    if not vintage_dates:
        return {"status": "empty_vintage_history", "indicator": indicator, "series_id": series_id,
                "vintage_dates_fetched": 0, "rows_parsed": 0, "rows_submitted": 0, "dry_run": dry_run}
    observations = fetch_vintage_observations(series_id, api_key, vintage_dates, get=get)
    rows = build_vintage_rows(indicator, series_id, observations)
    report = persist_vintage_rows(supabase, rows, dry_run=dry_run)
    report.update({
        "status": "ok" if rows else "empty_observation_history",
        "indicator": indicator,
        "series_id": series_id,
        "vintage_dates_fetched": len(vintage_dates),
    })
    return report
