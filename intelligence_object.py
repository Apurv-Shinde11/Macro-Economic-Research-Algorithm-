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
    "GLOBAL_LINKAGE",      # signals explaining when India's read is imported, not domestic
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


def _nearest_boundary(value: float, boundaries: list[float]) -> float | None:
    """
    The boundary VALUE itself (not the distance to it) -- e.g. for GDP
    growth at 7.2 with boundaries [6.0, 7.0], returns 7.0. Companion to
    _nearest_boundary_distance(); the story-generation layer needs both
    (the number to compare against, and how far away it is) to phrase a
    trigger condition without inventing which threshold is meant.
    """
    if value is None or not boundaries:
        return None
    return min(boundaries, key=lambda b: abs(value - b))


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
    nearest  = _nearest_boundary(raw_value, boundaries) if boundaries else None
    return {
        "id":                     label.lower().replace(" ", "_").replace("(", "").replace(")", ""),
        "label":                  label,
        "category":               category,
        "value":                  raw_value,
        "stance":                 stance(score),
        "score":                  score,
        "distance_to_threshold":  distance,
        "nearest_boundary":       nearest,
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
            "nearest_boundary": _nearest_boundary(growth, [6.0, 7.0]),
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
            "nearest_boundary": _nearest_boundary(inflation, [4.0, 6.0]),
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
            "nearest_boundary": _nearest_boundary(liquidity, [-0.3, 0.3]),
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
            "nearest_boundary": None,
            "weight": None,
        })

    return out


