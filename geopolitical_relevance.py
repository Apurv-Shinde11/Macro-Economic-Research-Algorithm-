"""
geopolitical_relevance.py — profile-aware RANKING of Geopolitical Watch
themes, not new instruction text.

Geopolitical Watch has no portfolio-instruction content to personalize --
it's 10 fixed thematic cards (status + summary + India linkage), same
content for every viewer, refreshed on a 72h cache per theme
(main_api.py's _get_theme_cached()). Forcing profile_guidance.py's
SO WHAT pattern onto this would mean inventing text this module has no
basis for. What's real and available instead: relevance. The same 10
cards, same content, reordered by how relevant each theme is likely to
be to THIS profile -- not new text generation, so grounding is a
non-issue by construction, same reasoning as profile_guidance.py.

The one piece of real, already-grounded data this reuses:
india_transmission_channel (TRADE / CURRENCY / ENERGY / CAPITAL_FLOWS /
SUPPLY_CHAIN / MULTIPLE) -- a field the theme-analysis LLM call already
produces from real headlines (see main_api.py's _generate_theme_analysis
prompt). Ranking against it isn't inventing a new judgment, it's reusing
one that's already there.

Scoring: relevance_score = channel_weight(mandate_type, channel) x
urgency_weight(status, investment_horizon).
  - channel_weight: which transmission channels matter most to a given
    mandate (equity mandates lean on CAPITAL_FLOWS/CURRENCY/TRADE --
    FII-flow and export sensitivity; debt mandates lean on
    CURRENCY/ENERGY -- the imported-inflation-to-rate-policy channel;
    balanced/multi_asset stay close to flat with a mild lift on
    MULTIPLE).
  - urgency_weight: how much today's status (ESCALATING / STABLE /
    DE_ESCALATING) should move the ranking, scaled by investment_horizon
    -- a short-horizon viewer cares a lot that something is escalating
    right now; a long-horizon viewer shouldn't have a structurally
    important theme (critical minerals, fragmentation/decoupling) drop
    just because this week's status happens to read STABLE.

risk_tolerance is deliberately NOT used here -- no defensible,
non-arbitrary reason "conservative vs aggressive" should change which
geopolitical theme is more relevant, unlike mandate_type/horizon which
have a real causal story. See the personalization design review.

Separate file from profile_guidance.py on purpose: the input/output
shape here (a list of fixed theme cards) has nothing to do with an
intelligence_object, so bundling it into that file would blur what that
file is for.
"""
from __future__ import annotations

# Same defaults dashboard.html's sidebar falls back to -- kept identical
# so ranking and the sidebar never silently disagree about what "no
# preference set" means. See profile_guidance.py's own _DEFAULTS.
_DEFAULTS = {
    "mandate_type":       "equity",
    "investment_horizon": "medium",
}

_TRANSMISSION_CHANNELS = (
    "TRADE", "CURRENCY", "ENERGY", "CAPITAL_FLOWS", "SUPPLY_CHAIN", "MULTIPLE",
)

# mandate_type -> transmission channel -> weight. 1.0 is neutral; above
# 1.0 means this channel matters more to this mandate, below means less.
_CHANNEL_WEIGHT = {
    "equity": {
        "CAPITAL_FLOWS": 1.5, "CURRENCY": 1.3, "TRADE": 1.2,
        "ENERGY": 1.0, "SUPPLY_CHAIN": 1.0, "MULTIPLE": 1.2,
    },
    "debt": {
        "CURRENCY": 1.5, "ENERGY": 1.3, "TRADE": 1.0,
        "CAPITAL_FLOWS": 1.1, "SUPPLY_CHAIN": 1.0, "MULTIPLE": 1.1,
    },
    "balanced": {
        "CAPITAL_FLOWS": 1.1, "CURRENCY": 1.1, "TRADE": 1.05,
        "ENERGY": 1.05, "SUPPLY_CHAIN": 1.0, "MULTIPLE": 1.2,
    },
    "multi_asset": {
        "CAPITAL_FLOWS": 1.15, "CURRENCY": 1.15, "TRADE": 1.1,
        "ENERGY": 1.1, "SUPPLY_CHAIN": 1.1, "MULTIPLE": 1.25,
    },
}

# status -> base urgency multiplier before horizon scaling.
_STATUS_BASE = {
    "ESCALATING":    1.5,
    "STABLE":        1.0,
    "DE_ESCALATING": 0.7,
}

# investment_horizon -> how much today's status should move the ranking.
# 1.0 = full effect (short horizon cares a lot this is escalating right
# now); 0.0 would mean status doesn't matter at all (not used -- even a
# long-horizon viewer should see a small nudge).
_HORIZON_FACTOR = {
    "short":  1.0,
    "medium": 0.6,
    "long":   0.25,
}


def _relevance_score(theme: dict, mandate: str, horizon: str) -> float:
    channel = theme.get("india_transmission_channel") or "MULTIPLE"
    if channel not in _TRANSMISSION_CHANNELS:
        channel = "MULTIPLE"
    channel_weight = _CHANNEL_WEIGHT[mandate].get(channel, 1.0)

    status = theme.get("status") or "STABLE"
    status_base = _STATUS_BASE.get(status, 1.0)
    horizon_factor = _HORIZON_FACTOR[horizon]
    urgency_weight = 1.0 + (status_base - 1.0) * horizon_factor

    return channel_weight * urgency_weight


def rank_geopolitical_themes(themes: list[dict], profile: dict, top_n: int = 3) -> list[dict]:
    """
    themes: the list _get_theme_cached() produces, one per
    GEOPOLITICAL_THEMES entry (theme_key, theme_label, status,
    status_confidence, headline_summary, context,
    india_transmission_channel, india_linkage, watch_for, ...).
    profile: a row from the "profiles" Supabase table (or any dict with
    mandate_type/investment_horizon) -- missing/unrecognised values fall
    back to the same defaults dashboard.html's sidebar uses.

    Returns the SAME theme dicts, same content, reordered by relevance
    descending, each with a "relevance_rank" (1-indexed position after
    reordering) and "relevant_to_profile" (True for the top `top_n`)
    added -- no theme content is changed or generated.
    """
    profile = profile or {}
    mandate = profile.get("mandate_type") or _DEFAULTS["mandate_type"]
    horizon = profile.get("investment_horizon") or _DEFAULTS["investment_horizon"]
    if mandate not in _CHANNEL_WEIGHT:
        mandate = _DEFAULTS["mandate_type"]
    if horizon not in _HORIZON_FACTOR:
        horizon = _DEFAULTS["investment_horizon"]

    scored = [
        (t, _relevance_score(t, mandate, horizon))
        for t in (themes or [])
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    ranked = []
    for i, (theme, score) in enumerate(scored):
        ranked.append({
            **theme,
            "relevance_score":    round(score, 4),
            "relevance_rank":     i + 1,
            "relevant_to_profile": i < top_n,
        })
    return ranked
