"""
scheduler.py — SENTINEL Daily Intelligence Briefing

Standalone script. Run independently of Streamlit.
Triggered by Windows Task Scheduler at 8:00 AM IST daily.

Flow:
  1. Fetch all active users from Supabase (direct HTTP)
  2. Run SENTINEL pipeline once (shared intelligence)
  2b. Run NotificationEngine — Tier 1 + 2 alerts, fatigue-controlled
  3. Generate white-labelled PDF per user
  4. Send plain text daily briefing email + PDF to each user
  5. Check for regime change — fire alert email + WhatsApp if detected
  6. Log all activity to Supabase

Run manually:
    python scheduler.py

Run with custom stress test inputs:
    python scheduler.py --repo 6.0 --deficit 4.5 --capex 11.0

Dry run (no emails or WhatsApp sent):
    python scheduler.py --dry-run

Weekly summary (add second Windows Task Scheduler job — Fridays 6pm IST):
    python scheduler.py --weekly-summary
"""

import sys
import os
import asyncio
import datetime
import argparse
import smtplib
import traceback
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email.mime.base      import MIMEBase
from email                import encoders

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# =========================
# 📦 IMPORTS
# =========================
import streamlit as st   # imported for side effects only

from supabase import create_client

from data_ingestion       import DataIngestor
from NLP                  import IndianMacroNLP
from regime_engine        import MacroRegimeEngine
from intel_aggregator     import IntelAggregator
from scenario_engine      import ScenarioEngine
from trigger_engine       import TriggerEngine
from asset_impact_engine  import AssetImpactEngine
from report_generator     import ReportGenerator
from cause_effect_engine  import CauseEffectEngine
from positioning_engine   import PositioningEngine
from liquidity_engine     import LiquidityEngine
from strategy_engine      import StrategyEngine
from decision_engine      import DecisionEngine
from schema_validator     import SchemaValidator
from schema_repair_engine import SchemaRepairEngine
from nse_data             import NSEDataFetcher
from pdf_report_generator import PDFReportGenerator
from notifications        import NotificationEngine          # ← ADDED
from schemas import (
    REGIME_SCHEMA, SCENARIO_SCHEMA,
    ASSET_SCHEMA, POSITIONING_SCHEMA, STRATEGY_SCHEMA
)
from economic_calendar import (
    get_upcoming_events,
    get_events_by_window,                                    # ← ADDED
    days_until_label
)


# =========================
# 🔧 SECRETS LOADER
# =========================
def load_secrets():
    try:
        import toml
        secrets_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".streamlit", "secrets.toml"
        )
        with open(secrets_path, "r") as f:
            return toml.load(f)
    except ImportError:
        import tomllib
        secrets_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".streamlit", "secrets.toml"
        )
        with open(secrets_path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"[Scheduler] Could not load secrets.toml: {e}")
        return {}


secrets = load_secrets()


def get_secret(key, default=None):
    return secrets.get(key, default)


# ── Mandatory disclaimers — do not remove ──────────────────────────────────
DISCLAIMER = """
<div style="
  margin-top: 32px;
  padding-top: 16px;
  border-top: 1px solid #e2e8f0;
  font-size: 11px;
  color: #94a3b8;
  line-height: 1.6;
  font-family: Arial, sans-serif;
">
  <strong>Important Disclaimer:</strong>
  This briefing is produced by Sentinel,
  an automated macroeconomic research
  system operated by EconIq. It is
  provided for informational purposes
  only and does not constitute investment
  advice, financial advice, or any
  recommendation to buy or sell any
  security or asset. Past regime
  classifications do not guarantee future
  results. Users are solely responsible
  for their own investment decisions.
  EconIq is not registered as a SEBI
  Research Analyst and does not provide
  regulated investment advisory services.
  <br><br>
  &copy; EconIq &middot; Sentinel Macro Intelligence &middot;
  Not investment advice.
</div>
"""

DISCLAIMER_TEXT = (
    "\n\n" + "─" * 56 + "\n"
    "IMPORTANT DISCLAIMER\n"
    "This briefing is produced by Sentinel, an automated macroeconomic\n"
    "research system operated by EconIq. It is provided for informational\n"
    "purposes only and does not constitute investment advice, financial\n"
    "advice, or any recommendation to buy or sell any security or asset.\n"
    "Past regime classifications do not guarantee future results. Users\n"
    "are solely responsible for their own investment decisions. EconIq\n"
    "is not registered as a SEBI Research Analyst and does not provide\n"
    "regulated investment advisory services.\n\n"
    "© EconIq · Sentinel Macro Intelligence · Not investment advice.\n"
    + "─" * 56
)

WHATSAPP_DISCLAIMER = (
    "\n\n_Disclaimer: This is automated "
    "macro research for informational "
    "purposes only. Not investment advice. "
    "— EconIq Sentinel_"
)

# =========================
# 🔄 DAILY DATA REFRESH
# Runs at 6:00 AM IST (00:30 UTC) — separate Windows Task Scheduler entry:
#   python scheduler.py --refresh-data
# =========================
_fred_rate_cache: dict = {"data": {}, "fetched_at": 0}


async def _refresh_market_data():
    """
    Daily refresh of automated data sources:
    - FRED policy rates cache clear
    - FBIL yield cache warm-up
    - Global macro Supabase cache clear (forces fresh World Bank + FRED
      fetch on next request to /api/global-macro)
    """
    global _fred_rate_cache

    try:
        # Clear FRED cache to force fresh fetch
        _fred_rate_cache = {"data": {}, "fetched_at": 0}

        # Pre-fetch FRED rates via main_api's function
        try:
            from main_api import _fetch_fred_rates
            _fetch_fred_rates()
        except Exception as _e:
            print(
                f"[SCHEDULER] FRED pre-fetch skipped: {_e}",
                flush=True
            )

        # Clear global macro Supabase cache via direct HTTP
        # Forces fresh 50-economy fetch (World Bank + FRED) on next request
        supabase_url = get_secret("SUPABASE_URL", "")
        service_key  = get_secret("SUPABASE_SERVICE_KEY", "")
        if supabase_url and service_key:
            try:
                url = (
                    f"{supabase_url.rstrip('/')}"
                    f"/rest/v1/global_macro_cache"
                    f"?id=not.is.null"
                )
                r = requests.delete(
                    url,
                    headers=_sb_headers(service_key),
                    timeout=10
                )
                if r.status_code in (200, 204):
                    print(
                        "[SCHEDULER] Global macro cache cleared "
                        "for daily refresh",
                        flush=True
                    )
                else:
                    print(
                        f"[SCHEDULER] Cache clear HTTP {r.status_code}: "
                        f"{r.text[:200]}",
                        flush=True
                    )
            except Exception as _e:
                print(
                    f"[SCHEDULER] Cache clear failed: {_e}",
                    flush=True
                )

        print(
            "[SCHEDULER] Daily data refresh complete",
            flush=True
        )

    except Exception as e:
        print(
            f"[SCHEDULER] Daily refresh failed: {e}",
            flush=True
        )


