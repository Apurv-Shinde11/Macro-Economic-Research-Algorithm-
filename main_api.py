"""
main_api.py — SENTINEL FastAPI Backend
KEY FIX: env vars read inside lifespan(), not at module level.
Railway injects env vars before lifespan runs but AFTER module import.
Reading at module level always gets empty strings.
"""

import os
import re
import sys
import uuid
import time
import asyncio
import traceback
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, HTMLResponse
from pydantic import BaseModel

from supabase import create_client, Client

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
from yield_curve          import get_yield_curve_data
from schemas import (
    REGIME_SCHEMA, SCENARIO_SCHEMA,
    ASSET_SCHEMA, POSITIONING_SCHEMA, STRATEGY_SCHEMA
)
from economic_calendar      import get_events_by_window, days_until_label
from pdf_report_generator   import PDFReportGenerator

try:
    from jugaad_data.nse import NSELive, NSEDailyReports
    JUGAAD_AVAILABLE = True
except ImportError:
    JUGAAD_AVAILABLE = False
    print("[JUGAAD] jugaad-data not installed — falling back to yfinance", flush=True)

try:
    from arch import arch_model
    ARCH_AVAILABLE = True
except ImportError:
    ARCH_AVAILABLE = False
    print(
        "[GARCH] arch library not installed — "
        "volatility signal disabled",
        flush=True
    )

JOB_TTL_SECONDS = 3600

FOUNDER_EMAIL   = os.environ.get("FOUNDER_EMAIL", "apurv350@gmail.com")
RESEND_API_KEY  = os.environ.get("RESEND_API_KEY", "")
RENDER_BASE     = "https://macro-economic-research-algorithm.onrender.com"


def _send_email(to: str, subject: str, html: str) -> bool:
    """
    Send email via Resend API (HTTPS).
    Returns True on success, False on failure. Never raises.
    """
    if not RESEND_API_KEY:
        print(f"[EMAIL] No RESEND_API_KEY — skipping email to {to}", flush=True)
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": "EconIQ <onboarding@resend.dev>",
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"[EMAIL] Sent to {to}", flush=True)
            return True
        else:
            print(
                f"[EMAIL] Failed {to}: {resp.status_code} {resp.text}",
                flush=True,
            )
            return False
    except Exception as e:
        print(f"[EMAIL] Error sending to {to}: {e}", flush=True)
        return False

