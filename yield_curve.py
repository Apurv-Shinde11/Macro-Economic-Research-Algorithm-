"""
yield_curve.py — SENTINEL Yield Curve Module

Fetches US Treasury yields via yfinance (live).
India G-Sec yields are hardcoded from RBI data —
updated monthly since IN*YT=RR symbols are 
delisted on Yahoo Finance.

Data sources:
  India G-Sec  — hardcoded RBI data, updated monthly
  US Treasury  — yfinance (^IRX, ^FVX, ^TNX, ^TYX)

Last India yield update: May 2026 (RBI data)
Next update due: June 2026 after RBI MPC
"""

import datetime


# =========================
# 🇮🇳 INDIA G-SEC YIELDS
# Hardcoded from RBI data — updated monthly
# Source: RBI website / CCIL / fbil.org.in
# Last verified: May 2026
# IN*YT=RR symbols are delisted on Yahoo Finance
# =========================
INDIA_YIELDS_CURRENT = {
    "3M":  6.45,   # RBI T-Bill 91-day (May 2026)
    "1Y":  6.52,   # RBI T-Bill 364-day (May 2026)
    "2Y":  6.60,   # G-Sec 2Y (May 2026)
    "3Y":  6.65,   # G-Sec 3Y (May 2026)
    "5Y":  6.72,   # G-Sec 5Y (May 2026)
    "7Y":  6.78,   # G-Sec 7Y (May 2026)
    "10Y": 6.85,   # G-Sec 10Y benchmark (May 2026)
    "30Y": 7.05,   # G-Sec 30Y (May 2026)
}

# =========================
# 🇺🇸 US TREASURY TICKERS
# Working Yahoo Finance tickers — verified May 2026
# =========================
US_TICKERS = {
    "3M":  "^IRX",    # 13-week Treasury bill
    "5Y":  "^FVX",    # 5-year Treasury note
    "10Y": "^TNX",    # 10-year Treasury note
    "30Y": "^TYX",    # 30-year Treasury bond
}

# US 2Y fallback — yfinance has no reliable 2Y ticker
# Approximated as midpoint between 3M and 5Y
# Updated from Fed data: May 2026
US_2Y_HARDCODED = 4.80

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
    Fetches India G-Sec benchmark yields from FBIL.
    Source: fbil.org.in/benchmarks/
    Falls back to hardcoded RBI data on failure.
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        url     = "https://www.fbil.org.in/benchmarks/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; "
                "Sentinel/1.0)"
            )
        }
        r = requests.get(url, headers=headers, timeout=10)

        if r.status_code != 200:
            raise Exception(
                f"FBIL returned {r.status_code}"
            )

        soup   = BeautifulSoup(r.text, "html.parser")
        result = {}

        TENOR_MAP = {
            "91 days":  "3M",
            "182 days": "6M",
            "364 days": "1Y",
            "2 year":   "2Y",
            "3 year":   "3Y",
            "5 year":   "5Y",
            "7 year":   "7Y",
            "10 year":  "10Y",
            "14 year":  "14Y",
            "30 year":  "30Y",
        }

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                tenor_text = cells[0].get_text(strip=True).lower()
                yield_text = cells[1].get_text(strip=True)

                for label, key in TENOR_MAP.items():
                    if label in tenor_text:
                        try:
                            val = float(
                                yield_text.replace("%", "").strip()
                            )
                            if 1.0 < val < 20.0:
                                result[key] = round(val, 3)
                        except Exception:
                            pass

        # Require 10Y to consider the fetch successful
        if result.get("10Y"):
            # Fill any missing tenors from hardcoded fallback
            for tenor, val in INDIA_YIELDS_CURRENT.items():
                if tenor not in result:
                    result[tenor] = val

            print(
                f"  [YieldCurve] India yields: live from FBIL "
                f"(10Y={result.get('10Y')}%)",
                flush=True
            )
            return result, "fbil_live"

        raise Exception(
            "10Y yield not found in FBIL response"
        )

    except Exception as e:
        print(
            f"  [YieldCurve] FBIL fetch failed: {e} "
            f"— using hardcoded fallback",
            flush=True
        )
        return dict(INDIA_YIELDS_CURRENT), "hardcoded"


def fetch_us_yields():
    """
    Fetches US Treasury yields via yfinance.
    Falls back to hardcoded values if fetch fails.
    US 2Y is hardcoded — no reliable yfinance symbol.
    """
    result  = {}
    success = 0

    for tenor, ticker in US_TICKERS.items():
        val = _fetch_yield(ticker)
        if val and val > 0:
            result[tenor]  = val
            success       += 1
            print(
                f"  [YieldCurve] US {tenor}: "
                f"{val}% (live)",
                flush=True
            )
        else:
            result[tenor] = US_FALLBACK.get(tenor, 4.5)
            print(
                f"  [YieldCurve] US {tenor}: "
                f"{result[tenor]}% (fallback)",
                flush=True
            )

    # US 2Y — hardcoded since no reliable yfinance symbol
    # Approximate: use live 3M and 5Y to interpolate if available
    if "3M" in result and "5Y" in result and success >= 2:
        interpolated_2y = round(
            result["3M"] * 0.35 + result["5Y"] * 0.65, 3
        )
        result["2Y"] = interpolated_2y
        print(
            f"  [YieldCurve] US 2Y: {interpolated_2y}% "
            f"(interpolated from 3M+5Y)",
            flush=True
        )
    else:
        result["2Y"] = US_2Y_HARDCODED
        print(
            f"  [YieldCurve] US 2Y: {US_2Y_HARDCODED}% "
            f"(hardcoded)",
            flush=True
        )

    source = "live" if success >= 3 else "partial_fallback"
    return result, source


