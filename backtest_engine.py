"""
BacktestEngine — SENTINEL Historical Validation Module

Validates regime detection accuracy, allocation performance,
and signal timing over a 5-year historical window (2020-2025).

Data sources: Yahoo Finance (yfinance) + hardcoded RBI/MOSPI history
Rebalancing:  Monthly
Benchmark:    Nifty 50 buy-and-hold
"""

import pandas as pd
import numpy as np
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta


# =========================
# 📊 GROUND TRUTH REGIMES
# =========================
GROUND_TRUTH_REGIMES = {
    "2020-01": "GROWTH_SLOWDOWN_SUPPORT",
    "2020-02": "GROWTH_SLOWDOWN_SUPPORT",
    "2020-03": "GROWTH_SLOWDOWN_SUPPORT",
    "2020-04": "GROWTH_SLOWDOWN_SUPPORT",
    "2020-05": "GROWTH_SLOWDOWN_SUPPORT",
    "2020-06": "GROWTH_SLOWDOWN_SUPPORT",
    "2020-07": "EARLY_CYCLE_RECOVERY",
    "2020-08": "EARLY_CYCLE_RECOVERY",
    "2020-09": "EARLY_CYCLE_RECOVERY",
    "2020-10": "EARLY_CYCLE_RECOVERY",
    "2020-11": "EARLY_CYCLE_RECOVERY",
    "2020-12": "EARLY_CYCLE_RECOVERY",
    "2021-01": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-02": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-03": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-04": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-05": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-06": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-07": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-08": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-09": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-10": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-11": "LIQUIDITY_DRIVEN_EXPANSION",
    "2021-12": "LIQUIDITY_DRIVEN_EXPANSION",
    "2022-01": "MONETARY_TIGHTENING",
    "2022-02": "MONETARY_TIGHTENING",
    "2022-03": "MONETARY_TIGHTENING",
    "2022-04": "MONETARY_TIGHTENING",
    "2022-05": "LIQUIDITY_TIGHTENING",
    "2022-06": "LIQUIDITY_TIGHTENING",
    "2022-07": "LIQUIDITY_TIGHTENING",
    "2022-08": "LIQUIDITY_TIGHTENING",
    "2022-09": "LIQUIDITY_TIGHTENING",
    "2022-10": "LIQUIDITY_TIGHTENING",
    "2022-11": "LIQUIDITY_TIGHTENING",
    "2022-12": "LIQUIDITY_TIGHTENING",
    "2023-01": "MONETARY_TIGHTENING",
    "2023-02": "MONETARY_TIGHTENING",
    "2023-03": "MONETARY_TIGHTENING",
    "2023-04": "MONETARY_TIGHTENING",
    "2023-05": "STABLE_GROWTH",
    "2023-06": "STABLE_GROWTH",
    "2023-07": "STABLE_GROWTH",
    "2023-08": "STABLE_GROWTH",
    "2023-09": "STABLE_GROWTH",
    "2023-10": "STABLE_GROWTH",
    "2023-11": "STABLE_GROWTH",
    "2023-12": "STABLE_GROWTH",
    "2024-01": "EARLY_CYCLE_RECOVERY",
    "2024-02": "EARLY_CYCLE_RECOVERY",
    "2024-03": "EARLY_CYCLE_RECOVERY",
    "2024-04": "EARLY_CYCLE_RECOVERY",
    "2024-05": "LIQUIDITY_DRIVEN_EXPANSION",
    "2024-06": "LIQUIDITY_DRIVEN_EXPANSION",
    "2024-07": "LIQUIDITY_DRIVEN_EXPANSION",
    "2024-08": "LIQUIDITY_DRIVEN_EXPANSION",
    "2024-09": "LIQUIDITY_DRIVEN_EXPANSION",
    "2024-10": "LIQUIDITY_DRIVEN_EXPANSION",
    "2024-11": "LIQUIDITY_DRIVEN_EXPANSION",
    "2024-12": "LIQUIDITY_DRIVEN_EXPANSION",
    "2025-01": "LIQUIDITY_DRIVEN_EXPANSION",
    "2025-02": "LIQUIDITY_DRIVEN_EXPANSION",
    "2025-03": "LIQUIDITY_DRIVEN_EXPANSION",
    "2025-04": "STABLE_GROWTH",
    "2025-05": "STABLE_GROWTH",
    "2025-06": "STABLE_GROWTH",
    "2025-07": "STABLE_GROWTH",
    "2025-08": "STABLE_GROWTH",
    "2025-09": "STABLE_GROWTH",
    "2025-10": "STABLE_GROWTH",
    "2025-11": "STABLE_GROWTH",
    "2025-12": "STABLE_GROWTH",
}

