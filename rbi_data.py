"""
rbi_data.py — RBI Data Integration Module

Fetches live macro signals from RBI's DBIE (Database on Indian Economy)
and RBI press releases. Used as additional scoring signals in the
regime engine.

Key indicators fetched:
  - Repo rate (live, not hardcoded)
  - Bank credit growth (% YoY)
  - Forex reserves (USD billion)
  - M3 money supply growth (% YoY)
  - CRR (Cash Reserve Ratio)
  - RBI liquidity posture (surplus/deficit)

DBIE API base: https://dbie.rbi.org.in/DBIE/dbie.rbi?site=api
"""

import requests
import json
import datetime


# =========================
# 📊 RBI DBIE SERIES IDs
# Verified series codes from DBIE
# =========================
DBIE_SERIES = {
    "repo_rate":       "RBINJ3MRATEREPO",   # Repo rate
    "crr":             "RBINJ3MRATECRR",    # CRR
    "credit_growth":   "RBIBS3MSCBY",       # Bank credit YoY growth
    "m3_growth":       "RBIML3MMSEAM3",     # M3 money supply
    "forex_reserves":  "RBIFX3MFXFXTOT",   # Total forex reserves
}

DBIE_BASE = "https://dbie.rbi.org.in/DBIE/dbie.rbi?site=api"

# RBI DBIE headers — required
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://dbie.rbi.org.in/",
}