# =========================
# 📊 CURVE ANALYSER
# =========================
def analyse_curve(india_yields, us_yields):
    """
    Analyses yield curve shape and derives macro signals.
    """
    # India spreads
    india_10y = india_yields.get("10Y", 6.85)
    india_2y  = india_yields.get("2Y",  6.60)
    india_3m  = india_yields.get("3M",  6.45)

    india_spread_10y_2y = round(india_10y - india_2y, 2)
    india_spread_10y_3m = round(india_10y - india_3m, 2)

    # US spreads
    us_10y = us_yields.get("10Y", 4.32)
    us_2y  = us_yields.get("2Y",  4.80)
    us_3m  = us_yields.get("3M",  5.25)

    us_spread_10y_2y = round(us_10y - us_2y, 2)

    # India-US 10Y spread (carry signal)
    india_us_spread = round(india_10y - us_10y, 2)

    # India curve shape
    if india_spread_10y_2y > 1.5:
        india_shape   = "STEEP"
        regime_signal = "EARLY_CYCLE_PRO_GROWTH"
        regime_detail = (
            f"India yield curve is steep "
            f"({india_spread_10y_2y:.2f}% 10Y-2Y spread). "
            "Steep curves historically precede accelerating "
            "growth and credit expansion. Pro-cyclical "
            "positioning favoured — Banks, Infrastructure, "
            "Real Estate benefit most."
        )
    elif india_spread_10y_2y >= 0.5:
        india_shape   = "NORMAL"
        regime_signal = "STABLE_GROWTH_CONFIRMED"
        regime_detail = (
            f"India yield curve is normally shaped "
            f"({india_spread_10y_2y:.2f}% 10Y-2Y spread). "
            "Balanced monetary conditions — neither signalling "
            "acceleration nor slowdown. Broad equity "
            "participation appropriate."
        )
    elif india_spread_10y_2y >= 0.0:
        india_shape   = "FLAT"
        regime_signal = "LATE_CYCLE_CAUTION"
        regime_detail = (
            f"India yield curve is flat "
            f"({india_spread_10y_2y:.2f}% 10Y-2Y spread). "
            "Flat curves signal late-cycle conditions — "
            "credit tightening and slowing momentum. "
            "Reduce cyclical exposure."
        )
    else:
        india_shape   = "INVERTED"
        regime_signal = "RECESSION_WARNING"
        regime_detail = (
            f"India yield curve is INVERTED "
            f"({india_spread_10y_2y:.2f}% 10Y-2Y spread). "
            "Curve inversions have preceded every major "
            "Indian growth slowdown. Defensive positioning "
            "strongly recommended."
        )

    # US curve shape
    if us_spread_10y_2y > 1.0:
        us_shape = "STEEP"
    elif us_spread_10y_2y >= 0.0:
        us_shape = "NORMAL/FLAT"
    else:
        us_shape = "INVERTED"

    # Carry signal
    if india_us_spread > 3.5:
        carry_signal = "STRONG_FII_MAGNET"
        carry_detail = (
            f"India-US 10Y spread at {india_us_spread:.2f}% — "
            "well above the 3.5% threshold. India highly "
            "attractive for carry trades. FII debt inflows "
            "likely; supports INR."
        )
    elif india_us_spread > 2.5:
        carry_signal = "NEUTRAL_CARRY"
        carry_detail = (
            f"India-US 10Y spread at {india_us_spread:.2f}% — "
            "within the normal 2.5-3.5% range. FII flows "
            "driven by equity fundamentals rather than "
            "rate differential."
        )
    else:
        carry_signal = "FII_OUTFLOW_RISK"
        carry_detail = (
            f"India-US 10Y spread compressed to "
            f"{india_us_spread:.2f}% — below the 2.5% "
            "comfort zone. FII debt outflow risk elevated. "
            "Monitor for INR pressure."
        )

    print(
        f"  [YieldCurve] India 10Y-2Y: "
        f"{india_spread_10y_2y:+.2f}% ({india_shape}) | "
        f"US 10Y-2Y: {us_spread_10y_2y:+.2f}% ({us_shape}) | "
        f"Carry: {india_us_spread:.2f}% ({carry_signal})",
        flush=True
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
# =========================
def get_yield_curve_data():
    """
    Returns yield curve data ready for display.
    India yields are hardcoded (update monthly).
    US yields are fetched live via yfinance.
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
        "timestamp":     datetime.datetime.now().strftime(
            "%H:%M IST"
        ),
        "tenors_india":  [
            "3M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "30Y"
        ],
        "tenors_us":     ["3M", "2Y", "5Y", "10Y", "30Y"],
    }