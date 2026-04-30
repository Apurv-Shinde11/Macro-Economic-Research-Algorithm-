from utils import ensure_dict, safe_get, ensure_list, safe_float


REGIME_PLAYBOOK = {
    "LIQUIDITY_DRIVEN_EXPANSION": [
        "Overweight cyclicals — Banks, Infra, Consumer Discretionary",
        "Increase mid-cap and small-cap allocation selectively",
        "Reduce cash drag — deploy into high-beta equity",
        "Favour credit instruments over government bonds",
        "Monitor FII flow data weekly — regime dependent on foreign participation",
        "Set stop-loss at liquidity reversal signals (RBI OMO, CRR hike)"
    ],
    "STABLE_GROWTH": [
        "Maintain pro-growth equity allocation across large and mid-caps",
        "Overweight Banks and IT — earnings visibility high",
        "Gradual SIP-style deployment — no need to time aggressively",
        "Bonds at neutral — no significant duration play",
        "Review quarterly — regime shift risk is low but monitor inflation"
    ],
    "MONETARY_TIGHTENING": [
        "Reduce equity exposure — prioritise quality over growth stocks",
        "Shift to short-duration bonds and T-bills",
        "Increase gold allocation as inflation and rate hedge",
        "Avoid leveraged positions and high-debt companies",
        "Defensives — FMCG, Pharma, IT — preferred over cyclicals",
        "Raise cash buffer to 15%+"
    ],
    "LIQUIDITY_TIGHTENING": [
        "Significant de-risking — reduce high-beta equity",
        "Move to capital preservation mode",
        "Short-duration debt and liquid funds preferred",
        "Gold as primary hedge against macro uncertainty",
        "Avoid NBFCs and highly leveraged balance sheets",
        "Wait for RBI liquidity signal reversal before re-entering risk"
    ],
    "EARLY_CYCLE_RECOVERY": [
        "Buy rate-sensitive sectors — Banks, Real Estate, Autos",
        "Increase duration in bond portfolio — rates heading lower",
        "Gradually reduce gold as safe haven demand fades",
        "Add mid-cap and small-cap exposure as credit conditions ease",
        "Position for earnings recovery in domestic cyclicals"
    ],
    "GROWTH_SLOWDOWN_SUPPORT": [
        "Shift to defensives — Pharma, FMCG, IT exports",
        "Increase bond allocation — flight to quality and rate cut potential",
        "Reduce cyclical exposure — Banks, Infra, Autos",
        "Gold as macro hedge",
        "Focus on high free-cash-flow businesses with strong balance sheets",
        "Avoid new equity deployment until growth signals stabilise"
    ],
    "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": [
        "Hard asset bias — gold, commodities, real assets",
        "Reduce INR-sensitive import-heavy sectors",
        "IT and Pharma (export earners) as currency hedge",
        "Short-duration bonds only — avoid long end",
        "Raise cash to 20%+ as volatility buffer",
        "Hedge FX exposure in international portfolios"
    ],
    "STAGFLATION_RISK": [
        "Hard asset overweight — gold is the primary holding",
        "Export earners only in equity — IT, Pharma, select commodities",
        "Avoid all domestic cyclicals and rate-sensitive names",
        "Maximum cash buffer — optionality over returns",
        "Short-duration, high-quality debt only",
        "Review portfolio weekly — regime is inherently unstable"
    ],
    "TRANSITION_PHASE": [
        "Adopt balanced barbell — quality growth + defensives",
        "Avoid concentrated sector bets until regime confirms",
        "Keep elevated cash — optionality is valuable",
        "Systematic deployment preferred over lump-sum",
        "Monitor RBI policy signals and FII flows as regime indicators"
    ]
}

# =========================
# 📐 CONVICTION THRESHOLDS
# Calibrated for Indian macro environment:
# — Dispersion is naturally high (3-scenario model)
# — Cap dispersion penalty at 0.45 to prevent
#   systematically low conviction in valid regimes
# =========================
CONVICTION_THRESHOLDS = {
    "HIGH":   0.52,   # score >= 0.52 → HIGH
    "MEDIUM": 0.38,   # score >= 0.38 → MEDIUM
                      # score <  0.38 → LOW
}
DISPERSION_CAP = 0.45


