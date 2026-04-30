from utils import ensure_dict, safe_get, ensure_list, safe_float


class CauseEffectEngine:
    def __init__(self):
        pass

    # =========================
    # 🧱 NODE BUILDER
    # =========================
    def _build_node(self, driver, chain_steps, impact, pressure, lag, confidence, category):
        return {
            "driver": driver,
            "chain_steps": ensure_list(chain_steps),
            "impact": impact,
            "pressure": pressure,
            "lag": lag,
            "confidence": safe_float(confidence, 0.5),
            "category": category,
            "narrative": " → ".join(ensure_list(chain_steps) + [impact])
        }

    # =========================
    # 🎯 SCORE FUNCTION (UPGRADED)
    # =========================
    def _score(self, node, regime_strength=1.0):
        node = ensure_dict(node, "Node")

        pressure_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        lag_map = {"IMMEDIATE": 3, "FORWARD": 2, "LAGGED": 2, "STRUCTURAL": 1}

        pressure = pressure_map.get(safe_get(node, "pressure", "LOW"), 1)
        confidence = safe_float(safe_get(node, "confidence", 0.5))
        lag = lag_map.get(safe_get(node, "lag", "LAGGED"), 1)

        base_score = (
            pressure * 0.5 +
            confidence * 2 +
            lag * 0.3
        )

        return round(base_score * regime_strength, 2)

    # =========================
    # 🧠 MAIN ENGINE (UPGRADED)
    # =========================
    def analyze(self, data, regime_output, liquidity_output=None):

        # -------------------------
        # 🛡️ SAFE STRUCTURE
        # -------------------------
        data = ensure_dict(data, "Input Data")
        regime_output = ensure_dict(regime_output, "Regime Output")
        liquidity_output = ensure_dict(liquidity_output or {}, "Liquidity")

        macro = ensure_dict(safe_get(data, "macro", {}))
        market = ensure_dict(safe_get(data, "market", {}))

        regime = safe_get(regime_output, "regime", "")
        liquidity_regime = safe_get(liquidity_output, "liquidity_regime", "UNKNOWN")
        liquidity_score = safe_float(safe_get(liquidity_output, "liquidity_score", 0))

        # -------------------------
        # 📊 EXTRACT VARIABLES
        # -------------------------
        repo = safe_float(safe_get(macro, "repo_rate", 6.5))
        inflation = safe_float(safe_get(macro, "inflation", {}).get("headline", 5.0))
        growth = safe_float(safe_get(macro, "growth", {}).get("gdp", 6.5))

        usd_inr = safe_float(safe_get(market, "fx", {}).get("usd_inr", 0))
        crude = safe_float(safe_get(market, "commodities", {}).get("crude_oil", 0))

        chain = []

        # =========================
        # 🛢️ OIL + INR COMBINED SHOCK (NEW)
        # =========================
        if crude > 85 and usd_inr > 83:
            chain.append(self._build_node(
                "Oil + Currency Shock",
                [
                    "Import Bill Surge",
                    "INR Depreciation",
                    "Imported Inflation Spike",
                    "Policy Constraint"
                ],
                "Equity Selloff & Bond Yield Spike",
                "HIGH",
                "IMMEDIATE",
                0.9,
                "EXTERNAL"
            ))

        elif crude > 85:
            chain.append(self._build_node(
                "Rising Crude Oil",
                [
                    "Import Bill Increase",
                    "Imported Inflation Rise",
                    "RBI Tightening Bias"
                ],
                "Rate-Sensitive Pressure",
                "HIGH",
                "IMMEDIATE",
                0.85,
                "EXTERNAL"
            ))

        elif usd_inr > 83:
            chain.append(self._build_node(
                "Currency Weakness",
                [
                    "Imported Inflation",
                    "Foreign Outflows"
                ],
                "Market Volatility",
                "HIGH",
                "IMMEDIATE",
                0.8,
                "EXTERNAL"
            ))

        # =========================
        # 💧 LIQUIDITY ENGINE (NEW CORE LAYER)
        # =========================
        if liquidity_score > 0.3:
            chain.append(self._build_node(
                "Liquidity Expansion",
                [
                    "System Liquidity Surplus",
                    "Credit Growth Acceleration",
                    "Risk Appetite Increase"
                ],
                "Equity Multiple Expansion",
                "HIGH",
                "FORWARD",
                0.9,
                "LIQUIDITY"
            ))

        elif liquidity_score < -0.3:
            chain.append(self._build_node(
                "Liquidity Tightening",
                [
                    "Funding Stress",
                    "Credit Contraction"
                ],
                "Equity De-rating & Volatility",
                "HIGH",
                "IMMEDIATE",
                0.85,
                "LIQUIDITY"
            ))

        # =========================
        # 📊 INTEREST RATE
        # =========================
        if repo > 6.0:
            chain.append(self._build_node(
                "High Interest Rates",
                [
                    "Higher Cost of Capital",
                    "Liquidity Drain"
                ],
                "Valuation Compression",
                "HIGH",
                "IMMEDIATE",
                0.8,
                "POLICY"
            ))

        elif repo < 5.5:
            chain.append(self._build_node(
                "Low Interest Rates",
                [
                    "Cheap Credit",
                    "Liquidity Boost"
                ],
                "Risk Asset Support",
                "HIGH",
                "FORWARD",
                0.8,
                "POLICY"
            ))

        # =========================
        # 📈 GROWTH
        # =========================
        if growth > 6.5:
            chain.append(self._build_node(
                "Strong Growth",
                [
                    "Earnings Expansion",
                    "Capex Cycle Strength"
                ],
                "Cyclical Outperformance",
                "HIGH",
                "STRUCTURAL",
                0.8,
                "GROWTH"
            ))

        elif growth < 5.5:
            chain.append(self._build_node(
                "Growth Slowdown",
                [
                    "Earnings Pressure",
                    "Investment Decline"
                ],
                "Defensive Outperformance",
                "MEDIUM",
                "LAGGED",
                0.7,
                "GROWTH"
            ))

        # =========================
        # 🧭 REGIME AMPLIFICATION (UPGRADED)
        # =========================
        regime_strength = 1.0

        if "LIQUIDITY_DRIVEN" in regime:
            regime_strength = 1.2
        elif "TIGHTENING" in regime:
            regime_strength = 1.15
        elif "STABLE_GROWTH" in regime:
            regime_strength = 1.05

        # =========================
        # 🎯 DOMINANT DRIVER (SMARTER)
        # =========================
        dominant = None

        if chain:
            scored_chain = [
                (node, self._score(node, regime_strength))
                for node in chain
            ]
            dominant = max(scored_chain, key=lambda x: x[1])[0]

        # =========================
        # 📊 OUTPUT
        # =========================
        return {
            "chain": ensure_list(chain),
            "dominant_driver": ensure_dict(dominant, "Dominant"),
            "summary": safe_get(
                dominant,
                "narrative",
                "No dominant macro force detected"
            ),
            "meta": {
                "regime_strength": regime_strength,
                "liquidity_regime": liquidity_regime
            }
        }