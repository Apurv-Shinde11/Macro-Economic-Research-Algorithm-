from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import pandas as pd


DEFAULT_DATASET_VERSION = "point_in_time_v1"


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

    available_at = (
        record.get("available_at")
        or record.get("published_at")
        or record.get("ingested_at")
        or record.get("retrieved_at")
    )
    normalized["available_at"] = available_at
    normalized["availability_quality"] = "UNKNOWN"
    if available_at is not None:
        if record.get("available_at") is not None or record.get("published_at") is not None:
            normalized["availability_quality"] = "EXACT"
        elif record.get("ingested_at") is not None or record.get("retrieved_at") is not None:
            normalized["availability_quality"] = "INGESTION_PROXY"
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
        elif candidate_time == current_time and record.get("ingested_at") and not current.get("ingested_at"):
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
        selected = normalized
        quality_hint = "UNKNOWN"
        warnings: list[str] = ["as_of is None; dataset reflects all available records without historical eligibility filtering."]
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
                f"{len(future)} observation(s) were excluded because they were published after as_of={as_of}."
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

    manifest = {
        "as_of": as_of.isoformat() if isinstance(as_of, datetime) else str(as_of),
        "dataset_version": DEFAULT_DATASET_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indicator_count": len({r["indicator"] for r in selected}),
        "observation_count": len(selected),
        "earliest_observation": min((r["period_date"] for r in selected), default=None),
        "latest_observation": max((r["period_date"] for r in selected), default=None),
        "availability_quality": quality_by_indicator,
        "sources": sorted({r["source"] for r in selected}),
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
