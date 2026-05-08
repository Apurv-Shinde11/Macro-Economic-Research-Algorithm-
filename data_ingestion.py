import requests
import os
import feedparser
from datetime import datetime, timezone
from functools import lru_cache


class DataIngestor:
    def __init__(self):
        # Alpha Vantage — news sentiment
        self.av_api_key = os.environ.get("AV_API_KEY", "demo")

        # Trading Economics — macro fallback
        self.te_api_key = os.environ.get("TE_API_KEY", "guest:guest")

        # FRED — US macro (free, register at fred.stlouisfed.org)
        self.fred_api_key = os.environ.get("FRED_API_KEY", None)

        # data.gov.in — India official macro (free, register at data.gov.in)
        self.datagov_key = os.environ.get("DATAGOV_KEY", None)

        # Supabase — last-known-good cache
        self.supabase_url = os.environ.get("SUPABASE_URL", None)
        self.supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", None)  # service role key for server-side writes

        self.av_url   = "https://www.alphavantage.co/query"
        self.fred_url = "https://api.stlouisfed.org/fred/series/observations"

        # NSE requires a live session cookie — initialise lazily
        self._nse_session = None

    # =========================
    # 🧰 SAFE REQUEST
    # =========================
    def _safe_request(self, url, params=None, headers=None, session=None):
        try:
            _headers = {"User-Agent": "Mozilla/5.0"}
            if headers:
                _headers.update(headers)
            caller = session if session else requests
            res = caller.get(url, params=params, headers=_headers, timeout=8)
            if res.status_code != 200:
                return {}
            data = res.json()
            return data if isinstance(data, (dict, list)) else {}
        except Exception:
            return {}

    # =========================
    # 🏦 SUPABASE HELPERS
    # =========================
    def _sb_headers(self):
        return {
            "apikey":        self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal"
        }

    def _sb_available(self):
        return bool(self.supabase_url and self.supabase_key)

    def _fetch_last_fii_dii(self):
        """Pull the most recent confirmed FII/DII row from Supabase."""
        if not self._sb_available():
            return None
        try:
            url = (
                f"{self.supabase_url}/rest/v1/fii_dii_cache"
                "?order=fetched_at.desc&limit=1"
            )
            res = requests.get(url, headers=self._sb_headers(), timeout=6)
            if res.status_code == 200:
                rows = res.json()
                if isinstance(rows, list) and rows:
                    return rows[0]
        except Exception:
            pass
        return None

    def _store_fii_dii(self, row: dict):
        """Upsert a successful FII/DII fetch into Supabase for future fallback."""
        if not self._sb_available():
            return
        try:
            url = f"{self.supabase_url}/rest/v1/fii_dii_cache"
            headers = {**self._sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
            requests.post(url, json=row, headers=headers, timeout=6)
        except Exception:
            pass

    # =========================
    # 🏛️  NSE SESSION
    # NSE blocks direct API calls — we need a warm session with cookies first.
    # =========================
    def _get_nse_session(self):
        if self._nse_session:
            return self._nse_session
        try:
            s = requests.Session()
            s.headers.update({
                "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer":         "https://www.nseindia.com/"
            })
            # Warm up — sets cookies
            s.get("https://www.nseindia.com", timeout=10)
            self._nse_session = s
            return s
        except Exception:
            return None

    # =========================
    # 📊 FII / DII FLOWS  ← THE FIX
    # Primary  : NSE live API
    # Fallback : Supabase last-known-good  (stale=True + timestamp)
    # =========================
    def fetch_fii_dii(self):
        """
        Returns a dict:
        {
          "fii_net_crore": float,
          "dii_net_crore": float,
          "fii_buy":  float, "fii_sell":  float,
          "dii_buy":  float, "dii_sell":  float,
          "trade_date": "DD-Mon-YYYY",
          "stale":  bool,          # True  → value from Supabase cache
          "cached_at": str | None, # ISO timestamp of cached row (stale only)
          "source": str
        }
        """
        result = self._try_nse_fii_dii()

        if result:
            # Persist the fresh value so future stale-fallback is accurate
            self._store_fii_dii({
                "fetched_at":    datetime.now(timezone.utc).isoformat(),
                "trade_date":    result.get("trade_date"),
                "fii_net_crore": result["fii_net_crore"],
                "dii_net_crore": result["dii_net_crore"],
                "fii_buy":       result.get("fii_buy"),
                "fii_sell":      result.get("fii_sell"),
                "dii_buy":       result.get("dii_buy"),
                "dii_sell":      result.get("dii_sell"),
                "source":        "nse_live"
            })
            result["stale"]     = False
            result["cached_at"] = None
            return result

        # ── Live fetch failed: try Supabase last-known-good ──
        cached = self._fetch_last_fii_dii()
        if cached:
            return {
                "fii_net_crore": cached.get("fii_net_crore", 0.0),
                "dii_net_crore": cached.get("dii_net_crore", 0.0),
                "fii_buy":       cached.get("fii_buy"),
                "fii_sell":      cached.get("fii_sell"),
                "dii_buy":       cached.get("dii_buy"),
                "dii_sell":      cached.get("dii_sell"),
                "trade_date":    cached.get("trade_date"),
                "stale":         True,
                "cached_at":     cached.get("fetched_at"),
                "source":        "supabase_cache"
            }

        # ── Nothing available at all ──
        return {
            "fii_net_crore": None,
            "dii_net_crore": None,
            "fii_buy":  None, "fii_sell":  None,
            "dii_buy":  None, "dii_sell":  None,
            "trade_date": None,
            "stale":      False,
            "cached_at":  None,
            "source":     "unavailable"
        }

    def _try_nse_fii_dii(self):
        """
        Hit NSE's FII/DII API.  Returns a partial dict on success, None on failure.
        NSE returns a list of category dicts like:
          [{"category":"FII/FPI","buyValue":12000,"sellValue":10000,"netValue":2000,...}, ...]
        """
        try:
            session = self._get_nse_session()
            if not session:
                return None

            data = self._safe_request(
                "https://www.nseindia.com/api/fiidiiTradeReact",
                headers={"Referer": "https://www.nseindia.com/market-data/fii-dii-activity"},
                session=session
            )

            if not isinstance(data, list) or not data:
                return None

            # Normalise category names (NSE sometimes varies capitalisation)
            fii_row = next(
                (r for r in data if "FII" in str(r.get("category", "")).upper()),
                None
            )
            dii_row = next(
                (r for r in data if "DII" in str(r.get("category", "")).upper()),
                None
            )

            if not fii_row and not dii_row:
                return None

            def _net(row):
                if not row:
                    return 0.0
                # NSE field names vary: netValue / netPurchaseSales / net
                for key in ("netValue", "netPurchaseSales", "net"):
                    val = row.get(key)
                    if isinstance(val, (int, float)):
                        return round(float(val), 2)
                # Derive from buy/sell
                buy  = row.get("buyValue")  or row.get("grossPurchase") or 0
                sell = row.get("sellValue") or row.get("grossSales")    or 0
                return round(float(buy) - float(sell), 2)

            def _val(row, *keys):
                if not row:
                    return None
                for k in keys:
                    v = row.get(k)
                    if isinstance(v, (int, float)):
                        return round(float(v), 2)
                return None

            # Trade date — first row usually carries it
            trade_date = (
                data[0].get("date") or
                data[0].get("tradeDate") or
                datetime.now(timezone.utc).strftime("%d-%b-%Y")
            )

            return {
                "fii_net_crore": _net(fii_row),
                "dii_net_crore": _net(dii_row),
                "fii_buy":  _val(fii_row, "buyValue",  "grossPurchase"),
                "fii_sell": _val(fii_row, "sellValue", "grossSales"),
                "dii_buy":  _val(dii_row, "buyValue",  "grossPurchase"),
                "dii_sell": _val(dii_row, "sellValue", "grossSales"),
                "trade_date": trade_date,
                "source": "nse_live"
            }

        except Exception:
            return None

    # =========================
    # 📰 NEWS SENTIMENT
    # Three sources: ET RSS + Moneycontrol RSS + Alpha Vantage
    # =========================
    def fetch_news_sentiment(self):
        headlines = []
        sources_used = []

        # --- Source 1: Economic Times Markets RSS (free, no key) ---
        try:
            feed = feedparser.parse(
                "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            )
            et_headlines = [
                entry.get("title", "")
                for entry in feed.entries[:6]
                if entry.get("title")
            ]
            if et_headlines:
                headlines.extend(et_headlines)
                sources_used.append("economic_times")
        except Exception:
            pass

        # --- Source 2: Moneycontrol RSS (free, no key) ---
        try:
            feed = feedparser.parse(
                "https://www.moneycontrol.com/rss/latestnews.xml"
            )
            mc_headlines = [
                entry.get("title", "")
                for entry in feed.entries[:6]
                if entry.get("title")
            ]
            if mc_headlines:
                headlines.extend(mc_headlines)
                sources_used.append("moneycontrol")
        except Exception:
            pass

        # --- Source 3: Alpha Vantage (only if real key present) ---
        if self.av_api_key and self.av_api_key != "demo":
            try:
                params = {
                    "function": "NEWS_SENTIMENT",
                    "tickers":  "FOREX:INR",
                    "topics":   "economy_macro",
                    "apikey":   self.av_api_key
                }
                data = self._safe_request(self.av_url, params=params)
                feed = data.get("feed", []) if isinstance(data, dict) else []
                av_headlines = [
                    item.get("title", "")
                    for item in feed[:6]
                    if isinstance(item, dict) and item.get("title")
                ]
                if av_headlines:
                    headlines.extend(av_headlines)
                    sources_used.append("alphavantage")
            except Exception:
                pass

        seen = set()
        unique_headlines = []
        for h in headlines:
            if h not in seen:
                seen.add(h)
                unique_headlines.append(h)

        return {
            "headlines": unique_headlines if unique_headlines else [
                "No major macro headlines available"
            ],
            "count":   len(unique_headlines),
            "sources": sources_used if sources_used else ["fallback"]
        }

    # =========================
    # 📊 MACRO INDICATORS
    # Sources: Trading Economics + FRED + data.gov.in + hardcoded fallback
    # =========================
    def fetch_macro_indicators(self):

        macro = {
            "repo_rate":   6.5,
            "us_fed_rate": 5.25,
            "inflation": {
                "headline": 5.2,
                "core":     None,
                "food":     None
            },
            "growth": {
                "gdp": 7.2,
                "iip": None
            },
            "liquidity":  None,
            "fii_flows":  None,
            "source":     "fallback"
        }

        # --- RBI Repo Rate via Trading Economics ---
        try:
            url  = (
                f"https://api.tradingeconomics.com/country/india"
                f"/indicator/interest%20rate?c={self.te_api_key}"
            )
            data = self._safe_request(url)
            if isinstance(data, list) and len(data) > 0:
                val = data[0].get("Value")
                if isinstance(val, (int, float)):
                    macro["repo_rate"] = float(val)
                    macro["source"]    = "trading_economics"
        except Exception:
            pass

        # --- US Fed Funds Rate via FRED ---
        if self.fred_api_key:
            try:
                params = {
                    "series_id":  "FEDFUNDS",
                    "api_key":    self.fred_api_key,
                    "file_type":  "json",
                    "sort_order": "desc",
                    "limit":      1
                }
                data = self._safe_request(self.fred_url, params=params)
                obs  = data.get("observations", []) if isinstance(data, dict) else []
                if obs:
                    val = obs[0].get("value", "")
                    if val and val != ".":
                        macro["us_fed_rate"] = float(val)
                        macro["source"]      = "fred"
            except Exception:
                pass

        # --- US CPI via FRED ---
        if self.fred_api_key:
            try:
                params = {
                    "series_id":  "CPIAUCSL",
                    "api_key":    self.fred_api_key,
                    "file_type":  "json",
                    "sort_order": "desc",
                    "limit":      2
                }
                data = self._safe_request(self.fred_url, params=params)
                obs  = data.get("observations", []) if isinstance(data, dict) else []
                if len(obs) >= 2:
                    curr = float(obs[0].get("value", 0))
                    prev = float(obs[1].get("value", 1))
                    macro["us_cpi_yoy"] = round((curr - prev) / prev * 100, 2)
            except Exception:
                pass

        # --- India CPI via data.gov.in ---
        if self.datagov_key:
            try:
                url    = (
                    "https://api.data.gov.in/resource/"
                    "8e8fcf49-f1ce-4c3a-83c3-c2dca0e79869"
                )
                params = {
                    "api-key": self.datagov_key,
                    "format":  "json",
                    "limit":   1
                }
                data    = self._safe_request(url, params=params)
                records = (
                    data.get("records", []) if isinstance(data, dict) else []
                )
                if records:
                    val = records[0].get("cpi_general")
                    if val:
                        macro["inflation"]["headline"] = float(val)
                        macro["source"] = "data.gov.in"
            except Exception:
                pass

        return macro

    # =========================
    # 📈 MARKET DATA
    # Source: Yahoo Finance (all symbols in one call)
    # =========================
    def fetch_market_data(self):

        market = {
            "equity":      {"nifty": None,     "banknifty": None},
            "fx":          {"usd_inr": None,   "dxy": None},
            "rates":       {"us10y": None},
            "commodities": {"crude_oil": None, "gold": None},
            "volatility":  {"vix": None},
            "changes":     {},
            "source":      "yahoo"
        }

        symbols = {
            "nifty":     "^NSEI",
            "banknifty": "^NSEBANK",
            "usd_inr":   "INR=X",
            "us10y":     "^TNX",
            "crude":     "BZ=F",
            "gold":      "GC=F",
            "vix":       "^VIX",
            "dxy":       "DX-Y.NYB"
        }

        try:
            all_syms = ",".join(symbols.values())
            resp     = self._safe_request(
                f"https://query1.finance.yahoo.com/v7/finance/quote"
                f"?symbols={all_syms}"
            )
            results = (
                resp.get("quoteResponse", {}).get("result", [])
                if isinstance(resp, dict) else []
            )

            lookup = {
                r.get("symbol"): r
                for r in results
                if isinstance(r, dict)
            }

            def price(key):
                return lookup.get(symbols[key], {}).get("regularMarketPrice")

            def chg(key):
                return lookup.get(symbols[key], {}).get(
                    "regularMarketChangePercent"
                )

            def fmt_chg(key):
                val = chg(key)
                return round(val, 2) if isinstance(val, (int, float)) else None

            market["equity"]["nifty"]          = price("nifty")
            market["equity"]["banknifty"]      = price("banknifty")
            market["fx"]["usd_inr"]            = price("usd_inr")
            market["fx"]["dxy"]                = price("dxy")
            market["rates"]["us10y"]           = price("us10y")
            market["commodities"]["crude_oil"] = price("crude")
            market["commodities"]["gold"]      = price("gold")
            market["volatility"]["vix"]        = price("vix")

            market["changes"] = {
                "nifty":     fmt_chg("nifty"),
                "banknifty": fmt_chg("banknifty"),
                "usd_inr":   fmt_chg("usd_inr"),
                "crude":     fmt_chg("crude"),
                "gold":      fmt_chg("gold")
            }

        except Exception:
            market["source"] = "partial"

        return market

    # =========================
    # 🧠 VALIDATION
    # =========================
    def validate_data(self, data):
        if not isinstance(data, dict):
            return {}
        market = data.get("market", {})
        if not market.get("equity", {}).get("nifty"):
            market["source"] = "degraded"
        data["market"] = market
        return data

    # =========================
    # 🧠 MASTER — fetch everything
    # =========================
    def get_all_data(self):
        return self.validate_data({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "macro":     self.fetch_macro_indicators(),
            "market":    self.fetch_market_data(),
            "news":      self.fetch_news_sentiment(),
            "fii_dii":   self.fetch_fii_dii()        # ← now a top-level key
        })