def _global_inputs_to_generic(regime_output: dict) -> list[dict]:
    """
    Folds in global-context inputs — data that explains when India's
    regime read is being driven by imported conditions rather than
    purely domestic ones. Both values are pre-computed elsewhere in the
    pipeline (yield_curve.py's analyse_curve() carry signal;
    main_api.py's _fetch_vol_term_structure() global risk proxy) and
    injected into regime_output["inputs"] before this function runs —
    no new fetchers, no invented thresholds.
    """
    inputs = regime_output.get("inputs", {}) or {}
    out = []

    carry_spread = inputs.get("india_us_carry_spread")
    if carry_spread is not None:
        # 2.5/3.5 mirror yield_curve.py::analyse_curve()'s own carry_signal
        # bands (FII_OUTFLOW_RISK / NEUTRAL_CARRY / STRONG_FII_MAGNET) —
        # keep in sync if those move.
        c_score = 1.0 if carry_spread > 3.5 else 0.5 if carry_spread > 2.5 else 0.0
        out.append({
            "id": "india_us_carry_spread", "label": "India-US 10Y Carry Spread",
            "category": "GLOBAL_LINKAGE",
            "value": carry_spread, "stance": stance(c_score), "score": c_score,
            "distance_to_threshold": _nearest_boundary_distance(carry_spread, [2.5, 3.5]),
            "nearest_boundary": _nearest_boundary(carry_spread, [2.5, 3.5]),
            "weight": None,
        })

    risk_score = inputs.get("global_risk_appetite_score")
    if risk_score is not None:
        # Pre-scored 0.85/0.50/0.10 tiers come straight from
        # main_api.py::_fetch_vol_term_structure()'s own CONTANGO/FLAT/
        # INVERTED classification — no raw-value band table to mirror
        # here, same treatment as this file's other pre-scored composite
        # indicators (see SENTINEL_LEADING_INDICATOR_MAP's None-boundary
        # entries above).
        out.append({
            "id": "global_risk_appetite", "label": "Global Risk Appetite (VIX Term Structure)",
            "category": "GLOBAL_LINKAGE",
            "value": inputs.get("global_risk_appetite_shape"),
            "stance": stance(risk_score), "score": risk_score,
            "distance_to_threshold": None,  # pre-scored, no numeric band
            "nearest_boundary": None,
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
    signals += _global_inputs_to_generic(regime_output)

    rel_flag, rel_note = reliability(confidence_score)

    return {
        "module": "sentinel",
        "theme": {
            "label": regime_output.get("regime", "").replace("_", " ").title(),
        },
        "confidence": {
            "score":                    confidence_score,
            "band":                     confidence_band(confidence_score),
            "reliability_flag":         rel_flag,
            "reliability_note":         rel_note,
            # Same field, same default, as main_api.py's own gate check
            # (regime.get("briefing_allowed", True)) -- mirrored here so
            # the story-generation layer can see it without depending on
            # main_api.py. Not a new gate; this is main_api.py's existing
            # gate made visible on the object that feeds the new layer.
            "briefing_allowed":         regime_output.get("briefing_allowed", True),
            "briefing_blocked_reason":  regime_output.get("briefing_blocked_reason", None),
        },
        "momentum": momentum,
        "signals": signals,
        "convergence":     detect_convergence(signals),
        "contradictions":  detect_contradictions(signals),
    }


# ══════════════════════════════════════════════════════════════════
# LAYER 2b — ATLAS-SPECIFIC MAPPING (minimal, India-only)
# ══════════════════════════════════════════════════════════════════

def build_atlas_intelligence_object(india_record: dict) -> dict:
    """
    india_record: one entry from GET /api/global-macro's economies[] list
    for code == 'IN' (see main_api.py::_build_economy_record). Pure
    reshape of India's own already-computed indicator values — no new
    data fetched, no LLM call.

    Deliberately scoped to India's own cross-category relationships only
    (e.g. "growth and PMI both point the same way") — NOT cross-economy
    comparison (e.g. "India vs. its peers"). That needs a real scoping
    decision (peer set, comparison methodology) that hasn't been made;
    improvising it here would misrepresent it as settled. The WHY
    section this feeds can honestly show convergence/contradiction
    within India's own signals; any cross-economy claim (including the
    Atlas mockup's own illustrative headline) stays placeholder until
    that separate piece is actually built.
    """
    gdp         = india_record.get("gdp_growth")
    pmi         = india_record.get("pmi")
    inflation   = india_record.get("inflation")
    policy_rate = india_record.get("policy_rate")
    currency    = india_record.get("currency_vs_usd")
    yield_10y   = india_record.get("yield_10y")

    signals = []

    if gdp is not None:
        # Mirrors the growth band already used for India elsewhere in this
        # file (_hard_inputs_to_generic) and regime_engine.py's _sig()
        # growth check: strong >=7.0, moderate >=6.0.
        _gdp_bounds = [6.0, 7.0]
        g_score = 1.0 if gdp >= 7.0 else 0.5 if gdp >= 6.0 else 0.0
        signals.append({
            "id": "gdp_growth", "label": "GDP Growth", "category": "GROWTH",
            "value": gdp, "stance": stance(g_score), "score": g_score,
            "distance_to_threshold": _nearest_boundary_distance(gdp, _gdp_bounds),
            "nearest_boundary": _nearest_boundary(gdp, _gdp_bounds),
        })

    if pmi is not None:
        # Boundaries mirror SENTINEL_LEADING_INDICATOR_MAP's Manufacturing
        # PMI band [50, 52, 55] above — same instrument, same thresholds.
        _pmi_bounds = [50, 55]
        p_score = 1.0 if pmi >= 55 else 0.5 if pmi >= 50 else 0.0
        signals.append({
            "id": "pmi", "label": "Manufacturing PMI", "category": "GROWTH",
            "value": pmi, "stance": stance(p_score), "score": p_score,
            "distance_to_threshold": _nearest_boundary_distance(pmi, _pmi_bounds),
            "nearest_boundary": _nearest_boundary(pmi, _pmi_bounds),
        })

    if inflation is not None:
        # Mirrors _hard_inputs_to_generic's inflation band — RBI's own
        # target (4.0) / upper tolerance (6.0), same as regime_engine.py's
        # self.inflation_target / self.inflation_upper.
        _inf_bounds = [4.0, 6.0]
        i_score = 1.0 if inflation < 4.0 else 0.5 if inflation < 6.0 else 0.0
        signals.append({
            "id": "inflation", "label": "Inflation (CPI)", "category": "INFLATION",
            "value": inflation, "stance": stance(i_score), "score": i_score,
            "distance_to_threshold": _nearest_boundary_distance(inflation, _inf_bounds),
            "nearest_boundary": _nearest_boundary(inflation, _inf_bounds),
        })

    if policy_rate is not None:
        # Mirrors regime_engine.py's self.repo_neutral = 6.0 — a full point
        # below is read as accommodative, a full point above as
        # restrictive. Deliberately conservative: no RBI stance/direction
        # signal (CUT/PAUSE/HIKE) is available here, only the raw rate
        # level, unlike Sentinel's rbi_stance signal.
        _rate_bounds = [5.0, 7.0]
        r_score = 1.0 if policy_rate <= 5.0 else 0.0 if policy_rate >= 7.0 else 0.5
        signals.append({
            "id": "policy_rate", "label": "RBI Policy Rate", "category": "POLICY",
            "value": policy_rate, "stance": stance(r_score), "score": r_score,
            "distance_to_threshold": _nearest_boundary_distance(policy_rate, _rate_bounds),
            "nearest_boundary": _nearest_boundary(policy_rate, _rate_bounds),
        })

    # Currency and yield are included per spec but deliberately left
    # NEUTRAL: unlike growth/inflation/PMI/policy rate, there's no
    # existing, defensible absolute-level threshold for these anywhere in
    # the codebase — a currency or yield LEVEL isn't inherently supportive
    # or stressed without a trend/delta, which isn't persisted today (see
    # stance()'s own docstring on this exact limitation). Included so
    # they're visible in the evidence trail, without asserting a
    # direction that isn't actually known. No boundaries exist for these,
    # so distance_to_threshold/nearest_boundary are honestly None rather
    # than guessed -- kept as explicit keys so every signal dict has the
    # same shape regardless of whether it has real threshold data.
    if currency is not None:
        signals.append({
            "id": "currency_usd_inr", "label": "USD/INR", "category": "EXTERNAL_MARKET",
            "value": currency, "stance": "NEUTRAL", "score": 0.5,
            "distance_to_threshold": None, "nearest_boundary": None,
        })
    if yield_10y is not None:
        signals.append({
            "id": "yield_10y", "label": "India 10Y Yield", "category": "EXTERNAL_MARKET",
            "value": yield_10y, "stance": "NEUTRAL", "score": 0.5,
            "distance_to_threshold": None, "nearest_boundary": None,
        })

    return {
        "module": "atlas",
        "theme": {"label": "India"},
        # Explicit None, not omitted -- Atlas has neither concept yet
        # (no regime-confidence equivalent, no persisted trend). Kept as
        # real keys so the shared story-generation layer can do
        # io.get("confidence") uniformly across both modules instead of
        # branching on whether the key exists at all.
        "confidence": None,
        "momentum": None,
        "signals": signals,
        "convergence":     detect_convergence(signals),
        "contradictions":  detect_contradictions(signals),
    }


# ══════════════════════════════════════════════════════════════════
# LAYER 2c — PE INTEL-SPECIFIC MAPPING
# ══════════════════════════════════════════════════════════════════

def build_pe_intelligence_object(
    regime: str,
    confidence_score: float | None,
    repo_rate: float | None,
    cost_of_capital: dict,
    briefing_allowed: bool = True,
    conviction: str | None = None,
) -> dict:
    """
    regime/confidence_score/repo_rate: the same fields already read off
    the user's latest `runs` row by main_api.py's /api/pe/overview --
    PE Intel has never had its own independent signal computation, it's
    a PE-flavoured reshape of the same regime read Sentinel scores.
    cost_of_capital: one entry of main_api.py's COST_OF_CAPITAL (or
    _build_live_cost_of_capital()'s output for that regime) --
    {"credit_spread": ..., "exit_environment": ..., "dry_powder_call": ...}.
    briefing_allowed: not a column on `runs` (only ever lived on the
    transient job result) -- callers derive this from that same run's
    persisted `story.status != "paused"` and pass it through, so PE
    Intel respects the same pause discipline Sentinel does rather than
    speaking confidently over a read the system itself judged too
    uncertain to narrate.
    conviction: the SAME `runs.conviction` field (strategy_engine.py's
    dispersion-adjusted HIGH/MEDIUM/LOW classifier) that pe.html's page
    header already renders as "N% confidence · X conviction" -- passed
    through here so confidence.band uses that exact value instead of
    independently re-deriving a LOW/MODERATE/HIGH bucket from the raw
    score via confidence_band(). The two are genuinely different
    formulas (conviction discounts for cross-scenario dispersion; the
    generic confidence_band() doesn't), and letting both reach the user
    produced the header and What Deserves Attention disagreeing on the
    SAME 54% score (MEDIUM conviction vs. LOW band) on one page. When
    conviction is omitted (e.g. a caller with no runs row) this falls
    back to confidence_band() so the field stays populated.

    Pure reshape -- computes no new judgment, just categorises fields
    _build_live_cost_of_capital() already produced.
    """
    rel_flag, rel_note = reliability(confidence_score)

    signals = []

    if repo_rate is not None:
        # Same [5.0, 7.0] bounds as Atlas's own policy_rate signal --
        # same instrument, same thresholds, not a new judgment call.
        _rate_bounds = [5.0, 7.0]
        r_score = 1.0 if repo_rate <= 5.0 else 0.0 if repo_rate >= 7.0 else 0.5
        signals.append({
            "id": "policy_rate", "label": "RBI Policy Rate", "category": "POLICY",
            "value": repo_rate, "stance": stance(r_score), "score": r_score,
            "distance_to_threshold": _nearest_boundary_distance(repo_rate, _rate_bounds),
            "nearest_boundary": _nearest_boundary(repo_rate, _rate_bounds),
        })

    # credit_spread / exit_environment are categorical reads from
    # COST_OF_CAPITAL, not numbers -- distance_to_threshold/
    # nearest_boundary stay honestly None, same pattern Atlas already
    # uses for currency/yield (no numeric boundary exists, so none is
    # claimed).
    _spread = (cost_of_capital or {}).get("credit_spread", "")
    if _spread:
        cs_score = (
            1.0 if "COMPRESSING" in _spread else
            0.0 if "WIDENING" in _spread or "SPIKING" in _spread else
            0.5
        )
        signals.append({
            "id": "credit_spread", "label": "Credit Spread", "category": "DOMESTIC_LIQUIDITY",
            "value": _spread, "stance": stance(cs_score), "score": cs_score,
            "distance_to_threshold": None, "nearest_boundary": None,
        })

    _exit = (cost_of_capital or {}).get("exit_environment", "")
    if _exit:
        ex_score = (
            1.0 if _exit.startswith("POSITIVE") or _exit.startswith("BUILDING") else
            0.0 if _exit.startswith("DIFFICULT") or _exit.startswith("CLOSED") or _exit.startswith("VERY") else
            0.5
        )
        signals.append({
            "id": "exit_environment", "label": "Exit Environment", "category": "FLOWS",
            "value": _exit, "stance": stance(ex_score), "score": ex_score,
            "distance_to_threshold": None, "nearest_boundary": None,
        })

    return {
        "module": "pe",
        "theme": {
            "label": (regime or "").replace("_", " ").title(),
        },
        "confidence": {
            "score":                    confidence_score,
            # Reuses the page's existing conviction classifier rather than
            # confidence_band()'s independent raw-score bucket -- see the
            # docstring above. reliability_flag/note below is deliberately
            # NOT changed: that's pegged to the original 70-80% backtest
            # band on raw score and must stay that way (see reliability()).
            "band":                     (conviction.upper() if conviction else confidence_band(confidence_score)),
            "reliability_flag":         rel_flag,
            "reliability_note":         rel_note,
            "briefing_allowed":         briefing_allowed,
            "briefing_blocked_reason":  None,
        },
        # dry_powder_call is PE's own derived conclusion (DEPLOY /
        # SELECTIVE / PRESERVE / HOLD), not an input signal -- treating
        # it as one would be circular. It plays the same structural
        # role Sentinel's leading_intelligence.trend plays for momentum.
        "momentum": (cost_of_capital or {}).get("dry_powder_call"),
        "signals": signals,
        "convergence":     detect_convergence(signals),
        "contradictions":  detect_contradictions(signals),
    }