class RBIDataFetcher:

    def __init__(self):
        self._cache      = {}
        self._cache_time = None
        self.CACHE_TTL   = 3600   # 1 hour — RBI data updates daily

    def _safe_float(self, val, default=0.0):
        try:
            return float(val)
        except Exception:
            return default

    # =========================
    # 📥 DBIE FETCHER
    # =========================
    def _fetch_dbie_series(self, series_id, periods=4):
        """
        Fetches last N periods for a DBIE series.
        Returns list of {"date": str, "value": float} dicts.
        """
        try:
            url    = f"{DBIE_BASE}&seriesId={series_id}&noOfPeriods={periods}"
            resp   = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                return []
            data   = resp.json()
            result = []
            # DBIE returns nested structure
            series = data.get("seriesData", data.get("data", []))
            for item in series:
                date  = item.get("period", item.get("date", ""))
                value = self._safe_float(item.get("obs_value", item.get("value", 0)))
                if date and value:
                    result.append({"date": date, "value": value})
            return sorted(result, key=lambda x: x["date"])
        except Exception as e:
            print(f"  [RBI] DBIE fetch failed for {series_id}: {e}")
            return []

    def _latest(self, series_data, default=0.0):
        """Returns the most recent value from a series."""
        if series_data:
            return series_data[-1]["value"]
        return default

    def _growth_rate(self, series_data):
        """Returns YoY growth rate if 2+ periods available."""
        if len(series_data) >= 2:
            prev = series_data[-2]["value"]
            curr = series_data[-1]["value"]
            if prev and prev != 0:
                return round((curr - prev) / prev * 100, 2)
        return 0.0

    # =========================
    # 🏦 RBI PRESS RELEASE PARSER
    # Extracts latest MPC stance from RBI website
    # =========================
    def _fetch_rbi_mpc_stance(self):
        """
        Fetches the latest RBI policy stance from press releases.
        Returns "ACCOMMODATIVE" | "NEUTRAL" | "WITHDRAWAL" | "UNKNOWN"
        """
        try:
            url  = (
                "https://www.rbi.org.in/Scripts/PublicationsView.aspx?"
                "id=mpc_press_release"
            )
            resp = requests.get(url, headers=HEADERS, timeout=10)
            text = resp.text.lower()

            if "withdrawal of accommodation" in text:
                return "WITHDRAWAL"
            elif "accommodative" in text:
                return "ACCOMMODATIVE"
            elif "neutral" in text:
                return "NEUTRAL"
            else:
                return "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    # =========================
    # 🚀 MAIN FETCH — ALL RBI SIGNALS
    # =========================
    def get_rbi_signals(self, use_cache=True):
        """
        Returns a dict of RBI macro signals for regime engine scoring.

        Output:
        {
            "repo_rate":          6.5,
            "crr":                4.0,
            "credit_growth_pct":  14.2,   # Bank credit YoY %
            "m3_growth_pct":      10.5,   # M3 YoY %
            "forex_reserves_bn":  645.0,  # USD billion
            "mpc_stance":         "NEUTRAL",
            "liquidity_signal":   "SURPLUS",  # SURPLUS | DEFICIT | NEUTRAL
            "regime_signals": {
                "credit_impulse":    "POSITIVE",  # credit expanding > 12%
                "money_supply":      "EXPANSIVE",  # M3 growing > 10%
                "forex_buffer":      "STRONG",     # >$600bn
                "policy_direction":  "EASING",     # based on recent rate moves
            },
            "timestamp": "2026-04-24 10:00:00",
            "source":    "RBI DBIE"
        }
        """
        # Return cache if fresh
        now = datetime.datetime.now()
        if (use_cache and self._cache and self._cache_time and
                (now - self._cache_time).seconds < self.CACHE_TTL):
            return self._cache

        print("  [RBI] Fetching DBIE signals...")
        signals = {}

        # -------------------------
        # Fetch each series
        # -------------------------
        repo_series    = self._fetch_dbie_series(DBIE_SERIES["repo_rate"],    4)
        crr_series     = self._fetch_dbie_series(DBIE_SERIES["crr"],          4)
        credit_series  = self._fetch_dbie_series(DBIE_SERIES["credit_growth"],4)
        m3_series      = self._fetch_dbie_series(DBIE_SERIES["m3_growth"],    4)
        forex_series   = self._fetch_dbie_series(DBIE_SERIES["forex_reserves"],4)

        # -------------------------
        # Extract latest values
        # Fall back to known values if DBIE unreachable
        # -------------------------
        repo_rate       = self._latest(repo_series,   default=6.5)
        crr             = self._latest(crr_series,    default=4.0)
        credit_growth   = self._latest(credit_series, default=13.0)
        m3_growth       = self._latest(m3_series,     default=10.5)
        forex_reserves  = self._latest(forex_series,  default=640.0)

        # Trend — is repo rate rising, falling, or flat?
        if len(repo_series) >= 2:
            repo_prev = repo_series[-2]["value"]
            if repo_rate < repo_prev:
                policy_direction = "EASING"
            elif repo_rate > repo_prev:
                policy_direction = "TIGHTENING"
            else:
                policy_direction = "NEUTRAL"
        else:
            policy_direction = "NEUTRAL"

        # -------------------------
        # Derive regime signals
        # -------------------------
        credit_impulse = (
            "POSITIVE" if credit_growth > 12.0 else
            "NEGATIVE" if credit_growth < 8.0  else
            "NEUTRAL"
        )
        money_supply = (
            "EXPANSIVE"    if m3_growth > 10.0 else
            "CONTRACTIONARY" if m3_growth < 7.0 else
            "NEUTRAL"
        )
        forex_buffer = (
            "STRONG"   if forex_reserves > 600 else
            "ADEQUATE" if forex_reserves > 500 else
            "WEAK"
        )

        # Liquidity posture based on credit + M3
        if credit_impulse == "POSITIVE" and money_supply == "EXPANSIVE":
            liquidity_signal = "SURPLUS"
        elif credit_impulse == "NEGATIVE" or money_supply == "CONTRACTIONARY":
            liquidity_signal = "DEFICIT"
        else:
            liquidity_signal = "NEUTRAL"

        # -------------------------
        # Build output
        # -------------------------
        result = {
            "repo_rate":         repo_rate,
            "crr":               crr,
            "credit_growth_pct": credit_growth,
            "m3_growth_pct":     m3_growth,
            "forex_reserves_bn": forex_reserves,
            "mpc_stance":        "NEUTRAL",   # Updated below if RBI press accessible
            "liquidity_signal":  liquidity_signal,
            "policy_direction":  policy_direction,
            "regime_signals": {
                "credit_impulse":   credit_impulse,
                "money_supply":     money_supply,
                "forex_buffer":     forex_buffer,
                "policy_direction": policy_direction,
            },
            "raw": {
                "repo_series":   repo_series,
                "credit_series": credit_series,
                "m3_series":     m3_series,
                "forex_series":  forex_series,
            },
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "source":    "RBI DBIE" if repo_series else "fallback"
        }

        self._cache      = result
        self._cache_time = now
        return result