class StrategyEngine:
    def __init__(self):
        pass

    # =========================
    # 🧰 SAFE FLOAT
    # =========================
    def _safe_float(self, val, default=0.0):
        try:
            return float(val)
        except Exception:
            return default

    # =========================
    # 🎯 CONVICTION CLASSIFIER
    # Fixed: cap dispersion penalty so strong regimes
    # aren't unfairly penalised by wide scenario spreads
    # =========================
    def _conviction_label(self, confidence, dispersion):
        # Cap dispersion contribution — a 3-scenario model
        # naturally produces high dispersion (0.55-0.65)
        # even in strong regimes. Without capping, conviction
        # is permanently LOW regardless of regime clarity.
        capped_dispersion = min(dispersion, DISPERSION_CAP)
        score = confidence * (1 - capped_dispersion)

        if score >= CONVICTION_THRESHOLDS["HIGH"]:
            return "HIGH"
        elif score >= CONVICTION_THRESHOLDS["MEDIUM"]:
            return "MEDIUM"
        else:
            return "LOW"

    # =========================
    # 📊 CONVICTION SCORE (numeric)
    # Returns the raw score for display alongside the label
    # =========================
    def _conviction_score(self, confidence, dispersion):
        capped_dispersion = min(dispersion, DISPERSION_CAP)
        return round(confidence * (1 - capped_dispersion), 3)

    # =========================
    # ⏳ TIME HORIZON
    # =========================
    def _time_horizon(self, triggers):
        for t in ensure_list(triggers):
            if isinstance(t, dict) and safe_get(t, "horizon") == "short-term":
                return "Tactical (0–3 months)"
        return "Strategic (3–12 months)"

    # =========================
    # ⚖️ RISK FRAMEWORK
    # Conviction + regime-aware risk parameters
    # =========================
    def _risk_framework(self, conviction, regime):
        base = {
            "HIGH": {
                "drawdown_tolerance":  "Moderate (up to -12%)",
                "cash_buffer":         "Low (5–8%)",
                "hedging_intensity":   "Selective — options on tail risks only",
                "rebalance_frequency": "Quarterly"
            },
            "MEDIUM": {
                "drawdown_tolerance":  "Controlled (up to -8%)",
                "cash_buffer":         "Moderate (8–12%)",
                "hedging_intensity":   "Moderate — gold + short duration",
                "rebalance_frequency": "Monthly"
            },
            "LOW": {
                "drawdown_tolerance":  "Low (up to -5%)",
                "cash_buffer":         "High (15–20%)",
                "hedging_intensity":   "Aggressive — gold, T-bills, options overlay",
                "rebalance_frequency": "Bi-weekly"
            }
        }.get(conviction, {})

        # Regime-specific overlays
        if "TIGHTENING" in str(regime):
            base["additional_note"] = (
                "Tightening regime — prioritise capital preservation over returns"
            )
        elif "EXPANSION" in str(regime):
            base["additional_note"] = (
                "Expansion regime — accept moderate drawdown for participation"
            )
        elif "STAGFLATION" in str(regime):
            base["additional_note"] = (
                "Stagflation — no easy hedge; hard assets and cash are the only refuge"
            )
        elif "RECOVERY" in str(regime):
            base["additional_note"] = (
                "Early recovery — front-load rate-sensitive exposure before the move"
            )

        return base

    # =========================
    # 🧠 MAIN ENGINE
    # =========================
    def generate_strategy(
        self,
        regime_output,
        scenario_output,
        positioning_output,
        trigger_output
    ):
        # -------------------------
        # Safe inputs
        # -------------------------
        regime_output      = ensure_dict(regime_output,      "Regime")
        scenario_output    = ensure_dict(scenario_output,    "Scenario")
        positioning_output = ensure_dict(positioning_output, "Positioning")
        triggers           = ensure_list(trigger_output)

        # -------------------------
        # Regime fields
        # -------------------------
        regime     = safe_get(regime_output, "regime",    "TRANSITION_PHASE")
        confidence = self._safe_float(safe_get(regime_output, "confidence", 0.6))
        narrative  = safe_get(regime_output, "narrative",  "")
        drivers    = ensure_list(safe_get(regime_output,  "drivers", []))
        challenger = safe_get(regime_output, "challenger", "")

        # -------------------------
        # Scenario fields
        # -------------------------
        meta         = ensure_dict(safe_get(scenario_output, "meta", {}), "Meta")
        dispersion   = self._safe_float(safe_get(meta, "dispersion",        0.3))
        dom_scenario = safe_get(meta, "dominant_scenario",                  "")
        sc_confidence= self._safe_float(safe_get(meta, "confidence",        0.6))

        # -------------------------
        # Positioning fields
        # -------------------------
        stance       = safe_get(positioning_output, "stance",             "")
        allocation   = ensure_dict(
            safe_get(positioning_output, "allocation", {}), "Allocation"
        )
        sector_pos   = ensure_list(
            safe_get(positioning_output, "sector_positioning", [])
        )
        pos_meta     = ensure_dict(
            safe_get(positioning_output, "meta", {}), "Pos Meta"
        )
        pos_conviction = self._safe_float(safe_get(pos_meta, "conviction", 0.6))

        # -------------------------
        # Conviction + horizon
        # -------------------------
        conviction       = self._conviction_label(confidence, dispersion)
        conviction_raw   = self._conviction_score(confidence, dispersion)
        horizon          = self._time_horizon(triggers)
        risk_fw          = self._risk_framework(conviction, regime)

        # -------------------------
        # Playbook
        # -------------------------
        # Base playbook from regime map
        # STAGFLATION_RISK added — was missing from previous version
        playbook = list(
            REGIME_PLAYBOOK.get(regime, REGIME_PLAYBOOK["TRANSITION_PHASE"])
        )

        # Scenario-driven overlays
        dom_lower = str(dom_scenario).lower()
        if "bearish" in dom_lower or "shock" in dom_lower:
            playbook.append(
                "Tail risk hedge active — maintain options overlay or gold position"
            )
        if "bullish" in dom_lower or "upside" in dom_lower:
            playbook.append(
                "Upside scenario in play — consider adding beta on dips"
            )

        # Challenger regime awareness
        if challenger and challenger != regime:
            playbook.append(
                f"Monitor challenger regime ({challenger.replace('_',' ').title()}) "
                f"— rotate positioning if it gains dominance"
            )

        # -------------------------
        # Trigger-driven risk map
        # -------------------------
        risk_map = []
        safe_triggers = [t for t in triggers if isinstance(t, dict)]
        top_triggers  = sorted(
            safe_triggers,
            key=lambda x: safe_get(x, "priority", 0),
            reverse=True
        )[:5]

        for t in top_triggers:
            risk_map.append({
                "risk":      safe_get(t, "name",      ""),
                "condition": safe_get(t, "condition", ""),
                "response":  safe_get(t, "action",    ""),
                "priority":  safe_get(t, "priority",  0)
            })

        # -------------------------
        # Strategy type label
        # -------------------------
        strategy_type = (
            f"{conviction} Conviction — "
            f"{str(regime).replace('_', ' ').title()}"
        )

        return {
            "strategy_type":       strategy_type,
            "regime":              regime,
            "confidence":          confidence,
            "conviction":          conviction,
            "conviction_score":    conviction_raw,
            "time_horizon":        horizon,
            "portfolio_stance":    stance,
            "allocation_guidance": allocation,
            "sector_positioning":  sector_pos,
            "playbook":            playbook,
            "risk_framework":      risk_fw,
            "trigger_risk_map":    risk_map,
            "regime_narrative":    narrative,
            "key_drivers":         drivers,
            "meta": {
                "dominant_scenario":  dom_scenario,
                "dispersion":         dispersion,
                "conviction_score":   conviction_raw,
                "scenario_confidence":sc_confidence,
                "challenger":         challenger,
                "pos_conviction":     pos_conviction
            }
        }