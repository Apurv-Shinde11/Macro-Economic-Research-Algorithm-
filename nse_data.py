import requests
import time
import datetime
import random


# =========================
# 📡 ENDPOINTS
# =========================
NSE_BASE  = "https://www.nseindia.com"
NSE_ARCH  = "https://archives.nseindia.com"
BSE_BASE  = "https://api.bseindia.com"

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

def _get_bse_headers():
    return {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         "https://www.bseindia.com/",
        "Origin":          "https://www.bseindia.com",
        "Connection":      "keep-alive",
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
                session = self._get_session()
                session.headers.update(_get_headers())

                resp = session.get(url, timeout=15)

                if resp.status_code == 200:
                    text = resp.text.strip()
                    if not text:
                        print(f"[NSE] Empty response on {endpoint_key} attempt {attempt+1}")
                        self.session = None
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
                print(f"[NSE] JSON error on {endpoint_key}: {e}")
                self.session = None
                time.sleep(1)
            except Exception as e:
                print(f"[NSE] Error on {endpoint_key}: {e}")
                return None

        return None

    # =========================
    # 📡 BSE FII/DII SOURCE
    # =========================
    def _fetch_bse_fii_dii(self):
        """
        Fetch FII/DII data from BSE — more reliable than NSE from server environments.
        NSE aggressively blocks non-browser IPs. BSE JSON API is more permissive.
        Returns None on any failure — never returns 0 as a value.
        """
        bse_urls = [
            f"{BSE_BASE}/BseIndiaAPI/api/fiidii/w",
            f"{BSE_BASE}/BseIndiaAPI/api/FIIDIITradeReact/w",
            "https://www.bseindia.com/markets/marketinfo/fiiactivity.aspx",
        ]

        for url in bse_urls:
            try:
                resp = requests.get(url, headers=_get_bse_headers(), timeout=12)
                if resp.status_code != 200:
                    print(f"[BSE] {url} → HTTP {resp.status_code}")
                    continue

                # Try JSON parse first
                try:
                    data = resp.json()
                except ValueError:
                    print(f"[BSE] Non-JSON response from {url} — skipping")
                    continue

                # BSE may return list or dict with Table/data key
                rows = (
                    data if isinstance(data, list)
                    else data.get("Table", data.get("Table1", data.get("data", [])))
                )

                if not rows:
                    print(f"[BSE] Empty rows from {url}")
                    continue

                fii_net    = None
                dii_net    = None
                trade_date = None

                for row in rows:
                    # Handle multiple BSE field naming conventions
                    category = str(
                        row.get("FII_DII_TYPE",
                        row.get("Category",
                        row.get("category", "")))
                    ).upper()

                    net_raw = (
                        row.get("NET") or
                        row.get("Net") or
                        row.get("netValue") or
                        row.get("net_value") or
                        row.get("NetValue")
                    )

                    date_raw = (
                        row.get("TRADE_DATE") or
                        row.get("TradeDate") or
                        row.get("date") or ""
                    )

                    if net_raw is not None:
                        net_val = self._safe_float(net_raw)
                        # Treat 0 as missing data — zero flow is indistinguishable
                        # from a failed fetch at BSE
                        if "FII" in category or "FPI" in category:
                            fii_net    = net_val if net_val != 0 else None
                            trade_date = date_raw
                        elif "DII" in category:
                            dii_net = net_val if net_val != 0 else None

                if fii_net is None and dii_net is None:
                    print(f"[BSE] Parsed rows but FII and DII both null/zero — trying next URL")
                    continue

                print(f"[BSE] ✓ FII/DII fetched — FII: {fii_net}, DII: {dii_net}, date: {trade_date}")
                return {
                    "fii_net_crore": fii_net,
                    "dii_net_crore": dii_net,
                    "source":        "bse",
                    "trade_date":    str(trade_date) if trade_date else "",
                    "stale":         False,
                }

            except requests.exceptions.Timeout:
                print(f"[BSE] Timeout on {url}")
                continue
            except Exception as e:
                print(f"[BSE] Error on {url}: {e}")
                continue

        print("[BSE] All BSE URLs failed")
        return None

    # =========================
    # 📊 FII / DII FLOWS
    # =========================
    def get_fii_dii(self):
        """
        Returns FII and DII net buy/sell figures.
        Source priority: BSE → NSE primary → NSE alternate → fallback (None values)
        NEVER returns 0 as a data value — 0 means data unavailable.
        """
        # ── 1. Try BSE first (most reliable from Railway server environment) ──
        bse = self._fetch_bse_fii_dii()
        if bse and bse.get("fii_net_crore") is not None:
            fii_net = bse["fii_net_crore"]
            dii_net = bse.get("dii_net_crore")
            combined = (fii_net or 0) + (dii_net or 0)
            return {
                "date":            bse.get("trade_date", ""),
                "fii":             {"net": fii_net},
                "dii":             {"net": dii_net},
                "fii_net_crore":   fii_net,
                "dii_net_crore":   dii_net,
                "source":          "bse",
                "fii_signal":      (
                    "BUYING"  if fii_net and fii_net >  500 else
                    "SELLING" if fii_net and fii_net < -500 else
                    "NEUTRAL"
                ),
                "dii_signal":      (
                    "BUYING"  if dii_net and dii_net >  500 else
                    "SELLING" if dii_net and dii_net < -500 else
                    "NEUTRAL"
                ),
                "combined_signal": (
                    "RISK_ON"  if combined >  1000 else
                    "RISK_OFF" if combined < -1000 else
                    "NEUTRAL"
                ),
                "raw": bse,
            }

        # ── 2. BSE failed — try NSE endpoints ──
        print("[FII] BSE failed — trying NSE...")
        data = self._fetch("fii_dii")
        if not data:
            print("[NSE] Primary FII/DII endpoint failed — trying alternate...")
            data = self._fetch("fii_dii_alt")

        if not data:
            return self._fii_dii_fallback()

        try:
            result = {
                "date":   "",
                "fii":    {"buy": 0, "sell": 0, "net": 0},
                "dii":    {"buy": 0, "sell": 0, "net": 0},
                "source": "nse",
                "raw":    data,
            }

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

            # Both zero after parse = data didn't populate
            if fii_net == 0 and dii_net == 0:
                print("[NSE] FII/DII both zero after parse — using fallback")
                return self._fii_dii_fallback()

            combined = fii_net + dii_net
            result.update({
                "fii_net_crore":   fii_net if fii_net != 0 else None,
                "dii_net_crore":   dii_net if dii_net != 0 else None,
                "fii_signal":      (
                    "BUYING"  if fii_net >  500 else
                    "SELLING" if fii_net < -500 else
                    "NEUTRAL"
                ),
                "dii_signal":      (
                    "BUYING"  if dii_net >  500 else
                    "SELLING" if dii_net < -500 else
                    "NEUTRAL"
                ),
                "combined_signal": (
                    "RISK_ON"  if combined >  1000 else
                    "RISK_OFF" if combined < -1000 else
                    "NEUTRAL"
                ),
            })
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
        """
        Called when all sources fail.
        Always returns None for net values — never 0.
        0 is indistinguishable from actual zero flow and must not be stored.
        """
        return {
            "date":            datetime.date.today().strftime("%d-%b-%Y"),
            "fii":             {"buy": None, "sell": None, "net": None},
            "dii":             {"buy": None, "sell": None, "net": None},
            "fii_net_crore":   None,
            "dii_net_crore":   None,
            "source":          "unavailable",
            "fii_signal":      "UNKNOWN",
            "dii_signal":      "UNKNOWN",
            "combined_signal": "NEUTRAL",
            "raw":             [],
            "error":           "All FII/DII sources unavailable — market may be closed",
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
                    ) if yearly_hi else 0,
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
                "total_call_oi": total_call,
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
        fii_net    = None if fii_failed else fii_dii.get("fii_net_crore") or fii_dii.get("fii", {}).get("net")
        dii_net    = None if fii_failed else fii_dii.get("dii_net_crore") or fii_dii.get("dii", {}).get("net")

        # Treat explicit 0 as None — zero is ambiguous
        if fii_net == 0:
            fii_net = None
        if dii_net == 0:
            dii_net = None

        source = fii_dii.get("source", "unavailable")
        fii_display = f"₹{fii_net:.0f} Cr" if fii_net is not None else "None"
        print(f"[NSE] Snapshot — FII: {fii_display} (src: {source}), VIX: {indices.get('india_vix', {}).get('last', 'N/A')}")

        return {
            "fii_dii":       fii_dii,
            "indices":       indices,
            "pcr":           pcr.get("pcr", 1.0),
            "timestamp":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fii_net_crore": fii_net,
            "dii_net_crore": dii_net,
            "fii_dii_source": source,
            "india_vix":     indices.get("india_vix",  {}).get("last", 15),
            "nifty_change":  indices.get("nifty50",    {}).get("change_pct", 0),
            "crude_price":   None,
            "flow_signal":   fii_dii.get("combined_signal", "NEUTRAL"),
            "market_bias":   indices.get("market_bias", "NEUTRAL"),
        }