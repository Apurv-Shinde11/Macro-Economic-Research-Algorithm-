import streamlit as st
import pandas as pd
import datetime
import json
import os

from streamlit_autorefresh import st_autorefresh

from data_ingestion       import DataIngestor
from NLP                  import IndianMacroNLP
from allocation_engine    import IndianSectorOptimizer
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
from yield_curve          import get_yield_curve_data
from utils                import safe_float
from sentinel_styles      import SENTINEL_CSS, build_status_bar
from notifications        import NotificationEngine          # ← ADDED
from supabase             import create_client               # ← ADDED
from schemas import (
    REGIME_SCHEMA,
    SCENARIO_SCHEMA,
    ASSET_SCHEMA,
    POSITIONING_SCHEMA,
    STRATEGY_SCHEMA
)
from auth import (
    is_logged_in, get_profile, get_current_user,
    render_auth_page, logout, check_access,
    save_run, get_run_history
)
from economic_calendar import (
    get_upcoming_events,
    get_events_by_window,
    days_until_label,
    CATEGORY_COLORS,
    IMPORTANCE_COLORS
)


# =========================
# 🛡️ SAFETY LAYER
# =========================
def ensure_dict(obj, name="Unknown"):
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            st.warning(f"{name} returned string. Replaced with empty dict.")
            return {}
    if not isinstance(obj, dict):
        st.warning(f"{name} returned {type(obj).__name__}. Replaced with empty dict.")
        return {}
    return obj


# ══════════════════════════════════════════════════════════════════════
# 🔔 NOTIFICATION ENGINE — initialise once at startup
# Uses the service role key (same as scheduler.py) so it can write
# to notification_logs without hitting RLS.
# ══════════════════════════════════════════════════════════════════════
@st.cache_resource
def _init_notif_engine():
    try:
        _url = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))
        _key = st.secrets.get(
            "SUPABASE_SERVICE_KEY",
            os.environ.get("SUPABASE_SERVICE_KEY", "")
        )
        if _url and _key:
            _client = create_client(_url, _key)
            return NotificationEngine(_client)
    except Exception as e:
        print(f"[Notifications] Init failed: {e}")
    return None

_notif_engine = _init_notif_engine()


# =========================
# ⚙️ PAGE CONFIG + CSS
# Must be the first Streamlit call.
# SENTINEL_CSS contains the full design system —
# dark/light adaptive, all component overrides,
# typography, animations, and the status bar styles.
# =========================
st.set_page_config(
    page_title="SENTINEL: Macro Intelligence Terminal",
    layout="wide"
)
st.markdown(SENTINEL_CSS, unsafe_allow_html=True)


# =========================
# 🔐 AUTH GATE
# =========================
if not is_logged_in():
    _, centre, _ = st.columns([1, 2, 1])
    with centre:
        render_auth_page()
    st.stop()

profile                       = get_profile()
user                          = get_current_user()
can_access, reason, days_left = check_access(profile)

if not can_access:
    st.markdown("""
    <div style='text-align:center;padding:60px 0 20px 0;'>
        <div style='font-family:var(--s-display,Syne,sans-serif);
             font-size:28px;font-weight:800;letter-spacing:3px;
             color:var(--s-text,#0f172a);'>🏛️ SENTINEL</div>
    </div>
    """, unsafe_allow_html=True)
    if reason == "pending":
        st.warning(
            "Your account is pending approval. "
            "You will receive an email once access is granted. "
            "This usually takes less than 24 hours."
        )
    elif reason == "expired":
        st.warning(
            "Your trial period has ended. "
            "Please contact us to upgrade your account to continue access."
        )
    else:
        st.error("Account access issue. Please contact support.")
    if st.button("Sign out", use_container_width=False):
        logout()
    st.stop()


# =========================
# ⚙️ ENGINE INIT
# =========================
@st.cache_resource
def init_engines():
    return {
        "ingestor":    DataIngestor(),
        "nlp":         IndianMacroNLP(),
        "allocator":   IndianSectorOptimizer(),
        "regime":      MacroRegimeEngine(),
        "aggregator":  IntelAggregator(),
        "scenario":    ScenarioEngine(),
        "trigger":     TriggerEngine(),
        "asset":       AssetImpactEngine(),
        "cause":       CauseEffectEngine(),
        "positioning": PositioningEngine(),
        "liquidity":   LiquidityEngine(),
        "strategy":    StrategyEngine(),
        "decision":    DecisionEngine(),
        "report":      ReportGenerator(),
        "validator":   SchemaValidator(),
        "repair":      SchemaRepairEngine(),
        "nse":         NSEDataFetcher(),
    }

eng = init_engines()


# =====================================================
# 🏛️ HEADER + ✅ ITEM 1 — DATA FRESHNESS INDICATOR
# =====================================================
st.title("🏛️ SENTINEL: Macro Intelligence Terminal")

_history        = get_run_history(limit=1)
_last_run       = _history[0].get("run_at", "") if _history else ""
_freshness_html = ""

if _last_run:
    try:
        _last_dt   = datetime.datetime.fromisoformat(
            _last_run.replace("Z", "+00:00")
        )
        _now_utc   = datetime.datetime.now(datetime.timezone.utc)
        _age_hours = (_now_utc - _last_dt).total_seconds() / 3600
        _last_str  = _last_dt.strftime("%d %b %Y, %H:%M IST")

        if _age_hours < 6:
            _dot   = "var(--s-green)"
            _label = f"Last run: {_last_str}"
            _warn  = ""
        elif _age_hours < 24:
            _dot   = "var(--s-amber)"
            _label = f"Last run: {_last_str}"
            _warn  = " · Data may be stale"
        else:
            _dot   = "var(--s-red)"
            _label = f"Last run: {_last_str}"
            _warn  = " · ⚠️ Run the engine to refresh intelligence"

        _freshness_html = (
            f"<span style='display:inline-flex;align-items:center;gap:6px;"
            f"font-family:var(--s-mono);font-size:11px;color:var(--s-text3);'>"
            f"<span style='width:6px;height:6px;border-radius:50%;"
            f"background:{_dot};display:inline-block;flex-shrink:0;'></span>"
            f"{_label}"
            f"<span style='color:{_dot};'>{_warn}</span>"
            f"</span>"
        )
    except Exception:
        _freshness_html = ""

_now_str = datetime.datetime.now().strftime("%d %b %Y, %H:%M:%S IST")

st.markdown(
    f"<div style='display:flex;justify-content:space-between;"
    f"align-items:center;margin-bottom:4px;margin-top:-8px;'>"
    f"<span style='font-family:var(--s-mono);font-size:11px;"
    f"color:var(--s-text3);'>Page loaded: {_now_str}</span>"
    f"{_freshness_html}"
    f"</div>",
    unsafe_allow_html=True
)


# =========================
# 📡 LIVE TICKER STRIP
# =========================
st_autorefresh(interval=300_000, limit=None, key="ticker_refresh")

@st.cache_data(ttl=300)
def fetch_ticker_data():
    try:
        import yfinance as yf
        symbols = {
            "^NSEI":     "Nifty 50",
            "^NSEBANK":  "Bank Nifty",
            "^INDIAVIX": "India VIX",
            "USDINR=X":  "USD/INR",
            "GC=F":      "Gold",
            "CL=F":      "Crude",
            "^TNX":      "US 10Y",
        }
        result = {}
        for sym, label in symbols.items():
            try:
                ticker = yf.Ticker(sym)
                hist   = ticker.history(period="5d", interval="1d")
                if len(hist) >= 2:
                    prev_close = float(hist["Close"].iloc[-2])
                    last_price = float(hist["Close"].iloc[-1])
                    chg_pct    = (
                        (last_price - prev_close) / prev_close * 100
                        if prev_close else 0
                    )
                elif len(hist) == 1:
                    last_price = float(hist["Close"].iloc[-1])
                    chg_pct    = 0.0
                else:
                    last_price = 0.0
                    chg_pct    = 0.0
                result[label] = {
                    "price":   round(last_price, 2),
                    "chg_pct": round(chg_pct,    2),
                }
            except Exception:
                result[label] = {"price": 0, "chg_pct": 0}
        return result
    except Exception:
        return {}


# =========================
# 📈 YIELD CURVE — CACHED
# =========================
@st.cache_data(ttl=3600)
def fetch_yield_curve():
    try:
        return get_yield_curve_data()
    except Exception as e:
        print(f"[YieldCurve] Fetch failed: {e}")
        return None


yield_curve_data = fetch_yield_curve()
ticker_data      = fetch_ticker_data()

if ticker_data:
    ticker_items = []
    for label, d in ticker_data.items():
        price   = d.get("price",   0)
        chg_pct = d.get("chg_pct", 0)
        color = "var(--s-green)" if chg_pct >= 0 else "var(--s-red)"
        arrow = "▲" if chg_pct >= 0 else "▼"
        if price > 0:
            ticker_items.append(
                f"<span style='margin-right:22px;'>"
                f"<span style='color:var(--s-text3);font-size:10px;"
                f"font-family:var(--s-display);letter-spacing:0.5px;'>"
                f"{label}</span>&nbsp;"
                f"<span style='font-weight:500;font-size:12px;"
                f"font-family:var(--s-mono);color:var(--s-text);'>"
                f"{price:,.2f}</span>&nbsp;"
                f"<span style='color:{color};font-size:10px;"
                f"font-family:var(--s-mono);'>"
                f"{arrow} {abs(chg_pct):.2f}%</span>"
                f"</span>"
            )
    if ticker_items:
        st.markdown(
            f"<div style='background:var(--s-surface);"
            f"border:0.5px solid var(--s-border);"
            f"border-radius:var(--s-radius);padding:8px 16px;"
            f"margin-bottom:8px;overflow-x:auto;white-space:nowrap;'>"
            f"<span style='color:var(--s-blue);font-weight:800;"
            f"font-size:8px;font-family:var(--s-display);letter-spacing:2px;"
            f"margin-right:16px;text-transform:uppercase;'>LIVE</span>"
            + "".join(ticker_items) +
            f"<span style='color:var(--s-text3);font-size:9px;"
            f"font-family:var(--s-mono);margin-left:14px;'>"
            f"refreshes every 5 min</span>"
            f"</div>",
            unsafe_allow_html=True
        )

st.divider()


