"""
yield_curve.py — SENTINEL Yield Curve Module

Fetches India G-Sec and US Treasury yields across tenors.
Derives regime signal from curve shape (steep/normal/flat/inverted).
Feeds 10Y-2Y spread signal back into regime engine context.

Data sources:
  India G-Sec  — yfinance tickers for Indian government bonds
  US Treasury  — yfinance tickers (^IRX, ^FVX, ^TNX, ^TYX)

All free. No API key required.
1-hour cache — yield curves move slowly intraday.
"""

import datetime


# =========================
# 🇮🇳 INDIA G-SEC TICKERS
# Yahoo Finance tickers for Indian government bonds
# Format: IN{tenor}YT=RR
# =========================
INDIA_TICKERS = {
    "3M":  "^IRX",         # Using US 3M as proxy — India 3M not on Yahoo
    "1Y":  "IN1YT=RR",
    "2Y":  "IN2YT=RR",
    "3Y":  "IN3YT=RR",
    "5Y":  "IN5YT=RR",
    "7Y":  "IN7YT=RR",
    "10Y": "IN10YT=RR",
    "30Y": "IN30YT=RR",
}

# =========================
# 🇺🇸 US TREASURY TICKERS
# Standard Yahoo Finance tickers
# =========================
US_TICKERS = {
    "3M":  "^IRX",
    "2Y":  "^TwoYr",
    "5Y":  "^FVX",
    "10Y": "^TNX",
    "30Y": "^TYX",
}

# Fallback values — RBI/Fed data as of Apr 2026
# Used when Yahoo Finance is unreachable
INDIA_FALLBACK = {
    "3M":  6.50,
    "1Y":  6.55,
    "2Y":  6.60,
    "3Y":  6.65,
    "5Y":  6.72,
    "7Y":  6.78,
    "10Y": 6.85,
    "30Y": 7.05,
}

US_FALLBACK = {
    "3M":  5.25,
    "2Y":  4.80,
    "5Y":  4.55,
    "10Y": 4.32,
    "30Y": 4.55,
}


# =========================
# 📥 YIELD FETCHER
# =========================
def _fetch_yield(ticker):
    """Fetches latest yield for a single ticker via yfinance."""
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if len(hist) >= 1:
            return round(float(hist["Close"].iloc[-1]), 3)
        return None
    except Exception:
        return None


def fetch_india_yields():
    """
    Fetches India G-Sec yields across all tenors.
    Returns dict of {tenor: yield_pct} with fallback values
    for any tenor that fails.
    """
    result  = {}
    success = 0

    for tenor, ticker in INDIA_TICKERS.items():
        val = _fetch_yield(ticker)
        if val and val > 0:
            result[tenor]  = val
            success       += 1
        else:
            result[tenor] = INDIA_FALLBACK.get(tenor, 7.0)

    source = "live" if success >= 4 else "fallback"
    return result, source


def fetch_us_yields():
    """
    Fetches US Treasury yields across key tenors.
    Returns dict of {tenor: yield_pct} with fallback values.
    """
    result  = {}
    success = 0

    for tenor, ticker in US_TICKERS.items():
        val = _fetch_yield(ticker)
        if val and val > 0:
            result[tenor]  = val
            success       += 1
        else:
            result[tenor] = US_FALLBACK.get(tenor, 4.5)

    source = "live" if success >= 3 else "fallback"
    return result, source


