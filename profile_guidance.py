"""
profile_guidance.py — deterministic, profile-aware SO WHAT layer.

Reinterprets the SAME grounded regime read every viewer sees (an
intelligence_object built by intelligence_object.py -- theme, confidence,
momentum, signals) into different concrete portfolio guidance depending on
who is reading it. Same opportunity, different fit per user, per the
"Opportunity Score vs User Fit" split in the original product design.

Deliberately NOT an LLM call. mandate_type (4) x risk_tolerance (3) x
investment_horizon (3) = 36 real, distinct guidance postures -- small
enough to define as explicit, auditable rules, the same way
dashboard.html's REGIME_PLAYBOOKS / _getMandatePlays() already handles
regime x conviction x mandate_type for the (separate) Recommended
Playbook card. client_profile affects only phrasing tone, not the
substance of the guidance; aum_size and benchmark are not read here --
see the personalization design review for why those three axes were
excluded from the bucket key for v1.

Grounding: every claim this module makes is either (a) the profile's own
declared category, verbatim, or (b) a value already present on
intelligence_object (theme label, confidence band, momentum). This module
never asserts anything about the user's actual holdings or outcomes --
it doesn't have that data and doesn't pretend to. If the read itself is
paused (briefing_allowed False) or has fallen through with no theme,
reinterpret() returns a "withheld" result instead of forcing an opinion
onto an unreliable or missing read.

Reusable beyond Sentinel by design: reinterpret() takes any module's
intelligence_object (Sentinel today; Atlas/PE Intel later use the same
shared "profiles" table) plus the profile dict, and returns guidance --
no Sentinel-specific state, no import from story_generation.py or
main_api.py.
"""
from __future__ import annotations

MANDATE_TYPES        = ("equity", "balanced", "debt", "multi_asset")
RISK_TOLERANCES       = ("conservative", "moderate", "aggressive")
INVESTMENT_HORIZONS   = ("short", "medium", "long")

# Same defaults populateSidebar() in dashboard.html falls back to when a
# profile row has no value saved yet -- kept identical so guidance and the
# sidebar never silently disagree about what "no preference set" means.
_DEFAULTS = {
    "mandate_type":       "equity",
    "risk_tolerance":     "moderate",
    "investment_horizon": "medium",
    "client_profile":     "mid_career",
}

_MANDATE_LABEL = {
    "equity":      "an equity long-only mandate",
    "balanced":    "a balanced mandate",
    "debt":        "a debt-heavy mandate",
    "multi_asset": "a multi-asset mandate",
}
_RISK_LABEL = {
    "conservative": "conservative risk tolerance",
    "moderate":     "moderate risk tolerance",
    "aggressive":   "aggressive risk tolerance",
}
_HORIZON_CLAUSE = {
    "short":  "over the next few weeks, tactically",
    "medium": "over the next one to two quarters",
    "long":   "as a structural tilt, not a reaction to a single read",
}

# Regime -> baseline risk posture. Mirrors the direction already implied
# by every regime's HIGH-conviction guidance in dashboard.html's
# REGIME_PLAYBOOKS (LIQUIDITY_DRIVEN_EXPANSION says "invest confidently";
# MONETARY_TIGHTENING says "reduce to minimum weight") -- this names the
# same direction that already exists there, it isn't a new judgment call.
# TRANSITION_PHASE has no REGIME_PLAYBOOKS entry either (falls back to
# STABLE_GROWTH there) -- mirrored the same way here.
_REGIME_POSTURE = {
    "LIQUIDITY_DRIVEN_EXPANSION":            "RISK_ON",
    "EARLY_CYCLE_RECOVERY":                  "RISK_ON",
    "STABLE_GROWTH":                         "NEUTRAL",
    "TRANSITION_PHASE":                      "NEUTRAL",
    "MONETARY_TIGHTENING":                   "DEFENSIVE",
    "LIQUIDITY_TIGHTENING":                  "DEFENSIVE",
    "GROWTH_SLOWDOWN_SUPPORT":               "DEFENSIVE",
    "STAGFLATION_RISK":                      "DEFENSIVE",
    "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": "DEFENSIVE",
}

# posture -> mandate -> base action. Direction always follows the regime
# read; risk_tolerance (below) only scales how far to lean into it, it
# never flips the direction.
_MANDATE_ACTION = {
    "RISK_ON": {
        "equity":      "add to equity positions",
        "balanced":    "tilt the equity sleeve higher and extend duration modestly",
        "debt":        "extend duration to capture spread compression",
        "multi_asset": "increase risk-asset weight across equity and credit",
    },
    "NEUTRAL": {
        "equity":      "hold current equity weight rather than adding",
        "balanced":    "hold the mandate at its current midpoint",
        "debt":        "hold neutral duration",
        "multi_asset": "hold current allocation across all sleeves",
    },
    "DEFENSIVE": {
        "equity":      "reduce equity exposure and raise the cash buffer",
        "balanced":    "cut the equity sleeve and shorten duration",
        "debt":        "shorten duration and avoid credit risk",
        "multi_asset": "de-risk across equity and credit, and add gold as a hedge",
    },
}

