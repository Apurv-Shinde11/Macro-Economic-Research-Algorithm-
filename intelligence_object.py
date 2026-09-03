"""
intelligence_object.py — Generic story-layer input object.

Assembles a module-agnostic {theme, signals, momentum, convergence,
contradictions, confidence} shape from a module's already-computed
signal outputs, for the eventual NLP reasoning layer to consume.

This file intentionally has two layers:

  1. GENERIC helpers (category vocabulary, stance/band classification,
     confidence reliability flagging, convergence/contradiction
     detection) — these know nothing about Sentinel, regimes, or any
     specific indicator name. Atlas and PE Intel are meant to reuse
     these directly.

  2. SENTINEL-SPECIFIC mapping (`build_sentinel_intelligence_object`)
     — knows how to read regime_engine.py's `detect_regime()` output
     and MacroRegimeEngine's indicator names, and turns it into the
     generic shape via the layer-1 helpers.

Nothing here recomputes or overrides regime_engine.py's scoring — it
only reads already-computed values (leading_intelligence.signals,
inputs, confidence) and reshapes/categorizes them. Threshold numbers
used for distance-to-threshold are mirrored from regime_engine.py's
_compute_leading_score() / _compute_signal_alignment() bands (cited
inline) rather than duplicated logic living in two places long-term —
if those bands change in regime_engine.py, update SENTINEL_THRESHOLDS
here to match.

No LLM call happens in this file. This is the deterministic input
object the eventual story prompt will consume — that prompt/call is a
separate, not-yet-built piece.
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════
# LAYER 1 — GENERIC, MODULE-AGNOSTIC
# ══════════════════════════════════════════════════════════════════

# Shared category vocabulary. A module's mapping function tags its own
# signals into these buckets; the convergence/contradiction detector
# below only ever reasons in terms of these labels, never raw
# indicator names.
SIGNAL_CATEGORIES = {
    "EXTERNAL_MARKET",     # crude, VIX, FII flows, global risk sentiment
    "DOMESTIC_LIQUIDITY",  # system liquidity, credit impulse, credit spreads
    "GROWTH",              # GDP, PMI, IIP-style real-economy signals
    "INFLATION",           # CPI and inflation-adjacent signals
    "POLICY",              # RBI stance, rate direction
    "FLOWS",               # capital flow trend signals distinct from spot FII level
    "VALUATION",           # P/E and valuation-level signals
    "COMPOSITE",           # signals that are themselves already a blend (e.g. IS-LM)
}

# Confidence bands + the flagged-unreliable range. This range is a
# provisional flag pending the separate backtest re-validation
# (Prompt 2) — see _reliability() docstring for the exact wording
# requirement.
CONFIDENCE_CAUTION_BAND = (0.70, 0.80)


def confidence_band(score: float) -> str:
    """LOW / MODERATE / HIGH bucket for a 0-1 confidence score."""
    if score is None:
        return "LOW"
    if score < 0.60:
        return "LOW"
    if score < 0.80:
        return "MODERATE"
    return "HIGH"


def reliability(score: float) -> tuple[str, str | None]:
    """
    Returns (reliability_flag, reliability_note).

    IMPORTANT — wording constraint (explicit product decision): the
    70-80% band is flagged because the ORIGINAL backtest showed poor
    outcomes there, but that finding has NOT been re-validated against
    current data (that's the separate, still-pending Prompt 2
    investigation). The note must read as provisional, not as a
    settled/confirmed finding. Do not strengthen this wording without
    that re-validation landing first.

    Wording is copied verbatim from the Sentinel Briefing UI mockup's
    'banded' state (finalized with Claude Design) so the backend field
    and the frontend's hardcoded copy never silently diverge. The
    frontend currently owns its own copy of this text rather than
    reading this field directly -- if that changes, this is the source
    of truth to read from.
    """
    if score is None:
        return "NORMAL", None
    lo, hi = CONFIDENCE_CAUTION_BAND
    if lo <= score < hi:
        return "CAUTION", (
            "This read sits in the 70–80% confidence band. An earlier "
            "backtest flagged this band as less reliable than others, "
            "and we have not yet re-confirmed that finding on current "
            "data. On that provisional basis the narrative below is "
            "presented in full but should carry less weight than its "
            "confidence number suggests. Treat the contradictions as "
            "the operative content."
        )
    return "NORMAL", None


def stance(score: float, supportive_at: float = 0.6, stressed_at: float = 0.4) -> str:
    """SUPPORTIVE / NEUTRAL / STRESSED from a 0-1 supportive-vs-stressed score.

    Named "stance" rather than "direction: UP/DOWN" deliberately — none
    of the source data available today (regime_engine.py's leading
    signals) carries a real day-over-day delta (yesterday's VIX/yield/
    PMI aren't persisted anywhere), so this reflects today's snapshot
    classification, not a verified trend. Framing it as UP/DOWN would
    overstate what's actually known.
    """
    if score is None:
        return "NEUTRAL"
    if score >= supportive_at:
        return "SUPPORTIVE"
    if score <= stressed_at:
        return "STRESSED"
    return "NEUTRAL"


def _nearest_boundary_distance(value: float, boundaries: list[float]) -> float | None:
    if value is None or not boundaries:
        return None
    return round(min(abs(value - b) for b in boundaries), 3)


def detect_convergence(signals: list[dict]) -> list[dict]:
    """
    Groups signals by category; when 2+ signals in the same category
    share a stance (all SUPPORTIVE or all STRESSED), that's convergence.
    Generic — operates purely on category/stance, no indicator-name
    knowledge.
    """
    by_category: dict[str, list[dict]] = {}
    for s in signals:
        by_category.setdefault(s["category"], []).append(s)

    out = []
    for category, group in by_category.items():
        if len(group) < 2:
            continue
        stances = {s["stance"] for s in group if s["stance"] != "NEUTRAL"}
        if len(stances) == 1:
            shared_stance = next(iter(stances))
            aligned = [s for s in group if s["stance"] == shared_stance]
            if len(aligned) < 2:
                continue
            out.append({
                "signal_ids": [s["id"] for s in aligned],
                "category":   category,
                "stance":     shared_stance,
                "description": (
                    f"{len(aligned)} {category.replace('_', ' ').lower()} "
                    f"signals are {shared_stance.lower()} together: "
                    + ", ".join(s["label"] for s in aligned)
                ),
            })
    return out


def detect_contradictions(signals: list[dict]) -> list[dict]:
    """
    Compares category groups pairwise; when one category's signals are
    clearly SUPPORTIVE on average and another's are clearly STRESSED,
    that's a contradiction worth naming (e.g. domestic liquidity
    supportive while external market signals are stressed).
    """
    by_category: dict[str, list[dict]] = {}
    for s in signals:
        by_category.setdefault(s["category"], []).append(s)

    def _avg_score(group):
        scored = [s["score"] for s in group if s["score"] is not None]
        return sum(scored) / len(scored) if scored else None

    def _dominant_stance(group):
        avg = _avg_score(group)
        if avg is None:
            return "NEUTRAL"
        return stance(avg)

    cats = sorted(by_category.keys())
    out = []
    for i in range(len(cats)):
        for j in range(i + 1, len(cats)):
            cat_a, cat_b = cats[i], cats[j]
            group_a, group_b = by_category[cat_a], by_category[cat_b]
            stance_a, stance_b = _dominant_stance(group_a), _dominant_stance(group_b)
            if {stance_a, stance_b} == {"SUPPORTIVE", "STRESSED"}:
                out.append({
                    "signal_ids": [s["id"] for s in group_a + group_b],
                    "description": (
                        f"{cat_a.replace('_', ' ').lower()} signals read "
                        f"{stance_a.lower()} while {cat_b.replace('_', ' ').lower()} "
                        f"signals read {stance_b.lower()} — these are pulling "
                        f"against each other, not confirming the same story."
                    ),
                })
    return out


# ══════════════════════════════════════════════════════════════════
# LAYER 2 — SENTINEL-SPECIFIC MAPPING
# ══════════════════════════════════════════════════════════════════

# indicator label (as emitted by regime_engine._compute_leading_score)
#   -> (category, raw-value threshold boundaries or None)
#
# Boundaries are mirrored from regime_engine.py::_compute_leading_score
# band edges as of this writing. Composite/pre-scored indicators
# (GARCH, credit impulse, FII trend, credit spread, IS-LM) arrive
# already as a 0-1 score with no accompanying raw-value band table in
# regime_engine.py, so their distance_to_threshold is honestly None
# rather than guessed.
SENTINEL_LEADING_INDICATOR_MAP = {
    "India VIX":               ("EXTERNAL_MARKET",    [14, 18, 22]),
    "Crude Oil":                ("EXTERNAL_MARKET",    [75, 95, 105]),
    "FII Flows":                 ("EXTERNAL_MARKET",    [-3000, -500, 500, 3000]),
    "India Yield Curve":     ("GROWTH",              [0, 0.5, 1.5]),
    "Manufacturing PMI":  ("GROWTH",              [50, 52, 55]),
    "Nifty P/E Ratio":       ("VALUATION",           [18, 22, 26, 30]),
    "Vol Forecast (GARCH)":            ("EXTERNAL_MARKET",    None),
    "Credit Impulse":               ("DOMESTIC_LIQUIDITY",  None),
    "FII Trend (7d)":               ("FLOWS",               None),
    "Credit Spread (AAA-GSec)":     ("DOMESTIC_LIQUIDITY",  None),
    "IS-LM Composite":              ("COMPOSITE",           None),
}


def _leading_signal_to_generic(sig: dict) -> dict:
    label = sig.get("indicator", "unknown")
    category, boundaries = SENTINEL_LEADING_INDICATOR_MAP.get(label, ("COMPOSITE", None))
    score = sig.get("score")
    raw_value = sig.get("value")
    distance = _nearest_boundary_distance(raw_value, boundaries) if boundaries else None
    return {
        "id":                     label.lower().replace(" ", "_").replace("(", "").replace(")", ""),
        "label":                  label,
        "category":               category,
        "value":                  raw_value,
        "stance":                 stance(score),
        "score":                  score,
        "distance_to_threshold":  distance,
        "weight":                 sig.get("weight"),
    }


def _hard_inputs_to_generic(regime_output: dict) -> list[dict]:
    """
    Folds in the core hard-data inputs (growth, inflation, liquidity,
    RBI stance) as signals too — these drive the regime classification
    itself but live outside leading_intelligence.signals, and the
    user's own illustrative example ("domestic liquidity up, RBI
    stance supportive") treats them as first-class story inputs.
    Thresholds mirrored from regime_engine.py's own attributes
    (inflation_target=4.0, inflation_upper=6.0) and the _sig() calls
    inside _compute_signal_alignment (gdp thresholds 6.0/7.0;
    liquidity 0.3/-0.3).
    """
    inputs = regime_output.get("inputs", {}) or {}
    out = []

    growth = inputs.get("growth")
    if growth is not None:
        # Boundaries (6.0/7.0) mirror regime_engine.py's _sig(growth, 7.0, 6.0)
        # call in _compute_signal_alignment() — keep in sync if those move.
        # NOTE: the 0.5 mid-tier score here is this file's own simplification,
        # not a mirror — regime_engine.py's _sig() returns 0.3 for that band.
        g_score = 1.0 if growth >= 7.0 else 0.5 if growth >= 6.0 else 0.0
        out.append({
            "id": "gdp_growth", "label": "GDP Growth", "category": "GROWTH",
            "value": growth, "stance": stance(g_score), "score": g_score,
            "distance_to_threshold": _nearest_boundary_distance(growth, [6.0, 7.0]),
            "weight": None,
        })

    inflation = inputs.get("inflation")
    if inflation is not None:
        # 4.0/6.0 mirror regime_engine.py's self.inflation_target /
        # self.inflation_upper class attributes — keep in sync if those move.
        # (regime_engine.py also has a separate _sig-based inflation band
        # at 4.5/6.0 used elsewhere; this deliberately follows the named
        # class attributes instead.)
        i_score = 1.0 if inflation < 4.0 else 0.5 if inflation < 6.0 else 0.0
        out.append({
            "id": "inflation", "label": "Inflation (CPI)", "category": "INFLATION",
            "value": inflation, "stance": stance(i_score), "score": i_score,
            "distance_to_threshold": _nearest_boundary_distance(inflation, [4.0, 6.0]),
            "weight": None,
        })

    liquidity = inputs.get("liquidity_score")
    if liquidity is not None:
        # 0.3/-0.3 is this file's own symmetric simplification, not a literal
        # mirror — regime_engine.py has three different liquidity _sig() bands
        # ((0.3,0.0), (0.5,0.1), (-0.5,-0.1)) for different purposes. If those
        # shift meaningfully, revisit whether 0.3/-0.3 still tracks them.
        l_score = 1.0 if liquidity >= 0.3 else 0.0 if liquidity <= -0.3 else 0.5
        out.append({
            "id": "domestic_liquidity", "label": "Domestic Liquidity", "category": "DOMESTIC_LIQUIDITY",
            "value": liquidity, "stance": stance(l_score), "score": l_score,
            "distance_to_threshold": _nearest_boundary_distance(liquidity, [-0.3, 0.3]),
            "weight": None,
        })

    rbi_signal = inputs.get("rbi_signal")
    if rbi_signal:
        # Direction (CUT supportive, HIKE stressed) mirrors regime_engine.py's
        # rbi_cut/rbi_hike _sig-equivalent in _compute_signal_alignment()
        # (line ~492) — keep in sync if that scoring direction changes.
        r_score = {"CUT": 1.0, "PAUSE": 0.5, "UNKNOWN": 0.5, "HIKE": 0.0}.get(rbi_signal, 0.5)
        out.append({
            "id": "rbi_stance", "label": "RBI Policy Stance", "category": "POLICY",
            "value": rbi_signal, "stance": stance(r_score), "score": r_score,
            "distance_to_threshold": None,  # categorical, no numeric distance
            "weight": None,
        })

    return out


def build_sentinel_intelligence_object(regime_output: dict) -> dict:
    """
    regime_output: the full dict returned by MacroRegimeEngine.detect_regime().
    Pure reshape — does not call any signal math, does not call an LLM.
    """
    confidence_score = regime_output.get("confidence")
    leading_signals = (regime_output.get("leading_intelligence", {}) or {}).get("signals", []) or []
    momentum = (regime_output.get("leading_intelligence", {}) or {}).get("trend", "STABLE")

    signals = [_leading_signal_to_generic(s) for s in leading_signals]
    signals += _hard_inputs_to_generic(regime_output)

    rel_flag, rel_note = reliability(confidence_score)

    return {
        "module": "sentinel",
        "theme": {
            "label": regime_output.get("regime", "").replace("_", " ").title(),
        },
        "confidence": {
            "score":             confidence_score,
            "band":              confidence_band(confidence_score),
            "reliability_flag":  rel_flag,
            "reliability_note":  rel_note,
        },
        "momentum": momentum,
        "signals": signals,
        "convergence":     detect_convergence(signals),
        "contradictions":  detect_contradictions(signals),
    }
