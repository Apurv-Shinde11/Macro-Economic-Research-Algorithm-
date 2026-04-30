from utils import ensure_dict, safe_get, ensure_list

class AssetImpactEngine:
    def __init__(self):
        pass

    # =========================
    # 🎯 SCENARIO TYPE → ASSET MAP
    # =========================
    def _get_asset_map(self, scenario_type):

        return {
            "bullish": {
                "assets": {
                    "equities": 2,
                    "bonds": -1,
                    "gold": 0,
                    "cash": -1
                },
                "sectors": {
                    "banks": 2,
                    "infra": 2,
                    "auto": 1,
                    "it": 0,
                    "fmcg": -1
                }
            },

            "bearish": {
                "assets": {
                    "equities": -2,
                    "bonds": 1,
                    "gold": 2,
                    "cash": 2
                },
                "sectors": {
                    "banks": -2,
                    "infra": -1,
                    "auto": -2,
                    "it": 1,
                    "fmcg": 2
                }
            },

            "baseline": {
                "assets": {
                    "equities": 0,
                    "bonds": 0,
                    "gold": 0,
                    "cash": 0
                },
                "sectors": {
                    "banks": 0,
                    "infra": 0,
                    "auto": 0,
                    "it": 0,
                    "fmcg": 0
                }
            }
        }.get(scenario_type, {})

    # =========================
    # 🧠 LIQUIDITY MULTIPLIER
    # =========================
    def _liquidity_multiplier(self, liquidity_score):

        if liquidity_score > 0.5:
            return 1.5
        elif liquidity_score > 0.2:
            return 1.2
        elif liquidity_score < -0.5:
            return 1.5
        elif liquidity_score < -0.2:
            return 1.2
        else:
            return 1.0

    # =========================
    # 🧠 MAIN ENGINE (SAFE VERSION)
    # =========================
    def analyze_assets(self, regime_output, scenario_output, liquidity_output):

        # ✅ STEP 3: Enforce dict
        regime_output = ensure_dict(regime_output, "Regime Output")
        scenario_output = ensure_dict(scenario_output, "Scenario Output")
        liquidity_output = ensure_dict(liquidity_output, "Liquidity Output")

        # ✅ SAFE EXTRACTION
        scenarios = ensure_list(safe_get(scenario_output, "scenarios", []))

        liquidity_score = safe_get(liquidity_output, "liquidity_score", 0)
        liquidity_regime = safe_get(liquidity_output, "liquidity_regime", "NEUTRAL")

        multiplier = self._liquidity_multiplier(liquidity_score)

        # --- INITIALIZE ---
        asset_scores = {
            "equities": 0,
            "bonds": 0,
            "gold": 0,
            "cash": 0
        }

        sector_scores = {
            "banks": 0,
            "infra": 0,
            "auto": 0,
            "it": 0,
            "fmcg": 0
        }

        # =========================
        # 🔄 SCENARIO AGGREGATION
        # =========================
        for scenario in scenarios:

            scenario = ensure_dict(scenario, "Scenario Item")

            s_type = safe_get(scenario, "type", "baseline")
            prob = safe_get(scenario, "probability", 0)

            mapping = self._get_asset_map(s_type)

            for asset, score in mapping.get("assets", {}).items():
                asset_scores[asset] += score * prob * multiplier

            for sector, score in mapping.get("sectors", {}).items():
                sector_scores[sector] += score * prob * multiplier

        # =========================
        # 🧠 LIQUIDITY-SPECIFIC TILTS
        # =========================
        if liquidity_regime == "LIQUIDITY_EXPANSION":
            asset_scores["equities"] += 0.5
            sector_scores["banks"] += 0.5
            sector_scores["infra"] += 0.5

        elif liquidity_regime == "LIQUIDITY_TIGHTENING":
            asset_scores["equities"] -= 0.7
            asset_scores["cash"] += 0.5
            asset_scores["gold"] += 0.3

            sector_scores["banks"] -= 0.7
            sector_scores["auto"] -= 0.5
            sector_scores["fmcg"] += 0.4

        # =========================
        # 🛢️ INDIA MACRO ADJUSTMENTS
        # =========================
        external = ensure_dict(safe_get(regime_output, "external_sector", {}), "External Sector")

        crude = safe_get(external, "crude_oil", None)
        usd_inr = safe_get(external, "usd_inr", None)

        if crude and crude > 85:
            sector_scores["auto"] -= 0.5
            sector_scores["fmcg"] -= 0.3
            sector_scores["it"] += 0.3
            asset_scores["gold"] += 0.3

        if usd_inr and usd_inr > 83:
            sector_scores["it"] += 0.5
            sector_scores["banks"] -= 0.5
            asset_scores["gold"] += 0.2

        # =========================
        # 📊 NORMALIZATION
        # =========================
        for k in asset_scores:
            asset_scores[k] = round(asset_scores[k], 2)

        for k in sector_scores:
            sector_scores[k] = round(sector_scores[k], 2)

        # =========================
        # 📊 CONVERT TO VIEWS
        # =========================
        asset_views = {k: self._score_to_view(v) for k, v in asset_scores.items()}
        sector_views = {k: self._score_to_view(v) for k, v in sector_scores.items()}

        return {
            "assets": asset_views,
            "sectors": sector_views,
            "raw_scores": {
                "assets": asset_scores,
                "sectors": sector_scores
            },
            "liquidity_context": {
                "score": liquidity_score,
                "regime": liquidity_regime,
                "multiplier": multiplier
            }
        }

    # =========================
    # 🧠 INTERPRETATION
    # =========================
    def _score_to_view(self, score):

        if score >= 1.2:
            return "Strong Positive"
        elif score >= 0.4:
            return "Positive"
        elif score <= -1.2:
            return "Strong Negative"
        elif score <= -0.4:
            return "Negative"
        else:
            return "Neutral"