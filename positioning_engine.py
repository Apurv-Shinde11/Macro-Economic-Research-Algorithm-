from utils import ensure_dict, safe_get, ensure_list, safe_float


REGIME_SECTOR_MAP = {
    "LIQUIDITY_DRIVEN_EXPANSION": {
        "Banks & BFSI":         "Overweight",
        "Infrastructure":       "Overweight",
        "Consumer Discretionary":"Overweight",
        "IT":                   "Neutral",
        "Pharma":               "Neutral",
        "FMCG":                 "Underweight",
        "Energy":               "Neutral"
    },
    "STABLE_GROWTH": {
        "Banks & BFSI":         "Overweight",
        "IT":                   "Overweight",
        "Consumer Discretionary":"Overweight",
        "Infrastructure":       "Neutral",
        "Pharma":               "Neutral",
        "FMCG":                 "Neutral",
        "Energy":               "Neutral"
    },
    "MONETARY_TIGHTENING": {
        "FMCG":                 "Overweight",
        "Pharma":               "Overweight",
        "IT":                   "Overweight",
        "Banks & BFSI":         "Underweight",
        "Infrastructure":       "Underweight",
        "Consumer Discretionary":"Underweight",
        "Energy":               "Neutral"
    },
    "LIQUIDITY_TIGHTENING": {
        "FMCG":                 "Overweight",
        "Pharma":               "Overweight",
        "IT":                   "Neutral",
        "Banks & BFSI":         "Underweight",
        "Infrastructure":       "Underweight",
        "Consumer Discretionary":"Underweight",
        "Energy":               "Underweight"
    },
    "EARLY_CYCLE_RECOVERY": {
        "Banks & BFSI":         "Overweight",
        "Real Estate":          "Overweight",
        "Autos":                "Overweight",
        "Infrastructure":       "Overweight",
        "FMCG":                 "Neutral",
        "IT":                   "Neutral",
        "Pharma":               "Neutral"
    },
    "GROWTH_SLOWDOWN_SUPPORT": {
        "Pharma":               "Overweight",
        "FMCG":                 "Overweight",
        "IT":                   "Neutral",
        "Banks & BFSI":         "Underweight",
        "Infrastructure":       "Underweight",
        "Consumer Discretionary":"Underweight",
        "Energy":               "Neutral"
    }
}

REGIME_ALLOCATION_MAP = {
    "LIQUIDITY_DRIVEN_EXPANSION":    {"equities": 0.65, "bonds": 0.15, "gold": 0.10, "cash": 0.10},
    "STABLE_GROWTH":                 {"equities": 0.60, "bonds": 0.20, "gold": 0.10, "cash": 0.10},
    "MONETARY_TIGHTENING":           {"equities": 0.35, "bonds": 0.25, "gold": 0.25, "cash": 0.15},
    "LIQUIDITY_TIGHTENING":          {"equities": 0.30, "bonds": 0.25, "gold": 0.25, "cash": 0.20},
    "EARLY_CYCLE_RECOVERY":          {"equities": 0.60, "bonds": 0.25, "gold": 0.08, "cash": 0.07},
    "GROWTH_SLOWDOWN_SUPPORT":       {"equities": 0.35, "bonds": 0.35, "gold": 0.20, "cash": 0.10},
    "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": {"equities": 0.30, "bonds": 0.20, "gold": 0.30, "cash": 0.20},
    "TRANSITION_PHASE":              {"equities": 0.45, "bonds": 0.25, "gold": 0.15, "cash": 0.15}
}

REGIME_STANCE_MAP = {
    "LIQUIDITY_DRIVEN_EXPANSION":    "Pro-Growth Risk-On",
    "STABLE_GROWTH":                 "Balanced Growth",
    "MONETARY_TIGHTENING":           "Defensive Risk-Off",
    "LIQUIDITY_TIGHTENING":          "Defensive — Capital Preservation",
    "EARLY_CYCLE_RECOVERY":          "Recovery — Rate Sensitive Overweight",
    "GROWTH_SLOWDOWN_SUPPORT":       "Cautious — Quality Bias",
    "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": "Inflation Hedge — Hard Assets",
    "TRANSITION_PHASE":              "Balanced — Await Regime Confirmation"
}


