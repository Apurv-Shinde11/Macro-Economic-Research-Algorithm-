from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import pandas as pd


DEFAULT_DATASET_VERSION = "point_in_time_v1"
AVAILABILITY_QUALITIES = ("EXACT", "INGESTION_PROXY", "ESTIMATED", "UNKNOWN")


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text[:10])
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    normalized["indicator"] = str(record.get("indicator") or "").strip()
    if not normalized["indicator"]:
        raise ValueError("Observation records require a non-empty indicator name.")
    normalized["period_date"] = str(record.get("period_date"))
    if not normalized["period_date"]:
        raise ValueError(f"Observation for '{normalized['indicator']}' is missing period_date.")
    normalized["value"] = record.get("value")
    normalized["source"] = str(record.get("source") or "UNKNOWN")

    # Publication time is the only automatically trusted exact availability.
    # Database ingestion is a conservative proxy. Provider vintage dates,
    # observation dates, and HTTP retrieval times are not publication times.
    published = record.get("published_at")
    ingested = record.get("ingested_at")
    declared_quality = str(record.get("availability_quality") or "").upper()
    declared_timestamp = record.get("availability_timestamp")
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    availability_basis = record.get("availability_basis") or metadata.get("availability_basis")
    available_at = None
    quality = "UNKNOWN"
    if published is not None and _coerce_datetime(published) is not None:
        available_at, quality = published, "EXACT"
    elif (
        availability_basis == "FRED_VINTAGE_DATE"
        and declared_quality == "ESTIMATED"
        and _coerce_datetime(declared_timestamp) is not None
    ):
        # Provider vintage dates anchor historical provider information sets,
        # even when EconIQ retrieves those vintages years later. Their date-only
        # precision remains ESTIMATED and never becomes source publication time.
        available_at, quality = declared_timestamp, "ESTIMATED"
    elif ingested is not None and _coerce_datetime(ingested) is not None:
        available_at, quality = ingested, "INGESTION_PROXY"
    elif declared_quality == "INGESTION_PROXY" and _coerce_datetime(declared_timestamp) is not None:
        available_at, quality = declared_timestamp, "INGESTION_PROXY"
    elif declared_quality == "ESTIMATED" and declared_timestamp is not None:
        available_at, quality = declared_timestamp, "ESTIMATED"

    normalized["availability_timestamp"] = available_at
    normalized["available_at"] = available_at  # compatibility alias
    normalized["availability_quality"] = quality
    normalized["_available_dt"] = _coerce_datetime(available_at)
    return normalized


def _is_eligible(record: dict[str, Any], as_of: datetime) -> bool:
    available = record.get("_available_dt")
    if available is None:
        return False
    return available <= as_of


