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