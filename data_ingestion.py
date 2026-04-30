import requests
import streamlit as st
import feedparser
from datetime import datetime


class DataIngestor:
    def __init__(self):
        # Alpha Vantage — news sentiment
        try:
            self.av_api_key = st.secrets["AV_API_KEY"]
        except Exception:
            self.av_api_key = "demo"

        # Trading Economics — macro fallback
        try:
            self.te_api_key = st.secrets["TE_API_KEY"]
        except Exception:
            self.te_api_key = "guest:guest"

        # FRED — US macro (free, register at fred.stlouisfed.org)
        try:
            self.fred_api_key = st.secrets["FRED_API_KEY"]
        except Exception:
            self.fred_api_key = None

        # data.gov.in — India official macro (free, register at data.gov.in)
        try:
            self.datagov_key = st.secrets["DATAGOV_KEY"]
        except Exception:
            self.datagov_key = None

        self.av_url   = "https://www.alphavantage.co/query"
        self.fred_url = "https://api.stlouisfed.org/fred/series/observations"

    # =========================
    # 🧰 SAFE REQUEST
    # =========================
    def _safe_request(self, url, params=None):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(
                url, params=params,
                headers=headers,
                timeout=8
            )
            if res.status_code != 200:
                return {}
            data = res.json()
            return data if isinstance(data, (dict, list)) else {}
        except Exception:
            return {}

    # =========================
    # 📰 NEWS SENTIMENT
    # Three sources: ET RSS + Moneycontrol RSS + Alpha Vantage
    # =========================
    @st.cache_data(ttl=1800)
    def fetch_news_sentiment(_self):
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
        if _self.av_api_key and _self.av_api_key != "demo":
            try:
                params = {
                    "function": "NEWS_SENTIMENT",
                    "tickers":  "FOREX:INR",
                    "topics":   "economy_macro",
                    "apikey":   _self.av_api_key
                }
                data = _self._safe_request(_self.av_url, params=params)
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

        # Deduplicate while preserving order
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
    @st.cache_data(ttl=3600)
    def fetch_macro_indicators(_self):

        # Hardcoded fallback baseline — always present
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
                f"/indicator/interest%20rate?c={_self.te_api_key}"
            )
            data = _self._safe_request(url)
            if isinstance(data, list) and len(data) > 0:
                val = data[0].get("Value")
                if isinstance(val, (int, float)):
                    macro["repo_rate"] = float(val)
                    macro["source"]    = "trading_economics"
        except Exception:
            pass

        # --- US Fed Funds Rate via FRED (free API key required) ---
        if _self.fred_api_key:
            try:
                params = {
                    "series_id":  "FEDFUNDS",
                    "api_key":    _self.fred_api_key,
                    "file_type":  "json",
                    "sort_order": "desc",
                    "limit":      1
                }
                data = _self._safe_request(_self.fred_url, params=params)
                obs  = data.get("observations", []) if isinstance(data, dict) else []
                if obs:
                    val = obs[0].get("value", "")
                    if val and val != ".":
                        macro["us_fed_rate"] = float(val)
                        macro["source"]      = "fred"
            except Exception:
                pass

        # --- US CPI via FRED ---
        if _self.fred_api_key:
            try:
                params = {
                    "series_id":  "CPIAUCSL",
                    "api_key":    _self.fred_api_key,
                    "file_type":  "json",
                    "sort_order": "desc",
                    "limit":      2
                }
                data = _self._safe_request(_self.fred_url, params=params)
                obs  = data.get("observations", []) if isinstance(data, dict) else []
                if len(obs) >= 2:
                    curr = float(obs[0].get("value", 0))
                    prev = float(obs[1].get("value", 1))
                    macro["us_cpi_yoy"] = round((curr - prev) / prev * 100, 2)
            except Exception:
                pass

        # --- India CPI via data.gov.in (free, official MOSPI data) ---
        if _self.datagov_key:
            try:
                url    = (
                    "https://api.data.gov.in/resource/"
                    "8e8fcf49-f1ce-4c3a-83c3-c2dca0e79869"
                )
                params = {
                    "api-key": _self.datagov_key,
                    "format":  "json",
                    "limit":   1
                }
                data    = _self._safe_request(url, params=params)
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
    @st.cache_data(ttl=900)
    def fetch_market_data(_self):

        market = {
            "equity":      {"nifty": None,    "banknifty": None},
            "fx":          {"usd_inr": None,  "dxy": None},
            "rates":       {"us10y": None},
            "commodities": {"crude_oil": None,"gold": None},
            "volatility":  {"vix": None},
            "changes":     {},
            "source":      "yahoo"
        }

        # All symbols in a single HTTP call — faster and avoids rate limits
        symbols = {
            "nifty":     "^NSEI",
            "banknifty": "^NSEBANK",
            "usd_inr":   "INR=X",
            "us10y":     "^TNX",
            "crude":     "BZ=F",       # Brent crude
            "gold":      "GC=F",
            "vix":       "^VIX",
            "dxy":       "DX-Y.NYB"
        }

        try:
            all_syms = ",".join(symbols.values())
            resp     = _self._safe_request(
                f"https://query1.finance.yahoo.com/v7/finance/quote"
                f"?symbols={all_syms}"
            )
            results = (
                resp.get("quoteResponse", {}).get("result", [])
                if isinstance(resp, dict) else []
            )

            # Build fast lookup by symbol
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

            market["equity"]["nifty"]         = price("nifty")
            market["equity"]["banknifty"]     = price("banknifty")
            market["fx"]["usd_inr"]           = price("usd_inr")
            market["fx"]["dxy"]               = price("dxy")
            market["rates"]["us10y"]          = price("us10y")
            market["commodities"]["crude_oil"]= price("crude")
            market["commodities"]["gold"]     = price("gold")
            market["volatility"]["vix"]       = price("vix")

            market["changes"] = {
                "nifty":    fmt_chg("nifty"),
                "banknifty":fmt_chg("banknifty"),
                "usd_inr":  fmt_chg("usd_inr"),
                "crude":    fmt_chg("crude"),
                "gold":     fmt_chg("gold")
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
            "timestamp": datetime.utcnow().isoformat(),
            "macro":     self.fetch_macro_indicators(),
            "market":    self.fetch_market_data(),
            "news":      self.fetch_news_sentiment()
        })