# risk_tolerance -> how the base action's magnitude gets qualified. Only
# ever dampens or amplifies size/pace -- never reverses direction.
_RISK_QUALIFIER = {
    "RISK_ON": {
        "conservative": "but size any new exposure conservatively rather than moving all at once",
        "moderate":     "at a normal pace for the mandate",
        "aggressive":   "and size it toward the top of the mandate's normal range",
    },
    "NEUTRAL": {
        "conservative": "and keep the cash buffer on the higher side while the read is neutral",
        "moderate":     "without forcing a directional call either way",
        "aggressive":   "while looking for the next signal that would justify moving off neutral",
    },
    "DEFENSIVE": {
        "conservative": "fully, in line with a conservative risk tolerance",
        "moderate":     "at a normal pace for the mandate",
        "aggressive":   "but only partially -- an aggressive risk tolerance can tolerate carrying more of the existing position through this read",
    },
}

# client_profile affects tone only, never the substance of the action --
# see module docstring.
_TONE_PREFIX = {
    "young_accumulator": "",
    "mid_career":         "",
    "pre_retirement":     "With a capital-preservation posture in mind, ",
    "hni_preservation":   "With a capital-preservation posture in mind, ",
}


def _regime_key_from_theme_label(label: str) -> str:
    """Reverses intelligence_object's theme.label formatting
    (SNAKE_CASE -> Title Case) back to the original regime key. Safe
    because that formatting is a pure, reversible transform
    (regime_output.get("regime", "").replace("_", " ").title())."""
    return (label or "").upper().replace(" ", "_")


def reinterpret(intelligence_object: dict, profile: dict) -> dict:
    """
    intelligence_object: output of build_sentinel_intelligence_object()
    (or, per module docstring, any future module's equivalent build_*
    function using the same theme/confidence/momentum shape).
    profile: a row from the "profiles" Supabase table (or any dict with
    the same field names) -- mandate_type, risk_tolerance,
    investment_horizon, client_profile. Missing/unrecognised values fall
    back to the same defaults dashboard.html's sidebar uses.

    Returns:
      {"status": "ok", "so_what": str, "bucket": {...}, "basis": {...}}
      or
      {"status": "withheld", "reason": str}
        when the read itself isn't reliable enough to personalize
        (briefing_allowed False, or no theme/confidence present at all)
    """
    io = intelligence_object or {}
    confidence = io.get("confidence") or {}

    if not io.get("theme", {}).get("label") or confidence.get("score") is None:
        return {"status": "withheld", "reason": "no regime read to personalize"}

    if confidence.get("briefing_allowed", True) is False:
        return {"status": "withheld", "reason": "briefing paused for this run"}

    profile = profile or {}
    mandate  = profile.get("mandate_type")       or _DEFAULTS["mandate_type"]
    risk     = profile.get("risk_tolerance")     or _DEFAULTS["risk_tolerance"]
    horizon  = profile.get("investment_horizon") or _DEFAULTS["investment_horizon"]
    tone_key = profile.get("client_profile")     or _DEFAULTS["client_profile"]

    if mandate not in MANDATE_TYPES:
        mandate = _DEFAULTS["mandate_type"]
    if risk not in RISK_TOLERANCES:
        risk = _DEFAULTS["risk_tolerance"]
    if horizon not in INVESTMENT_HORIZONS:
        horizon = _DEFAULTS["investment_horizon"]

    regime_key = _regime_key_from_theme_label(io["theme"]["label"])
    posture    = _REGIME_POSTURE.get(regime_key, "NEUTRAL")
    band       = confidence.get("band", "LOW")

    action    = _MANDATE_ACTION[posture][mandate]
    qualifier = _RISK_QUALIFIER[posture][risk]
    horizon_clause = _HORIZON_CLAUSE[horizon]
    tone_prefix = _TONE_PREFIX.get(tone_key, "")

    given = "given" if tone_prefix else "Given"
    sentence = (
        f"{tone_prefix}{given} {_MANDATE_LABEL[mandate]} and {_RISK_LABEL[risk]}, "
        f"this read favors moving to {action}, {qualifier}, {horizon_clause}."
    )

    if band == "LOW":
        sentence += (
            " Confidence on this read is LOW -- treat this as a lean, "
            "not a firm call, and wait for it to firm up before acting on it in size."
        )

    return {
        "status":  "ok",
        "so_what": sentence,
        "bucket": {
            "mandate_type":       mandate,
            "risk_tolerance":     risk,
            "investment_horizon": horizon,
        },
        "basis": {
            "regime":           regime_key,
            "confidence_band":  band,
            "posture":          posture,
        },
    }