class PositioningEngine:
    def __init__(self):
        pass

    def _safe_float(self, val, default=0.0):
        try:
            return float(val)
        except Exception:
            return default

    def _conviction_score(self, regime_conf, dispersion, liquidity_score):
        boost = 0.1 if liquidity_score > 0.3 else (-0.1 if liquidity_score < -0.3 else 0)
        return round(max(0.4, min(regime_conf * (1 - dispersion) + boost, 0.95)), 2)

    def _get_sector_positioning(self, regime):
        sector_map = REGIME_SECTOR_MAP.get(regime, {})
        result = []
        for sector, stance in sector_map.items():
            result.append({
                "sector": sector,
                "stance": stance,
                "badge":  "OW" if stance == "Overweight" else ("UW" if stance == "Underweight" else "N")
            })
        return result

    def _get_tactical_actions(self, triggers, regime, dominant_scenario):
        actions = []
        safe_triggers = [t for t in ensure_list(triggers) if isinstance(t, dict)]
        top = sorted(safe_triggers, key=lambda x: safe_get(x, "priority", 0), reverse=True)[:5]

        for t in top:
            action    = safe_get(t, "action")
            condition = safe_get(t, "condition")
            if action and condition:
                actions.append({"action": action, "condition": f"If {condition}", "source": "trigger"})

        if "bearish" in str(dominant_scenario).lower():
            actions.append({"action": "Introduce options overlay for downside protection", "condition": "Tail risk hedge", "source": "scenario"})
        if "bullish" in str(dominant_scenario).lower():
            actions.append({"action": "Increase beta exposure selectively in leading sectors", "condition": "Upside capture", "source": "scenario"})
        if "TIGHTENING" in str(regime):
            actions.append({"action": "Reduce duration in fixed income portfolio", "condition": "Rate risk management", "source": "regime"})

        return actions

    def generate_positioning(self, regime_output, scenario_output, asset_output, cause_effect_output, triggers):

        regime_output       = ensure_dict(regime_output,       "Regime")
        scenario_output     = ensure_dict(scenario_output,     "Scenario")
        asset_output        = ensure_dict(asset_output,        "Asset")
        cause_effect_output = ensure_dict(cause_effect_output, "Cause")
        triggers            = ensure_list(triggers)

        regime       = safe_get(regime_output, "regime",     "TRANSITION_PHASE")
        regime_conf  = self._safe_float(safe_get(regime_output, "confidence", 0.6))
        reg_narrative= safe_get(regime_output, "narrative",  "")
        drivers      = ensure_list(safe_get(regime_output, "drivers", []))

        meta         = ensure_dict(safe_get(scenario_output, "meta", {}), "Meta")
        dispersion   = self._safe_float(safe_get(meta, "dispersion", 0.3))
        dom_scenario = safe_get(meta, "dominant_scenario", "")

        components   = ensure_dict(safe_get(regime_output, "components", {}), "Components")
        liquidity    = ensure_dict(safe_get(components,    "liquidity",   {}), "Liquidity")
        liq_score    = self._safe_float(safe_get(liquidity, "score", 0))

        # Core outputs
        stance       = REGIME_STANCE_MAP.get(regime, "Balanced — Await Regime Confirmation")
        base_alloc   = REGIME_ALLOCATION_MAP.get(regime, {"equities": 0.45, "bonds": 0.25, "gold": 0.15, "cash": 0.15})
        conviction   = self._conviction_score(regime_conf, dispersion, liq_score)

        # Scale allocation by conviction
        allocation = {k: round(v * conviction / sum(base_alloc.values()), 3) for k, v in base_alloc.items()}
        total = sum(allocation.values())
        allocation = {k: round(v / total, 3) for k, v in allocation.items()}

        sector_positioning = self._get_sector_positioning(regime)
        sector_bias        = [f"{s['stance']} {s['sector']}" for s in sector_positioning]
        tactical_actions   = self._get_tactical_actions(triggers, regime, dom_scenario)

        return {
            "stance":             stance,
            "allocation":         allocation,
            "sector_positioning": sector_positioning,
            "sector_bias":        sector_bias,
            "tactical_actions":   tactical_actions,
            "regime_narrative":   reg_narrative,
            "key_drivers":        drivers,
            "meta": {
                "conviction":         conviction,
                "regime":             regime,
                "liquidity_state":    safe_get(liquidity, "system_liquidity", "neutral"),
                "dominant_scenario":  dom_scenario,
                "dispersion":         dispersion
            }
        }