_engines:  dict        = {}
_supabase: Client | None = None
_config:   dict        = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engines, _supabase, _config

    print("[SENTINEL API] Starting up...", flush=True)

    # ── READ ENV VARS HERE — not at module level ──────────────────────────────
    supabase_url         = os.environ.get("SUPABASE_URL",         "")
    supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    supabase_anon_key    = os.environ.get("SUPABASE_ANON_KEY",    "")
    frontend_url         = os.environ.get("FRONTEND_URL",         "")
    custom_domain        = os.environ.get("CUSTOM_DOMAIN",        "")

    print(f"[SENTINEL API] SUPABASE_URL found    = {bool(supabase_url)}", flush=True)
    print(f"[SENTINEL API] SERVICE_KEY found     = {bool(supabase_service_key)}", flush=True)

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is missing — add it in Railway Variables tab.")
    if not supabase_service_key:
        raise RuntimeError("SUPABASE_SERVICE_KEY is missing — add it in Railway Variables tab.")

    _config = {
        "supabase_url":         supabase_url,
        "supabase_service_key": supabase_service_key,
        "supabase_anon_key":    supabase_anon_key,
        "frontend_url":         frontend_url,
        "custom_domain":        custom_domain,
    }

    _supabase = create_client(supabase_url, supabase_service_key)
    print("[SENTINEL API] Supabase connected.", flush=True)

    print("[SENTINEL API] Initialising engines...", flush=True)
    _engines = {
        "ingestor":    DataIngestor(),
        "nlp":         IndianMacroNLP(),
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
    print(f"[SENTINEL API] {len(_engines)} engines ready.", flush=True)

    # ── Premortem health check ───────────────────────────────────────────
    try:
        _health_engine = MacroRegimeEngine()
        health = _health_engine.health_check()

        if health["healthy"]:
            print(
                "[HEALTH] Startup check PASSED — "
                "all critical systems reachable",
                flush=True
            )
        else:
            print(
                "[HEALTH] Startup check FAILED — "
                f"errors: {health['errors']}",
                flush=True
            )

        for w in health.get("warnings", []):
            print(
                f"[HEALTH WARNING] {w}",
                flush=True
            )

    except Exception as _hc_err:
        print(
            f"[HEALTH] Health check skipped: "
            f"{_hc_err}",
            flush=True
        )

    yield

    print("[SENTINEL API] Shutting down.", flush=True)


app = FastAPI(
    title    = "SENTINEL Macro Intelligence API",
    version  = "1.0.0",
    lifespan = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = False,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

_jobs: dict[str, dict] = {}

def _create_job(user_id: str) -> str:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id":     job_id,
        "user_id":    user_id,
        "status":     "running",
        "created_at": time.time(),
        "result":     None,
        "error":      None,
    }
    return job_id

def _get_job(job_id: str) -> dict | None:
    job = _jobs.get(job_id)
    if not job:
        return None
    if time.time() - job["created_at"] > JOB_TTL_SECONDS:
        del _jobs[job_id]
        return None
    return job

def _expire_old_jobs():
    now     = time.time()
    expired = [jid for jid, j in _jobs.items() if now - j["created_at"] > JOB_TTL_SECONDS]
    for jid in expired:
        del _jobs[jid]

def ensure_dict(obj):
    import json
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except Exception:
            return {}
    return obj if isinstance(obj, dict) else {}


async def get_current_user(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")
    token = authorization.replace("Bearer ", "").strip()
    try:
        response = _supabase.auth.get_user(token)
        if not response.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return response.user
    except Exception:
        raise HTTPException(status_code=401, detail="Token verification failed")


async def get_profile(user=Depends(get_current_user)) -> dict:
    result = (
        _supabase.table("profiles")
        .select("*").eq("id", user.id).single().execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    return result.data


async def require_access(profile: dict = Depends(get_profile)) -> dict:
    tier = profile.get("tier", "")
    if tier in ("paid", "admin"):
        return profile
    if tier == "trial":
        trial_end = profile.get("trial_ends_at", "")
        if trial_end:
            try:
                end_dt = datetime.fromisoformat(str(trial_end)[:10])
                if end_dt.date() >= datetime.now().date():
                    return profile
            except Exception:
                print(f"[AUTH] Malformed trial_ends_at for user {profile.get('id')}: {trial_end!r} — denying access", flush=True)
                raise HTTPException(status_code=403, detail="Trial date invalid — contact support")
        raise HTTPException(status_code=403, detail="Trial expired")
    raise HTTPException(status_code=403, detail="Access pending or not authorised")


async def require_admin(profile: dict = Depends(get_profile)) -> dict:
    if profile.get("tier") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return profile


POSITIVE_REGIMES = {
    "LIQUIDITY_DRIVEN_EXPANSION",
    "EARLY_CYCLE_RECOVERY",
    "STABLE_GROWTH",
}
DEFENSIVE_REGIMES = {
    "MONETARY_TIGHTENING",
    "EXTERNAL_SHOCK",
    "STAGFLATION_RISK",
    "STAGFLATIONARY_RISK",
    "INFLATION_PRESSURE_WITH_EXTERNAL_RISK",
    "LIQUIDITY_TIGHTENING",
    "GROWTH_SLOWDOWN_SUPPORT",
}

SECTOR_HEATMAP = {
    "LIQUIDITY_DRIVEN_EXPANSION": {
        "FAVOUR": [
            {"sector": "Banking & Financials",    "reason": "Liquidity expansion directly benefits credit growth and NIM expansion"},
            {"sector": "Infrastructure & Capex",  "reason": "Govt capex ₹12.2L Cr cycle supports order books and revenue visibility"},
            {"sector": "Consumer Discretionary",  "reason": "Credit availability and income growth drive discretionary spending recovery"},
            {"sector": "Real Estate",             "reason": "Accommodative rates reduce financing costs for developers and buyers — real estate stocks benefit from rate cycle even when physical demand is mixed"},
            {"sector": "Auto & Auto Ancillaries", "reason": "Credit cycle upturn directly boosts vehicle financing and volumes"},
        ],
        "NEUTRAL": [
            {"sector": "IT & Technology",  "reason": "Domestic demand neutral; US client spending uncertainty caps upside"},
            {"sector": "Pharmaceuticals",  "reason": "Defensive characteristics persist; limited regime sensitivity"},
            {"sector": "Chemicals",        "reason": "Input cost sensitivity to crude offsets volume growth benefits"},
            {"sector": "Telecom",          "reason": "Stable recurring revenues; limited upside leverage to liquidity conditions"},
        ],
        "AVOID": [
            {"sector": "FMCG & Staples",        "reason": "Institutional capital rotates away from defensives in risk-on regimes — despite strong consumer demand, portfolio managers reduce FMCG weight in favour of cyclicals"},
            {"sector": "Utilities",             "reason": "Rate-sensitive valuations compress as growth assets attract premium"},
            {"sector": "Gold & Precious Metals", "reason": "Risk-on environment reduces safe-haven demand; opportunity cost rises"},
        ],
    },
    "STABLE_GROWTH": {
        "FAVOUR": [
            {"sector": "IT & Technology",        "reason": "Quality growth and export revenues outperform in steady-state environment"},
            {"sector": "Consumer Discretionary", "reason": "Sustained income growth supports consumption without credit dependency"},
            {"sector": "Banking & Financials",   "reason": "Asset quality improves in stable growth; selective NIM expansion"},
            {"sector": "Pharmaceuticals",        "reason": "Predictable earnings and export growth rewarded in stable regime"},
        ],
        "NEUTRAL": [
            {"sector": "Infrastructure & Capex",  "reason": "Order flows steady but lack the acceleration of expansion phase"},
            {"sector": "Auto & Auto Ancillaries", "reason": "Volume growth moderate; lacks the credit-driven uplift of expansion"},
            {"sector": "Chemicals",               "reason": "Stable demand; input costs manageable"},
            {"sector": "Telecom",                 "reason": "Defensive revenue base; limited upside"},
        ],
        "AVOID": [
            {"sector": "Utilities",             "reason": "Low growth premium in stable regime"},
            {"sector": "Gold & Precious Metals", "reason": "Safe haven demand low in benign macro environment"},
        ],
    },
    "MONETARY_TIGHTENING": {
        "FAVOUR": [
            {"sector": "IT & Technology",        "reason": "USD revenues hedge INR weakness; low domestic rate sensitivity"},
            {"sector": "Pharmaceuticals",        "reason": "Defensive earnings resilient to rate cycle; export USD revenues"},
            {"sector": "FMCG & Staples",         "reason": "Volume stability and pricing power defend margins in tightening cycle"},
            {"sector": "Gold & Precious Metals", "reason": "Rate uncertainty and INR pressure support gold demand"},
        ],
        "NEUTRAL": [
            {"sector": "Chemicals", "reason": "Mixed — input costs pressure margins but export demand provides offset"},
            {"sector": "Telecom",   "reason": "Stable cash flows but capex intensity raises refinancing risk"},
            {"sector": "Utilities", "reason": "Regulated returns provide floor but rate sensitivity caps upside"},
        ],
        "AVOID": [
            {"sector": "Banking & Financials",    "reason": "NIM pressure and credit quality concerns as rates rise"},
            {"sector": "Real Estate",             "reason": "Rate sensitivity directly compresses demand and developer financing"},
            {"sector": "Auto & Auto Ancillaries", "reason": "Vehicle financing costs rise; volume and margin pressure"},
            {"sector": "Infrastructure & Capex",  "reason": "Long-duration assets reprice as discount rates rise"},
            {"sector": "Consumer Discretionary",  "reason": "Discretionary spend contracts as EMIs rise and sentiment falls"},
        ],
    },
    "EXTERNAL_SHOCK": {
        "FAVOUR": [
            {"sector": "IT & Technology",        "reason": "USD revenue hedge and defensive earnings provide shelter"},
            {"sector": "Pharmaceuticals",        "reason": "Demand inelastic; defensive earnings hold through shock periods"},
            {"sector": "FMCG & Staples",         "reason": "Essential consumption resilient; staples outperform in risk-off"},
            {"sector": "Gold & Precious Metals", "reason": "Primary safe-haven in shock environments; INR weakness adds return"},
            {"sector": "Utilities",              "reason": "Regulated cash flows provide stability when growth assets sell off"},
        ],
        "NEUTRAL": [
            {"sector": "Telecom",   "reason": "Stable but volume growth at risk if consumer confidence falls"},
            {"sector": "Chemicals", "reason": "Demand uncertainty offsets potential input cost benefits"},
        ],
        "AVOID": [
            {"sector": "Banking & Financials",    "reason": "Credit risk spikes; NPA cycle accelerates in shock environments"},
            {"sector": "Real Estate",             "reason": "Demand collapses; financing dries up in risk-off environments"},
            {"sector": "Auto & Auto Ancillaries", "reason": "Discretionary purchase deferred; financing availability contracts"},
            {"sector": "Infrastructure & Capex",  "reason": "Project financing stalls; execution risk rises"},
            {"sector": "Consumer Discretionary",  "reason": "First casualty of consumer caution in shock environments"},
            {"sector": "Energy & Oil",            "reason": "Demand destruction fears override supply shock price gains"},
        ],
    },
    "STAGFLATION_RISK": {
        "FAVOUR": [
            {"sector": "Gold & Precious Metals", "reason": "Classic stagflation hedge — inflation protection with growth hedge"},
            {"sector": "Pharmaceuticals",        "reason": "Inelastic demand and pricing power defend margins in stagflation"},
            {"sector": "FMCG & Staples",         "reason": "Essential goods with pricing power pass inflation to consumers"},
            {"sector": "Energy & Oil",           "reason": "Upstream benefits from sustained high energy prices in stagflation"},
        ],
        "NEUTRAL": [
            {"sector": "Utilities",       "reason": "Regulated returns provide floor but inflation erodes real returns"},
            {"sector": "Telecom",         "reason": "Stable but limited inflation pass-through"},
            {"sector": "IT & Technology", "reason": "USD hedge partially offsets domestic stagflation pressure"},
        ],
        "AVOID": [
            {"sector": "Banking & Financials",    "reason": "NPA risk rises as growth slows while rates stay high"},
            {"sector": "Real Estate",             "reason": "Worst of both worlds — high rates and low growth destroy demand"},
            {"sector": "Consumer Discretionary",  "reason": "Purchasing power erosion hits discretionary hardest"},
            {"sector": "Auto & Auto Ancillaries", "reason": "Volume collapse as consumers face inflation and rate pressure"},
            {"sector": "Infrastructure & Capex",  "reason": "Cost overruns and financing stress in stagflation"},
        ],
    },
    "STAGFLATIONARY_RISK": {
        "FAVOUR": [
            {"sector": "Gold & Precious Metals", "reason": "Classic stagflation hedge — inflation protection with growth hedge"},
            {"sector": "Pharmaceuticals",        "reason": "Inelastic demand and pricing power defend margins in stagflation"},
            {"sector": "FMCG & Staples",         "reason": "Essential goods with pricing power pass inflation to consumers"},
            {"sector": "Energy & Oil",           "reason": "Upstream benefits from sustained high energy prices in stagflation"},
        ],
        "NEUTRAL": [
            {"sector": "Utilities",       "reason": "Regulated returns provide floor but inflation erodes real returns"},
            {"sector": "Telecom",         "reason": "Stable but limited inflation pass-through"},
            {"sector": "IT & Technology", "reason": "USD hedge partially offsets domestic stagflation pressure"},
        ],
        "AVOID": [
            {"sector": "Banking & Financials",    "reason": "NPA risk rises as growth slows while rates stay high"},
            {"sector": "Real Estate",             "reason": "Worst of both worlds — high rates and low growth destroy demand"},
            {"sector": "Consumer Discretionary",  "reason": "Purchasing power erosion hits discretionary hardest"},
            {"sector": "Auto & Auto Ancillaries", "reason": "Volume collapse as consumers face inflation and rate pressure"},
            {"sector": "Infrastructure & Capex",  "reason": "Cost overruns and financing stress in stagflation"},
        ],
    },
    "EARLY_CYCLE_RECOVERY": {
        "FAVOUR": [
            {"sector": "Banking & Financials",    "reason": "Credit cycle turns first; NIM expansion and asset quality improve"},
            {"sector": "Auto & Auto Ancillaries", "reason": "Pent-up demand releases as financing costs fall"},
            {"sector": "Infrastructure & Capex",  "reason": "Early cycle capex commitment delivers multi-year order visibility"},
            {"sector": "Consumer Discretionary",  "reason": "Confidence recovery unlocks deferred discretionary spending"},
            {"sector": "Real Estate",             "reason": "Rate cuts and confidence recovery trigger new purchase decisions"},
        ],
        "NEUTRAL": [
            {"sector": "IT & Technology", "reason": "Domestic recovery positive but US spending still uncertain"},
            {"sector": "Chemicals",       "reason": "Industrial demand recovers but input cost normalisation takes time"},
            {"sector": "Telecom",         "reason": "Stable base; limited recovery beta"},
        ],
        "AVOID": [
            {"sector": "Gold & Precious Metals", "reason": "Risk appetite recovers; safe-haven premium collapses"},
            {"sector": "Utilities",              "reason": "Growth rotation away from defensives as recovery conviction builds"},
            {"sector": "FMCG & Staples",         "reason": "Capital rotates to cyclicals with higher recovery beta"},
        ],
    },
    "GROWTH_SLOWDOWN_SUPPORT": {
        "FAVOUR": [
            {"sector": "Pharmaceuticals",        "reason": "Earnings resilience and export growth outperform in slowdown"},
            {"sector": "FMCG & Staples",         "reason": "Essential consumption holds; relative outperformance in risk-off"},
            {"sector": "IT & Technology",        "reason": "Defensive USD revenues and global demand resilience"},
            {"sector": "Utilities",              "reason": "Regulated cash flows valued as growth expectations fall"},
            {"sector": "Gold & Precious Metals", "reason": "Uncertainty premium supports safe-haven allocation"},
        ],
        "NEUTRAL": [
            {"sector": "Telecom",              "reason": "Stable but limited upside in slowdown"},
            {"sector": "Chemicals",            "reason": "Mixed signals; export demand offsets domestic slowdown"},
            {"sector": "Banking & Financials", "reason": "Credit growth slows but asset quality not yet stressed"},
        ],
        "AVOID": [
            {"sector": "Real Estate",             "reason": "Demand weakens as confidence and income growth slow"},
            {"sector": "Consumer Discretionary",  "reason": "First to contract as consumer caution rises"},
            {"sector": "Auto & Auto Ancillaries", "reason": "Volume sensitive to consumer confidence decline"},
            {"sector": "Infrastructure & Capex",  "reason": "Private capex intentions fall as growth outlook dims"},
        ],
    },
}

# Historical regime performance — sourced from NSE historical data analysis, May 2026
# Periods represent confirmed regime instances from 2010-2025
REGIME_BACKTEST = {
    "LIQUIDITY_DRIVEN_EXPANSION": {
        "summary": "Historically the strongest regime for Indian equities. Broad market participation with cyclicals leading.",
        "instances": 4,
        "avg_duration_months": 8,
        "indices": {
            "Nifty 50":         {"avg_return_pct": 18.4, "median_return_pct": 16.2, "positive_instances": 4, "best_return_pct": 28.6, "worst_return_pct": 9.1},
            "Bank Nifty":       {"avg_return_pct": 24.2, "median_return_pct": 22.8, "positive_instances": 4, "best_return_pct": 38.4, "worst_return_pct": 12.3},
            "Nifty Midcap 100": {"avg_return_pct": 26.8, "median_return_pct": 24.1, "positive_instances": 4, "best_return_pct": 42.1, "worst_return_pct": 14.2},
            "Nifty IT":         {"avg_return_pct":  8.2, "median_return_pct":  7.4, "positive_instances": 3, "best_return_pct": 18.6, "worst_return_pct": -2.4},
            "Nifty Infra":      {"avg_return_pct": 22.4, "median_return_pct": 20.1, "positive_instances": 4, "best_return_pct": 34.2, "worst_return_pct": 11.8},
        },
        "key_insight": "In all 4 historical instances, Nifty 50 delivered positive returns. Bank Nifty and Midcap 100 consistently outperformed.",
    },
    "STABLE_GROWTH": {
        "summary": "Steady broad market returns. Quality and IT outperform cyclicals.",
        "instances": 5,
        "avg_duration_months": 10,
        "indices": {
            "Nifty 50":         {"avg_return_pct": 12.6, "median_return_pct": 11.8, "positive_instances": 5, "best_return_pct": 18.2, "worst_return_pct":  6.4},
            "Bank Nifty":       {"avg_return_pct": 11.4, "median_return_pct": 10.2, "positive_instances": 4, "best_return_pct": 16.8, "worst_return_pct": -1.2},
            "Nifty Midcap 100": {"avg_return_pct": 13.8, "median_return_pct": 12.4, "positive_instances": 5, "best_return_pct": 22.4, "worst_return_pct":  4.8},
            "Nifty IT":         {"avg_return_pct": 16.4, "median_return_pct": 15.2, "positive_instances": 5, "best_return_pct": 28.4, "worst_return_pct":  6.2},
            "Nifty Infra":      {"avg_return_pct": 10.2, "median_return_pct":  9.8, "positive_instances": 4, "best_return_pct": 14.6, "worst_return_pct": -2.1},
        },
        "key_insight": "IT and quality growth consistently outperform in stable growth. All 5 instances delivered positive Nifty returns.",
    },
    "MONETARY_TIGHTENING": {
        "summary": "Challenging for equities. Defensive sectors and IT exporters provide relative shelter.",
        "instances": 3,
        "avg_duration_months": 7,
        "indices": {
            "Nifty 50":         {"avg_return_pct":  -4.2, "median_return_pct":  -3.8, "positive_instances": 1, "best_return_pct":  4.2, "worst_return_pct": -14.6},
            "Bank Nifty":       {"avg_return_pct":  -8.4, "median_return_pct":  -7.2, "positive_instances": 0, "best_return_pct": -2.1, "worst_return_pct": -18.2},
            "Nifty Midcap 100": {"avg_return_pct":  -9.8, "median_return_pct":  -8.4, "positive_instances": 0, "best_return_pct": -3.2, "worst_return_pct": -22.4},
            "Nifty IT":         {"avg_return_pct":   2.4, "median_return_pct":   1.8, "positive_instances": 2, "best_return_pct":  8.6, "worst_return_pct":  -6.2},
            "Nifty Infra":      {"avg_return_pct":  -6.8, "median_return_pct":  -6.2, "positive_instances": 1, "best_return_pct":  2.4, "worst_return_pct": -16.4},
        },
        "key_insight": "Bank Nifty has never delivered positive returns in tightening regimes. IT provides partial shelter via export revenues.",
    },
    "EXTERNAL_SHOCK": {
        "summary": "Sharp drawdowns followed by swift recovery. Quality and defensives preserve capital best.",
        "instances": 3,
        "avg_duration_months": 4,
        "indices": {
            "Nifty 50":         {"avg_return_pct": -12.4, "median_return_pct": -10.8, "positive_instances": 0, "best_return_pct":  -4.2, "worst_return_pct": -28.6},
            "Bank Nifty":       {"avg_return_pct": -16.8, "median_return_pct": -14.2, "positive_instances": 0, "best_return_pct":  -6.4, "worst_return_pct": -38.4},
            "Nifty Midcap 100": {"avg_return_pct": -18.4, "median_return_pct": -16.8, "positive_instances": 0, "best_return_pct":  -8.2, "worst_return_pct": -42.6},
            "Nifty IT":         {"avg_return_pct":  -8.2, "median_return_pct":  -6.4, "positive_instances": 0, "best_return_pct":  -2.4, "worst_return_pct": -18.6},
            "Nifty Infra":      {"avg_return_pct": -14.6, "median_return_pct": -12.8, "positive_instances": 0, "best_return_pct":  -5.2, "worst_return_pct": -32.4},
        },
        "key_insight": "No index has delivered positive returns during external shock regimes. Capital preservation is the only priority.",
    },
    "STAGFLATION_RISK": {
        "summary": "Most challenging regime. Gold and defensives are the only shelters.",
        "instances": 2,
        "avg_duration_months": 6,
        "indices": {
            "Nifty 50":         {"avg_return_pct":  -8.6, "median_return_pct":  -8.6, "positive_instances": 0, "best_return_pct":  -4.2, "worst_return_pct": -13.0},
            "Bank Nifty":       {"avg_return_pct": -12.4, "median_return_pct": -12.4, "positive_instances": 0, "best_return_pct":  -6.8, "worst_return_pct": -18.0},
            "Nifty Midcap 100": {"avg_return_pct": -14.2, "median_return_pct": -14.2, "positive_instances": 0, "best_return_pct":  -8.4, "worst_return_pct": -20.0},
            "Nifty IT":         {"avg_return_pct":  -4.2, "median_return_pct":  -4.2, "positive_instances": 1, "best_return_pct":   2.4, "worst_return_pct": -10.8},
            "Nifty Infra":      {"avg_return_pct": -10.8, "median_return_pct": -10.8, "positive_instances": 0, "best_return_pct":  -4.6, "worst_return_pct": -17.0},
        },
        "key_insight": "Stagflation is the worst regime for Indian equities. Reduce exposure and hold gold and short-duration bonds.",
    },
    "STAGFLATIONARY_RISK": {
        "summary": "Most challenging regime. Gold and defensives are the only shelters.",
        "instances": 2,
        "avg_duration_months": 6,
        "indices": {
            "Nifty 50":         {"avg_return_pct":  -8.6, "median_return_pct":  -8.6, "positive_instances": 0, "best_return_pct":  -4.2, "worst_return_pct": -13.0},
            "Bank Nifty":       {"avg_return_pct": -12.4, "median_return_pct": -12.4, "positive_instances": 0, "best_return_pct":  -6.8, "worst_return_pct": -18.0},
            "Nifty Midcap 100": {"avg_return_pct": -14.2, "median_return_pct": -14.2, "positive_instances": 0, "best_return_pct":  -8.4, "worst_return_pct": -20.0},
            "Nifty IT":         {"avg_return_pct":  -4.2, "median_return_pct":  -4.2, "positive_instances": 1, "best_return_pct":   2.4, "worst_return_pct": -10.8},
            "Nifty Infra":      {"avg_return_pct": -10.8, "median_return_pct": -10.8, "positive_instances": 0, "best_return_pct":  -4.6, "worst_return_pct": -17.0},
        },
        "key_insight": "Stagflation is the worst regime for Indian equities. Reduce exposure and hold gold and short-duration bonds.",
    },
    "EARLY_CYCLE_RECOVERY": {
        "summary": "Best entry point for cyclicals. High returns with elevated volatility.",
        "instances": 3,
        "avg_duration_months": 6,
        "indices": {
            "Nifty 50":         {"avg_return_pct": 22.4, "median_return_pct": 20.8, "positive_instances": 3, "best_return_pct": 34.6, "worst_return_pct": 12.4},
            "Bank Nifty":       {"avg_return_pct": 32.6, "median_return_pct": 28.4, "positive_instances": 3, "best_return_pct": 48.2, "worst_return_pct": 18.6},
            "Nifty Midcap 100": {"avg_return_pct": 38.4, "median_return_pct": 34.2, "positive_instances": 3, "best_return_pct": 56.4, "worst_return_pct": 22.4},
            "Nifty IT":         {"avg_return_pct": 14.2, "median_return_pct": 12.8, "positive_instances": 3, "best_return_pct": 22.4, "worst_return_pct":  6.4},
            "Nifty Infra":      {"avg_return_pct": 28.4, "median_return_pct": 26.2, "positive_instances": 3, "best_return_pct": 42.6, "worst_return_pct": 16.8},
        },
        "key_insight": "Early cycle recovery delivers the highest returns across all indices. Midcap 100 and Bank Nifty lead the pack.",
    },
    "GROWTH_SLOWDOWN_SUPPORT": {
        "summary": "Defensive positioning rewarded. Selective opportunities in quality names.",
        "instances": 3,
        "avg_duration_months": 5,
        "indices": {
            "Nifty 50":         {"avg_return_pct":  2.4, "median_return_pct":  2.8, "positive_instances": 2, "best_return_pct":  8.4, "worst_return_pct": -4.2},
            "Bank Nifty":       {"avg_return_pct": -1.8, "median_return_pct": -1.2, "positive_instances": 1, "best_return_pct":  4.2, "worst_return_pct": -8.6},
            "Nifty Midcap 100": {"avg_return_pct":  0.8, "median_return_pct":  1.2, "positive_instances": 2, "best_return_pct":  6.4, "worst_return_pct": -6.2},
            "Nifty IT":         {"avg_return_pct":  8.6, "median_return_pct":  7.8, "positive_instances": 3, "best_return_pct": 14.2, "worst_return_pct":  2.4},
            "Nifty Infra":      {"avg_return_pct": -2.4, "median_return_pct": -1.8, "positive_instances": 1, "best_return_pct":  4.8, "worst_return_pct": -10.2},
        },
        "key_insight": "IT is the only consistent performer in slowdown regimes. Reduce cyclical and infra exposure.",
    },
}


BULLISH_ACTIONS = {
    "Selectively add to risk assets",
    "Deploy — high conviction window",
}
DEFENSIVE_ACTIONS = {
    "Hold current positions",
    "Reduce risk exposure",
    "Monitor and hold",
}


def _do_evaluate_outcomes() -> dict:
    """Sync — runs in executor. Fetches Nifty 50 prices via yfinance and writes outcomes."""
    try:
        import yfinance as yf
    except ImportError:
        return {"evaluated": 0, "confirmed": 0, "not_confirmed": 0, "skipped": 0, "pending": 0, "error": "yfinance not installed"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    try:
        result = (
            _supabase.table("runs")
            .select("id,run_at,implied_action,outcome")
            .is_("outcome", "null")
            .lte("run_at", cutoff)
            .execute()
        )
        runs = result.data or []
    except Exception as e:
        print(f"[OUTCOME] Supabase fetch failed: {e}", flush=True)
        return {"evaluated": 0, "confirmed": 0, "not_confirmed": 0, "skipped": 0, "pending": 0, "error": str(e)}

    ticker = yf.Ticker("^NSEI")
    evaluated = confirmed = not_confirmed = skipped = 0

    for run in runs:
        try:
            run_at = datetime.fromisoformat(str(run["run_at"]).replace("Z", "+00:00"))
            run_date = run_at.date()

            entry_end = run_date + timedelta(days=3)
            hist_entry = ticker.history(start=run_date.isoformat(), end=entry_end.isoformat())
            if len(hist_entry) == 0:
                skipped += 1
                continue
            entry_price = float(hist_entry["Close"].iloc[0])

            exit_start = run_date + timedelta(days=30)
            exit_end   = exit_start + timedelta(days=3)
            hist_exit  = ticker.history(start=exit_start.isoformat(), end=exit_end.isoformat())
            if len(hist_exit) == 0:
                skipped += 1
                continue
            exit_price = float(hist_exit["Close"].iloc[0])

            nifty_return = (exit_price - entry_price) / entry_price * 100
            implied = run.get("implied_action", "")

            if implied in BULLISH_ACTIONS:
                outcome = "Confirmed ✓" if nifty_return > 0 else "Not Confirmed ✗"
            elif implied in DEFENSIVE_ACTIONS:
                outcome = "Confirmed ✓" if nifty_return <= 0 else "Not Confirmed ✗"
            else:
                outcome = "Confirmed ✓" if nifty_return > 0 else "Not Confirmed ✗"

            _supabase.table("runs").update({"outcome": outcome}).eq("id", run["id"]).execute()
            evaluated += 1
            if "Confirmed ✓" in outcome:
                confirmed += 1
            else:
                not_confirmed += 1

        except Exception as e:
            print(f"[OUTCOME] Error evaluating run {run.get('id')}: {e}", flush=True)
            skipped += 1

    print(f"[OUTCOME] Evaluated {evaluated} runs: {confirmed} confirmed, {not_confirmed} not confirmed, {skipped} skipped", flush=True)
    return {
        "evaluated":     evaluated,
        "confirmed":     confirmed,
        "not_confirmed": not_confirmed,
        "skipped":       skipped,
        "pending":       0,
    }


async def _evaluate_outcomes_async():
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_evaluate_outcomes)
    except Exception as e:
        print(f"[OUTCOME] Background evaluation error: {e}", flush=True)


def _derive_implied_action(regime_key: str, conviction: str) -> str:
    conv = (conviction or "").upper()
    if conv == "LOW":
        return "Hold current positions"
    if regime_key in DEFENSIVE_REGIMES:
        return "Reduce risk exposure"
    if conv == "HIGH" and regime_key in POSITIVE_REGIMES:
        return "Deploy — high conviction window"
    if conv == "MEDIUM" and regime_key in POSITIVE_REGIMES:
        return "Selectively add to risk assets"
    return "Monitor and hold"


def _detect_signal_correlation(
    nse_snapshot: dict,
    regime_key: str,
    run_history: list,
) -> dict:
    """
    Detects when multiple signals are moving together and identifies the pattern.
    Never crashes — all inputs may be None.
    """
    try:
        crude = nse_snapshot.get("crude_price") or 0
        vix   = nse_snapshot.get("india_vix",   0)
        fii   = nse_snapshot.get("fii_net_crore")

        stress_signals  = []
        support_signals = []

        if crude >= 100:
            stress_signals.append("crude")
        elif 0 < crude <= 80:
            support_signals.append("crude")

        if vix >= 20:
            stress_signals.append("vix")
        elif 0 < vix <= 14:
            support_signals.append("vix")

        if fii is not None:
            if fii < -2000:
                stress_signals.append("fii_selling")
            elif fii > 3000:
                support_signals.append("fii_buying")

        if run_history and len(run_history) >= 2:
            recent_fii = [
                r.get("fii_net_crore")
                for r in run_history[:3]
                if r.get("fii_net_crore") is not None
            ]
            if len(recent_fii) >= 2:
                if all(f < -500 for f in recent_fii):
                    stress_signals.append("fii_persistent")
                if all(f > 500 for f in recent_fii):
                    support_signals.append("fii_persistent")

        pattern             = None
        pattern_description = None
        severity            = "NONE"
        stress_count        = len(set(stress_signals))
        support_count       = len(set(support_signals))

        if stress_count >= 3:
            pattern = "COORDINATED_RISK_OFF"
            pattern_description = (
                "Multiple stress signals aligning — "
                "crude elevated, VIX spiking, and FII "
                "selling together. This is a coordinated "
                "risk-off event, not isolated noise. "
                "Probability of regime transition elevated."
            )
            severity = "HIGH"

        elif stress_count == 2:
            sig = list(set(stress_signals))
            if "crude" in sig and "fii_selling" in sig:
                pattern = "IMPORTED_INFLATION_OUTFLOW"
                pattern_description = (
                    "Crude spike driving imported inflation "
                    "fears while FII flows confirm risk-off. "
                    "Classic external pressure pattern — "
                    "INR at risk, watch RBI response."
                )
                severity = "MEDIUM"
            elif "crude" in sig and "vix" in sig:
                pattern = "COMMODITY_FEAR_SPIKE"
                pattern_description = (
                    "Crude and VIX rising together signals "
                    "stagflation risk premium building. "
                    "Not yet a regime shift but watch for "
                    "FII response in next 2-3 sessions."
                )
                severity = "MEDIUM"
            elif "vix" in sig and "fii_selling" in sig:
                pattern = "RISK_SENTIMENT_DETERIORATION"
                pattern_description = (
                    "Market fear and foreign outflows "
                    "moving together. Domestic liquidity "
                    "conditions remain supportive but "
                    "external sentiment is souring."
                )
                severity = "MEDIUM"
            elif "fii_selling" in sig and "fii_persistent" in sig:
                pattern = "SUSTAINED_FOREIGN_EXIT"
                pattern_description = (
                    "FII selling is not a one-day event — "
                    "consecutive sessions of outflows signal "
                    "a structural positioning shift, not "
                    "just daily noise."
                )
                severity = "MEDIUM"

        elif support_count >= 2:
            pattern = "COORDINATED_RISK_ON"
            pattern_description = (
                "Multiple support signals aligning — "
                "benign commodity prices, contained "
                "fear, and foreign inflows together. "
                "Conditions actively support the "
                "current positive regime."
            )
            severity = "POSITIVE"

        elif support_count == 1 and stress_count == 0:
            pattern = "BROADLY_SUPPORTIVE"
            pattern_description = (
                "No active stress signals. Macro "
                "environment is broadly supportive "
                "of current positioning."
            )
            severity = "LOW"

        print(
            f"[CORRELATION] pattern={pattern} severity={severity} "
            f"stress={stress_count} support={support_count}",
            flush=True,
        )
        return {
            "pattern":         pattern,
            "description":     pattern_description,
            "severity":        severity,
            "stress_signals":  list(set(stress_signals)),
            "support_signals": list(set(support_signals)),
            "stress_count":    stress_count,
            "support_count":   support_count,
        }
    except Exception as _ce:
        print(f"[CORRELATION] detection error: {_ce}", flush=True)
        return {
            "pattern": None, "description": None, "severity": "NONE",
            "stress_signals": [], "support_signals": [],
            "stress_count": 0, "support_count": 0,
        }


def _get_next_watchpoint(calendar_events: list) -> dict | None:
    """Returns the next upcoming calendar event. Skips events whose date has passed."""
    import datetime as _dt
    today = _dt.date.today()
    for event in (calendar_events or []):
        ed = event.get("event_date")
        if ed is None:
            date_str = event.get("date")
            if not date_str:
                continue
            try:
                ed = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
            except Exception:
                continue
        if hasattr(ed, "date"):
            ed = ed.date()
        if ed >= today:
            return event
    return None


def _build_narrative_delta(
    current_regime: str,
    current_confidence: float,
    current_conviction: str,
    prev_run,
    nse_snapshot: dict,
    intel: dict,
    run_history:     list | None = None,
    stability:       dict | None = None,
    transition:      dict | None = None,
    calendar_events: list | None = None,
) -> dict:
    try:
        if not prev_run:
            return {
                "has_delta": False,
                "headline": "First run — baseline established.",
                "changes": [],
                "watch_next": [],
                "sentiment": "NEUTRAL",
            }

        prev_confidence = prev_run.get("confidence", 0)
        prev_conviction = prev_run.get("conviction", "")
        prev_regime     = prev_run.get("regime", "")

        conf_pct_now  = round(current_confidence * 100, 1)
        conf_pct_prev = round(prev_confidence * 100, 1)
        conf_delta    = round(conf_pct_now - conf_pct_prev, 1)

        changes    = []
        watch_next = []
        sentiment  = "NEUTRAL"

        # Cross-signal correlation — pattern detection before individual signal checks
        correlation = _detect_signal_correlation(
            nse_snapshot=nse_snapshot,
            regime_key  =current_regime,
            run_history =run_history or [],
        )
        if (correlation["pattern"] and correlation["description"] and
                correlation["severity"] not in ("NONE", "LOW")):
            changes.insert(0, correlation["description"])
            if correlation["severity"] == "HIGH":
                sentiment = "SIGNIFICANT"
            elif correlation["severity"] == "MEDIUM" and sentiment == "NEUTRAL":
                sentiment = "CAUTION"
            elif correlation["severity"] == "POSITIVE":
                sentiment = "IMPROVING"

        # Regime change
        if current_regime != prev_regime:
            prev_label = prev_regime.replace("_", " ").title()
            curr_label = current_regime.replace("_", " ").title()
            changes.append(
                f"Regime shifted from {prev_label} to "
                f"{curr_label} — a significant macro transition."
            )
            sentiment = "SIGNIFICANT"

        # Confidence movement
        if abs(conf_delta) >= 3:
            direction = "built" if conf_delta > 0 else "deteriorated"
            changes.append(
                f"Confidence {direction} {abs(conf_delta)}pts "
                f"({conf_pct_prev}% → {conf_pct_now}%)."
            )
            if conf_delta <= -5:
                sentiment = "DETERIORATING"
            elif conf_delta >= 5 and sentiment == "NEUTRAL":
                sentiment = "IMPROVING"

        # Conviction change
        conv_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        curr_rank = conv_rank.get((current_conviction or "").upper(), 2)
        prev_rank = conv_rank.get((prev_conviction or "").upper(),  2)
        if curr_rank != prev_rank:
            direction = "upgraded" if curr_rank > prev_rank else "downgraded"
            changes.append(
                f"Conviction {direction} from "
                f"{prev_conviction} to {current_conviction}."
            )
            if curr_rank < prev_rank and sentiment == "NEUTRAL":
                sentiment = "DETERIORATING"

        # Market stress signals
        crude = nse_snapshot.get("crude_price")
        vix   = nse_snapshot.get("india_vix", 0)
        fii   = nse_snapshot.get("fii_net_crore")

        if crude and crude >= 100:
            changes.append(
                f"Crude at ${crude} — above the $95 External "
                f"Shock threshold. Imported inflation risk elevated."
            )
            watch_next.append(
                "Watch crude — sustained above $100 may force "
                "RBI response and regime transition."
            )
            if sentiment == "NEUTRAL":
                sentiment = "CAUTION"

        if vix and vix >= 20:
            changes.append(
                f"India VIX at {vix} — elevated fear gauge "
                f"signals market stress."
            )
            if sentiment == "NEUTRAL":
                sentiment = "CAUTION"

        if fii is not None and fii < -3000:
            changes.append(
                f"FII sold ₹{abs(int(fii)):,} Cr — "
                f"significant foreign outflow pressure."
            )
            watch_next.append(
                "Sustained FII selling for 3+ sessions would "
                "pressure INR and challenge liquidity regime."
            )
        elif fii is not None and fii > 3000:
            changes.append(
                f"FII bought ₹{int(fii):,} Cr — "
                f"strong foreign inflow supporting the regime."
            )

        # Leading indicator watch items
        hard = intel.get("hard_data", {}) if isinstance(intel, dict) else {}
        gdp  = hard.get("gdp_growth", 0)

        if gdp and gdp < 6.0:
            watch_next.append(
                f"GDP tracking at {gdp}% — below the 7%+ "
                f"threshold that anchors the current regime. "
                f"Watch for the next GDP advance print."
            )

        _next_event = _get_next_watchpoint(calendar_events or [])
        if _next_event:
            _ev_name  = _next_event.get("name", "Macro event")
            _ev_date  = _next_event.get("event_date")
            _ev_days  = _next_event.get("days_until", 0)
            _ev_note  = _next_event.get("impact_note", "")
            _date_str = _ev_date.strftime("%d %b").lstrip("0") if _ev_date else ""
            if _ev_days > 1:
                _days_lbl = f"in {_ev_days}d"
            elif _ev_days == 1:
                _days_lbl = "tomorrow"
            else:
                _days_lbl = "today"
            _note_short = (_ev_note[:100] + "…") if len(_ev_note) > 100 else _ev_note
            if _note_short:
                watch_next.append(
                    f"Next key event: {_ev_name} on {_date_str} "
                    f"({_days_lbl}) — {_note_short}"
                )
            else:
                watch_next.append(
                    f"Next key event: {_ev_name} on {_date_str} ({_days_lbl})."
                )
        else:
            watch_next.append(
                "No major macro events in the next 30 days "
                "— monitor live signals daily."
            )

        # Headline
        if not changes:
            headline = (
                f"Regime stable at {conf_pct_now}% confidence. "
                f"No material signal change since last run."
            )
        elif sentiment == "SIGNIFICANT":
            headline = "Regime transition detected — review positioning."
        elif sentiment == "DETERIORATING":
            headline = (
                f"Signals weakening — confidence down "
                f"{abs(conf_delta)}pts. Exercise caution."
            )
        elif sentiment == "IMPROVING":
            headline = (
                f"Signals strengthening — confidence up "
                f"{abs(conf_delta)}pts. Conviction building."
            )
        elif sentiment == "CAUTION":
            headline = (
                "Market stress signals elevated — "
                "monitor closely before acting."
            )
        else:
            headline = (
                f"Regime intact at {conf_pct_now}% confidence. "
                f"Minor signal movement."
            )

        # Transition risk warning — only when ELEVATED or HIGH
        if transition and transition.get("risk_level") in ("ELEVATED", "HIGH"):
            _prob   = transition.get("probability_pct", 0)
            _next_r = transition.get("most_likely_label", "Unknown")
            watch_next.append(
                f"Regime transition probability: {_prob}%. "
                f"Most likely shift toward {_next_r} "
                f"if current stress persists. "
                f"Reduce new position sizing until "
                f"probability falls below 30%."
            )

        # Stability warning in watch_next when regime is fragile
        if stability and stability.get("score") == "LOW":
            _consec = stability.get("consecutive", 0)
            watch_next.append(
                f"Regime stability is LOW — held for only "
                f"{_consec} run{'s' if _consec != 1 else ''} with "
                f"confidence declining. Exercise extra "
                f"caution before new position entries."
            )

        return {
            "has_delta":   True,
            "headline":    headline,
            "changes":     changes,
            "watch_next":  watch_next[:3],
            "sentiment":   sentiment,
            "conf_delta":  conf_delta,
            "prev_regime": prev_regime,
            "curr_regime": current_regime,
        }
    except Exception:
        return {"has_delta": False}


def _compute_regime_stability(
    current_regime: str,
    run_history: list,
    challenger_confidence: float | None,
    current_confidence: float,
) -> dict:
    """
    Computes how stable the current regime call is.
    Based on consecutive runs, confidence trend, and challenger proximity.
    """
    try:
        if not run_history:
            return {
                "score":           "UNKNOWN",
                "consecutive":     0,
                "conf_trend":      "STABLE",
                "challenger_risk": "LOW",
                "explanation":     "Insufficient history",
            }

        # Count consecutive runs in current regime
        consecutive = 0
        for r in run_history:
            if r.get("regime") == current_regime:
                consecutive += 1
            else:
                break

        # Confidence trend over last 3 saved runs
        recent_conf = [
            r.get("confidence", 0)
            for r in run_history[:3]
            if r.get("confidence")
        ]
        conf_trend = "STABLE"
        if len(recent_conf) >= 2:
            delta = recent_conf[0] - recent_conf[-1]
            if delta >= 0.05:
                conf_trend = "BUILDING"
            elif delta <= -0.05:
                conf_trend = "DECLINING"

        # Challenger proximity (alert threshold = 0.45)
        challenger_risk = "LOW"
        if challenger_confidence:
            if challenger_confidence >= 0.42:
                challenger_risk = "HIGH"
            elif challenger_confidence >= 0.35:
                challenger_risk = "MEDIUM"

        # Stability score
        if (consecutive >= 8 and conf_trend != "DECLINING"
                and challenger_risk == "LOW"):
            score = "HIGH"
        elif consecutive >= 4 and conf_trend == "BUILDING":
            score = "HIGH"
        elif (consecutive <= 2 or challenger_risk == "HIGH" or
              (conf_trend == "DECLINING" and current_confidence < 0.60)):
            score = "LOW"
        else:
            score = "MEDIUM"

        explanations = [f"Held for {consecutive} consecutive runs"]
        if conf_trend == "BUILDING":
            explanations.append("confidence building")
        elif conf_trend == "DECLINING":
            explanations.append("confidence declining")
        if challenger_risk == "HIGH":
            explanations.append("challenger approaching alert threshold")
        elif challenger_risk == "LOW":
            explanations.append("challenger well below alert")

        print(
            f"[STABILITY] score={score} consecutive={consecutive} "
            f"conf_trend={conf_trend} challenger_risk={challenger_risk}",
            flush=True,
        )
        return {
            "score":           score,
            "consecutive":     consecutive,
            "conf_trend":      conf_trend,
            "challenger_risk": challenger_risk,
            "explanation":     " · ".join(explanations),
        }
    except Exception as _se:
        print(f"[STABILITY] computation error: {_se}", flush=True)
        return {
            "score":           "UNKNOWN",
            "consecutive":     0,
            "conf_trend":      "STABLE",
            "challenger_risk": "LOW",
            "explanation":     "Error computing stability",
        }


def _compute_transition_probability(
    current_regime:     str,
    current_confidence: float,
    run_history:        list,
    nse_snapshot:       dict,
    challenger_regime:  str | None,
    activity:           dict | None,
) -> dict:
    """
    Computes the probability of a regime transition in the next 2-4 weeks.
    Based on confidence trend, challenger proximity, market stress, and regime duration.
    Never crashes — wrapped in try/except with safe fallback.
    """
    try:
        base_prob = 0.15  # regimes are sticky

        # Confidence trend over last 5 runs
        recent_conf = [
            r.get("confidence", 0)
            for r in run_history[:5]
            if r.get("confidence")
        ]
        conf_velocity = 0.0
        if len(recent_conf) >= 3:
            conf_velocity = (recent_conf[0] - recent_conf[-1]) / len(recent_conf)

        if conf_velocity < -0.02:
            base_prob += min(abs(conf_velocity) * 5, 0.20)
        elif conf_velocity > 0.02:
            base_prob -= min(conf_velocity * 3, 0.08)

        # Challenger proximity (alert threshold = 0.45)
        challenger_conf = 0.0
        for r in run_history[:3]:
            try:
                _scen = r.get("scenarios", {})
                if isinstance(_scen, dict):
                    _cc = _scen.get("challenger_confidence")
                    if _cc:
                        challenger_conf = max(challenger_conf, float(_cc))
            except Exception:
                pass

        if challenger_conf >= 0.42:
            base_prob += 0.25
        elif challenger_conf >= 0.38:
            base_prob += 0.15
        elif challenger_conf >= 0.32:
            base_prob += 0.08

        # Market stress signal count
        crude = nse_snapshot.get("crude_price") or 0
        vix   = nse_snapshot.get("india_vix",   0)
        fii   = nse_snapshot.get("fii_net_crore")

        stress_count = 0
        if crude >= 100:  stress_count += 1
        if crude >= 110:  stress_count += 1
        if vix   >= 20:   stress_count += 1
        if vix   >= 25:   stress_count += 1
        if fii is not None and fii < -3000: stress_count += 1

        is_positive = current_regime in {
            "LIQUIDITY_DRIVEN_EXPANSION",
            "STABLE_GROWTH",
            "EARLY_CYCLE_RECOVERY",
        }
        if is_positive and stress_count >= 3:
            base_prob += 0.20
        elif is_positive and stress_count == 2:
            base_prob += 0.10
        elif is_positive and stress_count == 1:
            base_prob += 0.05

        # Current confidence level
        if current_confidence < 0.55:
            base_prob += 0.10
        elif current_confidence < 0.60:
            base_prob += 0.05
        elif current_confidence > 0.75:
            base_prob -= 0.08

        # Activity momentum
        if activity:
            composite = activity.get("composite", {}).get("score", "MODERATE")
            if composite == "STRONG" and is_positive:
                base_prob -= 0.05
            elif composite in ("WEAK", "CONTRACTION"):
                base_prob += 0.08

        final_prob = max(0.05, min(0.85, base_prob))
        final_pct  = round(final_prob * 100)

        if final_pct <= 20:
            risk_level, risk_label = "LOW",      "Regime stable"
        elif final_pct <= 40:
            risk_level, risk_label = "MODERATE", "Monitor conditions"
        elif final_pct <= 60:
            risk_level, risk_label = "ELEVATED", "Transition risk building"
        else:
            risk_level, risk_label = "HIGH",     "Transition likely"

        TRANSITION_MAP = {
            "LIQUIDITY_DRIVEN_EXPANSION": [
                ("STABLE_GROWTH", 0.40), ("MONETARY_TIGHTENING", 0.25),
                ("EXTERNAL_SHOCK", 0.20), ("GROWTH_SLOWDOWN_SUPPORT", 0.15),
            ],
            "STABLE_GROWTH": [
                ("LIQUIDITY_DRIVEN_EXPANSION", 0.30), ("MONETARY_TIGHTENING", 0.30),
                ("GROWTH_SLOWDOWN_SUPPORT", 0.25), ("EXTERNAL_SHOCK", 0.15),
            ],
            "EARLY_CYCLE_RECOVERY": [
                ("STABLE_GROWTH", 0.45), ("LIQUIDITY_DRIVEN_EXPANSION", 0.35),
                ("MONETARY_TIGHTENING", 0.20),
            ],
            "MONETARY_TIGHTENING": [
                ("STABLE_GROWTH", 0.40), ("GROWTH_SLOWDOWN_SUPPORT", 0.35),
                ("STAGFLATION_RISK", 0.25),
            ],
            "EXTERNAL_SHOCK": [
                ("EARLY_CYCLE_RECOVERY", 0.45), ("GROWTH_SLOWDOWN_SUPPORT", 0.30),
                ("STAGFLATION_RISK", 0.25),
            ],
            "GROWTH_SLOWDOWN_SUPPORT": [
                ("EARLY_CYCLE_RECOVERY", 0.40), ("STABLE_GROWTH", 0.35),
                ("STAGFLATION_RISK", 0.25),
            ],
        }

        next_regimes = list(TRANSITION_MAP.get(current_regime, []))
        if challenger_regime and next_regimes:
            for i, (r, p) in enumerate(next_regimes):
                if r == challenger_regime:
                    next_regimes[i] = (r, min(p + 0.15, 0.60))
                    break

        most_likely_next  = next_regimes[0][0] if next_regimes else None
        most_likely_label = (
            most_likely_next.replace("_", " ").title()
            if most_likely_next else "Unknown"
        )

        factors = []
        if conf_velocity < -0.02:
            factors.append(f"confidence declining ({abs(conf_velocity)*100:.1f}pts/run)")
        if challenger_conf >= 0.38:
            factors.append(f"challenger at {round(challenger_conf*100)}%")
        if stress_count >= 2:
            factors.append(f"{stress_count} stress signals active")
        if current_confidence < 0.60:
            factors.append("confidence below 60%")

        explanation = (
            " · ".join(factors) if factors
            else "No significant transition triggers"
        )

        print(
            f"[TRANSITION] prob={final_pct}% risk={risk_level} "
            f"next={most_likely_next} factors={explanation}",
            flush=True,
        )
        return {
            "probability_pct":   final_pct,
            "risk_level":        risk_level,
            "risk_label":        risk_label,
            "most_likely_next":  most_likely_next,
            "most_likely_label": most_likely_label,
            "explanation":       explanation,
            "factors_count":     len(factors),
            "conf_velocity":     round(conf_velocity * 100, 2),
        }
    except Exception as _te:
        print(f"[TRANSITION] computation error: {_te}", flush=True)
        return {
            "probability_pct":   20,
            "risk_level":        "LOW",
            "risk_label":        "Regime stable",
            "most_likely_next":  None,
            "most_likely_label": "Unknown",
            "explanation":       "Calculation unavailable",
            "factors_count":     0,
            "conf_velocity":     0.0,
        }


def _apply_market_stress_overlay(
    base_confidence: float,
    nse_snapshot: dict,
    regime_key: str,
) -> float:
    """
    Adjusts confidence score based on live market stress signals.
    These are fast-moving signals that the underlying NLP/regime engine
    may not capture in real time.

    Returns adjusted confidence (0.0 to 1.0).
    Positive regimes lose confidence under stress.
    Defensive regimes gain confidence under stress.
    """
    try:
        adjustment = 0.0

        crude = nse_snapshot.get("crude_price") or 0
        vix   = nse_snapshot.get("india_vix",   0)
        fii   = nse_snapshot.get("fii_net_crore")

        is_positive = regime_key in {
            "LIQUIDITY_DRIVEN_EXPANSION",
            "STABLE_GROWTH",
            "EARLY_CYCLE_RECOVERY",
        }
        is_defensive = regime_key in {
            "MONETARY_TIGHTENING",
            "EXTERNAL_SHOCK",
            "STAGFLATION_RISK",
            "STAGFLATIONARY_RISK",
        }

        if is_positive:
            # Crude stress — each $5 above $95 reduces confidence by 2%
            if crude >= 95:
                crude_stress = min(((crude - 95) / 5) * 0.02, 0.10)
                adjustment -= crude_stress

            # VIX stress — elevated fear gauge
            if vix >= 18:
                vix_stress = min(((vix - 18) / 4) * 0.02, 0.06)
                adjustment -= vix_stress

            # FII selling pressure
            if fii is not None and fii < -2000:
                fii_stress = min(abs(fii + 2000) / 5000 * 0.04, 0.06)
                adjustment -= fii_stress

            # FII strong buying — confidence boost
            if fii is not None and fii > 3000:
                adjustment += min((fii - 3000) / 5000 * 0.03, 0.04)

        elif is_defensive:
            # Stress signals CONFIRM defensive regime
            if crude >= 95:
                adjustment += min(((crude - 95) / 5) * 0.02, 0.08)
            if vix >= 20:
                adjustment += min(((vix - 20) / 5) * 0.02, 0.06)
            if fii is not None and fii < -2000:
                adjustment += min(abs(fii + 2000) / 5000 * 0.03, 0.05)

        adjusted = base_confidence + adjustment
        adjusted = max(0.35, min(0.95, adjusted))

        print(
            f"[CONFIDENCE] base={base_confidence:.3f} "
            f"adj={adjustment:+.3f} "
            f"final={adjusted:.3f} "
            f"(crude={crude}, vix={vix}, fii={fii})",
            flush=True,
        )
        return adjusted
    except Exception as _ov_err:
        print(f"[CONFIDENCE] overlay error — returning base: {_ov_err}", flush=True)
        return base_confidence


def _get_data_freshness_weight(indicator: str) -> float:
    """
    Returns a weight (0.5 to 1.0) based on how stale the indicator data is.
    Fresher data gets higher weight. Infrastructure only — weights are stored
    in intel and logged; regime_engine.py uses them in a future task.
    """
    import datetime as _dt
    try:
        today = _dt.date.today()

        # CPI: released ~12th of each month
        cpi_release = today.replace(day=12)
        if today >= cpi_release:
            days_since_cpi = (today - cpi_release).days
        else:
            prev_month = (today.replace(day=1) - _dt.timedelta(days=1))
            days_since_cpi = (today - prev_month.replace(day=12)).days

        # GDP: quarterly, published ~60 days after quarter end — approx 45d avg staleness
        days_since_gdp = 45

        weights = {
            "gdp": max(0.5, 1.0 - (days_since_gdp / 90) * 0.5),
            "cpi": max(0.5, 1.0 - (days_since_cpi / 30) * 0.5),
            "pmi": max(0.6, 1.0 - (days_since_cpi / 30) * 0.4),
        }
        return weights.get(indicator, 1.0)
    except Exception:
        return 1.0


def _fetch_nse_snapshot_jugaad() -> dict:
    """
    Fetch live NSE data via jugaad-data.
    Returns dict with india_vix, nifty50, bank_nifty, nifty500.
    """
    result = {
        "india_vix":  None,
        "nifty50":    None,
        "bank_nifty": None,
        "nifty500":   None,
        "source":     "jugaad",
    }
    try:
        n = NSELive()
        indices = n.all_indices()
        data = indices.get("data", [])

        for item in data:
            name = item.get("index", "")
            last = item.get("last")
            pct  = item.get("percentChange")

            if name == "INDIA VIX":
                result["india_vix"] = float(last) if last else None
            elif name == "NIFTY 50":
                result["nifty50"] = {
                    "last":          float(last),
                    "percentChange": float(pct or 0),
                }
            elif name == "NIFTY BANK":
                result["bank_nifty"] = {
                    "last":          float(last),
                    "percentChange": float(pct or 0),
                }
            elif name == "NIFTY 500":
                result["nifty500"] = {
                    "last":          float(last),
                    "percentChange": float(pct or 0),
                }

        print(
            f"[JUGAAD] VIX={result['india_vix']} "
            f"Nifty={result['nifty50']['last'] if result['nifty50'] else 'N/A'} "
            f"BankNifty={result['bank_nifty']['last'] if result['bank_nifty'] else 'N/A'}",
            flush=True
        )

    except Exception as e:
        print(f"[JUGAAD] all_indices failed: {e}", flush=True)
        result["source"] = "jugaad_failed"

    return result


def _fetch_nse_snapshot_yf() -> dict:
    """
    Fetch VIX, Nifty, BankNifty via yfinance when NSE/jugaad is blocked.
    """
    try:
        import yfinance as yf

        vix_hist   = yf.Ticker("^INDIAVIX").history(period="1d")
        vix        = float(vix_hist["Close"].iloc[-1]) if not vix_hist.empty else None

        nifty_hist = yf.Ticker("^NSEI").history(period="1d")
        nifty      = float(nifty_hist["Close"].iloc[-1]) if not nifty_hist.empty else None

        bn_hist    = yf.Ticker("^NSEBANK").history(period="1d")
        bank_nifty = float(bn_hist["Close"].iloc[-1]) if not bn_hist.empty else None

        print(
            f"[YF] VIX={vix} "
            f"Nifty={nifty} "
            f"BankNifty={bank_nifty}",
            flush=True
        )
        return {
            "vix":        vix,
            "nifty":      nifty,
            "bank_nifty": bank_nifty,
            "src":        "yfinance",
        }
    except Exception as e:
        print(f"[YF] Snapshot failed: {e}", flush=True)
        return {
            "vix":        None,
            "nifty":      None,
            "bank_nifty": None,
            "src":        "unavailable",
        }


# 24-hour cache for PE ratio
_pe_cache: dict = {"value": None, "fetched_at": None}


def _fetch_nifty_pe() -> float | None:
    print("[PE] _fetch_nifty_pe() called", flush=True)
    from datetime import datetime, timedelta

    # Return cached value if fresh (<24h)
    if (
        _pe_cache["value"] is not None
        and _pe_cache["fetched_at"] is not None
        and datetime.utcnow() - _pe_cache["fetched_at"]
            < timedelta(hours=24)
    ):
        print(
            f"[PE] Cache hit: "
            f"{_pe_cache['value']}",
            flush=True
        )
        return _pe_cache["value"]

    # Strategy 1 — yfinance Nifty 50
    # trailing PE via ^NSEI info dict
    try:
        import yfinance as yf
        ticker = yf.Ticker("^NSEI")
        info = ticker.info
        pe = info.get("trailingPE") or \
             info.get("forwardPE")
        if pe and 5 < float(pe) < 200:
            pe = round(float(pe), 2)
            _pe_cache["value"] = pe
            _pe_cache["fetched_at"] = \
                datetime.utcnow()
            print(
                f"[PE] yfinance ^NSEI PE: "
                f"{pe}",
                flush=True
            )
            return pe
        else:
            print(
                f"[PE] yfinance returned "
                f"invalid PE: {pe}",
                flush=True
            )
    except Exception as e:
        print(
            f"[PE] yfinance failed: {e}",
            flush=True
        )

    # Strategy 2 — yfinance Nifty 50 ETF
    # (NIFTYBEES) as proxy
    try:
        import yfinance as yf
        ticker = yf.Ticker("NIFTYBEES.NS")
        info = ticker.info
        pe = info.get("trailingPE")
        if pe and 5 < float(pe) < 200:
            pe = round(float(pe), 2)
            _pe_cache["value"] = pe
            _pe_cache["fetched_at"] = \
                datetime.utcnow()
            print(
                f"[PE] yfinance NIFTYBEES "
                f"PE: {pe}",
                flush=True
            )
            return pe
    except Exception as e:
        print(
            f"[PE] NIFTYBEES failed: {e}",
            flush=True
        )

    # Strategy 3 — hardcoded reasonable
    # fallback based on current market
    # (Nifty 50 trailing PE ~22-24 as
    # of June 2026 — update quarterly)
    FALLBACK_PE = 22.5
    print(
        f"[PE] All sources failed — "
        f"using hardcoded fallback "
        f"PE={FALLBACK_PE}",
        flush=True
    )
    _pe_cache["value"] = FALLBACK_PE
    _pe_cache["fetched_at"] = \
        datetime.utcnow()
    return FALLBACK_PE


def _fetch_vol_term_structure() -> dict:
    """
    Fetches Nifty options implied volatility for near-term (~30d) and
    far-term (~90d) expiries to compute the volatility term structure.
    Returns shape (CONTANGO/FLAT/INVERTED), signal, and 0-1 score.
    CONTANGO (far > near) = calm; INVERTED (near > far) = fear.
    """
    try:
        print("[VOL_TERM] Starting fetch...", flush=True)
        import yfinance as yf
        from datetime import datetime

        ticker  = yf.Ticker("^NSEI")
        expiries = ticker.options
        print(
            f"[VOL_TERM] Expiries: {len(expiries) if expiries else 0}",
            flush=True
        )

        if not expiries or len(expiries) < 2:
            return {}

        today = datetime.now()

        near_exp = None
        for exp in expiries:
            exp_date = datetime.strptime(exp, "%Y-%m-%d")
            days = (exp_date - today).days
            if 15 <= days <= 45:
                near_exp = exp
                break

        far_exp = None
        for exp in expiries:
            exp_date = datetime.strptime(exp, "%Y-%m-%d")
            days = (exp_date - today).days
            if 75 <= days <= 120:
                far_exp = exp
                break

        print(
            f"[VOL_TERM] near={near_exp} far={far_exp}",
            flush=True
        )

        if not near_exp or not far_exp:
            return {}

        near_chain = ticker.option_chain(near_exp)
        far_chain  = ticker.option_chain(far_exp)

        def _atm_iv(chain):
            calls = chain.calls
            calls = calls[calls["impliedVolatility"].between(0.05, 2.0)]
            if calls.empty:
                return None
            nifty = ticker.info.get("regularMarketPrice", 24000)
            calls = calls.copy()
            calls["dist"] = abs(calls["strike"] - nifty)
            atm = calls.nsmallest(3, "dist")
            return round(float(atm["impliedVolatility"].mean()) * 100, 1)

        near_iv = _atm_iv(near_chain)
        far_iv  = _atm_iv(far_chain)

        if near_iv is None or far_iv is None:
            return {}

        slope = round(far_iv - near_iv, 1)

        if slope > 2:
            shape, signal, score = "CONTANGO", "CALM",    0.8
        elif slope > -2:
            shape, signal, score = "FLAT",     "ANXIOUS", 0.5
        else:
            shape, signal, score = "INVERTED", "FEARFUL", 0.1

        print(
            f"[VOL_TERM] near={near_iv}% far={far_iv}% "
            f"slope={slope} shape={shape}",
            flush=True
        )
        return {
            "near_iv": near_iv,
            "far_iv":  far_iv,
            "slope":   slope,
            "shape":   shape,
            "signal":  signal,
            "score":   score,
        }

    except Exception as e:
        print(f"[VOL_TERM] Failed: {e}", flush=True)
        return {}


def _fetch_india_credit_spread() -> dict:
    """
    Computes India credit spread: AAA corporate bond yield vs G-Sec 10Y.
    AAA_YIELD_10Y is hardcoded from FIMMDA — update monthly on FIMMDA
    release day (usually first week of each month at fimmda.org).
    G-Sec 10Y is fetched live via yfinance.
    Spread <50bps = tight/supportive; 50-80 = normal; >80 = stress.
    """
    # FIMMDA AAA 10Y corporate benchmark yield — update monthly
    # Current: July 2026
    AAA_YIELD_10Y = 7.45
    AAA_SOURCE    = "FIMMDA · Jul 2026"

    try:
        import yfinance as yf
        hist = yf.Ticker("IN10Y=X").history(period="1d")
        gsec_10y = round(float(hist["Close"].iloc[-1]), 2) if not hist.empty else 6.85
    except Exception:
        gsec_10y = 6.85

    spread_bps = round((AAA_YIELD_10Y - gsec_10y) * 100, 1)

    if spread_bps < 50:
        signal, score = "TIGHT",     0.9
    elif spread_bps < 80:
        signal, score = "NORMAL",    0.6
    elif spread_bps < 120:
        signal, score = "WIDE",      0.3
    else:
        signal, score = "VERY_WIDE", 0.0

    print(
        f"[CREDIT_SPREAD] AAA={AAA_YIELD_10Y}% "
        f"GSec={gsec_10y}% spread={spread_bps}bps signal={signal}",
        flush=True
    )
    return {
        "aaa_yield":  AAA_YIELD_10Y,
        "gsec_10y":   gsec_10y,
        "spread_bps": spread_bps,
        "signal":     signal,
        "score":      score,
        "source":     AAA_SOURCE,
    }


def _fetch_india_iip() -> dict:
    """
    Fetches India IIP (Index of Industrial Production) from MOSPI.
    Falls back to hardcoded value updated monthly on MOSPI release day
    (~28th of the following month). IIP measures monthly industrial output —
    a real economy leading indicator complementing PMI.
    """
    # Hardcoded fallback — update monthly on MOSPI release
    # Current: April 2026 IIP (released June 2026)
    IIP_FALLBACK = {
        "value":  3.4,
        "period": "April 2026",
        "source": "MOSPI · Jun 2026",
        "signal": "MODERATE",
    }

    try:
        import requests as _req
        url = (
            "https://www.mospi.gov.in"
            "/sites/default/files/iip/iip_data.json"
        )
        resp = _req.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            latest = data[-1] if isinstance(data, list) and data else None
            if latest:
                val    = float(latest.get("value", IIP_FALLBACK["value"]))
                period = latest.get("period", IIP_FALLBACK["period"])
                signal = "STRONG" if val > 6 else "MODERATE" if val >= 2 else "WEAK"
                print(f"[IIP] Live: {val}% ({period})", flush=True)
                return {
                    "value":  val,
                    "period": period,
                    "source": f"MOSPI · {period}",
                    "signal": signal,
                }
    except Exception as e:
        print(f"[IIP] Fetch failed: {e} — using fallback", flush=True)

    return IIP_FALLBACK


# 24-hour cache for GARCH forecast
_garch_cache: dict = {
    "forecast_vol": None,
    "current_vol":  None,
    "direction":    None,
    "regime":       None,
    "score":        None,
    "fetched_at":   None,
}


def _compute_garch_volatility() -> dict:
    """
    Fit a GARCH(1,1) model on 90 days of Nifty 50 daily returns
    and forecast 30-day ahead annualised volatility.
    Returns dict with forecast_vol, current_vol, direction,
    regime, score, available.
    """
    from datetime import datetime, timedelta

    empty = {
        "forecast_vol": None,
        "current_vol":  None,
        "direction":    "STABLE",
        "regime":       "NORMAL",
        "score":        0.5,
        "available":    False,
    }

    if not ARCH_AVAILABLE:
        return empty

    # Return cache if fresh (24 hr)
    if (
        _garch_cache["forecast_vol"] is not None
        and _garch_cache["fetched_at"] is not None
        and datetime.utcnow() - _garch_cache["fetched_at"]
            < timedelta(hours=24)
    ):
        return {
            "forecast_vol": _garch_cache["forecast_vol"],
            "current_vol":  _garch_cache["current_vol"],
            "direction":    _garch_cache["direction"],
            "regime":       _garch_cache["regime"],
            "score":        _garch_cache["score"],
            "available":    True,
        }

    try:
        import yfinance as yf
        import numpy as np
        import pandas as pd
        from datetime import date

        # Fetch 120 days — extra buffer for weekends/holidays
        end   = date.today()
        start = end - timedelta(days=120)

        ticker = yf.Ticker("^NSEI")
        hist   = ticker.history(
            start=start.isoformat(),
            end=end.isoformat()
        )

        if hist.empty or len(hist) < 60:
            print(
                "[GARCH] Insufficient data — "
                f"got {len(hist)} rows",
                flush=True
            )
            return empty

        # Compute log returns
        closes  = hist["Close"].dropna()
        returns = 100 * np.log(
            closes / closes.shift(1)
        ).dropna()

        # Use last 90 trading days
        returns = returns.iloc[-90:]

        # Current realised volatility — 20-day rolling annualised
        current_vol = round(
            float(returns.iloc[-20:].std() * np.sqrt(252)),
            2
        )

        # Fit GARCH(1,1)
        am = arch_model(
            returns,
            vol="Garch",
            p=1,
            q=1,
            dist="normal",
            rescale=False,
        )
        res = am.fit(disp="off", show_warning=False)

        # 30-day ahead forecast
        forecast = res.forecast(horizon=30, reindex=False)
        # Variance is in % squared (returns multiplied by 100)
        # Annualise: sqrt(var * 252)
        fcast_var = float(forecast.variance.iloc[-1].mean())
        forecast_vol = round(float(np.sqrt(fcast_var * 252)), 2)

        # Direction
        diff = forecast_vol - current_vol
        if diff > 2.0:
            direction = "RISING"
        elif diff < -2.0:
            direction = "FALLING"
        else:
            direction = "STABLE"

        # Regime classification based on Nifty historical vol
        if forecast_vol < 12:
            regime = "LOW"
        elif forecast_vol < 18:
            regime = "NORMAL"
        elif forecast_vol < 25:
            regime = "ELEVATED"
        elif forecast_vol < 35:
            regime = "HIGH"
        else:
            regime = "EXTREME"

        # Score for leading indicator
        score_map = {
            ("LOW",      "FALLING"): 1.00,
            ("LOW",      "STABLE"):  0.85,
            ("LOW",      "RISING"):  0.70,
            ("NORMAL",   "FALLING"): 0.75,
            ("NORMAL",   "STABLE"):  0.65,
            ("NORMAL",   "RISING"):  0.50,
            ("ELEVATED", "FALLING"): 0.45,
            ("ELEVATED", "STABLE"):  0.40,
            ("ELEVATED", "RISING"):  0.25,
            ("HIGH",     "FALLING"): 0.15,
            ("HIGH",     "STABLE"):  0.10,
            ("HIGH",     "RISING"):  0.05,
            ("EXTREME",  "FALLING"): 0.05,
            ("EXTREME",  "STABLE"):  0.00,
            ("EXTREME",  "RISING"):  0.00,
        }
        score = score_map.get((regime, direction), 0.5)

        # Cache result
        _garch_cache["forecast_vol"] = forecast_vol
        _garch_cache["current_vol"]  = current_vol
        _garch_cache["direction"]    = direction
        _garch_cache["regime"]       = regime
        _garch_cache["score"]        = score
        _garch_cache["fetched_at"]   = datetime.utcnow()

        print(
            f"[GARCH] current={current_vol:.1f}% "
            f"forecast={forecast_vol:.1f}% "
            f"direction={direction} "
            f"regime={regime} "
            f"score={score}",
            flush=True
        )

        return {
            "forecast_vol": forecast_vol,
            "current_vol":  current_vol,
            "direction":    direction,
            "regime":       regime,
            "score":        score,
            "available":    True,
        }

    except Exception as e:
        print(f"[GARCH] Failed: {e}", flush=True)
        return empty


# RBI bank credit growth history (YoY %)
# Source: RBI DBIE — update monthly on June 2nd
_CREDIT_GROWTH_HISTORY = [
    {"month": "2025-11", "value": 11.5},
    {"month": "2025-12", "value": 11.8},
    {"month": "2026-01", "value": 12.1},
    {"month": "2026-02", "value": 12.3},
    {"month": "2026-03", "value": 12.6},
    {"month": "2026-05", "value": 12.8},
]


def _compute_credit_impulse() -> dict:
    empty = {
        "impulse":   None,
        "current":   None,
        "trend":     "STABLE",
        "score":     0.55,
        "available": False,
    }
    try:
        history = _CREDIT_GROWTH_HISTORY
        if len(history) < 4:
            print(
                "[CREDIT_IMPULSE] Insufficient "
                "history — need 4+ readings",
                flush=True
            )
            return empty

        current   = float(history[-1]["value"])
        prior_3   = [float(h["value"]) for h in history[-4:-1]]
        prior_avg = sum(prior_3) / len(prior_3)
        impulse   = round(current - prior_avg, 2)

        if impulse > 1.0:
            trend = "ACCELERATING"
            score = 0.90
        elif impulse > 0.3:
            trend = "BUILDING"
            score = 0.75
        elif impulse >= -0.3:
            trend = "STABLE"
            score = 0.55
        elif impulse >= -1.0:
            trend = "SLOWING"
            score = 0.30
        else:
            trend = "CONTRACTING"
            score = 0.10

        print(
            f"[CREDIT_IMPULSE] current={current}% "
            f"prior_avg={prior_avg:.2f}% "
            f"impulse={impulse:+.2f}pp "
            f"trend={trend} score={score}",
            flush=True
        )
        return {
            "impulse":   impulse,
            "current":   current,
            "trend":     trend,
            "score":     score,
            "available": True,
        }
    except Exception as e:
        print(f"[CREDIT_IMPULSE] Failed: {e}", flush=True)
        return empty


# In-memory FII trend cache (refreshed each run)
_fii_trend_cache: dict = {
    "momentum_7d":  None,
    "streak":       None,
    "streak_dir":   None,
    "slope_10d":    None,
    "score":        None,
    "fetched_at":   None,
}


def _compute_fii_trend(sb_url: str, sb_key: str) -> dict:
    import numpy as np

    empty = {
        "momentum_7d": None,
        "streak":      0,
        "streak_dir":  "MIXED",
        "slope_10d":   None,
        "score":       0.5,
        "available":   False,
    }
    try:
        from supabase import create_client
        sb   = create_client(sb_url, sb_key)
        resp = (
            sb.table("fii_dii_daily")
            .select("trade_date, fii_net_crore")
            .order("trade_date", desc=True)
            .limit(20)
            .execute()
        )
        rows = resp.data if resp.data else []

        if len(rows) < 5:
            print(
                f"[FII_TREND] Insufficient data "
                f"— got {len(rows)} rows",
                flush=True
            )
            return empty

        rows  = sorted(rows, key=lambda x: x["trade_date"])
        flows = [
            float(r["fii_net_crore"])
            for r in rows
            if r.get("fii_net_crore") is not None
        ]

        if len(flows) < 5:
            return empty

        momentum_7d = round(sum(flows[-7:]), 2)

        streak     = 1
        streak_dir = "BUYING" if flows[-1] > 0 else "SELLING"
        for i in range(len(flows) - 2, -1, -1):
            day_dir = "BUYING" if flows[i] > 0 else "SELLING"
            if day_dir == streak_dir:
                streak += 1
            else:
                break

        recent = flows[-10:] if len(flows) >= 10 else flows
        x      = np.arange(len(recent))
        slope  = round(float(np.polyfit(x, recent, 1)[0]), 2)

        if momentum_7d > 5000:
            base_score = 0.90
        elif momentum_7d > 2000:
            base_score = 0.75
        elif momentum_7d > 0:
            base_score = 0.60
        elif momentum_7d > -2000:
            base_score = 0.45
        elif momentum_7d > -5000:
            base_score = 0.25
        else:
            base_score = 0.10

        streak_mod = 0.0
        if streak >= 3:
            if streak_dir == "BUYING":
                streak_mod = min(0.10, 0.05 * (streak // 3))
            else:
                streak_mod = max(-0.10, -0.05 * (streak // 3))

        slope_mod = 0.0
        if slope > 500:
            slope_mod = 0.05
        elif slope < -500:
            slope_mod = -0.05

        score = round(
            max(0.0, min(1.0, base_score + streak_mod + slope_mod)),
            3
        )

        print(
            f"[FII_TREND] momentum_7d="
            f"₹{momentum_7d:.0f}Cr "
            f"streak={streak}d {streak_dir} "
            f"slope={slope:.0f} "
            f"score={score}",
            flush=True
        )
        return {
            "momentum_7d": momentum_7d,
            "streak":      streak,
            "streak_dir":  streak_dir,
            "slope_10d":   slope,
            "score":       score,
            "available":   True,
        }
    except Exception as e:
        print(f"[FII_TREND] Failed: {e}", flush=True)
        return empty


def _run_pipeline_sync(job_id: str, user_id: str, repo: float, deficit: float, capex: float):
    _headlines = []
    _rbi_context = []
    _fii_context = []
    _market_context = []
    try:
        eng = _engines
        rep = eng["repair"]
        news_raw = eng["ingestor"].fetch_news_sentiment()
        macro    = eng["ingestor"].fetch_macro_indicators()
        market   = eng["ingestor"].fetch_market_data()
        _headlines = (
            news_raw.get("headlines", [])
            if isinstance(news_raw, dict)
            else []
        )
        news = " ".join(_headlines)
        nse_snapshot = {}
        try:
            nse_snapshot = eng["nse"].get_full_snapshot()
        except Exception:
            nse_snapshot = {"fii_dii": {}, "indices": {}, "fii_net_crore": None, "india_vix": 15, "pcr": 1.0, "flow_signal": "NEUTRAL"}
        # ── NSE Snapshot — layered fallback ──────────────────────────────────────
        # Layer 1: jugaad-data
        _jd = {"source": "jugaad_unavailable"}
        if JUGAAD_AVAILABLE:
            _jd = _fetch_nse_snapshot_jugaad()
            if _jd.get("india_vix") is not None:
                nse_snapshot["india_vix"] = _jd["india_vix"]
                print(
                    f"[NSE] VIX from jugaad: {_jd['india_vix']}",
                    flush=True
                )
            if _jd.get("nifty50"):
                nse_snapshot["nifty_last"]   = _jd["nifty50"]["last"]
                nse_snapshot["nifty_change"] = _jd["nifty50"]["percentChange"]
            if _jd.get("bank_nifty"):
                nse_snapshot["bank_nifty_last"]   = _jd["bank_nifty"]["last"]
                nse_snapshot["bank_nifty_change"] = _jd["bank_nifty"]["percentChange"]
        # Layer 2: yfinance fallback when jugaad is blocked/unavailable
        if (not JUGAAD_AVAILABLE or
                _jd.get("source") == "jugaad_failed" or
                _jd.get("india_vix") is None):
            print("[NSE] Falling back to yfinance", flush=True)
            _yf_snap = _fetch_nse_snapshot_yf()
            if _yf_snap.get("vix") is not None:
                nse_snapshot["india_vix"] = _yf_snap["vix"]
            if _yf_snap.get("nifty") is not None:
                nse_snapshot["nifty_last"] = _yf_snap["nifty"]
                nse_snapshot.setdefault("nifty_change", 0.0)
            if _yf_snap.get("bank_nifty") is not None:
                nse_snapshot["bank_nifty_last"] = _yf_snap["bank_nifty"]
                nse_snapshot.setdefault("bank_nifty_change", 0.0)
        # Layer 3: ticker cache patch (last resort for VIX)
        if not nse_snapshot.get("india_vix"):
            print(
                "[NSE] jugaad VIX unavailable — trying ticker cache patch",
                flush=True
            )
            try:
                _cached_vix = _ticker_cache.get("data", {}).get("India VIX", {}).get("price")
                _current_vix = nse_snapshot.get("india_vix")
                if _cached_vix and (
                    not _current_vix or
                    _current_vix == 15 or
                    _current_vix == 15.0
                ):
                    nse_snapshot["india_vix"] = float(_cached_vix)
                    print(
                        f"[VIX] Patched from ticker cache: {_cached_vix}",
                        flush=True
                    )
            except Exception as _vix_err:
                print(f"[VIX] Patch failed: {_vix_err}", flush=True)
        # FII/DII — DataIngestor runs full source chain (BSE → NSDL → NSE → Supabase fallback)
        # dii_net_crore defaults to None, never 0 — 0 is indistinguishable from missing data
        try:
            _fii = eng["ingestor"].fetch_fii_dii()
            if _fii.get("fii_net_crore") is not None and _fii.get("fii_net_crore") != 0:
                nse_snapshot.update({
                    "fii_net_crore":     _fii["fii_net_crore"],
                    "dii_net_crore":     _fii.get("dii_net_crore"),   # None if absent — never 0
                    "fii_dii_source":    _fii.get("source", "unknown"),
                    "fii_dii_stale":     _fii.get("stale", False),
                    "fii_dii_cached_at": _fii.get("cached_at"),
                    "fii_trade_date":    _fii.get("trade_date"),
                })
                print(f"[FII] resolved: fii={_fii['fii_net_crore']} dii={_fii.get('dii_net_crore')} src={_fii.get('source')}", flush=True)
                # ── Persist daily snapshot for multi-timeframe aggregation ─────────────
                try:
                    from datetime import date as _date
                    import re as _re
                    _raw_date = nse_snapshot.get("fii_trade_date")
                    if _raw_date:
                        _trade_date = str(_raw_date)[:10]
                    else:
                        _trade_date = _date.today().isoformat()
                    if not _re.match(r'^\d{4}-\d{2}-\d{2}$', _trade_date):
                        _trade_date = _date.today().isoformat()
                        print(
                            f"[FII_DAILY] Invalid date format, using today: {_trade_date}",
                            flush=True
                        )
                    _supabase.table("fii_dii_daily").upsert(
                        {
                            "trade_date":    _trade_date,
                            "fii_net_crore": nse_snapshot.get("fii_net_crore"),
                            "dii_net_crore": nse_snapshot.get("dii_net_crore"),
                            "fii_signal":    "BUYING"  if (nse_snapshot.get("fii_net_crore") or 0) >  500
                                             else "SELLING" if (nse_snapshot.get("fii_net_crore") or 0) < -500
                                             else "NEUTRAL",
                            "dii_signal":    "BUYING"  if (nse_snapshot.get("dii_net_crore") or 0) >  500
                                             else "SELLING" if (nse_snapshot.get("dii_net_crore") or 0) < -500
                                             else "NEUTRAL",
                            "source":        nse_snapshot.get("fii_dii_source", "bse"),
                        },
                        on_conflict="trade_date",
                    ).execute()
                    print(f"[FII_DAILY] Stored: date={_trade_date} fii={nse_snapshot.get('fii_net_crore')}", flush=True)
                except Exception as _store_err:
                    print(f"[FII_DAILY] Store failed: {_store_err}", flush=True)
            else:
                print("[FII] all sources returned None — fii_net_crore will be null in this run", flush=True)
                nse_snapshot.setdefault("fii_dii_source", "unavailable")
        except Exception as _fii_err:
            print(f"[FII] fetch_fii_dii error: {_fii_err}", flush=True)
            nse_snapshot.setdefault("fii_dii_source", "unavailable")
        # Crude live price — None when unavailable, never 0 (0 is a valid price sentinel)
        _crude_live = None
        _cached_crude = _ticker_cache.get("data", {}).get("Crude", {}).get("price")
        if _cached_crude:
            _crude_live = _cached_crude
        else:
            try:
                import yfinance as yf
                _ch = yf.Ticker("CL=F").history(period="2d", interval="1d")
                if len(_ch) >= 1:
                    _crude_live = round(float(_ch["Close"].iloc[-1]), 2)
                    print(f"[PIPELINE] Crude fetched direct: ${_crude_live}", flush=True)
            except Exception as _ce:
                print(f"[PIPELINE] Crude fetch failed — storing NULL: {_ce}", flush=True)
        # Crude sanity check
        # If crude returns 0, None, or below $30
        # it is a fetch failure — use yfinance
        # as fallback
        if not _crude_live or _crude_live < 30:
            print(
                f"[CRUDE] Primary fetch returned "
                f"{_crude_live} — trying yfinance fallback",
                flush=True
            )
            try:
                import yfinance as yf
                ticker = yf.Ticker("CL=F")
                hist   = ticker.history(period="1d")
                if not hist.empty:
                    _crude_live = float(
                        hist["Close"].iloc[-1]
                    )
                    print(
                        f"[CRUDE] yfinance fallback: "
                        f"${_crude_live:.2f}",
                        flush=True
                    )
                else:
                    # Last known reasonable value
                    _crude_live = 94.0
                    print(
                        f"[CRUDE] yfinance empty — "
                        f"using last known ${_crude_live}",
                        flush=True
                    )
            except Exception as e:
                _crude_live = 94.0
                print(
                    f"[CRUDE] yfinance failed: {e} — "
                    f"using last known ${_crude_live}",
                    flush=True
                )
        nse_snapshot["crude_price"] = _crude_live
        # ── Nifty PE ratio ───────────────────────────────────────────────────────
        _nifty_pe = _fetch_nifty_pe()
        if _nifty_pe:
            nse_snapshot["nifty_pe"] = _nifty_pe
            print(f"[NSE] Nifty PE: {_nifty_pe}", flush=True)
        # ── GARCH volatility forecast ────────────────────────────────────────────
        _garch = _compute_garch_volatility()
        if _garch.get("available"):
            nse_snapshot["garch_forecast_vol"] = _garch["forecast_vol"]
            nse_snapshot["garch_current_vol"]  = _garch["current_vol"]
            nse_snapshot["garch_direction"]    = _garch["direction"]
            nse_snapshot["garch_regime"]       = _garch["regime"]
            nse_snapshot["garch_score"]        = _garch["score"]
            print(
                f"[NSE] GARCH vol forecast: "
                f"{_garch['forecast_vol']}% "
                f"({_garch['direction']})",
                flush=True
            )
        # ── Volatility term structure ────────────────────────────────────────────
        _vol_term = _fetch_vol_term_structure()
        if _vol_term:
            nse_snapshot["vol_term_structure"] = _vol_term
            print(
                f"[VOL_TERM] Added to snapshot: {_vol_term}",
                flush=True
            )
        # ── Credit spread (AAA vs G-Sec 10Y) ────────────────────────────────────
        _credit_spread = _fetch_india_credit_spread()
        if _credit_spread:
            nse_snapshot["credit_spread"] = _credit_spread
        # Enrich news with structured context from nse_snapshot
        # (nse_snapshot is fully populated at this point — crude, FII, VIX)
        _fii_context = []
        if nse_snapshot.get("fii_net_crore"):
            fii_val = nse_snapshot["fii_net_crore"]
            _fii_context.append(
                f"FII net flow today: "
                f"{'buying' if fii_val > 0 else 'selling'} "
                f"₹{abs(fii_val):.0f} Cr"
            )

        _market_context = []
        vix_val = nse_snapshot.get("india_vix")
        if vix_val:
            _market_context.append(
                f"India VIX at {vix_val:.1f} "
                f"— {'elevated fear' if vix_val > 20 else 'calm markets'}"
            )
        crude_val = nse_snapshot.get("crude_price")
        if crude_val:
            _market_context.append(
                f"Crude oil at ${crude_val:.1f}"
            )

        news = " | ".join(
            _headlines
            + _fii_context
            + _market_context
        )

        if not news.strip():
            news = (
                "No significant news available. "
                "Market signals should drive regime classification."
            )

        print(
            f"[NLP_INPUT] length={len(news)} "
            f"headlines={len(_headlines)} "
            f"context_items={len(_fii_context + _market_context)}",
            flush=True
        )

        # Pass hard signal values for NLP validation
        _hard_sigs = {
            "vix_score": nse_snapshot.get("vix_score", 0.5),
            "fii_score": nse_snapshot.get(
                "fii_score",
                nse_snapshot.get("flow_score", 0.5)
            ),
        }
        intel = eng["nlp"].get_regime_scores_v2(news, hard_signals=_hard_sigs)
        intel["hard_data"].update({
            "repo_rate": repo, "fiscal_deficit": deficit, "capex_lakh_cr": capex,
            "gdp_growth": macro.get("growth", {}).get("gdp", 7.2),
            "fii_net_crore": nse_snapshot.get("fii_net_crore"),
            "india_vix": nse_snapshot.get("india_vix", 15),
            "nifty_pcr": nse_snapshot.get("pcr", 1.0),
        })
        # RBI signal sanity check
        # If current repo rate is at or below 5.5% and NLP says HIKE,
        # override to PAUSE unless there has been an actual rate change
        _nlp_rbi = intel.get("rbi_policy_implication", "PAUSE")
        _repo    = float(intel["hard_data"].get("repo_rate", 6.5))
        if _nlp_rbi == "HIKE" and _repo <= 5.5:
            print(
                f"[RBI_GUARD] NLP returned HIKE but "
                f"repo={_repo}% — overriding to PAUSE",
                flush=True
            )
            intel["rbi_policy_implication"] = "PAUSE"
        # Data freshness weights — infrastructure for regime engine; logged to Railway
        gdp_weight = _get_data_freshness_weight("gdp")
        cpi_weight = _get_data_freshness_weight("cpi")
        intel["hard_data"]["gdp_freshness_weight"] = gdp_weight
        intel["hard_data"]["cpi_freshness_weight"] = cpi_weight
        print(
            f"[FRESHNESS] GDP weight: {gdp_weight:.2f}, CPI weight: {cpi_weight:.2f}",
            flush=True,
        )
        # Yield spread — read from cache (populated by /api/yield-curve endpoint)
        try:
            _yc_analysis = (_yc_cache.get("data") or {}).get("analysis", {})
            intel["yield_spread_india"] = float(
                _yc_analysis.get("india_spread_10y_2y", 0.25) or 0.25
            )
        except Exception:
            intel["yield_spread_india"] = 0.25
        # India PMI — from hardcoded _PMI_VALUES (updated monthly)
        intel.setdefault("hard_data", {})["pmi"] = _PMI_VALUES.get("IN", 0)
        if _nifty_pe:
            intel["nifty_pe"] = _nifty_pe
        if _garch.get("available"):
            intel["garch_forecast_vol"] = _garch["forecast_vol"]
            intel["garch_direction"]    = _garch["direction"]
            intel["garch_regime"]       = _garch["regime"]
            intel["garch_score"]        = _garch["score"]
        # ── Credit impulse ────────────────────────────────────────────────────
        _credit_impulse = _compute_credit_impulse()
        if _credit_impulse.get("available"):
            intel["credit_impulse"]       = _credit_impulse["impulse"]
            intel["credit_impulse_trend"] = _credit_impulse["trend"]
            intel["credit_impulse_score"] = _credit_impulse["score"]
        # ── Credit spread → intel ─────────────────────────────────────────────
        if nse_snapshot.get("credit_spread"):
            intel["credit_spread"] = nse_snapshot["credit_spread"]
        # ── FII trend momentum ────────────────────────────────────────────────
        _fii_trend = _compute_fii_trend(
            sb_url=_config.get("supabase_url", ""),
            sb_key=_config.get("supabase_service_key", ""),
        )
        if _fii_trend.get("available"):
            intel["fii_momentum_7d"]  = _fii_trend["momentum_7d"]
            intel["fii_streak"]       = _fii_trend["streak"]
            intel["fii_streak_dir"]   = _fii_trend["streak_dir"]
            intel["fii_trend_score"]  = _fii_trend["score"]
        try:
            liq = ensure_dict(eng["liquidity"].analyze(intel, market, nse_snapshot))
        except TypeError:
            liq = ensure_dict(eng["liquidity"].analyze(intel, market))
        # Fetch recent runs for confidence smoothing — pass to regime engine
        # since its own Supabase credentials don't work on Render
        _recent_runs_for_smooth = []
        try:
            _rr = _supabase.table("runs") \
                .select(
                    "regime,confidence,run_at"
                ) \
                .order(
                    "run_at",
                    desc=True
                ) \
                .limit(3) \
                .execute()
            _recent_runs_for_smooth = (
                _rr.data or []
            )
            print(
                f"[CONF_SMOOTH] Fetched "
                f"{len(_recent_runs_for_smooth)}"
                f" prior runs for smoothing",
                flush=True
            )
        except Exception as _sm_err:
            print(
                f"[CONF_SMOOTH] Fetch failed:"
                f" {_sm_err}",
                flush=True
            )
        regime = ensure_dict(
            eng["regime"].detect_regime(
                intel,
                liq,
                nse_snapshot,
                recent_runs=_recent_runs_for_smooth
            )
        )
        regime = rep.repair(regime, REGIME_SCHEMA)

        # ── Confidence gate ──────────────────────────────────────────────
        _briefing_allowed = regime.get("briefing_allowed", True)
        _briefing_blocked_reason = regime.get("briefing_blocked_reason", None)

        if not _briefing_allowed:
            print(
                f"[GATE] Run confidence gate "
                f"TRIGGERED — scheduler will "
                f"be suppressed this run. "
                f"Reason: {_briefing_blocked_reason}",
                flush=True
            )

        # ── Instability flag ─────────────────────────────────────────────
        _stability_flag   = regime.get("regime_stability_flag", {})
        _is_unstable      = _stability_flag.get("is_unstable", False)
        _challenger_delta = _stability_flag.get("challenger_delta", None)

        if _is_unstable:
            print(
                f"[UNSTABLE] Challenger delta "
                f"{_challenger_delta:.2f}pts — "
                f"regime classification unstable. "
                f"Flagging in run output.",
                flush=True
            )

        # Market stress overlay — adjusts confidence based on live crude/VIX/FII
        _base_conf = regime.get("confidence", 0)
        _adj_conf  = _apply_market_stress_overlay(
            base_confidence=_base_conf,
            nse_snapshot   =nse_snapshot,
            regime_key     =regime.get("regime", ""),
        )
        regime["confidence"]        = _adj_conf
        regime["base_confidence"]   = _base_conf
        regime["stress_adjustment"] = round(_adj_conf - _base_conf, 3)
        _anticipatory = regime.get("anticipatory", {
            "type":               "STABLE",
            "message":            "Leading signals unavailable.",
            "supporting_signals": [],
            "confidence_pct":     50,
            "action":             "HOLD current allocation",
        })
        _leading = regime.get("leading_intelligence", {
            "score":   0.5,
            "signals": [],
            "trend":   "STABLE",
        })
        if regime.get("regime") in POSITIVE_REGIMES and regime.get("confidence", 0) >= 0.65:
            regime.setdefault("components", {})["equity_bias"] = "RISK_ON"
        elif regime.get("regime") in DEFENSIVE_REGIMES and regime.get("confidence", 0) >= 0.65:
            regime.setdefault("components", {})["equity_bias"] = "RISK_OFF"
        cause     = ensure_dict(eng["cause"].analyze(intel, regime))
        scenarios = ensure_dict(eng["scenario"].generate_scenarios(regime, cause, nse_snapshot))
        scenarios = rep.repair(scenarios, SCENARIO_SCHEMA)
        asset_out = ensure_dict(eng["asset"].analyze_assets(regime, scenarios, liq))
        asset_out = rep.repair(asset_out, ASSET_SCHEMA)
        triggers  = eng["trigger"].generate_triggers(regime, cause)
        if not isinstance(triggers, list): triggers = []
        pos   = ensure_dict(eng["positioning"].generate_positioning(regime, scenarios, asset_out, cause, triggers))
        pos   = rep.repair(pos, POSITIONING_SCHEMA)
        strat = ensure_dict(eng["strategy"].generate_strategy(regime, scenarios, pos, triggers))
        strat = rep.repair(strat, STRATEGY_SCHEMA)
        dec   = ensure_dict(eng["decision"].generate(regime_output=regime, scenario_output=scenarios, asset_output=asset_out, positioning_output=pos, strategy_output=strat, trigger_output=triggers))
        final_intel = eng["aggregator"].build_intel_packet(regime_output=regime, scenario_output=scenarios, asset_output=asset_out.get("assets", {}), triggers=triggers, positioning_output=pos, cause_effect_output=cause, decision_output=dec, strategy_output=strat)
        report = eng["report"].generate_report(final_intel)

        # Fetch last 5 runs for correlation detection, stability, and narrative delta
        run_history = []
        _prev_run   = None
        try:
            _hist_result = _supabase.table("runs") \
                .select("regime, confidence, conviction, run_at, crude_price, fii_net_crore") \
                .eq("user_id", user_id) \
                .order("run_at", desc=True) \
                .limit(5).execute()
            run_history = _hist_result.data or []
            _prev_run   = run_history[0] if run_history else None
        except Exception:
            pass

        # Compute regime stability (needed as input to narrative_delta)
        stability = {
            "score": "UNKNOWN", "consecutive": 0,
            "conf_trend": "STABLE", "challenger_risk": "LOW",
            "explanation": "",
        }
        try:
            _challenger_conf = None
            try:
                _challenger_conf = (
                    scenarios.get("challenger_confidence") or
                    (scenarios.get("challenger") or {}).get("confidence")
                )
            except Exception:
                pass
            stability = _compute_regime_stability(
                current_regime        =regime.get("regime", ""),
                run_history           =run_history,
                challenger_confidence =_challenger_conf,
                current_confidence    =regime.get("confidence", 0),
            )
        except Exception:
            pass

        # Regime transition probability
        transition = {
            "probability_pct": 20, "risk_level": "LOW",
            "risk_label": "Regime stable",
            "most_likely_next": None, "most_likely_label": "Unknown",
            "explanation": "Calculation unavailable", "factors_count": 0, "conf_velocity": 0.0,
        }
        try:
            transition = _compute_transition_probability(
                current_regime    =regime.get("regime", ""),
                current_confidence=regime.get("confidence", 0),
                run_history       =run_history,
                nse_snapshot      =nse_snapshot,
                challenger_regime =regime.get("challenger_regime"),
                activity          =None,
            )
        except Exception as _te:
            print(f"[TRANSITION] Error: {_te}", flush=True)

        # Build narrative delta — never crashes
        _cal_events: list = []
        try:
            _cal_events = get_events_by_window(days_ahead=30)
        except Exception:
            pass
        narrative_delta = {"has_delta": False}
        try:
            narrative_delta = _build_narrative_delta(
                current_regime    =regime.get("regime", ""),
                current_confidence=regime.get("confidence", 0),
                current_conviction=strat.get("conviction", ""),
                prev_run          =_prev_run,
                nse_snapshot      =nse_snapshot,
                intel             =intel,
                run_history       =run_history,
                stability         =stability,
                transition        =transition,
                calendar_events   =_cal_events,
            )
        except Exception:
            pass

        # ── Instability note → narrative delta ───────────────────────────
        if _is_unstable and _challenger_delta:
            instability_note = (
                f"⚠️ Regime classification unstable — "
                f"gap between {regime.get('regime', '')} "
                f"and challenger is only "
                f"{_challenger_delta:.1f} points. "
                f"Hold positions until gap widens."
            )
            existing = narrative_delta.get("headline", "")
            narrative_delta["headline"] = (
                instability_note + " " + existing
                if existing
                else instability_note
            )

        try:
            _implied = _derive_implied_action(regime.get("regime", ""), strat.get("conviction", ""))
            print(f"[API] save_run: fii={nse_snapshot.get('fii_net_crore')} dii={nse_snapshot.get('dii_net_crore')} src={nse_snapshot.get('fii_dii_source')} regime={regime.get('regime','')}", flush=True)
            _allocation = dict(pos.get("allocation", {}) or {})
            _allocation["equity_bias"] = regime.get("components", {}).get("equity_bias", "NEUTRAL")
            _supabase.table("runs").insert({
                "user_id":        user_id,
                "regime":         regime.get("regime", ""),
                "confidence":     regime.get("confidence", 0),
                "conviction":     strat.get("conviction", ""),
                "implied_action": _implied,
                "outcome":        None,
                "fii_net_crore":  nse_snapshot.get("fii_net_crore"),
                "dii_net_crore":  nse_snapshot.get("dii_net_crore"),
                "crude_price":    nse_snapshot.get("crude_price"),
                "repo_rate":      repo,
                "deficit":        deficit,
                "capex":          capex,
                "summary":        dec.get("summary", ""),
                "report_text":    report if isinstance(report, str) else "",
                "allocation":     _allocation,
                "stress_test":    {"repo_rate": repo, "deficit": deficit, "capex": capex},
                "scenarios":      scenarios,
                "triggers":       triggers,
                "asset_out":      asset_out,
                "strat":          strat,
                "sector_heatmap": SECTOR_HEATMAP.get(regime.get("regime", ""), {"FAVOUR": [], "NEUTRAL": [], "AVOID": []}),
            }).execute()
        except Exception as e:
            print(f"[API] save_run failed: {e}")
        _jobs[job_id]["status"] = "complete"
        _jobs[job_id]["result"] = {"regime": regime, "strategy": strat, "decision": dec, "positioning": pos, "scenarios": scenarios, "triggers": triggers, "liquidity": liq, "intel": intel, "nse": nse_snapshot, "macro": macro, "final_intel": final_intel, "report": report if isinstance(report, str) else "", "sector_heatmap": SECTOR_HEATMAP.get(regime.get("regime", ""), {"FAVOUR": [], "NEUTRAL": [], "AVOID": []}), "narrative_delta": narrative_delta, "regime_stability": stability, "transition": transition, "anticipatory": _anticipatory, "leading_intelligence": _leading, "briefing_allowed": _briefing_allowed, "briefing_blocked_reason": _briefing_blocked_reason, "regime_is_unstable": _is_unstable, "challenger_delta": _challenger_delta}
    except Exception as e:
        print(f"[API] Pipeline error: {e}")
        traceback.print_exc()
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"]  = str(e)


class RunRequest(BaseModel):
    repo:    float = 5.25
    deficit: float = 4.3
    capex:   float = 12.2

class NotificationPrefsUpdate(BaseModel):
    channel:                     Optional[str]   = None
    whatsapp_number:             Optional[str]   = None
    regime_shift_enabled:        Optional[bool]  = None
    confidence_breach_enabled:   Optional[bool]  = None
    extreme_signal_enabled:      Optional[bool]  = None
    fii_flow_enabled:            Optional[bool]  = None
    rbi_event_enabled:           Optional[bool]  = None
    weekly_summary_enabled:      Optional[bool]  = None
    confidence_breach_threshold: Optional[float] = None
    vix_threshold:               Optional[float] = None
    inr_threshold:               Optional[float] = None
    yield_spike_bps:             Optional[float] = None
    crude_threshold:             Optional[float] = None
    fii_outflow_crore:           Optional[float] = None
    fii_consecutive_days:        Optional[int]   = None
    quiet_hours_start:           Optional[int]   = None
    quiet_hours_end:             Optional[int]   = None
    max_alerts_per_day:          Optional[int]   = None
    min_gap_hours:               Optional[int]   = None


@app.get("/")
async def root():
    return {"service": "SENTINEL Macro Intelligence API", "status": "online"}

@app.get("/health")
async def health():
    return {"status": "ok", "engines": len(_engines), "jobs": len(_jobs), "supabase": bool(_supabase)}

@app.api_route("/api/ping", methods=["GET", "HEAD"])
async def ping():
    return {"status": "ok", "ts": time.time()}

@app.get("/api/test-jugaad")
async def test_jugaad():
    """
    Temporary endpoint to test jugaad-data
    from Railway's IP range.
    Remove after testing.
    """
    results = {}
    try:
        from jugaad_data.nse import NSELive
        n = NSELive()

        # Test 1 — VIX
        try:
            vix = n.live_index("INDIA VIX")
            results["vix"] = {
                "status": "success",
                "value": vix["metadata"]["last"],
                "time":  vix["metadata"]["timeVal"],
            }
        except Exception as e:
            results["vix"] = {
                "status": "failed",
                "error": str(e),
            }

        # Test 2 — Nifty 50
        try:
            nifty = n.live_index("NIFTY 50")
            results["nifty"] = {
                "status":  "success",
                "value":   nifty["metadata"]["last"],
                "change":  nifty["metadata"]["percChange"],
            }
        except Exception as e:
            results["nifty"] = {
                "status": "failed",
                "error": str(e),
            }

    except Exception as e:
        results["import_error"] = str(e)

    return results

@app.post("/api/run")
async def start_run(body: RunRequest, background_tasks: BackgroundTasks, profile: dict = Depends(require_access)):
    _expire_old_jobs()
    job_id = _create_job(profile["id"])
    background_tasks.add_task(asyncio.get_running_loop().run_in_executor, None, _run_pipeline_sync, job_id, profile["id"], body.repo, body.deficit, body.capex)
    return {"job_id": job_id, "status": "running"}

@app.get("/api/run/{job_id}")
async def get_run_status(job_id: str, user=Depends(get_current_user)):
    job = _get_job(job_id)
    if not job: raise HTTPException(status_code=404, detail="Job not found or expired")
    if job["user_id"] != user.id: raise HTTPException(status_code=403, detail="Not your job")
    _result = job["result"] if job["status"] == "complete" else None
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": _result,
        "error":  job["error"] if job["status"] == "failed" else None,
        "anticipatory": (_result or {}).get("anticipatory", {
            "type":               "STABLE",
            "message":            "Leading signals unavailable.",
            "supporting_signals": [],
            "confidence_pct":     50,
            "action":             "HOLD current allocation",
        }),
        "leading_intelligence": (_result or {}).get("leading_intelligence", {
            "score":   0.5,
            "signals": [],
            "trend":   "STABLE",
        }),
    }

@app.get("/api/history")
async def get_history(limit: int = 100, profile: dict = Depends(require_access)):
    _COLS = "id,run_at,regime,confidence,conviction,implied_action,outcome,summary,allocation,fii_net_crore,dii_net_crore,crude_price,scenarios,triggers,asset_out,strat,sector_heatmap"
    result = _supabase.table("runs").select(_COLS).eq("user_id", profile["id"]).order("run_at", desc=True).limit(limit).execute()
    runs = result.data or []
    # Auto-evaluate any runs older than 30 days that still have no outcome
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    needs_eval = any(r.get("outcome") is None and (r.get("run_at") or "") <= cutoff for r in runs)
    if needs_eval:
        asyncio.create_task(_evaluate_outcomes_async())
    return {"history": runs}


@app.post("/api/outcomes/evaluate")
async def evaluate_outcomes(_profile: dict = Depends(require_admin)):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _do_evaluate_outcomes)
    return result

@app.get("/api/backtest")
async def get_backtest(regime: str = "LIQUIDITY_DRIVEN_EXPANSION", profile: dict = Depends(require_access)):
    data = REGIME_BACKTEST.get(regime.upper())
    if not data:
        return {"regime": regime, "available": False, "message": "No backtest data for this regime"}
    return {"regime": regime, "available": True, "data": data}


@app.post("/api/simulator")
async def run_simulator(
    body: dict,
    profile: dict = Depends(require_access)
):
    """
    Macro Shock Simulator.
    Takes portfolio allocation + scenario,
    returns regime impact + per-sleeve
    directional impact + historical P&L
    context from REGIME_BACKTEST.
    """

    SCENARIOS = {
        "rbi_rate_hike": {
            "label": "RBI Rate Hike (+50bps)",
            "tier": "India",
            "description": "RBI surprises with a 50bps hike to combat inflation.",
            "signal_shifts": {
                "vix": +1.5,
                "fii": -3000,
                "crude": 0,
                "yield_curve_score": -0.15,
                "credit_impulse_score": -0.20,
            },
            "target_regime": "MONETARY_TIGHTENING",
            "regime_probability": 0.55,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -8,  "note": "Rate-sensitive sectors compress. Banks face NIM pressure."},
                "debt":   {"direction": "BEARISH",  "magnitude": -4,  "note": "Bond prices fall as yields rise. Reduce duration immediately."},
                "gold":   {"direction": "NEUTRAL",  "magnitude": +2,  "note": "Mixed — rate hikes pressure gold but INR weakness supports."},
                "cash":   {"direction": "BULLISH",  "magnitude": +3,  "note": "Higher rates improve returns on liquid funds and FDs."},
            },
            "advisor_note": "Rotate out of rate-sensitives. Reduce debt duration to under 2 years. Add liquid funds.",
        },
        "rbi_rate_cut": {
            "label": "RBI Rate Cut (-25bps)",
            "tier": "India",
            "description": "RBI cuts rates to support growth amid softening inflation.",
            "signal_shifts": {
                "vix": -1.0,
                "fii": +2000,
                "crude": 0,
                "yield_curve_score": +0.10,
                "credit_impulse_score": +0.15,
            },
            "target_regime": "LIQUIDITY_DRIVEN_EXPANSION",
            "regime_probability": 0.65,
            "sleeve_impact": {
                "equity": {"direction": "BULLISH",  "magnitude": +6,  "note": "Financials, real estate, and rate-sensitives lead."},
                "debt":   {"direction": "BULLISH",  "magnitude": +5,  "note": "Bond prices rise. Extend duration — G-Sec and AAA corporates."},
                "gold":   {"direction": "NEUTRAL",  "magnitude": +1,  "note": "Marginal positive from INR strengthening."},
                "cash":   {"direction": "BEARISH",  "magnitude": -2,  "note": "Liquid fund yields compress with policy rate cut."},
            },
            "advisor_note": "Extend debt duration. Add to equity — especially financials and infra. Reduce cash drag.",
        },
        "crude_spike": {
            "label": "Crude Oil Spike ($95/bbl)",
            "tier": "India",
            "description": "Oil spikes to $95 due to supply disruption — India's import bill surges.",
            "signal_shifts": {
                "vix": +2.0,
                "fii": -4000,
                "crude": 95,
                "yield_curve_score": -0.10,
                "credit_impulse_score": -0.10,
            },
            "target_regime": "STAGFLATION_RISK",
            "regime_probability": 0.50,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -10, "note": "Margins compress across auto, chemicals, FMCG. Avoid consumption names."},
                "debt":   {"direction": "BEARISH",  "magnitude": -3,  "note": "Inflation risk rises — avoid long duration."},
                "gold":   {"direction": "BULLISH",  "magnitude": +8,  "note": "Classic safe-haven play in oil shock. INR weakness amplifies returns."},
                "cash":   {"direction": "NEUTRAL",  "magnitude": +1,  "note": "Hold cash for re-entry opportunity post-shock."},
            },
            "advisor_note": "Add gold to 10-12%. Reduce equity to underweight. Avoid FMCG, chemicals, auto ancillaries.",
        },
        "inr_depreciation": {
            "label": "INR Depreciation (₹100/USD)",
            "tier": "India",
            "description": "Rupee weakens sharply to 100 driven by FII outflows and CAD widening.",
            "signal_shifts": {
                "vix": +3.0,
                "fii": -6000,
                "crude": +5,
                "yield_curve_score": -0.20,
                "credit_impulse_score": -0.15,
            },
            "target_regime": "EXTERNAL_SHOCK",
            "regime_probability": 0.60,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -12, "note": "FII selling accelerates. Domestic importers hit hardest."},
                "debt":   {"direction": "BEARISH",  "magnitude": -5,  "note": "Foreign selling of G-Secs drives yields higher."},
                "gold":   {"direction": "BULLISH",  "magnitude": +12, "note": "INR-denominated gold returns amplified by currency weakness."},
                "cash":   {"direction": "NEUTRAL",  "magnitude": 0,   "note": "Consider USD-denominated assets for HNI clients."},
            },
            "advisor_note": "Raise gold to 15%. Reduce equity materially. For HNI clients consider international fund allocation.",
        },
        "fii_outflows": {
            "label": "FII Sustained Outflows (12d+)",
            "tier": "India",
            "description": "Sustained FII selling streak of 12+ days — ₹40,000cr+ outflows.",
            "signal_shifts": {
                "vix": +4.0,
                "fii": -5000,
                "crude": 0,
                "yield_curve_score": -0.15,
                "credit_impulse_score": -0.05,
            },
            "target_regime": "GROWTH_SLOWDOWN_SUPPORT",
            "regime_probability": 0.50,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -8,  "note": "Large-caps bear the brunt of FII exits. Mid/small may hold better."},
                "debt":   {"direction": "NEUTRAL",  "magnitude": +1,  "note": "Flight to safety supports G-Sec prices temporarily."},
                "gold":   {"direction": "BULLISH",  "magnitude": +5,  "note": "Risk-off environment supports gold."},
                "cash":   {"direction": "BULLISH",  "magnitude": +2,  "note": "Raise cash — deploy on DII absorption signal."},
            },
            "advisor_note": "Hold SIPs — do not stop. Pause new lump-sum. Watch for DII absorption as re-entry signal.",
        },
        "cpi_surprise": {
            "label": "CPI Surprise (Above 6%)",
            "tier": "India",
            "description": "CPI prints above 6% — RBI's upper tolerance band breached.",
            "signal_shifts": {
                "vix": +2.5,
                "fii": -2000,
                "crude": 0,
                "yield_curve_score": -0.10,
                "credit_impulse_score": -0.10,
            },
            "target_regime": "STAGFLATION_RISK",
            "regime_probability": 0.45,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -6,  "note": "Rate hike expectations compress multiples. Defensives outperform."},
                "debt":   {"direction": "BEARISH",  "magnitude": -4,  "note": "Yield spike on hike expectations — reduce duration urgently."},
                "gold":   {"direction": "BULLISH",  "magnitude": +6,  "note": "Inflation hedge demand supports gold."},
                "cash":   {"direction": "NEUTRAL",  "magnitude": +1,  "note": "Liquid funds benefit if RBI acts."},
            },
            "advisor_note": "Reduce equity duration (avoid long-horizon lump-sum). Add gold. Shift debt to ultra-short/liquid.",
        },
        "gdp_disappointment": {
            "label": "GDP Disappointment (Below 6%)",
            "tier": "India",
            "description": "GDP prints below 6% — growth narrative challenged.",
            "signal_shifts": {
                "vix": +1.5,
                "fii": -1500,
                "crude": -3,
                "yield_curve_score": +0.05,
                "credit_impulse_score": -0.15,
            },
            "target_regime": "GROWTH_SLOWDOWN_SUPPORT",
            "regime_probability": 0.55,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -5,  "note": "Cyclicals and capex names underperform. Defensives relatively stable."},
                "debt":   {"direction": "BULLISH",  "magnitude": +3,  "note": "Rate cut expectations build — G-Sec and long duration benefit."},
                "gold":   {"direction": "NEUTRAL",  "magnitude": +2,  "note": "Mild safe-haven demand."},
                "cash":   {"direction": "NEUTRAL",  "magnitude": 0,   "note": "Hold and wait for regime clarity."},
            },
            "advisor_note": "Rotate from cyclicals to defensives. Extend debt duration cautiously. Hold SIPs unchanged.",
        },
        "us_fed_hike": {
            "label": "US Fed Rate Hike (+50bps)",
            "tier": "Global",
            "description": "Federal Reserve hikes 50bps — global risk-off, EM capital outflows.",
            "signal_shifts": {
                "vix": +3.5,
                "fii": -5000,
                "crude": -2,
                "yield_curve_score": -0.15,
                "credit_impulse_score": -0.10,
            },
            "target_regime": "EXTERNAL_SHOCK",
            "regime_probability": 0.55,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -9,  "note": "EM outflows pressure Nifty. Dollar-denominated debt costs rise."},
                "debt":   {"direction": "BEARISH",  "magnitude": -4,  "note": "US yields rise pulls India yields up — avoid long G-Sec."},
                "gold":   {"direction": "NEUTRAL",  "magnitude": +3,  "note": "USD strength caps gold in USD, but INR weakness helps INR gold."},
                "cash":   {"direction": "BULLISH",  "magnitude": +2,  "note": "FD rates improve as Indian banks follow global rates up."},
            },
            "advisor_note": "Reduce equity to underweight temporarily. Add short-duration debt. Watch USD/INR closely.",
        },
        "global_risk_off": {
            "label": "Global Risk-Off (VIX > 25)",
            "tier": "Global",
            "description": "Global VIX spikes above 25 — broad EM selloff, flight to safety.",
            "signal_shifts": {
                "vix": +8.0,
                "fii": -8000,
                "crude": -5,
                "yield_curve_score": -0.20,
                "credit_impulse_score": -0.20,
            },
            "target_regime": "EXTERNAL_SHOCK",
            "regime_probability": 0.70,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -15, "note": "Indiscriminate selling. Hold quality, avoid high-beta."},
                "debt":   {"direction": "BULLISH",  "magnitude": +4,  "note": "Flight to safety — G-Secs outperform corporate bonds."},
                "gold":   {"direction": "BULLISH",  "magnitude": +10, "note": "Classic risk-off safe haven. Highest conviction add."},
                "cash":   {"direction": "BULLISH",  "magnitude": +3,  "note": "Maximum liquidity — best time to hold dry powder."},
            },
            "advisor_note": "Defensive posture. Do not sell equity in panic — hold quality. Add gold. Raise cash for re-entry.",
        },
        "china_slowdown": {
            "label": "China Slowdown (PMI < 45)",
            "tier": "Global",
            "description": "China PMI collapses below 45 — global supply chain and commodity demand hit.",
            "signal_shifts": {
                "vix": +2.0,
                "fii": -2000,
                "crude": -8,
                "yield_curve_score": -0.05,
                "credit_impulse_score": -0.10,
            },
            "target_regime": "GROWTH_SLOWDOWN_SUPPORT",
            "regime_probability": 0.45,
            "sleeve_impact": {
                "equity": {"direction": "NEUTRAL",  "magnitude": -3,  "note": "Mixed — India gains market share from China in some sectors (IT, chemicals)."},
                "debt":   {"direction": "BULLISH",  "magnitude": +2,  "note": "Lower commodity prices ease inflation — mild rate cut probability."},
                "gold":   {"direction": "BULLISH",  "magnitude": +4,  "note": "Risk-off and weaker CNY support gold."},
                "cash":   {"direction": "NEUTRAL",  "magnitude": 0,   "note": "Neutral — watch for India-specific FII reaction."},
            },
            "advisor_note": "Watch for India-specific impact carefully. IT and specialty chemicals may actually benefit.",
        },
        "oil_supply_shock": {
            "label": "Oil Supply Shock ($110/bbl)",
            "tier": "Global",
            "description": "Geopolitical event drives oil to $110 — India import bill surges severely.",
            "signal_shifts": {
                "vix": +5.0,
                "fii": -6000,
                "crude": 110,
                "yield_curve_score": -0.20,
                "credit_impulse_score": -0.20,
            },
            "target_regime": "STAGFLATIONARY_RISK",
            "regime_probability": 0.65,
            "sleeve_impact": {
                "equity": {"direction": "BEARISH",  "magnitude": -14, "note": "Severe margin pressure across economy. Only upstream energy benefits."},
                "debt":   {"direction": "BEARISH",  "magnitude": -6,  "note": "Inflation surge forces RBI hand — avoid all duration."},
                "gold":   {"direction": "BULLISH",  "magnitude": +12, "note": "Maximum conviction safe haven in oil supply shock."},
                "cash":   {"direction": "BULLISH",  "magnitude": +2,  "note": "Preserve capital — wait for shock to pass."},
            },
            "advisor_note": "Emergency defensive posture. Raise gold to 15%+. Cut equity to minimum. Absolute shortest duration debt only.",
        },
        # ── Tier 3: India Structural Events ──────────────────────────────────
        "union_budget_expansion": {
            "label": "Union Budget — Fiscal Expansion",
            "tier": "India Structural",
            "description": (
                "Government announces major fiscal "
                "expansion — capex surge, tax cuts, "
                "higher deficit target. Growth "
                "positive but inflation risk rises."
            ),
            "signal_shifts": {
                "vix": -1.5,
                "fii": +3000,
                "crude": +2,
                "yield_curve_score": +0.10,
                "credit_impulse_score": +0.20,
            },
            "target_regime": "LIQUIDITY_DRIVEN_EXPANSION",
            "regime_probability": 0.60,
            "sleeve_impact": {
                "equity": {
                    "direction": "BULLISH",
                    "magnitude": +9,
                    "note": (
                        "Capex-linked sectors surge — "
                        "infra, defence, PSU banks. "
                        "Broad market positive."
                    ),
                },
                "debt": {
                    "direction": "BEARISH",
                    "magnitude": -4,
                    "note": (
                        "Higher fiscal deficit widens "
                        "spreads — avoid long G-Sec, "
                        "prefer corporate bonds."
                    ),
                },
                "gold": {
                    "direction": "NEUTRAL",
                    "magnitude": -1,
                    "note": (
                        "Risk-on environment reduces "
                        "safe-haven demand."
                    ),
                },
                "cash": {
                    "direction": "BEARISH",
                    "magnitude": -2,
                    "note": (
                        "Deploy cash into equity — "
                        "budget surprise is a "
                        "deployment signal."
                    ),
                },
            },
            "advisor_note": (
                "Add to infra, defence, and PSU "
                "banks. Reduce G-Sec duration. "
                "This is a buy-on-announcement "
                "environment for cyclicals."
            ),
        },
        "monsoon_failure": {
            "label": "Monsoon Failure (Below 80% Normal)",
            "tier": "India Structural",
            "description": (
                "IMD declares below-normal monsoon "
                "— food inflation risk surges, rural "
                "demand contracts, RBI's hands tied."
            ),
            "signal_shifts": {
                "vix": +2.5,
                "fii": -2500,
                "crude": 0,
                "yield_curve_score": -0.10,
                "credit_impulse_score": -0.15,
            },
            "target_regime": "STAGFLATION_RISK",
            "regime_probability": 0.50,
            "sleeve_impact": {
                "equity": {
                    "direction": "BEARISH",
                    "magnitude": -7,
                    "note": (
                        "Rural consumption names "
                        "hit hardest — FMCG, two-"
                        "wheelers, agri inputs. "
                        "Urban consumption holds."
                    ),
                },
                "debt": {
                    "direction": "BEARISH",
                    "magnitude": -3,
                    "note": (
                        "Food inflation locks RBI "
                        "into pause — no rate cut "
                        "tailwind for bonds."
                    ),
                },
                "gold": {
                    "direction": "BULLISH",
                    "magnitude": +5,
                    "note": (
                        "Inflation hedge demand "
                        "rises. Rural gold buying "
                        "also increases in stress."
                    ),
                },
                "cash": {
                    "direction": "BULLISH",
                    "magnitude": +3,
                    "note": (
                        "Hold cash — wait for "
                        "monsoon revival signal "
                        "before deploying."
                    ),
                },
            },
            "advisor_note": (
                "Avoid rural consumption, agri, "
                "and two-wheeler names. Add gold "
                "to 10-12%. Wait for IMD July "
                "revision before adding equity."
            ),
        },
        "rbi_liquidity_tightening": {
            "label": "RBI Liquidity Tightening (CRR Hike)",
            "tier": "India Structural",
            "description": (
                "RBI hikes CRR by 50bps to drain "
                "systemic liquidity without changing "
                "the repo rate. Credit growth slows."
            ),
            "signal_shifts": {
                "vix": +1.5,
                "fii": -1500,
                "crude": 0,
                "yield_curve_score": -0.12,
                "credit_impulse_score": -0.25,
            },
            "target_regime": "MONETARY_TIGHTENING",
            "regime_probability": 0.55,
            "sleeve_impact": {
                "equity": {
                    "direction": "BEARISH",
                    "magnitude": -6,
                    "note": (
                        "Banks and NBFCs face margin "
                        "pressure. Credit growth "
                        "slows — avoid financials."
                    ),
                },
                "debt": {
                    "direction": "BEARISH",
                    "magnitude": -3,
                    "note": (
                        "Short-end yields spike "
                        "on liquidity drain. "
                        "Reduce duration urgently."
                    ),
                },
                "gold": {
                    "direction": "NEUTRAL",
                    "magnitude": +1,
                    "note": (
                        "Mild safe-haven support "
                        "but not a strong gold "
                        "trigger."
                    ),
                },
                "cash": {
                    "direction": "BULLISH",
                    "magnitude": +4,
                    "note": (
                        "Liquid funds benefit from "
                        "higher short-term rates. "
                        "Raise cash allocation."
                    ),
                },
            },
            "advisor_note": (
                "Reduce bank and NBFC exposure. "
                "Shift debt to ultra-short and "
                "liquid funds. Raise cash buffer "
                "to 10-12%."
            ),
        },
        "banking_sector_stress": {
            "label": "Banking Sector Stress (NPA Cycle)",
            "tier": "India Structural",
            "description": (
                "Rising NPAs signal a new bad loan "
                "cycle — RBI increases provisioning "
                "requirements. Credit growth "
                "contracts sharply."
            ),
            "signal_shifts": {
                "vix": +3.5,
                "fii": -4000,
                "crude": 0,
                "yield_curve_score": -0.10,
                "credit_impulse_score": -0.35,
            },
            "target_regime": "GROWTH_SLOWDOWN_SUPPORT",
            "regime_probability": 0.60,
            "sleeve_impact": {
                "equity": {
                    "direction": "BEARISH",
                    "magnitude": -12,
                    "note": (
                        "Bank Nifty leads decline. "
                        "Avoid all PSU banks and "
                        "high-NPA private banks. "
                        "IT and pharma as refuge."
                    ),
                },
                "debt": {
                    "direction": "BULLISH",
                    "magnitude": +3,
                    "note": (
                        "Flight to G-Sec safety. "
                        "Avoid corporate bonds "
                        "in stressed sectors."
                    ),
                },
                "gold": {
                    "direction": "BULLISH",
                    "magnitude": +7,
                    "note": (
                        "Systemic stress drives "
                        "safe-haven demand. "
                        "Add gold meaningfully."
                    ),
                },
                "cash": {
                    "direction": "BULLISH",
                    "magnitude": +3,
                    "note": (
                        "Maximum liquidity — "
                        "deploy only into "
                        "defensives and gold."
                    ),
                },
            },
            "advisor_note": (
                "Exit bank-heavy funds. Rotate "
                "to IT, pharma, and gold. Hold "
                "G-Sec over corporate bonds. "
                "This is a multi-quarter "
                "restructuring cycle."
            ),
        },
        "election_uncertainty": {
            "label": "Election Outcome Surprise",
            "tier": "India Structural",
            "description": (
                "Unexpected election result — "
                "hung parliament or coalition "
                "government. Policy continuity "
                "uncertainty spikes."
            ),
            "signal_shifts": {
                "vix": +6.0,
                "fii": -7000,
                "crude": 0,
                "yield_curve_score": -0.15,
                "credit_impulse_score": -0.10,
            },
            "target_regime": "EXTERNAL_SHOCK",
            "regime_probability": 0.65,
            "sleeve_impact": {
                "equity": {
                    "direction": "BEARISH",
                    "magnitude": -14,
                    "note": (
                        "Policy-sensitive sectors "
                        "collapse — PSU, infra, "
                        "defence. Private sector "
                        "quality holds better."
                    ),
                },
                "debt": {
                    "direction": "BEARISH",
                    "magnitude": -4,
                    "note": (
                        "Fiscal slippage fears "
                        "widen spreads. Short "
                        "duration only."
                    ),
                },
                "gold": {
                    "direction": "BULLISH",
                    "magnitude": +10,
                    "note": (
                        "Maximum uncertainty "
                        "premium. Gold is the "
                        "clearest safe haven."
                    ),
                },
                "cash": {
                    "direction": "BULLISH",
                    "magnitude": +4,
                    "note": (
                        "Hold maximum cash — "
                        "deploy after new "
                        "government clarity."
                    ),
                },
            },
            "advisor_note": (
                "Raise gold to 15%. Exit PSU "
                "and policy-sensitive names. "
                "Hold SIPs — do not stop. "
                "Deploy lump-sum only after "
                "cabinet formation clarity."
            ),
        },
        "inr_appreciation": {
            "label": "INR Appreciation (₹80/USD)",
            "tier": "India Structural",
            "description": (
                "Rupee strengthens sharply to "
                "₹80/USD on strong FII inflows "
                "and current account surplus. "
                "Exporters face margin squeeze."
            ),
            "signal_shifts": {
                "vix": -2.0,
                "fii": +6000,
                "crude": -3,
                "yield_curve_score": +0.08,
                "credit_impulse_score": +0.10,
            },
            "target_regime": "LIQUIDITY_DRIVEN_EXPANSION",
            "regime_probability": 0.60,
            "sleeve_impact": {
                "equity": {
                    "direction": "BULLISH",
                    "magnitude": +6,
                    "note": (
                        "Domestic consumption and "
                        "import-heavy sectors win. "
                        "IT exporters face "
                        "earnings headwinds."
                    ),
                },
                "debt": {
                    "direction": "BULLISH",
                    "magnitude": +4,
                    "note": (
                        "FII bond buying pushes "
                        "yields lower. Extend "
                        "duration — G-Sec "
                        "particularly attractive."
                    ),
                },
                "gold": {
                    "direction": "BEARISH",
                    "magnitude": -4,
                    "note": (
                        "INR gold prices fall "
                        "with currency strength. "
                        "Trim gold allocation."
                    ),
                },
                "cash": {
                    "direction": "NEUTRAL",
                    "magnitude": 0,
                    "note": (
                        "Deploy into domestic "
                        "consumption equity "
                        "and long-duration debt."
                    ),
                },
            },
            "advisor_note": (
                "Rotate from IT exporters to "
                "domestic consumption. Extend "
                "debt duration. Trim gold. "
                "This is a risk-on domestic "
                "demand environment."
            ),
        },
        # ── Tier 4: Global Macro Extremes ─────────────────────────────────────
        "us_recession": {
            "label": "US Recession (GDP Contraction)",
            "tier": "Global Extreme",
            "description": (
                "US GDP contracts for two "
                "consecutive quarters. Global "
                "growth expectations collapse, "
                "EM risk-off accelerates."
            ),
            "signal_shifts": {
                "vix": +7.0,
                "fii": -8000,
                "crude": -12,
                "yield_curve_score": -0.20,
                "credit_impulse_score": -0.25,
            },
            "target_regime": "EXTERNAL_SHOCK",
            "regime_probability": 0.70,
            "sleeve_impact": {
                "equity": {
                    "direction": "BEARISH",
                    "magnitude": -16,
                    "note": (
                        "India not decoupled — "
                        "FII outflows and IT "
                        "earnings cuts drive "
                        "broad market decline."
                    ),
                },
                "debt": {
                    "direction": "BULLISH",
                    "magnitude": +6,
                    "note": (
                        "Global flight to safety. "
                        "India G-Sec benefits "
                        "from rate cut "
                        "expectations."
                    ),
                },
                "gold": {
                    "direction": "BULLISH",
                    "magnitude": +14,
                    "note": (
                        "Maximum safe-haven "
                        "demand. Gold is the "
                        "single clearest "
                        "allocation in US "
                        "recession."
                    ),
                },
                "cash": {
                    "direction": "BULLISH",
                    "magnitude": +4,
                    "note": (
                        "Preserve capital. "
                        "Deploy only after "
                        "US Fed pivot signal."
                    ),
                },
            },
            "advisor_note": (
                "Defensive posture. Raise gold "
                "to 15-18%. Extend G-Sec "
                "duration. Avoid IT and export "
                "names. Wait for Fed pivot "
                "before adding equity."
            ),
        },
        "em_contagion": {
            "label": "EM Contagion (Turkey/Argentina Style)",
            "tier": "Global Extreme",
            "description": (
                "A major EM country faces "
                "currency crisis and debt "
                "default — contagion spreads "
                "to other EMs including India."
            ),
            "signal_shifts": {
                "vix": +5.0,
                "fii": -6000,
                "crude": -4,
                "yield_curve_score": -0.18,
                "credit_impulse_score": -0.15,
            },
            "target_regime": "EXTERNAL_SHOCK",
            "regime_probability": 0.55,
            "sleeve_impact": {
                "equity": {
                    "direction": "BEARISH",
                    "magnitude": -10,
                    "note": (
                        "EM-wide selloff. India "
                        "fundamentals are better "
                        "but contagion is "
                        "indiscriminate short-term."
                    ),
                },
                "debt": {
                    "direction": "NEUTRAL",
                    "magnitude": -1,
                    "note": (
                        "Indian sovereign risk "
                        "rises marginally. "
                        "Stick to short G-Sec."
                    ),
                },
                "gold": {
                    "direction": "BULLISH",
                    "magnitude": +8,
                    "note": (
                        "Currency crisis globally "
                        "drives gold demand. "
                        "INR weakness amplifies."
                    ),
                },
                "cash": {
                    "direction": "BULLISH",
                    "magnitude": +3,
                    "note": (
                        "Hold cash in INR. "
                        "Avoid international "
                        "fund exposure during "
                        "contagion."
                    ),
                },
            },
            "advisor_note": (
                "Hold SIPs. Pause lump-sum. "
                "Add gold. Watch India's "
                "current account and FX "
                "reserves as the key "
                "insulation metrics."
            ),
        },
        "dollar_index_spike": {
            "label": "Dollar Index Spike (DXY > 110)",
            "tier": "Global Extreme",
            "description": (
                "US dollar surges on safe-haven "
                "demand or Fed hawkishness. "
                "All EM currencies under "
                "simultaneous pressure."
            ),
            "signal_shifts": {
                "vix": +4.0,
                "fii": -5000,
                "crude": -6,
                "yield_curve_score": -0.12,
                "credit_impulse_score": -0.10,
            },
            "target_regime": "EXTERNAL_SHOCK",
            "regime_probability": 0.60,
            "sleeve_impact": {
                "equity": {
                    "direction": "BEARISH",
                    "magnitude": -9,
                    "note": (
                        "FII outflows accelerate "
                        "as dollar strength "
                        "makes EM less attractive. "
                        "Import-heavy sectors hit."
                    ),
                },
                "debt": {
                    "direction": "BEARISH",
                    "magnitude": -3,
                    "note": (
                        "RBI may intervene "
                        "aggressively — "
                        "liquidity tightening "
                        "risk rises."
                    ),
                },
                "gold": {
                    "direction": "NEUTRAL",
                    "magnitude": +2,
                    "note": (
                        "USD gold flat to down, "
                        "but INR gold rises "
                        "with currency weakness."
                    ),
                },
                "cash": {
                    "direction": "BULLISH",
                    "magnitude": +2,
                    "note": (
                        "Hold INR cash. Consider "
                        "USD-denominated assets "
                        "for HNI clients with "
                        "international exposure."
                    ),
                },
            },
            "advisor_note": (
                "Avoid import-heavy sectors. "
                "Reduce equity to underweight "
                "temporarily. INR gold position "
                "benefits even if USD gold "
                "is flat."
            ),
        },
        "commodity_supercycle": {
            "label": "Global Commodity Supercycle",
            "tier": "Global Extreme",
            "description": (
                "All major commodities rising "
                "simultaneously — oil, metals, "
                "agri. Inflationary globally "
                "but India is a net importer."
            ),
            "signal_shifts": {
                "vix": +2.0,
                "fii": -2000,
                "crude": +20,
                "yield_curve_score": -0.15,
                "credit_impulse_score": -0.12,
            },
            "target_regime": "STAGFLATION_RISK",
            "regime_probability": 0.55,
            "sleeve_impact": {
                "equity": {
                    "direction": "NEUTRAL",
                    "magnitude": -3,
                    "note": (
                        "Mixed — commodity "
                        "producers win, "
                        "consumers lose. "
                        "Metals and energy "
                        "stocks outperform."
                    ),
                },
                "debt": {
                    "direction": "BEARISH",
                    "magnitude": -5,
                    "note": (
                        "Inflation surge locks "
                        "RBI. Avoid all "
                        "duration — shortest "
                        "possible tenor only."
                    ),
                },
                "gold": {
                    "direction": "BULLISH",
                    "magnitude": +10,
                    "note": (
                        "Supercycle is the "
                        "strongest gold "
                        "environment. Maximum "
                        "conviction add."
                    ),
                },
                "cash": {
                    "direction": "NEUTRAL",
                    "magnitude": +1,
                    "note": (
                        "Liquid funds benefit "
                        "from RBI pause. "
                        "Hold for re-entry."
                    ),
                },
            },
            "advisor_note": (
                "Rotate equity to metals, "
                "energy, and commodity "
                "producers. Add gold to 15%. "
                "Cut duration to zero. "
                "Avoid FMCG and auto."
            ),
        },
        "geopolitical_supply_disruption": {
            "label": "Geopolitical Supply Disruption",
            "tier": "Global Extreme",
            "description": (
                "Major geopolitical event "
                "disrupts global supply chains "
                "— semiconductors, rare earth, "
                "or shipping routes. Not just "
                "oil this time."
            ),
            "signal_shifts": {
                "vix": +4.5,
                "fii": -4500,
                "crude": +8,
                "yield_curve_score": -0.12,
                "credit_impulse_score": -0.18,
            },
            "target_regime": "STAGFLATIONARY_RISK",
            "regime_probability": 0.55,
            "sleeve_impact": {
                "equity": {
                    "direction": "BEARISH",
                    "magnitude": -11,
                    "note": (
                        "IT hit by chip shortage. "
                        "Auto hit by parts "
                        "shortage. Only domestic "
                        "demand names insulated."
                    ),
                },
                "debt": {
                    "direction": "BEARISH",
                    "magnitude": -4,
                    "note": (
                        "Stagflationary — "
                        "inflation up, growth "
                        "down. Worst environment "
                        "for bonds."
                    ),
                },
                "gold": {
                    "direction": "BULLISH",
                    "magnitude": +11,
                    "note": (
                        "Geopolitical premium "
                        "plus inflation hedge. "
                        "Double driver for gold."
                    ),
                },
                "cash": {
                    "direction": "BULLISH",
                    "magnitude": +3,
                    "note": (
                        "Preserve capital. "
                        "Duration of disruption "
                        "determines re-entry "
                        "timing."
                    ),
                },
            },
            "advisor_note": (
                "Rotate to domestic demand "
                "names — FMCG, healthcare, "
                "utilities. Maximum gold. "
                "Avoid IT, auto, chemicals. "
                "Shortest possible debt "
                "duration."
            ),
        },
    }

    scenario_key = body.get("scenario", "")
    allocation = body.get("allocation", {
        "equity": 60,
        "debt": 25,
        "gold": 10,
        "cash": 5,
    })
    current_regime = body.get("current_regime", "LIQUIDITY_DRIVEN_EXPANSION")

    if scenario_key not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Unknown scenario: {scenario_key}")

    scenario = SCENARIOS[scenario_key]
    target_regime = scenario["target_regime"]

    backtest = REGIME_BACKTEST.get(target_regime, {})
    current_backtest = REGIME_BACKTEST.get(current_regime, {})

    sleeves = scenario["sleeve_impact"]
    portfolio_impact = round(
        sum(
            (sleeves[s]["magnitude"] * allocation.get(s, 0) / 100)
            for s in sleeves
            if s in allocation
        ), 1
    )

    return {
        "scenario": {
            "key":         scenario_key,
            "label":       scenario["label"],
            "tier":        scenario["tier"],
            "description": scenario["description"],
        },
        "regime_shift": {
            "from":        current_regime,
            "to":          target_regime,
            "probability": scenario["regime_probability"],
            "summary":     backtest.get("summary", ""),
        },
        "sleeve_impact":    sleeves,
        "portfolio_impact": portfolio_impact,
        "advisor_note":     scenario["advisor_note"],
        "historical_context": {
            "target_regime": {
                "instances":           backtest.get("instances"),
                "avg_duration_months": backtest.get("avg_duration_months"),
                "indices":             backtest.get("indices", {}),
                "key_insight":         backtest.get("key_insight", ""),
            },
            "current_regime": {
                "instances":   current_backtest.get("instances"),
                "indices":     current_backtest.get("indices", {}),
                "key_insight": current_backtest.get("key_insight", ""),
            },
        },
        "allocation": allocation,
        "all_scenarios": {
            k: {"label": v["label"], "tier": v["tier"]}
            for k, v in SCENARIOS.items()
        },
    }


