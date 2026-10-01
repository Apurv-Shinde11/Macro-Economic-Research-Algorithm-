# =========================
# 📊 REGIME SCHEMA
# =========================
REGIME_SCHEMA = {
    "regime": str,
    "confidence": float,
    "components": dict,
    "external_sector": dict,
    "drivers": list,
    "inputs": dict
}

# =========================
# 🔮 SCENARIO SCHEMA
# =========================
SCENARIO_SCHEMA = {
    "scenarios": list,
    "meta": dict
}

# =========================
# 📊 ASSET SCHEMA
# =========================
ASSET_SCHEMA = {
    "assets": dict,
    "sectors": dict,
    "raw_scores": dict
}

# =========================
# 🎯 POSITIONING SCHEMA
# =========================
POSITIONING_SCHEMA = {
    "stance": str,
    "allocation": dict,
    "sector_bias": list,
    "tactical_actions": list,
    "meta": dict
}

# =========================
# 🧠 STRATEGY SCHEMA
# =========================
STRATEGY_SCHEMA = {
    "strategy_type": str,
    "confidence": float,
    "portfolio_stance": str,
    "allocation_guidance": dict,
    "sector_positioning": list,
    "playbook": list,
    "risk_framework": list,
    "meta": dict
}

# Canonical successful fresh-run payload. Unknown fields are allowed so new
# intelligence domains can be added without invalidating existing consumers.
ECONIQ_RUN_CONTRACT_VERSION = "1.0"
ECONIQ_RUN_RESULT_SCHEMA = {
    "required": {
        "regime": dict,
        "strategy": dict,
        "decision": dict,
        "scenarios": dict,
        "briefing_allowed": bool,
        "briefing_blocked_reason": (str, type(None)),
        "intelligence_object": (dict, type(None)),
        "story": dict,
        "guidance": dict,
        "contract_meta": dict,
    },
    "optional": {
        "positioning": dict,
        "triggers": list,
        "liquidity": dict,
        "intel": dict,
        "nse": dict,
        "macro": dict,
        "final_intel": dict,
        "report": str,
        "sector_heatmap": dict,
        "narrative_delta": dict,
        "regime_stability": dict,
        "transition": dict,
        "anticipatory": dict,
        "leading_intelligence": dict,
        "regime_is_unstable": bool,
        "challenger_delta": (int, float, type(None)),
    },
}