"""
nse_data.py — NSE India Data Module

Fetches FII/DII flows, market indices, VIX, and option chain data
directly from NSE's internal JSON endpoints.

No API key required. Uses session cookie approach.
"""

import requests
import json
import time
import datetime
from functools import lru_cache


# =========================
# 📡 NSE ENDPOINTS
# =========================
NSE_BASE    = "https://www.nseindia.com"
ENDPOINTS   = {
    "fii_dii":      "/api/fiidiiTradeReact",
    "indices":      "/api/allIndices",
    "option_chain": "/api/option-chain-indices?symbol=NIFTY",
    "market_status":"/api/marketStatus",
    "advances":     "/api/market-data-pre-open?key=ALL",
}

# Browser headers — required to avoid 403
HEADERS = {
    "User-Agent":       (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "Referer":          "https://www.nseindia.com/",
    "Connection":       "keep-alive",
    "DNT":              "1",
}


class NSEDataFetcher:

    def __init__(self):
        self.session     = None
        self.session_time = None
        self.SESSION_TTL  = 300   # refresh session every 5 minutes

    # =========================
    # 🔐 SESSION MANAGEMENT
    # =========================
    def _get_session(self):
        """
        Creates a browser-like session by hitting the NSE homepage first.
        NSE requires this to set cookies before JSON endpoints respond.
        """
        now = time.time()

        # Reuse existing session if fresh
        if (self.session and self.session_time and
                now - self.session_time < self.SESSION_TTL):
            return self.session

        session = requests.Session()
        session.headers.update(HEADERS)

        try:
            # Hit homepage to get session cookies
            session.get(NSE_BASE, timeout=10)
            time.sleep(0.5)   # small delay — mimics human behaviour
            # Hit a common page to warm up the session
            session.get(f"{NSE_BASE}/market-data/live-equity-market", timeout=10)
            time.sleep(0.3)

            self.session      = session
            self.session_time = now
            return session

        except Exception as e:
            print(f"[NSE] Session init failed: {e}")
            return session

    def _fetch(self, endpoint_key, retries=2):
        """
        Fetches a JSON endpoint with session cookie and retry logic.
        Returns parsed dict or None on failure.
        """
        url = NSE_BASE + ENDPOINTS[endpoint_key]

        for attempt in range(retries):
            try:
                session  = self._get_session()
                response = session.get(url, timeout=15)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 403:
                    # Session expired — force refresh
                    self.session = None
                    time.sleep(1)
                    continue
                else:
                    print(f"[NSE] {endpoint_key} returned {response.status_code}")
                    return None

            except requests.exceptions.Timeout:
                print(f"[NSE] Timeout on {endpoint_key} (attempt {attempt+1})")
                time.sleep(1)
            except Exception as e:
                print(f"[NSE] Error on {endpoint_key}: {e}")
                return None

        return None

    # =========================
    # 📊 FII / DII FLOWS
    # Most important for SENTINEL regime engine
    # =========================
    def get_fii_dii(self):
        """
        Returns today's FII and DII net buy/sell in equity cash segment.

        Output:
        {
            "date": "17-Apr-2026",
            "fii": {
                "buy": 12500.5,    # Rs. Crore
                "sell": 10200.3,
                "net": 2300.2      # positive = net buyer
            },
            "dii": {
                "buy": 8400.1,
                "sell": 7100.5,
                "net": 1299.6
            },
            "fii_signal": "BUYING",    # BUYING / SELLING / NEUTRAL
            "dii_signal": "BUYING",
            "combined_signal": "RISK_ON",  # RISK_ON / RISK_OFF / NEUTRAL
            "raw": [...]
        }
        """
        data = self._fetch("fii_dii")
        if not data:
            return self._fii_dii_fallback()

        try:
            result = {
                "date":     "",
                "fii":      {"buy": 0, "sell": 0, "net": 0},
                "dii":      {"buy": 0, "sell": 0, "net": 0},
                "raw":      data
            }

            for entry in data:
                category = str(entry.get("category", "")).upper()
                buy  = float(str(entry.get("buyValue",  "0")).replace(",", "") or 0)
                sell = float(str(entry.get("sellValue", "0")).replace(",", "") or 0)
                net  = float(str(entry.get("netValue",  "0")).replace(",", "") or 0)
                date = entry.get("date", "")

                if "FII" in category or "FPI" in category:
                    result["fii"]  = {"buy": buy, "sell": sell, "net": net}
                    result["date"] = date
                elif "DII" in category:
                    result["dii"]  = {"buy": buy, "sell": sell, "net": net}

            # Derive signals
            fii_net = result["fii"]["net"]
            dii_net = result["dii"]["net"]

            result["fii_signal"] = (
                "BUYING"  if fii_net >  500 else
                "SELLING" if fii_net < -500 else
                "NEUTRAL"
            )
            result["dii_signal"] = (
                "BUYING"  if dii_net >  500 else
                "SELLING" if dii_net < -500 else
                "NEUTRAL"
            )

            # Combined signal for regime engine
            combined_net = fii_net + dii_net
            result["combined_signal"] = (
                "RISK_ON"  if combined_net >  1000 else
                "RISK_OFF" if combined_net < -1000 else
                "NEUTRAL"
            )

            return result

        except Exception as e:
            print(f"[NSE] FII/DII parse error: {e}")
            return self._fii_dii_fallback()

    def _fii_dii_fallback(self):
        return {
            "date":            datetime.date.today().strftime("%d-%b-%Y"),
            "fii":             {"buy": 0, "sell": 0, "net": 0},
            "dii":             {"buy": 0, "sell": 0, "net": 0},
            "fii_signal":      "UNKNOWN",
            "dii_signal":      "UNKNOWN",
            "combined_signal": "NEUTRAL",
            "raw":             [],
            "error":           "NSE fetch failed"
        }

    # =========================
    # 📈 MARKET INDICES
    # Nifty 50, Bank Nifty, India VIX
    # =========================
    def get_indices(self):
        """
        Returns key index values including Nifty 50, Bank Nifty, India VIX.

        Output:
        {
            "nifty50":    {"last": 22150.5, "change_pct": 0.45},
            "bank_nifty": {"last": 48200.0, "change_pct": 0.62},
            "india_vix":  {"last": 13.5,    "change_pct": -2.1},
            "nifty_midcap": {...},
            "market_bias": "BULLISH"  # derived from breadth
        }
        """
        data = self._fetch("indices")
        if not data:
            return {}

        try:
            result     = {}
            index_data = data.get("data", [])

            name_map = {
                "NIFTY 50":          "nifty50",
                "NIFTY BANK":        "bank_nifty",
                "INDIA VIX":         "india_vix",
                "NIFTY MIDCAP 100":  "nifty_midcap",
                "NIFTY IT":          "nifty_it",
                "NIFTY FMCG":        "nifty_fmcg",
                "NIFTY PHARMA":      "nifty_pharma",
            }

            for idx in index_data:
                name = idx.get("indexSymbol", "")
                key  = name_map.get(name)
                if not key:
                    continue

                last       = float(idx.get("last",           0))
                prev_close = float(idx.get("previousClose",  0))
                change_pct = float(idx.get("percentChange",  0))
                yearly_hi  = float(idx.get("yearHigh",       0))
                yearly_lo  = float(idx.get("yearLow",        0))

                result[key] = {
                    "last":        last,
                    "prev_close":  prev_close,
                    "change_pct":  change_pct,
                    "year_high":   yearly_hi,
                    "year_low":    yearly_lo,
                    "from_52w_high": round(
                        ((last - yearly_hi) / yearly_hi * 100), 1
                    ) if yearly_hi else 0
                }

            # Derive market bias from Nifty + VIX
            nifty_chg = result.get("nifty50", {}).get("change_pct", 0)
            vix_val   = result.get("india_vix", {}).get("last", 15)

            result["market_bias"] = (
                "BULLISH"  if nifty_chg >  0.5 and vix_val < 16 else
                "BEARISH"  if nifty_chg < -0.5 or  vix_val > 20 else
                "NEUTRAL"
            )

            return result

        except Exception as e:
            print(f"[NSE] Indices parse error: {e}")
            return {}

    # =========================
    # 📊 OPTION CHAIN (PCR)
    # Put-Call Ratio = sentiment signal
    # =========================
    def get_pcr(self):
        """
        Returns Put-Call Ratio for Nifty — a sentiment signal.
        PCR > 1.2 = bearish sentiment (market buying puts)
        PCR < 0.8 = bullish sentiment (market buying calls)

        Output:
        {
            "pcr": 1.05,
            "pcr_signal": "NEUTRAL",   # BULLISH / BEARISH / NEUTRAL
            "total_put_oi": 45000000,
            "total_call_oi": 42000000
        }
        """
        data = self._fetch("option_chain")
        if not data:
            return {"pcr": 1.0, "pcr_signal": "NEUTRAL"}

        try:
            records    = data.get("records", {}).get("data", [])
            total_put  = sum(
                r.get("PE", {}).get("openInterest", 0)
                for r in records if r.get("PE")
            )
            total_call = sum(
                r.get("CE", {}).get("openInterest", 0)
                for r in records if r.get("CE")
            )

            pcr = round(total_put / total_call, 2) if total_call > 0 else 1.0

            return {
                "pcr":          pcr,
                "pcr_signal": (
                    "BEARISH" if pcr > 1.2 else
                    "BULLISH" if pcr < 0.8 else
                    "NEUTRAL"
                ),
                "total_put_oi":  total_put,
                "total_call_oi": total_call
            }

        except Exception as e:
            print(f"[NSE] PCR parse error: {e}")
            return {"pcr": 1.0, "pcr_signal": "NEUTRAL"}

    # =========================
    # 🧠 FULL NSE SNAPSHOT
    # Single call returns everything SENTINEL needs
    # =========================
    def get_full_snapshot(self):
        """
        Returns a combined dict of all NSE data for the regime engine.
        Called once per SENTINEL pipeline run.
        """
        fii_dii = self.get_fii_dii()
        indices = self.get_indices()
        pcr     = self.get_pcr()

        return {
            "fii_dii":      fii_dii,
            "indices":      indices,
            "pcr":          pcr,
            "timestamp":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            # Convenience fields for regime engine
            "fii_net_crore": fii_dii.get("fii", {}).get("net", 0),
            "dii_net_crore": fii_dii.get("dii", {}).get("net", 0),
            "india_vix":     indices.get("india_vix", {}).get("last", 15),
            "nifty_change":  indices.get("nifty50",   {}).get("change_pct", 0),
            "pcr":           pcr.get("pcr", 1.0),
            "flow_signal":   fii_dii.get("combined_signal", "NEUTRAL"),
            "market_bias":   indices.get("market_bias", "NEUTRAL"),
        }