# =========================
# 🎛️ SIDEBAR
# =========================
st.sidebar.header("🕹️ Stress Test Controls")
st.sidebar.caption("Override macro inputs manually")

tier       = profile.get("tier", "trial")
firm       = profile.get("firm_name") or ""
name       = profile.get("full_name", "") or (user.email if user else "")
tier_email = user.email if user else ""

firm_html = (
    f"<span style='color:var(--s-text3);font-size:11px;'>{firm}</span><br>"
    if firm else ""
)

if tier == "paid":
    tier_label  = "PAID"
    tier_color  = "var(--s-green)"
    tier_bg     = "var(--s-green-dim)"
    tier_border = "rgba(34,201,122,0.25)"
elif tier == "trial":
    days_str    = f"{days_left}d left" if days_left is not None else "Trial"
    tier_label  = f"TRIAL — {days_str}"
    tier_color  = "var(--s-amber)"
    tier_bg     = "var(--s-amber-dim)"
    tier_border = "rgba(244,162,97,0.25)"
else:
    tier_label  = tier.upper()
    tier_color  = "var(--s-text3)"
    tier_bg     = "var(--s-panel)"
    tier_border = "var(--s-border)"

st.sidebar.markdown(
    f"<div class='user-card'>"
    f"<strong style='color:var(--s-text);font-family:var(--s-sans);'>"
    f"{name}</strong><br>"
    f"{firm_html}"
    f"<span style='color:var(--s-text3);font-size:11px;'>{tier_email}</span><br>"
    f"<span style='background:{tier_bg};color:{tier_color};"
    f"border:0.5px solid {tier_border};"
    f"padding:2px 8px;border-radius:4px;font-size:9px;"
    f"font-family:var(--s-display);font-weight:700;letter-spacing:0.8px;"
    f"margin-top:5px;display:inline-block;'>"
    f"{tier_label}"
    f"</span>"
    f"</div>",
    unsafe_allow_html=True
)

# Upcoming events strip
st.sidebar.markdown(
    "<div style='font-size:9px;font-weight:700;letter-spacing:1.5px;"
    "color:var(--s-text3);font-family:var(--s-display);text-transform:uppercase;"
    "margin-bottom:8px;padding-bottom:6px;border-bottom:0.5px solid var(--s-border);'>"
    "📅 Upcoming Events</div>",
    unsafe_allow_html=True
)

upcoming_events = get_upcoming_events(n=3)
for ev in upcoming_events:
    cat    = ev["category"]
    colors = CATEGORY_COLORS.get(
        cat, {"bg": "#f1f1f1", "border": "#999", "text": "#333"}
    )
    label         = days_until_label(ev["days_until"])
    urgency_color = (
        "var(--s-red)"   if ev["days_until"] <= 7  else
        "var(--s-amber)" if ev["days_until"] <= 21 else
        "var(--s-blue)"
    )
    st.sidebar.markdown(
        f"<div style='background:var(--s-panel);"
        f"border-left:2px solid {colors['border']};"
        f"border-radius:0 4px 4px 0;padding:6px 10px;margin-bottom:4px;'>"
        f"<div style='font-size:8px;color:{colors['text']};"
        f"font-family:var(--s-display);font-weight:700;"
        f"letter-spacing:1px;text-transform:uppercase;'>{cat}</div>"
        f"<div style='font-size:11px;color:var(--s-text);font-weight:500;"
        f"font-family:var(--s-sans);line-height:1.4;'>{ev['name']}</div>"
        f"<div style='font-size:9px;color:var(--s-text3);"
        f"font-family:var(--s-mono);'>"
        f"{ev['event_date'].strftime('%d %b %Y')}"
        f"&nbsp;&nbsp;"
        f"<span style='color:{urgency_color};font-weight:700;'>{label}</span>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True
    )

if not upcoming_events:
    st.sidebar.caption("No upcoming events in next 90 days.")

st.sidebar.divider()

repo    = st.sidebar.slider("Repo Rate (%)",             4.0,  7.5,  5.25, 0.25)
deficit = st.sidebar.slider("Fiscal Deficit (%)",        3.0,  6.5,  4.3,  0.1)
capex   = st.sidebar.number_input("Capex (Rs. Lakh Cr)", 5.0, 20.0, 12.2)

st.sidebar.divider()
run = st.sidebar.button(
    "▶ Run Intelligence Engine", use_container_width=True
)

st.sidebar.divider()
if st.sidebar.button("🔄 Clear Cache & Reload", use_container_width=True):
    st.cache_resource.clear()
    st.cache_data.clear()
    st.rerun()

st.sidebar.divider()
if st.sidebar.button("🚪 Sign Out", use_container_width=True):
    logout()