# =========================
# 🏦 RBI REPO RATE HISTORY
# =========================
RBI_REPO_HISTORY = {
    "2020-01": 5.15, "2020-02": 5.15, "2020-03": 5.15,
    "2020-04": 4.40, "2020-05": 4.00, "2020-06": 4.00,
    "2020-07": 4.00, "2020-08": 4.00, "2020-09": 4.00,
    "2020-10": 4.00, "2020-11": 4.00, "2020-12": 4.00,
    "2021-01": 4.00, "2021-02": 4.00, "2021-03": 4.00,
    "2021-04": 4.00, "2021-05": 4.00, "2021-06": 4.00,
    "2021-07": 4.00, "2021-08": 4.00, "2021-09": 4.00,
    "2021-10": 4.00, "2021-11": 4.00, "2021-12": 4.00,
    "2022-01": 4.00, "2022-02": 4.00, "2022-03": 4.00,
    "2022-04": 4.00, "2022-05": 4.40, "2022-06": 4.90,
    "2022-07": 4.90, "2022-08": 5.40, "2022-09": 5.90,
    "2022-10": 5.90, "2022-11": 5.90, "2022-12": 6.25,
    "2023-01": 6.25, "2023-02": 6.25, "2023-03": 6.50,
    "2023-04": 6.50, "2023-05": 6.50, "2023-06": 6.50,
    "2023-07": 6.50, "2023-08": 6.50, "2023-09": 6.50,
    "2023-10": 6.50, "2023-11": 6.50, "2023-12": 6.50,
    "2024-01": 6.50, "2024-02": 6.50, "2024-03": 6.50,
    "2024-04": 6.50, "2024-05": 6.50, "2024-06": 6.50,
    "2024-07": 6.50, "2024-08": 6.50, "2024-09": 6.50,
    "2024-10": 6.50, "2024-11": 6.50, "2024-12": 6.50,
    "2025-01": 6.25, "2025-02": 6.25, "2025-03": 6.00,
    "2025-04": 6.00, "2025-05": 6.00, "2025-06": 6.00,
    "2025-07": 6.00, "2025-08": 6.00, "2025-09": 6.00,
    "2025-10": 6.00, "2025-11": 6.00, "2025-12": 6.00,
}

# =========================
# 📈 INDIA CPI HISTORY (% YoY)
# =========================
INDIA_CPI_HISTORY = {
    "2020-01": 7.59, "2020-02": 6.58, "2020-03": 5.91,
    "2020-04": 5.84, "2020-05": 6.27, "2020-06": 6.23,
    "2020-07": 6.73, "2020-08": 6.69, "2020-09": 7.27,
    "2020-10": 7.61, "2020-11": 6.93, "2020-12": 4.59,
    "2021-01": 4.06, "2021-02": 5.03, "2021-03": 5.52,
    "2021-04": 4.23, "2021-05": 6.30, "2021-06": 6.26,
    "2021-07": 5.59, "2021-08": 5.30, "2021-09": 4.35,
    "2021-10": 4.48, "2021-11": 4.91, "2021-12": 5.59,
    "2022-01": 6.01, "2022-02": 6.07, "2022-03": 6.95,
    "2022-04": 7.79, "2022-05": 7.04, "2022-06": 7.01,
    "2022-07": 6.71, "2022-08": 7.00, "2022-09": 7.41,
    "2022-10": 6.77, "2022-11": 5.88, "2022-12": 5.72,
    "2023-01": 6.52, "2023-02": 6.44, "2023-03": 5.66,
    "2023-04": 4.70, "2023-05": 4.25, "2023-06": 4.81,
    "2023-07": 7.44, "2023-08": 6.83, "2023-09": 5.02,
    "2023-10": 4.87, "2023-11": 5.55, "2023-12": 5.69,
    "2024-01": 5.10, "2024-02": 5.09, "2024-03": 4.85,
    "2024-04": 4.83, "2024-05": 4.75, "2024-06": 5.08,
    "2024-07": 3.54, "2024-08": 3.65, "2024-09": 5.49,
    "2024-10": 6.21, "2024-11": 5.48, "2024-12": 5.22,
    "2025-01": 4.26, "2025-02": 3.61, "2025-03": 3.34,
    "2025-04": 3.16, "2025-05": 3.16, "2025-06": 3.16,
    "2025-07": 3.16, "2025-08": 3.16, "2025-09": 3.16,
    "2025-10": 3.16, "2025-11": 3.16, "2025-12": 3.16,
}

