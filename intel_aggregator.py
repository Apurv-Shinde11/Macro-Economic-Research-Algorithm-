import datetime
from utils import ensure_dict, safe_get, ensure_list


class IntelAggregator:
    def __init__(self):
        pass

    # =========================
    # 🛡️ VALIDATION LAYER
    # =========================
    def _validate(self, data, name):
        if not data or not isinstance(data, dict):
            return {"error": f"{name} missing or invalid"}
        return data

    # =========================
    # 🧠 META SUMMARY
    # Flattened summary for quick UI access and downstream consumption
    # =========================
    def _build_meta(
        self,
        regime_output,
        scenario_output,
        positioning_output,
        strategy_output=None,
        decision_output=None
    ):
        # Core regime + scenario meta
        meta = {
            "regime":              regime_output.get("regime",     "UNKNOWN"),
            "regime_confidence":   regime_output.get("confidence", 0.0),
            "regime_narrative":    regime_output.get("narrative",  ""),
            "dominant_scenario":   scenario_output.get("meta", {}).get("dominant_scenario"),
            "scenario_dispersion": scenario_output.get("meta", {}).get("dispersion"),
            "scenario_confidence": scenario_output.get("meta", {}).get("confidence"),
        }

        # Positioning meta
        pos_meta = positioning_output.get("meta", {})
        meta.update({
            "stance":           positioning_output.get("stance",     ""),
            "conviction":       pos_meta.get("conviction",           0.0),
            "liquidity_state":  pos_meta.get("liquidity_state",      "neutral"),
            "equity_bias":      regime_output.get("components", {})
                                            .get("equity_bias",      "NEUTRAL"),
            "rbi_signal":       regime_output.get("components", {})
                                            .get("rbi_signal",       "UNKNOWN"),
        })

        # Strategy meta
        if isinstance(strategy_output, dict) and strategy_output:
            meta.update({
                "strategy_type":    strategy_output.get("strategy_type",    ""),
                "time_horizon":     strategy_output.get("time_horizon",     ""),
                "conviction_label": strategy_output.get("conviction",       ""),
                "portfolio_stance": strategy_output.get("portfolio_stance", "")
            })

        # Decision meta — ✅ uses correct DecisionEngine output keys
        if isinstance(decision_output, dict) and decision_output:
            risk = decision_output.get("risk", {})
            meta.update({
                "decision_risk_level":      risk.get("risk_level",       ""),
                "expected_drawdown":        risk.get("expected_drawdown", 0.0),
                "worst_case_drawdown":      risk.get("worst_case",        0.0),
                "decision_equity_alloc":    decision_output.get(
                    "allocation", {}
                ).get("equity", 0),
                "decision_summary":         decision_output.get("summary", "")
            })

        return meta

    # =========================
    # 📊 NLP INTELLIGENCE SUMMARY
    # Pulls LLM-enriched fields into a clean top-level block
    # =========================
    def _build_nlp_summary(self, regime_output):
        nlp = regime_output.get("nlp_intelligence", {})
        if not nlp:
            return {}

        return {
            "dominant_theme":        nlp.get("dominant_theme",  ""),
            "key_signals":           nlp.get("key_signals",     []),
            "india_risks":           nlp.get("india_risks",     []),
            "global_factors":        nlp.get("global_factors",  []),
            "reasoning":             nlp.get("reasoning",       ""),
            "nlp_confidence":        nlp.get("nlp_confidence",  0.0),
            "nlp_source":            nlp.get("source",          "keyword"),
            "provider":              nlp.get("provider",        "none")
        }

    # =========================
    # 🌍 EXTERNAL SECTOR SUMMARY
    # =========================
    def _build_external_summary(self, regime_output):
        ext = regime_output.get("external_sector", {})
        return {
            "risk_flags":    ext.get("risk_flags",    ["stable"]),
            "india_risks":   ext.get("india_risks",   []),
            "global_factors":ext.get("global_factors",[])
        }

    # =========================
    # 📈 REGIME SCORES SNAPSHOT
    # Preserves soft scoring for audit trail
    # =========================
    def _build_regime_scores(self, regime_output):
        return {
            "scores":     regime_output.get("regime_scores", {}),
            "challenger": regime_output.get("challenger",    ""),
            "drivers":    regime_output.get("drivers",       []),
            "inputs":     regime_output.get("inputs",        {})
        }

    # =========================
    # 🧠 MAIN AGGREGATOR
    # Single source of truth for all downstream consumers
    # (ReportGenerator, main.py display, future API layer)
    # =========================
    def build_intel_packet(
        self,
        regime_output,
        scenario_output,
        asset_output,
        triggers,
        positioning_output,
        cause_effect_output = None,
        decision_output     = None,
        strategy_output     = None
    ):
        """
        Builds the final intelligence packet.

        All engines feed in here — output is the single source of truth
        consumed by ReportGenerator, main.py display, and any future API.

        Parameters
        ----------
        regime_output       : MacroRegimeEngine output
        scenario_output     : ScenarioEngine output
        asset_output        : AssetImpactEngine output (assets dict, not full output)
        triggers            : TriggerEngine output (list)
        positioning_output  : PositioningEngine output
        cause_effect_output : CauseEffectEngine output (optional)
        decision_output     : DecisionEngine output (optional)
        strategy_output     : StrategyEngine output (optional)
        """

        # =========================
        # 🛡️ INPUT VALIDATION
        # =========================
        regime_output      = self._validate(regime_output,      "macro_regime")
        scenario_output    = self._validate(scenario_output,    "scenarios")
        positioning_output = self._validate(positioning_output, "positioning")

        # asset_output may arrive as the raw engine output or pre-extracted dict
        if isinstance(asset_output, dict) and "assets" in asset_output:
            asset_dict = asset_output.get("assets", {})
        else:
            asset_dict = asset_output if isinstance(asset_output, dict) else {}

        triggers            = ensure_list(triggers)
        cause_effect_output = ensure_dict(cause_effect_output) if cause_effect_output else None
        decision_output     = ensure_dict(decision_output)     if decision_output     else None
        strategy_output     = ensure_dict(strategy_output)     if strategy_output     else None

        # =========================
        # 🧠 CORE PACKET
        # =========================
        intel_packet = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

            # Full engine outputs preserved for audit / report generation
            "macro_regime":       regime_output,
            "scenarios":          scenario_output,
            "positioning":        positioning_output,
            "risk_triggers":      triggers,

            # Asset data — both raw and flattened
            "asset_implications": asset_dict,
            "assets":             asset_dict,
            "sectors":            regime_output.get("components", {})
        }

        # =========================
        # ➕ OPTIONAL LAYERS
        # =========================

        # Cause-effect chain
        if cause_effect_output:
            intel_packet["cause_effect"] = cause_effect_output

        # Strategy output
        if strategy_output:
            intel_packet["strategy"] = strategy_output
            intel_packet["playbook"] = strategy_output.get("playbook", [])

        # Decision layer — ✅ correct DecisionEngine key names throughout
        if decision_output:
            intel_packet["decision"] = decision_output

            # Flatten key outputs for fast UI access
            intel_packet["decision_allocation"] = decision_output.get(
                "allocation",      {}
            )
            intel_packet["decision_sector_bets"]= decision_output.get(
                "sector_bets",     []
            )
            intel_packet["decision_risk"]        = decision_output.get(
                "risk",            {}
            )
            intel_packet["decision_tactical"]    = decision_output.get(
                "tactical_moves",  []
            )
            intel_packet["decision_summary"]     = decision_output.get(
                "summary",         ""
            )

        # =========================
        # 📊 ENRICHED SUMMARY BLOCKS
        # Pre-built views consumed by ReportGenerator and main.py
        # =========================
        intel_packet["nlp_intelligence"] = self._build_nlp_summary(regime_output)
        intel_packet["external_sector"]  = self._build_external_summary(regime_output)
        intel_packet["regime_scores"]    = self._build_regime_scores(regime_output)

        # =========================
        # 🧠 META — top-level summary
        # Single dict with all key signals for dashboard header
        # =========================
        intel_packet["meta"] = self._build_meta(
            regime_output      = regime_output,
            scenario_output    = scenario_output,
            positioning_output = positioning_output,
            strategy_output    = strategy_output,
            decision_output    = decision_output
        )

        return intel_packet