# =========================
# 🚀 PIPELINE EXECUTION
# =========================
if run:
    with st.spinner("Running macro intelligence pipeline..."):

        validator = eng["validator"]
        repair    = eng["repair"]

        try:
            # ── DATA INGESTION ──
            news_raw = eng["ingestor"].fetch_news_sentiment()
            macro    = eng["ingestor"].fetch_macro_indicators()
            market   = eng["ingestor"].fetch_market_data()

            news = (
                " ".join(news_raw.get("headlines", []))
                if isinstance(news_raw, dict) else str(news_raw)
            )

            # ── NSE SNAPSHOT ──
            nse_snapshot = {}
            try:
                nse_snapshot = eng["nse"].get_full_snapshot()
            except Exception as nse_err:
                st.warning(f"NSE data unavailable: {nse_err}")
                nse_snapshot = {
                    "fii_dii":       {},
                    "indices":       {},
                    "pcr":           {},
                    "fii_net_crore": 0,
                    "dii_net_crore": 0,
                    "india_vix":     15,
                    "nifty_change":  0,
                    "flow_signal":   "NEUTRAL",
                    "market_bias":   "NEUTRAL",
                }

            # ── ✅ ITEM 2 — Inject live crude into nse_snapshot ──
            _crude_live = (
                ticker_data.get("Crude", {}).get("price", 0)
                if ticker_data else 0
            )
            if _crude_live > 0:
                nse_snapshot["crude_price"] = _crude_live

            # ── NLP ──
            intel = eng["nlp"].get_regime_scores(news)
            intel["hard_data"].update({
                "repo_rate":      repo,
                "fiscal_deficit": deficit,
                "capex_lakh_cr":  capex,
                "gdp_growth":     macro.get("growth", {}).get("gdp", 7.2),
                "fii_net_crore":  nse_snapshot.get("fii_net_crore", 0),
                "india_vix":      nse_snapshot.get("india_vix",     15),
                "nifty_pcr":      nse_snapshot.get("pcr",           1.0),
            })

            # ── DEBUG (collapsed) ──
            with st.expander("🛠️ Debug Info", expanded=False):
                st.write("**News:**",   type(news_raw).__name__, "→", str(news_raw)[:300])
                st.write("**Macro:**",  type(macro).__name__,    "→", macro)
                st.write("**Market:**", type(market).__name__,   "→", market)
                st.write("**Intel:**",  intel)
                st.write("**NSE Snapshot:**", nse_snapshot)
                st.write("---")
                st.write("**🔑 Secrets diagnostic:**")
                for key in ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "AV_API_KEY"]:
                    try:
                        val = st.secrets.get(key, None)
                        if val and "your_" not in str(val) and "sk-ant-..." not in str(val):
                            st.write(f"✅ {key}: found ({str(val)[:8]}...)")
                        else:
                            st.write(f"❌ {key}: missing or placeholder")
                    except Exception as e:
                        st.write(f"❌ {key}: error — {e}")
                st.write(f"**NLP source:** {intel.get('source', 'unknown')}")
                st.write(f"**NLP provider:** {intel.get('provider', 'unknown')}")
                st.write(f"**FII signal:** {nse_snapshot.get('flow_signal', 'unknown')}")
                st.write(f"**India VIX:** {nse_snapshot.get('india_vix', '—')}")
                st.write(f"**Nifty PCR:** {nse_snapshot.get('pcr', '—')}")

            # ── CORE PIPELINE ──
            try:
                liquidity = ensure_dict(
                    eng["liquidity"].analyze(intel, market, nse_snapshot), "Liquidity"
                )
            except TypeError:
                liquidity = ensure_dict(
                    eng["liquidity"].analyze(intel, market), "Liquidity"
                )
                flow = nse_snapshot.get("flow_signal", "NEUTRAL")
                liquidity["nse_flow_signal"] = flow
                if flow == "RISK_ON":
                    liquidity["liquidity_regime"] = liquidity.get(
                        "liquidity_regime", "LIQUIDITY_EXPANSION"
                    )

            regime = ensure_dict(
                eng["regime"].detect_regime(intel, liquidity), "Regime"
            )
            regime = repair.repair(regime, REGIME_SCHEMA)
            validator.validate(regime, REGIME_SCHEMA, "Regime")

            _regime_name = regime.get("regime", "")
            _confidence  = regime.get("confidence", 0)
            _PRO_RISK = {
                "LIQUIDITY_DRIVEN_EXPANSION", "EARLY_CYCLE_RECOVERY", "STABLE_GROWTH"
            }
            _RISK_OFF = {
                "LIQUIDITY_TIGHTENING", "MONETARY_TIGHTENING",
                "GROWTH_SLOWDOWN_SUPPORT", "STAGFLATION_RISK",
                "INFLATION_PRESSURE_WITH_EXTERNAL_RISK"
            }
            if _regime_name in _PRO_RISK and _confidence > 0.65:
                regime.setdefault("components", {})["equity_bias"] = "RISK_ON"
            elif _regime_name in _RISK_OFF and _confidence > 0.65:
                regime.setdefault("components", {})["equity_bias"] = "RISK_OFF"

            cause     = ensure_dict(eng["cause"].analyze(intel, regime), "Cause")
            scenarios = ensure_dict(
                eng["scenario"].generate_scenarios(regime, cause, nse_snapshot),
                "Scenarios"
            )
            scenarios    = repair.repair(scenarios, SCENARIO_SCHEMA)
            validator.validate(scenarios, SCENARIO_SCHEMA, "Scenario")

            asset_output = ensure_dict(
                eng["asset"].analyze_assets(regime, scenarios, liquidity), "Asset"
            )
            asset_output = repair.repair(asset_output, ASSET_SCHEMA)
            validator.validate(asset_output, ASSET_SCHEMA, "Asset")

            triggers = eng["trigger"].generate_triggers(regime, cause)
            if not isinstance(triggers, list):
                triggers = []

            positioning = ensure_dict(
                eng["positioning"].generate_positioning(
                    regime, scenarios, asset_output, cause, triggers
                ), "Positioning"
            )
            positioning = repair.repair(positioning, POSITIONING_SCHEMA)
            validator.validate(positioning, POSITIONING_SCHEMA, "Positioning")

            strategy = ensure_dict(
                eng["strategy"].generate_strategy(
                    regime, scenarios, positioning, triggers
                ), "Strategy"
            )
            strategy = repair.repair(strategy, STRATEGY_SCHEMA)
            validator.validate(strategy, STRATEGY_SCHEMA, "Strategy")

            decision = ensure_dict(
                eng["decision"].generate(
                    regime_output      = regime,
                    scenario_output    = scenarios,
                    asset_output       = asset_output,
                    positioning_output = positioning,
                    strategy_output    = strategy,
                    trigger_output     = triggers
                ), "Decision"
            )

            with st.expander("🔬 Pipeline Diagnostic", expanded=False):
                st.write("**Regime:**",       regime)
                st.write("**Cause:**",        cause)
                st.write("**Scenarios:**",    scenarios)
                st.write("**Assets:**",       asset_output)
                st.write("**Triggers:**",     triggers)
                st.write("**Positioning:**",  positioning)
                st.write("**Strategy:**",     strategy)
                st.write("**Decision:**",     decision)
                st.write("**NSE Snapshot:**", nse_snapshot)
                st.write("**RBI Data:**",     regime.get("rbi_data", {}))

            final_intel = eng["aggregator"].build_intel_packet(
                regime_output       = regime,
                scenario_output     = scenarios,
                asset_output        = asset_output.get("assets", {}),
                triggers            = triggers,
                positioning_output  = positioning,
                cause_effect_output = cause,
                decision_output     = decision,
                strategy_output     = strategy
            )

            report = eng["report"].generate_report(final_intel)

            try:
                save_run(
                    regime      = regime.get("regime",     ""),
                    confidence  = regime.get("confidence", 0),
                    conviction  = strategy.get("conviction", ""),
                    repo        = repo,
                    deficit     = deficit,
                    capex       = float(capex),
                    summary     = decision.get("summary", ""),
                    report_text = report if isinstance(report, str) else "",
                    allocation  = positioning.get("allocation", {}),
                    stress_test = {
                        "repo_rate": repo,
                        "deficit":   deficit,
                        "capex":     float(capex)
                    }
                )
            except Exception as save_err:
                print(f"[Auth] save_run failed: {save_err}")

            # ══════════════════════════════════════════════════════════
            # 🔔 NOTIFICATION ENGINE — fires after every pipeline run
            #
            # Checks all Tier 1 + 2 conditions against notification_
            # preferences in Supabase. Fatigue controls are enforced
            # inside the engine — this call is always safe to make.
            # ══════════════════════════════════════════════════════════
            if _notif_engine:
                try:
                    _yc           = yield_curve_data or {}
                    _india_yields = _yc.get("india_yields", {})
                    _fii_raw      = nse_snapshot.get("fii_dii", {})
                    _fii_net      = nse_snapshot.get(
                        "fii_net_crore",
                        _fii_raw.get("fii", {}).get("net", 0)
                    )

                    # Upcoming RBI / macro events in the next 48 hours
                    _rbi_events = [
                        {
                            "name":        ev["name"],
                            "date":        ev["event_date"].strftime("%d %b %Y"),
                            "hours_ahead": ev["days_until"] * 24,
                        }
                        for ev in get_events_by_window(days_ahead=2)
                        if ev.get("category") in ("RBI MPC", "CPI", "GDP")
                        and not ev.get("released", False)
                    ]

                    _notif_engine.check_and_send_all({
                        # ── Regime ────────────────────────────────────
                        "regime":            regime.get("regime", ""),
                        "confidence":        regime.get("confidence", 0) * 100,
                        "challenger_regime": regime.get("challenger", ""),
                        "challenger_conf":   (
                            regime.get("components", {})
                                  .get("challenger_confidence", 0) * 100
                        ),
                        "previous_regime":   (
                            regime.get("change_info", {})
                                  .get("previous_regime", "")
                        ),
                        "regime_stable":     not regime.get(
                            "change_info", {}
                        ).get("changed", False),

                        # ── Playbook ──────────────────────────────────
                        "playbook": {
                            "recommendation": decision.get("summary", ""),
                            "equity_stance":  regime.get(
                                "components", {}
                            ).get("equity_bias", "NEUTRAL"),
                            "top_actions":    (
                                strategy.get("playbook", [])[:3]
                                if isinstance(strategy.get("playbook"), list)
                                else []
                            ),
                        },

                        # ── Market signals ────────────────────────────
                        # ticker_data keys match what the engine expects:
                        # "India VIX", "USD/INR", "Crude" from yfinance labels
                        "ticker_data": {
                            "VIX":   {"price": ticker_data.get("India VIX",  {}).get("price", 0)},
                            "INR":   {"price": ticker_data.get("USD/INR",    {}).get("price", 0)},
                            "Crude": {"price": ticker_data.get("Crude",      {}).get("price", 0)},
                        },

                        # ── Yield ─────────────────────────────────────
                        "yield_data": {
                            "10Y": {
                                "price":      _india_yields.get("10Y", 6.85),
                                "change_bps": 0,   # intraday bps change — not yet available
                            }
                        },

                        # ── FII flows ────────────────────────────────
                        "fii_data": {
                            "net_flow_crore":           _fii_net,
                            "consecutive_outflow_days": nse_snapshot.get(
                                "consecutive_outflow_days", 0
                            ),
                            "rolling_5d": _fii_net,   # single-day proxy until rolling is tracked
                        },

                        # ── Calendar events ───────────────────────────
                        "rbi_events": _rbi_events,
                    })
                    print("[Notifications] Engine check complete.")
                except Exception as _notif_err:
                    # Never let notification errors surface to the user
                    print(f"[Notifications] Engine error: {_notif_err}")

        except Exception as e:
            st.error(f"🚨 Pipeline Error: {str(e)}")
            st.stop()

    # ── STATUS BAR ──
    st.markdown(
        build_status_bar(
            pipeline_ran     = True,
            nse_ok           = bool(nse_snapshot.get("india_vix", 0)),
            rbi_source       = regime.get("rbi_data", {}).get("source", "fallback"),
            nlp_provider     = intel.get("provider", "none"),
            consecutive_days = regime.get("components", {}).get("consecutive_days", 0),
            persistence_adj  = regime.get("components", {}).get("persistence_adj", 0.0),
        ),
        unsafe_allow_html=True
    )

    # ── REGIME CHANGE BANNER ──
    change_info = regime.get("change_info", {})

    if change_info.get("changed"):
        prev_r = change_info.get("previous_regime", "").replace("_", " ").title()
        curr_r = change_info.get("current_regime",  "").replace("_", " ").title()
        conf_v = int(change_info.get("confidence", 0) * 100)

        high_urgency_transitions = {
            ("LIQUIDITY_DRIVEN_EXPANSION", "MONETARY_TIGHTENING"),
            ("LIQUIDITY_DRIVEN_EXPANSION", "INFLATION_PRESSURE_WITH_EXTERNAL_RISK"),
            ("LIQUIDITY_DRIVEN_EXPANSION", "LIQUIDITY_TIGHTENING"),
            ("STABLE_GROWTH",              "STAGFLATION_RISK"),
        }
        prev_key     = change_info.get("previous_regime", "")
        curr_key     = change_info.get("current_regime",  "")
        is_high      = (prev_key, curr_key) in high_urgency_transitions
        banner_class = "regime-change-banner high" if is_high else "regime-change-banner"
        icon         = "🚨" if is_high else "⚠️"
        urgency_txt  = "URGENT — " if is_high else ""

        st.markdown(
            f"<div class='{banner_class}'>"
            f"<strong style='font-family:var(--s-display);letter-spacing:0.5px;'>"
            f"{icon} {urgency_txt}REGIME CHANGE DETECTED</strong><br>"
            f"<strong>{prev_r}</strong> → <strong>{curr_r}</strong>"
            f"&nbsp;&nbsp;|&nbsp;&nbsp;Confidence: {conf_v}%<br>"
            f"<span style='font-size:13px;font-family:var(--s-sans);'>"
            f"A confirmed macro regime transition has occurred. "
            f"Review your portfolio positioning — "
            f"the playbook below reflects the new regime. "
            f"Alert emails have been dispatched to all active users."
            f"</span>"
            f"</div>",
            unsafe_allow_html=True
        )

    # =========================================================
    # 📊 DISPLAY SECTIONS
    # =========================================================

    # ── SECTION 1 — MACRO SNAPSHOT ──
    st.subheader("🧠 Macro Snapshot")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Repo Rate",      f"{repo}%")
    c2.metric("Fiscal Deficit", f"{deficit}%")
    c3.metric("GDP Growth",     f"{macro.get('growth', {}).get('gdp', 7.2)}%")
    c4.metric("Conviction",     strategy.get("conviction", "—"))

    regime_label   = regime.get("regime", "—").replace("_", " ").title()
    confidence     = int(regime.get("confidence", 0) * 100)
    challenger     = regime.get("challenger", "")
    rbi_signal     = (
        regime.get("rbi_data", {}).get("policy_direction")
        or regime.get("components", {}).get("rbi_signal", "PAUSE")
    )
    equity_bias    = regime.get("components", {}).get("equity_bias", "NEUTRAL")
    liq_regime_raw = liquidity.get("liquidity_regime", "")
    liq_regime     = (
        str(liq_regime_raw).replace("_", " ").title() if liq_regime_raw else "—"
    )

    r1, r2, r3, r4 = st.columns(4)
    r1.markdown(
        f"**Regime**<br>"
        f"<span style='font-size:17px;font-weight:600;"
        f"font-family:var(--s-serif);'>{regime_label}</span>",
        unsafe_allow_html=True
    )
    r2.markdown(
        f"**Confidence**<br>"
        f"<span style='font-size:17px;font-weight:500;"
        f"font-family:var(--s-mono);'>{confidence}%</span>",
        unsafe_allow_html=True
    )
    r3.markdown(
        f"**Liquidity Regime**<br>"
        f"<span style='font-size:17px;font-weight:500;"
        f"font-family:var(--s-mono);'>{liq_regime}</span>",
        unsafe_allow_html=True
    )
    r4.markdown(
        f"**Equity Bias**<br>"
        f"<span style='font-size:17px;font-weight:500;"
        f"font-family:var(--s-mono);'>{equity_bias}</span>",
        unsafe_allow_html=True
    )

    sig_col, chal_col = st.columns(2)
    with sig_col:
        rbi_color = (
            "var(--s-green-dim)" if rbi_signal in ("CUT",  "EASING")     else
            "var(--s-red-dim)"   if rbi_signal in ("HIKE", "TIGHTENING") else
            "var(--s-panel)"
        )
        rbi_text = (
            "var(--s-green)" if rbi_signal in ("CUT",  "EASING")     else
            "var(--s-red)"   if rbi_signal in ("HIKE", "TIGHTENING") else
            "var(--s-text3)"
        )
        rbi_border = (
            "rgba(34,201,122,0.30)" if rbi_signal in ("CUT",  "EASING")     else
            "rgba(232,64,64,0.30)"  if rbi_signal in ("HIKE", "TIGHTENING") else
            "var(--s-border)"
        )
        st.markdown(
            f"<span style='background:{rbi_color};color:{rbi_text};"
            f"border:0.5px solid {rbi_border};"
            f"padding:4px 12px;border-radius:4px;"
            f"font-size:11px;font-weight:700;"
            f"font-family:var(--s-display);letter-spacing:0.5px;'>"
            f"RBI Signal: {rbi_signal}</span>",
            unsafe_allow_html=True
        )
    with chal_col:
        if challenger:
            st.markdown(
                f"<div class='challenger-box'>⚠️ Challenger regime: "
                f"<strong>{challenger.replace('_', ' ').title()}</strong></div>",
                unsafe_allow_html=True
            )

    st.write("")

    narrative = regime.get("narrative", "")
    if narrative:
        st.markdown(
            f"<div class='regime-box'>{narrative}</div>",
            unsafe_allow_html=True
        )

    drivers = regime.get("drivers", [])
    if drivers:
        st.markdown("**Key Macro Drivers:**")
        d_cols = st.columns(min(len(drivers), 4))
        for i, d in enumerate(drivers):
            d_cols[i % 4].info(d)

    # ── NLP INTELLIGENCE BLOCK ──
    nlp_intel = regime.get("nlp_intelligence", {})

    if not nlp_intel or not nlp_intel.get("dominant_theme"):
        nlp_intel = {
            "dominant_theme": intel.get("dominant_theme",       ""),
            "key_signals":    intel.get("key_signals",          []),
            "india_risks":    intel.get("india_specific_risks", []),
            "global_factors": intel.get("global_macro_factors", []),
            "reasoning":      intel.get("reasoning",            ""),
            "nlp_confidence": intel.get("confidence",           0.0),
            "source":         intel.get("source",               "keyword"),
            "provider":       intel.get("provider",             "none")
        }

    nlp_source  = intel.get("source",   "keyword")
    provider    = intel.get("provider", "none")
    dom_theme   = nlp_intel.get("dominant_theme", "")
    nlp_conf    = int(safe_float(
        nlp_intel.get("nlp_confidence", intel.get("confidence", 0))
    ) * 100)
    key_signals = nlp_intel.get("key_signals",    [])
    india_risks = nlp_intel.get("india_risks",    intel.get("india_specific_risks", []))
    global_facs = nlp_intel.get("global_factors", intel.get("global_macro_factors", []))
    reasoning   = nlp_intel.get("reasoning",      intel.get("reasoning", ""))

    is_llm = (
        nlp_source == "llm+keyword" or
        provider not in ["none", "", None]
    )
    has_llm_content = (
        is_llm or
        (dom_theme and dom_theme != "Keyword-derived signal") or
        key_signals or india_risks or global_facs
    )

    if has_llm_content:
        st.write("")
        st.markdown("**🤖 NLP Intelligence**")

        source_badge = (
            "<span class='source-badge-llm'>LLM + Keyword</span>"
            if is_llm
            else "<span class='source-badge-keyword'>Keyword only</span>"
        )

        if provider and provider != "none":
            st.caption(f"LLM provider: {provider}")

        if dom_theme and dom_theme != "Keyword-derived signal":
            st.markdown(
                f"**Dominant Theme:** {dom_theme} &nbsp;"
                f"{source_badge} &nbsp;"
                f"<span style='font-size:12px;color:var(--s-text3);'>"
                f"NLP confidence: {nlp_conf}%</span>",
                unsafe_allow_html=True
            )

        if key_signals:
            pills = "".join(
                f"<span class='signal-pill'>{s}</span>" for s in key_signals
            )
            st.markdown(
                f"<div style='line-height:2.4;margin-bottom:8px;'>"
                f"<strong>Key Signals:</strong>&nbsp;{pills}</div>",
                unsafe_allow_html=True
            )

        if india_risks:
            pills = "".join(
                f"<span class='risk-pill'>{r}</span>" for r in india_risks
            )
            st.markdown(
                f"<div style='line-height:2.4;margin-bottom:8px;'>"
                f"<strong>India Risks:</strong>&nbsp;{pills}</div>",
                unsafe_allow_html=True
            )

        if global_facs:
            pills = "".join(
                f"<span class='global-pill'>{f}</span>" for f in global_facs
            )
            st.markdown(
                f"<div style='line-height:2.4;margin-bottom:8px;'>"
                f"<strong>Global Factors:</strong>&nbsp;{pills}</div>",
                unsafe_allow_html=True
            )

        if reasoning and reasoning != "LLM unavailable — keyword engine used.":
            st.markdown(
                f"<div class='reasoning-box'>💬 LLM Reasoning: {reasoning}</div>",
                unsafe_allow_html=True
            )

    st.divider()

    # ── SECTION 2 — SCENARIO OUTLOOK ──
    st.subheader("🔮 Scenario Outlook")

    scenario_list = scenarios.get("scenarios", [])
    if scenario_list:
        sc_cols = st.columns(len(scenario_list))
        for i, sc in enumerate(scenario_list):
            with sc_cols[i]:
                prob      = int(safe_float(sc.get("probability", 0)) * 100)
                dominance = sc.get("dominance", "")
                sc_type   = sc.get("type", "")
                bg = (
                    "var(--s-green-dim)" if sc_type == "bullish" else
                    "var(--s-red-dim)"   if sc_type == "bearish" else
                    "var(--s-blue-dim)"
                )
                border = (
                    "var(--s-green)" if sc_type == "bullish" else
                    "var(--s-red)"   if sc_type == "bearish" else
                    "var(--s-blue)"
                )
                prob_color = (
                    "var(--s-green)" if sc_type == "bullish" else
                    "var(--s-red)"   if sc_type == "bearish" else
                    "var(--s-blue)"
                )
                sc_desc = sc.get("description", "")
                st.markdown(
                    f"<div style='background:{bg};border-left:3px solid {border};"
                    f"border-radius:0 var(--s-radius-lg) var(--s-radius-lg) 0;"
                    f"padding:14px;margin-bottom:8px;'>"
                    f"<div style='font-family:var(--s-display);font-size:9px;"
                    f"font-weight:700;letter-spacing:1.2px;color:var(--s-text3);"
                    f"margin-bottom:6px;'>{sc.get('name','')}</div>"
                    f"<div style='font-size:28px;font-weight:500;font-family:var(--s-mono);"
                    f"color:{prob_color};margin:4px 0;line-height:1;'>{prob}%</div>"
                    f"<div style='font-size:10px;color:var(--s-text3);"
                    f"font-family:var(--s-display);font-weight:600;letter-spacing:0.8px;"
                    f"margin-bottom:8px;'>{dominance}</div>"
                    f"<div style='font-size:12px;line-height:1.6;"
                    f"color:var(--s-text2);font-family:var(--s-sans);'>"
                    f"{sc_desc}</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )

                sc_drivers = sc.get("drivers", [])
                if isinstance(sc_drivers, list) and sc_drivers:
                    pills = "".join(
                        f"<span class='driver-pill'>{d}</span>"
                        for d in sc_drivers[:3]
                    )
                    st.markdown(
                        f"<div style='line-height:2.4;margin-bottom:10px;'>"
                        f"<strong>Drivers:</strong>&nbsp;{pills}</div>",
                        unsafe_allow_html=True
                    )

                impact = sc.get("asset_impact", {})
                if impact:
                    st.markdown("**Asset Impact:**")
                    for asset, view in impact.items():
                        st.markdown(f"**{asset.capitalize()}:** {view}")

                key_risk = sc.get("key_risk", "")
                if key_risk:
                    st.caption(f"⚠️ Key risk: {key_risk}")

    st.divider()

    # ── SECTION 3 — PORTFOLIO POSITIONING ──
    st.subheader("🧭 Portfolio Positioning")

    pos_left, pos_right = st.columns([1, 1])

    with pos_left:
        stance           = positioning.get("stance", "—")
        horizon          = strategy.get("time_horizon", "—")
        conviction_raw   = strategy.get(
            "conviction_score",
            positioning.get("meta", {}).get("conviction", 0)
        )
        conviction_pct   = int(safe_float(conviction_raw) * 100)
        conviction_label = strategy.get("conviction", "—")

        st.markdown(f"**Stance:** {stance}")
        st.markdown(f"**Time Horizon:** {horizon}")
        st.markdown(f"**Conviction:** {conviction_label} ({conviction_pct}%)")

        alloc_raw = positioning.get("allocation", {})
        if alloc_raw:
            st.markdown("**Asset Allocation:**")
            alloc_df = pd.DataFrame(alloc_raw.items(), columns=["Asset", "Allocation %"])
            if alloc_df["Allocation %"].max() <= 1.0:
                alloc_df["Allocation %"] = (alloc_df["Allocation %"] * 100).round(1)
            else:
                alloc_df["Allocation %"] = alloc_df["Allocation %"].round(1)
            alloc_df["Allocation %"] = alloc_df["Allocation %"].astype(str) + "%"
            st.table(alloc_df)

        key_drivers = positioning.get("key_drivers", [])
        if key_drivers:
            st.markdown("**Positioning Rationale:**")
            for d in key_drivers[:3]:
                st.markdown(f"• {d}")

    with pos_right:
        sector_pos = positioning.get("sector_positioning", [])
        if sector_pos:
            st.markdown("**Sector Positioning:**")
            for sp in sector_pos:
                if isinstance(sp, dict):
                    stance_val = sp.get("stance", "Neutral")
                    badge_cls  = (
                        "ow-badge"  if stance_val == "Overweight"  else
                        "uw-badge"  if stance_val == "Underweight" else
                        "neu-badge"
                    )
                    st.markdown(
                        f"<div style='margin-bottom:6px;'>"
                        f"<span class='{badge_cls}'>{stance_val}</span>"
                        f"&nbsp;&nbsp;{sp.get('sector', '')}</div>",
                        unsafe_allow_html=True
                    )

    tactical = positioning.get("tactical_actions", [])
    if tactical:
        st.markdown("**Tactical Actions:**")
        for t in tactical:
            if isinstance(t, dict):
                st.markdown(
                    f"<div class='trigger-row'>"
                    f"<strong>{t.get('action', '')}</strong>"
                    f" &mdash; <em>{t.get('condition', '')}</em>"
                    f"</div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(f"<div class='trigger-row'>{t}</div>", unsafe_allow_html=True)

    st.divider()

    # ── SECTION 4 — STRATEGY INTELLIGENCE ──
    st.subheader("🎯 Strategy Intelligence")

    st.markdown(f"**{strategy.get('strategy_type', '—')}**")

    s1, s2, s3, s4 = st.columns(4)
    s1.markdown(
        f"**Portfolio Stance**<br>{strategy.get('portfolio_stance', '—')}",
        unsafe_allow_html=True
    )
    s2.markdown(
        f"**Confidence**<br>{round(safe_float(strategy.get('confidence', 0)) * 100)}%",
        unsafe_allow_html=True
    )
    s3.markdown(
        f"**Time Horizon**<br>{strategy.get('time_horizon', '—')}",
        unsafe_allow_html=True
    )
    s4.markdown(
        f"**Conviction**<br>{strategy.get('conviction', '—')}",
        unsafe_allow_html=True
    )

    strat_left, strat_right = st.columns([1, 1])

    with strat_left:
        playbook = strategy.get("playbook", [])
        if playbook:
            st.markdown("**Playbook:**")
            for p in playbook:
                st.markdown(f"<div class='playbook-item'>▸ {p}</div>", unsafe_allow_html=True)
        else:
            st.info("No playbook entries generated.")

        strat_drivers = strategy.get("key_drivers", [])
        if strat_drivers:
            st.write("")
            st.markdown("**Key Drivers:**")
            for d in strat_drivers[:4]:
                st.markdown(f"• {d}")

    with strat_right:
        risk_fw = strategy.get("risk_framework", {})
        if isinstance(risk_fw, dict) and risk_fw:
            st.markdown("**Risk Framework:**")
            for k, v in risk_fw.items():
                st.markdown(f"**{k.replace('_', ' ').title()}:** {v}")

        trigger_map = strategy.get("trigger_risk_map", [])
        if trigger_map:
            st.write("")
            st.markdown("**Trigger Risk Map:**")
            for t in trigger_map:
                if isinstance(t, dict) and t.get("risk"):
                    st.markdown(
                        f"<div class='trigger-row'>"
                        f"<strong>{t.get('risk', '')}</strong>"
                        f" → {t.get('response', '')}"
                        f"<br><em style='font-size:12px;color:var(--s-text3);'>"
                        f"If: {t.get('condition', '')}</em>"
                        f"</div>",
                        unsafe_allow_html=True
                    )

    st.divider()

    # ── SECTION 5 — DECISION INTELLIGENCE ──
    st.subheader("🧠 Decision Intelligence")

    dec_summary  = decision.get("summary",       "")
    dec_alloc    = decision.get("allocation",     {})
    dec_risk     = decision.get("risk",           {})
    dec_sectors  = decision.get("sector_bets",    [])
    dec_tactical = decision.get("tactical_moves", [])

    if dec_summary:
        st.markdown(f"<div class='decision-box'>{dec_summary}</div>", unsafe_allow_html=True)

    d1, d2, d3 = st.columns(3)
    risk_level = dec_risk.get("risk_level",       "—")
    exp_dd     = dec_risk.get("expected_drawdown", 0)
    worst_case = dec_risk.get("worst_case",        0)

    risk_bg = (
        "var(--s-red-dim)"   if risk_level == "HIGH"     else
        "var(--s-amber-dim)" if risk_level == "MODERATE" else
        "var(--s-green-dim)"
    )
    risk_color = (
        "var(--s-red)"   if risk_level == "HIGH"     else
        "var(--s-amber)" if risk_level == "MODERATE" else
        "var(--s-green)"
    )
    d1.markdown(
        f"<div style='background:{risk_bg};border:0.5px solid {risk_color};"
        f"border-radius:var(--s-radius-lg);padding:12px;text-align:center;'>"
        f"<div style='font-size:9px;font-family:var(--s-display);font-weight:700;"
        f"letter-spacing:1px;color:var(--s-text3);margin-bottom:6px;'>RISK LEVEL</div>"
        f"<div style='font-size:22px;font-weight:500;font-family:var(--s-mono);"
        f"color:{risk_color};'>{risk_level}</div>"
        f"</div>",
        unsafe_allow_html=True
    )
    d2.metric("Expected Drawdown", f"{exp_dd}%")
    d3.metric("Worst Case",        f"{worst_case}%")

    st.write("")
    dec_left, dec_right = st.columns([1, 1])

    with dec_left:
        if dec_alloc:
            st.markdown("**Decision Allocation:**")
            dec_df = pd.DataFrame(dec_alloc.items(), columns=["Asset", "Allocation %"])
            dec_df["Allocation %"] = dec_df["Allocation %"].astype(str) + "%"
            st.table(dec_df)

    with dec_right:
        if dec_sectors:
            st.markdown("**Sector Bets:**")
            for s in dec_sectors:
                st.markdown(f"• **{s}**")

        if dec_tactical:
            st.write("")
            st.markdown("**Tactical Moves:**")
            for t in dec_tactical:
                if isinstance(t, dict):
                    st.markdown(
                        f"<div class='trigger-row'>"
                        f"<strong>{t.get('action', '')}</strong>"
                        f" — {t.get('reason', '')}"
                        f"</div>",
                        unsafe_allow_html=True
                    )

    st.divider()

    # ── SECTION 6 — ACTIVE TRIGGERS ──
    if triggers:
        st.subheader("⚡ Active Triggers")
        t_cols = st.columns(min(len(triggers), 3))
        for i, t in enumerate(triggers):
            with t_cols[i % 3]:
                if isinstance(t, dict):
                    st.markdown(
                        f"<div class='trigger-row'>"
                        f"<strong>{t.get('name', t.get('trigger', ''))}</strong><br>"
                        f"{t.get('action', t.get('description', ''))}"
                        f"</div>",
                        unsafe_allow_html=True
                    )
        st.divider()

    # ── SECTION 7 — LIVE MARKET SNAPSHOT ──
    live_nifty = market.get("equity",      {}).get("nifty")
    live_inr   = market.get("fx",          {}).get("usd_inr")
    live_crude = market.get("commodities", {}).get("crude_oil")
    live_gold  = market.get("commodities", {}).get("gold")
    live_vix   = market.get("volatility",  {}).get("vix")
    live_us10y = market.get("rates",       {}).get("us10y")

    if any([live_nifty, live_inr, live_crude]):
        st.subheader("📈 Live Market Snapshot")
        changes = market.get("changes", {})

        def fmt_chg(val):
            return f"{round(val, 2)}%" if isinstance(val, (int, float)) else None

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Nifty 50",    f"{round(live_nifty):,}"               if live_nifty else "—", fmt_chg(changes.get("nifty")))
        m2.metric("USD/INR",     f"Rs.{round(safe_float(live_inr), 2)}" if live_inr   else "—", fmt_chg(changes.get("usd_inr")))
        m3.metric("Brent Crude", f"${round(safe_float(live_crude), 1)}" if live_crude else "—", fmt_chg(changes.get("crude")))
        m4.metric("Gold",        f"${round(safe_float(live_gold)):,}"   if live_gold  else "—", fmt_chg(changes.get("gold")))
        m5.metric("VIX",         f"{round(safe_float(live_vix), 1)}"    if live_vix   else "—")
        m6.metric("US 10Y",      f"{round(safe_float(live_us10y), 2)}%" if live_us10y else "—")
        st.divider()

    # ── SECTION 7B — NSE INTELLIGENCE ──
    fii_data     = nse_snapshot.get("fii_dii", {})
    nse_indices  = nse_snapshot.get("indices", {})
    nse_pcr_data = nse_snapshot.get("pcr_data", {})
    if not isinstance(nse_pcr_data, dict):
        nse_pcr_data = {}

    import datetime as _dt
    _now_hour    = _dt.datetime.now().hour
    _market_open = 9 <= _now_hour < 16

    has_nse_data = (
        fii_data.get("fii_signal") not in ["UNKNOWN", None, ""] or
        nse_indices.get("nifty50") or
        _market_open
    )

    if has_nse_data:
        st.subheader("🏦 FII / DII Flow Intelligence")

        fii_info  = fii_data.get("fii", {})
        dii_info  = fii_data.get("dii", {})
        fii_net   = fii_info.get("net", 0)
        dii_net   = dii_info.get("net", 0)
        fii_sig   = fii_data.get("fii_signal",      "NEUTRAL")
        dii_sig   = fii_data.get("dii_signal",      "NEUTRAL")
        flow_sig  = fii_data.get("combined_signal", "NEUTRAL")
        data_date = fii_data.get("date", "")

        if data_date:
            st.caption(f"Data as of: {data_date}")

        f1, f2, f3, f4, f5, f6 = st.columns(6)
        fii_arrow = "▲" if fii_net > 0 else "▼" if fii_net < 0 else "—"
        f1.metric("FII Net (Rs. Cr)", f"{fii_arrow} {abs(round(fii_net, 0)):,.0f}", fii_sig)
        dii_arrow = "▲" if dii_net > 0 else "▼" if dii_net < 0 else "—"
        f2.metric("DII Net (Rs. Cr)", f"{dii_arrow} {abs(round(dii_net, 0)):,.0f}", dii_sig)
        vix_nse = nse_snapshot.get("india_vix", "—")
        f3.metric("India VIX (NSE)",  f"{round(safe_float(vix_nse), 1)}" if vix_nse != "—" else "—")
        pcr_val = nse_snapshot.get("pcr", "—")
        pcr_sig = nse_pcr_data.get("pcr_signal", "NEUTRAL") if isinstance(nse_pcr_data, dict) else "NEUTRAL"
        f4.metric("Nifty PCR",        f"{round(safe_float(pcr_val), 2)}" if pcr_val != "—" else "—", pcr_sig)
        nifty_nse  = nse_indices.get("nifty50",    {})
        bank_nifty = nse_indices.get("bank_nifty", {})
        f5.metric("Nifty (NSE)",  f"{round(nifty_nse.get('last', 0)):,}"  if nifty_nse  else "—", f"{nifty_nse.get('change_pct', 0):+.2f}%"  if nifty_nse  else None)
        f6.metric("Bank Nifty",   f"{round(bank_nifty.get('last', 0)):,}" if bank_nifty else "—", f"{bank_nifty.get('change_pct', 0):+.2f}%" if bank_nifty else None)

        st.write("")

        flow_bg    = "var(--s-green-dim)" if flow_sig == "RISK_ON"  else "var(--s-red-dim)"  if flow_sig == "RISK_OFF" else "var(--s-panel)"
        flow_text  = "var(--s-green)"     if flow_sig == "RISK_ON"  else "var(--s-red)"       if flow_sig == "RISK_OFF" else "var(--s-text3)"
        flow_emoji = (
            "🟢" if fii_net >  1000 else
            "🔴" if fii_net < -1000 else
            "🟡"
        )

        if flow_sig == "RISK_ON":
            flow_narrative = (
                f"DII buying of Rs.{dii_net:,.0f} Cr is dominant. "
                f"FII activity of Rs.{fii_net:,.0f} Cr is secondary. "
                f"Domestic institutional support is sustaining the risk-on environment."
            )
        elif flow_sig == "RISK_OFF":
            flow_narrative = (
                f"Combined institutional flows signal risk-off. "
                f"FII net: Rs.{fii_net:,.0f} Cr. DII net: Rs.{dii_net:,.0f} Cr. "
                f"Monitor for regime transition if sustained beyond 3 sessions."
            )
        else:
            flow_narrative = (
                f"Mixed flows — FII Rs.{fii_net:,.0f} Cr, DII Rs.{dii_net:,.0f} Cr. "
                f"Watch FII threshold: sustained buy above Rs.1,000 Cr = bullish confirmation."
            )

        st.markdown(
            f"<div style='background:{flow_bg};border-left:3px solid {flow_text};"
            f"border-radius:0 var(--s-radius) var(--s-radius) 0;"
            f"padding:12px 16px;margin-bottom:8px;font-size:13px;"
            f"font-family:var(--s-sans);'>"
            f"{flow_emoji} <strong>Flow Signal: {flow_sig}</strong> — {flow_narrative}"
            f"</div>",
            unsafe_allow_html=True
        )

        if not fii_data or fii_data.get("fii_signal") in ["UNKNOWN", None, ""]:
            st.caption(
                "NSE flow data temporarily unavailable. "
                "This can occur if NSE's API is slow or returning a CAPTCHA. "
                "Try pressing **🔄 Clear Cache & Reload** in the sidebar and running again."
            )

        sector_indices = {
            k: nse_indices[k]
            for k in ["nifty_it", "nifty_fmcg", "nifty_pharma", "nifty_midcap"]
            if k in nse_indices
        }
        if sector_indices:
            st.write("")
            st.markdown("**Sector Index Performance:**")
            s_cols = st.columns(len(sector_indices))
            for idx, (key, data) in enumerate(sector_indices.items()):
                label = key.replace("nifty_", "Nifty ").title()
                chg   = data.get("change_pct", 0)
                s_cols[idx].metric(label, f"{round(data.get('last', 0)):,}", f"{chg:+.2f}%")

        st.divider()

    # ── SECTION 7C — SECTOR HEATMAP ──
    nse_indices_heatmap = nse_snapshot.get("indices", {})
    sector_map = {
        "nifty_it":     ("IT",      "Technology"),
        "nifty_fmcg":   ("FMCG",    "Defensives"),
        "nifty_pharma": ("Pharma",  "Healthcare"),
        "nifty_midcap": ("Mid Cap", "Breadth"),
        "bank_nifty":   ("Banks",   "Financials"),
    }
    heatmap_data = {
        k: nse_indices_heatmap[k]
        for k in sector_map
        if k in nse_indices_heatmap and nse_indices_heatmap[k].get("last", 0) > 0
    }

    if heatmap_data:
        st.subheader("🟩 Sector Heatmap")
        st.caption("Colour intensity reflects magnitude of move. Green = positive, Red = negative.")

        tiles_html = ""
        for key, (short_name, category) in sector_map.items():
            if key not in heatmap_data:
                continue
            data      = heatmap_data[key]
            chg_pct   = data.get("change_pct", 0)
            last      = data.get("last",       0)
            intensity = min(abs(chg_pct) / 3.0, 1.0)

            if chg_pct > 0:
                r = int(200 + intensity * 30); g = int(230 - intensity * 10); b = int(200 + intensity * 30)
                bg = f"rgb({r},{g},{b})"; tc = "#1e6823"
            elif chg_pct < 0:
                r = int(240 - intensity * 10); g = int(200 - intensity * 60); b = int(200 - intensity * 60)
                bg = f"rgb({r},{g},{b})"; tc = "#8b1a1a"
            else:
                bg = "var(--s-panel)"; tc = "var(--s-text3)"

            arrow = "▲" if chg_pct > 0 else "▼" if chg_pct < 0 else "—"
            tiles_html += (
                f"<div style='display:inline-block;background:{bg};"
                f"border-radius:var(--s-radius-lg);padding:14px 18px;margin:4px;"
                f"min-width:120px;text-align:center;vertical-align:top;'>"
                f"<div style='font-size:9px;font-family:var(--s-display);"
                f"color:{tc};opacity:0.7;margin-bottom:2px;'>{category}</div>"
                f"<div style='font-size:13px;font-weight:700;font-family:var(--s-display);"
                f"color:{tc};'>{short_name}</div>"
                f"<div style='font-size:18px;font-weight:500;font-family:var(--s-mono);"
                f"color:{tc};margin:4px 0;'>{arrow} {abs(chg_pct):.2f}%</div>"
                f"<div style='font-size:9px;font-family:var(--s-mono);"
                f"color:{tc};opacity:0.7;'>{round(last):,}</div>"
                f"</div>"
            )

        st.markdown(
            f"<div style='background:var(--s-surface);border:0.5px solid var(--s-border);"
            f"border-radius:var(--s-radius-lg);padding:12px;margin-bottom:8px;'>"
            f"{tiles_html}</div>",
            unsafe_allow_html=True
        )

        current_regime = regime.get("regime", "")
        favoured_map   = {
            "LIQUIDITY_DRIVEN_EXPANSION": "Banks, Infra, Consumer Discretionary",
            "STABLE_GROWTH":              "Banks, IT, Consumer Discretionary",
            "EARLY_CYCLE_RECOVERY":       "Banks, Real Estate, Autos",
            "MONETARY_TIGHTENING":        "FMCG, Pharma, IT",
            "LIQUIDITY_TIGHTENING":       "FMCG, Pharma — defensive rotation",
            "GROWTH_SLOWDOWN_SUPPORT":    "Pharma, FMCG, IT exports",
            "STAGFLATION_RISK":           "Gold, IT (export), Pharma",
        }
        favoured = favoured_map.get(current_regime, "")
        if favoured:
            st.caption(
                f"**Regime alignment:** "
                f"{current_regime.replace('_', ' ').title()} "
                f"favours → {favoured}"
            )

        st.divider()

    # ── SECTION 7D — RBI INTELLIGENCE ──
    rbi_display = regime.get("rbi_data", {})

    if rbi_display and rbi_display.get("repo_rate"):
        st.subheader("🏛️ RBI Intelligence")

        source_tag = (
            "Live — RBI DBIE"
            if rbi_display.get("source") == "RBI DBIE"
            else "Fallback values"
        )
        st.caption(
            f"Source: {source_tag}  |  "
            f"These signals were applied as scoring adjustments in this pipeline run."
        )

        rb1, rb2, rb3, rb4 = st.columns(4)
        rb1.metric("Repo Rate (RBI)", f"{rbi_display.get('repo_rate', '—')}%",     rbi_display.get("policy_direction", ""))
        rb2.metric("Credit Growth",   f"{rbi_display.get('credit_growth', '—')}%", rbi_display.get("credit_impulse", ""))
        rb3.metric("Forex Reserves",  f"${rbi_display.get('forex_reserves', '—')}B")
        rb4.metric("M3 Growth",       f"{rbi_display.get('m3_growth', '—')}%",     rbi_display.get("liquidity_signal", ""))

        liq_signal = rbi_display.get("liquidity_signal", "NEUTRAL")
        policy_dir = rbi_display.get("policy_direction", "NEUTRAL")
        credit_imp = rbi_display.get("credit_impulse",   "NEUTRAL")

        rbi_bg     = "var(--s-green-dim)" if liq_signal == "SURPLUS" else "var(--s-red-dim)" if liq_signal == "DEFICIT" else "var(--s-amber-dim)"
        rbi_border = "var(--s-green)"     if liq_signal == "SURPLUS" else "var(--s-red)"     if liq_signal == "DEFICIT" else "var(--s-amber)"

        st.markdown(
            f"<div style='background:{rbi_bg};border-left:3px solid {rbi_border};"
            f"border-radius:0 var(--s-radius) var(--s-radius) 0;"
            f"padding:10px 14px;font-size:13px;margin-top:8px;"
            f"font-family:var(--s-sans);color:var(--s-text2);'>"
            f"<strong>RBI Regime Contribution:</strong> "
            f"Policy direction is <strong>{policy_dir}</strong>, "
            f"credit impulse is <strong>{credit_imp}</strong>, "
            f"system liquidity is <strong>{liq_signal}</strong>. "
            f"These signals adjusted the regime engine scoring for this run."
            f"</div>",
            unsafe_allow_html=True
        )
        st.divider()

    # ── SECTION 8 — FULL INTELLIGENCE + REPORT ──
    with st.expander("🔍 Full Intelligence Object"):
        st.json(final_intel)

    with st.expander("📄 Generated Report"):
        if isinstance(report, str):
            st.markdown(report)
        elif isinstance(report, dict):
            st.json(report)

    # ── SECTION 9 — PDF DOWNLOAD ──
    st.subheader("📥 Download Report")
    try:
        from pdf_report_generator import PDFReportGenerator
        firm_name = (
            profile.get("firm_name", "SENTINEL Intelligence")
            or "SENTINEL Intelligence"
        )
        pdf_gen   = PDFReportGenerator(firm_name=firm_name)
        pdf_bytes = pdf_gen.generate(final_intel)
        st.download_button(
            label               = "⬇️ Download PDF Report",
            data                = pdf_bytes,
            file_name           = (
                f"SENTINEL_Report_"
                f"{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
            ),
            mime                = "application/pdf",
            use_container_width = True
        )
    except Exception as e:
        st.warning(f"PDF generation unavailable: {e}")

else:
    st.info(
        "⬅️ Configure inputs and press "
        "**▶ Run Intelligence Engine** to begin."
    )
    st.markdown(build_status_bar(pipeline_ran=False), unsafe_allow_html=True)


# =======================================================
# ✅ SECTION 7E — YIELD CURVE (ALWAYS VISIBLE)
# =======================================================
def _render_yield_curve(yc):
    analysis = yc.get("analysis", {})
    india_y  = yc.get("india_yields", {})
    us_y     = yc.get("us_yields",    {})

    india_src = yc.get("india_source", "fallback")
    us_src    = yc.get("us_source",    "fallback")
    st.caption(
        f"India G-Sec: {'Live' if india_src == 'live' else 'Fallback values'}  |  "
        f"US Treasury: {'Live' if us_src == 'live' else 'Fallback values'}  |  "
        f"Updated: {yc.get('timestamp', '')}"
    )

    india_spread = analysis.get("india_spread_10y_2y", 0)
    us_spread    = analysis.get("us_spread_10y_2y",    0)
    carry        = analysis.get("india_us_spread_10y", 0)
    india_shape  = analysis.get("india_curve_shape",   "NORMAL")
    carry_signal = analysis.get("carry_signal",        "NEUTRAL_CARRY")

    ky1, ky2, ky3, ky4 = st.columns(4)

    spread_color = (
        "var(--s-green)" if india_spread > 1.5 else
        "var(--s-amber)" if india_spread > 0.0 else
        "var(--s-red)"
    )
    ky1.markdown(
        f"**India 10Y-2Y Spread**<br>"
        f"<span style='font-size:20px;font-weight:500;font-family:var(--s-mono);"
        f"color:{spread_color};'>{india_spread:+.2f}%</span><br>"
        f"<span style='font-size:10px;font-family:var(--s-display);color:var(--s-text3);'>"
        f"{india_shape}</span>",
        unsafe_allow_html=True
    )

    us_color = (
        "var(--s-green)" if us_spread > 0.5 else
        "var(--s-red)"   if us_spread < 0   else
        "var(--s-amber)"
    )
    ky2.markdown(
        f"**US 10Y-2Y Spread**<br>"
        f"<span style='font-size:20px;font-weight:500;font-family:var(--s-mono);"
        f"color:{us_color};'>{us_spread:+.2f}%</span><br>"
        f"<span style='font-size:10px;font-family:var(--s-display);color:var(--s-text3);'>"
        f"{analysis.get('us_curve_shape', '—')}</span>",
        unsafe_allow_html=True
    )

    carry_color = (
        "var(--s-green)" if carry_signal == "STRONG_FII_MAGNET" else
        "var(--s-red)"   if carry_signal == "FII_OUTFLOW_RISK"  else
        "var(--s-text3)"
    )
    carry_label = (
        "FII Magnet" if carry_signal == "STRONG_FII_MAGNET" else
        "FII Risk"   if carry_signal == "FII_OUTFLOW_RISK"  else
        "Neutral"
    )
    ky3.markdown(
        f"**India-US 10Y Spread**<br>"
        f"<span style='font-size:20px;font-weight:500;font-family:var(--s-mono);"
        f"color:{carry_color};'>{carry:+.2f}%</span><br>"
        f"<span style='font-size:10px;font-family:var(--s-display);color:var(--s-text3);'>"
        f"{carry_label}</span>",
        unsafe_allow_html=True
    )

    india_10y = india_y.get("10Y", 6.85)
    ky4.markdown(
        f"**India 10Y Yield**<br>"
        f"<span style='font-size:20px;font-weight:500;font-family:var(--s-mono);"
        f"color:var(--s-text);'>{india_10y:.2f}%</span><br>"
        f"<span style='font-size:10px;font-family:var(--s-display);color:var(--s-text3);'>"
        f"G-Sec benchmark</span>",
        unsafe_allow_html=True
    )

    st.write("")

    try:
        import plotly.graph_objects as go

        tenors_india       = yc.get("tenors_india", [])
        tenors_us          = yc.get("tenors_us",    [])
        india_vals         = [india_y.get(t) for t in tenors_india if india_y.get(t)]
        us_vals            = [us_y.get(t)    for t in tenors_us    if us_y.get(t)]
        valid_india_tenors = [t for t in tenors_india if india_y.get(t)]
        valid_us_tenors    = [t for t in tenors_us    if us_y.get(t)]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=valid_india_tenors, y=india_vals, mode="lines+markers",
            name="India G-Sec",
            line=dict(color="#4361ee", width=3), marker=dict(size=8, color="#4361ee"),
            hovertemplate="India %{x}: %{y:.2f}%<extra></extra>"
        ))
        fig.add_trace(go.Scatter(
            x=valid_us_tenors, y=us_vals, mode="lines+markers",
            name="US Treasury",
            line=dict(color="#f4a261", width=2, dash="dot"), marker=dict(size=6, color="#f4a261"),
            hovertemplate="US %{x}: %{y:.2f}%<extra></extra>"
        ))
        if "2Y" in india_y and "10Y" in india_y:
            fig.add_vrect(
                x0="2Y", x1="10Y", fillcolor="#4361ee", opacity=0.05, line_width=0,
                annotation_text=f"Spread: {india_spread:+.2f}%",
                annotation_position="top left",
                annotation_font_size=10, annotation_font_color="#4361ee"
            )
        fig.update_layout(
            height=320, margin=dict(l=0, r=0, t=20, b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="DM Sans, sans-serif", size=12),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            xaxis=dict(title="Tenor", showgrid=True, gridcolor="rgba(67,97,238,0.10)", tickfont=dict(size=11)),
            yaxis=dict(title="Yield (%)", showgrid=True, gridcolor="rgba(67,97,238,0.10)", tickformat=".2f", ticksuffix="%"),
            hovermode="x unified"
        )
        st.plotly_chart(fig, use_container_width=True)

    except ImportError:
        st.caption("Install plotly for chart: `pip install plotly`")
        col_i, col_u = st.columns(2)
        with col_i:
            st.markdown("**India G-Sec Yields**")
            for t, v in india_y.items():
                st.markdown(f"`{t:>3}` &nbsp; {v:.2f}%", unsafe_allow_html=True)
        with col_u:
            st.markdown("**US Treasury Yields**")
            for t, v in us_y.items():
                st.markdown(f"`{t:>3}` &nbsp; {v:.2f}%", unsafe_allow_html=True)

    regime_detail = analysis.get("regime_signal_detail", "")
    carry_detail  = analysis.get("carry_detail",         "")
    regime_sig    = analysis.get("regime_signal",        "")

    sig_bg = (
        "var(--s-green-dim)" if "STEEP"    in india_shape or "EARLY" in regime_sig else
        "var(--s-red-dim)"   if "INVERTED" in india_shape else
        "var(--s-amber-dim)" if "FLAT"     in india_shape else
        "var(--s-blue-dim)"
    )
    sig_border = (
        "var(--s-green)" if "STEEP"    in india_shape or "EARLY" in regime_sig else
        "var(--s-red)"   if "INVERTED" in india_shape else
        "var(--s-amber)" if "FLAT"     in india_shape else
        "var(--s-blue)"
    )
    st.markdown(
        f"<div style='background:{sig_bg};border-left:3px solid {sig_border};"
        f"border-radius:0 var(--s-radius) var(--s-radius) 0;"
        f"padding:12px 16px;margin-bottom:8px;font-size:13px;"
        f"font-family:var(--s-sans);color:var(--s-text2);'>"
        f"<strong style='font-family:var(--s-display);font-size:10px;"
        f"letter-spacing:0.8px;'>Curve Signal: {india_shape}</strong> — {regime_detail}"
        f"</div>",
        unsafe_allow_html=True
    )

    carry_bg     = "var(--s-green-dim)" if carry_signal == "STRONG_FII_MAGNET" else "var(--s-red-dim)" if carry_signal == "FII_OUTFLOW_RISK" else "var(--s-panel)"
    carry_border = "var(--s-green)"     if carry_signal == "STRONG_FII_MAGNET" else "var(--s-red)"     if carry_signal == "FII_OUTFLOW_RISK" else "var(--s-border)"
    st.markdown(
        f"<div style='background:{carry_bg};border-left:3px solid {carry_border};"
        f"border-radius:0 var(--s-radius) var(--s-radius) 0;"
        f"padding:12px 16px;margin-top:6px;font-size:13px;"
        f"font-family:var(--s-sans);color:var(--s-text2);'>"
        f"<strong style='font-family:var(--s-display);font-size:10px;"
        f"letter-spacing:0.8px;'>Carry Signal: "
        f"{carry_signal.replace('_', ' ').title()}</strong> — {carry_detail}"
        f"</div>",
        unsafe_allow_html=True
    )