# =========================
# 🛡️ SAFETY LAYER
# =========================
def ensure_dict(obj, name="Unknown"):
    import json
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            print(f"  [Pipeline] {name} returned string — replaced with {{}}")
            return {}
    if not isinstance(obj, dict):
        print(f"  [Pipeline] {name} returned "
              f"{type(obj).__name__} — replaced with {{}}")
        return {}
    return obj


# =========================
# 👥 DIRECT HTTP USER FETCH
# =========================
def fetch_users_direct(supabase_url, service_key):
    base    = supabase_url.rstrip("/")
    url     = (
        f"{base}/rest/v1/profiles"
        f"?select=id,email,full_name,firm_name,tier,trial_ends_at,"
        f"whatsapp_number,mandate_type,risk_tolerance,aum_size,investment_horizon"
    )
    headers = {
        "apikey":        service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type":  "application/json"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json()
        print(f"  [Error] Supabase HTTP {r.status_code}: {r.text[:300]}")
        return []
    except Exception as e:
        print(f"  [Error] Direct user fetch failed: {e}")
        return []


# =========================
# 💾 SUPABASE DIRECT HTTP WRITERS
# =========================
def _sb_headers(service_key):
    return {
        "apikey":        service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal"
    }


def log_email_direct(supabase_url, service_key,
                     user_id, recipient, subject,
                     regime, status, error=None):
    url = f"{supabase_url.rstrip('/')}/rest/v1/email_logs"
    payload = {
        "user_id":   user_id,
        "recipient": recipient,
        "subject":   subject,
        "regime":    regime,
        "status":    status,
        "error":     error[:300] if error else None
    }
    try:
        requests.post(url, headers=_sb_headers(service_key),
                      json=payload, timeout=10)
    except Exception as e:
        print(f"  [Log] Failed to write email log: {e}")


def log_regime_alert(supabase_url, service_key,
                     previous_regime, new_regime,
                     confidence, users_notified):
    url = f"{supabase_url.rstrip('/')}/rest/v1/regime_alerts"
    payload = {
        "previous_regime": previous_regime,
        "new_regime":      new_regime,
        "confidence":      confidence,
        "users_notified":  users_notified,
        "alert_date":      datetime.date.today().isoformat()
    }
    try:
        r = requests.post(url, headers=_sb_headers(service_key),
                          json=payload, timeout=10)
        if r.status_code in (200, 201):
            print("  [AlertLog] Regime alert logged to Supabase")
        else:
            print(f"  [AlertLog] Log failed HTTP {r.status_code}: "
                  f"{r.text[:200]}")
    except Exception as e:
        print(f"  [AlertLog] Failed to log regime alert: {e}")


def log_whatsapp_direct(supabase_url, service_key,
                        user_id, recipient, message_preview,
                        regime, conviction, status, error=None):
    url = f"{supabase_url.rstrip('/')}/rest/v1/whatsapp_logs"
    payload = {
        "user_id":         user_id,
        "recipient":       recipient,
        "message_preview": (message_preview or "")[:100],
        "regime":          regime,
        "conviction":      conviction,
        "status":          status,
        "error":           error[:300] if error else None,
    }
    try:
        requests.post(url, headers=_sb_headers(service_key),
                      json=payload, timeout=10)
    except Exception as e:
        print(f"  [Log] Failed to write WhatsApp log: {e}")


def _derive_rbi_signal_from_regime(regime_key):
    _map = {
        "MONETARY_TIGHTENING":                   "Hawkish — rising rates",
        "LIQUIDITY_TIGHTENING":                  "Tightening — watch duration",
        "LIQUIDITY_DRIVEN_EXPANSION":            "Accommodative",
        "STABLE_GROWTH":                         "Neutral to accommodative",
        "EARLY_CYCLE_RECOVERY":                  "Easing — rate cut cycle",
        "GROWTH_SLOWDOWN_SUPPORT":               "Supportive — potential cuts",
        "STAGFLATION_RISK":                      "Constrained — limited room to cut",
        "STAGFLATIONARY_RISK":                   "Constrained — limited room to cut",
        "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": "Hawkish pressure",
    }
    return _map.get(regime_key, "Monitor RBI guidance")


def fetch_user_last_run(supabase_url, service_key, user_id):
    """Fetch the most recent run for a user. Returns None if no runs exist."""
    base = supabase_url.rstrip("/")
    url  = (
        f"{base}/rest/v1/runs"
        f"?select=regime,confidence,conviction,implied_action,allocation,summary"
        f"&user_id=eq.{user_id}"
        f"&order=run_at.desc"
        f"&limit=1"
    )
    headers = {
        "apikey":        service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type":  "application/json",
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data[0] if data else None
        print(f"  [WA] Run fetch HTTP {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"  [WA] Run fetch failed for {user_id}: {e}")
        return None


def build_personalized_wa_briefing(user_profile, run_data,
                                    upcoming_events, dashboard_url=""):
    """Build a personalised WhatsApp morning briefing from a user's last run."""
    import json as _json

    firm_name = user_profile.get("firm_name") or "SENTINEL"
    mandate   = (user_profile.get("mandate_type") or "equity").lower()
    today_str = datetime.date.today().strftime("%d %b %Y")

    regime_key  = (run_data.get("regime") or "UNKNOWN").upper()
    regime_name = regime_key.replace("_", " ").title()
    conf        = int((run_data.get("confidence") or 0) * 100)
    conviction  = run_data.get("conviction") or "—"
    implied     = run_data.get("implied_action") or "Review full briefing."

    alloc = run_data.get("allocation") or {}
    if isinstance(alloc, str):
        try:
            alloc = _json.loads(alloc)
        except Exception:
            alloc = {}
    equity_bias = alloc.get("equity_bias", "NEUTRAL")

    if upcoming_events:
        ev         = upcoming_events[0]
        lbl        = days_until_label(ev["days_until"])
        watchpoint = f"{ev['name']} — {lbl}"
    else:
        watchpoint = "Monitor macro conditions"

    if mandate == "debt":
        mandate_line = f"Rate environment: {_derive_rbi_signal_from_regime(regime_key)}."
    elif mandate in ("balanced", "multi_asset"):
        mandate_line = f"Cross-asset: {implied.lower()}"
    else:
        mandate_line = f"Equity bias is {equity_bias}."

    url_line = f"\nFull dashboard: {dashboard_url}" if dashboard_url else ""

    return (
        f"{firm_name} | Sentinel Briefing — {today_str}\n\n"
        f"Regime: {regime_name}\n"
        f"Confidence: {conf}% | Conviction: {conviction}\n\n"
        f"Signal: {implied}\n\n"
        f"Watchpoint: {watchpoint}\n\n"
        f"{mandate_line}"
        f"{url_line}\n"
        f"_Not investment advice._"
    ) + WHATSAPP_DISCLAIMER


def build_whatsapp_briefing(pipeline, firm_name, upcoming_events, dashboard_url=""):
    reg        = pipeline["regime"]
    strat      = pipeline["strategy"]
    dec        = pipeline["decision"]
    regime_name = reg.get("regime", "UNKNOWN").replace("_", " ").title()
    conf       = int(reg.get("confidence", 0) * 100)
    conviction = strat.get("conviction", "MEDIUM")
    action     = dec.get("summary", "Review full briefing.")
    if len(action) > 120:
        action = action[:117] + "..."
    today_str  = datetime.date.today().strftime("%d %b %Y")
    display_firm = firm_name or "SENTINEL"

    watchpoint = ""
    if upcoming_events:
        ev  = upcoming_events[0]
        lbl = days_until_label(ev["days_until"])
        watchpoint = f"{ev['name']} — {lbl}"
    else:
        watchpoint = "Monitor regime confidence trajectory"

    url_line = f"\nFull dashboard: {dashboard_url}" if dashboard_url else ""

    return (
        f"{display_firm} | Sentinel Briefing — {today_str}\n"
        f"Regime: {regime_name} | Confidence: {conf}% | Conviction: {conviction}\n"
        f"Action: {action}\n"
        f"Watchpoint: {watchpoint}"
        f"{url_line}\n"
        f"_Not investment advice._"
    ) + WHATSAPP_DISCLAIMER


def already_alerted_today(supabase_url, service_key, new_regime):
    today = datetime.date.today().isoformat()
    url   = (
        f"{supabase_url.rstrip('/')}/rest/v1/regime_alerts"
        f"?alert_date=eq.{today}"
        f"&new_regime=eq.{new_regime}"
        f"&limit=1"
    )
    headers = {
        "apikey":        service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type":  "application/json"
    }
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            return len(r.json()) > 0
        return False
    except Exception:
        return False


# =========================
# 🚀 PIPELINE RUNNER
# =========================
def run_pipeline(repo=5.25, deficit=4.3, capex=12.2):
    print(f"  [Pipeline] Starting — "
          f"repo={repo}%, deficit={deficit}%, capex={capex}L Cr")

    ingestor    = DataIngestor()
    nlp         = IndianMacroNLP()
    regime_eng  = MacroRegimeEngine()
    aggregator  = IntelAggregator()
    scenario    = ScenarioEngine()
    trigger     = TriggerEngine()
    asset       = AssetImpactEngine()
    report_gen  = ReportGenerator()
    cause       = CauseEffectEngine()
    positioning = PositioningEngine()
    liquidity   = LiquidityEngine()
    strategy    = StrategyEngine()
    decision    = DecisionEngine()
    validator   = SchemaValidator()
    repair      = SchemaRepairEngine()
    nse         = NSEDataFetcher()

    news_raw = ingestor.fetch_news_sentiment()
    macro    = ingestor.fetch_macro_indicators()
    market   = ingestor.fetch_market_data()

    news = (
        " ".join(news_raw.get("headlines", []))
        if isinstance(news_raw, dict) else str(news_raw)
    )

    nse_snapshot = {}
    try:
        nse_snapshot = nse.get_full_snapshot()
        print("  [Pipeline] NSE snapshot: OK")
    except Exception as e:
        print(f"  [Pipeline] NSE unavailable: {e} — using fallback")
        nse_snapshot = {
            "fii_dii": {}, "indices": {},
            "fii_net_crore": 0, "india_vix": 15,
            "pcr": 1.0, "flow_signal": "NEUTRAL"
        }

    intel = nlp.get_regime_scores(news)
    intel["hard_data"].update({
        "repo_rate":      repo,
        "fiscal_deficit": deficit,
        "capex_lakh_cr":  capex,
        "gdp_growth":     macro.get("growth", {}).get("gdp", 7.2),
        "fii_net_crore":  nse_snapshot.get("fii_net_crore", 0),
        "india_vix":      nse_snapshot.get("india_vix", 15),
        "nifty_pcr":      nse_snapshot.get("pcr", 1.0),
    })
    print(f"  [Pipeline] NLP: {intel.get('provider', 'keyword')} — "
          f"theme: {intel.get('dominant_theme', '')[:40]}")

    try:
        liq = ensure_dict(
            liquidity.analyze(intel, market, nse_snapshot), "Liquidity"
        )
    except TypeError:
        liq = ensure_dict(liquidity.analyze(intel, market), "Liquidity")

    reg = ensure_dict(regime_eng.detect_regime(intel, liq), "Regime")
    reg = repair.repair(reg, REGIME_SCHEMA)

    _PRO_RISK = {
        "LIQUIDITY_DRIVEN_EXPANSION",
        "EARLY_CYCLE_RECOVERY",
        "STABLE_GROWTH"
    }
    _RISK_OFF = {
        "LIQUIDITY_TIGHTENING", "MONETARY_TIGHTENING",
        "GROWTH_SLOWDOWN_SUPPORT", "STAGFLATION_RISK",
        "INFLATION_PRESSURE_WITH_EXTERNAL_RISK"
    }
    if reg.get("regime") in _PRO_RISK and reg.get("confidence", 0) > 0.65:
        reg.setdefault("components", {})["equity_bias"] = "RISK_ON"
    elif reg.get("regime") in _RISK_OFF and reg.get("confidence", 0) > 0.65:
        reg.setdefault("components", {})["equity_bias"] = "RISK_OFF"

    cse       = ensure_dict(cause.analyze(intel, reg), "Cause")
    scen      = ensure_dict(
        scenario.generate_scenarios(reg, cse, nse_snapshot), "Scenarios"
    )
    scen      = repair.repair(scen, SCENARIO_SCHEMA)
    asset_out = ensure_dict(asset.analyze_assets(reg, scen, liq), "Asset")
    asset_out = repair.repair(asset_out, ASSET_SCHEMA)
    trigs     = trigger.generate_triggers(reg, cse)
    if not isinstance(trigs, list):
        trigs = []
    pos       = ensure_dict(
        positioning.generate_positioning(reg, scen, asset_out, cse, trigs),
        "Positioning"
    )
    pos       = repair.repair(pos, POSITIONING_SCHEMA)
    strat     = ensure_dict(
        strategy.generate_strategy(reg, scen, pos, trigs), "Strategy"
    )
    strat     = repair.repair(strat, STRATEGY_SCHEMA)
    dec       = ensure_dict(
        decision.generate(
            regime_output=reg, scenario_output=scen,
            asset_output=asset_out, positioning_output=pos,
            strategy_output=strat, trigger_output=trigs
        ), "Decision"
    )

    final_intel = aggregator.build_intel_packet(
        regime_output=reg, scenario_output=scen,
        asset_output=asset_out.get("assets", {}),
        triggers=trigs, positioning_output=pos,
        cause_effect_output=cse, decision_output=dec,
        strategy_output=strat
    )
    report = report_gen.generate_report(final_intel)

    print(f"  [Pipeline] Complete — "
          f"regime: {reg.get('regime')} "
          f"({int(reg.get('confidence', 0) * 100)}%) "
          f"conviction: {strat.get('conviction')}")

    return {
        "final_intel": final_intel,
        "regime":      reg,
        "strategy":    strat,
        "decision":    dec,
        "positioning": pos,
        "liquidity":   liq,
        "report":      report,
        "macro":       macro,
        "market":      market,
        "nse":         nse_snapshot,
        "intel":       intel,
    }


# ══════════════════════════════════════════════════════════════════════
# 🔔 NOTIFICATION ENGINE HELPERS
# Called from main() as Step 2b.
# Isolated into a function so it never raises and stops the scheduler.
# ══════════════════════════════════════════════════════════════════════

def _build_notif_payload(pipeline: dict) -> dict:
    """
    Maps the pipeline output dict to the shape NotificationEngine expects.
    Attempts a live yfinance fetch for VIX / INR / Crude.
    Falls back gracefully to NSE snapshot values if yfinance is unavailable.
    """
    reg       = pipeline["regime"]
    strat     = pipeline["strategy"]
    dec       = pipeline["decision"]
    nse_snap  = pipeline["nse"]
    fii_raw   = nse_snap.get("fii_dii", {})
    fii_net   = nse_snap.get(
        "fii_net_crore",
        fii_raw.get("fii", {}).get("net", 0)
    )

    # ── Yield curve (reuse if already fetched, otherwise quick fetch) ──
    india_yields = {}
    try:
        from yield_curve import get_yield_curve_data
        _yc          = get_yield_curve_data() or {}
        india_yields = _yc.get("india_yields", {})
    except Exception as e:
        print(f"  [Notifications] Yield curve fetch skipped: {e}")

    # ── Live ticker prices for signal thresholds ──
    vix_price   = nse_snap.get("india_vix", 0)
    inr_price   = 0.0
    crude_price = nse_snap.get("crude_price", 0.0)

    try:
        import yfinance as yf
        _vix_hist   = yf.Ticker("^INDIAVIX").history(period="2d")
        _inr_hist   = yf.Ticker("USDINR=X").history(period="2d")
        _crude_hist = yf.Ticker("CL=F").history(period="2d")
        if not _vix_hist.empty:
            vix_price   = float(_vix_hist["Close"].iloc[-1])
        if not _inr_hist.empty:
            inr_price   = float(_inr_hist["Close"].iloc[-1])
        if not _crude_hist.empty:
            crude_price = float(_crude_hist["Close"].iloc[-1])
    except Exception as e:
        print(f"  [Notifications] yfinance fetch skipped: {e} "
              f"— using NSE snapshot values")

    # ── RBI / macro events in next 48 hours ──
    rbi_events = []
    try:
        rbi_events = [
            {
                "name":        ev["name"],
                "date":        ev["event_date"].strftime("%d %b %Y"),
                "hours_ahead": ev["days_until"] * 24,
            }
            for ev in get_events_by_window(days_ahead=2)
            if ev.get("category") in ("RBI MPC", "CPI", "GDP")
            and not ev.get("released", False)
        ]
    except Exception as e:
        print(f"  [Notifications] Calendar fetch skipped: {e}")

    # ── Assemble payload ──
    return {
        # Regime
        "regime":            reg.get("regime", ""),
        "confidence":        reg.get("confidence", 0) * 100,
        "challenger_regime": reg.get("challenger", ""),
        "challenger_conf":   (
            reg.get("components", {})
               .get("challenger_confidence", 0) * 100
        ),
        "previous_regime":   (
            reg.get("change_info", {})
               .get("previous_regime", "")
        ),
        "regime_stable":     not reg.get("change_info", {})
                                     .get("changed", False),

        # Playbook
        "playbook": {
            "recommendation": dec.get("summary", ""),
            "equity_stance":  reg.get("components", {})
                                 .get("equity_bias", "NEUTRAL"),
            "top_actions":    (
                strat.get("playbook", [])[:3]
                if isinstance(strat.get("playbook"), list)
                else []
            ),
        },

        # Market signals — notification engine expects these key names
        "ticker_data": {
            "VIX":   {"price": vix_price},
            "INR":   {"price": inr_price},
            "Crude": {"price": crude_price},
        },

        # Yield
        "yield_data": {
            "10Y": {
                "price":      india_yields.get("10Y", 6.85),
                "change_bps": 0,    # intraday bps change — not yet tracked
            }
        },

        # FII flows
        "fii_data": {
            "net_flow_crore":           fii_net,
            "consecutive_outflow_days": nse_snap.get(
                "consecutive_outflow_days", 0
            ),
            "rolling_5d": fii_net,  # single-day proxy until rolling is tracked
        },

        # Calendar events
        "rbi_events": rbi_events,
    }


def run_notification_engine(supabase_client, pipeline: dict,
                             dry_run: bool = False) -> None:
    """
    Step 2b. Wraps NotificationEngine.check_and_send_all().
    In dry-run mode, prints what *would* have fired without sending.
    Never raises — all exceptions are caught and logged.
    """
    print(f"\n  [Step 2b] Running notification engine...")

    if dry_run:
        print("  [Step 2b] DRY RUN — conditions will be evaluated "
              "but no notifications will be sent.")

    try:
        engine  = NotificationEngine(supabase_client)
        payload = _build_notif_payload(pipeline)

        if dry_run:
            # In dry-run mode: log what conditions are met without sending.
            # We temporarily monkey-patch the delivery functions.
            import notifications as _notif_module
            _orig_email    = _notif_module._send_email
            _orig_whatsapp = _notif_module._send_whatsapp

            _dry_log = []

            def _dry_email(to, subject, body_html):
                _dry_log.append(f"  [DRY EMAIL] → {to} | {subject}")
                return True

            def _dry_whatsapp(to, text):
                _dry_log.append(f"  [DRY WHATSAPP] → {to} | {text[:60]}...")
                return True

            _notif_module._send_email    = _dry_email
            _notif_module._send_whatsapp = _dry_whatsapp

            try:
                engine.check_and_send_all(payload)
            finally:
                _notif_module._send_email    = _orig_email
                _notif_module._send_whatsapp = _orig_whatsapp

            if _dry_log:
                for line in _dry_log:
                    print(line)
            else:
                print("  [Step 2b] No alert conditions met — nothing to send.")

        else:
            engine.check_and_send_all(payload)
            print("  [Step 2b] Notification engine check complete.")

    except Exception as e:
        # Never let notification errors kill the scheduler
        print(f"  [Step 2b] Notification engine error: {e}")
        traceback.print_exc()


def run_weekly_summary(supabase_client, pipeline: dict,
                       dry_run: bool = False) -> None:
    """
    Separate entry point for the Friday 6pm IST weekly digest.
    Add a second Windows Task Scheduler job:
        Trigger : Weekly, Friday, 18:00 IST
        Action  : python scheduler.py --weekly-summary
    """
    print(f"\n  [Weekly] Sending weekly summary digest...")

    try:
        engine  = NotificationEngine(supabase_client)
        payload = _build_notif_payload(pipeline)

        # Enrich payload with week-level signals for the digest email
        reg = pipeline["regime"]
        payload["week_signals"] = [
            f"Regime: {reg.get('regime', '').replace('_', ' ').title()} "
            f"held for the week",
            f"Conviction: {pipeline['strategy'].get('conviction', '—')}",
            f"Equity bias: {reg.get('components', {}).get('equity_bias', 'NEUTRAL')}",
        ]
        payload["watch_next_week"] = [
            "Monitor challenger regime confidence trajectory",
            "RBI scheduled events — check economic calendar",
            "US Fed meeting minutes — global liquidity implications",
        ]

        if dry_run:
            print("  [Weekly] DRY RUN — weekly summary not sent.")
            return

        engine.send_weekly_summary(payload)
        print("  [Weekly] Weekly summary dispatched.")

    except Exception as e:
        print(f"  [Weekly] Weekly summary error: {e}")
        traceback.print_exc()


# =========================
# 📧 DAILY BRIEFING EMAIL BUILDER
# =========================
def build_email_body(pipeline, user_name, upcoming_events):
    reg    = pipeline["regime"]
    strat  = pipeline["strategy"]
    dec    = pipeline["decision"]
    pos    = pipeline["positioning"]
    intel  = pipeline["intel"]

    regime_name    = reg.get("regime", "—").replace("_", " ").title()
    confidence     = int(reg.get("confidence", 0) * 100)
    conviction     = strat.get("conviction", "—")
    equity_bias    = reg.get("components", {}).get("equity_bias", "NEUTRAL")
    narrative      = reg.get("narrative", "")
    drivers        = reg.get("drivers", [])[:3]
    dominant_theme = intel.get("dominant_theme", "")
    dec_summary    = dec.get("summary", "")

    sector_pos  = pos.get("sector_positioning", [])
    overweight  = [
        s.get("sector", "") for s in sector_pos
        if isinstance(s, dict) and s.get("stance") == "Overweight"
    ]
    underweight = [
        s.get("sector", "") for s in sector_pos
        if isinstance(s, dict) and s.get("stance") == "Underweight"
    ]

    alloc       = pos.get("allocation", {})
    alloc_lines = []
    for asset_name, val in alloc.items():
        pct = round(val * 100, 0) if val <= 1.0 else round(val, 0)
        alloc_lines.append(f"  {asset_name.capitalize():<16} {int(pct)}%")

    next_event_line = ""
    if upcoming_events:
        ev  = upcoming_events[0]
        lbl = days_until_label(ev["days_until"])
        next_event_line = (
            f"  Next event:  {ev['name']} — "
            f"{ev['event_date'].strftime('%d %b %Y')} ({lbl})"
        )

    now_str = datetime.datetime.now().strftime("%d %b %Y, %H:%M IST")

    body = f"""SENTINEL: Macro Intelligence Briefing
{now_str}
{"=" * 56}

Good morning{", " + user_name if user_name else ""},

Today's macro intelligence snapshot is attached as a PDF.
Here is the summary:

REGIME INTELLIGENCE
{"─" * 40}
Regime       : {regime_name}
Confidence   : {confidence}%
Conviction   : {conviction}
Equity Bias  : {equity_bias}

{narrative}

KEY MACRO DRIVERS
{"─" * 40}
{"".join(f"  • {d}" + chr(10) for d in drivers)}
NLP THEME: {dominant_theme}

PORTFOLIO POSITIONING
{"─" * 40}
{"".join(alloc_lines + [chr(10)])}
  Overweight  : {", ".join(overweight) if overweight else "—"}
  Underweight : {", ".join(underweight) if underweight else "—"}

DECISION SUMMARY
{"─" * 40}
{dec_summary}

{"UPCOMING ECONOMIC EVENTS" + chr(10) + "─" * 40 + chr(10) + next_event_line if next_event_line else ""}

{"─" * 56}
SENTINEL Macro Intelligence Terminal
Full report attached as PDF.

This briefing is generated algorithmically and is for
informational purposes only. Not investment advice.
SEBI registration not required for internal research tools.
{"─" * 56}
"""
    return body.strip() + DISCLAIMER_TEXT


# =========================
# ⚠️ REGIME CHANGE ALERT BUILDERS
# =========================
_POSITIONING_SHIFT = {
    ("LIQUIDITY_DRIVEN_EXPANSION", "STABLE_GROWTH"): {
        "implication": (
            "Liquidity tailwind fading. Shift from high-beta "
            "cyclicals toward quality growth."
        ),
        "action": (
            "Reduce mid-cap and small-cap overweights. "
            "Rotate into large-cap quality."
        ),
        "urgency": "MEDIUM"
    },
    ("LIQUIDITY_DRIVEN_EXPANSION", "MONETARY_TIGHTENING"): {
        "implication": (
            "Tightening cycle beginning. Rate-sensitive sectors "
            "face multiple compression."
        ),
        "action": (
            "Reduce Banks and Real Estate. "
            "Add IT exporters and FMCG defensives."
        ),
        "urgency": "HIGH"
    },
    ("LIQUIDITY_DRIVEN_EXPANSION", "INFLATION_PRESSURE_WITH_EXTERNAL_RISK"): {
        "implication": (
            "Inflation risk dominant. "
            "Commodity and oil shock scenario elevated."
        ),
        "action": (
            "Add Gold and short-duration bonds. "
            "Reduce domestic cyclicals."
        ),
        "urgency": "HIGH"
    },
    ("LIQUIDITY_DRIVEN_EXPANSION", "LIQUIDITY_TIGHTENING"): {
        "implication": (
            "RBI beginning active liquidity withdrawal. "
            "Financial conditions tightening rapidly."
        ),
        "action": (
            "Reduce high-beta equity. Shift to defensives "
            "and short-duration instruments."
        ),
        "urgency": "HIGH"
    },
    ("STABLE_GROWTH", "LIQUIDITY_DRIVEN_EXPANSION"): {
        "implication": (
            "Liquidity conditions improving. "
            "Risk appetite returning."
        ),
        "action": (
            "Increase equity allocation. "
            "Add cyclicals and mid-caps selectively."
        ),
        "urgency": "MEDIUM"
    },
    ("MONETARY_TIGHTENING", "EARLY_CYCLE_RECOVERY"): {
        "implication": (
            "Peak tightening may be behind us. "
            "Early recovery signals emerging."
        ),
        "action": (
            "Begin rotating into rate-sensitive sectors: "
            "Banks, Real Estate, Autos."
        ),
        "urgency": "MEDIUM"
    },
    ("GROWTH_SLOWDOWN_SUPPORT", "EARLY_CYCLE_RECOVERY"): {
        "implication": (
            "Growth trough appears to be forming. "
            "Recovery positioning warranted."
        ),
        "action": (
            "Reduce defensive overweight. "
            "Selectively add quality cyclicals."
        ),
        "urgency": "MEDIUM"
    },
    ("STABLE_GROWTH", "STAGFLATION_RISK"): {
        "implication": (
            "Stagflation risk emerging — high inflation "
            "meeting weak growth. Most challenging macro environment."
        ),
        "action": (
            "Add Gold strongly. Rotate into IT and Pharma exporters. "
            "Avoid domestic cyclicals entirely."
        ),
        "urgency": "HIGH"
    },
}

_DEFAULT_SHIFT = {
    "implication": (
        "Macro regime has shifted. Review portfolio "
        "positioning against new regime playbook."
    ),
    "action": (
        "Consult full SENTINEL briefing for "
        "detailed allocation recommendations."
    ),
    "urgency": "MEDIUM"
}

_URGENCY_PREFIX = {
    "HIGH":   "🚨 URGENT ACTION REQUIRED",
    "MEDIUM": "⚠️  REGIME CHANGE ALERT",
    "LOW":    "ℹ️  REGIME NOTE"
}


def _fmt(regime):
    return regime.replace("_", " ").title()


def build_alert_email(change_info, regime_output, user_name):
    prev      = change_info.get("previous_regime", "")
    curr      = change_info.get("current_regime",  "")
    conf      = change_info.get("confidence", 0.0)

    shift     = _POSITIONING_SHIFT.get((prev, curr), _DEFAULT_SHIFT)
    urgency   = shift["urgency"]
    prefix    = _URGENCY_PREFIX.get(urgency, "⚠️  REGIME CHANGE ALERT")

    drivers   = regime_output.get("drivers",   [])[:3]
    narrative = regime_output.get("narrative", "")[:400]

    now_str   = datetime.datetime.now().strftime("%d %b %Y, %H:%M IST")

    subject = (
        f"⚠️ SENTINEL REGIME CHANGE | "
        f"{_fmt(prev)} → {_fmt(curr)} | "
        f"{int(conf * 100)}% Confidence"
    )

    body = f"""SENTINEL: Regime Change Alert
{now_str}
{"=" * 56}

{prefix}

{"Good morning, " + user_name + "." if user_name else ""}

SENTINEL has detected a confirmed change in the India
macro regime during today's morning analysis.

REGIME TRANSITION
{"─" * 40}
  Previous  : {_fmt(prev)}
  New       : {_fmt(curr)}
  Confidence: {int(conf * 100)}%

WHAT THIS MEANS
{"─" * 40}
{shift["implication"]}

RECOMMENDED ACTION
{"─" * 40}
{shift["action"]}

KEY MACRO DRIVERS (New Regime)
{"─" * 40}
{"".join(f"  • {d}" + chr(10) for d in drivers)}
REGIME NARRATIVE
{"─" * 40}
{narrative}{"..." if len(regime_output.get("narrative", "")) > 400 else ""}

{"─" * 56}
SENTINEL Macro Intelligence Terminal
Your full daily briefing has also been sent separately.

This is an automated regime change alert.
Not investment advice. For informational purposes only.
{"─" * 56}
"""
    return subject, body + DISCLAIMER_TEXT


def build_whatsapp_message(change_info, regime_output):
    prev    = change_info.get("previous_regime", "")
    curr    = change_info.get("current_regime",  "")
    conf    = int(change_info.get("confidence", 0.0) * 100)

    shift   = _POSITIONING_SHIFT.get((prev, curr), _DEFAULT_SHIFT)
    urgency = shift["urgency"]
    icon    = "🚨" if urgency == "HIGH" else "⚠️"

    drivers = regime_output.get("drivers", [])[:2]
    drv_txt = "".join(f"  • {d}\n" for d in drivers)

    now_str = datetime.datetime.now().strftime("%d %b %Y, %H:%M IST")

    return (
        f"{icon} *SENTINEL REGIME CHANGE*\n"
        f"{now_str}\n\n"
        f"*{_fmt(prev)}*\n"
        f"→ *{_fmt(curr)}*\n"
        f"Confidence: {conf}%\n\n"
        f"*What this means*\n"
        f"{shift['implication']}\n\n"
        f"*Recommended action*\n"
        f"{shift['action']}\n\n"
        f"*Key drivers*\n"
        f"{drv_txt}\n"
        f"_Full briefing in your email._\n"
        f"_SENTINEL · Not investment advice._"
    ) + WHATSAPP_DISCLAIMER


# =========================
# 📤 EMAIL SENDER
# =========================
def send_email(recipient_email, subject, body, pdf_bytes,
               pdf_filename, sender_email, app_password, sender_name):
    try:
        msg            = MIMEMultipart()
        msg["From"]    = f"{sender_name} <{sender_email}>"
        msg["To"]      = recipient_email
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain"))

        if pdf_bytes:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(pdf_bytes)
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{pdf_filename}"'
            )
            msg.attach(part)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, app_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())

        return True, None

    except smtplib.SMTPAuthenticationError:
        return False, (
            "Gmail authentication failed. "
            "Check GMAIL_APP_PASS in secrets.toml."
        )
    except Exception as e:
        return False, str(e)


# =========================
# 📱 WHATSAPP SENDER
# =========================
def send_whatsapp(message, account_sid, auth_token,
                   from_number, to_number):
    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        msg    = client.messages.create(
            body  = message,
            from_ = from_number,
            to    = to_number
        )
        return True, msg.sid
    except ImportError:
        return False, "Twilio not installed — run: pip install twilio"
    except Exception as e:
        return False, str(e)


# =========================
# 🏃 MAIN SCHEDULER
# =========================
def main():

    print("\n" + "=" * 56)
    print(f"  SENTINEL Daily Briefing Scheduler")
    print(f"  {datetime.datetime.now().strftime('%d %b %Y, %H:%M:%S IST')}")
    print("=" * 56)

    # -------------------------
    # CLI arguments
    # -------------------------
    parser = argparse.ArgumentParser(
        description="SENTINEL Daily Email Scheduler"
    )
    parser.add_argument("--repo",    type=float, default=5.25)
    parser.add_argument("--deficit", type=float, default=4.3)
    parser.add_argument("--capex",   type=float, default=12.2)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run pipeline and preview without sending"
    )
    parser.add_argument(
        "--weekly-summary", action="store_true",
        help="Send weekly digest instead of daily briefing "
             "(add as separate Friday 6pm Windows Task)"
    )
    parser.add_argument(
        "--refresh-data", action="store_true",
        help="Clear and pre-warm FRED/macro caches — "
             "run at 6:00 AM IST (00:30 UTC) via separate Task Scheduler entry"
    )
    args    = parser.parse_args()
    dry_run = args.dry_run

    if dry_run:
        print("  [Mode] DRY RUN — no emails or WhatsApp will be sent\n")
    if args.weekly_summary:
        print("  [Mode] WEEKLY SUMMARY run\n")

    # ── Data refresh mode — exits after cache clear ──────────────────────
    if args.refresh_data:
        print("  [Mode] DATA REFRESH — clearing and pre-warming caches\n")
        asyncio.run(_refresh_market_data())
        print(f"\n{'=' * 56}")
        print(f"  Data refresh complete — "
              f"{datetime.datetime.now().strftime('%d %b %Y, %H:%M:%S IST')}")
        print(f"{'=' * 56}\n")
        sys.exit(0)

    # -------------------------
    # Credentials
    # -------------------------
    supabase_url         = get_secret("SUPABASE_URL")
    supabase_service_key = get_secret("SUPABASE_SERVICE_KEY")
    sender_email         = get_secret("GMAIL_SENDER")
    app_password         = get_secret("GMAIL_APP_PASS")
    sender_name          = get_secret("SENTINEL_SENDER_NAME",
                                       "SENTINEL Intelligence")
    twilio_sid           = get_secret("TWILIO_ACCOUNT_SID",   "")
    twilio_token         = get_secret("TWILIO_AUTH_TOKEN",     "")
    wa_from              = get_secret("TWILIO_WHATSAPP_FROM",  "")
    wa_to                = get_secret("TWILIO_WHATSAPP_TO",    "")
    dashboard_url        = get_secret("DASHBOARD_URL",         "")

    if not supabase_url or not supabase_service_key:
        print("  [Error] SUPABASE_URL or SUPABASE_SERVICE_KEY missing.")
        sys.exit(1)

    if not sender_email or not app_password:
        print("  [Error] GMAIL_SENDER or GMAIL_APP_PASS missing.")
        sys.exit(1)

    supabase = create_client(supabase_url, supabase_service_key)
    print("  [Step 1] Supabase connected")

    # -------------------------
    # Fetch active users
    # -------------------------
    print("  [Step 1] Fetching active users from Supabase...")

    all_users_raw = fetch_users_direct(supabase_url, supabase_service_key)
    print(f"  [Step 1] Direct HTTP returned "
          f"{len(all_users_raw)} total rows")

    all_users = [
        u for u in all_users_raw
        if u.get("tier") in ("paid", "trial")
    ]

    active_users = []
    today        = datetime.date.today()
    for u in all_users:
        if u["tier"] == "paid":
            active_users.append(u)
        elif u["tier"] == "trial":
            trial_end = u.get("trial_ends_at")
            if trial_end:
                try:
                    end_date = datetime.date.fromisoformat(
                        str(trial_end)[:10]
                    )
                    if end_date >= today:
                        active_users.append(u)
                except Exception:
                    active_users.append(u)
            else:
                active_users.append(u)

    print(f"  [Step 1] {len(active_users)} active users found "
          f"({len(all_users_raw) - len(active_users)} excluded)")

    if not active_users:
        print("  [Step 1] No active users — exiting.")
        sys.exit(0)

    # -------------------------
    # Run pipeline ONCE
    # -------------------------
    print(f"\n  [Step 2] Running SENTINEL pipeline...")
    print(f"           repo={args.repo}%  deficit={args.deficit}%  "
          f"capex={args.capex}L Cr")
    try:
        pipeline = run_pipeline(
            repo    = args.repo,
            deficit = args.deficit,
            capex   = args.capex
        )
    except Exception as e:
        print(f"  [Error] Pipeline failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── Confidence gate ──────────────────────────────────────────────────
    briefing_allowed        = pipeline["regime"].get("briefing_allowed", True)
    briefing_blocked_reason = pipeline["regime"].get("briefing_blocked_reason", None)
    regime_is_unstable      = (
        pipeline["regime"]
        .get("regime_stability_flag", {})
        .get("is_unstable", False)
    )

    if not briefing_allowed:
        if dry_run:
            print(
                f"[DRY RUN] [SCHEDULER] Briefing would be SUPPRESSED — "
                f"{briefing_blocked_reason}",
                flush=True
            )
            print(
                "[DRY RUN] [SCHEDULER] No email or WhatsApp "
                "would be sent this run.",
                flush=True
            )
        else:
            print(
                f"[SCHEDULER] Briefing SUPPRESSED — "
                f"{briefing_blocked_reason}",
                flush=True
            )
            print(
                "[SCHEDULER] No email or WhatsApp "
                "sent this run.",
                flush=True
            )
            try:
                supabase.table("notification_logs").insert({
                    "type":    "briefing_suppressed",
                    "reason":  briefing_blocked_reason,
                    "sent_at": datetime.datetime.utcnow().isoformat(),
                }).execute()
            except Exception:
                pass
            return

    # ── Instability warning ──────────────────────────────────────────────
    if regime_is_unstable:
        print(
            "[SCHEDULER] Regime unstable — "
            "adding instability warning to briefing",
            flush=True
        )

    # ══════════════════════════════════════════════════════════════
    # Step 2b — NOTIFICATION ENGINE
    #
    # Runs after every pipeline execution.
    # Checks all Tier 1 + 2 alert conditions.
    # Fatigue controls enforced inside engine — safe to call always.
    # --weekly-summary flag routes to the weekly digest instead.
    # ══════════════════════════════════════════════════════════════
    if args.weekly_summary:
        run_weekly_summary(supabase, pipeline, dry_run=dry_run)
        print(f"\n{'=' * 56}")
        print(f"  Weekly summary complete — "
              f"{datetime.date.today().strftime('%d %b %Y')}")
        if dry_run:
            print(f"  (DRY RUN — nothing actually sent)")
        print(f"{'=' * 56}\n")
        sys.exit(0)
    else:
        run_notification_engine(supabase, pipeline, dry_run=dry_run)

    # -------------------------
    # Build daily subject line
    # -------------------------
    upcoming_events = get_upcoming_events(n=1)

    regime_name = (
        pipeline["regime"].get("regime", "UNKNOWN")
        .replace("_", " ").title()
    )
    confidence  = int(pipeline["regime"].get("confidence", 0) * 100)
    conviction  = pipeline["strategy"].get("conviction", "MEDIUM")
    today_str   = datetime.date.today().strftime("%d %b %Y")
    subject     = (
        f"SENTINEL | {today_str} | "
        f"{regime_name} | {confidence}% | {conviction} Conviction"
    )

    print(f"\n  [Step 3] Sending daily briefings...")
    print(f"           Subject: {subject}")

    # -------------------------
    # Step 3 — Daily briefing to all users
    # -------------------------
    sent_count   = 0
    failed_count = 0

    for u in active_users:
        user_email = u.get("email", "")
        user_name  = u.get("full_name", "") or ""
        firm_name  = u.get("firm_name",  "") or "SENTINEL Intelligence"
        user_id    = u.get("id", "")

        if not user_email:
            print(f"  [Skip] User {user_id} has no email")
            continue

        print(f"  → {user_email} ({firm_name})...", end=" ", flush=True)

        try:
            pdf_gen   = PDFReportGenerator(firm_name=firm_name)
            pdf_bytes = pdf_gen.generate(pipeline["final_intel"])
        except Exception as e:
            print(f"PDF failed ({e}) — sending without attachment")
            pdf_bytes = None

        pdf_filename = (
            f"SENTINEL_Briefing_{today_str.replace(' ', '_')}.pdf"
        )

        body = build_email_body(pipeline, user_name, upcoming_events)

        if dry_run:
            print(f"DRY RUN")
            print(
                f"\n{'─'*48}\n"
                f"DAILY BRIEFING PREVIEW:\n{body[:500]}..."
                f"\n{'─'*48}\n"
            )
            sent_count += 1
            continue

        success, error = send_email(
            recipient_email = user_email,
            subject         = subject,
            body            = body,
            pdf_bytes       = pdf_bytes,
            pdf_filename    = pdf_filename,
            sender_email    = sender_email,
            app_password    = app_password,
            sender_name     = sender_name
        )

        if success:
            print("✅ sent")
            sent_count += 1
            log_email_direct(
                supabase_url, supabase_service_key,
                user_id, user_email, subject,
                regime_name, "sent"
            )
        else:
            print(f"❌ failed: {error}")
            failed_count += 1
            log_email_direct(
                supabase_url, supabase_service_key,
                user_id, user_email, subject,
                regime_name, "failed", error
            )

    # =========================================================
    # Step 3b — PER-USER WHATSAPP DAILY BRIEFING
    # =========================================================
    wa_users = [
        u for u in active_users
        if u.get("whatsapp_number") and u["whatsapp_number"].strip()
    ]
    print(f"\n  [Step 3b] WhatsApp briefings — "
          f"{len(wa_users)} user(s) with number on file")

    if not (twilio_sid and twilio_token and wa_from):
        print("  [Step 3b] Twilio credentials missing — skipping WhatsApp")
    elif not wa_users:
        print("  [Step 3b] No users with whatsapp_number — skipping")
    else:
        wa_briefing_sent   = 0
        wa_briefing_failed = 0
        for u in wa_users:
            wa_num    = u["whatsapp_number"].strip()
            user_id   = u.get("id", "")
            firm_name = u.get("firm_name", "") or "SENTINEL"
            print(f"  → WhatsApp briefing: {wa_num} ({firm_name})...",
                  end=" ", flush=True)

            last_run = fetch_user_last_run(
                supabase_url, supabase_service_key, user_id
            )
            if not last_run:
                print("skip — no run history")
                continue

            wa_msg = build_personalized_wa_briefing(
                user_profile    = u,
                run_data        = last_run,
                upcoming_events = upcoming_events,
                dashboard_url   = dashboard_url,
            )
            run_conviction  = last_run.get("conviction") or "—"
            run_regime_name = (
                (last_run.get("regime") or "").replace("_", " ").title()
            )

            if dry_run:
                print("DRY RUN")
                print(
                    f"\n{'─'*48}\n"
                    f"WHATSAPP BRIEFING PREVIEW:\n{wa_msg}\n"
                    f"{'─'*48}\n"
                )
                wa_briefing_sent += 1
                continue

            try:
                wa_ok, wa_result = send_whatsapp(
                    message     = wa_msg,
                    account_sid = twilio_sid,
                    auth_token  = twilio_token,
                    from_number = wa_from,
                    to_number   = wa_num,
                )
            except Exception as wa_exc:
                wa_ok, wa_result = False, str(wa_exc)

            if wa_ok:
                print(f"✅ sent (SID: {wa_result})")
                wa_briefing_sent += 1
                log_whatsapp_direct(
                    supabase_url, supabase_service_key,
                    user_id, wa_num, wa_msg[:100],
                    run_regime_name, run_conviction, "sent"
                )
            else:
                print(f"❌ failed: {wa_result}")
                wa_briefing_failed += 1
                log_whatsapp_direct(
                    supabase_url, supabase_service_key,
                    user_id, wa_num, wa_msg[:100],
                    run_regime_name, run_conviction, "failed", wa_result
                )

        print(f"  [Step 3b] WhatsApp briefing sent: {wa_briefing_sent}  "
              f"failed: {wa_briefing_failed}")

    # =========================================================
    # Step 4 — REGIME CHANGE ALERT SYSTEM
    # =========================================================
    print(f"\n  [Step 4] Checking for regime change...")

    change_info = pipeline["regime"].get("change_info", {
        "changed":         False,
        "previous_regime": None,
        "current_regime":  pipeline["regime"].get("regime", ""),
        "confidence":      pipeline["regime"].get("confidence", 0.0),
        "reason":          "change_info not present in pipeline output"
    })

    print(f"  [Step 4] {change_info.get('reason', '—')}")

    if not change_info.get("changed"):
        print("  [Step 4] No alert required — regime stable")

    else:
        prev_regime = change_info.get("previous_regime", "")
        curr_regime = change_info.get("current_regime",  "")
        conf_val    = change_info.get("confidence", 0.0)

        print(f"\n  [Step 4] *** REGIME CHANGE CONFIRMED ***")
        print(f"           {prev_regime} → {curr_regime}")
        print(f"           Confidence: {int(conf_val * 100)}%")

        if not dry_run and already_alerted_today(
            supabase_url, supabase_service_key, curr_regime
        ):
            print(
                f"  [Step 4] Alert already sent today for "
                f"{curr_regime} — skipping duplicate"
            )

        else:
            print(f"  [Step 4] Sending regime change alerts...")

            alert_sent_count = 0
            wa_sent          = False

            for u in active_users:
                user_email = u.get("email", "")
                user_name  = u.get("full_name", "") or ""
                user_id    = u.get("id", "")

                if not user_email:
                    continue

                print(f"  → Alert email: {user_email}...",
                      end=" ", flush=True)

                alert_subject, alert_body = build_alert_email(
                    change_info   = change_info,
                    regime_output = pipeline["regime"],
                    user_name     = user_name
                )

                if dry_run:
                    print("DRY RUN")
                    print(
                        f"\n{'─'*48}\n"
                        f"ALERT EMAIL PREVIEW:\n{alert_body[:500]}..."
                        f"\n{'─'*48}\n"
                    )
                    alert_sent_count += 1
                    continue

                success, error = send_email(
                    recipient_email = user_email,
                    subject         = alert_subject,
                    body            = alert_body,
                    pdf_bytes       = None,
                    pdf_filename    = "",
                    sender_email    = sender_email,
                    app_password    = app_password,
                    sender_name     = sender_name
                )

                if success:
                    print("✅ sent")
                    alert_sent_count += 1
                    log_email_direct(
                        supabase_url, supabase_service_key,
                        user_id, user_email,
                        alert_subject, curr_regime, "sent"
                    )
                else:
                    print(f"❌ failed: {error}")
                    log_email_direct(
                        supabase_url, supabase_service_key,
                        user_id, user_email,
                        alert_subject, curr_regime,
                        "failed", error
                    )

            if twilio_sid and twilio_token and wa_from and wa_to:
                print(f"  → WhatsApp: {wa_to}...",
                      end=" ", flush=True)

                wa_msg = build_whatsapp_message(
                    change_info   = change_info,
                    regime_output = pipeline["regime"]
                )

                if dry_run:
                    print("DRY RUN")
                    print(
                        f"\n{'─'*48}\n"
                        f"WHATSAPP PREVIEW:\n{wa_msg}\n"
                        f"{'─'*48}\n"
                    )
                    wa_sent = True
                else:
                    wa_ok, wa_result = send_whatsapp(
                        message     = wa_msg,
                        account_sid = twilio_sid,
                        auth_token  = twilio_token,
                        from_number = wa_from,
                        to_number   = wa_to
                    )
                    if wa_ok:
                        print(f"✅ sent (SID: {wa_result})")
                        wa_sent = True
                    else:
                        print(f"❌ failed: {wa_result}")
            else:
                print(
                    "  [Step 4] WhatsApp skipped — "
                    "Twilio credentials not configured in secrets.toml"
                )

            if not dry_run:
                log_regime_alert(
                    supabase_url     = supabase_url,
                    service_key      = supabase_service_key,
                    previous_regime  = prev_regime,
                    new_regime       = curr_regime,
                    confidence       = conf_val,
                    users_notified   = alert_sent_count
                )

            print(f"\n  [Step 4] Alert summary:")
            print(f"           Emails sent : {alert_sent_count}")
            print(
                f"           WhatsApp    : "
                f"{'✅ sent' if wa_sent else '❌ not sent / not configured'}"
            )

    # -------------------------
    # Final summary
    # -------------------------
    print(f"\n{'=' * 56}")
    print(f"  Briefing complete — {today_str}")
    print(f"  Daily briefing sent : {sent_count}")
    print(f"  Daily briefing fail : {failed_count}")
    print(
        f"  Regime change alert : "
        f"{'FIRED' if change_info.get('changed') else 'not required'}"
    )
    if dry_run:
        print(f"  (DRY RUN — nothing actually sent)")
    print(f"{'=' * 56}\n")


if __name__ == "__main__":
    main()