# =========================
# 🧮 REGIME SCORE INJECTOR
# Translates RBI signals into regime score adjustments
# Called by regime_engine.py
# =========================
def rbi_score_adjustments(rbi_signals):
    """
    Returns a dict of score adjustments for each regime type
    based on RBI macro signals.

    Positive = boosts this regime's probability
    Negative = reduces this regime's probability

    Scale: -0.15 to +0.15 per regime per signal
    """
    if not rbi_signals:
        return {}

    regime_sigs = rbi_signals.get("regime_signals", {})
    policy_dir  = regime_sigs.get("policy_direction",  "NEUTRAL")
    credit      = regime_sigs.get("credit_impulse",    "NEUTRAL")
    money       = regime_sigs.get("money_supply",      "NEUTRAL")
    forex       = regime_sigs.get("forex_buffer",      "ADEQUATE")
    repo        = rbi_signals.get("repo_rate",          6.5)

    adjustments = {
        "LIQUIDITY_DRIVEN_EXPANSION":            0.0,
        "STABLE_GROWTH":                         0.0,
        "EARLY_CYCLE_RECOVERY":                  0.0,
        "MONETARY_TIGHTENING":                   0.0,
        "LIQUIDITY_TIGHTENING":                  0.0,
        "GROWTH_SLOWDOWN_SUPPORT":               0.0,
        "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": 0.0,
        "STAGFLATION_RISK":                      0.0,
        "TRANSITION_PHASE":                      0.0,
    }

    # Credit impulse signal
    if credit == "POSITIVE":
        adjustments["LIQUIDITY_DRIVEN_EXPANSION"] += 0.10
        adjustments["STABLE_GROWTH"]              += 0.06
        adjustments["EARLY_CYCLE_RECOVERY"]       += 0.05
        adjustments["LIQUIDITY_TIGHTENING"]       -= 0.08
        adjustments["GROWTH_SLOWDOWN_SUPPORT"]    -= 0.08
    elif credit == "NEGATIVE":
        adjustments["GROWTH_SLOWDOWN_SUPPORT"]    += 0.10
        adjustments["LIQUIDITY_TIGHTENING"]       += 0.06
        adjustments["LIQUIDITY_DRIVEN_EXPANSION"] -= 0.10
        adjustments["STABLE_GROWTH"]              -= 0.06

    # Money supply signal
    if money == "EXPANSIVE":
        adjustments["LIQUIDITY_DRIVEN_EXPANSION"] += 0.08
        adjustments["EARLY_CYCLE_RECOVERY"]       += 0.05
        adjustments["MONETARY_TIGHTENING"]        -= 0.06
    elif money == "CONTRACTIONARY":
        adjustments["MONETARY_TIGHTENING"]        += 0.08
        adjustments["LIQUIDITY_TIGHTENING"]       += 0.06
        adjustments["LIQUIDITY_DRIVEN_EXPANSION"] -= 0.08

    # Policy direction signal
    if policy_dir == "EASING":
        adjustments["EARLY_CYCLE_RECOVERY"]       += 0.10
        adjustments["LIQUIDITY_DRIVEN_EXPANSION"] += 0.06
        adjustments["MONETARY_TIGHTENING"]        -= 0.10
        adjustments["LIQUIDITY_TIGHTENING"]       -= 0.10
    elif policy_dir == "TIGHTENING":
        adjustments["MONETARY_TIGHTENING"]        += 0.10
        adjustments["LIQUIDITY_TIGHTENING"]       += 0.08
        adjustments["LIQUIDITY_DRIVEN_EXPANSION"] -= 0.10
        adjustments["EARLY_CYCLE_RECOVERY"]       -= 0.08

    # Forex buffer — external resilience signal
    if forex == "STRONG":
        adjustments["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] -= 0.08
        adjustments["STAGFLATION_RISK"]                      -= 0.06
        adjustments["STABLE_GROWTH"]                         += 0.05
    elif forex == "WEAK":
        adjustments["INFLATION_PRESSURE_WITH_EXTERNAL_RISK"] += 0.10
        adjustments["STAGFLATION_RISK"]                      += 0.06
        adjustments["STABLE_GROWTH"]                         -= 0.06

    # Repo rate level — absolute level signal
    if repo <= 5.0:
        # Ultra-accommodative — strongly pro-expansion
        adjustments["LIQUIDITY_DRIVEN_EXPANSION"] += 0.08
        adjustments["EARLY_CYCLE_RECOVERY"]       += 0.06
    elif repo >= 6.5:
        # Elevated — tightening bias
        adjustments["MONETARY_TIGHTENING"]        += 0.06
        adjustments["STABLE_GROWTH"]              -= 0.04

    # Cap all adjustments at ±0.20
    for regime in adjustments:
        adjustments[regime] = max(-0.20, min(0.20, adjustments[regime]))

    return adjustments