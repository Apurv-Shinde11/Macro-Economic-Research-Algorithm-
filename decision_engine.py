from utils import ensure_dict, safe_get, ensure_list, safe_float


class DecisionEngine:
    def __init__(self):

        # -------------------------
        # 📊 Regime → Base Allocation
        # -------------------------
        self.allocation_map = {
            "LIQUIDITY_DRIVEN_EXPANSION": {"equity": 70, "debt": 20, "gold": 5, "cash": 5},
            "EARLY_CYCLE_RECOVERY": {"equity": 65, "debt": 25, "gold": 5, "cash": 5},
            "STABLE_GROWTH": {"equity": 60, "debt": 30, "gold": 5, "cash": 5},
            "MONETARY_TIGHTENING": {"equity": 45, "debt": 40, "gold": 10, "cash": 5},
            "STAGFLATION_RISK": {"equity": 35, "debt": 40, "gold": 15, "cash": 10},
            "TRANSITION_PHASE": {"equity": 55, "debt": 30, "gold": 10, "cash": 5}
        }

        # -------------------------
        # 🏭 Sector Positioning
        # -------------------------
        self.sector_map = {
            "LIQUIDITY_DRIVEN_EXPANSION": ["Banks", "Infrastructure", "Consumer Discretionary"],
            "EARLY_CYCLE_RECOVERY": ["Banks", "Autos", "Real Estate"],
            "STABLE_GROWTH": ["Banks", "IT"],
            "MONETARY_TIGHTENING": ["FMCG", "Pharma"],
            "STAGFLATION_RISK": ["FMCG", "Pharma", "IT"],
            "TRANSITION_PHASE": []
        }

        # -------------------------
        # 📉 Scenario Drawdowns
        # -------------------------
        self.drawdown_map = {
            "Base": -5,
            "Growth Upside": 10,
            "External Shock / Inflation Risk": -20,
            "Tail": -25
        }

    # =========================
    # 📊 ALLOCATION ENGINE
    # =========================
    def _build_allocation(self, regime, confidence):
        base = self.allocation_map.get(
            regime,
            self.allocation_map["TRANSITION_PHASE"]
        ).copy()

        # Confidence adjustment
        if confidence < 0.5:
            base["cash"] += 5
            base["equity"] -= 5

        return base

    # =========================
    # 🏭 SECTOR BETS
    # =========================
    def _sector_bets(self, regime):
        return self.sector_map.get(regime, [])

    # =========================
    # ⚠️ RISK ENGINE
    # =========================
    def _risk_engine(self, scenarios):

        scenarios = ensure_list(scenarios)

        expected_drawdown = 0
        worst_case = -5

        for sc in scenarios:
            if not isinstance(sc, dict):
                continue

            name = safe_get(sc, "name", "")
            prob = safe_float(safe_get(sc, "probability", 0))

            drawdown = self.drawdown_map.get(name, -5)

            expected_drawdown += prob * drawdown
            worst_case = min(worst_case, drawdown)

        # Classification
        if expected_drawdown < -10:
            risk_level = "HIGH"
        elif expected_drawdown < -5:
            risk_level = "MODERATE"
        else:
            risk_level = "LOW"

        return {
            "expected_drawdown": round(expected_drawdown, 2),
            "worst_case": worst_case,
            "risk_level": risk_level
        }

    # =========================
    # ⚡ TACTICAL MOVES
    # =========================
    def _tactical_moves(self, triggers):

        triggers = ensure_list(triggers)
        moves = []

        for t in triggers:
            if not isinstance(t, dict):
                continue

            name = safe_get(t, "trigger", "").lower()

            if "liquidity" in name:
                moves.append({
                    "action": "Reduce equity exposure",
                    "reason": "Liquidity tightening signal"
                })

            elif "yield" in name or "us 10y" in name:
                moves.append({
                    "action": "Hedge via gold / defensives",
                    "reason": "Rising global yields"
                })

            elif "oil" in name:
                moves.append({
                    "action": "Shift to defensives",
                    "reason": "Oil-driven inflation risk"
                })

        return moves

    # =========================
    # 🧾 SUMMARY BUILDER
    # =========================
    def _build_summary(self, regime, allocation, risk):

        return (
            f"Regime: {regime.replace('_',' ')}. "
            f"Equity allocation set at {allocation.get('equity')}%. "
            f"Risk level is {risk.get('risk_level')} "
            f"with expected drawdown of {risk.get('expected_drawdown')}%."
        )

    # =========================
    # 🧠 MAIN ENGINE
    # =========================
    def generate(
        self,
        regime_output,
        scenario_output,
        asset_output,
        positioning_output,
        strategy_output,
        trigger_output
    ):
        # -------------------------
        # 🛡️ SAFE INPUTS
        # -------------------------
        regime_output = ensure_dict(regime_output)
        scenario_output = ensure_dict(scenario_output)
        positioning_output = ensure_dict(positioning_output)
        trigger_output = ensure_list(trigger_output)

        regime = safe_get(regime_output, "regime", "TRANSITION_PHASE")
        confidence = safe_float(safe_get(regime_output, "confidence", 0.5))

        scenarios = safe_get(scenario_output, "scenarios", [])

        # -------------------------
        # 📊 1. Allocation
        # -------------------------
        allocation = self._build_allocation(regime, confidence)

        # -------------------------
        # 🏭 2. Sector Bets
        # -------------------------
        sector_bets = self._sector_bets(regime)

        # -------------------------
        # ⚠️ 3. Risk
        # -------------------------
        risk = self._risk_engine(scenarios)

        # -------------------------
        # ⚡ 4. Tactical Moves
        # -------------------------
        tactical_moves = self._tactical_moves(trigger_output)

        # -------------------------
        # 🧾 5. Summary
        # -------------------------
        summary = self._build_summary(regime, allocation, risk)

        # -------------------------
        # 🎯 FINAL OUTPUT
        # -------------------------
        return {
            "allocation": allocation,
            "sector_bets": sector_bets,
            "risk": risk,
            "tactical_moves": tactical_moves,
            "summary": summary,
            "meta": {
                "regime": regime,
                "confidence": confidence
            }
        }