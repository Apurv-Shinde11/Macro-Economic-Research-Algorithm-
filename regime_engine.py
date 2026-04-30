from rbi_data import RBIDataFetcher, rbi_score_adjustments
import requests
import datetime


# =========================
# 📊 SUPABASE DIRECT HTTP HELPER
# Shared by both persistence scorer and change detector.
# =========================
def _fetch_recent_runs(supabase_url, service_key, limit=10):
    """
    Fetches the last N runs from Supabase.
    Returns list of dicts with regime, confidence, run_at.
    Most recent first.
    """
    try:
        url = (
            f"{supabase_url.rstrip('/')}/rest/v1/runs"
            f"?select=regime,confidence,run_at"
            f"&order=run_at.desc&limit={limit}"
        )
        headers = {
            "apikey":        service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type":  "application/json"
        }
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            return r.json()
        return []
    except Exception:
        return []


def _load_supabase_credentials():
    """
    Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from
    .streamlit/secrets.toml without requiring Streamlit.
    """
    import os
    try:
        import toml
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".streamlit", "secrets.toml"
        )
        with open(path, "r") as f:
            s = toml.load(f)
        return s.get("SUPABASE_URL", ""), s.get("SUPABASE_SERVICE_KEY", "")
    except ImportError:
        import tomllib
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".streamlit", "secrets.toml"
        )
        with open(path, "rb") as f:
            s = tomllib.load(f)
        return s.get("SUPABASE_URL", ""), s.get("SUPABASE_SERVICE_KEY", "")
    except Exception:
        return "", ""