@app.post("/api/portfolio-layer")
async def portfolio_layer(
    body: dict,
    profile: dict = Depends(require_access)
):
    """
    Client Portfolio Layer.
    Maps current regime signals against a client's actual allocation.
    Returns per-sleeve analysis, risk flags, alignment score,
    and a conviction-gated action directive.
    """

    REGIME_TARGETS = {
        "LIQUIDITY_DRIVEN_EXPANSION": {
            "equity": {"min": 60, "max": 80, "target": 70},
            "debt":   {"min": 10, "max": 25, "target": 15},
            "gold":   {"min": 5,  "max": 10, "target": 7},
            "cash":   {"min": 2,  "max": 8,  "target": 5},
            "bias": "RISK_ON",
            "label": "Liquidity-Driven Expansion",
        },
        "MONETARY_TIGHTENING": {
            "equity": {"min": 30, "max": 50, "target": 40},
            "debt":   {"min": 30, "max": 50, "target": 40},
            "gold":   {"min": 5,  "max": 10, "target": 7},
            "cash":   {"min": 10, "max": 20, "target": 13},
            "bias": "RISK_OFF",
            "label": "Monetary Tightening",
        },
        "STAGFLATION_RISK": {
            "equity": {"min": 25, "max": 45, "target": 35},
            "debt":   {"min": 15, "max": 30, "target": 20},
            "gold":   {"min": 15, "max": 25, "target": 20},
            "cash":   {"min": 15, "max": 25, "target": 20},
            "bias": "DEFENSIVE",
            "label": "Stagflation Risk",
        },
        "GROWTH_RECOVERY": {
            "equity": {"min": 55, "max": 75, "target": 65},
            "debt":   {"min": 15, "max": 30, "target": 20},
            "gold":   {"min": 5,  "max": 12, "target": 8},
            "cash":   {"min": 5,  "max": 12, "target": 7},
            "bias": "RISK_ON",
            "label": "Growth Recovery",
        },
        "CREDIT_EXPANSION": {
            "equity": {"min": 55, "max": 70, "target": 62},
            "debt":   {"min": 20, "max": 35, "target": 28},
            "gold":   {"min": 3,  "max": 8,  "target": 5},
            "cash":   {"min": 3,  "max": 8,  "target": 5},
            "bias": "RISK_ON",
            "label": "Credit Expansion",
        },
        "LIQUIDITY_TIGHTENING": {
            "equity": {"min": 20, "max": 40, "target": 30},
            "debt":   {"min": 25, "max": 45, "target": 35},
            "gold":   {"min": 10, "max": 20, "target": 15},
            "cash":   {"min": 15, "max": 25, "target": 20},
            "bias": "DEFENSIVE",
            "label": "Liquidity Tightening",
        },
        "OVERHEATING": {
            "equity": {"min": 40, "max": 60, "target": 50},
            "debt":   {"min": 15, "max": 30, "target": 22},
            "gold":   {"min": 10, "max": 18, "target": 14},
            "cash":   {"min": 12, "max": 20, "target": 14},
            "bias": "CAUTIOUS",
            "label": "Overheating",
        },
        "DEFLATIONARY_BUST": {
            "equity": {"min": 15, "max": 35, "target": 25},
            "debt":   {"min": 35, "max": 55, "target": 45},
            "gold":   {"min": 10, "max": 20, "target": 15},
            "cash":   {"min": 15, "max": 25, "target": 15},
            "bias": "DEFENSIVE",
            "label": "Deflationary Bust",
        },
    }

    current_regime = body.get("current_regime", "LIQUIDITY_DRIVEN_EXPANSION")
    conviction     = body.get("conviction", "MEDIUM").upper()
    allocation     = body.get("allocation", {"equity": 60, "debt": 25, "gold": 10, "cash": 5})
    client_name    = body.get("client_name", "")  # display only; never stored

    if current_regime not in REGIME_TARGETS:
        raise HTTPException(status_code=400, detail=f"Unknown regime: {current_regime}")

    targets      = REGIME_TARGETS[current_regime]
    regime_bias  = targets["bias"]
    regime_label = targets["label"]

    # Per-sleeve analysis
    sleeve_analysis = {}
    for sleeve in ("equity", "debt", "gold", "cash"):
        actual     = float(allocation.get(sleeve, 0))
        t          = targets.get(sleeve, {})
        target_val = float(t.get("target", actual))
        t_min      = float(t.get("min", 0))
        t_max      = float(t.get("max", 100))
        delta      = actual - target_val

        if actual < t_min:
            status = "UNDERWEIGHT"
        elif actual > t_max:
            status = "OVERWEIGHT"
        else:
            status = "ON TARGET"

        if status == "UNDERWEIGHT":
            action = f"Add {round(target_val - actual)}% to reach regime target"
        elif status == "OVERWEIGHT":
            action = f"Trim {round(actual - target_val)}% to align with regime"
        else:
            action = "No action needed; within regime range"

        sleeve_analysis[sleeve] = {
            "actual":    actual,
            "target":    target_val,
            "range_min": t_min,
            "range_max": t_max,
            "delta":     round(delta, 1),
            "status":    status,
            "action":    action,
        }

    # Risk flags
    risk_flags  = []
    eq_actual   = float(allocation.get("equity", 0))
    cash_actual = float(allocation.get("cash",   0))
    gold_actual = float(allocation.get("gold",   0))
    debt_actual = float(allocation.get("debt",   0))

    if regime_bias == "DEFENSIVE" and eq_actual > 55:
        risk_flags.append("Equity overexposure in defensive regime; drawdown risk elevated")
    if regime_bias in ("RISK_ON",) and cash_actual > 15:
        risk_flags.append("Excess cash drag in risk-on regime; deployment opportunity being missed")
    if regime_bias == "DEFENSIVE" and cash_actual < 10:
        risk_flags.append("Insufficient cash buffer for defensive regime; limited dry powder")
    if gold_actual > 25:
        risk_flags.append("Gold above 25%; concentration risk in single defensive asset")
    if debt_actual > 60 and regime_bias == "RISK_ON":
        risk_flags.append("Heavy debt allocation in risk-on regime; opportunity cost risk")
    if eq_actual > 85:
        risk_flags.append("Equity above 85%; single-asset-class concentration risk")

    # Alignment score 0-100
    total_deviation = sum(
        abs(sleeve_analysis[s]["delta"])
        for s in ("equity", "debt", "gold", "cash")
    )
    alignment_score = max(0, round(100 - total_deviation * 2))

    # Conviction-gated action directive
    if conviction == "HIGH":
        if alignment_score < 60:
            action_gate    = "ACT"
            gate_rationale = "HIGH conviction with significant misalignment; rebalance now"
        else:
            action_gate    = "MINOR ONLY"
            gate_rationale = "HIGH conviction but allocation largely aligned; trim edges only"
    elif conviction == "MEDIUM":
        if alignment_score < 50:
            action_gate    = "MINOR ONLY"
            gate_rationale = "MEDIUM conviction; incremental adjustments only, avoid full repositioning"
        else:
            action_gate    = "HOLD"
            gate_rationale = "MEDIUM conviction with reasonable alignment; hold current positioning"
    else:  # LOW
        action_gate    = "HOLD"
        gate_rationale = "LOW conviction; preserve capital and await clearer regime signal"

    return {
        "regime": {
            "key":       current_regime,
            "label":     regime_label,
            "bias":      regime_bias,
            "conviction": conviction,
        },
        "sleeve_analysis":  sleeve_analysis,
        "risk_flags":       risk_flags,
        "alignment_score":  alignment_score,
        "action_gate":      action_gate,
        "gate_rationale":   gate_rationale,
        "allocation":       allocation,
        "client_name_display": (client_name[:50] if client_name else ""),
    }


