"""
nse_data.py — NSE India Data Module

Fetches FII/DII flows, market indices, VIX, and option chain data.
Uses multiple endpoint strategies with robust session management.
"""

import requests
import time
import datetime
import random


# =========================
# 📡 NSE ENDPOINTS
# =========================
NSE_BASE  = "https://www.nseindia.com"
NSE_ARCH  = "https://archives.nseindia.com"

ENDPOINTS = {
    "fii_dii":       "/api/fiidiiTradeReact",
    "fii_dii_alt":   "/api/fii-dii",
    "indices":       "/api/allIndices",
    "option_chain":  "/api/option-chain-indices?symbol=NIFTY",
    "market_status": "/api/marketStatus",
}

# Rotate user agents to avoid fingerprinting
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def _get_headers():
    return {
        "User-Agent":       random.choice(USER_AGENTS),
        "Accept":           "application/json, text/plain, */*",
        "Accept-Language":  "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding":  "gzip, deflate, br",
        "Referer":          "https://www.nseindia.com/market-data/live-equity-market",
        "Connection":       "keep-alive",
        "DNT":              "1",
        "Sec-Fetch-Dest":   "empty",
        "Sec-Fetch-Mode":   "cors",
        "Sec-Fetch-Site":   "same-origin",
        "Cache-Control":    "no-cache",
        "Pragma":           "no-cache",
    }


