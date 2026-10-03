"""Shared signal provenance and operational freshness helpers."""
from __future__ import annotations

from datetime import date, datetime, timezone


FRESHNESS_POLICIES = {
    "MARKET_DAILY": {"current_days": 2, "recent_days": 5},
    "POLICY_EVENT": {"current_days": 90, "recent_days": 180},
    "WEEKLY": {"current_days": 21, "recent_days": 45},
    "MONTHLY_MACRO": {"current_days": 60, "recent_days": 120},
}

SIGNAL_FREQUENCY_CLASSES = {
    "cpi": "MONTHLY_MACRO",
    "repo_rate": "POLICY_EVENT",
    "bank_credit_growth": "WEEKLY",
    "fii": "MARKET_DAILY",
    "dii": "MARKET_DAILY",
    "india_vix": "MARKET_DAILY",
    "crude_oil": "MARKET_DAILY",
    "usd_inr": "MARKET_DAILY",
    "india_10y": "MARKET_DAILY",
    "us_10y": "MARKET_DAILY",
}

SIGNAL_UNITS = {
    "cpi": "% YoY",
    "repo_rate": "%",
    "bank_credit_growth": "% YoY",
    "fii": "INR crore net",
    "dii": "INR crore net",
    "india_vix": "index points",
    "crude_oil": "USD/barrel",
    "usd_inr": "INR per USD",
    "india_10y": "% yield",
    "us_10y": "% yield",
}

ACQUISITION_STATES = {"LIVE", "CACHED", "FALLBACK", "MISSING"}
FRESHNESS_STATES = {"CURRENT", "RECENT", "STALE", "UNKNOWN"}
SOURCE_TYPES = {"PRIMARY", "SECONDARY", "CACHE", "FALLBACK", "UNKNOWN"}


def _parse_observation(value):
    if not value:
        return None, "unknown"
    if isinstance(value, datetime):
        return value.date(), "day"
    if isinstance(value, date):
        return value, "day"

    raw = str(value).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date(), "day"
    except ValueError:
        pass

    for fmt in (
        "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M",
        "%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(raw, fmt).date(), "day"
        except ValueError:
            continue

    try:
        parsed = datetime.strptime(raw, "%Y-%m")
        return date(parsed.year, parsed.month, 1), "month"
    except ValueError:
        for fmt in ("%B %Y", "%b %Y"):
            try:
                parsed = datetime.strptime(raw, fmt)
                return date(parsed.year, parsed.month, 1), "month"
            except ValueError:
                continue
    return None, "unknown"


def _freshness(observed_at, frequency_class, now):
    observation_date, precision = _parse_observation(observed_at)
    if observation_date is None:
        return "UNKNOWN", None, precision

    age_days = max(0, (now.date() - observation_date).days)
    policy = FRESHNESS_POLICIES[frequency_class]
    if age_days <= policy["current_days"]:
        state = "CURRENT"
    elif age_days <= policy["recent_days"]:
        state = "RECENT"
    else:
        state = "STALE"
    return state, age_days, precision


def build_signal_metadata(
    signal,
    value=None,
    source="unknown",
    source_type="UNKNOWN",
    observed_at=None,
    retrieved_at=None,
    acquisition="LIVE",
    fallback_reason=None,
    cached=False,
    now=None,
    **extra,
):
    """Build one metadata record without changing or manufacturing its value."""
    if signal not in SIGNAL_FREQUENCY_CLASSES:
        raise ValueError(f"Unknown migrated signal: {signal}")

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    if value is None:
        acquisition = "MISSING"
        source_type = "UNKNOWN" if source_type == "UNKNOWN" else source_type
    if acquisition not in ACQUISITION_STATES:
        raise ValueError(f"Invalid acquisition state: {acquisition}")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"Invalid source type: {source_type}")

    frequency_class = SIGNAL_FREQUENCY_CLASSES[signal]
    freshness, age_days, precision = _freshness(
        observed_at, frequency_class, now
    )
    if acquisition == "MISSING":
        freshness = "UNKNOWN"
        quality = "POOR"
    elif freshness == "UNKNOWN":
        quality = "UNKNOWN"
    elif freshness == "STALE" or acquisition == "FALLBACK":
        quality = "DEGRADED"
    else:
        quality = "GOOD"

    record = {
        "signal": signal,
        "value": value,
        "unit": SIGNAL_UNITS[signal],
        "source": source or "unknown",
        "source_type": source_type,
        "observed_at": observed_at,
        "observation_precision": precision,
        "age_basis": "month_start_upper_bound" if precision == "month" else (
            "observation_date" if precision == "day" else "unknown"
        ),
        "retrieved_at": retrieved_at or now.isoformat(),
        "freshness": freshness,
        "frequency_class": frequency_class,
        "age_days": age_days,
        "acquisition": acquisition,
        "fallback_used": acquisition == "FALLBACK",
        "fallback_reason": fallback_reason,
        "cached": bool(cached),
        "quality": quality,
    }
    record.update(extra)
    return record


