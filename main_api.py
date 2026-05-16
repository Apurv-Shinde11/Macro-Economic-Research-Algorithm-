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
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
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

JOB_TTL_SECONDS = 3600

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
            "Banking & Financials",
            "Infrastructure & Capex",
            "Consumer Discretionary",
            "Real Estate",
            "Auto & Auto Ancillaries",
        ],
        "NEUTRAL": [
            "IT & Technology",
            "Pharmaceuticals",
            "Chemicals",
            "Telecom",
        ],
        "AVOID": [
            "FMCG & Staples",
            "Utilities",
            "Gold & Precious Metals",
        ],
    },
    "STABLE_GROWTH": {
        "FAVOUR": [
            "IT & Technology",
            "Consumer Discretionary",
            "Banking & Financials",
            "Pharmaceuticals",
        ],
        "NEUTRAL": [
            "Infrastructure & Capex",
            "Auto & Auto Ancillaries",
            "Chemicals",
            "Telecom",
        ],
        "AVOID": [
            "Utilities",
            "Gold & Precious Metals",
        ],
    },
    "MONETARY_TIGHTENING": {
        "FAVOUR": [
            "IT & Technology",
            "Pharmaceuticals",
            "FMCG & Staples",
            "Gold & Precious Metals",
        ],
        "NEUTRAL": [
            "Chemicals",
            "Telecom",
            "Utilities",
        ],
        "AVOID": [
            "Banking & Financials",
            "Real Estate",
            "Auto & Auto Ancillaries",
            "Infrastructure & Capex",
            "Consumer Discretionary",
        ],
    },
    "EXTERNAL_SHOCK": {
        "FAVOUR": [
            "IT & Technology",
            "Pharmaceuticals",
            "FMCG & Staples",
            "Gold & Precious Metals",
            "Utilities",
        ],
        "NEUTRAL": [
            "Telecom",
            "Chemicals",
        ],
        "AVOID": [
            "Banking & Financials",
            "Real Estate",
            "Auto & Auto Ancillaries",
            "Infrastructure & Capex",
            "Consumer Discretionary",
            "Energy & Oil",
        ],
    },
    "STAGFLATION_RISK": {
        "FAVOUR": [
            "Gold & Precious Metals",
            "Pharmaceuticals",
            "FMCG & Staples",
            "Energy & Oil",
        ],
        "NEUTRAL": [
            "Utilities",
            "Telecom",
            "IT & Technology",
        ],
        "AVOID": [
            "Banking & Financials",
            "Real Estate",
            "Consumer Discretionary",
            "Auto & Auto Ancillaries",
            "Infrastructure & Capex",
        ],
    },
    "STAGFLATIONARY_RISK": {
        "FAVOUR": [
            "Gold & Precious Metals",
            "Pharmaceuticals",
            "FMCG & Staples",
            "Energy & Oil",
        ],
        "NEUTRAL": [
            "Utilities",
            "Telecom",
            "IT & Technology",
        ],
        "AVOID": [
            "Banking & Financials",
            "Real Estate",
            "Consumer Discretionary",
            "Auto & Auto Ancillaries",
            "Infrastructure & Capex",
        ],
    },
    "EARLY_CYCLE_RECOVERY": {
        "FAVOUR": [
            "Banking & Financials",
            "Auto & Auto Ancillaries",
            "Infrastructure & Capex",
            "Consumer Discretionary",
            "Real Estate",
        ],
        "NEUTRAL": [
            "IT & Technology",
            "Chemicals",
            "Telecom",
        ],
        "AVOID": [
            "Gold & Precious Metals",
            "Utilities",
            "FMCG & Staples",
        ],
    },
    "GROWTH_SLOWDOWN_SUPPORT": {
        "FAVOUR": [
            "Pharmaceuticals",
            "FMCG & Staples",
            "IT & Technology",
            "Utilities",
            "Gold & Precious Metals",
        ],
        "NEUTRAL": [
            "Telecom",
            "Chemicals",
            "Banking & Financials",
        ],
        "AVOID": [
            "Real Estate",
            "Consumer Discretionary",
            "Auto & Auto Ancillaries",
            "Infrastructure & Capex",
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


def _run_pipeline_sync(job_id: str, user_id: str, repo: float, deficit: float, capex: float):
    try:
        eng = _engines
        rep = eng["repair"]
        news_raw = eng["ingestor"].fetch_news_sentiment()
        macro    = eng["ingestor"].fetch_macro_indicators()
        market   = eng["ingestor"].fetch_market_data()
        news = " ".join(news_raw.get("headlines", [])) if isinstance(news_raw, dict) else str(news_raw)
        nse_snapshot = {}
        try:
            nse_snapshot = eng["nse"].get_full_snapshot()
        except Exception:
            nse_snapshot = {"fii_dii": {}, "indices": {}, "fii_net_crore": None, "india_vix": 15, "pcr": 1.0, "flow_signal": "NEUTRAL"}
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
        nse_snapshot["crude_price"] = _crude_live
        intel = eng["nlp"].get_regime_scores(news)
        intel["hard_data"].update({
            "repo_rate": repo, "fiscal_deficit": deficit, "capex_lakh_cr": capex,
            "gdp_growth": macro.get("growth", {}).get("gdp", 7.2),
            "fii_net_crore": nse_snapshot.get("fii_net_crore"),
            "india_vix": nse_snapshot.get("india_vix", 15),
            "nifty_pcr": nse_snapshot.get("pcr", 1.0),
        })
        try:
            liq = ensure_dict(eng["liquidity"].analyze(intel, market, nse_snapshot))
        except TypeError:
            liq = ensure_dict(eng["liquidity"].analyze(intel, market))
        regime = ensure_dict(eng["regime"].detect_regime(intel, liq))
        regime = rep.repair(regime, REGIME_SCHEMA)
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
        _jobs[job_id]["result"] = {"regime": regime, "strategy": strat, "decision": dec, "positioning": pos, "scenarios": scenarios, "triggers": triggers, "liquidity": liq, "intel": intel, "nse": nse_snapshot, "macro": macro, "final_intel": final_intel, "report": report if isinstance(report, str) else "", "sector_heatmap": SECTOR_HEATMAP.get(regime.get("regime", ""), {"FAVOUR": [], "NEUTRAL": [], "AVOID": []})}
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
    return {"job_id": job_id, "status": job["status"], "result": job["result"] if job["status"] == "complete" else None, "error": job["error"] if job["status"] == "failed" else None}

@app.get("/api/history")
async def get_history(limit: int = 20, profile: dict = Depends(require_access)):
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
# Last verified: May 2026 (sources: Fed, ECB, BoJ, PBoC, BoE, RBI official pages)
_POLICY_RATES = {
    "US": 4.50,   # US Federal Funds Rate
    "CN": 3.10,   # PBoC Loan Prime Rate 1Y
    "DE": 2.40,   # ECB Deposit Facility Rate
    "JP": 0.50,   # Bank of Japan Policy Rate
    "GB": 4.25,   # Bank of England Bank Rate
    "IN": 5.25,   # RBI Repo Rate
}

# ── Hardcoded PMI — update monthly on S&P Global release day ─────────────────
# Last verified: May 2026 (source: S&P Global Manufacturing PMI releases)
_PMI_VALUES = {
    "US": 50.2,   # S&P Global US Manufacturing PMI
    "CN": 49.8,   # Caixin China Manufacturing PMI
    "DE": 48.4,   # S&P Global Germany Manufacturing PMI
    "JP": 48.7,   # au Jibun Bank Japan Manufacturing PMI
    "GB": 45.4,   # S&P Global UK Manufacturing PMI
    "IN": 58.8,   # S&P Global India Manufacturing PMI
}

_ECONOMIES = [
    {"code": "US", "name": "United States",  "flag": "🇺🇸",
     "currency_label": "USD", "wb_code": "US",
     "ticker_currency": None,
     "ticker_yield": "^TNX"},
    {"code": "CN", "name": "China",          "flag": "🇨🇳",
     "currency_label": "CNY", "wb_code": "CN",
     "ticker_currency": "USDCNY=X",
     "ticker_yield": None},
    {"code": "DE", "name": "Germany",        "flag": "🇩🇪",
     "currency_label": "EUR", "wb_code": "DE",
     "ticker_currency": "EURUSD=X",
     "ticker_yield": "^IRDE10"},
    {"code": "JP", "name": "Japan",          "flag": "🇯🇵",
     "currency_label": "JPY", "wb_code": "JP",
     "ticker_currency": "USDJPY=X",
     "ticker_yield": "^IRJP10"},
    {"code": "GB", "name": "United Kingdom", "flag": "🇬🇧",
     "currency_label": "GBP", "wb_code": "GB",
     "ticker_currency": "GBPUSD=X",
     "ticker_yield": "^IRGB10Y"},
    {"code": "IN", "name": "India",          "flag": "🇮🇳",
     "currency_label": "INR", "wb_code": "IN",
     "ticker_currency": "USDINR=X",
     "ticker_yield": None},
]

_YIELD_FALLBACKS = {
    "CN": 2.10,
    "IN": 6.85,
    "DE": 2.45,   
    "JP": 1.45,   
    "GB": 4.42,
}

_global_macro_cache_mem: dict = {"data": None, "fetched_at": 0}


def _wb_fetch(wb_code: str, indicator: str) -> float | None:
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
            val = rec.get("value")
            if val is not None:
                return round(float(val), 2)
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


def _build_economy_record(eco, gdp, inflation, unemployment, currency_map, yield_map):
    code = eco["code"]
    raw_fx = currency_map.get(code)
    currency_vs_usd = 1.0 if code == "US" else (round(raw_fx, 4) if raw_fx else None)
    yield_10y = yield_map.get(code) or _YIELD_FALLBACKS.get(code)
    return {
        "code":            code,
        "name":            eco["name"],
        "flag":            eco["flag"],
        "currency_label":  eco["currency_label"],
        "gdp_growth":      gdp,
        "inflation":       inflation,
        "policy_rate":     _POLICY_RATES.get(code),
        "pmi":             _PMI_VALUES.get(code),
        "unemployment":    unemployment,
        "currency_vs_usd": currency_vs_usd,
        "yield_10y":       yield_10y,
        "macro_signal":    _derive_macro_signal(gdp, inflation),
        "last_updated":    datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/global-macro")
async def get_global_macro():
    global _global_macro_cache_mem
    cache_age = time.time() - _global_macro_cache_mem.get("fetched_at", 0)
    if _global_macro_cache_mem.get("data") and cache_age < 21600:
        return {**_global_macro_cache_mem["data"], "cached": True}
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
        if cached.data and len(cached.data) >= 6:
            # Enrich cached rows with computed fields not stored in Supabase
            economy_meta = {e["code"]: e for e in _ECONOMIES}
            enriched = []
            for row in cached.data:
                code = row.get("economy")
                meta = economy_meta.get(code, {})
                enriched.append({
                    **row,
                    "code":           code,
                    "name":           meta.get("name", code),
                    "flag":           meta.get("flag", ""),
                    "currency_label": meta.get("currency_label", ""),
                    "yield_10y":      row.get("yield_10y") or _YIELD_FALLBACKS.get(code),
                    "macro_signal":   _derive_macro_signal(row.get("gdp_growth"), row.get("inflation")),
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

    print("[GLOBAL_MACRO] Fetching fresh data...", flush=True)
    try:
        currency_map, yield_map = _fetch_live_economy_data()
    except Exception as _e:
        print(f"[GLOBAL_MACRO] Live data fetch failed: {_e}", flush=True)
        currency_map, yield_map = {}, {}

    economies = []
    for eco in _ECONOMIES:
        wb = eco["wb_code"]
        gdp          = _wb_fetch(wb, "NY.GDP.MKTP.KD.ZG")
        inflation    = _wb_fetch(wb, "FP.CPI.TOTL.ZG")
        unemployment = _wb_fetch(wb, "SL.UEM.TOTL.ZS")
        record = _build_economy_record(
            eco, gdp, inflation, unemployment, currency_map, yield_map
        )
        economies.append(record)
        try:
            _supabase.table("global_macro_cache").upsert(
                {
                    "economy":         eco["code"],
                    "gdp_growth":      gdp,
                    "inflation":       inflation,
                    "policy_rate":     _POLICY_RATES.get(eco["code"]),
                    "pmi":             _PMI_VALUES.get(eco["code"]),
                    "unemployment":    unemployment,
                    "currency_vs_usd": record.get("currency_vs_usd"),
                    "yield_10y":       record.get("yield_10y"),
                    "last_updated":    datetime.now(timezone.utc).isoformat(),
                    "data_sources": {
                        "gdp":          "World Bank NY.GDP.MKTP.KD.ZG",
                        "inflation":    "World Bank FP.CPI.TOTL.ZG",
                        "unemployment": "World Bank SL.UEM.TOTL.ZS",
                        "policy_rate":  "Central bank official — hardcoded May 2026",
                        "pmi":          "S&P Global — hardcoded May 2026",
                        "currency":     "yfinance live",
                        "yield":        "yfinance live / hardcoded fallback",
                    },
                },
                on_conflict="economy",
            ).execute()
        except Exception as _e:
            print(f"[GLOBAL_MACRO] Supabase upsert failed {eco['code']}: {_e}", flush=True)
        print(f"[GLOBAL_MACRO] {eco['code']} — signal: {record['macro_signal']}", flush=True)

    result = {
        "economies":       economies,
        "page_updated_at": datetime.now(timezone.utc).isoformat(),
        "cached":          False,
    }
    _global_macro_cache_mem = {"data": result, "fetched_at": time.time()}
    return result

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