# =========================
# 📊 INDIA GDP HISTORY (% YoY)
# =========================
INDIA_GDP_HISTORY = {
    "2020-01":  4.10, "2020-02":  4.10, "2020-03":  3.10,
    "2020-04":-23.90, "2020-05":-23.90, "2020-06":-23.90,
    "2020-07": -7.40, "2020-08": -7.40, "2020-09": -7.40,
    "2020-10":  0.40, "2020-11":  0.40, "2020-12":  0.40,
    "2021-01":  1.60, "2021-02":  1.60, "2021-03":  1.60,
    "2021-04": -7.30, "2021-05": -7.30, "2021-06": -7.30,
    "2021-07": 20.10, "2021-08": 20.10, "2021-09": 20.10,
    "2021-10":  8.40, "2021-11":  8.40, "2021-12":  8.40,
    "2022-01":  4.10, "2022-02":  4.10, "2022-03":  4.10,
    "2022-04": 13.50, "2022-05": 13.50, "2022-06": 13.50,
    "2022-07":  6.30, "2022-08":  6.30, "2022-09":  6.30,
    "2022-10":  4.40, "2022-11":  4.40, "2022-12":  4.40,
    "2023-01":  6.10, "2023-02":  6.10, "2023-03":  6.10,
    "2023-04":  7.80, "2023-05":  7.80, "2023-06":  7.80,
    "2023-07":  7.60, "2023-08":  7.60, "2023-09":  7.60,
    "2023-10":  8.40, "2023-11":  8.40, "2023-12":  8.40,
    "2024-01":  7.80, "2024-02":  7.80, "2024-03":  7.80,
    "2024-04":  6.70, "2024-05":  6.70, "2024-06":  6.70,
    "2024-07":  6.70, "2024-08":  6.70, "2024-09":  6.70,
    "2024-10":  6.40, "2024-11":  6.40, "2024-12":  6.40,
    "2025-01":  6.50, "2025-02":  6.50, "2025-03":  6.50,
    "2025-04":  7.00, "2025-05":  7.00, "2025-06":  7.00,
    "2025-07":  7.00, "2025-08":  7.00, "2025-09":  7.00,
    "2025-10":  7.00, "2025-11":  7.00, "2025-12":  7.00,
}

# =========================
# 📊 REGIME → ALLOCATION MAP
# =========================
REGIME_ALLOCATION = {
    "LIQUIDITY_DRIVEN_EXPANSION":            {"equity": 0.65, "bonds": 0.15, "gold": 0.10, "cash": 0.10},
    "STABLE_GROWTH":                         {"equity": 0.60, "bonds": 0.20, "gold": 0.10, "cash": 0.10},
    "MONETARY_TIGHTENING":                   {"equity": 0.35, "bonds": 0.25, "gold": 0.25, "cash": 0.15},
    "LIQUIDITY_TIGHTENING":                  {"equity": 0.30, "bonds": 0.25, "gold": 0.25, "cash": 0.20},
    "EARLY_CYCLE_RECOVERY":                  {"equity": 0.60, "bonds": 0.25, "gold": 0.08, "cash": 0.07},
    "GROWTH_SLOWDOWN_SUPPORT":               {"equity": 0.35, "bonds": 0.35, "gold": 0.20, "cash": 0.10},
    "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": {"equity": 0.30, "bonds": 0.20, "gold": 0.30, "cash": 0.20},
    "STAGFLATION_RISK":                      {"equity": 0.25, "bonds": 0.25, "gold": 0.35, "cash": 0.15},
    "TRANSITION_PHASE":                      {"equity": 0.45, "bonds": 0.25, "gold": 0.15, "cash": 0.15},
}

# Adjacent regimes for weighted accuracy scoring
REGIME_NEIGHBOURS = {
    "EARLY_CYCLE_RECOVERY":       ["STABLE_GROWTH",    "LIQUIDITY_DRIVEN_EXPANSION"],
    "MONETARY_TIGHTENING":        ["LIQUIDITY_TIGHTENING", "STABLE_GROWTH"],
    "LIQUIDITY_TIGHTENING":       ["MONETARY_TIGHTENING",  "INFLATION_PRESSURE_WITH_EXTERNAL_RISK"],
    "LIQUIDITY_DRIVEN_EXPANSION": ["STABLE_GROWTH",    "EARLY_CYCLE_RECOVERY"],
    "GROWTH_SLOWDOWN_SUPPORT":    ["MONETARY_TIGHTENING",  "TRANSITION_PHASE"],
    "STABLE_GROWTH":              ["LIQUIDITY_DRIVEN_EXPANSION", "EARLY_CYCLE_RECOVERY"],
}