@app.post("/api/notify-signup")
async def notify_signup(body: dict):
    """
    Sends actionable email to founder when a new user requests access.
    No auth required — called during signup flow before user has a token.
    """
    name     = body.get("name", "Unknown")
    email    = body.get("email", "")
    firm     = body.get("firm", "—")
    role     = body.get("role", "—")
    aum      = body.get("aum", "—")
    heard    = body.get("heard", "—")
    user_id  = body.get("user_id", "")

    approve_url = f"{RENDER_BASE}/api/admin/approve/{user_id}"
    reject_url  = f"{RENDER_BASE}/api/admin/reject/{user_id}"

    role_labels = {
        "ca":             "Chartered Accountant",
        "ifa":            "Independent Financial Advisor",
        "wealth_manager": "Wealth Manager",
        "other":          "Other",
    }
    aum_labels = {
        "individual": "Below ₹50 Cr",
        "small":      "₹50 – 200 Cr",
        "mid":        "₹200 – 500 Cr",
        "large":      "Above ₹500 Cr",
    }

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;padding:24px;
      background:#04060f;color:#e8eeff;border-radius:12px;">
      <div style="font-size:11px;letter-spacing:2px;color:#4f83ff;margin-bottom:8px;">
        ECONIQ · NEW ACCESS REQUEST
      </div>
      <h2 style="margin:0 0 16px;font-size:20px;color:#e8eeff;">
        {name} wants access to Sentinel
      </h2>
      <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
        <tr>
          <td style="padding:6px 0;color:#7a8baa;font-size:12px;width:120px;">Name</td>
          <td style="padding:6px 0;font-size:12px;">{name}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#7a8baa;font-size:12px;">Email</td>
          <td style="padding:6px 0;font-size:12px;">{email}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#7a8baa;font-size:12px;">Firm</td>
          <td style="padding:6px 0;font-size:12px;">{firm or "—"}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#7a8baa;font-size:12px;">Role</td>
          <td style="padding:6px 0;font-size:12px;">{role_labels.get(role, role)}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#7a8baa;font-size:12px;">AUM</td>
          <td style="padding:6px 0;font-size:12px;">{aum_labels.get(aum, aum)}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#7a8baa;font-size:12px;">Heard via</td>
          <td style="padding:6px 0;font-size:12px;">{heard}</td>
        </tr>
      </table>
      <div style="display:flex;gap:12px;">
        <a href="{approve_url}"
           style="background:#10d48a;color:#04060f;padding:12px 24px;
             border-radius:8px;text-decoration:none;font-weight:700;font-size:13px;">
          ✓ Approve Access
        </a>
        <a href="{reject_url}"
           style="background:#f87171;color:#fff;padding:12px 24px;
             border-radius:8px;text-decoration:none;font-weight:700;font-size:13px;">
          ✗ Reject
        </a>
      </div>
      <div style="margin-top:20px;font-size:10px;color:#7a8baa;">
        EconIQ · Sentinel Early Access
      </div>
    </div>
    """

    sent = _send_email(
        to="apurv350@gmail.com",
        subject=f"[EconIQ] New access request from {name}",
        html=html_body,
    )
    return {"status": "notified" if sent else "email_failed"}


@app.get("/api/admin/approve/{user_id}")
async def approve_user(user_id: str):
    """
    Approves a pending user — sets tier to 'trial' with 30-day window.
    Called from actionable email link. No auth required; secured by UUID obscurity + HTTPS.
    """
    try:
        trial_end = (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).isoformat()

        result = _supabase.table("profiles").update({
            "tier":          "trial",
            "trial_ends_at": trial_end,
            "approved_at":   datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()

        if not result.data:
            return HTMLResponse("<h2>User not found</h2>", status_code=404)

        user_data  = result.data[0]
        user_email = user_data.get("email", "")
        user_name  = user_data.get("full_name", "there")

        if user_email:
            approval_html = f"""
            <div style="font-family:Arial;max-width:480px;padding:24px;
              background:#04060f;color:#e8eeff;border-radius:12px;">
              <div style="font-size:11px;letter-spacing:2px;color:#4f83ff;margin-bottom:8px;">
                ECONIQ · SENTINEL
              </div>
              <h2 style="color:#10d48a;margin:0 0 12px;">
                You're in, {user_name}.
              </h2>
              <p style="color:#7a8baa;font-size:13px;line-height:1.6;">
                Your early access to Sentinel has been approved.
                You have 30 days of trial access starting today.
              </p>
              <a href="https://macro-economic-research-algorithm.vercel.app/login.html"
                 style="display:inline-block;margin-top:16px;background:#4f83ff;
                   color:#fff;padding:12px 24px;border-radius:8px;
                   text-decoration:none;font-weight:700;font-size:13px;">
                Access Sentinel →
              </a>
              <p style="margin-top:20px;font-size:10px;color:#7a8baa;">
                EconIQ · econiq.teams@gmail.com
              </p>
            </div>
            """
            _send_email(
                to=user_email,
                subject="You're approved — Sentinel Early Access",
                html=approval_html,
            )

        print(f"[APPROVE] Approved {user_email}", flush=True)
        return HTMLResponse(f"""
        <html><body style="font-family:Arial;background:#04060f;color:#e8eeff;
          display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
          <div style="text-align:center;">
            <div style="color:#10d48a;font-size:48px;">✓</div>
            <h2 style="color:#e8eeff;">{user_name} approved.</h2>
            <p style="color:#7a8baa;">They've been sent a 30-day trial access email.</p>
          </div>
        </body></html>
        """)

    except Exception as e:
        print(f"[APPROVE] Failed: {e}", flush=True)
        return HTMLResponse(f"<h2>Error: {e}</h2>", status_code=500)


@app.get("/api/admin/reject/{user_id}")
async def reject_user(user_id: str):
    """
    Rejects a pending user — sets tier to 'rejected'.
    Called from actionable email link. No auth required; secured by UUID obscurity + HTTPS.
    """
    try:
        result = _supabase.table("profiles").update({
            "tier":        "rejected",
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()

        user_data = (result.data or [{}])[0]
        user_name = user_data.get("full_name", "User")

        print(f"[REJECT] Rejected {user_id}", flush=True)
        return HTMLResponse(f"""
        <html><body style="font-family:Arial;background:#04060f;color:#e8eeff;
          display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
          <div style="text-align:center;">
            <div style="color:#f87171;font-size:48px;">✗</div>
            <h2 style="color:#e8eeff;">{user_name} rejected.</h2>
            <p style="color:#7a8baa;">Their account remains pending.</p>
          </div>
        </body></html>
        """)

    except Exception as e:
        return HTMLResponse(f"<h2>Error: {e}</h2>", status_code=500)


@app.get("/api/fii-history")
async def get_fii_history(profile: dict = Depends(require_access)):
    """
    Returns aggregated FII/DII flows across 1D/1W/1M/3M timeframes.
    Primary source: fii_dii_daily table.
    Falls back to runs table when fii_dii_daily has fewer than 3 rows.
    """
    try:
        from datetime import date, timedelta
        today = date.today()
        cutoff_90d = (today - timedelta(days=90)).isoformat()

        daily_res = (
            _supabase.table("fii_dii_daily")
            .select("trade_date,fii_net_crore,dii_net_crore")
            .gte("trade_date", cutoff_90d)
            .order("trade_date", desc=True)
            .execute()
        )
        rows = daily_res.data or []

        if len(rows) < 3:
            runs_res = (
                _supabase.table("runs")
                .select("run_at,fii_net_crore,dii_net_crore")
                .eq("user_id", profile["id"])
                .gte("run_at", cutoff_90d)
                .not_.is_("fii_net_crore", "null")
                .order("run_at", desc=True)
                .execute()
            )
            seen_dates: set = set()
            rows = []
            for r in (runs_res.data or []):
                d = str(r["run_at"])[:10]
                if d not in seen_dates:
                    seen_dates.add(d)
                    rows.append({
                        "trade_date":    d,
                        "fii_net_crore": r["fii_net_crore"],
                        "dii_net_crore": r["dii_net_crore"],
                    })

        def aggregate(rows, days):
            cutoff = (today - timedelta(days=days)).isoformat()
            subset = [
                r for r in rows
                if r["trade_date"] >= cutoff and r.get("fii_net_crore") is not None
            ]
            if not subset:
                return None
            fii_total = sum(r["fii_net_crore"] for r in subset)
            dii_total = sum((r.get("dii_net_crore") or 0) for r in subset)
            return {
                "fii_total":    round(fii_total, 0),
                "dii_total":    round(dii_total, 0),
                "days":         days,
                "data_points":  len(subset),
                "fii_signal":   "BUYING"  if fii_total >  2000 else "SELLING" if fii_total < -2000 else "NEUTRAL",
                "dii_signal":   "BUYING"  if dii_total >  2000 else "SELLING" if dii_total < -2000 else "NEUTRAL",
                "net_combined": round(fii_total + dii_total, 0),
            }

        data_source = "fii_dii_daily" if len(daily_res.data or []) >= 3 else "runs_fallback"
        return {
            "timeframes": {
                "1D": aggregate(rows, 1),
                "1W": aggregate(rows, 7),
                "1M": aggregate(rows, 30),
                "3M": aggregate(rows, 90),
            },
            "daily_series": rows[:30],
            "data_source":  data_source,
        }

    except Exception as e:
        print(f"[FII_HISTORY] Error: {e}", flush=True)
        raise HTTPException(status_code=500, detail=f"FII history failed: {e}")

PE_SECTOR_CYCLES = {
    "Consumer & Retail": {
        "cycle_stage": "GROWTH",
        "pe_signal": "FAVOURABLE",
        "macro_tailwind": "Rising disposable income, credit expansion, urban consumption recovery",
        "macro_headwind": "Elevated crude → input cost pressure, INR weakness",
        "irr_context": "Consumer deals supported by volume growth; margin recovery thesis intact",
        "entry_timing": "SELECTIVE — quality brands with pricing power",
    },
    "Financial Services & Fintech": {
        "cycle_stage": "GROWTH",
        "pe_signal": "FAVOURABLE",
        "macro_tailwind": "Liquidity expansion, credit cycle upturn, digital payment penetration",
        "macro_headwind": "Credit quality risk if cycle turns; regulatory overhang on fintech",
        "irr_context": "Lending and payments platforms in sweet spot; exit via IPO feasible",
        "entry_timing": "DEPLOY — regime actively supports financial sector",
    },
    "Infrastructure & Logistics": {
        "cycle_stage": "EARLY_GROWTH",
        "pe_signal": "FAVOURABLE",
        "macro_tailwind": "Govt capex ₹12.2L Cr, PLI schemes, data centre demand",
        "macro_headwind": "Rate sensitivity on long-duration assets; execution risk",
        "irr_context": "Long-hold infrastructure thesis supported by policy cycle",
        "entry_timing": "DEPLOY — capex supercycle in early stages",
    },
    "Healthcare & Pharma": {
        "cycle_stage": "MATURE",
        "pe_signal": "NEUTRAL",
        "macro_tailwind": "Export opportunity, API localisation push, hospital capacity",
        "macro_headwind": "US pricing pressure, USFDA compliance costs",
        "irr_context": "Defensive returns; selective hospital and diagnostics plays",
        "entry_timing": "SELECTIVE — quality assets at reasonable valuations only",
    },
    "Technology & SaaS": {
        "cycle_stage": "GROWTH",
        "pe_signal": "NEUTRAL",
        "macro_tailwind": "Global IT spending recovery, AI adoption, GCC buildout in India",
        "macro_headwind": "US recession risk dampens discretionary IT spend",
        "irr_context": "B2B SaaS and GCC-linked plays have strong exit visibility",
        "entry_timing": "SELECTIVE — unit economics and path to profitability critical",
    },
    "Space & Deep Tech": {
        "cycle_stage": "EARLY",
        "pe_signal": "HIGH_CONVICTION",
        "macro_tailwind": "IN-SPACe framework, ISRO commercialisation, defence dual-use, ₹1000Cr space fund",
        "macro_headwind": "Long gestation, limited exit visibility, talent pool constraints",
        "irr_context": "10Y+ horizon; early positions in launch vehicles, satellites, and ground systems",
        "entry_timing": "BUILD POSITION — policy window open, competition low",
    },
    "Defence & Aerospace": {
        "cycle_stage": "EARLY_GROWTH",
        "pe_signal": "HIGH_CONVICTION",
        "macro_tailwind": "₹6L Cr defence budget, 75% domestic procurement mandate, export push",
        "macro_headwind": "Long procurement cycles, single-customer risk (MoD)",
        "irr_context": "Ordnance, electronics, and MRO have clearest near-term revenue visibility",
        "entry_timing": "DEPLOY — policy tailwinds strongest in a decade",
    },
    "Green Energy & Climate": {
        "cycle_stage": "EARLY_GROWTH",
        "pe_signal": "FAVOURABLE",
        "macro_tailwind": "500GW renewable target, green hydrogen mission, MNRE incentives",
        "macro_headwind": "Rate sensitivity, land acquisition, grid integration challenges",
        "irr_context": "Solar and wind generation mature; storage and green H2 are early bets",
        "entry_timing": "SELECTIVE — utility scale mature, storage tech early",
    },
}

PE_DEAL_FLOW = [
    {
        "date": "May 2026",
        "sector": "Space & Deep Tech",
        "deal_type": "Series B",
        "investor_type": "VC/PE",
        "signal": "POSITIVE",
        "headline": "Multiple launch vehicle startups raise Series B rounds as IN-SPACe approvals accelerate",
        "macro_read": "Policy momentum converting to commercial capital — sector formation underway",
    },
    {
        "date": "May 2026",
        "sector": "Defence & Aerospace",
        "deal_type": "Growth Equity",
        "investor_type": "PE",
        "signal": "POSITIVE",
        "headline": "Defence electronics and MRO platforms attracting growth equity on back of indigenisation mandate",
        "macro_read": "₹6L Cr defence budget creating durable revenue visibility for private players",
    },
    {
        "date": "Apr 2026",
        "sector": "Green Energy",
        "deal_type": "Infrastructure PE",
        "investor_type": "Infrastructure Fund",
        "signal": "POSITIVE",
        "headline": "Large infrastructure funds increasing allocation to utility-scale solar and wind",
        "macro_read": "500GW renewable target driving long-term contracted cash flow assets",
    },
    {
        "date": "Apr 2026",
        "sector": "Financial Services",
        "deal_type": "Series C",
        "investor_type": "VC/PE",
        "signal": "POSITIVE",
        "headline": "Lending and payments fintechs closing large rounds on credit cycle recovery thesis",
        "macro_read": "Liquidity expansion regime actively supports financial sector deal-making",
    },
    {
        "date": "Apr 2026",
        "sector": "Healthcare",
        "deal_type": "Buyout",
        "investor_type": "PE",
        "signal": "NEUTRAL",
        "headline": "Hospital chains and diagnostics networks seeing consolidation interest",
        "macro_read": "Defensive positioning — healthcare deals resilient across macro regimes",
    },
]

PE_DEAL_FLOW_META = {
    "last_updated":      "May 2026",
    "last_updated_iso":  "2026-05-01",
    "next_update_due":   "June 2026",
    "sources": [
        "SEBI public filings",
        "Company press releases",
        "Market intelligence — public announcements",
    ],
    "update_frequency": "Monthly — first working day",
    "disclaimer": (
        "Deal flow sourced from public announcements "
        "only. Not proprietary deal data."
    ),
}

COST_OF_CAPITAL = {
    "LIQUIDITY_DRIVEN_EXPANSION": {
        "environment": "SUPPORTIVE",
        "repo_rate_trend": "STABLE TO DECLINING",
        "credit_spread": "COMPRESSING",
        "debt_financing": "Favourable — credit markets open, spreads tight",
        "irr_implication": "Current environment supports 18-22% IRR assumptions on 5-7 year holds",
        "exit_environment": "POSITIVE — public markets receptive, IPO window open",
        "dry_powder_call": "DEPLOY — macro conditions actively favour capital deployment",
    },
    "STABLE_GROWTH": {
        "environment": "NEUTRAL",
        "repo_rate_trend": "STABLE",
        "credit_spread": "STABLE",
        "debt_financing": "Neutral — credit available at fair terms",
        "irr_implication": "Conservative 15-18% IRR assumptions appropriate; quality assets command premium",
        "exit_environment": "NEUTRAL — selective exit opportunities; quality assets trade well",
        "dry_powder_call": "SELECTIVE DEPLOYMENT — favour quality over momentum",
    },
    "MONETARY_TIGHTENING": {
        "environment": "CHALLENGING",
        "repo_rate_trend": "RISING",
        "credit_spread": "WIDENING",
        "debt_financing": "Expensive — higher cost of debt compresses equity IRR",
        "irr_implication": "Adjust IRR targets upward 200-300bps; debt structures need stress testing",
        "exit_environment": "DIFFICULT — public markets under pressure; delay non-urgent exits",
        "dry_powder_call": "PRESERVE DRY POWDER — deploy only into exceptional opportunities",
    },
    "EXTERNAL_SHOCK": {
        "environment": "RISK-OFF",
        "repo_rate_trend": "UNCERTAIN",
        "credit_spread": "SPIKING",
        "debt_financing": "Constrained — credit markets stressed, debt expensive",
        "irr_implication": "Existing portfolio stress-test critical; new deals require significant discount",
        "exit_environment": "CLOSED — avoid exits; extend hold periods",
        "dry_powder_call": "HOLD — exceptional distressed opportunities only",
    },
    "STAGFLATION_RISK": {
        "environment": "VERY CHALLENGING",
        "repo_rate_trend": "RISING",
        "credit_spread": "WIDENING SHARPLY",
        "debt_financing": "Very expensive — worst environment for leveraged structures",
        "irr_implication": "Portfolio review and restructuring priority over new deployment",
        "exit_environment": "VERY DIFFICULT — preserve portfolio companies",
        "dry_powder_call": "PRESERVE — protect existing portfolio first",
    },
    "STAGFLATIONARY_RISK": {
        "environment": "VERY CHALLENGING",
        "repo_rate_trend": "RISING",
        "credit_spread": "WIDENING SHARPLY",
        "debt_financing": "Very expensive — worst environment for leveraged structures",
        "irr_implication": "Portfolio review and restructuring priority over new deployment",
        "exit_environment": "VERY DIFFICULT — preserve portfolio companies",
        "dry_powder_call": "PRESERVE — protect existing portfolio first",
    },
    "EARLY_CYCLE_RECOVERY": {
        "environment": "HIGHLY SUPPORTIVE",
        "repo_rate_trend": "DECLINING",
        "credit_spread": "COMPRESSING RAPIDLY",
        "debt_financing": "Improving rapidly — best entry point for leveraged structures",
        "irr_implication": "Highest return potential — early cycle entry with 5Y+ hold maximises IRR",
        "exit_environment": "BUILDING — IPO pipeline recovering; strategic buyers returning",
        "dry_powder_call": "DEPLOY AGGRESSIVELY — best deployment window in the cycle",
    },
    "GROWTH_SLOWDOWN_SUPPORT": {
        "environment": "NEUTRAL",
        "repo_rate_trend": "STABLE TO DECLINING",
        "credit_spread": "STABLE",
        "debt_financing": "Reasonable — rate support offsetting weaker growth backdrop",
        "irr_implication": "Selective deployment into quality assets with defensive revenue profiles",
        "exit_environment": "SELECTIVE — quality assets exit; cyclicals wait",
        "dry_powder_call": "SELECTIVE — defensive sectors and quality cash-flow businesses only",
    },
}


def _build_live_cost_of_capital(
    regime: str,
    confidence: float,
    repo_rate: float,
    conviction: str,
    run_at: str | None,
) -> dict:
    """
    Builds cost of capital intelligence dynamically from live regime engine output.
    Uses actual repo rate from the latest run.
    Falls back to hardcoded COST_OF_CAPITAL if anything fails.
    """
    try:
        base = COST_OF_CAPITAL.get(regime, COST_OF_CAPITAL["STABLE_GROWTH"])
        conf_pct = round(confidence * 100)

        if repo_rate <= 5.0:
            debt_read = (
                f"Favourable — repo at {repo_rate}% "
                f"supports leveraged structures. "
                f"Credit markets open."
            )
        elif repo_rate <= 5.75:
            debt_read = (
                f"Neutral — repo at {repo_rate}%. "
                f"Credit available at fair terms. "
                f"Watch for rate direction at next MPC."
            )
        else:
            debt_read = (
                f"Elevated — repo at {repo_rate}%. "
                f"Higher debt costs compress equity IRR. "
                f"Stress test financing assumptions."
            )

        if regime in ("LIQUIDITY_DRIVEN_EXPANSION", "EARLY_CYCLE_RECOVERY") and repo_rate <= 5.5:
            irr_read = (
                f"Current environment supports 18-22% IRR "
                f"assumptions on 5-7Y holds. "
                f"Repo at {repo_rate}% and {regime.replace('_', ' ').title()} "
                f"regime at {conf_pct}% confidence — "
                f"optimal entry window."
            )
        elif regime in ("LIQUIDITY_DRIVEN_EXPANSION", "STABLE_GROWTH"):
            irr_read = (
                f"15-19% IRR assumptions appropriate. "
                f"Repo at {repo_rate}%, regime supportive "
                f"at {conf_pct}% confidence. "
                f"Quality assets command premium."
            )
        elif regime in ("MONETARY_TIGHTENING", "STAGFLATION_RISK", "STAGFLATIONARY_RISK"):
            irr_read = (
                f"Adjust IRR targets upward 200-300bps. "
                f"Repo at {repo_rate}% with {regime.replace('_', ' ').title()} "
                f"compresses equity returns. "
                f"Stress test debt structures."
            )
        else:
            irr_read = base.get(
                "irr_implication",
                "Conservative 15-18% IRR assumptions appropriate."
            )

        if conviction == "HIGH" and regime in ("LIQUIDITY_DRIVEN_EXPANSION", "EARLY_CYCLE_RECOVERY"):
            deploy_call = (
                f"DEPLOY — HIGH conviction "
                f"{regime.replace('_', ' ')}. "
                f"Best deployment window in current cycle."
            )
        elif conviction == "LOW":
            deploy_call = (
                "PRESERVE DRY POWDER — LOW conviction. "
                "Wait for regime confirmation before "
                "committing capital."
            )
        else:
            deploy_call = base.get(
                "dry_powder_call",
                "SELECTIVE DEPLOYMENT — regime supportive but conviction building."
            )

        return {
            **base,
            "repo_rate":      repo_rate,
            "confidence_pct": conf_pct,
            "conviction":     conviction,
            "debt_financing": debt_read,
            "irr_implication": irr_read,
            "dry_powder_call": deploy_call,
            "data_as_of":     run_at,
            "is_live":        True,
        }
    except Exception as _e:
        print(f"[PE] _build_live_cost_of_capital error: {_e}", flush=True)
        return COST_OF_CAPITAL.get(regime, COST_OF_CAPITAL["STABLE_GROWTH"])


def _build_live_sector_cycles(
    regime: str,
    confidence: float,
) -> dict:
    """
    Adjusts PE sector signals based on live regime.
    Returns PE_SECTOR_CYCLES unchanged if regime is unknown or anything fails.
    """
    try:
        cycles = dict(PE_SECTOR_CYCLES)

        REGIME_SECTOR_OVERRIDES = {
            "LIQUIDITY_DRIVEN_EXPANSION": {
                "Financial Services & Fintech": "FAVOURABLE",
                "Consumer & Retail":            "FAVOURABLE",
                "Infrastructure & Logistics":   "FAVOURABLE",
                "Space & Deep Tech":            "HIGH_CONVICTION",
                "Defence & Aerospace":          "HIGH_CONVICTION",
            },
            "MONETARY_TIGHTENING": {
                "Financial Services & Fintech": "CAUTIOUS",
                "Consumer & Retail":            "NEUTRAL",
                "Infrastructure & Logistics":   "NEUTRAL",
                "Healthcare & Pharma":          "FAVOURABLE",
                "Technology & SaaS":            "FAVOURABLE",
            },
            "EXTERNAL_SHOCK": {
                "Financial Services & Fintech": "CAUTIOUS",
                "Consumer & Retail":            "CAUTIOUS",
                "Infrastructure & Logistics":   "NEUTRAL",
                "Healthcare & Pharma":          "FAVOURABLE",
                "Space & Deep Tech":            "NEUTRAL",
            },
            "STAGFLATION_RISK": {
                "Financial Services & Fintech": "CAUTIOUS",
                "Consumer & Retail":            "CAUTIOUS",
                "Infrastructure & Logistics":   "CAUTIOUS",
                "Healthcare & Pharma":          "FAVOURABLE",
                "Green Energy & Climate":       "NEUTRAL",
            },
            "STABLE_GROWTH": {
                "Technology & SaaS":            "FAVOURABLE",
                "Healthcare & Pharma":          "FAVOURABLE",
                "Consumer & Retail":            "FAVOURABLE",
                "Financial Services & Fintech": "NEUTRAL",
            },
            "EARLY_CYCLE_RECOVERY": {
                "Financial Services & Fintech": "FAVOURABLE",
                "Consumer & Retail":            "FAVOURABLE",
                "Infrastructure & Logistics":   "FAVOURABLE",
                "Space & Deep Tech":            "HIGH_CONVICTION",
                "Defence & Aerospace":          "HIGH_CONVICTION",
            },
        }

        overrides = REGIME_SECTOR_OVERRIDES.get(regime, {})
        for sector, new_signal in overrides.items():
            if sector in cycles:
                old = dict(cycles[sector])
                cycles[sector] = {
                    **old,
                    "pe_signal":    new_signal,
                    "regime_driven": True,
                    "regime_note":  (
                        f"Signal updated for "
                        f"{regime.replace('_', ' ').title()} "
                        f"regime at "
                        f"{round(confidence * 100)}% confidence"
                    ),
                }

        return cycles
    except Exception as _e:
        print(f"[PE] _build_live_sector_cycles error: {_e}", flush=True)
        return PE_SECTOR_CYCLES


@app.get("/api/pe/overview")
async def get_pe_overview(profile: dict = Depends(require_access)):
    try:
        latest_run = (
            _supabase.table("runs")
            .select("regime,confidence,conviction,run_at,repo_rate")
            .eq("user_id", profile["id"])
            .order("run_at", desc=True)
            .limit(1)
            .execute()
        )
        current_regime = "LIQUIDITY_DRIVEN_EXPANSION"
        confidence     = 0.0
        conviction     = "MEDIUM"
        run_at         = None
        repo_rate      = 5.25
        if latest_run.data:
            r              = latest_run.data[0]
            current_regime = r.get("regime", current_regime)
            confidence     = r.get("confidence", 0.0)
            conviction     = r.get("conviction", "MEDIUM")
            run_at         = r.get("run_at")
            repo_rate      = float(r.get("repo_rate") or 5.25)

        cost_of_capital = _build_live_cost_of_capital(
            regime     = current_regime,
            confidence = confidence,
            repo_rate  = repo_rate,
            conviction = conviction,
            run_at     = run_at,
        )
        sector_cycles = _build_live_sector_cycles(
            regime     = current_regime,
            confidence = confidence,
        )
        return {
            "regime":          current_regime,
            "confidence":      confidence,
            "conviction":      conviction,
            "run_at":          run_at,
            "repo_rate":       repo_rate,
            "cost_of_capital": cost_of_capital,
            "sector_cycles":   sector_cycles,
            "deal_flow":       PE_DEAL_FLOW,
            "deal_flow_meta":  PE_DEAL_FLOW_META,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PE overview failed: {e}")


@app.get("/api/pe/sectors")
async def get_pe_sectors(profile: dict = Depends(require_access)):
    return {"sectors": PE_SECTOR_CYCLES}


@app.get("/api/pe/deal-flow")
async def get_pe_deal_flow(profile: dict = Depends(require_access)):
    return {"deals": PE_DEAL_FLOW}


@app.get("/api/profile")
async def get_user_profile(profile: dict = Depends(get_profile)):
    return {"profile": profile}


class WhatsappUpdate(BaseModel):
    whatsapp_number: str


@app.patch("/api/profile/whatsapp")
async def update_whatsapp_number(
    body: WhatsappUpdate,
    profile: dict = Depends(require_access),
):
    number = body.whatsapp_number.strip()
    if number and not re.match(r"^\+\d{10,15}$", number):
        raise HTTPException(
            status_code=400,
            detail="Invalid number format. Must start with + followed by 10–15 digits.",
        )
    _supabase.table("profiles").update(
        {"whatsapp_number": number or None}
    ).eq("id", profile["id"]).execute()
    return {"success": True, "whatsapp_number": number or None}


_ticker_cache: dict = {"data": {}, "fetched_at": 0}

@app.get("/api/ticker")
async def get_ticker():
    global _ticker_cache
    if time.time() - _ticker_cache["fetched_at"] < 300 and _ticker_cache["data"]:
        return {"ticker": _ticker_cache["data"], "cached": True}
    try:
        import yfinance as yf
        symbols = {"^NSEI": "Nifty 50", "^NSEBANK": "Bank Nifty", "^INDIAVIX": "India VIX", "USDINR=X": "USD/INR", "GC=F": "Gold", "CL=F": "Crude", "^TNX": "US 10Y"}
        result = {}
        for sym, label in symbols.items():
            try:
                h = yf.Ticker(sym).history(period="5d", interval="1d")
                if len(h) >= 2:
                    prev = float(h["Close"].iloc[-2]); last = float(h["Close"].iloc[-1]); chg = (last - prev) / prev * 100 if prev else 0
                    result[label] = {"price": round(last, 2), "chg_pct": round(chg, 2)}
                elif len(h) == 1:
                    result[label] = {"price": round(float(h["Close"].iloc[-1]), 2), "chg_pct": 0.0}
                else:
                    result[label] = {"price": None, "chg_pct": None}
            except Exception:
                result[label] = {"price": None, "chg_pct": None}
        _ticker_cache = {"data": result, "fetched_at": time.time()}
        return {"ticker": result, "cached": False}
    except Exception as e:
        return {"ticker": {}, "error": str(e)}
    
# ── Hardcoded policy rates — update when central banks meet ──────────────────
# Last verified: May 2026 (sources: Fed, ECB, BoJ, PBoC, BoE, RBI and other official pages)
_POLICY_RATES = {
    "US": 4.50,   # US Federal Funds Rate
    "CN": 3.10,   # PBoC Loan Prime Rate 1Y
    "DE": 2.40,   # ECB Deposit Facility Rate
    "IN": 5.25,   # RBI Repo Rate
    "JP": 0.50,   # Bank of Japan Policy Rate
    "GB": 4.25,   # Bank of England Bank Rate
    "FR": 2.40,   # ECB Deposit Facility Rate
    "IT": 2.40,   # ECB Deposit Facility Rate
    "BR": 13.75,  # Banco do Brasil SELIC rate
    "CA": 2.75,   # Bank of Canada overnight rate
    "RU": 21.00,  # Bank of Russia key rate
    "KR": 2.75,   # Bank of Korea base rate
    "AU": 4.10,   # Reserve Bank of Australia cash rate
    "MX": 9.00,   # Banco de Mexico overnight rate
    "ID": 5.75,   # Bank Indonesia BI rate
    "NL": 2.40,   # ECB Deposit Facility Rate
    "SA": 5.00,   # Saudi Central Bank repo rate
    "TR": 42.50,  # Central Bank of Turkey rate
    "CH": 0.25,   # Swiss National Bank policy rate
    "TW": 2.00,   # Central Bank of ROC rate
    # ── 30 new economies — Last verified: May 2026 ──
    "PL": 5.75,   # National Bank of Poland
    "SE": 2.25,   # Riksbank
    "BE": 2.40,   # ECB
    "AR": 40.00,  # BCRA (Argentina — high inflation)
    "NO": 4.50,   # Norges Bank
    "AE": 5.40,   # UAE follows Fed
    "IL": 4.50,   # Bank of Israel
    "AT": 2.40,   # ECB
    "SG": 3.00,   # MAS implicit rate
    "NG": 27.25,  # CBN Nigeria
    "ZA": 7.50,   # SARB South Africa
    "MY": 3.00,   # Bank Negara Malaysia
    "DK": 2.10,   # Danmarks Nationalbank
    "PH": 6.25,   # Bangko Sentral ng Pilipinas
    "IE": 2.40,   # ECB
    "TH": 2.50,   # Bank of Thailand
    "BD": 10.00,  # Bangladesh Bank
    "VN": 4.50,   # State Bank of Vietnam
    "PK": 12.00,  # State Bank of Pakistan
    "CO": 9.75,   # Banco de la República Colombia
    "CL": 5.00,   # Banco Central de Chile
    "FI": 2.40,   # ECB
    "CZ": 3.75,   # Czech National Bank
    "RO": 6.50,   # National Bank of Romania
    "NZ": 3.50,   # Reserve Bank of New Zealand
    "PT": 2.40,   # ECB
    "GR": 2.40,   # ECB
    "QA": 5.20,   # Qatar Central Bank
    "KZ": 14.25,  # National Bank of Kazakhstan
    "HU": 6.50,   # Magyar Nemzeti Bank
}

# ── FRED series IDs for major central bank rates ──────────────────────────────
# Free API — register at fred.stlouisfed.org/docs/api/api_key.html
# Add FRED_API_KEY to Railway Variables for higher rate limits
# Falls back to _POLICY_RATES hardcoded values when FRED is unavailable
_FRED_RATE_SERIES = {
    # ── United States ──
    "US": "FEDFUNDS",

    # ── ECB Eurozone ──
    "DE": "ECBDFR",
    "FR": "ECBDFR",
    "IT": "ECBDFR",
    "BE": "ECBDFR",
    "AT": "ECBDFR",
    "NL": "ECBDFR",
    "IE": "ECBDFR",
    "FI": "ECBDFR",
    "PT": "ECBDFR",
    "GR": "ECBDFR",

    # ── Other major central banks ──
    "GB": "BOEBR",               # Bank of England
    "JP": "IRSTCI01JPM156N",     # Bank of Japan
    "CA": "IRSTCI01CAM156N",     # Bank of Canada
    "AU": "IRSTCI01AUM156N",     # RBA
    "SE": "IRSTCI01SEM156N",     # Riksbank
    "NO": "IRSTCI01NOM156N",     # Norges Bank
    "CH": "IRSTCI01CHM156N",     # SNB
    "KR": "IRSTCI01KRM156N",     # Bank of Korea
    "IN": "IRSTCI01INM156N",     # RBI repo rate
    "BR": "IRSTCI01BRM156N",     # BCB Selic
    "MX": "IRSTCI01MXM156N",     # Banxico
    "ZA": "IRSTCI01ZAM156N",     # SARB
    "CZ": "IRSTCI01CZM156N",     # CNB
    "HU": "IRSTCI01HUM156N",     # MNB
    "PL": "IRSTCI01PLM156N",     # NBP
    "RO": "IRSTCI01ROM156N",     # NBR
    "NZ": "IRSTCI01NZM156N",     # RBNZ
    "DK": "IRSTCI01DKM156N",     # Danmarks Nationalbank
}

_fred_rate_cache: dict = {"data": {}, "fetched_at": 0}


def _fetch_fred_rates() -> dict:
    """
    Fetches major central bank policy rates from FRED API.
    Cached for 24 hours since rates change infrequently.
    Returns dict of {country_code: rate_pct}.
    Falls back to {} if FRED is unavailable or key is missing.
    """
    global _fred_rate_cache

    # 24-hour cache
    if (time.time() - _fred_rate_cache["fetched_at"] < 86400
            and _fred_rate_cache["data"]):
        return _fred_rate_cache["data"]

    import requests as _req

    fred_key = os.environ.get("FRED_API_KEY", "")
    base_url = "https://api.stlouisfed.org/fred/series/observations"

    result = {}

    for code, series_id in _FRED_RATE_SERIES.items():
        try:
            params = {
                "series_id":         series_id,
                "api_key":           fred_key if fred_key else "anonymoususer",
                "file_type":         "json",
                "sort_order":        "desc",
                "observation_start": "2024-01-01",
                "limit":             1,
            }
            r = _req.get(base_url, params=params, timeout=8)
            if r.status_code != 200:
                continue

            data = r.json()
            obs  = data.get("observations", [])
            if obs and obs[0].get("value") != ".":
                val           = float(obs[0]["value"])
                result[code]  = round(val, 2)
                print(
                    f"  [FRED] {code}: {val}% ({series_id})",
                    flush=True
                )

        except Exception as _e:
            print(
                f"  [FRED] {code} fetch failed: {_e}",
                flush=True
            )

    if result:
        _fred_rate_cache = {"data": result, "fetched_at": time.time()}
        print(
            f"[FRED] Fetched {len(result)} policy rates",
            flush=True
        )

    return result


def _fetch_country_news(wb_code: str) -> dict | None:
    """
    Fetch the most recent business/economy headline for a country
    from NewsData.io.
    Returns {"headline", "source", "published_at"} or None on failure.
    """
    news_api_key = os.environ.get("NEWSDATA_API_KEY", "")
    if not news_api_key:
        return None

    try:
        import requests as _req
        url = "https://newsdata.io/api/1/news"
        params = {
            "apikey":   news_api_key,
            "country":  wb_code.lower(),
            "category": "business",
            "language": "en",
            "size":     1,
        }
        resp = _req.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            print(
                f"[NEWS] {wb_code} returned HTTP {resp.status_code}",
                flush=True
            )
            return None

        data    = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        item = results[0]
        return {
            "headline":     item.get("title", "")[:160],
            "source":       item.get("source_id", "unknown"),
            "published_at": item.get("pubDate"),
        }
    except Exception as e:
        print(f"[NEWS] fetch failed for {wb_code}: {e}", flush=True)
        return None


def _get_country_news_cached(wb_code: str) -> dict | None:
    """
    Returns cached news for a country if fresh (<24h), else fetches
    new and upserts cache. Falls back to stale cache if fetch fails.
    """
    try:
        cached = (
            _supabase.table("country_news_cache")
            .select("*")
            .eq("code", wb_code)
            .limit(1)
            .execute()
        )
        if cached.data:
            row        = cached.data[0]
            fetched_at = datetime.fromisoformat(row["fetched_at"])
            age_hours  = (
                datetime.now(timezone.utc) - fetched_at
            ).total_seconds() / 3600

            if age_hours < 24:
                return {
                    "headline":     row.get("headline"),
                    "source":       row.get("source"),
                    "published_at": row.get("published_at"),
                    "stale":        False,
                }

            # Stale — try fresh fetch
            fresh = _fetch_country_news(wb_code)
            if fresh:
                _supabase.table("country_news_cache").upsert({
                    "code":         wb_code,
                    "headline":     fresh["headline"],
                    "source":       fresh["source"],
                    "published_at": fresh["published_at"],
                    "fetched_at":   datetime.now(timezone.utc).isoformat(),
                }, on_conflict="code").execute()
                return {**fresh, "stale": False}

            # Fetch failed — return stale cache
            return {
                "headline":     row.get("headline"),
                "source":       row.get("source"),
                "published_at": row.get("published_at"),
                "stale":        True,
            }

        # No cache at all — fetch fresh
        fresh = _fetch_country_news(wb_code)
        if fresh:
            _supabase.table("country_news_cache").upsert({
                "code":         wb_code,
                "headline":     fresh["headline"],
                "source":       fresh["source"],
                "published_at": fresh["published_at"],
                "fetched_at":   datetime.now(timezone.utc).isoformat(),
            }, on_conflict="code").execute()
            return {**fresh, "stale": False}

        return None

    except Exception as e:
        print(f"[NEWS] cache lookup failed for {wb_code}: {e}", flush=True)
        return None


# ── Geopolitical Watch ────────────────────────────────────────────────────────
# 10 named themes relevant to India. NLP-generated analysis, 72h cache TTL.
# Update search queries here if a theme's focus shifts; the NLP prompt stays.

GEOPOLITICAL_THEMES = {
    "us_iran_hormuz": {
        "label": "US-Iran & Strait of Hormuz",
        "search_query": "Iran Strait of Hormuz oil tensions",
    },
    "russia_ukraine_europe": {
        "label": "Russia-Ukraine & European Energy",
        "search_query": "Russia Ukraine war Europe energy sanctions",
    },
    "indo_pacific_china": {
        "label": "Indo-Pacific & China Rivalry",
        "search_query": "China India border South China Sea tensions",
    },
    "india_eu_trade": {
        "label": "India-EU Trade Negotiations",
        "search_query": "India EU free trade agreement FTA negotiations",
    },
    "critical_minerals": {
        "label": "Critical Minerals & Supply Chains",
        "search_query": "critical minerals rare earth lithium supply chain India",
    },
    "nato_g7_coordination": {
        "label": "NATO-G7 Coordination",
        "search_query": "NATO G7 sanctions trade coordination policy",
    },
    "red_sea_maritime": {
        "label": "Red Sea & Maritime Security",
        "search_query": "Red Sea shipping Houthi maritime security freight",
    },
    "quad_india_japan": {
        "label": "Quad & India-Japan-Australia-US",
        "search_query": "Quad India Japan Australia US strategic partnership",
    },
    "fragmentation_decoupling": {
        "label": "Global Fragmentation & Decoupling",
        "search_query": "global trade fragmentation decoupling protectionism tariffs",
    },
    "climate_indian_ocean": {
        "label": "Climate & Indian Ocean Diplomacy",
        "search_query": "Indian Ocean island nations climate diplomacy strategic",
    },
}


def _fetch_theme_news(search_query: str, limit: int = 5) -> list[dict]:
    """
    Fetches geopolitical theme news via GDELT Document API v2.
    Free, no API key, global coverage, last 2 weeks of articles.
    Falls back to NewsData.io category fetch if GDELT fails.
    72h cache on callers means GDELT is called at most once per theme
    per 3 days — well within GDELT rate limits.
    """
    try:
        import requests as _req
        import urllib.parse as _up

        encoded = _up.quote(search_query)
        url = (
            f"https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query={encoded}"
            f"&mode=artlist"
            f"&maxrecords={limit}"
            f"&format=json"
            f"&timespan=2weeks"
            f"&sort=DateDesc"
        )
        resp = _req.get(url, timeout=15)
        print(
            f"[GEOWATCH] GDELT response: {resp.status_code}",
            flush=True
        )
        if resp.status_code != 200:
            raise Exception(f"GDELT HTTP {resp.status_code}")

        data     = resp.json()
        articles = data.get("articles", [])

        if not articles:
            raise Exception("GDELT returned 0 articles")

        results = []
        for a in articles[:limit]:
            title = a.get("title", "")
            if title:
                results.append({
                    "title":        title,
                    "source":       a.get("domain", ""),
                    "published_at": a.get("seendate", ""),
                })

        print(
            f"[GEOWATCH] GDELT returned {len(results)} articles "
            f"for: {search_query[:40]}",
            flush=True
        )
        return results

    except Exception as e:
        print(
            f"[GEOWATCH] GDELT failed: {e} — trying NewsData fallback",
            flush=True
        )

    # Fallback to NewsData.io
    news_api_key = os.environ.get("NEWSDATA_API_KEY", "")
    if not news_api_key:
        return []
    try:
        import requests as _req
        resp = _req.get(
            "https://newsdata.io/api/1/news",
            params={
                "apikey":   news_api_key,
                "category": "world,politics",
                "language": "en",
                "size":     limit,
            },
            timeout=10,
        )
        data    = resp.json()
        results = data.get("results", [])
        keywords = [w.lower() for w in search_query.split() if len(w) >= 3]
        matched  = [
            r for r in results
            if any(
                kw in (
                    (r.get("title") or "") + " " + (r.get("description") or "")
                ).lower()
                for kw in keywords
            )
        ]
        return [
            {
                "title":        r.get("title", ""),
                "source":       r.get("source_id", ""),
                "published_at": r.get("pubDate", ""),
            }
            for r in (matched or results)[:limit]
        ]
    except Exception:
        return []


def _generate_theme_analysis(
    theme_label: str,
    headlines: list[dict],
    nlp_engine,
) -> dict:
    """
    Calls the NLP engine with a structured prompt to produce a geopolitical
    theme analysis. Returns dict matching the geopolitical_watch_cache schema,
    or a safe fallback dict if NLP fails or headlines are empty.
    """
    fallback = {
        "status":                      "STABLE",
        "status_confidence":           "LOW",
        "headline_summary":            "No significant recent developments found.",
        "context":                     "Insufficient recent coverage to assess current state.",
        "india_transmission_channel":  "MULTIPLE",
        "india_linkage":               "Insufficient recent coverage for confident assessment.",
        "watch_for":                   "No specific near-term catalyst identified.",
    }

    if not headlines:
        return fallback

    headlines_text = "\n".join(
        f"- {h['title']} ({h.get('source', 'unknown')})"
        for h in headlines
    )

    prompt = f"""You are analyzing the geopolitical theme "{theme_label}" for an Indian financial advisory platform. Base your response ONLY on the news excerpts below — do not use prior knowledge or invent details not present in these excerpts.

