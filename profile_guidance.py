"""
profile_guidance.py — deterministic, profile-aware SO WHAT layer.

Reinterprets the SAME grounded read every viewer sees (an
intelligence_object built by intelligence_object.py -- theme, confidence,
momentum, signals) into different concrete guidance depending on who is
reading it. Same opportunity, different fit per user, per the
"Opportunity Score vs User Fit" split in the original product design.

Deliberately NOT an LLM call. mandate_type (4) x risk_tolerance (3) x
investment_horizon (3) = 36 real, distinct guidance postures per module --
small enough to define as explicit, auditable rules, the same way
dashboard.html's REGIME_PLAYBOOKS / _getMandatePlays() already handles
regime x conviction x mandate_type for the (separate) Recommended
Playbook card. client_profile affects only phrasing tone, not the
substance of the guidance; aum_size and benchmark are not read here --
see the personalization design review for why those three axes were
excluded from the bucket key for v1.

Grounding: every claim this module makes is either (a) the profile's own
declared category, verbatim, or (b) a value already present on
intelligence_object. This module never asserts anything about the user's
actual holdings or outcomes -- it doesn't have that data and doesn't
pretend to. If the underlying read isn't reliable enough to personalize
(paused, or genuinely nothing to reason from), reinterpret() returns a
"withheld" result instead of forcing an opinion onto it.

Module-agnostic by design, extended per-module rather than rewritten:
reinterpret(io, profile) is the one public entry point regardless of
which module built `io`. What genuinely differs per module is (a) how a
directional POSTURE (RISK_ON / NEUTRAL / DEFENSIVE) gets derived from
that module's intelligence_object -- Sentinel and PE Intel both key off
a regime name via theme.label, since PE Intel's own intelligence_object
is itself a reshape of the same regime read; Atlas has no regime concept
at all, so its posture comes from averaging its own signals' scores
instead -- and (b) the action vocabulary for that posture, since "add to
equity positions" means nothing to a PE Intel or Atlas reader. Everything
else (bucket derivation from mandate/risk/horizon, defaults, sentence
assembly, withheld-handling) is shared, written once, in _MODULE_CONFIG
below. See the personalization design review for the reasoning behind
each module's posture derivation and action vocabulary.
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

# Describes the PROFILE itself, not any module's action -- shared across
# every module's sentence.
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
# client_profile affects tone only, never the substance of the action.
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


def _bucket_from_profile(profile: dict) -> dict:
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

    return {
        "mandate_type":       mandate,
        "risk_tolerance":     risk,
        "investment_horizon": horizon,
        "tone_key":           tone_key,
    }


# ══════════════════════════════════════════════════════════════════
# SENTINEL + PE INTEL — shared posture/gate/hedge logic.
#
# Both derive posture from a regime name via theme.label (PE Intel's
# own intelligence_object is itself a reshape of the same regime read
# Sentinel scores -- see build_pe_intelligence_object()), and both carry
# a real confidence score + briefing_allowed gate, so the withheld-check
# and LOW-confidence hedge are identical, not just similar.
# ══════════════════════════════════════════════════════════════════

# Regime -> baseline posture. Mirrors the direction already implied by
# every regime's HIGH-conviction guidance in dashboard.html's
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
    # PE Intel-only regime keys (COST_OF_CAPITAL in main_api.py has these
    # in addition to the 9 above) -- EXTERNAL_SHOCK reads the same as
    # STAGFLATION_RISK there (RISK-OFF, spiking spreads), so DEFENSIVE.
    "EXTERNAL_SHOCK":                        "DEFENSIVE",
    "STAGFLATIONARY_RISK":                   "DEFENSIVE",
}


def _regime_posture_fn(io: dict) -> str:
    regime_key = _regime_key_from_theme_label((io.get("theme") or {}).get("label"))
    return _REGIME_POSTURE.get(regime_key, "NEUTRAL")


def _confidence_gate_fn(io: dict):
    confidence = io.get("confidence") or {}
    if not (io.get("theme") or {}).get("label") or confidence.get("score") is None:
        return False, "no regime read to personalize"
    if confidence.get("briefing_allowed", True) is False:
        return False, "briefing paused for this run"
    return True, None


def _confidence_hedge_fn(io: dict) -> str | None:
    confidence = io.get("confidence") or {}
    if confidence.get("band") == "LOW":
        return (
            "Confidence on this read is LOW -- treat this as a lean, "
            "not a firm call, and wait for it to firm up before acting on it in size."
        )
    return None


def _confidence_basis_fn(io: dict, posture: str) -> dict:
    confidence = io.get("confidence") or {}
    return {
        "regime":          _regime_key_from_theme_label((io.get("theme") or {}).get("label")),
        "confidence_band": confidence.get("band", "LOW"),
        "posture":         posture,
    }


# posture -> mandate -> base action. Direction always follows the regime
# read; risk_tolerance only scales how far to lean into it, never
# reverses direction.
_SENTINEL_MANDATE_ACTION = {
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
_SENTINEL_RISK_QUALIFIER = {
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

# PE Intel: mandate_type describes a listed-markets construct that
# doesn't map literally onto a fund's actual decisions (deploy dry
# powder, adjust IRR targets, stress-test the existing portfolio, delay
# or accelerate exits) -- so it's read here as a proxy for how
# public-market-correlated the LP's overall book is: "equity" -> most
# sensitive to exit-window/IPO-pipeline commentary, "debt" -> most
# sensitive to cost-of-debt/credit-spread commentary, "balanced"/
# "multi_asset" -> blended. See the personalization design review.
_PE_MANDATE_ACTION = {
    "RISK_ON": {
        "equity":      "lean into new capital deployment -- public-market exit conditions are supportive for eventual monetisation",
        "balanced":    "deploy selectively while keeping an eye on both new capital calls and near-term exit timing",
        "debt":        "take advantage of compressing credit spreads to lock in favourable financing on new and existing deals",
        "multi_asset": "deploy across both new platform investments and add-on financing, with credit conditions supportive on both fronts",
    },
    "NEUTRAL": {
        "equity":      "hold current deployment pace -- exit conditions don't yet justify accelerating monetisation",
        "balanced":    "maintain a steady deployment pace without leaning hard into new capital calls or exits",
        "debt":        "hold current financing terms -- no strong case yet to renegotiate or extend leverage",
        "multi_asset": "maintain current pace across deployment and financing decisions",
    },
    "DEFENSIVE": {
        "equity":      "slow new deployment and delay non-urgent exits until public-market conditions improve",
        "balanced":    "preserve dry powder and prioritise supporting existing portfolio companies over new deployment",
        "debt":        "avoid new leveraged structures -- widening spreads make financing expensive right now",
        "multi_asset": "preserve dry powder across the board and stress-test existing portfolio financing",
    },
}
_PE_RISK_QUALIFIER = {
    "RISK_ON": {
        "conservative": "but size new commitments conservatively rather than committing the full allocation at once",
        "moderate":     "at a normal pace for the fund's stated strategy",
        "aggressive":   "and size commitments toward the top of the fund's normal deployment range",
    },
    "NEUTRAL": {
        "conservative": "and keep dry powder on the higher side while conditions stay neutral",
        "moderate":     "without forcing a directional call either way",
        "aggressive":   "while watching closely for the next signal that would justify accelerating",
    },
    "DEFENSIVE": {
        "conservative": "fully, in line with a conservative risk tolerance",
        "moderate":     "at a normal pace for the fund's stated strategy",
        "aggressive":   "but only partially -- an aggressive risk tolerance can tolerate carrying more exposure through this environment",
    },
}


# ══════════════════════════════════════════════════════════════════
# ATLAS — no regime, no confidence score. Posture comes from averaging
# Atlas's own scored signals; the hedge is about signal coverage, not
# confidence, since Atlas has no confidence concept to hedge with.
# ══════════════════════════════════════════════════════════════════

# The 4 Atlas signals that carry a real 0/0.5/1 score (currency/yield are
# always NEUTRAL/0.5 today -- see build_atlas_intelligence_object -- so
# including them would silently drag every read toward NEUTRAL).
_ATLAS_SCORED_SIGNAL_IDS = ("gdp_growth", "pmi", "inflation", "policy_rate")


def _atlas_posture_fn(io: dict) -> str | None:
    scores = [
        s.get("score") for s in (io.get("signals") or [])
        if s.get("id") in _ATLAS_SCORED_SIGNAL_IDS and s.get("score") is not None
    ]
    if not scores:
        return None
    avg = sum(scores) / len(scores)
    if avg >= 0.66:
        return "RISK_ON"
    if avg <= 0.33:
        return "DEFENSIVE"
    return "NEUTRAL"


def _atlas_gate_fn(io: dict):
    if _atlas_posture_fn(io) is None:
        return False, "no scored signals to personalize"
    return True, None


def _atlas_hedge_fn(io: dict) -> str | None:
    covered = sum(
        1 for s in (io.get("signals") or [])
        if s.get("id") in _ATLAS_SCORED_SIGNAL_IDS and s.get("score") is not None
    )
    if covered < 3:
        return (
            f"This is based on partial signal coverage ({covered} of "
            f"{len(_ATLAS_SCORED_SIGNAL_IDS)} indicators available today) -- "
            "treat it as directional, not complete."
        )
    return None


def _atlas_basis_fn(io: dict, posture: str) -> dict:
    covered = sum(
        1 for s in (io.get("signals") or [])
        if s.get("id") in _ATLAS_SCORED_SIGNAL_IDS and s.get("score") is not None
    )
    return {
        "signal_coverage": f"{covered}/{len(_ATLAS_SCORED_SIGNAL_IDS)}",
        "posture":         posture,
    }


# Atlas isn't "what should I do right now" the way Sentinel's regime
# read is -- it's India's standalone macro backdrop, so the action
# vocabulary is framed as an India-exposure tilt, not an immediate
# trade. See the personalization design review.
_ATLAS_MANDATE_ACTION = {
    "RISK_ON": {
        "equity":      "lean toward maintaining or building India equity exposure -- growth and inflation are currently reading supportive together",
        "balanced":    "keep India within its normal weight in the equity sleeve, with no need to trim on the current backdrop",
        "debt":        "hold or extend India duration -- the inflation/policy backdrop doesn't argue for shortening",
        "multi_asset": "keep India exposure at or above its structural weight across sleeves",
    },
    "NEUTRAL": {
        "equity":      "hold India exposure at its current weight -- the backdrop doesn't argue for adding or trimming",
        "balanced":    "make no change to India's equity weight on this backdrop alone",
        "debt":        "hold neutral India duration -- nothing here argues for a shift either way",
        "multi_asset": "hold current India weight across sleeves",
    },
    "DEFENSIVE": {
        "equity":      "avoid adding to India equity exposure until growth or inflation signals improve",
        "balanced":    "trim India's equity weight modestly rather than adding into a stressed backdrop",
        "debt":        "shorten India duration -- the inflation/policy backdrop argues for caution",
        "multi_asset": "trim India exposure across sleeves and lean on hedges rather than adding",
    },
}
_ATLAS_RISK_QUALIFIER = {
    "RISK_ON": {
        "conservative": "though keep any addition modest given a conservative risk tolerance",
        "moderate":     "at a normal pace",
        "aggressive":   "and lean into it -- an aggressive risk tolerance can size this tilt up",
    },
    "NEUTRAL": {
        "conservative": "and keep India exposure on the lighter side while the backdrop stays neutral",
        "moderate":     "without forcing a directional call either way",
        "aggressive":   "while watching for the next signal that would justify a real tilt",
    },
    "DEFENSIVE": {
        "conservative": "fully, in line with a conservative risk tolerance",
        "moderate":     "at a normal pace",
        "aggressive":   "but only partially -- an aggressive risk tolerance can tolerate carrying more India exposure through this backdrop",
    },
}


# ══════════════════════════════════════════════════════════════════
# DISPATCH — one entry per module. reinterpret() below never branches
# on module name directly; it only ever reads from this table.
# ══════════════════════════════════════════════════════════════════
_MODULE_CONFIG = {
    "sentinel": {
        "read_description": "this read",
        "posture_fn":        _regime_posture_fn,
        "gate_fn":           _confidence_gate_fn,
        "hedge_fn":          _confidence_hedge_fn,
        "basis_fn":          _confidence_basis_fn,
        "mandate_action":    _SENTINEL_MANDATE_ACTION,
        "risk_qualifier":    _SENTINEL_RISK_QUALIFIER,
    },
    "pe": {
        "read_description": "the current cost-of-capital read",
        "posture_fn":        _regime_posture_fn,
        "gate_fn":           _confidence_gate_fn,
        "hedge_fn":          _confidence_hedge_fn,
        "basis_fn":          _confidence_basis_fn,
        "mandate_action":    _PE_MANDATE_ACTION,
        "risk_qualifier":    _PE_RISK_QUALIFIER,
    },
    "atlas": {
        "read_description": "India's current growth/inflation/policy backdrop",
        "posture_fn":        _atlas_posture_fn,
        "gate_fn":           _atlas_gate_fn,
        "hedge_fn":          _atlas_hedge_fn,
        "basis_fn":          _atlas_basis_fn,
        "mandate_action":    _ATLAS_MANDATE_ACTION,
        "risk_qualifier":    _ATLAS_RISK_QUALIFIER,
    },
}


def reinterpret(intelligence_object: dict, profile: dict) -> dict:
    """
    intelligence_object: output of build_sentinel_intelligence_object(),
    build_pe_intelligence_object(), or build_atlas_intelligence_object()
    -- dispatch is keyed on its "module" field, so any future module's
    build_* function works here as long as it sets "module" and is
    registered in _MODULE_CONFIG above.
    profile: a row from the "profiles" Supabase table (or any dict with
    the same field names) -- mandate_type, risk_tolerance,
    investment_horizon, client_profile. Missing/unrecognised values fall
    back to the same defaults dashboard.html's sidebar uses.

    Returns:
      {"status": "ok", "so_what": str, "bucket": {...}, "basis": {...}}
      or
      {"status": "withheld", "reason": str}
        when the read itself isn't reliable enough to personalize, or
        intelligence_object's module has no registered guidance template
    """
    io = intelligence_object or {}
    config = _MODULE_CONFIG.get(io.get("module"))
    if config is None:
        return {"status": "withheld", "reason": f"no guidance template for module {io.get('module')!r}"}

    ok, reason = config["gate_fn"](io)
    if not ok:
        return {"status": "withheld", "reason": reason}

    bucket = _bucket_from_profile(profile)
    posture = config["posture_fn"](io)

    action    = config["mandate_action"][posture][bucket["mandate_type"]]
    qualifier = config["risk_qualifier"][posture][bucket["risk_tolerance"]]
    horizon_clause = _HORIZON_CLAUSE[bucket["investment_horizon"]]
    tone_prefix = _TONE_PREFIX.get(bucket["tone_key"], "")

    given = "given" if tone_prefix else "Given"
    sentence = (
        f"{tone_prefix}{given} {_MANDATE_LABEL[bucket['mandate_type']]} and {_RISK_LABEL[bucket['risk_tolerance']]}, "
        f"{config['read_description']} favors moving to {action}, {qualifier}, {horizon_clause}."
    )

    hedge = config["hedge_fn"](io)
    if hedge:
        sentence += " " + hedge

    return {
        "status":  "ok",
        "so_what": sentence,
        "bucket": {
            "mandate_type":       bucket["mandate_type"],
            "risk_tolerance":     bucket["risk_tolerance"],
            "investment_horizon": bucket["investment_horizon"],
        },
        "basis": config["basis_fn"](io, posture),
    }