BULLISH_REGIMES = {
    "LIQUIDITY_DRIVEN_EXPANSION",
    "EARLY_CYCLE_RECOVERY",
    "STABLE_GROWTH"
}
BEARISH_REGIMES = {
    "LIQUIDITY_TIGHTENING",
    "MONETARY_TIGHTENING",
    "GROWTH_SLOWDOWN_SUPPORT",
    "STAGFLATION_RISK",
    "INFLATION_PRESSURE_WITH_EXTERNAL_RISK"
}


# =========================
# 🚀 BACKTEST ENGINE
# =========================
class BacktestEngine:
    def __init__(self, regime_engine=None, fred_api_key=None):
        self.regime_engine = regime_engine
        self.fred_api_key  = fred_api_key
        self.price_cache   = {}

    def _safe_float(self, val, default=0.0):
        try:
            f = float(val)
            return f if not (np.isnan(f) or np.isinf(f)) else default
        except Exception:
            return default

    def _month_key(self, dt):
        return dt.strftime("%Y-%m")

    # -------------------------
    # 📥 PRICE LOADER
    # -------------------------
    def _fetch_yahoo_prices(self, symbol, start, end):
        if symbol in self.price_cache:
            return self.price_cache[symbol]
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            df     = ticker.history(start=start, end=end, interval="1mo")
            if df.empty:
                print(f"  [WARN] No data for {symbol}")
                return {}
            prices = {}
            for idx, row in df.iterrows():
                prices[idx.strftime("%Y-%m")] = self._safe_float(row.get("Close", 0))
            self.price_cache[symbol] = prices
            return prices
        except ImportError:
            print("  [WARN] yfinance not installed. Run: pip install yfinance")
            return {}
        except Exception as e:
            print(f"  [WARN] Failed to fetch {symbol}: {e}")
            return {}

    def _fetch_fred_series(self, series_id):
        if not self.fred_api_key:
            return {}
        try:
            resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id":  series_id,
                    "api_key":    self.fred_api_key,
                    "file_type":  "json",
                    "frequency":  "m",
                    "sort_order": "asc"
                },
                timeout=15
            )
            if resp.status_code != 200:
                return {}
            result = {}
            for obs in resp.json().get("observations", []):
                try:
                    result[obs["date"][:7]] = float(obs["value"])
                except Exception:
                    continue
            return result
        except Exception as e:
            print(f"  [WARN] FRED fetch failed for {series_id}: {e}")
            return {}

    def load_historical_data(self, start_date, end_date):
        print("  Loading Nifty prices...")
        nifty    = self._fetch_yahoo_prices("^NSEI", start_date, end_date)
        print("  Loading Gold prices...")
        gold     = self._fetch_yahoo_prices("GC=F",  start_date, end_date)
        print("  Loading USD/INR...")
        inr      = self._fetch_yahoo_prices("INR=X", start_date, end_date)
        print("  Loading US 10Y yield...")
        us10y    = self._fetch_yahoo_prices("^TNX",  start_date, end_date)
        print("  Loading FRED US rates...")
        us_rates = self._fetch_fred_series("FEDFUNDS")
        return {
            "nifty": nifty, "gold": gold,
            "inr": inr, "us10y": us10y, "us_rates": us_rates
        }

    # -------------------------
    # 🔢 RETURNS
    # -------------------------
    def _monthly_returns(self, price_dict):
        keys    = sorted(price_dict.keys())
        returns = {}
        for i in range(1, len(keys)):
            prev = price_dict.get(keys[i-1], 0)
            curr = price_dict.get(keys[i],   0)
            returns[keys[i]] = (curr - prev) / prev if prev > 0 else 0.0
        return returns

    def _bond_return(self, month_key, prev_month_key, duration=5.0):
        curr_rate    = RBI_REPO_HISTORY.get(month_key,      6.5)
        prev_rate    = RBI_REPO_HISTORY.get(prev_month_key, 6.5)
        price_return = -duration * ((curr_rate - prev_rate) / 100)
        carry_return = (curr_rate / 100) / 12
        return price_return + carry_return

    def _cash_return(self, month_key):
        return (RBI_REPO_HISTORY.get(month_key, 6.0) / 100) / 12

    # -------------------------
    # 🧠 REGIME CLASSIFIER
    # -------------------------
    def _classify_regime(self, month_key):
        repo      = RBI_REPO_HISTORY.get(month_key,  6.5)
        inflation = INDIA_CPI_HISTORY.get(month_key, 5.0)
        growth    = INDIA_GDP_HISTORY.get(month_key, 6.5)

        if self.regime_engine:
            liquidity_score = max(-1.0, min(1.0, (6.0 - repo) / 3.0))
            intel = {
                "sentiment_score":        0.0,
                "regime_type":            "NEUTRAL/WATCH",
                "growth_intensity":       3,
                "rbi_policy_implication": "UNKNOWN",
                "equity_bias":            "NEUTRAL",
                "confidence":             0.5,
                "source":                 "keyword",
                "provider":               "none",
                "dominant_theme":         "",
                "key_signals":            [],
                "india_specific_risks":   [],
                "global_macro_factors":   [],
                "reasoning":              "",
                "hard_data": {
                    "repo_rate":      repo,
                    "cpi":            inflation,
                    "gdp_growth":     growth,
                    "fiscal_deficit": 4.5,
                    "capex_lakh_cr":  10.0
                }
            }
            liquidity_output = {
                "liquidity_regime": (
                    "LIQUIDITY_EXPANSION"  if liquidity_score >  0.2 else
                    "LIQUIDITY_TIGHTENING" if liquidity_score < -0.2 else
                    "LIQUIDITY_NEUTRAL"
                ),
                "liquidity_score": liquidity_score
            }
            try:
                result = self.regime_engine.detect_regime(intel, liquidity_output)
                return (
                    result.get("regime",     "TRANSITION_PHASE"),
                    result.get("confidence", 0.6)
                )
            except Exception as e:
                print(f"  [WARN] RegimeEngine failed for {month_key}: {e}")

        return self._rule_based_regime(repo, inflation, growth)

    def _rule_based_regime(self, repo, inflation, growth):
        if growth < -5.0:
            return "GROWTH_SLOWDOWN_SUPPORT", 0.80
        if growth < 0:
            return "GROWTH_SLOWDOWN_SUPPORT", 0.80

        if inflation > 6.5 and repo > 5.5:
            return "LIQUIDITY_TIGHTENING",    0.75
        elif inflation > 6.5 and 0 < growth < 3.0:
            return "STAGFLATION_RISK",        0.72
        elif inflation > 6.0:
            return "MONETARY_TIGHTENING",     0.70
        elif growth < 5.5 and repo < 5.0:
            return "EARLY_CYCLE_RECOVERY",    0.70
        elif repo < 5.0 and growth > 6.0:
            return "LIQUIDITY_DRIVEN_EXPANSION", 0.75
        elif growth > 6.5 and inflation < 6.0:
            return "STABLE_GROWTH",           0.68
        else:
            return "TRANSITION_PHASE",        0.55

    # -------------------------
    # 🎯 SIGNAL TIMING
    # -------------------------
    def _analyse_signal_timing(self, monthly_results):
        timing_events = []
        keys          = sorted(monthly_results.keys())

        for i in range(2, len(keys)):
            prev2 = monthly_results[keys[i-2]]
            prev1 = monthly_results[keys[i-1]]
            curr  = monthly_results[keys[i]]

            if prev1["sentinel_regime"] == prev2["sentinel_regime"]:
                continue

            nifty_ret  = self._safe_float(curr.get("nifty_return", 0))
            mkt_dir    = "up" if nifty_ret > 2.0 else "down" if nifty_ret < -2.0 else "flat"
            new_regime = prev1["sentinel_regime"]
            sig_dir    = (
                "bullish" if new_regime in BULLISH_REGIMES else
                "bearish" if new_regime in BEARISH_REGIMES else
                "neutral"
            )
            correct = (
                (sig_dir == "bullish" and mkt_dir == "up") or
                (sig_dir == "bearish" and mkt_dir == "down")
            )

            timing_events.append({
                "signal_month":     keys[i-1],
                "market_month":     keys[i],
                "from_regime":      prev2["sentinel_regime"],
                "to_regime":        new_regime,
                "signal_direction": sig_dir,
                "nifty_return":     round(nifty_ret, 2),
                "market_direction": mkt_dir,
                "signal_correct":   correct,
                "lead_months":      1
            })

        return timing_events

    # -------------------------
    # 🚀 MAIN RUNNER
    # -------------------------
    def run(self, start_date="2020-01-01", end_date="2025-12-31"):
        print(f"\nSENTINEL Backtest: {start_date} → {end_date}")
        print("=" * 50)

        data      = self.load_historical_data(start_date, end_date)
        nifty_ret = self._monthly_returns(data["nifty"])
        gold_ret  = self._monthly_returns(data["gold"])

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date,   "%Y-%m-%d")
        months   = []
        cur      = start_dt.replace(day=1)
        while cur <= end_dt:
            months.append(cur.strftime("%Y-%m"))
            cur += relativedelta(months=1)

        sentinel_value   = 100.0
        benchmark_value  = 100.0
        sentinel_values  = {}
        benchmark_values = {}
        monthly_results  = {}
        sentinel_rets    = []
        benchmark_rets   = []
        prev_month       = None

        print(f"  Processing {len(months)} months...")

        for month_key in months:
            if month_key not in nifty_ret:
                continue

            sentinel_regime, confidence = self._classify_regime(month_key)
            ground_truth                = GROUND_TRUTH_REGIMES.get(month_key, "UNKNOWN")
            regime_correct              = (sentinel_regime == ground_truth)

            alloc = REGIME_ALLOCATION.get(
                sentinel_regime, REGIME_ALLOCATION["TRANSITION_PHASE"]
            )

            eq_r   = self._safe_float(nifty_ret.get(month_key, 0))
            gold_r = self._safe_float(gold_ret.get(month_key,  0))
            bond_r = self._bond_return(month_key, prev_month or month_key)
            cash_r = self._cash_return(month_key)

            s_ret = (
                alloc["equity"] * eq_r   +
                alloc["bonds"]  * bond_r +
                alloc["gold"]   * gold_r +
                alloc["cash"]   * cash_r
            )
            b_ret = eq_r

            sentinel_value  *= (1 + s_ret)
            benchmark_value *= (1 + b_ret)

            sentinel_values[month_key]  = round(sentinel_value,  2)
            benchmark_values[month_key] = round(benchmark_value, 2)
            sentinel_rets.append(s_ret)
            benchmark_rets.append(b_ret)

            monthly_results[month_key] = {
                "sentinel_regime":  sentinel_regime,
                "ground_truth":     ground_truth,
                "regime_correct":   regime_correct,
                "regime_match":     "",       # filled in accuracy block below
                "confidence":       round(confidence, 2),
                "allocation":       alloc,
                "nifty_return":     round(eq_r   * 100, 2),
                "gold_return":      round(gold_r  * 100, 2),
                "bond_return":      round(bond_r  * 100, 2),
                "cash_return":      round(cash_r  * 100, 2),
                "sentinel_return":  round(s_ret   * 100, 2),
                "benchmark_return": round(b_ret   * 100, 2),
                "sentinel_value":   round(sentinel_value,  2),
                "benchmark_value":  round(benchmark_value, 2),
                "repo_rate":        RBI_REPO_HISTORY.get(month_key,  6.5),
                "inflation":        INDIA_CPI_HISTORY.get(month_key, 5.0),
                "gdp_growth":       INDIA_GDP_HISTORY.get(month_key, 6.5),
            }

            prev_month = month_key

        print(f"  Months processed: {len(monthly_results)}")

        # -------------------------
        # 📐 PERFORMANCE METRICS
        # -------------------------
        def annualised_return(rets):
            if not rets:
                return 0.0
            total = np.prod(1 + np.array(rets))
            return round((total ** (12 / len(rets)) - 1) * 100, 2)

        def sharpe(rets, rf_monthly=0.005):
            arr    = np.array(rets)
            excess = arr - rf_monthly
            std    = excess.std()
            return round((excess.mean() / std) * np.sqrt(12), 2) if std > 0 else 0.0

        def max_dd(values_dict):
            vals = list(values_dict.values())
            if not vals:
                return 0.0
            peak = vals[0]
            mdd  = 0.0
            for v in vals:
                peak = max(peak, v)
                mdd  = min(mdd, (v - peak) / peak)
            return round(mdd * 100, 2)

        def win_rate(rets):
            arr = np.array(rets)
            return round((arr > 0).sum() / len(arr) * 100, 1) if len(arr) else 0.0

        def volatility(rets):
            return round(np.array(rets).std() * np.sqrt(12) * 100, 2) if rets else 0.0

        def calmar(ann_ret, mdd):
            return round(ann_ret / abs(mdd), 2) if mdd != 0 else 0.0

        # -------------------------
        # ✅ BUG 1 FIX — Regime accuracy loop
        # All logic is INSIDE the for loop with correct indentation
        # Both regime_breakdown dict AND weighted accuracy are built here
        # -------------------------
        known         = [v for v in monthly_results.values() if v["ground_truth"] != "UNKNOWN"]
        correct_n     = 0
        partial_n     = 0
        regime_breakdown = {}   # ✅ BUG 2 FIX — defined here, used in return dict

        for v in known:                                    # ← loop starts
            gt  = v["ground_truth"]
            det = v["sentinel_regime"]

            # Per-regime breakdown tracking
            if gt not in regime_breakdown:
                regime_breakdown[gt] = {"correct": 0, "partial": 0, "total": 0}
            regime_breakdown[gt]["total"] += 1

            # Weighted match classification — ALL inside the loop
            if det == gt:
                correct_n += 1
                v["regime_match"] = "exact"
                regime_breakdown[gt]["correct"] += 1
            elif det in REGIME_NEIGHBOURS.get(gt, []):
                partial_n += 1
                v["regime_match"] = "adjacent"
                regime_breakdown[gt]["partial"] += 1
            else:
                v["regime_match"] = "miss"
                                                           # ← loop ends

        # Compute accuracy from totals
        weighted_correct      = correct_n + 0.5 * partial_n
        regime_accuracy       = round(weighted_correct / len(known) * 100, 1) if known else 0.0
        regime_accuracy_exact = round(correct_n        / len(known) * 100, 1) if known else 0.0

        # Finalize per-regime accuracy
        for gt, stats in regime_breakdown.items():
            weighted = stats["correct"] + 0.5 * stats["partial"]
            stats["accuracy"] = round(
                weighted / stats["total"] * 100, 1
            ) if stats["total"] > 0 else 0.0

        # -------------------------
        # Signal timing
        # -------------------------
        timing_events   = self._analyse_signal_timing(monthly_results)
        timing_correct  = sum(1 for e in timing_events if e["signal_correct"])
        timing_hit_rate = round(
            timing_correct / len(timing_events) * 100, 1
        ) if timing_events else 0.0

        # Regime transition matrix
        transition_matrix = {}
        month_list        = sorted(monthly_results.keys())
        for i in range(1, len(month_list)):
            fr = monthly_results[month_list[i-1]]["sentinel_regime"]
            to = monthly_results[month_list[i]  ]["sentinel_regime"]
            transition_matrix.setdefault(fr, {})
            transition_matrix[fr][to] = transition_matrix[fr].get(to, 0) + 1

        # Monthly outperformance
        outperform_months = sum(
            1 for v in monthly_results.values()
            if v["sentinel_return"] > v["benchmark_return"]
        )
        outperform_rate = round(
            outperform_months / len(monthly_results) * 100, 1
        ) if monthly_results else 0.0

        # Avg return per regime
        regime_returns = {}
        for v in monthly_results.values():
            regime_returns.setdefault(v["sentinel_regime"], [])
            regime_returns[v["sentinel_regime"]].append(v["sentinel_return"])
        regime_avg_returns = {
            r: round(float(np.mean(rets)), 2)
            for r, rets in regime_returns.items()
        }

        s_ann = annualised_return(sentinel_rets)
        b_ann = annualised_return(benchmark_rets)
        s_mdd = max_dd(sentinel_values)
        b_mdd = max_dd(benchmark_values)

        return {
            "summary": {
                "start_date":             start_date,
                "end_date":               end_date,
                "months_tested":          len(sentinel_rets),
                "years_tested":           round(len(sentinel_rets) / 12, 1),

                "sentinel_total_return":  round(sentinel_value  - 100, 1),
                "benchmark_total_return": round(benchmark_value - 100, 1),
                "sentinel_annualised":    s_ann,
                "benchmark_annualised":   b_ann,
                "excess_return":          round(s_ann - b_ann, 2),

                "sentinel_sharpe":        sharpe(sentinel_rets),
                "benchmark_sharpe":       sharpe(benchmark_rets),
                "sentinel_volatility":    volatility(sentinel_rets),
                "benchmark_volatility":   volatility(benchmark_rets),
                "sentinel_max_drawdown":  s_mdd,
                "benchmark_max_drawdown": b_mdd,
                "sentinel_calmar":        calmar(s_ann, s_mdd),
                "benchmark_calmar":       calmar(b_ann, b_mdd),
                "sentinel_win_rate":      win_rate(sentinel_rets),
                "benchmark_win_rate":     win_rate(benchmark_rets),
                "outperform_rate":        outperform_rate,

                "regime_accuracy_pct":    regime_accuracy,
                "regime_accuracy_exact":  regime_accuracy_exact,
                "regime_correct_months":  correct_n,
                "regime_partial_months":  partial_n,
                "regime_total_months":    len(known),
                "signal_events":          len(timing_events),
                "signal_hit_rate":        timing_hit_rate,
                "signal_correct":         timing_correct,
            },
            "monthly_results":    monthly_results,
            "sentinel_values":    sentinel_values,
            "benchmark_values":   benchmark_values,
            "regime_breakdown":   regime_breakdown,    # ✅ now always defined
            "regime_avg_returns": regime_avg_returns,
            "timing_events":      timing_events,
            "transition_matrix":  transition_matrix,
        }