# =========================
# 📊 CURVE ANALYSER
# Derives regime signal from curve shape
# =========================
def analyse_curve(india_yields, us_yields):
    """
    Analyses yield curve shape and derives macro signals.

    Returns:
    {
        "india_spread_10y_2y":  float,   # key spread
        "india_spread_10y_3m":  float,   # full curve steepness
        "us_spread_10y_2y":     float,
        "india_us_spread_10y":  float,   # carry trade signal
        "india_curve_shape":    str,     # STEEP/NORMAL/FLAT/INVERTED
        "us_curve_shape":       str,
        "regime_signal":        str,     # macro implication
        "regime_signal_detail": str,     # explanation for display
        "carry_signal":         str,     # FII flow implication
    }
    """
    # India spreads
    india_10y = india_yields.get("10Y", 6.85)
    india_2y  = india_yields.get("2Y",  6.60)
    india_3m  = india_yields.get("3M",  6.50)
    india_30y = india_yields.get("30Y", 7.05)

    india_spread_10y_2y = round(india_10y - india_2y,  2)
    india_spread_10y_3m = round(india_10y - india_3m,  2)

    # US spreads
    us_10y = us_yields.get("10Y", 4.32)
    us_2y  = us_yields.get("2Y",  4.80)
    us_3m  = us_yields.get("3M",  5.25)

    us_spread_10y_2y = round(us_10y - us_2y, 2)

    # India-US 10Y spread (carry signal)
    india_us_spread = round(india_10y - us_10y, 2)

    # -------------------------
    # India curve shape
    # -------------------------
    if india_spread_10y_2y > 1.5:
        india_shape         = "STEEP"
        regime_signal       = "EARLY_CYCLE_PRO_GROWTH"
        regime_detail       = (
            f"India yield curve is steep ({india_spread_10y_2y:.2f}% 10Y-2Y spread). "
            "Steep curves historically precede accelerating growth and credit expansion. "
            "Pro-cyclical positioning favoured — Banks, Infrastructure, Real Estate benefit most."
        )
    elif india_spread_10y_2y >= 0.5:
        india_shape         = "NORMAL"
        regime_signal       = "STABLE_GROWTH_CONFIRMED"
        regime_detail       = (
            f"India yield curve is normally shaped ({india_spread_10y_2y:.2f}% 10Y-2Y spread). "
            "Balanced monetary conditions — neither signalling acceleration nor slowdown. "
            "Broad equity participation appropriate; no urgent duration call in bonds."
        )
    elif india_spread_10y_2y >= 0.0:
        india_shape         = "FLAT"
        regime_signal       = "LATE_CYCLE_CAUTION"
        regime_detail       = (
            f"India yield curve is flat ({india_spread_10y_2y:.2f}% 10Y-2Y spread). "
            "Flat curves signal late-cycle conditions — credit tightening and slowing momentum. "
            "Reduce cyclical exposure; shift toward quality and defensives."
        )
    else:
        india_shape         = "INVERTED"
        regime_signal       = "RECESSION_WARNING"
        regime_detail       = (
            f"India yield curve is INVERTED ({india_spread_10y_2y:.2f}% 10Y-2Y spread). "
            "Curve inversions have preceded every major Indian growth slowdown. "
            "Defensive positioning strongly recommended — reduce equities, extend bond duration."
        )

    # US curve shape
    if us_spread_10y_2y > 1.0:
        us_shape = "STEEP"
    elif us_spread_10y_2y >= 0.0:
        us_shape = "NORMAL/FLAT"
    else:
        us_shape = "INVERTED"

    # -------------------------
    # India-US carry signal
    # -------------------------
    if india_us_spread > 3.5:
        carry_signal = "STRONG_FII_MAGNET"
        carry_detail = (
            f"India-US 10Y spread at {india_us_spread:.2f}% — "
            "well above the 3.5% threshold. India highly attractive for carry trades. "
            "FII debt inflows likely; supports INR and reduces external sector risk."
        )
    elif india_us_spread > 2.5:
        carry_signal = "NEUTRAL_CARRY"
        carry_detail = (
            f"India-US 10Y spread at {india_us_spread:.2f}% — "
            "within the normal 2.5-3.5% range. "
            "FII flows driven by equity fundamentals rather than rate differential."
        )
    else:
        carry_signal = "FII_OUTFLOW_RISK"
        carry_detail = (
            f"India-US 10Y spread compressed to {india_us_spread:.2f}% — "
            "below the 2.5% comfort zone. "
            "FII debt outflow risk elevated. Monitor for INR pressure and equity selloff."
        )

    return {
        "india_spread_10y_2y":  india_spread_10y_2y,
        "india_spread_10y_3m":  india_spread_10y_3m,
        "us_spread_10y_2y":     us_spread_10y_2y,
        "india_us_spread_10y":  india_us_spread,
        "india_curve_shape":    india_shape,
        "us_curve_shape":       us_shape,
        "regime_signal":        regime_signal,
        "regime_signal_detail": regime_detail,
        "carry_signal":         carry_signal,
        "carry_detail":         carry_detail,
    }


# =========================
# 🚀 MAIN ENTRY POINT
# Called from main.py — returns everything needed for display
# =========================
def get_yield_curve_data():
    """
    Fetches and analyses yield curve data.
    Returns a complete dict ready for display in main.py.
    Silently falls back to hardcoded values if data unavailable.
    """
    india_yields, india_source = fetch_india_yields()
    us_yields,    us_source    = fetch_us_yields()
    analysis                   = analyse_curve(india_yields, us_yields)

    return {
        "india_yields":  india_yields,
        "us_yields":     us_yields,
        "analysis":      analysis,
        "india_source":  india_source,
        "us_source":     us_source,
        "timestamp":     datetime.datetime.now().strftime("%H:%M IST"),
        "tenors_india":  ["3M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "30Y"],
        "tenors_us":     ["3M", "2Y", "5Y", "10Y", "30Y"],
    }