class MacroRegimeEngine:
    def __init__(self):
        self.repo_neutral     = 6.0
        self.inflation_target = 4.0
        self.inflation_upper  = 6.0
        self.oil_risk_level   = 85
        self.inr_risk_level   = 83
        self._rbi_fetcher     = RBIDataFetcher()

        # Load Supabase credentials once at init.
        # Shared by persistence scorer AND change detector.
        self._sb_url, self._sb_key = _load_supabase_credentials()

    # =========================
    # ✅ REGIME PERSISTENCE SCORER
    # Reads last 10 runs, counts consecutive same-regime
    # days, returns confidence adjustment [-0.05, +0.05].
    # =========================
    def _regime_persistence_adjustment(self, current_regime, recent_runs):
        """
        Returns (adj: float, consecutive_days: int).
        Accepts pre-fetched recent_runs to avoid a second
        Supabase call — the change detector fetches first.

        Adjustment scale:
          0 days (just appeared) → -0.05
          1 day                  → -0.03
          2 days                 →  0.00
          3–4 days               → +0.02
          5–6 days               → +0.03
          7+ days                → +0.05
        """
        if not recent_runs:
            return 0.0, 0

        try:
            regimes    = [r.get("regime", "") for r in recent_runs]
            consecutive = 0
            for r in regimes:
                if r == current_regime:
                    consecutive += 1
                else:
                    break

            if consecutive == 0:
                adj = -0.05
            elif consecutive == 1:
                adj = -0.03
            elif consecutive == 2:
                adj = 0.0
            elif consecutive <= 4:
                adj = +0.02
            elif consecutive <= 6:
                adj = +0.03
            else:
                adj = +0.05

            print(
                f"  [Regime] Persistence: {consecutive} consecutive "
                f"{current_regime} runs → confidence adj {adj:+.2f}"
            )
            return adj, consecutive

        except Exception as e:
            print(f"  [Regime] Persistence check skipped: {e}")
            return 0.0, 0

    # =========================
    # ✅ REGIME CHANGE DETECTOR
    # Compares current regime against the most recent
    # saved run. Returns change metadata dict that flows
    # through to scheduler.py for alert firing.
    #
    # A change is flagged when:
    #   1. Current regime differs from last saved regime
    #   2. Confidence > 0.60
    #
    # Duplicate-alert prevention is handled in scheduler.py
    # (checks regime_alerts table before sending).
    # The engine just detects — it does not send.
    # =========================
    def _detect_regime_change(self, current_regime,
                               current_confidence, recent_runs):
        """
        Returns a dict:
        {
            "changed":          bool,
            "previous_regime":  str | None,
            "current_regime":   str,
            "confidence":       float,
            "reason":           str
        }
        """
        if not recent_runs:
            return {
                "changed":         False,
                "previous_regime": None,
                "current_regime":  current_regime,
                "confidence":      current_confidence,
                "reason":          "No history — first run or Supabase unreachable"
            }

        previous_regime = recent_runs[0].get("regime", "")

        if current_regime == previous_regime:
            return {
                "changed":         False,
                "previous_regime": previous_regime,
                "current_regime":  current_regime,
                "confidence":      current_confidence,
                "reason":          f"Regime stable: {current_regime}"
            }

        if current_confidence < 0.60:
            return {
                "changed":         False,
                "previous_regime": previous_regime,
                "current_regime":  current_regime,
                "confidence":      current_confidence,
                "reason":          (
                    f"Shift detected ({previous_regime} → {current_regime}) "
                    f"but confidence {current_confidence:.0%} below 60% — likely noise"
                )
            }

        # Confirmed change
        print(
            f"  [Regime] *** CHANGE DETECTED: "
            f"{previous_regime} → {current_regime} "
            f"({current_confidence:.0%} confidence) ***"
        )
        return {
            "changed":         True,
            "previous_regime": previous_regime,
            "current_regime":  current_regime,
            "confidence":      current_confidence,
            "reason":          (
                f"Confirmed: {previous_regime} → {current_regime} "
                f"at {current_confidence:.0%} confidence"
            )
        }

    def _safe_float(self, val, default=0.0):
        try:
            return float(val)
        except Exception:
            return default

    def _rbi_policy_stance(self, inflation, growth, rbi_signal="UNKNOWN"):
        if rbi_signal == "CUT":
            return "dovish"
        elif rbi_signal == "HIKE":
            return "hawkish"
        if inflation > self.inflation_upper:
            return "hawkish"
        elif growth < 5.5:
            return "dovish"
        else:
            return "neutral"

    def _inflation_driver(self, inflation, crude):
        if crude and crude > self.oil_risk_level:
            return "oil-driven"
        elif inflation > self.inflation_upper:
            return "broad-based"
        else:
            return "contained"

    def _external_risk(self, usd_inr, crude,
                        india_risks=None, global_factors=None):
        flags = []
        if usd_inr and usd_inr > self.inr_risk_level:
            flags.append("currency_weakness")
        if crude and crude > self.oil_risk_level:
            flags.append("oil_shock")
        if isinstance(india_risks, list):
            for r in india_risks:
                if isinstance(r, str) and r.strip():
                    flags.append(r.lower().replace(" ", "_"))
        if isinstance(global_factors, list):
            for f in global_factors:
                if isinstance(f, str) and f.strip():
                    flags.append(f.lower().replace(" ", "_"))
        return list(set(flags)) if flags else ["stable"]

    def _adjust_confidence(self, base_confidence,
                            nlp_confidence, nlp_source):
        if nlp_source == "llm+keyword":
            return round(
                0.7 * base_confidence + 0.3 * nlp_confidence, 2
            )
        else:
            return round(base_confidence * 0.92, 2)

    def _score_regimes(
        self,
        policy_stance,
        liquidity_score,
        growth,
        inflation,
        sentiment,
        equity_bias,
        nlp_regime,
        rbi_signal,
        fiscal_supportive
    ):
        scores = {
            "LIQUIDITY_TIGHTENING":                  0.0,
            "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": 0.0,
            "LIQUIDITY_DRIVEN_EXPANSION":            0.0,
            "EARLY_CYCLE_RECOVERY":                  0.0,
            "MONETARY_TIGHTENING":                   0.0,
            "GROWTH_SLOWDOWN_SUPPORT":               0.0,
            "STABLE_GROWTH":                         0.0,
            "STAGFLATION_RISK":                      0.0,
            "TRANSITION_PHASE":                      0.5
        }

        if policy_stance == "hawkish":
            scores["LIQUIDITY_TIGHTENING"] += 1.5
        if liquidity_score < -0.3:
            scores["LIQUIDITY_TIGHTENING"] += 2.0
        if nlp_regime == "HAWKISH":
            scores["LIQUIDITY_TIGHTENING"] += 0.5
        if equity_bias == "RISK_OFF":
            scores["LIQUIDITY_TIGHTENING"] += 0.5

        if inflation > self.inflation_upper:
            scores["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] += 2.0
        if policy_stance == "hawkish":
            scores["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] += 1.0
        if nlp_regime == "HAWKISH":
            scores["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] += 1.0
        if rbi_signal == "HIKE":
            scores["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] += 1.0
        if sentiment > 0.2:
            scores["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] += 0.5

        if liquidity_score > 0.3:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] += 2.0
        if growth >= 6.0:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] += 1.5
        if equity_bias == "RISK_ON":
            scores["LIQUIDITY_DRIVEN_EXPANSION"] += 1.0
        if fiscal_supportive:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] += 0.8
        if nlp_regime == "DOVISH":
            scores["LIQUIDITY_DRIVEN_EXPANSION"] += 0.8
        if liquidity_score > 0.3:
            scores["STABLE_GROWTH"] -= 1.5

        if policy_stance == "dovish":
            scores["EARLY_CYCLE_RECOVERY"] += 1.5
        if liquidity_score > 0:
            scores["EARLY_CYCLE_RECOVERY"] += 1.0
        if rbi_signal == "CUT":
            scores["EARLY_CYCLE_RECOVERY"] += 1.5
        if growth >= 5.5 and growth < 7.0:
            scores["EARLY_CYCLE_RECOVERY"] += 0.5
        if equity_bias == "RISK_ON":
            scores["EARLY_CYCLE_RECOVERY"] += 0.5

        if policy_stance == "hawkish":
            scores["MONETARY_TIGHTENING"] += 1.5
        if rbi_signal == "HIKE":
            scores["MONETARY_TIGHTENING"] += 1.5
        if inflation > self.inflation_target:
            scores["MONETARY_TIGHTENING"] += 1.0
        if liquidity_score >= -0.3:
            scores["MONETARY_TIGHTENING"] += 0.5

        if growth < 5.5:
            scores["GROWTH_SLOWDOWN_SUPPORT"] += 2.0
        if policy_stance == "dovish":
            scores["GROWTH_SLOWDOWN_SUPPORT"] += 1.0
        if sentiment < -0.2:
            scores["GROWTH_SLOWDOWN_SUPPORT"] += 0.5
        if equity_bias == "RISK_OFF":
            scores["GROWTH_SLOWDOWN_SUPPORT"] += 0.5

        if policy_stance == "neutral":
            scores["STABLE_GROWTH"] += 1.5
        if growth >= 6.5:
            scores["STABLE_GROWTH"] += 1.5
        if inflation <= self.inflation_upper:
            scores["STABLE_GROWTH"] += 1.0
        if equity_bias == "NEUTRAL":
            scores["STABLE_GROWTH"] += 0.5
        if abs(sentiment) < 0.2:
            scores["STABLE_GROWTH"] += 0.5

        if inflation > self.inflation_upper and growth < 5.5:
            scores["STAGFLATION_RISK"] += 3.0
        if policy_stance == "hawkish" and growth < 5.5:
            scores["STAGFLATION_RISK"] += 1.5
        if equity_bias == "RISK_OFF" and sentiment > 0.2:
            scores["STAGFLATION_RISK"] += 1.0

        return scores

    def _build_narrative(self, regime, growth, inflation, capex,
                          liquidity_score, rbi_signal, dominant_theme,
                          consecutive_days=0):
        narratives = {
            "LIQUIDITY_TIGHTENING": (
                "RBI is actively draining liquidity while maintaining "
                "a hawkish stance. Credit conditions are tightening and "
                "financial conditions are restrictive. Risk assets face "
                "valuation headwinds — defensives and short-duration "
                "bonds preferred."
            ),
            "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": (
                f"Inflation at {inflation}% is the dominant macro force "
                f"— above RBI's comfort band. "
                f"{'RBI is signalling a rate hike.' if rbi_signal == 'HIKE' else 'Rate cuts are unlikely near-term.'} "
                "Equity multiples may compress. Gold and short-duration "
                "instruments offer refuge."
            ),
            "LIQUIDITY_DRIVEN_EXPANSION": (
                f"India is in a liquidity-driven expansion phase. "
                f"GDP tracking at {growth}% — above trend. "
                "RBI's accommodative liquidity posture supports "
                "risk-on positioning. "
                f"Capex at Rs.{capex}L Cr signals sustained public "
                "investment momentum. Equities, cyclicals, and "
                "high-beta assets are in favour."
            ),
            "EARLY_CYCLE_RECOVERY": (
                "India is entering an early cycle recovery. "
                f"{'RBI has signalled rate cuts — ' if rbi_signal == 'CUT' else 'RBI is turning dovish — '}"
                "liquidity conditions improving. Rate-sensitive sectors "
                "— Banks, Real Estate, Autos — stand to benefit most."
            ),
            "MONETARY_TIGHTENING": (
                f"Monetary tightening is the dominant regime. "
                f"Inflation at {inflation}% above the 4% target. "
                f"{'RBI has signalled a rate hike.' if rbi_signal == 'HIKE' else 'Rate hikes or an extended pause — no cuts expected.'} "
                "Duration risk in bonds is elevated. Defensives "
                "outperform cyclicals."
            ),
            "GROWTH_SLOWDOWN_SUPPORT": (
                f"Growth is below potential at {growth}% — "
                "RBI is in support mode. Policy rates are heading lower. "
                "Quality bonds and defensives outperform. Avoid high-beta "
                "and cyclical exposure until growth stabilises."
            ),
            "STABLE_GROWTH": (
                f"India is in a stable growth regime. GDP at {growth}%, "
                "inflation contained. Balanced macro allows broad equity "
                "participation. No strong directional macro trade — "
                "focus on stock selection and quality."
            ),
            "STAGFLATION_RISK": (
                f"Stagflation risk is elevated — high inflation "
                f"({inflation}%) coexisting with weak growth ({growth}%). "
                "This is the most challenging macro environment. "
                "Hard assets (gold, commodities) and export earners "
                "(IT, Pharma) are preferred. Avoid domestic cyclicals "
                "and rate-sensitive sectors entirely."
            ),
            "TRANSITION_PHASE": (
                "Macro signals are mixed — no single dominant force "
                "is confirmed. "
                f"{dominant_theme + ' is the tentative theme.' if dominant_theme else ''} "
                "Adopt a balanced, diversified posture and monitor "
                "for regime confirmation."
            )
        }

        base = narratives.get(regime, narratives["TRANSITION_PHASE"])

        # Append persistence context
        if consecutive_days >= 7:
            base += (
                f" This regime has been confirmed for "
                f"{consecutive_days} consecutive runs — "
                "high structural conviction."
            )
        elif consecutive_days >= 3:
            base += (
                f" Regime has persisted for {consecutive_days} "
                "consecutive runs, building structural confidence."
            )
        elif consecutive_days == 0:
            base += (
                " This regime has just emerged in recent data — "
                "conviction will build over the next 2–3 runs "
                "as signals confirm."
            )

        return base

    def _build_drivers(self, regime, growth, inflation, capex,
                        liquidity_score, rbi_signal,
                        dominant_theme, key_signals):
        driver_map = {
            "LIQUIDITY_TIGHTENING": [
                "Hawkish RBI stance",
                "Liquidity withdrawal from system",
                "Tight financial conditions — credit expensive"
            ],
            "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": [
                f"Inflation at {inflation}% — above RBI comfort band",
                f"RBI signal: {rbi_signal}",
                "External sector under pressure"
            ],
            "LIQUIDITY_DRIVEN_EXPANSION": [
                "Abundant system liquidity",
                f"GDP growth at {growth}% — above trend",
                "Risk-on environment with FII inflow support"
            ],
            "EARLY_CYCLE_RECOVERY": [
                f"RBI turning dovish — signal: {rbi_signal}",
                "Liquidity conditions improving",
                "Growth recovering from trough"
            ],
            "MONETARY_TIGHTENING": [
                f"Inflation at {inflation}% — above 4% target",
                f"RBI signal: {rbi_signal}",
                "Liquidity neutral to tight"
            ],
            "GROWTH_SLOWDOWN_SUPPORT": [
                f"GDP growth weak at {growth}%",
                "RBI in policy support mode",
                "Fiscal stimulus likely required"
            ],
            "STABLE_GROWTH": [
                f"Steady GDP growth at {growth}%",
                "Inflation within RBI comfort band",
                "Balanced policy environment"
            ],
            "STAGFLATION_RISK": [
                f"Inflation elevated at {inflation}%",
                f"Growth weak at {growth}%",
                "Dual pressure — no easy policy response"
            ],
            "TRANSITION_PHASE": [
                "Mixed macro signals — no dominant force",
                f"NLP theme: {dominant_theme}" if dominant_theme
                else "Regime awaiting confirmation"
            ]
        }
        drivers = list(driver_map.get(regime, ["Mixed signals"]))
        for sig in key_signals[:2]:
            if isinstance(sig, str) and sig not in drivers:
                drivers.append(sig)
        return drivers

    # =========================
    # 🚦 MAIN ENTRY POINT
    # =========================
    def detect_regime(self, intel, liquidity_output=None):
        intel = intel if isinstance(intel, dict) else {}

        hard_data      = intel.get("hard_data",              {})
        sentiment      = self._safe_float(intel.get("sentiment_score",        0.0))
        nlp_regime     = intel.get("regime_type",            "NEUTRAL/WATCH")
        nlp_confidence = self._safe_float(intel.get("confidence",             0.5))
        nlp_source     = intel.get("source",                 "keyword")
        rbi_signal     = intel.get("rbi_policy_implication", "UNKNOWN")
        equity_bias    = intel.get("equity_bias",            "NEUTRAL")
        dominant_theme = intel.get("dominant_theme",         "")
        key_signals    = intel.get("key_signals",            [])
        india_risks    = intel.get("india_specific_risks",   [])
        global_factors = intel.get("global_macro_factors",   [])
        reasoning      = intel.get("reasoning",              "")
        provider       = intel.get("provider",               "none")

        repo      = self._safe_float(hard_data.get("repo_rate",      6.5),  6.5)
        inflation = self._safe_float(hard_data.get("cpi",            5.0),  5.0)
        growth    = self._safe_float(hard_data.get("gdp_growth",     7.2),  7.2)
        deficit   = self._safe_float(hard_data.get("fiscal_deficit", 4.3),  4.3)
        capex     = self._safe_float(hard_data.get("capex_lakh_cr",  12.2), 12.2)

        liquidity_regime = "UNKNOWN"
        liquidity_score  = 0.0
        if isinstance(liquidity_output, dict):
            liquidity_regime = liquidity_output.get(
                "liquidity_regime", "UNKNOWN"
            )
            liquidity_score  = self._safe_float(
                liquidity_output.get("liquidity_score", 0)
            )

        policy_stance     = self._rbi_policy_stance(
            inflation, growth, rbi_signal
        )
        inflation_type    = self._inflation_driver(inflation, None)
        external_risk     = self._external_risk(
            None, None, india_risks, global_factors
        )
        fiscal_supportive = deficit > 4.0 or capex > 10.0

        scores = self._score_regimes(
            policy_stance     = policy_stance,
            liquidity_score   = liquidity_score,
            growth            = growth,
            inflation         = inflation,
            sentiment         = sentiment,
            equity_bias       = equity_bias,
            nlp_regime        = nlp_regime,
            rbi_signal        = rbi_signal,
            fiscal_supportive = fiscal_supportive
        )

        # -------------------------
        # RBI SIGNAL INJECTION
        # -------------------------
        rbi_data_out = {}
        try:
            rbi_signals = self._rbi_fetcher.get_rbi_signals()
            rbi_adj     = rbi_score_adjustments(rbi_signals)

            for regime_key, adj in rbi_adj.items():
                if regime_key in scores:
                    scores[regime_key] = max(
                        0.0, min(10.0, scores[regime_key] + adj)
                    )

            rbi_data_out = {
                "repo_rate":        rbi_signals.get("repo_rate",         6.5),
                "credit_growth":    rbi_signals.get("credit_growth_pct", 0),
                "m3_growth":        rbi_signals.get("m3_growth_pct",     0),
                "forex_reserves":   rbi_signals.get("forex_reserves_bn", 0),
                "liquidity_signal": rbi_signals.get("liquidity_signal",  "NEUTRAL"),
                "policy_direction": rbi_signals.get("policy_direction",  "NEUTRAL"),
                "credit_impulse":   rbi_signals.get(
                    "regime_signals", {}
                ).get("credit_impulse", "NEUTRAL"),
                "source":           rbi_signals.get("source", "fallback")
            }

            print(
                f"  [Regime] RBI signals applied — "
                f"credit: {rbi_data_out['credit_impulse']}, "
                f"policy: {rbi_data_out['policy_direction']}, "
                f"liquidity: {rbi_data_out['liquidity_signal']}"
            )

        except Exception as rbi_err:
            print(f"  [Regime] RBI scoring skipped: {rbi_err}")
            rbi_data_out = {}

        # -------------------------
        # Pick winning regime
        # -------------------------
        regime    = max(scores, key=scores.get)
        top_score = scores[regime]

        # -------------------------
        # Base confidence by regime
        # -------------------------
        base_confidence_map = {
            "LIQUIDITY_TIGHTENING":                  0.82,
            "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": 0.76,
            "LIQUIDITY_DRIVEN_EXPANSION":            0.78,
            "EARLY_CYCLE_RECOVERY":                  0.72,
            "MONETARY_TIGHTENING":                   0.70,
            "GROWTH_SLOWDOWN_SUPPORT":               0.72,
            "STABLE_GROWTH":                         0.68,
            "STAGFLATION_RISK":                      0.80,
            "TRANSITION_PHASE":                      0.55
        }
        base_conf = base_confidence_map.get(regime, 0.60)

        sorted_scores  = sorted(scores.values(), reverse=True)
        margin         = (
            sorted_scores[0] - sorted_scores[1]
            if len(sorted_scores) > 1 else 1.0
        )
        if margin < 1.0:
            base_conf *= 0.90

        confidence = self._adjust_confidence(
            base_conf, nlp_confidence, nlp_source
        )

        # -------------------------
        # Challenger regime
        # -------------------------
        sorted_regimes = sorted(
            scores.items(), key=lambda x: x[1], reverse=True
        )
        challenger = sorted_regimes[1][0] if len(sorted_regimes) > 1 else None

        # -------------------------
        # ✅ SINGLE SUPABASE FETCH
        # Fetches recent runs ONCE and passes the result to
        # both the persistence scorer and change detector.
        # Prevents two round-trips to Supabase per pipeline run.
        # -------------------------
        recent_runs = []
        if self._sb_url and self._sb_key:
            recent_runs = _fetch_recent_runs(
                self._sb_url, self._sb_key, limit=10
            )

        # -------------------------
        # ✅ REGIME PERSISTENCE ADJUSTMENT
        # -------------------------
        persistence_adj, consecutive_days = \
            self._regime_persistence_adjustment(regime, recent_runs)

        confidence = round(
            max(0.40, min(0.95, confidence + persistence_adj)), 2
        )

        # -------------------------
        # ✅ REGIME CHANGE DETECTION
        # Runs after final confidence is calculated so the
        # change check uses the true final confidence value.
        # -------------------------
        change_info = self._detect_regime_change(
            current_regime      = regime,
            current_confidence  = confidence,
            recent_runs         = recent_runs
        )

        # -------------------------
        # EQUITY BIAS OVERRIDE
        # -------------------------
        PRO_RISK_REGIMES = {
            "LIQUIDITY_DRIVEN_EXPANSION",
            "EARLY_CYCLE_RECOVERY",
            "STABLE_GROWTH"
        }
        RISK_OFF_REGIMES = {
            "LIQUIDITY_TIGHTENING",
            "MONETARY_TIGHTENING",
            "GROWTH_SLOWDOWN_SUPPORT",
            "STAGFLATION_RISK",
            "INFLATION_PRESSURE_WITH_EXTERNAL_RISK"
        }

        if regime in PRO_RISK_REGIMES and confidence > 0.65:
            equity_bias = "RISK_ON"
        elif regime in RISK_OFF_REGIMES and confidence > 0.65:
            equity_bias = "RISK_OFF"

        # -------------------------
        # Narrative + Drivers
        # -------------------------
        narrative = self._build_narrative(
            regime           = regime,
            growth           = growth,
            inflation        = inflation,
            capex            = capex,
            liquidity_score  = liquidity_score,
            rbi_signal       = rbi_signal,
            dominant_theme   = dominant_theme,
            consecutive_days = consecutive_days
        )
        drivers = self._build_drivers(
            regime          = regime,
            growth          = growth,
            inflation       = inflation,
            capex           = capex,
            liquidity_score = liquidity_score,
            rbi_signal      = rbi_signal,
            dominant_theme  = dominant_theme,
            key_signals     = key_signals
        )

        # -------------------------
        # Final output
        # change_info flows to scheduler.py for alert firing
        # and to main.py for the in-app banner (future use)
        # -------------------------
        return {
            "regime":     regime,
            "confidence": confidence,
            "narrative":  narrative,

            "components": {
                "growth": (
                    "strong"   if growth >= 6.5 else
                    "moderate" if growth >= 5.5 else
                    "weak"
                ),
                "inflation": {
                    "level":  inflation,
                    "status": (
                        "high" if inflation > self.inflation_upper
                        else "moderate"
                    ),
                    "driver": inflation_type
                },
                "liquidity": {
                    "regime":           liquidity_regime,
                    "score":            liquidity_score,
                    "system_liquidity": (
                        "abundant" if liquidity_score >  0.3 else
                        "tight"    if liquidity_score < -0.3 else
                        "neutral"
                    )
                },
                "policy_stance":     policy_stance,
                "fiscal_supportive": fiscal_supportive,
                "rbi_signal":        rbi_signal,
                "equity_bias":       equity_bias,
                "consecutive_days":  consecutive_days,
                "persistence_adj":   persistence_adj
            },

            "external_sector": {
                "risk_flags":     external_risk,
                "india_risks":    india_risks,
                "global_factors": global_factors
            },

            "nlp_intelligence": {
                "dominant_theme":  dominant_theme,
                "key_signals":     key_signals,
                "india_risks":     india_risks,
                "global_factors":  global_factors,
                "reasoning":       reasoning,
                "nlp_confidence":  nlp_confidence,
                "source":          nlp_source,
                "provider":        provider
            },

            "regime_scores": scores,
            "challenger":    challenger,
            "drivers":       drivers,
            "rbi_data":      rbi_data_out,

            # ✅ Change detection flows downstream
            # scheduler.py reads this to decide whether to fire alerts
            # main.py can read this to show an in-app banner
            "change_info": change_info,

            "inputs": {
                "repo_rate":       repo,
                "inflation":       inflation,
                "growth":          growth,
                "fiscal_deficit":  deficit,
                "capex_lakh_cr":   capex,
                "liquidity_score": liquidity_score,
                "nlp_sentiment":   sentiment,
                "rbi_signal":      rbi_signal,
                "equity_bias":     equity_bias
            }
        }