if yield_curve_data:
    st.divider()
    st.subheader("📈 Yield Curve")
    _render_yield_curve(yield_curve_data)
    st.divider()


# =======================================================
# SECTION 10 — HISTORICAL REGIME ARCHIVE
# =======================================================
with st.expander("🗂️ Historical Regime Archive", expanded=False):

    history = get_run_history(limit=30)

    if not history:
        st.info(
            "No run history yet. Press **▶ Run Intelligence Engine** "
            "to generate your first intelligence snapshot."
        )
    else:
        st.caption(f"Showing last {len(history)} runs — most recent first.")
        st.write("")

        history_asc = list(reversed(history))

        st.markdown("**📊 Regime Confidence Over Time**")
        conf_df = pd.DataFrame([
            {"Date": r.get("run_at", "")[:10], "Confidence": int(r.get("confidence", 0) * 100)}
            for r in history_asc
        ]).set_index("Date")
        st.bar_chart(conf_df, color="#4361ee", height=160)
        st.caption("Bar height = regime confidence % at time of each run")
        st.write("")

        st.markdown("**🎯 Conviction Trend**")
        conv_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        conv_df  = pd.DataFrame([
            {"Date": r.get("run_at", "")[:10], "Conviction": conv_map.get(r.get("conviction", "LOW"), 1)}
            for r in history_asc
        ]).set_index("Date")
        st.line_chart(conv_df, color="#7c3aed", height=140)
        st.caption("3 = HIGH   2 = MEDIUM   1 = LOW")
        st.write("")

        runs_with_alloc = [
            r for r in history_asc
            if r.get("allocation") and isinstance(r.get("allocation"), dict) and len(r["allocation"]) > 0
        ]
        if runs_with_alloc:
            st.markdown("**📈 Asset Allocation Over Time**")
            alloc_rows = []
            for r in runs_with_alloc:
                alloc = r.get("allocation", {})
                row   = {"Date": r.get("run_at", "")[:10]}
                for asset, val in alloc.items():
                    pct = round(val * 100, 1) if val <= 1.0 else round(val, 1)
                    row[asset.capitalize()] = pct
                alloc_rows.append(row)
            alloc_df = pd.DataFrame(alloc_rows).set_index("Date")
            st.area_chart(alloc_df, height=180)
            st.caption("Asset allocation % at time of each run")
            st.write("")

        st.markdown("**📋 Run Log**")
        table_rows = []
        for r in history:
            table_rows.append({
                "Date":       r.get("run_at", "")[:16].replace("T", " "),
                "Regime":     r.get("regime", "—").replace("_", " ").title(),
                "Confidence": f"{int(r.get('confidence', 0) * 100)}%",
                "Conviction": r.get("conviction", "—"),
                "Repo Rate":  f"{r.get('repo_rate', '—')}%",
                "Deficit":    f"{r.get('deficit', '—')}%",
                "Capex":      f"Rs.{r.get('capex', '—')}L Cr",
            })
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

        st.write("")
        st.markdown("**🔍 Inspect a Specific Run**")

        run_labels = [
            f"{r.get('run_at', '')[:16].replace('T', ' ')}  —  "
            f"{r.get('regime', '—').replace('_', ' ').title()}  —  "
            f"{r.get('conviction', '—')}"
            for r in history
        ]

        selected_idx = st.selectbox(
            "Select run",
            options=range(len(run_labels)),
            format_func=lambda i: run_labels[i],
            label_visibility="collapsed"
        )

        if selected_idx is not None:
            sel = history[selected_idx]

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Regime",     sel.get("regime", "—").replace("_", " ").title())
            col_b.metric("Confidence", f"{int(sel.get('confidence', 0) * 100)}%")
            col_c.metric("Conviction", sel.get("conviction", "—"))

            alloc = sel.get("allocation", {})
            if alloc and isinstance(alloc, dict) and len(alloc) > 0:
                st.write("")
                st.markdown("**Allocation at time of run:**")
                alloc_detail = pd.DataFrame(alloc.items(), columns=["Asset", "Allocation %"])
                if alloc_detail["Allocation %"].max() <= 1.0:
                    alloc_detail["Allocation %"] = (alloc_detail["Allocation %"] * 100).round(1)
                alloc_detail["Allocation %"] = alloc_detail["Allocation %"].astype(str) + "%"
                st.table(alloc_detail)

            stress = sel.get("stress_test", {})
            if stress and isinstance(stress, dict):
                st.markdown(
                    f"**Stress test inputs:** "
                    f"Repo Rate **{stress.get('repo_rate', '—')}%**"
                    f" &nbsp;|&nbsp; "
                    f"Fiscal Deficit **{stress.get('deficit', '—')}%**"
                    f" &nbsp;|&nbsp; "
                    f"Capex **Rs.{stress.get('capex', '—')}L Cr**"
                )

            summary_text = sel.get("summary", "")
            if summary_text:
                st.write("")
                st.markdown(
                    f"<div class='decision-box'>{summary_text}</div>",
                    unsafe_allow_html=True
                )

            report_text = sel.get("report_text", "")
            if report_text:
                with st.expander("📄 Full report for this run"):
                    st.text(report_text)


