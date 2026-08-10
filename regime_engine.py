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
        self.oil_risk_level   = 95
        self.inr_risk_level   = 90
        self._rbi_fetcher     = RBIDataFetcher()

        # Load Supabase credentials once at init.
        # Shared by persistence scorer AND change detector.
        self._sb_url, self._sb_key = _load_supabase_credentials()

    def health_check(self) -> dict:
        """
        Startup probe — call once before first pipeline run to confirm
        all critical systems are reachable.

        Returns dict:
        {
            "healthy":  bool,
            "checks":   list[dict],
            "warnings": list[str],
            "errors":   list[str],
        }
        """
        checks   = []
        warnings = []
        errors   = []

        # ── Check 1: Supabase reachable ──────────────────────────────
        sb_ok = False
        try:
            if self._sb_url and self._sb_key:
                runs  = _fetch_recent_runs(
                    self._sb_url, self._sb_key, limit=1
                )
                sb_ok = isinstance(runs, list)
                checks.append({
                    "name":   "Supabase connectivity",
                    "status": "PASS" if sb_ok else "FAIL",
                    "detail": (
                        f"Returned {len(runs)} rows"
                        if sb_ok
                        else "No data returned"
                    ),
                })
            else:
                errors.append(
                    "Supabase URL or key missing — "
                    "run history unavailable"
                )
                checks.append({
                    "name":   "Supabase connectivity",
                    "status": "FAIL",
                    "detail": "Credentials not found",
                })
        except Exception as e:
            errors.append(f"Supabase check failed: {e}")
            checks.append({
                "name":   "Supabase connectivity",
                "status": "ERROR",
                "detail": str(e),
            })

        # ── Check 2: RBI data fetcher ─────────────────────────────────
        rbi_ok = False
        try:
            rbi_signals = self._rbi_fetcher.get_rbi_signals()
            rbi_ok      = isinstance(rbi_signals, dict)
            checks.append({
                "name":   "RBI data fetcher",
                "status": "PASS" if rbi_ok else "WARN",
                "detail": (
                    f"repo_rate={rbi_signals.get('repo_rate', '?')}"
                    if rbi_ok
                    else "Fallback data in use"
                ),
            })
            if not rbi_ok:
                warnings.append(
                    "RBI DBIE unreachable — "
                    "using hardcoded fallback values"
                )
        except Exception as e:
            warnings.append(
                f"RBI fetcher warning: {e} — fallback will be used"
            )
            checks.append({
                "name":   "RBI data fetcher",
                "status": "WARN",
                "detail": str(e),
            })

        # ── Check 3: NLP source quality ───────────────────────────────
        # Cannot test LLM keys here without an actual API call —
        # instead advise the caller to verify nlp_source on first run.
        try:
            checks.append({
                "name":   "NLP layer",
                "status": "ADVISORY",
                "detail": (
                    "Cannot probe LLM keys without a live call. "
                    "Verify nlp_source='llm+keyword' on first run output."
                ),
            })
            warnings.append(
                "NLP health advisory: confirm "
                "nlp_source=llm+keyword on first "
                "live run. If nlp_source=keyword, "
                "confidence will be suppressed ~8% "
                "and equity_bias defaults to NEUTRAL."
            )
        except Exception as e:
            checks.append({
                "name":   "NLP layer",
                "status": "ADVISORY",
                "detail": str(e),
            })

        healthy = len(errors) == 0

        print(
            f"  [HEALTH] Overall: "
            f"{'HEALTHY' if healthy else 'UNHEALTHY'} — "
            f"{len(checks)} checks, "
            f"{len(warnings)} warnings, "
            f"{len(errors)} errors",
            flush=True
        )
        for c in checks:
            print(
                f"  [HEALTH] {c['name']}: "
                f"{c['status']} — {c['detail']}",
                flush=True
            )

        return {
            "healthy":  healthy,
            "checks":   checks,
            "warnings": warnings,
            "errors":   errors,
        }

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
        # Kept for backward compatibility; superseded by _compute_signal_alignment.
        if nlp_source == "llm+keyword":
            return round(
                0.7 * base_confidence + 0.3 * nlp_confidence, 2
            )
        else:
            return round(base_confidence * 0.92, 2)

    def _smooth_confidence(
        self,
        raw_confidence: float,
        recent_runs: list
    ) -> float:
        """
        3-run exponential weighted average on displayed confidence.
        Raw confidence drives all internal logic unchanged.
        Weights: current=0.50, prev=0.30, prev-2=0.20
        """
        if not recent_runs:
            return raw_confidence

        prior = []
        for r in (recent_runs or [])[-2:]:
            c = r.get("confidence")
            if c is not None:
                try:
                    prior.append(float(c))
                except (ValueError, TypeError):
                    pass

        if len(prior) == 0:
            return raw_confidence
        elif len(prior) == 1:
            smoothed = round(
                0.60 * raw_confidence
                + 0.40 * prior[-1], 3
            )
        else:
            smoothed = round(
                0.50 * raw_confidence
                + 0.30 * prior[-1]
                + 0.20 * prior[-2], 3
            )

        print(
            f"[CONF_SMOOTH] raw="
            f"{raw_confidence:.3f} "
            f"prior={[round(p,3) for p in prior]} "
            f"smoothed={smoothed:.3f}",
            flush=True
        )
        return smoothed

    # =========================
    # ✅ THREE-STATE SIGNAL HELPER
    # =========================
    def _sig(self, value, strong_t, neutral_t, direction="above"):
        """
        Maps a continuous value onto a three-state signal weight.
        direction='above': STRONG when value >= strong_t, NEUTRAL when >= neutral_t
        direction='below': STRONG when value <= strong_t, NEUTRAL when <= neutral_t
        """
        if direction == "above":
            if value >= strong_t:  return 1.0
            if value >= neutral_t: return 0.3
            return 0.0
        else:
            if value <= strong_t:  return 1.0
            if value <= neutral_t: return 0.3
            return 0.0

    # =========================
    # ✅ SIGNAL ALIGNMENT SCORER
    # Replaces fixed base_confidence_map + binary margin penalty.
    # Each signal is STRONG (1.0) / NEUTRAL (0.3) / WEAK (0.0).
    # Weighted average → alignment score in [0, 1].
    # =========================
    def _compute_signal_alignment(
        self, regime, policy_stance, liquidity_score,
        growth, inflation, sentiment, equity_bias,
        nlp_regime, rbi_signal, fiscal_supportive,
        crude_live=0, vix_live=15, fii_live=None,
    ):
        """
        Returns (alignment: float [0,1], details: list[dict]).
        alignment = weighted mean of per-signal states for the winning regime.
        details   = full per-signal breakdown for diagnostics and dashboard output.
        """
        S, N, W = 1.0, 0.3, 0.0

        # ── pre-compute common signal states ──────────────────────────────
        liq_expan  = self._sig(liquidity_score,  0.5,  0.1)           # abundant
        liq_tight  = self._sig(liquidity_score, -0.5, -0.1, "below")  # tight
        liq_pos    = self._sig(liquidity_score,  0.3,  0.0)           # positive

        gdp_str    = self._sig(growth, 7.0, 6.0)                      # above trend
        gdp_weak   = self._sig(growth, 5.0, 5.5, "below")             # weak

        infl_hot   = self._sig(inflation, 6.0, 4.5)                   # above target
        infl_ok    = (S if inflation < 4.0 else N if inflation < 5.5 else W)

        eq_on      = (S if equity_bias == "RISK_ON"  else N if equity_bias == "NEUTRAL" else W)
        eq_off     = (S if equity_bias == "RISK_OFF" else N if equity_bias == "NEUTRAL" else W)

        nlp_dov    = (S if nlp_regime == "DOVISH"  else N if "NEUTRAL" in str(nlp_regime) else W)
        nlp_haw    = (S if nlp_regime == "HAWKISH" else N if "NEUTRAL" in str(nlp_regime) else W)

        pol_dov    = (S if policy_stance == "dovish"  else N if policy_stance == "neutral" else W)
        pol_haw    = (S if policy_stance == "hawkish" else N if policy_stance == "neutral" else W)
        pol_neu    = (S if policy_stance == "neutral" else N)

        rbi_cut    = (S if rbi_signal == "CUT"  else N if rbi_signal in ("PAUSE", "UNKNOWN") else W)
        rbi_hike   = (S if rbi_signal == "HIKE" else N if rbi_signal in ("PAUSE", "UNKNOWN") else W)

        fiscal     = (S if fiscal_supportive else N)
        sent_neg   = (S if sentiment < -0.3  else N if sentiment < 0.0  else W)
        sent_calm  = (S if abs(sentiment) < 0.1 else N if abs(sentiment) < 0.3 else W)

        # Live market signal states
        crude_stress  = self._sig(crude_live, 105, 95)
        crude_support = self._sig(crude_live, 70, 80, "below")
        vix_stress    = self._sig(vix_live, 25, 20)
        vix_calm      = self._sig(vix_live, 12, 16, "below")

        fii_buying  = 0.0
        fii_selling = 0.0
        if fii_live is not None:
            fii_buying  = (
                1.0 if fii_live > 3000 else
                0.3 if fii_live > 500  else
                0.0
            )
            fii_selling = (
                1.0 if fii_live < -3000 else
                0.3 if fii_live < -500  else
                0.0
            )

        # ── per-regime signal tables: (label, weight, state) ──────────────
        tables = {
            "LIQUIDITY_DRIVEN_EXPANSION": [
                ("System Liquidity",       0.25, liq_expan),
                ("GDP Growth",             0.20, gdp_str),
                ("Market Bias (RISK_ON)",  0.15, eq_on),
                ("NLP Tone (Dovish)",      0.12, nlp_dov),
                ("Fiscal Support",         0.08, fiscal),
                ("Crude Contained",        0.10, crude_support),
                ("VIX Calm",               0.05, vix_calm),
                ("FII Buying",             0.05, fii_buying),
            ],
            "EXTERNAL_SHOCK": [
                ("Crude Spike",            0.35, crude_stress),
                ("VIX Stress",             0.25, vix_stress),
                ("FII Selling",            0.20, fii_selling),
                ("Market Bias (RISK_OFF)", 0.20, eq_off),
            ],
            "LIQUIDITY_TIGHTENING": [
                ("Liquidity Drain",        0.35, liq_tight),
                ("Policy Hawkishness",     0.25, pol_haw),
                ("Market Bias (RISK_OFF)", 0.20, eq_off),
                ("NLP Tone (Hawkish)",     0.20, nlp_haw),
            ],
            "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": [
                ("Inflation Level",        0.35, infl_hot),
                ("Policy Hawkishness",     0.25, pol_haw),
                ("NLP Tone (Hawkish)",     0.25, nlp_haw),
                ("RBI Hike Signal",        0.15, rbi_hike),
            ],
            "EARLY_CYCLE_RECOVERY": [
                ("Policy Dovishness",      0.35, pol_dov),
                ("RBI Cut Signal",         0.30, rbi_cut),
                ("Liquidity Improving",    0.20, liq_pos),
                ("Market Bias (RISK_ON)",  0.15, eq_on),
            ],
            "MONETARY_TIGHTENING": [
                ("Policy Hawkishness",     0.35, pol_haw),
                ("RBI Hike Signal",        0.30, rbi_hike),
                ("Inflation Above Target", 0.20, infl_hot),
                ("Liquidity Signal",       0.15, (S if liquidity_score < 0 else N)),
            ],
            "GROWTH_SLOWDOWN_SUPPORT": [
                ("Growth Weakness",        0.40, gdp_weak),
                ("Policy Dovishness",      0.30, pol_dov),
                ("Market Bias (RISK_OFF)", 0.20, eq_off),
                ("Negative Sentiment",     0.10, sent_neg),
            ],
            "STABLE_GROWTH": [
                ("Policy Neutral",         0.25, pol_neu),
                ("GDP Above Trend",        0.20, self._sig(growth, 6.5, 5.5)),
                ("Inflation Contained",    0.20, infl_ok),
                ("Subdued Sentiment",      0.15, sent_calm),
                ("Crude Contained",        0.10, crude_support),
                ("VIX Calm",               0.10, vix_calm),
            ],
            "STAGFLATION_RISK": [
                ("Inflation Elevated",     0.40, infl_hot),
                ("Growth Weakness",        0.40, gdp_weak),
                ("Bearish Sentiment",      0.20, eq_off),
            ],
            "TRANSITION_PHASE": [
                ("Signal Clarity",         1.00, N),
            ],
        }

        signals   = tables.get(regime, [("Default", 1.0, N)])
        total_w   = sum(w for _, w, _ in signals)
        alignment = sum(w * s for _, w, s in signals) / total_w if total_w > 0 else 0.5
        alignment = max(0.0, min(1.0, alignment))

        details = [
            {
                "signal":       label,
                "weight":       w,
                "state":        "STRONG" if s >= 0.9 else "NEUTRAL" if s >= 0.2 else "WEAK",
                "state_value":  round(s, 2),
                "contribution": round(w * s, 4),
            }
            for label, w, s in signals
        ]

        n_strong  = sum(1 for d in details if d["state"] == "STRONG")
        n_neutral = sum(1 for d in details if d["state"] == "NEUTRAL")
        n_weak    = sum(1 for d in details if d["state"] == "WEAK")
        print(
            f"  [Regime] Signal alignment for {regime}: {alignment:.3f} "
            f"({n_strong}S / {n_neutral}N / {n_weak}W)",
            flush=True
        )
        return alignment, details

    def _compute_signal_entropy(
        self,
        signals: list
    ) -> dict:
        """
        Computes Shannon entropy across
        the nine signal scores to
        measure how aligned or
        contradictory the signals are.

        Low entropy = signals aligned
        → high conviction
        High entropy = signals
        contradictory → genuine
        uncertainty

        Returns:
        {
          "entropy": float (0.0-1.0),
          "label": str,
          "confidence_adj": float,
          "interpretation": str
        }
        """
        import math

        if not signals:
            return {
                "entropy": 1.0,
                "label": "UNCERTAIN",
                "confidence_adj": -0.05,
                "interpretation":
                    "No signals available"
            }

        # Extract scores
        scores = [
            max(0.001, float(
                s.get("score", 0.5)
            ))
            for s in signals
        ]

        import math

        n = len(scores)
        if n == 0:
            return {
                "entropy": 1.0,
                "label": "UNCERTAIN",
                "confidence_adj": -0.05,
                "interpretation":
                    "No signals available"
            }

        # Mean score — how bullish/bearish
        mean_score = sum(scores) / n

        # Variance around the mean —
        # low variance = signals agree
        # high variance = signals disagree
        variance = sum(
            (s - mean_score) ** 2
            for s in scores
        ) / n
        std_dev = math.sqrt(variance)

        # Normalize std_dev to 0-1 range
        # Max possible std_dev with scores
        # in [0,1] is 0.5
        normalized_dispersion = round(
            min(1.0, std_dev / 0.5), 3
        )

        # Also compute directional
        # conviction — how far mean is
        # from neutral (0.5)
        # High conviction = mean far
        # from 0.5 (bullish or bearish)
        directional_conviction = round(
            abs(mean_score - 0.5) * 2, 3
        )

        # Combined alignment score:
        # High when signals agree AND
        # have directional conviction
        # Low when signals scattered
        # or all near neutral 0.5
        alignment = round(
            (1 - normalized_dispersion)
            * directional_conviction, 3
        )

        # Classify alignment
        if alignment > 0.55:
            label = "ALIGNED"
            adj   = +0.02
            direction = (
                "BULLISH"
                if mean_score > 0.5
                else "BEARISH"
            )
            interpretation = (
                f"Signals strongly aligned"
                f" — {direction} conviction"
            )
        elif alignment > 0.30:
            label = "MODERATE"
            adj   = 0.0
            interpretation = (
                "Moderate signal alignment"
                " — directional but mixed"
            )
        elif alignment > 0.15:
            label = "DISPERSED"
            adj   = -0.02
            interpretation = (
                "Signals contradictory"
                " — treat with caution"
            )
        else:
            label = "UNCERTAIN"
            adj   = -0.05
            interpretation = (
                "Signals near neutral"
                " with high dispersion"
                " — possible regime"
                " transition"
            )

        print(
            f"  [ENTROPY] mean={mean_score:.3f}"
            f" std={std_dev:.3f}"
            f" alignment={alignment:.3f}"
            f" label={label}"
            f" adj={adj:+.2f}",
            flush=True
        )

        return {
            "entropy":          normalized_dispersion,
            "alignment":        alignment,
            "mean_score":       mean_score,
            "label":            label,
            "confidence_adj":   adj,
            "interpretation":   interpretation,
        }

    # =========================
    # ✅ LEADING INDICATOR SCORER
    # Fast-moving signals scored independently of lagging regime data.
    # Returns (score: float [0,1], signals: list[dict], trend: str, entropy: dict)
    # =========================
    def _compute_leading_score(
        self,
        regime,
        crude_live,
        vix_live,
        fii_live,
        liquidity_score,
        yield_spread_india,
        pmi_level,
        recent_runs,
        nifty_pe=0.0,
        garch_score=None,
        garch_regime=None,
        garch_direction=None,
        credit_impulse_score=None,
        credit_impulse_trend=None,
        fii_trend_score=None,
        fii_streak_dir=None,
        credit_spread_score=None,
        credit_spread_signal=None,
        islm_score=None,
        islm_regime=None,
    ):
        signals = []
        score   = 0.5

        # ── VIX signal ────────────────────────────────────────────────
        if vix_live > 0:
            if vix_live < 14:
                vix_sig, vix_label = 1.0, "CALM"
            elif vix_live < 18:
                vix_sig, vix_label = 0.65, "NORMAL"
            elif vix_live < 22:
                vix_sig, vix_label = 0.35, "ELEVATED"
            else:
                vix_sig, vix_label = 0.0, "FEAR"
            signals.append({
                "indicator": "India VIX",
                "value":     vix_live,
                "signal":    vix_label,
                "score":     vix_sig,
                "weight":    0.20,
                "type":      "LEADING",
            })

        # ── Crude signal ───────────────────────────────────────────────
        if crude_live > 0:
            if crude_live < 75:
                crude_sig, crude_label = 1.0, "SUPPORTIVE"
            elif crude_live < 95:
                crude_sig, crude_label = 0.65, "NEUTRAL"
            elif crude_live < 105:
                crude_sig, crude_label = 0.25, "STRESS"
            else:
                crude_sig, crude_label = 0.0, "EXTREME_STRESS"
            signals.append({
                "indicator": "Crude Oil",
                "value":     crude_live,
                "signal":    crude_label,
                "score":     crude_sig,
                "weight":    0.20,
                "type":      "LEADING",
            })

        # ── FII signal ─────────────────────────────────────────────────
        if fii_live is not None:
            if fii_live > 3000:
                fii_sig, fii_label = 1.0, "STRONG_BUYING"
            elif fii_live > 500:
                fii_sig, fii_label = 0.75, "BUYING"
            elif fii_live > -500:
                fii_sig, fii_label = 0.5, "NEUTRAL"
            elif fii_live > -3000:
                fii_sig, fii_label = 0.25, "SELLING"
            else:
                fii_sig, fii_label = 0.0, "HEAVY_SELLING"
            signals.append({
                "indicator": "FII Flows",
                "value":     fii_live,
                "signal":    fii_label,
                "score":     fii_sig,
                "weight":    0.25,
                "type":      "LEADING",
            })

        # ── Yield curve signal ─────────────────────────────────────────
        if yield_spread_india is not None:
            if yield_spread_india > 1.5:
                yc_sig, yc_label = 1.0, "STEEP_PRO_GROWTH"
            elif yield_spread_india > 0.5:
                yc_sig, yc_label = 0.75, "NORMAL"
            elif yield_spread_india >= 0:
                yc_sig, yc_label = 0.35, "FLAT_LATE_CYCLE"
            else:
                yc_sig, yc_label = 0.0, "INVERTED_WARNING"
            signals.append({
                "indicator": "India Yield Curve",
                "value":     round(yield_spread_india, 2),
                "signal":    yc_label,
                "score":     yc_sig,
                "weight":    0.20,
                "type":      "LEADING",
            })

        # ── PMI signal ─────────────────────────────────────────────────
        if pmi_level and pmi_level > 0:
            if pmi_level > 55:
                pmi_sig, pmi_label = 1.0, "STRONG_EXPANSION"
            elif pmi_level > 52:
                pmi_sig, pmi_label = 0.75, "HEALTHY_EXPANSION"
            elif pmi_level >= 50:
                pmi_sig, pmi_label = 0.5, "MARGINAL_EXPANSION"
            else:
                pmi_sig, pmi_label = 0.0, "CONTRACTION"
            signals.append({
                "indicator": "Manufacturing PMI",
                "value":     pmi_level,
                "signal":    pmi_label,
                "score":     pmi_sig,
                "weight":    0.15,
                "type":      "LEADING",
            })

        # ── PE ratio signal ────────────────────────────────────────────
        if nifty_pe > 0:
            if nifty_pe < 18:
                pe_sig, pe_label = 1.0, "CHEAP"
            elif nifty_pe < 22:
                pe_sig, pe_label = 0.75, "FAIR_VALUE"
            elif nifty_pe < 26:
                pe_sig, pe_label = 0.45, "STRETCHED"
            elif nifty_pe < 30:
                pe_sig, pe_label = 0.20, "EXPENSIVE"
            else:
                pe_sig, pe_label = 0.0, "BUBBLE_RISK"
            signals.append({
                "indicator": "Nifty P/E Ratio",
                "value":     nifty_pe,
                "signal":    pe_label,
                "score":     pe_sig,
                "weight":    0.15,
                "type":      "LEADING",
            })
            print(
                f"  [PE Signal] PE={nifty_pe} "
                f"label={pe_label} score={pe_sig}",
                flush=True
            )

        # ── GARCH volatility signal ────────────────────────────────────
        if garch_score is not None:
            signals.append({
                "indicator": "Vol Forecast (GARCH)",
                "value":     garch_score,
                "signal":    (
                    f"{garch_regime}_{garch_direction}"
                    if garch_regime and garch_direction
                    else "UNAVAILABLE"
                ),
                "score":     float(garch_score),
                "weight":    0.15,
                "type":      "LEADING",
            })
            print(
                f"  [GARCH Signal] regime={garch_regime} "
                f"direction={garch_direction} "
                f"score={garch_score}",
                flush=True
            )

        # ── Credit impulse signal ──────────────────────────────────────
        if credit_impulse_score is not None:
            signals.append({
                "indicator": "Credit Impulse",
                "value":     credit_impulse_score,
                "signal":    credit_impulse_trend or "STABLE",
                "score":     float(credit_impulse_score),
                "weight":    0.10,
                "type":      "LEADING",
            })
            print(
                f"  [Credit Impulse Signal] "
                f"trend={credit_impulse_trend} "
                f"score={credit_impulse_score}",
                flush=True
            )

        # ── FII trend signal ───────────────────────────────────────────
        if fii_trend_score is not None:
            signals.append({
                "indicator": "FII Trend (7d)",
                "value":     fii_trend_score,
                "signal":    fii_streak_dir or "MIXED",
                "score":     float(fii_trend_score),
                "weight":    0.15,
                "type":      "LEADING",
            })
            print(
                f"  [FII Trend Signal] "
                f"streak_dir={fii_streak_dir} "
                f"score={fii_trend_score}",
                flush=True
            )

        # ── Credit spread signal (Signal 10) ────────────────────────────
        # AAA corporate bond yield vs G-Sec 10Y — wider spread = stress
        if credit_spread_score is not None:
            signals.append({
                "indicator": "Credit Spread (AAA-GSec)",
                "value":     credit_spread_score,
                "signal":    credit_spread_signal or "UNKNOWN",
                "score":     float(credit_spread_score),
                "weight":    0.10,
                "type":      "LEADING",
            })
            print(
                f"  [Credit Spread Signal] "
                f"signal={credit_spread_signal} "
                f"score={credit_spread_score}",
                flush=True
            )

        # ── IS-LM composite signal ───────────────────────────────────────
        # Reads whether the real economy (IS/goods market) and monetary
        # conditions (LM/money market) are pulling the same direction —
        # see _compute_islm_composite() in main_api.py for the score.
        if islm_score is not None:
            signals.append({
                "indicator": "IS-LM Composite",
                "value":     islm_score,
                "signal":    islm_regime or "NEUTRAL",
                "score":     float(islm_score),
                "weight":    0.10,
                "type":      "LEADING",
            })
            print(
                f"  [ISLM Signal] "
                f"regime={islm_regime} "
                f"score={islm_score}",
                flush=True
            )

        # Compute signal entropy
        _entropy = self._compute_signal_entropy(
            signals
        )
        _entropy_adj = _entropy[
            "confidence_adj"
        ]

        # ── Weighted score ─────────────────────────────────────────────
        if signals:
            total_w = sum(s["weight"] for s in signals)
            score   = sum(s["score"] * s["weight"] for s in signals) / total_w
            score   = round(max(0.0, min(1.0, score)), 3)

        # Apply entropy adjustment
        # Entropy penalizes contradictory signals, rewards alignment
        score = round(
            max(0.0, min(1.0,
                score + _entropy_adj
            )), 3
        )

        # ── Trend from recent run confidence ───────────────────────────
        trend = "STABLE"
        if recent_runs and len(recent_runs) >= 3:
            recent_conf = [
                float(r.get("confidence", 0.5))
                for r in recent_runs[:3]
            ]
            if recent_conf[0] > recent_conf[2] + 0.03:
                trend = "BUILDING"
            elif recent_conf[0] < recent_conf[2] - 0.03:
                trend = "DETERIORATING"

        print(
            f"  [Leading] Score: {score:.3f} "
            f"entropy={_entropy['entropy']:.3f} "
            f"({_entropy['label']}) "
            f"signals: {len(signals)} "
            f"trend: {trend}",
            flush=True
        )
        return score, signals, trend, _entropy

    # =========================
    # ✅ ANTICIPATORY SIGNAL DETECTOR
    # Proactively flags forming patterns across consecutive runs.
    # Returns a dict with type, message, supporting_signals, etc.
    # =========================
    def _detect_anticipatory_signals(
        self,
        current_regime,
        leading_score,
        leading_signals,
        leading_trend,
        crude_live,
        vix_live,
        fii_live,
        recent_runs,
    ):
        supporting   = []
        stress_count = 0
        support_count = 0

        for sig in leading_signals:
            s = sig["score"]
            if s >= 0.65:
                support_count += 1
                supporting.append(
                    f"{sig['indicator']}: {sig['signal']}"
                )
            elif s <= 0.35:
                stress_count += 1
                supporting.append(
                    f"{sig['indicator']}: {sig['signal']} ⚠️"
                )

        # Pattern 1: DEPLOYMENT_WINDOW forming
        if (
            support_count >= 3
            and stress_count == 0
            and leading_trend == "BUILDING"
            and current_regime in (
                "LIQUIDITY_DRIVEN_EXPANSION",
                "EARLY_CYCLE_RECOVERY",
                "STABLE_GROWTH"
            )
        ):
            return {
                "type": "DEPLOYMENT_WINDOW",
                "message": (
                    f"{support_count} of {len(leading_signals)} "
                    "leading indicators aligned and building. "
                    "Conditions are forming for a high-conviction "
                    "deployment window. Consider pre-positioning "
                    "before confidence crosses HIGH threshold."
                ),
                "supporting_signals": supporting,
                "confidence_pct":     round(leading_score * 100),
                "action": (
                    "PRE-POSITION — leading indicators "
                    "strengthening ahead of lagging confirmation"
                ),
            }

        # Pattern 2: REGIME_FORMING — leading diverges from current
        if (
            stress_count >= 3
            and current_regime in (
                "LIQUIDITY_DRIVEN_EXPANSION",
                "STABLE_GROWTH"
            )
            and leading_trend == "DETERIORATING"
        ):
            return {
                "type": "REGIME_FORMING",
                "message": (
                    f"{stress_count} of {len(leading_signals)} "
                    "leading indicators now stressed. "
                    "Current regime classification may lag "
                    "actual conditions by 1-2 sessions. "
                    "Watch for regime transition signal."
                ),
                "supporting_signals": supporting,
                "confidence_pct":     round(leading_score * 100),
                "action": (
                    "REDUCE NEW POSITIONS — leading indicators "
                    "diverging from current regime classification"
                ),
            }

        # Pattern 3: STRESS_BUILDING — crude + FII + VIX all stressed
        if (
            crude_live > 100
            and fii_live is not None
            and fii_live < -1000
            and vix_live > 18
        ):
            return {
                "type": "STRESS_BUILDING",
                "message": (
                    f"Three stress signals active simultaneously: "
                    f"Crude at ${crude_live:.0f} (imported inflation), "
                    f"FII selling ₹{abs(fii_live):.0f} Cr "
                    f"(foreign outflow), "
                    f"VIX at {vix_live:.1f} (market fear). "
                    "This combination has historically preceded "
                    "regime transitions within 2-4 sessions."
                ),
                "supporting_signals": supporting,
                "confidence_pct":     round(leading_score * 100),
                "action": (
                    "HOLD — do not add new positions until "
                    "at least one stress signal resolves"
                ),
            }

        # Pattern 4: STABLE — balanced signals
        if abs(support_count - stress_count) <= 1:
            return {
                "type": "STABLE",
                "message": (
                    "Leading indicators are balanced — "
                    "no strong directional signal forming. "
                    "Current regime likely to persist."
                ),
                "supporting_signals": supporting,
                "confidence_pct":     round(leading_score * 100),
                "action": "HOLD current allocation",
            }

        # Pattern 5: CAUTION — mixed signals
        return {
            "type": "CAUTION",
            "message": (
                f"Mixed signals — {support_count} supportive, "
                f"{stress_count} stressed. "
                "Monitor next 2 sessions for directional clarity."
            ),
            "supporting_signals": supporting,
            "confidence_pct":     round(leading_score * 100),
            "action": (
                "SELECTIVE — maintain existing positions, "
                "defer new entries"
            ),
        }

    # =========================
    # ✅ DATA STALENESS PENALTY
    # GDP (quarterly) and CPI (monthly) lose confidence weight as they age.
    # Confidence drifts down between releases, spikes on fresh data.
    # Max penalty: 0.07 (CPI 0.03 + GDP 0.04).
    # =========================
    def _compute_data_staleness_penalty(self):
        """
        Returns a float [0.0, 0.07] to subtract from confidence.
        CPI: released ~12th of each month. Max penalty 0.03 at 30 days old.
        GDP: quarterly, published ~60 days after quarter-end. Max penalty 0.04 at 90+ days.
        """
        try:
            today = datetime.date.today()

            # CPI staleness
            cpi_release = today.replace(day=12)
            if today >= cpi_release:
                days_since_cpi = (today - cpi_release).days
            else:
                prev_month = (today.replace(day=1) - datetime.timedelta(days=1))
                days_since_cpi = (today - prev_month.replace(day=12)).days
            cpi_penalty = min(0.03, max(0, days_since_cpi) / 30 * 0.03)

            # GDP staleness
            m = today.month
            if   m <= 3:  qend = datetime.date(today.year - 1, 12, 31)
            elif m <= 6:  qend = datetime.date(today.year,      3, 31)
            elif m <= 9:  qend = datetime.date(today.year,      6, 30)
            else:         qend = datetime.date(today.year,      9, 30)
            # GDP is not yet published until ~60 days after quarter end
            days_since_gdp = max(0, (today - qend).days - 60)
            gdp_penalty = min(0.04, days_since_gdp / 90 * 0.04)

            penalty = round(cpi_penalty + gdp_penalty, 3)
            print(
                f"  [Regime] Data staleness: CPI={days_since_cpi}d ({cpi_penalty:.3f}) "
                f"GDP={days_since_gdp}d ({gdp_penalty:.3f}) total_penalty={penalty:.3f}",
                flush=True
            )
            return penalty
        except Exception as e:
            print(f"  [Regime] Staleness check skipped: {e}", flush=True)
            return 0.0

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
        fiscal_supportive,
        crude_live=0,
        vix_live=15,
        fii_live=None,
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

        # ── Live crude signal adjustments ────────────
        if crude_live >= 105:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] -= 1.5
            scores["STABLE_GROWTH"]              -= 1.0
            scores["EXTERNAL_SHOCK"] = scores.get("EXTERNAL_SHOCK", 0) + 2.0
            scores["STAGFLATION_RISK"]           += 1.5
            scores["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] += 1.5
        elif crude_live >= 95:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] -= 0.8
            scores["STABLE_GROWTH"]              -= 0.5
            scores["EXTERNAL_SHOCK"] = scores.get("EXTERNAL_SHOCK", 0) + 1.0
            scores["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] += 0.8
        elif 0 < crude_live <= 75:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] += 0.5
            scores["STABLE_GROWTH"]              += 0.5
            scores["EARLY_CYCLE_RECOVERY"]       += 0.3

        # ── Live VIX signal adjustments ──────────────
        if vix_live >= 25:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] -= 1.5
            scores["STABLE_GROWTH"]              -= 1.0
            scores["EXTERNAL_SHOCK"] = scores.get("EXTERNAL_SHOCK", 0) + 1.5
            scores["GROWTH_SLOWDOWN_SUPPORT"]    += 1.0
        elif vix_live >= 20:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] -= 0.8
            scores["STABLE_GROWTH"]              -= 0.5
            scores["EXTERNAL_SHOCK"] = scores.get("EXTERNAL_SHOCK", 0) + 0.8
        elif vix_live <= 12:
            scores["LIQUIDITY_DRIVEN_EXPANSION"] += 0.5
            scores["STABLE_GROWTH"]              += 0.5

        # ── Live FII signal adjustments ──────────────
        if fii_live is not None:
            if fii_live < -3000:
                scores["LIQUIDITY_DRIVEN_EXPANSION"] -= 1.2
                scores["STABLE_GROWTH"]              -= 0.8
                scores["GROWTH_SLOWDOWN_SUPPORT"]    += 0.8
                scores["EXTERNAL_SHOCK"] = scores.get("EXTERNAL_SHOCK", 0) + 0.5
            elif fii_live < -1000:
                scores["LIQUIDITY_DRIVEN_EXPANSION"] -= 0.5
                scores["STABLE_GROWTH"]              -= 0.3
            elif fii_live > 3000:
                scores["LIQUIDITY_DRIVEN_EXPANSION"] += 0.8
                scores["STABLE_GROWTH"]              += 0.5
                scores["EARLY_CYCLE_RECOVERY"]       += 0.3
            elif fii_live > 1000:
                scores["LIQUIDITY_DRIVEN_EXPANSION"] += 0.3
                scores["STABLE_GROWTH"]              += 0.2

        # Clamp all scores to valid range
        for k in scores:
            scores[k] = max(0.0, min(10.0, scores[k]))

        print(
            f"  [Regime] Live signal adjustments applied: "
            f"crude={crude_live} vix={vix_live} fii={fii_live}",
            flush=True
        )

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
    def detect_regime(self, intel, liquidity_output=None,
                      nse_snapshot=None, recent_runs=None):
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

        # ── Live market signals from nse_snapshot ────
        nse         = nse_snapshot or {}
        crude_live  = float(nse.get("crude_price") or 0)
        vix_live    = float(nse.get("india_vix")   or 15)
        fii_live    = (
            float(nse.get("fii_net_crore"))
            if nse.get("fii_net_crore") is not None
            else None
        )
        usd_inr     = float(nse.get("usd_inr")    or 0)

        print(
            f"  [Regime] Live signals: crude={crude_live} "
            f"vix={vix_live} fii={fii_live} inr={usd_inr}",
            flush=True
        )

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
        inflation_type    = self._inflation_driver(inflation, crude_live or None)
        external_risk     = self._external_risk(
            usd_inr or None,
            crude_live or None,
            india_risks,
            global_factors
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
            fiscal_supportive = fiscal_supportive,
            crude_live        = crude_live,
            vix_live          = vix_live,
            fii_live          = fii_live,
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

        rbi_signal = (
            rbi_data_out.get("policy_direction")
            or intel.get("rbi_policy_implication")
            or "PAUSE"
        )

        # -------------------------
        # Pick winning regime
        # -------------------------
        regime    = max(scores, key=scores.get)
        top_score = scores[regime]

        # -------------------------
        # Score ordering (shared by confidence + challenger)
        # -------------------------
        sorted_scores  = sorted(scores.values(), reverse=True)
        sorted_regimes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        challenger     = sorted_regimes[1][0] if len(sorted_regimes) > 1 else None

        # -------------------------
        # Confidence — three-state signal alignment pipeline
        #
        # Band mapping:
        #   alignment 1.0 → base 0.92  (all primaries strongly agree)
        #   alignment 0.7 → base 0.76  (most agree, a few neutral)
        #   alignment 0.5 → base 0.66  (mix of strong + neutral)
        #   alignment 0.3 → base 0.56  (majority neutral/weak)
        #   alignment 0.0 → base 0.40  (signals disagree)
        # -------------------------
        alignment, signal_details = self._compute_signal_alignment(
            regime            = regime,
            policy_stance     = policy_stance,
            liquidity_score   = liquidity_score,
            growth            = growth,
            inflation         = inflation,
            sentiment         = sentiment,
            equity_bias       = equity_bias,
            nlp_regime        = nlp_regime,
            rbi_signal        = rbi_signal,
            fiscal_supportive = fiscal_supportive,
            crude_live        = crude_live,
            vix_live          = vix_live,
            fii_live          = fii_live,
        )
        base_conf = 0.40 + alignment * 0.52

        # NLP blend: LLM source is more reliable — pull 30% toward nlp_confidence
        if nlp_source == "llm+keyword":
            confidence = round(0.70 * base_conf + 0.30 * nlp_confidence, 4)
        else:
            confidence = round(base_conf * 0.93, 4)

        # Challenger inverse pressure: a strong challenger regime
        # mathematically reduces primary regime confidence
        primary_score_val    = sorted_scores[0] if sorted_scores else 1.0
        challenger_score_val = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
        challenger_ratio     = challenger_score_val / max(primary_score_val, 0.001)
        challenger_pressure  = round(min(0.10, challenger_ratio * 0.12), 4)
        confidence          -= challenger_pressure

        # Challenger delta in raw score points
        # If winner beats runner-up by < 1.0pt the regime classification is unstable
        challenger_delta  = round(primary_score_val - challenger_score_val, 2)
        regime_is_unstable = challenger_delta < 1.0
        # Note: scores are on a 0-10 scale, so 1.0 point = 10% gap threshold

        if regime_is_unstable:
            print(
                f"  [UNSTABLE] Regime gap only "
                f"{challenger_delta:.2f} pts "
                f"({regime} vs {challenger}) — "
                f"flagging as unstable",
                flush=True
            )

        # Time decay: GDP (quarterly) and CPI (monthly) erode confidence
        # as they age; fresh release = spike back up
        staleness_penalty = self._compute_data_staleness_penalty()
        confidence       -= staleness_penalty

        print(
            f"  [Regime] Confidence pipeline: base={base_conf:.3f} "
            f"post_nlp_blend={confidence + challenger_pressure + staleness_penalty:.3f} "
            f"challenger_pressure={challenger_pressure:.3f} "
            f"staleness={staleness_penalty:.3f} "
            f"pre_persistence={confidence:.3f}",
            flush=True
        )

        # -------------------------
        # ✅ SINGLE SUPABASE FETCH
        # Fetches recent runs ONCE and passes the result to
        # both the persistence scorer and change detector.
        # Prevents two round-trips to Supabase per pipeline run.
        # -------------------------
        # Use pre-fetched recent_runs if provided (from main_api.py
        # which has working Supabase). Fall back to own fetch only
        # if not provided.
        if recent_runs is None:
            recent_runs = []
            if self._sb_url and self._sb_key:
                recent_runs = (
                    _fetch_recent_runs(
                        self._sb_url,
                        self._sb_key,
                        limit=10
                    )
                )
        print(
            f"[CONF_SMOOTH] recent_runs "
            f"available: {len(recent_runs)}",
            flush=True
        )

        # ── Leading indicator score ──────────────
        leading_score   = 0.5
        leading_signals = []
        leading_trend   = "STABLE"
        leading_entropy = {
            "entropy":        1.0,
            "label":          "UNCERTAIN",
            "confidence_adj": -0.05,
            "interpretation": "No signals available",
        }
        anticipatory    = {
            "type":               "STABLE",
            "message":            "Leading signals unavailable.",
            "supporting_signals": [],
            "confidence_pct":     50,
            "action":             "HOLD current allocation",
        }
        try:
            _yield_spread = float(
                intel.get("yield_spread_india", 0.25) or 0.25
            )
            _pmi = float(
                hard_data.get("pmi", 0) or 0
            )
            leading_score, leading_signals, leading_trend, leading_entropy = \
                self._compute_leading_score(
                    regime               = regime,
                    crude_live           = crude_live,
                    vix_live             = vix_live,
                    fii_live             = fii_live,
                    liquidity_score      = liquidity_score,
                    yield_spread_india   = _yield_spread,
                    pmi_level            = _pmi,
                    recent_runs          = recent_runs,
                    nifty_pe             = float(intel.get("nifty_pe", 0) or 0),
                    garch_score          = intel.get("garch_score"),
                    garch_regime         = intel.get("garch_regime"),
                    garch_direction      = intel.get("garch_direction"),
                    credit_impulse_score = intel.get("credit_impulse_score"),
                    credit_impulse_trend = intel.get("credit_impulse_trend"),
                    fii_trend_score      = intel.get("fii_trend_score"),
                    fii_streak_dir       = intel.get("fii_streak_dir"),
                    credit_spread_score  = intel.get("credit_spread", {}).get("score")
                                          if isinstance(intel.get("credit_spread"), dict)
                                          else None,
                    credit_spread_signal = intel.get("credit_spread", {}).get("signal")
                                          if isinstance(intel.get("credit_spread"), dict)
                                          else None,
                )
            leading_adj = round(
                max(-0.05, min(0.05,
                    (leading_score - 0.5) * 0.10
                )), 4
            )
            confidence = round(confidence + leading_adj, 4)
            print(
                f"  [Leading] Score={leading_score:.3f} "
                f"adj={leading_adj:+.3f} "
                f"new_conf={confidence:.3f}",
                flush=True
            )
            anticipatory = self._detect_anticipatory_signals(
                current_regime  = regime,
                leading_score   = leading_score,
                leading_signals = leading_signals,
                leading_trend   = leading_trend,
                crude_live      = crude_live,
                vix_live        = vix_live,
                fii_live        = fii_live,
                recent_runs     = recent_runs,
            )
            print(
                f"  [Anticipatory] type={anticipatory['type']} "
                f"action={anticipatory['action'][:40]}",
                flush=True
            )
        except Exception as _lead_err:
            print(
                f"  [Leading] ERROR: {_lead_err}",
                flush=True
            )

        # -------------------------
        # ✅ REGIME PERSISTENCE ADJUSTMENT
        # -------------------------
        persistence_adj, consecutive_days = \
            self._regime_persistence_adjustment(regime, recent_runs)

        confidence = round(
            max(0.40, min(0.95, confidence + persistence_adj)), 2
        )

        # Store raw for all internal logic (gates, change detection)
        _raw_conf = confidence
        # Smooth for display only — does not affect scoring or gates
        confidence = self._smooth_confidence(
            confidence,
            recent_runs or []
        )

        # -------------------------
        # ✅ REGIME CHANGE DETECTION
        # Runs after final confidence is calculated so the
        # change check uses the true final confidence value.
        # -------------------------
        change_info = self._detect_regime_change(
            current_regime      = regime,
            current_confidence  = _raw_conf,
            recent_runs         = recent_runs
        )

        # -------------------------
        # EQUITY BIAS OVERRIDE
        # -------------------------
        REGIME_BIAS_MAP = {
            "LIQUIDITY_DRIVEN_EXPANSION":              0.7,
            "EARLY_CYCLE_RECOVERY":                    0.75,
            "STABLE_GROWTH":                           0.6,
            "GROWTH_SLOWDOWN_SUPPORT":                 0.35,
            "MONETARY_TIGHTENING":                     0.2,
            "LIQUIDITY_TIGHTENING":                    0.2,
            "STAGFLATION_RISK":                        0.1,
            "INFLATION_PRESSURE_WITH_EXTERNAL_RISK":   0.15,
        }

        nlp_score    = {"RISK_ON": 1.0, "NEUTRAL": 0.5, "RISK_OFF": 0.0}.get(equity_bias, 0.5)
        regime_score = REGIME_BIAS_MAP.get(regime, 0.5)
        blend        = (nlp_score * 0.4) + (regime_score * 0.6)

        if blend >= 0.65:
            equity_bias = "RISK_ON"
        elif blend <= 0.35:
            equity_bias = "RISK_OFF"
        else:
            equity_bias = "NEUTRAL"

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
        if _raw_conf < 0.60:
            print(
                f"  [GATE] Briefing BLOCKED — "
                f"raw_conf {_raw_conf:.0%} below 0.60 gate",
                flush=True
            )
        else:
            print(
                f"  [GATE] Briefing ALLOWED — "
                f"raw_conf {_raw_conf:.0%} above 0.60 gate",
                flush=True
            )

        return {
            # ── Premortem safety gates ──────────────
            "briefing_allowed": _raw_conf >= 0.60,
            "briefing_blocked_reason": (
                None if _raw_conf >= 0.60
                else f"Confidence {_raw_conf:.0%} below 60% gate — "
                     f"briefing suppressed to prevent low-conviction advice"
            ),

            "regime":         regime,
            "confidence":     confidence,
            "raw_confidence": _raw_conf,
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
            "challenger_confidence": round(challenger_ratio, 3),

            "regime_stability_flag": {
                "is_unstable":      regime_is_unstable,
                "challenger_delta": challenger_delta,
                "primary_score":    round(primary_score_val, 2),
                "challenger_score": round(challenger_score_val, 2),
                "warning": (
                    f"Regime gap {challenger_delta:.1f}pts — "
                    f"below 1.0pt stability threshold. "
                    f"Hold briefing or add instability warning."
                    if regime_is_unstable else None
                ),
            },

            "drivers":       drivers,
            "rbi_data":      rbi_data_out,

            # Signal alignment breakdown — used for diagnostics and dashboard
            "signal_alignment": {
                "score":               round(alignment, 3),
                "details":             signal_details,
                "challenger_pressure": challenger_pressure,
                "staleness_penalty":   staleness_penalty,
            },

            # ✅ Change detection flows downstream
            # scheduler.py reads this to decide whether to fire alerts
            # main.py can read this to show an in-app banner
            "change_info": change_info,

            # ✅ Leading intelligence — fast-moving signal breakdown
            "leading_intelligence": {
                "score":   leading_score,
                "signals": leading_signals,
                "trend":   leading_trend,
            },

            # ✅ Signal entropy — Shannon entropy across leading signals,
            # measures alignment quality (used as a confidence modifier)
            "signal_entropy": leading_entropy,

            # ✅ Anticipatory signal — proactive pattern detection
            "anticipatory": anticipatory,

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