# =========================
# 📊 BACKTEST FORMATTER
# =========================
class BacktestFormatter:

    @staticmethod
    def summary_text(results):
        s = results["summary"]
        lines = [
            f"SENTINEL Backtest — {s['start_date']} to {s['end_date']}",
            f"Period: {s['years_tested']} years ({s['months_tested']} months)",
            "",
            "── RETURNS ──────────────────────────────",
            f"  SENTINEL total return:       {s['sentinel_total_return']}%",
            f"  Nifty 50 total return:       {s['benchmark_total_return']}%",
            f"  SENTINEL annualised:         {s['sentinel_annualised']}% p.a.",
            f"  Nifty annualised:            {s['benchmark_annualised']}% p.a.",
            f"  Excess return (alpha):       {s['excess_return']:+.2f}% p.a.",
            "",
            "── RISK ─────────────────────────────────",
            f"  SENTINEL Sharpe ratio:       {s['sentinel_sharpe']}",
            f"  Nifty Sharpe ratio:          {s['benchmark_sharpe']}",
            f"  SENTINEL volatility:         {s['sentinel_volatility']}%",
            f"  Nifty volatility:            {s['benchmark_volatility']}%",
            f"  SENTINEL max drawdown:       {s['sentinel_max_drawdown']}%",
            f"  Nifty max drawdown:          {s['benchmark_max_drawdown']}%",
            f"  SENTINEL Calmar ratio:       {s['sentinel_calmar']}",
            f"  SENTINEL monthly win rate:   {s['sentinel_win_rate']}%",
            f"  Monthly outperformance:      {s['outperform_rate']}% of months",
            "",
            "── REGIME INTELLIGENCE ──────────────────",
            f"  Weighted accuracy:           {s['regime_accuracy_pct']}%",
            f"  Exact accuracy:              {s['regime_accuracy_exact']}%",
            f"  Exact correct months:        {s['regime_correct_months']} / {s['regime_total_months']}",
            f"  Adjacent (partial) months:   {s['regime_partial_months']} / {s['regime_total_months']}",
            "",
            "── SIGNAL TIMING ────────────────────────",
            f"  Regime shift events:         {s['signal_events']}",
            f"  Signal hit rate:             {s['signal_hit_rate']}%",
            f"  Correct signals:             {s['signal_correct']} / {s['signal_events']}",
        ]
        return "\n".join(lines)

    @staticmethod
    def to_dataframe(results):
        rows = []
        for month, v in sorted(results["monthly_results"].items()):
            rows.append({
                "Month":           month,
                "Regime":          v["sentinel_regime"].replace("_", " ").title(),
                "Ground Truth":    v["ground_truth"].replace("_",   " ").title(),
                "Match":           v.get("regime_match", ""),
                "Confidence":      f"{int(v['confidence'] * 100)}%",
                "Nifty Ret %":     v["nifty_return"],
                "SENTINEL Ret %":  v["sentinel_return"],
                "SENTINEL Value":  v["sentinel_value"],
                "Benchmark Value": v["benchmark_value"],
                "Repo Rate %":     v["repo_rate"],
                "CPI %":           v["inflation"],
                "GDP %":           v["gdp_growth"],
            })
        return pd.DataFrame(rows)