def _latest_for_key(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = (record["indicator"], record["period_date"])
        current = latest_by_key.get(key)
        if current is None:
            latest_by_key[key] = record
            continue
        current_time = current.get("_available_dt") or datetime.min.replace(tzinfo=timezone.utc)
        candidate_time = record.get("_available_dt") or datetime.min.replace(tzinfo=timezone.utc)
        if candidate_time > current_time:
            latest_by_key[key] = record
        elif candidate_time == current_time:
            candidate_key = (str(record.get("ingested_at") or ""), str(record.get("vintage_id") or ""), str(record.get("value")))
            current_key = (str(current.get("ingested_at") or ""), str(current.get("vintage_id") or ""), str(current.get("value")))
            if candidate_key > current_key:
                latest_by_key[key] = record
    return list(latest_by_key.values())


def build_point_in_time_dataset(
    records: Sequence[dict[str, Any]] | pd.DataFrame,
    as_of: str | datetime | None = None,
    indicators: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return the subset of records that was legitimately available by the as-of timestamp.

    This is intentionally conservative: if no defensible availability timestamp exists,
    the record is excluded rather than silently treated as live-safe. When only an
    ingestion timestamp exists we treat it as an INGESTION_PROXY, which is safer than
    pretending the value was known from a precise public release.
    """
    if records is None:
        records = []

    if isinstance(records, pd.DataFrame):
        rows = records.to_dict(orient="records")
    else:
        rows = list(records)

    normalized = [_normalize_record(record) for record in rows]
    if indicators is not None:
        allowed = {str(ind) for ind in indicators}
        normalized = [record for record in normalized if record["indicator"] in allowed]

    if as_of is None:
        eligible = [record for record in normalized if _is_eligible(record, datetime.max.replace(tzinfo=timezone.utc))]
        selected = _latest_for_key(eligible)
        quality_hint = "UNKNOWN"
        warnings: list[str] = ["as_of is None; latest vintages with defensible availability are selected."]
    else:
        as_of_dt = _coerce_datetime(as_of)
        if as_of_dt is None:
            raise ValueError(f"Could not parse as_of timestamp: {as_of!r}")
        eligible = [record for record in normalized if _is_eligible(record, as_of_dt)]
        selected = _latest_for_key(eligible)
        warnings = []
        missing = [r for r in normalized if r.get("_available_dt") is None]
        if missing:
            warnings.append(
                f"{len(missing)} observation(s) were excluded because no defensible availability timestamp was present."
            )

        future = [r for r in normalized if r.get("_available_dt") is not None and r["_available_dt"] > as_of_dt]
        if future:
            warnings.append(
                f"{len(future)} observation(s) were excluded because their availability timestamp is after as_of={as_of}."
            )
        quality_hint = "safe"

    selected = sorted(selected, key=lambda r: (r["indicator"], str(r["period_date"])))

    quality_by_indicator: dict[str, str] = {}
    for record in selected:
        indicator = record["indicator"]
        current = quality_by_indicator.get(indicator)
        if current is None or current == "UNKNOWN":
            quality_by_indicator[indicator] = record["availability_quality"]
        elif current == "INGESTION_PROXY" and record["availability_quality"] == "EXACT":
            quality_by_indicator[indicator] = "EXACT"

    eligible_records = [r for r in normalized if _is_eligible(r, as_of_dt if as_of is not None else datetime.max.replace(tzinfo=timezone.utc))]
    key_counts: dict[tuple[str, str], int] = defaultdict(int)
    eligible_key_counts: dict[tuple[str, str], int] = defaultdict(int)
    for record in normalized:
        key_counts[(record["indicator"], record["period_date"])] += 1
    for record in eligible_records:
        eligible_key_counts[(record["indicator"], record["period_date"])] += 1
    input_quality_counts = {
        quality: sum(r["availability_quality"] == quality for r in normalized)
        for quality in AVAILABILITY_QUALITIES
    }
    selected_quality_counts = {
        quality: sum(r["availability_quality"] == quality for r in selected)
        for quality in AVAILABILITY_QUALITIES
    }
    manifest = {
        "as_of": as_of.isoformat() if isinstance(as_of, datetime) else str(as_of),
        "dataset_version": DEFAULT_DATASET_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indicator_count": len({r["indicator"] for r in selected}),
        "observation_count": len(selected),
        "earliest_observation": min((r["period_date"] for r in selected), default=None),
        "latest_observation": max((r["period_date"] for r in selected), default=None),
        "availability_quality": quality_by_indicator,
        "availability_quality_counts": selected_quality_counts,
        "input_availability_quality_counts": input_quality_counts,
        "excluded_unknown_availability": sum(r["_available_dt"] is None for r in normalized),
        "excluded_future_observations": sum(r["_available_dt"] is not None and r["_available_dt"] > (as_of_dt if as_of is not None else datetime.max.replace(tzinfo=timezone.utc)) for r in normalized),
        "revision_rows_considered": sum(max(0, count - 1) for count in key_counts.values()),
        "revisions_selected": sum(max(0, count - 1) for count in eligible_key_counts.values()),
        "sources": sorted({r["source"] for r in selected}),
        "source_coverage": {source: sum(r["source"] == source for r in selected) for source in sorted({r["source"] for r in selected})},
        "warnings": warnings,
    }
    return {"records": selected, "manifest": manifest, "quality_hint": quality_hint}


def build_point_in_time_panel(
    records: Sequence[dict[str, Any]] | pd.DataFrame,
    as_of: str | datetime | None = None,
    indicators: Sequence[str] | None = None,
) -> pd.DataFrame:
    dataset = build_point_in_time_dataset(records=records, as_of=as_of, indicators=indicators)
    rows = [
        {"period_date": record["period_date"], "indicator": record["indicator"], "value": record["value"]}
        for record in dataset["records"]
    ]
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["period_date"] = pd.to_datetime(frame["period_date"])
    panel = frame.pivot_table(index="period_date", columns="indicator", values="value", aggfunc="last")

    start = frame["period_date"].min()
    if as_of is not None:
        as_of_dt = _coerce_datetime(as_of)
        if as_of_dt is not None:
            end = pd.Timestamp(as_of_dt.year, as_of_dt.month, 1)
        else:
            end = frame["period_date"].max()
    else:
        end = frame["period_date"].max()
    full_index = pd.date_range(start=start, end=end, freq="MS")
    panel = panel.reindex(full_index)
    if indicators is not None:
        panel = panel.reindex(columns=list(indicators))
    return panel.sort_index()