class NSEDataFetcher:

    def __init__(self):
        self.session      = None
        self.session_time = None
        self.SESSION_TTL  = 240  # 4 minutes

    # =========================
    # 🔐 SESSION MANAGEMENT
    # =========================
    def _warm_session(self, session):
        """Warm up the session by visiting multiple pages like a real browser."""
        warm_urls = [
            f"{NSE_BASE}/",
            f"{NSE_BASE}/market-data/live-equity-market",
            f"{NSE_BASE}/market-data/equity-stock-indices",
        ]
        for url in warm_urls:
            try:
                session.get(url, timeout=10)
                time.sleep(random.uniform(0.3, 0.8))
            except Exception:
                pass

    def _get_session(self):
        now = time.time()
        if (self.session and self.session_time and
                now - self.session_time < self.SESSION_TTL):
            return self.session

        session = requests.Session()
        session.headers.update(_get_headers())
        self._warm_session(session)
        self.session      = session
        self.session_time = now
        return session

    def _fetch(self, endpoint_key, retries=3):
        """Fetch JSON endpoint with session + retry logic."""
        url = NSE_BASE + ENDPOINTS[endpoint_key]

        for attempt in range(retries):
            try:
                # Refresh headers each attempt
                session = self._get_session()
                session.headers.update(_get_headers())

                resp = session.get(url, timeout=15)

                if resp.status_code == 200:
                    text = resp.text.strip()
                    if not text:
                        print(f"[NSE] Empty response on {endpoint_key} attempt {attempt+1}")
                        self.session = None  # force session refresh
                        time.sleep(1.5)
                        continue
                    return resp.json()

                elif resp.status_code in (403, 401):
                    print(f"[NSE] Auth error {resp.status_code} on {endpoint_key} — refreshing session")
                    self.session = None
                    time.sleep(2)
                    continue

                else:
                    print(f"[NSE] {endpoint_key} returned HTTP {resp.status_code}")
                    return None

            except requests.exceptions.Timeout:
                print(f"[NSE] Timeout on {endpoint_key} attempt {attempt+1}")
                time.sleep(1)
            except ValueError as e:
                print(f"[NSE] Error on {endpoint_key}: {e}")
                self.session = None
                time.sleep(1)
            except Exception as e:
                print(f"[NSE] Error on {endpoint_key}: {e}")
                return None

        return None

    # =========================
    # 📊 FII / DII FLOWS
    # =========================
    def get_fii_dii(self):
        """
        Returns today's FII and DII net buy/sell in equity cash segment.
        Tries primary endpoint first, then alternate endpoint.
        """
        # Try primary endpoint
        data = self._fetch("fii_dii")

        # Try alternate endpoint if primary fails
        if not data:
            print("[NSE] Trying alternate FII/DII endpoint...")
            data = self._fetch("fii_dii_alt")

        if not data:
            return self._fii_dii_fallback()

        try:
            result = {
                "date": "",
                "fii":  {"buy": 0, "sell": 0, "net": 0},
                "dii":  {"buy": 0, "sell": 0, "net": 0},
                "raw":  data
            }

            # Handle both list and dict response formats
            entries = data if isinstance(data, list) else data.get("data", [])

            for entry in entries:
                category = str(entry.get("category", "")).upper()
                buy  = self._safe_float(entry.get("buyValue",  0))
                sell = self._safe_float(entry.get("sellValue", 0))
                net  = self._safe_float(entry.get("netValue",  0))
                date = entry.get("date", "")

                if "FII" in category or "FPI" in category:
                    result["fii"]  = {"buy": buy, "sell": sell, "net": net}
                    result["date"] = date
                elif "DII" in category:
                    result["dii"]  = {"buy": buy, "sell": sell, "net": net}

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

            combined_net = fii_net + dii_net
            result["combined_signal"] = (
                "RISK_ON"  if combined_net >  1000 else
                "RISK_OFF" if combined_net < -1000 else
                "NEUTRAL"
            )

            # If both are 0 after parse, data likely didn't populate
            if fii_net == 0 and dii_net == 0:
                print("[NSE] FII/DII both zero after parse — using fallback")
                return self._fii_dii_fallback()

            return result

        except Exception as e:
            print(f"[NSE] FII/DII parse error: {e}")
            return self._fii_dii_fallback()

    def _safe_float(self, val):
        try:
            return float(str(val).replace(",", "").strip() or 0)
        except Exception:
            return 0.0

    def _fii_dii_fallback(self):
        return {
            "date":            datetime.date.today().strftime("%d-%b-%Y"),
            "fii":             {"buy": 0, "sell": 0, "net": 0},
            "dii":             {"buy": 0, "sell": 0, "net": 0},
            "fii_signal":      "UNKNOWN",
            "dii_signal":      "UNKNOWN",
            "combined_signal": "NEUTRAL",
            "raw":             [],
            "error":           "NSE fetch failed — market may be closed or NSE blocking server IP"
        }

    # =========================
    # 📈 MARKET INDICES
    # =========================
    def get_indices(self):
        data = self._fetch("indices")
        if not data:
            return {}

        try:
            result     = {}
            index_data = data.get("data", [])

            name_map = {
                "NIFTY 50":         "nifty50",
                "NIFTY BANK":       "bank_nifty",
                "INDIA VIX":        "india_vix",
                "NIFTY MIDCAP 100": "nifty_midcap",
                "NIFTY IT":         "nifty_it",
                "NIFTY FMCG":       "nifty_fmcg",
                "NIFTY PHARMA":     "nifty_pharma",
            }

            for idx in index_data:
                name = idx.get("indexSymbol", "")
                key  = name_map.get(name)
                if not key:
                    continue

                last       = self._safe_float(idx.get("last",          0))
                prev_close = self._safe_float(idx.get("previousClose", 0))
                change_pct = self._safe_float(idx.get("percentChange", 0))
                yearly_hi  = self._safe_float(idx.get("yearHigh",      0))
                yearly_lo  = self._safe_float(idx.get("yearLow",       0))

                result[key] = {
                    "last":       last,
                    "prev_close": prev_close,
                    "change_pct": change_pct,
                    "year_high":  yearly_hi,
                    "year_low":   yearly_lo,
                    "from_52w_high": round(
                        ((last - yearly_hi) / yearly_hi * 100), 1
                    ) if yearly_hi else 0
                }

            nifty_chg = result.get("nifty50",   {}).get("change_pct", 0)
            vix_val   = result.get("india_vix", {}).get("last", 15)

            result["market_bias"] = (
                "BULLISH" if nifty_chg >  0.5 and vix_val < 16 else
                "BEARISH" if nifty_chg < -0.5 or  vix_val > 20 else
                "NEUTRAL"
            )

            return result

        except Exception as e:
            print(f"[NSE] Indices parse error: {e}")
            return {}

    # =========================
    # 📊 OPTION CHAIN (PCR)
    # =========================
    def get_pcr(self):
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
                "pcr": pcr,
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
    # =========================
    def get_full_snapshot(self):
        fii_dii = self.get_fii_dii()
        indices = self.get_indices()
        pcr     = self.get_pcr()

        # Use None when FII/DII fetch failed — fii_signal UNKNOWN means fallback was used
        fii_failed = fii_dii.get("fii_signal") == "UNKNOWN"
        fii_net = None if fii_failed else fii_dii.get("fii", {}).get("net")
        dii_net = None if fii_failed else fii_dii.get("dii", {}).get("net")
        # Treat explicit zero as None — zero is ambiguous (closed market vs actual zero flow)
        if fii_net == 0:
            fii_net = None
        if dii_net == 0:
            dii_net = None

        fii_display = f"₹{fii_net:.0f} Cr" if fii_net is not None else "None"
        print(f"[NSE] Snapshot — FII: {fii_display}, VIX: {indices.get('india_vix', {}).get('last', 'N/A')}")

        return {
            "fii_dii":       fii_dii,
            "indices":       indices,
            "pcr":           pcr,
            "timestamp":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fii_net_crore": fii_net,
            "dii_net_crore": dii_net,
            "india_vix":     indices.get("india_vix",  {}).get("last", 15),
            "nifty_change":  indices.get("nifty50",    {}).get("change_pct", 0),
            "crude_price":   0,
            "flow_signal":   fii_dii.get("combined_signal", "NEUTRAL"),
            "market_bias":   indices.get("market_bias", "NEUTRAL"),
        }