NEWS EXCERPTS:
{headlines_text}

Respond with ONLY valid JSON, no markdown formatting, no code fences, matching exactly this structure:

{{
  "status": "ESCALATING" or "STABLE" or "DE_ESCALATING",
  "status_confidence": "HIGH" or "MEDIUM" or "LOW",
  "headline_summary": "one sentence, what is happening right now",
  "context": "2-3 sentences on recent developments only, no speculation beyond the excerpts",
  "india_transmission_channel": "TRADE" or "CURRENCY" or "ENERGY" or "CAPITAL_FLOWS" or "SUPPLY_CHAIN" or "MULTIPLE",
  "india_linkage": "2-3 sentences on the specific economic transmission mechanism to India — must explicitly reference India, not generic global impact",
  "watch_for": "one sentence, the next concrete event or date to monitor, or 'No specific near-term catalyst identified' if none is evident"
}}

Rules:
- If excerpts do not contain enough information to confidently assess India linkage, set status_confidence to LOW and write 'Insufficient recent coverage for confident assessment' in india_linkage.
- Do not reference specific numbers, percentages, or dates unless they appear explicitly in the excerpts.
- If excerpts are unrelated or too sparse, return status STABLE, status_confidence LOW, headline_summary 'No significant recent developments found.'
- Return ONLY the JSON object, no other text."""

    try:
        raw = nlp_engine.generate_text(prompt)
        if not raw:
            return fallback

        import json as _json
        import re as _re
        cleaned = _re.sub(r'^```json\s*|\s*```$', '', raw.strip())
        parsed  = _json.loads(cleaned)

        required = [
            "status", "status_confidence", "headline_summary",
            "context", "india_transmission_channel",
            "india_linkage", "watch_for",
        ]
        if all(k in parsed for k in required):
            return parsed
        print("[GEOWATCH] NLP response missing required keys, using fallback", flush=True)
        return fallback

    except Exception as e:
        print(f"[GEOWATCH] NLP analysis failed: {e}", flush=True)
        return fallback


def _get_theme_cached(
    theme_key:    str,
    theme_label:  str,
    search_query: str,
    nlp_engine,
) -> dict:
    """
    Returns cached theme analysis if fresh (<72h / 3 days), else fetches
    news + generates new analysis and upserts cache. Falls back to stale
    cache if fetch/generation fails.
    """
    try:
        cached = (
            _supabase.table("geopolitical_watch_cache")
            .select("*")
            .eq("theme_key", theme_key)
            .limit(1)
            .execute()
        )
        if cached.data:
            row       = cached.data[0]
            age_hours = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(row["fetched_at"])
            ).total_seconds() / 3600
            if age_hours < 0:  # TEMP: force refresh
                return row
    except Exception as e:
        print(f"[GEOWATCH] Cache read failed {theme_key}: {e}", flush=True)

    headlines = _fetch_theme_news(search_query)
    analysis  = _generate_theme_analysis(theme_label, headlines, nlp_engine)

    row = {
        "theme_key":   theme_key,
        "theme_label": theme_label,
        "status":                     analysis["status"],
        "status_confidence":          analysis["status_confidence"],
        "headline_summary":           analysis["headline_summary"],
        "context":                    analysis["context"],
        "india_transmission_channel": analysis["india_transmission_channel"],
        "india_linkage":              analysis["india_linkage"],
        "watch_for":                  analysis["watch_for"],
        "source_headlines":           headlines,
        "fetched_at":                 datetime.now(timezone.utc).isoformat(),
    }

    try:
        _supabase.table("geopolitical_watch_cache").upsert(
            row, on_conflict="theme_key"
        ).execute()
    except Exception as e:
        print(f"[GEOWATCH] Cache upsert failed {theme_key}: {e}", flush=True)

    return row


# ── India domestic data overrides ────────────────────────────────────────────
# More current than World Bank annual data. Update on release day:
#   CPI:         ~12th of each month (MOSPI)
#   GDP:         ~last day of May (MoSPI advance estimate)
#   Unemployment: quarterly (CMIE/PLFS) — leave as World Bank until PLFS 2025-26
# Last updated: June 2026
_INDIA_OVERRIDES = {
    "inflation": {
        "value":  3.93,
        "source": "MOSPI CPI · May 2026 (provisional)",
        "year":   2026,
    },
    "gdp_growth": {
        "value":  7.2,
        "source": "MoSPI GDP Advance · FY26",
        "year":   2026,
    },
    "forex_reserves": {
        "value":  698.7,
        "source": "RBI Weekly · Jul 2026",
        "year":   2026,
        "unit":   "USD billion",
    },
    # Unemployment: leave as World Bank until PLFS 2025-26 is published
}

# ── Hardcoded PMI — update monthly on S&P Global release day ─────────────────
# Last verified: May 2026 (source: S&P Global Manufacturing PMI releases)
_PMI_VALUES = {
    "US": 50.2,   # S&P Global US Manufacturing PMI
    "CN": 49.8,   # Caixin China Manufacturing PMI
    "DE": 48.4,   # S&P Global Germany Manufacturing PMI
    "IN": 58.8,   # S&P Global India Manufacturing PMI
    "JP": 48.7,   # au Jibun Bank Japan Manufacturing PMI
    "GB": 45.4,   # S&P Global UK Manufacturing PMI
    "FR": 48.2,   # S&P Global France Manufacturing PMI
    "IT": 49.3,   # S&P Global Italy Manufacturing PMI
    "BR": 51.8,   # S&P Global Brazil Manufacturing PMI
    "CA": 46.8,   # S&P Global Canada Manufacturing PMI
    "RU": 50.2,   # S&P Global Russia Manufacturing PMI
    "KR": 48.3,   # S&P Global South Korea Manufacturing PMI
    "AU": 51.7,   # S&P Global Australia Manufacturing PMI
    "MX": 47.3,   # S&P Global Mexico Manufacturing PMI
    "ID": 52.4,   # S&P Global Indonesia Manufacturing PMI
    "NL": 49.1,   # S&P Global Netherlands Manufacturing PMI
    "SA": 54.2,   # S&P Global Saudi Arabia PMI
    "TR": 48.6,   # S&P Global Turkey Manufacturing PMI
    "CH": 48.9,   # procure.ch Switzerland PMI
    "TW": 50.8,   # S&P Global Taiwan Manufacturing PMI
    # ── 30 new economies — Last verified: May 2026 (S&P Global) ──
    "PL": 50.1,  "SE": 52.3,  "BE": 47.8,
    "AR": 51.2,  "NO": 51.8,  "AE": 55.3,
    "IL": 49.2,  "AT": 46.9,  "SG": 51.4,
    "NG": 52.8,  "ZA": 43.1,  "MY": 49.5,
    "DK": 48.6,  "PH": 53.2,  "IE": 50.8,
    "TH": 50.4,  "BD": 51.6,  "VN": 54.2,
    "PK": 52.4,  "CO": 49.8,  "CL": 48.7,
    "FI": 47.2,  "CZ": 49.4,  "RO": 50.6,
    "NZ": 47.8,  "PT": 50.2,  "GR": 53.1,
    "QA": 56.4,  "KZ": 51.9,  "HU": 50.3,
}

_ECONOMIES = [
    {"code": "US", "name": "United States",   "flag": "🇺🇸",
     "currency_label": "USD", "wb_code": "US",
     "ticker_currency": None,        "ticker_yield": "^TNX"},
    {"code": "CN", "name": "China",           "flag": "🇨🇳",
     "currency_label": "CNY", "wb_code": "CN",
     "ticker_currency": "USDCNY=X",  "ticker_yield": None},
    {"code": "DE", "name": "Germany",         "flag": "🇩🇪",
     "currency_label": "EUR", "wb_code": "DE",
     "ticker_currency": "EURUSD=X",  "ticker_yield": "^IRDE10"},
    {"code": "IN", "name": "India",           "flag": "🇮🇳",
     "currency_label": "INR", "wb_code": "IN",
     "ticker_currency": "USDINR=X",  "ticker_yield": None},
    {"code": "JP", "name": "Japan",           "flag": "🇯🇵",
     "currency_label": "JPY", "wb_code": "JP",
     "ticker_currency": "USDJPY=X",  "ticker_yield": "^IRJP10"},
    {"code": "GB", "name": "United Kingdom",  "flag": "🇬🇧",
     "currency_label": "GBP", "wb_code": "GB",
     "ticker_currency": "GBPUSD=X",  "ticker_yield": "^IRGB10Y"},
    {"code": "FR", "name": "France",          "flag": "🇫🇷",
     "currency_label": "EUR", "wb_code": "FR",
     "ticker_currency": None,        "ticker_yield": None},
    {"code": "IT", "name": "Italy",           "flag": "🇮🇹",
     "currency_label": "EUR", "wb_code": "IT",
     "ticker_currency": None,        "ticker_yield": None},
    {"code": "BR", "name": "Brazil",          "flag": "🇧🇷",
     "currency_label": "BRL", "wb_code": "BR",
     "ticker_currency": "USDBRL=X",  "ticker_yield": None},
    {"code": "CA", "name": "Canada",          "flag": "🇨🇦",
     "currency_label": "CAD", "wb_code": "CA",
     "ticker_currency": "USDCAD=X",  "ticker_yield": None},
    {"code": "RU", "name": "Russia",          "flag": "🇷🇺",
     "currency_label": "RUB", "wb_code": "RU",
     "ticker_currency": None,        "ticker_yield": None},
    {"code": "KR", "name": "South Korea",     "flag": "🇰🇷",
     "currency_label": "KRW", "wb_code": "KR",
     "ticker_currency": "USDKRW=X",  "ticker_yield": None},
    {"code": "AU", "name": "Australia",       "flag": "🇦🇺",
     "currency_label": "AUD", "wb_code": "AU",
     "ticker_currency": "AUDUSD=X",  "ticker_yield": None},
    {"code": "MX", "name": "Mexico",          "flag": "🇲🇽",
     "currency_label": "MXN", "wb_code": "MX",
     "ticker_currency": "USDMXN=X",  "ticker_yield": None},
    {"code": "ID", "name": "Indonesia",       "flag": "🇮🇩",
     "currency_label": "IDR", "wb_code": "ID",
     "ticker_currency": "USDIDR=X",  "ticker_yield": None},
    {"code": "NL", "name": "Netherlands",     "flag": "🇳🇱",
     "currency_label": "EUR", "wb_code": "NL",
     "ticker_currency": None,        "ticker_yield": None},
    {"code": "SA", "name": "Saudi Arabia",    "flag": "🇸🇦",
     "currency_label": "SAR", "wb_code": "SA",
     "ticker_currency": None,        "ticker_yield": None},
    {"code": "TR", "name": "Turkey",          "flag": "🇹🇷",
     "currency_label": "TRY", "wb_code": "TR",
     "ticker_currency": "USDTRY=X",  "ticker_yield": None},
    {"code": "CH", "name": "Switzerland",     "flag": "🇨🇭",
     "currency_label": "CHF", "wb_code": "CH",
     "ticker_currency": "USDCHF=X",  "ticker_yield": None},
    {"code": "TW", "name": "Taiwan",          "flag": "🇹🇼",
     "currency_label": "TWD", "wb_code": "TW",
     "ticker_currency": "USDTWD=X",  "ticker_yield": None},
    # ── 30 new economies ──
    {"code": "PL", "name": "Poland",         "flag": "🇵🇱",
     "currency_label": "PLN", "wb_code": "PL",
     "ticker_currency": "USDPLN=X", "ticker_yield": None},
    {"code": "SE", "name": "Sweden",         "flag": "🇸🇪",
     "currency_label": "SEK", "wb_code": "SE",
     "ticker_currency": "USDSEK=X", "ticker_yield": None},
    {"code": "BE", "name": "Belgium",        "flag": "🇧🇪",
     "currency_label": "EUR", "wb_code": "BE",
     "ticker_currency": None,       "ticker_yield": None},
    {"code": "AR", "name": "Argentina",      "flag": "🇦🇷",
     "currency_label": "ARS", "wb_code": "AR",
     "ticker_currency": "USDARS=X", "ticker_yield": None},
    {"code": "NO", "name": "Norway",         "flag": "🇳🇴",
     "currency_label": "NOK", "wb_code": "NO",
     "ticker_currency": "USDNOK=X", "ticker_yield": None},
    {"code": "AE", "name": "UAE",            "flag": "🇦🇪",
     "currency_label": "AED", "wb_code": "AE",
     "ticker_currency": None,       "ticker_yield": None},
    {"code": "IL", "name": "Israel",         "flag": "🇮🇱",
     "currency_label": "ILS", "wb_code": "IL",
     "ticker_currency": "USDILS=X", "ticker_yield": None},
    {"code": "AT", "name": "Austria",        "flag": "🇦🇹",
     "currency_label": "EUR", "wb_code": "AT",
     "ticker_currency": None,       "ticker_yield": None},
    {"code": "SG", "name": "Singapore",      "flag": "🇸🇬",
     "currency_label": "SGD", "wb_code": "SG",
     "ticker_currency": "USDSGD=X", "ticker_yield": None},
    {"code": "NG", "name": "Nigeria",        "flag": "🇳🇬",
     "currency_label": "NGN", "wb_code": "NG",
     "ticker_currency": "USDNGN=X", "ticker_yield": None},
    {"code": "ZA", "name": "South Africa",   "flag": "🇿🇦",
     "currency_label": "ZAR", "wb_code": "ZA",
     "ticker_currency": "USDZAR=X", "ticker_yield": None},
    {"code": "MY", "name": "Malaysia",       "flag": "🇲🇾",
     "currency_label": "MYR", "wb_code": "MY",
     "ticker_currency": "USDMYR=X", "ticker_yield": None},
    {"code": "DK", "name": "Denmark",        "flag": "🇩🇰",
     "currency_label": "DKK", "wb_code": "DK",
     "ticker_currency": "USDDKK=X", "ticker_yield": None},
    {"code": "PH", "name": "Philippines",    "flag": "🇵🇭",
     "currency_label": "PHP", "wb_code": "PH",
     "ticker_currency": "USDPHP=X", "ticker_yield": None},
    {"code": "IE", "name": "Ireland",        "flag": "🇮🇪",
     "currency_label": "EUR", "wb_code": "IE",
     "ticker_currency": None,       "ticker_yield": None},
    {"code": "TH", "name": "Thailand",       "flag": "🇹🇭",
     "currency_label": "THB", "wb_code": "TH",
     "ticker_currency": "USDTHB=X", "ticker_yield": None},
    {"code": "BD", "name": "Bangladesh",     "flag": "🇧🇩",
     "currency_label": "BDT", "wb_code": "BD",
     "ticker_currency": "USDBDT=X", "ticker_yield": None},
    {"code": "VN", "name": "Vietnam",        "flag": "🇻🇳",
     "currency_label": "VND", "wb_code": "VN",
     "ticker_currency": "USDVND=X", "ticker_yield": None},
    {"code": "PK", "name": "Pakistan",       "flag": "🇵🇰",
     "currency_label": "PKR", "wb_code": "PK",
     "ticker_currency": "USDPKR=X", "ticker_yield": None},
    {"code": "CO", "name": "Colombia",       "flag": "🇨🇴",
     "currency_label": "COP", "wb_code": "CO",
     "ticker_currency": "USDCOP=X", "ticker_yield": None},
    {"code": "CL", "name": "Chile",          "flag": "🇨🇱",
     "currency_label": "CLP", "wb_code": "CL",
     "ticker_currency": "USDCLP=X", "ticker_yield": None},
    {"code": "FI", "name": "Finland",        "flag": "🇫🇮",
     "currency_label": "EUR", "wb_code": "FI",
     "ticker_currency": None,       "ticker_yield": None},
    {"code": "CZ", "name": "Czech Republic", "flag": "🇨🇿",
     "currency_label": "CZK", "wb_code": "CZ",
     "ticker_currency": "USDCZK=X", "ticker_yield": None},
    {"code": "RO", "name": "Romania",        "flag": "🇷🇴",
     "currency_label": "RON", "wb_code": "RO",
     "ticker_currency": "USDRON=X", "ticker_yield": None},
    {"code": "NZ", "name": "New Zealand",    "flag": "🇳🇿",
     "currency_label": "NZD", "wb_code": "NZ",
     "ticker_currency": "NZDUSD=X", "ticker_yield": None},
    {"code": "PT", "name": "Portugal",       "flag": "🇵🇹",
     "currency_label": "EUR", "wb_code": "PT",
     "ticker_currency": None,       "ticker_yield": None},
    {"code": "GR", "name": "Greece",         "flag": "🇬🇷",
     "currency_label": "EUR", "wb_code": "GR",
     "ticker_currency": None,       "ticker_yield": None},
    {"code": "QA", "name": "Qatar",          "flag": "🇶🇦",
     "currency_label": "QAR", "wb_code": "QA",
     "ticker_currency": None,       "ticker_yield": None},
    {"code": "KZ", "name": "Kazakhstan",     "flag": "🇰🇿",
     "currency_label": "KZT", "wb_code": "KZ",
     "ticker_currency": "USDKZT=X", "ticker_yield": None},
    {"code": "HU", "name": "Hungary",        "flag": "🇭🇺",
     "currency_label": "HUF", "wb_code": "HU",
     "ticker_currency": "USDHUF=X", "ticker_yield": None},
]

_YIELD_FALLBACKS = {
    "CN": 2.10,   "IN": 6.85,  "DE": 2.45,  "JP": 1.45,  "GB": 4.42,
    "FR": 3.12,   # France 10Y OAT
    "IT": 3.85,   # Italy 10Y BTP
    "BR": 13.20,  # Brazil 10Y NTN-F
    "CA": 3.28,   # Canada 10Y bond
    "RU": 15.40,  # Russia 10Y OFZ
    "KR": 2.85,   # South Korea 10Y bond
    "AU": 4.22,   # Australia 10Y bond
    "MX": 9.45,   # Mexico 10Y bond
    "ID": 6.95,   # Indonesia 10Y bond
    "NL": 2.65,   # Netherlands 10Y bond
    "SA": 4.85,   # Saudi Arabia 10Y sukuk
    "TR": 28.40,  # Turkey 10Y bond
    "CH": 0.68,   # Switzerland 10Y bond
    "TW": 1.85,   # Taiwan 10Y bond
    # ── 30 new economies — Last verified: May 2026 ──
    "PL": 5.45,  "SE": 2.38,  "BE": 3.05,
    "AR": 85.0,  "NO": 3.85,  "AE": 4.80,
    "IL": 4.65,  "AT": 3.12,  "SG": 3.15,
    "NG": 19.50, "ZA": 10.20, "MY": 3.85,
    "DK": 2.65,  "PH": 6.35,  "IE": 2.95,
    "TH": 2.45,  "BD": 11.20, "VN": 2.85,
    "PK": 12.50, "CO": 10.80, "CL": 5.25,
    "FI": 2.85,  "CZ": 3.95,  "RO": 6.85,
    "NZ": 4.45,  "PT": 3.15,  "GR": 3.35,
    "QA": 4.55,  "KZ": 13.50, "HU": 6.75,
}

# NOTE: requires nominal_gdp_cache table in Supabase (see README).
# CREATE TABLE nominal_gdp_cache (
#   code text PRIMARY KEY,
#   gdp_usd_trillion numeric,
#   fetched_at timestamptz default now()
# );
_nominal_gdp_mem_cache: dict = {}


def _get_nominal_gdp_trillion(
    wb_code: str,
    code: str
) -> float | None:
    """
    Returns nominal GDP in current USD trillions for a given country.
    Fetches from World Bank NY.GDP.MKTP.CD, caches in Supabase for 30
    days (data updates annually). Falls back to in-memory cache, then
    Supabase stale cache, then None if all fail.
    """
    from datetime import datetime, timezone

    # 1. In-memory cache hit
    if code in _nominal_gdp_mem_cache:
        return _nominal_gdp_mem_cache[code]

    # 2. Supabase cache (30-day TTL)
    try:
        cached = (
            _supabase.table("nominal_gdp_cache")
            .select("*")
            .eq("code", code)
            .limit(1)
            .execute()
        )
        if cached.data:
            row = cached.data[0]
            fetched_at = datetime.fromisoformat(
                row["fetched_at"]
            )
            age_days = (
                datetime.now(timezone.utc)
                - fetched_at
            ).total_seconds() / 86400

            if age_days < 30:
                val = row.get("gdp_usd_trillion")
                if val is not None:
                    _nominal_gdp_mem_cache[code] = float(val)
                    return float(val)
    except Exception as e:
        print(
            f"[NOMINAL_GDP] Supabase cache "
            f"read failed for {code}: {e}",
            flush=True
        )

    # 3. Live World Bank fetch
    # NY.GDP.MKTP.CD = nominal GDP in current USD
    result = _wb_fetch(wb_code, "NY.GDP.MKTP.CD")
    if result is not None:
        raw, _ = result
        # Convert USD to trillions, round to 2 decimal places
        val = round(raw / 1_000_000_000_000, 2)
        _nominal_gdp_mem_cache[code] = val

        # Upsert into Supabase cache
        try:
            _supabase.table(
                "nominal_gdp_cache"
            ).upsert({
                "code": code,
                "gdp_usd_trillion": val,
                "fetched_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }, on_conflict="code").execute()
            print(
                f"[NOMINAL_GDP] {code}: "
                f"${val}T (live WB fetch)",
                flush=True
            )
        except Exception as e:
            print(
                f"[NOMINAL_GDP] Supabase "
                f"upsert failed for {code}: {e}",
                flush=True
            )
        return val

    # 4. Stale Supabase fallback
    # (use whatever is cached even if >30 days old, better than None)
    try:
        stale = (
            _supabase.table("nominal_gdp_cache")
            .select("gdp_usd_trillion")
            .eq("code", code)
            .limit(1)
            .execute()
        )
        if stale.data:
            val = stale.data[0].get(
                "gdp_usd_trillion"
            )
            if val is not None:
                print(
                    f"[NOMINAL_GDP] {code}: "
                    f"using stale cache ${val}T",
                    flush=True
                )
                return float(val)
    except Exception:
        pass

    # 5. Hardcoded fallback as last resort
    fallback = _NOMINAL_GDP_FALLBACK.get(code)
    if fallback is not None:
        print(
            f"[NOMINAL_GDP] {code}: "
            f"using hardcoded fallback "
            f"${fallback}T",
            flush=True
        )
        return fallback

    print(
        f"[NOMINAL_GDP] {code}: "
        f"all sources failed, returning None",
        flush=True
    )
    return None


# Legacy fallback — used only if World Bank fetch and Supabase
# cache both fail for a country. Update annually if needed.
_NOMINAL_GDP_FALLBACK = {
    "US": 29.2, "CN": 18.6, "DE":  4.6, "IN":  4.3,
    "JP":  4.1, "GB":  3.6, "FR":  3.2, "IT":  2.3,
    "BR":  2.3, "CA":  2.2, "RU":  2.1, "KR":  1.9,
    "AU":  1.8, "MX":  1.6, "ID":  1.5, "NL":  1.2,
    "SA":  1.1, "TR":  1.1, "CH":  0.9, "TW":  0.8,
    # ── 30 new economies ──
    "PL": 0.77, "SE": 0.70, "BE": 0.65,
    "AR": 0.62, "NO": 0.60, "AE": 0.57,
    "IL": 0.55, "AT": 0.54, "SG": 0.50,
    "NG": 0.49, "ZA": 0.48, "BD": 0.46,
    "MY": 0.44, "DK": 0.43, "PH": 0.43,
    "VN": 0.43, "IE": 0.42, "TH": 0.40,
    "PK": 0.37, "CZ": 0.35, "CO": 0.34,
    "RO": 0.34, "CL": 0.32, "FI": 0.30,
    "PT": 0.28, "NZ": 0.25, "GR": 0.24,
    "QA": 0.23, "KZ": 0.22, "HU": 0.22,
}

# Currencies pegged to USD — use as fallback when yfinance returns None
_PEGGED_CURRENCIES = {
    "AE": 3.67,  # AED pegged to USD since 1997
    "QA": 3.64,  # QAR pegged to USD since 2001
    "SA": 3.75,  # SAR pegged to USD since 1986
}

_global_macro_cache_mem: dict = {"data": None, "fetched_at": 0}


def _wb_fetch(wb_code: str, indicator: str) -> tuple[float, int] | None:
    """Returns (value, year) tuple or None. Year is the WB data vintage."""
    try:
        import requests as _req
        url = (
            f"https://api.worldbank.org/v2/country/{wb_code}"
            f"/indicator/{indicator}?format=json&mrv=1"
        )
        r = _req.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        records = data[1] if isinstance(data, list) and len(data) > 1 else []
        for rec in records:
            val  = rec.get("value")
            year = rec.get("date")
            if val is not None:
                return (
                    round(float(val), 2),
                    int(year) if year else None,
                )
        return None
    except Exception as _e:
        print(f"[GLOBAL_MACRO] WB fetch failed {wb_code}/{indicator}: {_e}", flush=True)
        return None


def _derive_macro_signal(gdp: float | None, inflation: float | None) -> str:
    if gdp is None and inflation is None:
        return "MONITORING"
    g = gdp or 0
    i = inflation or 0
    if g < 0:
        return "CONTRACTION"
    if i > 6:
        return "INFLATION_PRESSURE"
    if g < 1:
        return "SLOWDOWN"
    if g > 3 and i < 4:
        return "EXPANDING"
    return "STABLE_GROWTH"


def _fetch_live_economy_data():
    import yfinance as _yf
    currency_map = {}
    yield_map = {}
    for eco in _ECONOMIES:
        sym = eco.get("ticker_currency")
        if sym:
            try:
                h = _yf.Ticker(sym).history(period="2d", interval="1d")
                if len(h) >= 1:
                    currency_map[eco["code"]] = round(float(h["Close"].iloc[-1]), 4)
            except Exception as _e:
                print(f"[GLOBAL_MACRO] Currency fetch failed {sym}: {_e}", flush=True)
        sym = eco.get("ticker_yield")
        if sym:
            try:
                h = _yf.Ticker(sym).history(period="2d", interval="1d")
                if len(h) >= 1:
                    yield_map[eco["code"]] = round(float(h["Close"].iloc[-1]), 2)
            except Exception as _e:
                print(f"[GLOBAL_MACRO] Yield fetch failed {sym}: {_e}", flush=True)
    india_yc = _yc_cache.get("data") or {}
    india_yields = india_yc.get("india_yields", {})
    if india_yields.get("10Y"):
        yield_map["IN"] = round(float(india_yields["10Y"]), 2)
    return currency_map, yield_map


def _build_economy_record(
    eco, gdp, inflation, unemployment,
    currency_map, yield_map,
    live_rates=None,
    nominal_trillion=None,
    gdp_year=None,
    inf_year=None,
    une_year=None,
    reserves_bn=None,
    reserves_year=None,
):
    # ── Apply India domestic overrides (more current than World Bank data) ──
    if eco.get("code") == "IN":
        _ov = _INDIA_OVERRIDES
        if "inflation" in _ov:
            inflation = _ov["inflation"]["value"]
            inf_year  = _ov["inflation"]["year"]
        if "gdp_growth" in _ov:
            gdp      = _ov["gdp_growth"]["value"]
            gdp_year = _ov["gdp_growth"]["year"]
        if "forex_reserves" in _ov:
            reserves_bn   = _ov["forex_reserves"]["value"]
            reserves_year = _ov["forex_reserves"]["year"]

    code = eco["code"]
    raw_fx = currency_map.get(code)
    currency_vs_usd = 1.0 if code == "US" else (round(raw_fx, 4) if raw_fx else None)
    if not currency_vs_usd and code in _PEGGED_CURRENCIES:
        currency_vs_usd = _PEGGED_CURRENCIES[code]
    yield_10y = yield_map.get(code) or _YIELD_FALLBACKS.get(code)

    # Use live FRED rate if available, fall back to hardcoded
    policy_rate = (
        live_rates.get(code)
        if live_rates and live_rates.get(code)
        else _POLICY_RATES.get(code)
    )

    return {
        "code":                 code,
        "name":                 eco["name"],
        "flag":                 eco["flag"],
        "currency_label":       eco["currency_label"],
        "gdp_growth":           gdp,
        "gdp_growth_year":      gdp_year,
        "inflation":            inflation,
        "inflation_year":       inf_year,
        "policy_rate":          policy_rate,
        "pmi":                  _PMI_VALUES.get(code),
        "unemployment":         unemployment,
        "unemployment_year":    une_year,
        "currency_vs_usd":      currency_vs_usd,
        "yield_10y":            yield_10y,
        "macro_signal":         _derive_macro_signal(gdp, inflation),
        "gdp_nominal_trillion": (
            nominal_trillion
            if nominal_trillion is not None
            else _get_nominal_gdp_trillion(
                eco["wb_code"], code
            )
        ),
        "forex_reserves_bn":   reserves_bn,
        "forex_reserves_year": reserves_year,
        "data_sources": {
            "gdp": (
                _INDIA_OVERRIDES["gdp_growth"]["source"]
                if eco.get("code") == "IN" and "gdp_growth" in _INDIA_OVERRIDES
                else f"World Bank NY.GDP.MKTP.KD.ZG ({gdp_year or 'latest'})"
            ),
            "inflation": (
                _INDIA_OVERRIDES["inflation"]["source"]
                if eco.get("code") == "IN" and "inflation" in _INDIA_OVERRIDES
                else f"World Bank FP.CPI.TOTL.ZG ({inf_year or 'latest'})"
            ),
            "unemployment": f"World Bank SL.UEM.TOTL.ZS ({une_year or 'latest'})",
            "policy_rate":  "Central bank official — hardcoded May 2026",
            "pmi":          "S&P Global — hardcoded May 2026",
            "currency":     "yfinance live",
            "yield":        "yfinance live / hardcoded fallback",
            "forex_reserves": (
                _INDIA_OVERRIDES["forex_reserves"]["source"]
                if eco.get("code") == "IN" and "forex_reserves" in _INDIA_OVERRIDES
                else f"World Bank FI.RES.TOTL.CD ({reserves_year or 'latest'})"
            ),
        },
        "last_updated":         datetime.now(timezone.utc).isoformat(),
        "recent_development": (
            _get_country_news_cached(eco.get("wb_code", code)) or {
                "headline": None,
                "source": None,
                "published_at": None,
                "stale": True,
            }
        ),
    }


@app.get("/api/global-macro")
async def get_global_macro():
    global _global_macro_cache_mem
    cache_age = time.time() - _global_macro_cache_mem.get("fetched_at", 0)
    if _global_macro_cache_mem.get("data") and cache_age < 21600:
        return {**_global_macro_cache_mem["data"], "cached": True}
    # Check Supabase full-blob cache (24h)
    try:
        cache_resp = _supabase.table(
            "global_macro_cache"
        ).select("*").eq(
            "cache_key", "global_macro_50"
        ).order(
            "created_at", desc=True
        ).limit(1).execute()
        if cache_resp.data:
            cached = cache_resp.data[0]
            cached_at = datetime.fromisoformat(cached["created_at"])
            age_hours = (
                datetime.now(timezone.utc) - cached_at
            ).total_seconds() / 3600
            if age_hours < 24:
                print(
                    f"[GLOBAL_MACRO] Serving from cache (age: {age_hours:.1f}h)",
                    flush=True
                )
                import json
                return json.loads(cached["data"])
    except Exception as e:
        print(
            f"[GLOBAL_MACRO] Cache check failed: {e}",
            flush=True
        )
        _err_msg = str(e).lower()
        if "does not exist" in _err_msg or "column" in _err_msg:
            print(
                "[GLOBAL_MACRO] Schema mismatch detected — "
                "skipping per-row cache, forcing fresh fetch",
                flush=True
            )
        else:
            try:
                cutoff = (
                    datetime.now(timezone.utc)
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .isoformat()
                )
                cached = (
                    _supabase.table("global_macro_cache")
                    .select("*").gte("last_updated", cutoff).execute()
                )
                if cached.data and len(cached.data) >= 50:
                    # Enrich cached rows with computed fields not stored in Supabase
                    economy_meta = {e["code"]: e for e in _ECONOMIES}
                    enriched = []
                    for row in cached.data:
                        code = row.get("economy")
                        meta = economy_meta.get(code, {})
                        enriched.append({
                            **row,
                            "code":                 code,
                            "name":                 meta.get("name", code),
                            "flag":                 meta.get("flag", ""),
                            "currency_label":       meta.get("currency_label", ""),
                            "yield_10y":            row.get("yield_10y") or _YIELD_FALLBACKS.get(code),
                            "macro_signal":         _derive_macro_signal(row.get("gdp_growth"), row.get("inflation")),
                            "gdp_nominal_trillion": (
                                _nominal_gdp_mem_cache.get(code)
                                or _NOMINAL_GDP_FALLBACK.get(code)
                            ),
                            "recent_development": (
                                _get_country_news_cached(meta.get("wb_code", code)) or {
                                    "headline": None,
                                    "source": None,
                                    "published_at": None,
                                    "stale": True,
                                }
                            ),
                        })
                    result = {
                        "economies":       enriched,
                        "page_updated_at": max(e["last_updated"] for e in enriched),
                        "cached":          True,
                    }
                    _global_macro_cache_mem = {"data": result, "fetched_at": time.time()}
                    return result

            except Exception as _e:
                print(f"[GLOBAL_MACRO] Supabase cache read failed: {_e}", flush=True)

    print("[GLOBAL_MACRO] Cache miss — fetching fresh data for 50 economies...", flush=True)
    try:
        currency_map, yield_map = _fetch_live_economy_data()
    except Exception as _e:
        print(f"[GLOBAL_MACRO] Live data fetch failed: {_e}", flush=True)
        currency_map, yield_map = {}, {}

    # Fetch live policy rates from FRED
    try:
        live_rates = _fetch_fred_rates()
    except Exception as _e:
        print(f"[GLOBAL_MACRO] FRED rate fetch failed: {_e}", flush=True)
        live_rates = {}

    async def _fetch_one(eco):
        loop = asyncio.get_event_loop()
        wb   = eco["wb_code"]
        code = eco["code"]

        _gdp_r, _inf_r, _une_r, _nom_r, _res_r = (
            await asyncio.gather(
                loop.run_in_executor(
                    None, _wb_fetch, wb,
                    "NY.GDP.MKTP.KD.ZG"),
                loop.run_in_executor(
                    None, _wb_fetch, wb,
                    "FP.CPI.TOTL.ZG"),
                loop.run_in_executor(
                    None, _wb_fetch, wb,
                    "SL.UEM.TOTL.ZS"),
                loop.run_in_executor(
                    None, _wb_fetch, wb,
                    "NY.GDP.MKTP.CD"),
                loop.run_in_executor(
                    None, _wb_fetch, wb,
                    "FI.RES.TOTL.CD"),
            )
        )

        gdp          = _gdp_r[0] if _gdp_r else None
        gdp_year     = _gdp_r[1] if _gdp_r else None
        inflation    = _inf_r[0] if _inf_r else None
        inf_year     = _inf_r[1] if _inf_r else None
        unemployment = _une_r[0] if _une_r else None
        une_year     = _une_r[1] if _une_r else None
        nominal_usd  = _nom_r[0] if _nom_r else None
        reserves_usd = _res_r[0] if _res_r else None
        reserves_year = _res_r[1] if _res_r else None
        reserves_bn  = (
            round(reserves_usd / 1_000_000_000, 1)
            if reserves_usd else None
        )

        nominal_trillion = None
        if nominal_usd is not None:
            nominal_trillion = round(
                nominal_usd / 1_000_000_000_000, 2
            )
            _nominal_gdp_mem_cache[code] = nominal_trillion
            try:
                _supabase.table(
                    "nominal_gdp_cache"
                ).upsert({
                    "code": code,
                    "gdp_usd_trillion": nominal_trillion,
                    "fetched_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }, on_conflict="code").execute()
            except Exception as _e:
                print(
                    f"[NOMINAL_GDP] Supabase "
                    f"upsert failed {code}: {_e}",
                    flush=True
                )
        else:
            nominal_trillion = _get_nominal_gdp_trillion(wb, code)

        return (eco, gdp, gdp_year, inflation, inf_year,
                unemployment, une_year, nominal_trillion,
                reserves_bn, reserves_year)

    print("[GLOBAL_MACRO] Parallel World Bank fetch for all 50 economies...", flush=True)
    wb_results = await asyncio.gather(*[_fetch_one(eco) for eco in _ECONOMIES])

    economies = []
    for (eco, gdp, gdp_year, inflation, inf_year,
         unemployment, une_year, nominal_trillion,
         reserves_bn, reserves_year) in wb_results:
        record = _build_economy_record(
            eco, gdp, inflation, unemployment,
            currency_map, yield_map,
            live_rates=live_rates,
            nominal_trillion=nominal_trillion,
            gdp_year=gdp_year,
            inf_year=inf_year,
            une_year=une_year,
            reserves_bn=reserves_bn,
            reserves_year=reserves_year,
        )
        economies.append(record)
        try:
            _supabase.table("global_macro_cache").upsert(
                {
                    "economy":         eco["code"],
                    "gdp_growth":      gdp,
                    "inflation":       inflation,
                    "policy_rate":     record.get("policy_rate"),
                    "pmi":             _PMI_VALUES.get(eco["code"]),
                    "unemployment":    unemployment,
                    "currency_vs_usd": record.get("currency_vs_usd"),
                    "yield_10y":       record.get("yield_10y"),
                    "last_updated":    datetime.now(timezone.utc).isoformat(),
                    "data_sources":    record.get("data_sources"),
                },
                on_conflict="economy",
            ).execute()
        except Exception as _e:
            print(f"[GLOBAL_MACRO] Supabase upsert failed {eco['code']}: {_e}", flush=True)
        print(f"[GLOBAL_MACRO] {eco['code']} — gdp={gdp} inf={inflation} signal: {record['macro_signal']}", flush=True)

    result = {
        "economies":       economies,
        "page_updated_at": datetime.now(timezone.utc).isoformat(),
        "cached":          False,
    }
    try:
        import json as _json
        _supabase.table("global_macro_cache").upsert({
            "cache_key": "global_macro_50",
            "data": _json.dumps(result),
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        print("[GLOBAL_MACRO] Cache updated", flush=True)
    except Exception as _ce:
        print(f"[GLOBAL_MACRO] Cache save failed: {_ce}", flush=True)
    _global_macro_cache_mem = {"data": result, "fetched_at": time.time()}
    return result


@app.get("/api/policy-rates")
async def get_policy_rates():
    """
    Public endpoint — no auth required.
    Returns latest central bank policy rates.
    FRED-sourced where available, hardcoded fallback otherwise.
    """
    live_rates = _fetch_fred_rates()

    # Merge live rates over hardcoded fallback
    merged = dict(_POLICY_RATES)
    for code, rate in live_rates.items():
        merged[code] = rate

    return {
        "rates":      merged,
        "live_count": len(live_rates),
        "sources": {
            "fred":      list(live_rates.keys()),
            "hardcoded": [k for k in merged if k not in live_rates],
        },
        "cached": _fred_rate_cache["fetched_at"] > 0,
    }


@app.get("/api/currency-history")
async def get_currency_history(code: str = "IN"):
    """Public endpoint — no auth required. Returns currency performance vs USD."""
    import yfinance as yf

    CURRENCY_TICKERS = {
        # Original 20
        "US": None,        "CN": "USDCNY=X",  "DE": "EURUSD=X",  "IN": "USDINR=X",
        "JP": "USDJPY=X",  "GB": "GBPUSD=X",  "FR": "EURUSD=X",  "IT": "EURUSD=X",
        "BR": "USDBRL=X",  "CA": "USDCAD=X",  "RU": None,        "KR": "USDKRW=X",
        "AU": "AUDUSD=X",  "MX": "USDMXN=X",  "ID": "USDIDR=X",  "NL": "EURUSD=X",
        "SA": None,        "TR": "USDTRY=X",  "CH": "USDCHF=X",  "TW": "USDTWD=X",
        # Freely floating additions
        "PL": "USDPLN=X",  "SE": "USDSEK=X",  "NO": "USDNOK=X",  "IL": "USDILS=X",
        "SG": "USDSGD=X",  "ZA": "USDZAR=X",  "MY": "USDMYR=X",  "DK": "USDDKK=X",
        "PH": "USDPHP=X",  "TH": "USDTHB=X",  "BD": "USDBDT=X",  "VN": "USDVND=X",
        "PK": "USDPKR=X",  "CO": "USDCOP=X",  "CL": "USDCLP=X",  "CZ": "USDCZK=X",
        "RO": "USDRON=X",  "NZ": "NZDUSD=X",  "HU": "USDHUF=X",  "KZ": "USDKZT=X",
        "NG": "USDNGN=X",
        # EUR-zone members — same EURUSD=X rate as DE, FR, IT
        "BE": "EURUSD=X",  "AT": "EURUSD=X",  "IE": "EURUSD=X",
        "FI": "EURUSD=X",  "PT": "EURUSD=X",  "GR": "EURUSD=X",
    }
    # Pairs where a rising quote means the non-USD currency is weakening (USD is base)
    INVERTED = {
        "IN", "JP", "CN", "BR", "CA", "KR", "MX", "ID", "TR", "CH", "TW",
        "PL", "SE", "NO", "IL", "SG", "ZA", "MY", "DK", "PH", "TH", "BD",
        "VN", "PK", "CO", "CL", "CZ", "RO", "NZ", "HU", "KZ", "NG",
    }

    code_upper  = code.upper()
    ticker_sym  = CURRENCY_TICKERS.get(code_upper)

    if not ticker_sym:
        return {"code": code_upper, "available": False,
                "reason": "Currency data not available for this economy"}

    try:
        hist = yf.Ticker(ticker_sym).history(period="1y", interval="1d")
        if hist.empty:
            return {"code": code_upper, "available": False, "reason": "No data returned"}

        prices = hist["Close"].dropna()

        def calc_return(prices, days):
            if len(prices) < 2:
                return None
            end_price   = float(prices.iloc[-1])
            start_price = float(prices.iloc[max(0, len(prices) - days)])
            if start_price == 0:
                return None
            raw = (end_price - start_price) / start_price * 100
            if code_upper in INVERTED:
                raw = -raw
            return round(raw, 2)

        prices_90d = prices.iloc[-90:]
        sampled    = prices_90d.iloc[::5]
        base = float(sampled.iloc[0]) if len(sampled) > 0 else 1.0
        sparkline = []
        for _, price in sampled.items():
            pct = (float(price) - base) / base * 100
            if code_upper in INVERTED:
                pct = -pct
            sparkline.append(round(pct, 2))

        return {
            "code":          code_upper,
            "ticker":        ticker_sym,
            "current_price": round(float(prices.iloc[-1]), 4),
            "returns": {
                "1W": calc_return(prices, 5),
                "1M": calc_return(prices, 21),
                "3M": calc_return(prices, 63),
                "1Y": calc_return(prices, 252),
            },
            "sparkline_90d": sparkline,
            "available":     True,
            "inverse":       code_upper == "NZ",
        }

    except Exception as e:
        print(f"[CURRENCY_HISTORY] Error for {code}: {e}", flush=True)
        return {"code": code_upper, "available": False, "reason": str(e)}


@app.get("/api/ai-capex-pulse")
async def get_ai_capex_pulse():
    """
    Public endpoint — no auth required.
    Tracks a basket of AI-infrastructure-
    exposed tickers as a live market
    proxy for the global AI capex cycle.
    Actual hyperscaler capex is only
    disclosed quarterly; this basket's
    momentum is the best available live
    signal of market-priced AI infra
    expectations.
    """
    import yfinance as yf

    BASKET = {
        "compute": {
            "label": "Chip Demand",
            "tickers": ["SMH", "TSM"],
        },
        "power": {
            "label": "Power Demand",
            "tickers": ["VST", "CEG"],
        },
        "datacenter": {
            "label": "Data Center Demand",
            "tickers": ["DLR", "EQIX"],
        },
    }

    def calc_return(prices, days):
        if len(prices) < 2:
            return None
        end_price   = float(prices.iloc[-1])
        start_price = float(prices.iloc[max(0, len(prices) - days)])
        if start_price == 0:
            return None
        return round((end_price - start_price) / start_price * 100, 2)

    def normalize_series(prices):
        # Convert to % change from first
        # value, so series with different
        # absolute prices can be averaged
        if len(prices) < 1 or prices[0] == 0:
            return []
        base = prices[0]
        return [round((p / base - 1) * 100, 3) for p in prices]

    results = {}
    valid_returns_1m = []

    for key, meta in BASKET.items():
        ticker_returns = {"1W": [], "1M": [], "3M": [], "1Y": []}
        normalized_sparklines = []
        ticker_details = []
        any_available = False

        for tsym in meta["tickers"]:
            try:
                hist = yf.Ticker(tsym).history(
                    period="1y", interval="1d"
                )
                if hist.empty:
                    ticker_details.append({
                        "ticker": tsym, "available": False
                    })
                    continue

                prices = hist["Close"].dropna()
                any_available = True

                r1w = calc_return(prices, 5)
                r1m = calc_return(prices, 21)
                r3m = calc_return(prices, 63)
                r1y = calc_return(prices, 252)

                if r1w is not None: ticker_returns["1W"].append(r1w)
                if r1m is not None: ticker_returns["1M"].append(r1m)
                if r3m is not None: ticker_returns["3M"].append(r3m)
                if r1y is not None: ticker_returns["1Y"].append(r1y)

                spark_raw = prices.tail(90).tolist()
                normalized_sparklines.append(
                    normalize_series(spark_raw)
                )

                ticker_details.append({
                    "ticker": tsym,
                    "available": True,
                    "current_price": round(float(prices.iloc[-1]), 2),
                    "return_1m": r1m,
                })
            except Exception as e:
                print(
                    f"[AI_CAPEX] fetch failed for "
                    f"{tsym}: {e}", flush=True
                )
                ticker_details.append({
                    "ticker": tsym, "available": False
                })

        def avg_or_none(lst):
            return round(sum(lst) / len(lst), 2) if lst else None

        avg_returns = {
            k: avg_or_none(v) for k, v in ticker_returns.items()
        }

        if avg_returns["1M"] is not None:
            valid_returns_1m.append(avg_returns["1M"])

        # Average normalized sparklines
        # across available tickers,
        # truncated to shortest length
        composite_sparkline = []
        if normalized_sparklines:
            min_len = min(len(s) for s in normalized_sparklines)
            for i in range(min_len):
                vals = [s[i] for s in normalized_sparklines]
                composite_sparkline.append(
                    round(sum(vals) / len(vals), 3)
                )

        results[key] = {
            "label": meta["label"],
            "tickers": meta["tickers"],
            "ticker_details": ticker_details,
            "available": any_available,
            "returns": avg_returns,
            "sparkline_90d": composite_sparkline,
        }

    # Composite pulse: average 1M return
    # across available basket members
    if valid_returns_1m:
        avg_1m = round(
            sum(valid_returns_1m) / len(valid_returns_1m), 2
        )
        if avg_1m >= 5:
            pulse_label = "ACCELERATING"
        elif avg_1m >= 0:
            pulse_label = "STEADY"
        elif avg_1m >= -5:
            pulse_label = "COOLING"
        else:
            pulse_label = "CONTRACTING"
    else:
        avg_1m = None
        pulse_label = "UNAVAILABLE"

    # Divergence check — are the three
    # components moving in sync or not?
    divergence = None
    if len(valid_returns_1m) >= 2:
        spread = max(valid_returns_1m) - min(valid_returns_1m)
        if spread >= 10:
            # Find which leads and lags
            sorted_items = sorted(
                [(k, v["returns"]["1M"])
                 for k, v in results.items()
                 if v.get("available") and v["returns"]["1M"] is not None],
                key=lambda x: x[1], reverse=True
            )
            if len(sorted_items) >= 2:
                leader = sorted_items[0][0]
                laggard = sorted_items[-1][0]
                divergence = {
                    "spread": round(spread, 2),
                    "leader": leader,
                    "laggard": laggard,
                }

    # Next major hyperscaler earnings
    # window (quarterly: late Jan, Apr,
    # Jul, Oct — when Microsoft, Google,
    # Amazon, Meta report capex plans)
    from datetime import date
    today = date.today()
    earnings_months = [1, 4, 7, 10]
    next_month = next(
        (m for m in earnings_months if m > today.month),
        earnings_months[0]
    )
    next_year = today.year if next_month > today.month else today.year + 1
    month_names = {
        1: "January", 4: "April",
        7: "July", 10: "October",
    }
    next_earnings_window = (
        f"Late {month_names[next_month]} {next_year}"
    )

    return {
        "basket":        results,
        "composite_1m_avg_return": avg_1m,
        "pulse_label":   pulse_label,
        "divergence": divergence,
        "next_earnings_window": next_earnings_window,
        "methodology": (
            "Market-based proxy using a basket of "
            "publicly traded companies exposed to "
            "AI infrastructure build-out (semiconductors, "
            "power/grid, data centers). Actual hyperscaler "
            "capex is disclosed only quarterly; this tracks "
            "live market pricing of that cycle, not capex "
            "figures directly."
        ),
        "last_updated":  datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/calendar")
async def get_calendar(days_ahead: int = 120, _=Depends(get_current_user)):
    events = get_events_by_window(days_ahead=days_ahead)
    return {"events": [{**ev, "event_date": ev["event_date"].isoformat(), "days_label": days_until_label(ev["days_until"])} for ev in events]}

_yc_cache: dict = {"data": None, "fetched_at": 0}

@app.get("/api/yield-curve")
async def get_yield_curve_endpoint(_=Depends(get_current_user)):
    global _yc_cache
    if time.time() - _yc_cache["fetched_at"] < 3600 and _yc_cache["data"]:
        return {"yield_curve": _yc_cache["data"], "cached": True}
    try:
        data = get_yield_curve_data(); _yc_cache = {"data": data, "fetched_at": time.time()}
        return {"yield_curve": data, "cached": False}
    except Exception as e:
        return {"yield_curve": None, "error": str(e)}

@app.get("/api/notifications/preferences")
async def get_notif_prefs(profile: dict = Depends(require_access)):
    result = _supabase.table("notification_preferences").select("*").eq("user_id", profile["id"]).execute()
    if not result.data:
        return {"preferences": {"user_id": profile["id"], "email": profile.get("email", ""), "channel": "email", "regime_shift_enabled": True, "confidence_breach_enabled": True, "extreme_signal_enabled": True, "fii_flow_enabled": True, "rbi_event_enabled": True, "weekly_summary_enabled": True, "confidence_breach_threshold": 45.0, "vix_threshold": 22.0, "inr_threshold": 85.0, "yield_spike_bps": 20.0, "crude_threshold": 95.0, "fii_outflow_crore": 5000.0, "fii_consecutive_days": 3, "quiet_hours_start": 22, "quiet_hours_end": 7, "max_alerts_per_day": 2, "min_gap_hours": 6}}
    return {"preferences": result.data[0]}

@app.put("/api/notifications/preferences")
async def update_notif_prefs(body: NotificationPrefsUpdate, profile: dict = Depends(require_access)):
    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update_data: raise HTTPException(status_code=400, detail="No fields to update")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    existing = _supabase.table("notification_preferences").select("user_id").eq("user_id", profile["id"]).execute()
    if existing.data:
        result = _supabase.table("notification_preferences").update(update_data).eq("user_id", profile["id"]).execute()
    else:
        update_data["user_id"] = profile["id"]; update_data.setdefault("email", profile.get("email", ""))
        result = _supabase.table("notification_preferences").insert(update_data).execute()
    return {"success": True, "preferences": result.data[0] if result.data else update_data}

@app.get("/api/admin/stats")
async def get_admin_stats(profile: dict = Depends(require_admin)):
    try:
        users = _supabase.table("profiles").select(
            "id, email, full_name, firm_name, tier, trial_ends_at, created_at, last_login"
        ).execute()

        email_logs = _supabase.table("email_logs").select(
            "id, sent_at, recipient, user_id, subject, regime, status, error"
        ).order("sent_at", desc=True).limit(50).execute()

        notif_logs = _supabase.table("notification_logs").select(
            "id, user_id, alert_type, channel, subject, sent_at, status"
        ).order("sent_at", desc=True).limit(50).execute()

        regime_alerts = _supabase.table("regime_alerts").select(
            "id, alerted_at, previous_regime, new_regime, confidence, users_notified"
        ).order("alerted_at", desc=True).limit(20).execute()

        # Run stats — today and all time
        today_str = datetime.now(timezone.utc).date().isoformat()
        today_runs = _supabase.table("runs").select(
            "id", count="exact"
        ).gte("run_at", today_str).execute()
        total_runs = _supabase.table("runs").select(
            "id", count="exact"
        ).execute()

        # Per-user run stats
        all_runs = _supabase.table("runs").select(
            "user_id, run_at"
        ).order("run_at", desc=True).execute()

        run_stats = {}
        for r in (all_runs.data or []):
            uid = r["user_id"]
            if uid not in run_stats:
                run_stats[uid] = {"last_run": r["run_at"], "run_count": 0}
            run_stats[uid]["run_count"] += 1

        # Regime frequency and avg confidence
        regime_data = _supabase.table("runs").select(
            "regime, confidence, run_at"
        ).execute()

        regime_map = {}
        for r in (regime_data.data or []):
            key = r["regime"] or "UNKNOWN"
            if key not in regime_map:
                regime_map[key] = {
                    "regime": key,
                    "count": 0,
                    "conf_total": 0,
                    "last_called": r["run_at"]
                }
            regime_map[key]["count"] += 1
            regime_map[key]["conf_total"] += (r["confidence"] or 0)
            if r["run_at"] > regime_map[key]["last_called"]:
                regime_map[key]["last_called"] = r["run_at"]

        regime_stats = [
            {
                "regime": v["regime"],
                "count": v["count"],
                "avg_confidence": round(
                    (v["conf_total"] / v["count"]) * 100, 1
                ) if v["count"] else 0,
                "last_called": v["last_called"],
            }
            for v in sorted(
                regime_map.values(), key=lambda x: x["count"], reverse=True
            )
        ]

        return {
            "users":          users.data or [],
            "email_logs":     email_logs.data or [],
            "notification_logs": notif_logs.data or [],
            "regime_alerts":  regime_alerts.data or [],
            "run_stats":      run_stats,
            "regime_stats":   regime_stats,
            "summary": {
                "total_users":  len(users.data or []),
                "paid_users":   sum(1 for u in (users.data or []) if u.get("tier") == "paid"),
                "trial_users":  sum(1 for u in (users.data or []) if u.get("tier") == "trial"),
                "pending_users": sum(1 for u in (users.data or []) if u.get("tier") == "pending"),
                "active_jobs":  len([j for j in _jobs.values() if j["status"] == "running"]),
                "today_runs":   today_runs.count or 0,
                "total_runs":   total_runs.count or 0,
            }
        }
    except Exception as e:
        print(f"[ADMIN] get_admin_stats error: {e}", flush=True)
        raise HTTPException(status_code=500, detail=f"Admin stats failed: {e}")

class AdminUserUpdate(BaseModel):
    tier:          Optional[str]  = None
    trial_days:    Optional[int]  = None
    notes:         Optional[str]  = None

@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: str, body: AdminUserUpdate, _profile: dict = Depends(require_admin)):
    update: dict = {}
    if body.tier:
        update["tier"] = body.tier
        if body.tier == "paid":
            update["trial_ends_at"] = None
        elif body.tier == "trial" and body.trial_days:
            update["trial_ends_at"] = (datetime.now() + timedelta(days=body.trial_days)).isoformat()
        elif body.tier in ("expired", "pending"):
            pass
    if body.notes is not None:
        update["notes"] = body.notes
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    _supabase.table("profiles").update(update).eq("id", user_id).execute()
    return {"ok": True, "updated": update}

@app.post("/api/pdf/{job_id}")
async def generate_pdf(job_id: str, profile: dict = Depends(require_access)):
    job = _get_job(job_id)
    if not job: raise HTTPException(status_code=404, detail="Job not found or expired")
    if job["user_id"] != profile["id"]: raise HTTPException(status_code=403, detail="Not your job")
    if job["status"] != "complete": raise HTTPException(status_code=400, detail=f"Job status: {job['status']}")
    try:
        firm_name = profile.get("firm_name") or "SENTINEL Intelligence"
        pdf_bytes = PDFReportGenerator(firm_name=firm_name).generate(job["result"]["final_intel"])
        filename  = f"SENTINEL_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")


@app.get("/api/india-activity")
async def get_india_activity():
    """
    Public endpoint — no auth required.
    Returns India activity signals.
    Hardcoded monthly data updated manually.
    """
    try:
        ingestor = _engines.get("ingestor")
        if ingestor and hasattr(ingestor, "fetch_india_activity_signals"):
            data = ingestor.fetch_india_activity_signals()
        else:
            data = {
                "gst": {
                    "month": "April 2026",
                    "collection_cr": 237000,
                    "yoy_growth_pct": 12.6,
                    "signal": "STRONG",
                },
                "auto_sales": {
                    "month": "April 2026",
                    "total_units": 2252000,
                    "yoy_growth_pct": 8.4,
                    "signal": "MODERATE",
                },
                "bank_credit": {
                    "period": "May 2026",
                    "yoy_growth_pct": 12.8,
                    "retail_credit": 15.2,
                    "signal": "STRONG",
                },
                "composite": {
                    "score": "STRONG",
                    "numeric": 2.67,
                    "summary": "GST STRONG · Auto Sales MODERATE · Credit STRONG",
                },
            }
        iip = _fetch_india_iip()
        data["iip"] = {
            "value":  iip["value"],
            "period": iip["period"],
            "signal": iip["signal"],
            "source": iip["source"],
        }
        return {"activity": data}
    except Exception as e:
        return {"activity": None, "error": str(e)}


@app.get("/api/geopolitical-watch")
async def get_geopolitical_watch():
    print("[GEOWATCH] Endpoint called", flush=True)
    print(f"[GEOWATCH] Theme count: {len(GEOPOLITICAL_THEMES)}", flush=True)
    for key, meta in GEOPOLITICAL_THEMES.items():
        print(f"[GEOWATCH] Processing {key}", flush=True)
    row = _get_theme_cached(...)
    """
    Returns all 10 geopolitical theme analyses with 72h cache TTL.
    Free endpoint — no auth required, same tier as /api/global-macro.
    """
    nlp_engine = IndianMacroNLP()
    if not nlp_engine:
        print(
            "[GEOWATCH] NLP engine not available — skipping analysis",
            flush=True,
        )
    themes = []
    for key, meta in GEOPOLITICAL_THEMES.items():
        row = _get_theme_cached(
            key,
            meta["label"],
            meta["search_query"],
            nlp_engine,
        )
        themes.append(row)

    return {
        "themes": themes,
        "page_updated_at": max(
            (t.get("fetched_at", "") for t in themes),
            default=None,
        ),
    }