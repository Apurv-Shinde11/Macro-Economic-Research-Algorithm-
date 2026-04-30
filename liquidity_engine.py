class LiquidityEngine:
    def __init__(self):
        """
        Institutional Liquidity Engine

        Tracks:
        - Policy stance (repo vs neutral)
        - Bond yields (cost of capital)
        - FX pressure (external liquidity)
        """

        # --- BASELINES (India-specific) ---
        self.repo_neutral = 5.5
        self.us10y_threshold = 4.25
        self.inr_depreciation_threshold = 83.0

    # =========================
    # 🧠 MAIN FUNCTION
    # =========================
    def analyze(self, intel_output, market_data):
        """
        Returns structured liquidity intelligence
        """

        hard = intel_output.get("hard_data", {})

        repo = float(hard.get("repo_rate", 5.25))
        inflation = float(hard.get("inflation", 5.0))

        us10y = float(market_data.get("bond_yield_proxy", 4.0))
        usd_inr = float(market_data.get("usd_inr", 83.0))

        signals = []

        # =========================
        # 🧠 POLICY LIQUIDITY
        # =========================
        if repo > self.repo_neutral:
            signals.append(("policy", -1, "Tight policy stance"))
        elif repo < self.repo_neutral:
            signals.append(("policy", +1, "Accommodative policy"))
        else:
            signals.append(("policy", 0, "Neutral policy"))

        # =========================
        # 🧠 BOND MARKET LIQUIDITY
        # =========================
        if us10y > self.us10y_threshold:
            signals.append(("global_liquidity", -1, "Rising global yields tightening liquidity"))
        else:
            signals.append(("global_liquidity", +0.5, "Stable global yields"))

        # =========================
        # 🧠 FX / EXTERNAL LIQUIDITY
        # =========================
        if usd_inr > self.inr_depreciation_threshold:
            signals.append(("fx", -1, "INR depreciation → imported tightening"))
        else:
            signals.append(("fx", +0.5, "Stable currency"))

        # =========================
        # 🧠 INFLATION FILTER (SECOND-ORDER)
        # =========================
        if inflation > 6:
            signals.append(("inflation", -0.5, "Inflation constrains easing"))
        elif inflation < 4:
            signals.append(("inflation", +0.5, "Room for easing"))

        # =========================
        # 🧠 AGGREGATION
        # =========================
        total_score = sum([s[1] for s in signals])
        normalized_score = max(min(total_score / len(signals), 1), -1)

        # =========================
        # 🧠 REGIME CLASSIFICATION
        # =========================
        if normalized_score > 0.3:
            regime = "LIQUIDITY_EXPANSION"
        elif normalized_score < -0.3:
            regime = "LIQUIDITY_TIGHTENING"
        else:
            regime = "LIQUIDITY_NEUTRAL"

        # =========================
        # 🧠 TREND DETECTION
        # (simple proxy — can upgrade later)
        # =========================
        if repo < self.repo_neutral and trending_down(inflation):
            trend = "IMPROVING"
        elif repo > self.repo_neutral:
            trend = "DETERIORATING"
        else:
            trend = "STABLE"

        # =========================
        # 🧠 MARKET INTERPRETATION
        # =========================
        implication = self._build_implication(regime, trend)

        # =========================
        # 🧠 CONFIDENCE
        # =========================
        confidence = min(0.9, 0.5 + abs(normalized_score))

        return {
            "liquidity_regime": regime,
            "liquidity_score": round(normalized_score, 2),
            "trend": trend,
            "signals": [
                {"type": s[0], "score": s[1], "comment": s[2]}
                for s in signals
            ],
            "market_implication": implication,
            "confidence": round(confidence, 2)
        }

    # =========================
    # 🧠 INTERPRETATION ENGINE
    # =========================
    def _build_implication(self, regime, trend):

        if regime == "LIQUIDITY_EXPANSION":
            return "Supportive for equities, high-beta, and cyclicals"

        elif regime == "LIQUIDITY_TIGHTENING":
            return "Pressure on valuations, favor defensives and cash"

        else:
            return "Mixed signals, range-bound market conditions"


# =========================
# ⚠️ HELPER (TEMP)
# =========================
def trending_down(value):
    """
    Placeholder for trend detection.
    Replace later with time-series logic.
    """
    return value < 5.5