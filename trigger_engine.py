# trigger_engine.py

class TriggerEngine:
    def __init__(self):
        pass

    # =========================
    # 🎯 SCORING FUNCTION
    # =========================
    def _score_trigger(self, confidence, impact_weight):
        return round(confidence * impact_weight, 2)

    # =========================
    # 🎯 NORMALIZE PORTFOLIO IMPACT
    # =========================
    def _normalize_impact(self, impact):
        total = sum(abs(v) for v in impact.values())
        if total == 0:
            return impact
        return {k: round(v / total, 3) for k, v in impact.items()}

    # =========================
    # 🧠 MAIN ENGINE
    # =========================
    def generate_triggers(self, regime_output, cause_effect_output):

        regime = regime_output.get("regime", "")
        regime_conf = regime_output.get("confidence", 0.6)

        # --- External ---
        external = regime_output.get("external_sector", {})
        crude = external.get("crude_oil")
        usd_inr = external.get("usd_inr")

        # --- Liquidity ---
        liquidity = regime_output.get("components", {}).get("liquidity", {})
        liquidity_state = liquidity.get("system_liquidity", "neutral")
        liquidity_score = liquidity.get("liquidity_score", 0)

        # --- Inflation ---
        inflation = regime_output.get("components", {}).get("inflation", {}).get("level", 5)

        # --- Cause-effect ---
        dominant = cause_effect_output.get("dominant_driver", {})

        triggers = []

        # =========================
        # 🛢️ OIL SHOCK
        # =========================
        if crude and crude > 85:
            impact = {
                "equities": -0.15,
                "bonds": 0.05,
                "gold": 0.15,
                "cash": 0.05
            }

            triggers.append({
                "name": "Oil Shock Risk",
                "condition": "Crude Oil > 85",
                "category": "external",
                "signal_strength": "HIGH",
                "action": "Reduce cyclicals; Increase inflation hedges",
                "portfolio_impact": self._normalize_impact(impact),
                "probability": 0.7,
                "confidence": regime_conf,
                "horizon": "short-term",
                "priority": self._score_trigger(regime_conf, 3)
            })

        # =========================
        # 💱 INR WEAKNESS
        # =========================
        if usd_inr and usd_inr > 83:
            impact = {
                "equities": -0.05,
                "gold": 0.1,
                "cash": 0.05
            }

            triggers.append({
                "name": "Currency Weakness",
                "condition": "USD/INR > 83",
                "category": "external",
                "signal_strength": "MEDIUM",
                "action": "Overweight exporters; Hedge INR risk",
                "portfolio_impact": self._normalize_impact(impact),
                "probability": 0.65,
                "confidence": regime_conf,
                "horizon": "short-term",
                "priority": self._score_trigger(regime_conf, 3)
            })

        # =========================
        # 📊 INFLATION SHOCK
        # =========================
        if inflation > 6:
            impact = {
                "equities": -0.1,
                "bonds": -0.05,
                "gold": 0.15
            }

            triggers.append({
                "name": "Inflation Breach",
                "condition": "CPI > 6%",
                "category": "inflation",
                "signal_strength": "HIGH",
                "action": "Reduce duration; Shift to real assets",
                "portfolio_impact": self._normalize_impact(impact),
                "probability": 0.7,
                "confidence": regime_conf,
                "horizon": "short-term",
                "priority": self._score_trigger(regime_conf, 3)
            })

        # =========================
        # 💧 LIQUIDITY TIGHTENING
        # =========================
        if liquidity_state == "tight" or liquidity_score < -1:

            impact = {
                "equities": -0.15,
                "bonds": 0.1,
                "cash": 0.1
            }

            triggers.append({
                "name": "Liquidity Tightening",
                "condition": "System liquidity deteriorates",
                "category": "liquidity",
                "signal_strength": "HIGH",
                "action": "Reduce leverage; Move to defensives",
                "portfolio_impact": self._normalize_impact(impact),
                "probability": 0.7,
                "confidence": regime_conf,
                "horizon": "short-medium",
                "priority": self._score_trigger(regime_conf, 3)
            })

        # =========================
        # 💧 LIQUIDITY EXPANSION
        # =========================
        if liquidity_state == "abundant" or liquidity_score > 1:

            impact = {
                "equities": 0.15,
                "cash": -0.1
            }

            triggers.append({
                "name": "Liquidity Expansion",
                "condition": "System liquidity improves",
                "category": "liquidity",
                "signal_strength": "HIGH",
                "action": "Increase beta exposure; Add cyclicals",
                "portfolio_impact": self._normalize_impact(impact),
                "probability": 0.65,
                "confidence": regime_conf,
                "horizon": "short-medium",
                "priority": self._score_trigger(regime_conf, 2.8)
            })

        # =========================
        # 🌍 GLOBAL YIELD SHOCK
        # =========================
        triggers.append({
            "name": "Global Yield Shock",
            "condition": "US 10Y Yield spikes",
            "category": "global",
            "signal_strength": "MEDIUM",
            "action": "Expect FII outflows; Hedge equity risk",
            "portfolio_impact": {
                "equities": -0.1,
                "gold": 0.05
            },
            "probability": 0.55,
            "confidence": 0.6,
            "horizon": "short-term",
            "priority": self._score_trigger(0.6, 2.5)
        })

        # =========================
        # 🧠 DOMINANT DRIVER
        # =========================
        if dominant:

            triggers.append({
                "name": "Dominant Macro Force",
                "condition": dominant.get("driver", "Unknown"),
                "category": "meta",
                "signal_strength": "MEDIUM",
                "action": dominant.get("impact", "Monitor macro developments"),
                "portfolio_impact": {},  # optional (can enhance later)
                "probability": dominant.get("confidence", 0.6),
                "confidence": dominant.get("confidence", 0.6),
                "horizon": "medium-term",
                "priority": self._score_trigger(dominant.get("confidence", 0.6), 2.5)
            })

        # =========================
        # ⚠️ UNCERTAINTY REGIME
        # =========================
        if regime_conf < 0.55:

            impact = {
                "equities": -0.1,
                "cash": 0.15
            }

            triggers.append({
                "name": "High Uncertainty",
                "condition": "Macro signals diverge",
                "category": "risk",
                "signal_strength": "MEDIUM",
                "action": "Reduce position size; Increase cash buffer",
                "portfolio_impact": self._normalize_impact(impact),
                "probability": 0.5,
                "confidence": regime_conf,
                "horizon": "short-term",
                "priority": self._score_trigger(regime_conf, 2)
            })

        # =========================
        # 🏆 SORT BY PRIORITY
        # =========================
        triggers = sorted(triggers, key=lambda x: x["priority"], reverse=True)

        return triggers