def build_signal_provenance(signal_inputs, now=None):
    """Create the fixed initial signal set; absent inputs remain explicitly missing."""
    now = now or datetime.now(timezone.utc)
    provenance = {}
    for signal in SIGNAL_FREQUENCY_CLASSES:
        details = dict(signal_inputs.get(signal) or {})
        details.pop("signal", None)
        provenance[signal] = build_signal_metadata(
            signal=signal,
            now=now,
            **details,
        )
    return provenance


def build_data_quality_summary(provenance):
    """Return deterministic operational counts, not a model-confidence score."""
    states = {
        "freshness": {key: 0 for key in FRESHNESS_STATES},
        "acquisition": {key: 0 for key in ACQUISITION_STATES},
    }
    covered = 0
    for item in provenance.values():
        freshness = item.get("freshness", "UNKNOWN")
        acquisition = item.get("acquisition", "MISSING")
        states["freshness"][freshness if freshness in FRESHNESS_STATES else "UNKNOWN"] += 1
        states["acquisition"][acquisition if acquisition in ACQUISITION_STATES else "MISSING"] += 1
        if item.get("value") is not None and acquisition != "MISSING":
            covered += 1

    total = len(provenance)
    coverage = round(covered / total * 100) if total else 0
    if covered == 0:
        quality = "UNKNOWN"
    elif (
        coverage >= 90
        and states["freshness"]["STALE"] == 0
        and states["acquisition"]["MISSING"] == 0
        and states["acquisition"]["FALLBACK"] <= 1
    ):
        quality = "GOOD"
    elif coverage >= 70:
        quality = "DEGRADED"
    else:
        quality = "POOR"

    return {
        "total_signals": total,
        "current": states["freshness"]["CURRENT"],
        "recent": states["freshness"]["RECENT"],
        "stale": states["freshness"]["STALE"],
        "unknown": states["freshness"]["UNKNOWN"],
        "live": states["acquisition"]["LIVE"],
        "cached": states["acquisition"]["CACHED"],
        "fallback": states["acquisition"]["FALLBACK"],
        "missing": states["acquisition"]["MISSING"],
        "coverage_pct": coverage,
        "quality": quality,
        "quality_basis": (
            "Operational counts only: GOOD requires >=90% coverage, no stale or "
            "missing signals, and at most one fallback; DEGRADED requires >=70% "
            "coverage; otherwise POOR. UNKNOWN means no values are available. "
            "This is not a model-confidence probability."
        ),
    }


def provenance_alerts(provenance):
    alerts = []
    for signal, item in provenance.items():
        if item["acquisition"] == "FALLBACK":
            alerts.append(f"fallback={signal}")
        if item["acquisition"] == "MISSING":
            alerts.append(f"missing={signal}")
        if item["freshness"] == "STALE":
            alerts.append(f"stale={signal}")
        if item["freshness"] == "UNKNOWN":
            alerts.append(f"unknown_observation_date={signal}")
    return alerts