# =======================================================
# SECTION 11 — ECONOMIC CALENDAR (ALWAYS VISIBLE)
# =======================================================
st.divider()
st.subheader("📅 Economic Calendar")

cal_events = get_events_by_window(days_ahead=120)
today      = datetime.date.today()

if not cal_events:
    st.info("No events in the calendar window.")
else:
    st.caption(
        "India macro events — past 30 days and next 120 days. "
        "Impact notes are research-grade context for PMS and wealth management. "
        "Not investment advice."
    )
    st.write("")

    all_cats     = ["All", "RBI MPC", "CPI", "GDP", "Union Budget", "SEBI"]
    selected_cat = st.radio(
        "Filter", all_cats, horizontal=True, label_visibility="collapsed"
    )

    filtered_events = (
        cal_events if selected_cat == "All"
        else [e for e in cal_events if e["category"] == selected_cat]
    )

    st.write("")

    for ev in filtered_events:
        event_date = ev["event_date"]
        days_until = ev["days_until"]
        released   = ev["released"]
        cat        = ev["category"]
        importance = ev["importance"]
        colors     = CATEGORY_COLORS.get(
            cat, {"bg": "#f1f1f1", "border": "#999", "text": "#333"}
        )
        imp_colors = IMPORTANCE_COLORS.get(
            importance, {"bg": "#f1f1f1", "text": "#555"}
        )

        if released:
            state_label = "RELEASED"
            state_color = "var(--s-text3)"
            state_bg    = "var(--s-panel)"
            bw          = "3px"
        elif days_until == 0:
            state_label = "TODAY"
            state_color = "var(--s-red)"
            state_bg    = "var(--s-red-dim)"
            bw          = "4px"
        elif days_until <= 7:
            state_label = days_until_label(days_until)
            state_color = "var(--s-red)"
            state_bg    = "var(--s-panel)"
            bw          = "4px"
        elif days_until <= 21:
            state_label = days_until_label(days_until)
            state_color = "var(--s-amber)"
            state_bg    = "var(--s-panel)"
            bw          = "3px"
        else:
            state_label = days_until_label(days_until)
            state_color = "var(--s-blue)"
            state_bg    = "var(--s-panel)"
            bw          = "3px"

        actual_html = ""
        if released and ev.get("actual"):
            actual_html = (
                f"<div style='margin-top:6px;font-size:11px;'>"
                f"<span style='color:var(--s-text3);'>Actual: </span>"
                f"<span style='font-family:var(--s-mono);font-weight:700;"
                f"color:var(--s-green);'>{ev['actual']}</span>"
                f"</div>"
            )

        consensus_html = ""
        if ev.get("consensus") and ev["consensus"] != "TBD":
            consensus_html = (
                f"<span style='font-size:11px;color:var(--s-text2);'>"
                f"Consensus: <strong style='font-family:var(--s-mono);'>"
                f"{ev['consensus']}</strong></span>"
            )

        previous_html = ""
        if ev.get("previous"):
            previous_html = (
                f"<span style='font-size:11px;color:var(--s-text3);"
                f"margin-left:14px;'>"
                f"Previous: {ev['previous']}</span>"
            )

        impact_html = ""
        if ev.get("impact_note"):
            impact_html = (
                f"<div style='background:var(--s-panel2);"
                f"border-radius:var(--s-radius);padding:6px 10px;margin-top:8px;"
                f"font-size:12px;font-family:var(--s-sans);"
                f"color:var(--s-text2);line-height:1.6;'>"
                f"💡 {ev['impact_note']}"
                f"</div>"
            )

        st.markdown(
            f"<div style='background:{state_bg};"
            f"border-left:{bw} solid {colors['border']};"
            f"border-radius:0 var(--s-radius-lg) var(--s-radius-lg) 0;"
            f"padding:12px 16px;margin-bottom:8px;'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;margin-bottom:4px;'>"
            f"<div>"
            f"<span style='background:{colors['bg']};color:{colors['text']};"
            f"font-size:9px;font-weight:700;font-family:var(--s-display);"
            f"letter-spacing:0.8px;padding:1px 7px;border-radius:3px;"
            f"margin-right:5px;'>{cat}</span>"
            f"<span style='background:{imp_colors['bg']};color:{imp_colors['text']};"
            f"font-size:9px;font-weight:600;font-family:var(--s-display);"
            f"padding:1px 7px;border-radius:3px;'>{importance}</span>"
            f"</div>"
            f"<span style='font-size:11px;font-weight:700;"
            f"font-family:var(--s-display);color:{state_color};'>"
            f"{state_label}</span>"
            f"</div>"
            f"<div style='font-size:14px;font-weight:500;font-family:var(--s-sans);"
            f"color:var(--s-text);margin-bottom:2px;'>{ev['name']}</div>"
            f"<div style='font-size:11px;font-family:var(--s-mono);"
            f"color:var(--s-text3);margin-bottom:6px;'>"
            f"{event_date.strftime('%A, %d %B %Y')}</div>"
            f"<div style='margin-bottom:4px;'>{consensus_html}{previous_html}</div>"
            f"{actual_html}"
            f"{impact_html}"
            f"</div>",
            unsafe_allow_html=True
        )

    st.write("")
    st.caption(
        "Sources: RBI (rbi.org.in), MOSPI (mospi.gov.in), SEBI (sebi.gov.in). "
        "Dates subject to official confirmation. "
        "Update economic_calendar.py when new dates are announced."
    )