# =========================
# 🧪 STANDALONE TEST
# =========================
if __name__ == "__main__":
    from regime_engine import MacroRegimeEngine

    print("Initialising engines...")
    engine = BacktestEngine(regime_engine=MacroRegimeEngine())

    print("Starting backtest...")
    results = engine.run(start_date="2020-01-01", end_date="2025-12-31")

    print("\n" + BacktestFormatter.summary_text(results))

    print("\n── REGIME BREAKDOWN ──────────────────────────────")
    for regime, stats in sorted(
        results["regime_breakdown"].items(),
        key=lambda x: x[1]["accuracy"],
        reverse=True
    ):
        print(
            f"  {regime:<45} "
            f"{stats['accuracy']:5.1f}%  "
            f"({stats['correct']}E + {stats['partial']}A / {stats['total']})"
        )

    print("\n── TOP SIGNAL TIMING EVENTS ──────────────────────")
    for e in results["timing_events"][:8]:
        tick = "✓" if e["signal_correct"] else "✗"
        print(
            f"  {tick}  {e['signal_month']}  "
            f"{e['to_regime'][:35]:<35}  "
            f"Nifty +1mo: {e['nifty_return']:+5.1f}%"
        )

    print("\n── REGIME AVG MONTHLY RETURNS ────────────────────")
    for r, avg in sorted(
        results["regime_avg_returns"].items(),
        key=lambda x: x[1],
        reverse=True
    ):
        bar = "█" * max(0, int(avg * 3))
        print(f"  {r:<45} {avg:+5.2f}%  {bar}")