import os
import sys
import json
from datetime import (
    datetime, timedelta
)

# Windows consoles default to cp1252, which can't encode the emoji
# used in this script's output — force UTF-8 so it runs out of the
# box instead of crashing on the first print().
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import numpy as np
from scipy import stats
import yfinance as yf
from supabase import (
    create_client
)

# ── Config ──────────────────────────

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", ""
)
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY", ""
    ) or os.environ.get(
    "SERVICE_KEY", ""
)

# Forward return window in days
FORWARD_DAYS = 20

# Minimum confidence to include
# run in analysis
MIN_CONFIDENCE = 0.0

# ── Step 1: Load runs ────────────────

def load_runs(sb):
    print("\n📊 Loading runs from "
          "Supabase...")
    # NOTE: "equity_bias" and "briefing_allowed" are NOT top-level
    # columns on the runs table (verified against the actual insert
    # in main_api.py). equity_bias lives nested inside the JSON
    # "allocation" column; briefing_allowed is never persisted at
    # all (it only exists transiently in the in-memory job result
    # returned to the frontend for that one run). Selecting either
    # as a flat column makes PostgREST reject the whole query.
    result = sb.table("runs") \
        .select(
            "run_at,confidence,"
            "conviction,allocation,"
            "implied_action,regime"
        ) \
        .order("run_at",
               desc=False) \
        .execute()

    runs = result.data or []
    print(f"   Found {len(runs)} "
          f"total runs")
    return runs

# ── Step 2: Load Nifty prices ────────

def load_nifty():
    print("\n📈 Fetching Nifty 50 "
          "historical prices...")
    ticker = yf.Ticker("^NSEI")
    hist = ticker.history(
        period="1y",
        interval="1d"
    )
    hist.index = pd.to_datetime(
        hist.index
    ).tz_localize(None)
    print(f"   Got {len(hist)} "
          f"trading days")
    return hist

# ── Step 3: Match runs to returns ────

def compute_forward_returns(
    runs, nifty_hist
):
    print(f"\n🔗 Computing {FORWARD_DAYS}"
          f"-day forward returns...")

    results = []
    skipped = 0

    for run in runs:
        try:
            run_dt = pd.to_datetime(
                run["run_at"]
            ).tz_localize(None)
            run_date = run_dt.date()

            # Find the next available
            # trading day on or after
            # run date
            future_dates = nifty_hist[
                nifty_hist.index.date
                >= run_date
            ]

            if len(future_dates) < (
                FORWARD_DAYS + 1
            ):
                skipped += 1
                continue

            # Entry price: close on
            # run date (or next
            # trading day)
            entry_price = float(
                future_dates
                ["Close"].iloc[0]
            )

            # Exit price: N days later
            exit_price = float(
                future_dates
                ["Close"].iloc[
                    FORWARD_DAYS
                ]
            )

            fwd_return = (
                exit_price
                - entry_price
            ) / entry_price * 100

            # Determine signal
            # from run data
            action = (
                run.get(
                    "implied_action", ""
                ) or ""
            ).upper()

            # equity_bias is nested inside the "allocation" JSON
            # column, not a flat field on the run row.
            allocation = run.get("allocation") or {}
            equity_bias = (
                allocation.get(
                    "equity_bias", ""
                ) or ""
            ).upper()

            confidence = float(
                run.get(
                    "confidence", 0
                ) or 0
            )

            # briefing_allowed is never written to Supabase (it's
            # only present in the transient job-result payload for
            # the run that produced it), so it can't be recovered
            # historically. Defaulting to True means the BLOCKED
            # bucket below will always end up empty — that's
            # expected, not a bug.
            briefing = True

            # Classify signal
            if not briefing:
                signal = "BLOCKED"
            elif (
                equity_bias == "RISK_ON"
                and confidence >= 0.65
            ):
                signal = "ADD"
            elif (
                equity_bias == "RISK_OFF"
                or confidence < 0.55
            ):
                signal = "REDUCE"
            else:
                signal = "HOLD"

            results.append({
                "date": run_date,
                "confidence":
                    confidence,
                "signal":      signal,
                "equity_bias":
                    equity_bias,
                "regime":
                    run.get("regime",""),
                "fwd_return":
                    fwd_return,
                "entry":
                    entry_price,
                "exit":
                    exit_price,
            })

        except Exception as e:
            skipped += 1
            continue

    print(f"   Matched {len(results)}"
          f" runs "
          f"({skipped} skipped — "
          f"insufficient future data)")
    return results

# ── Step 4: Statistics ───────────────

def analyse(results):
    if not results:
        print("No results to analyse")
        return

    df = pd.DataFrame(results)

    print("\n" + "═" * 50)
    print("  SENTINEL SIGNAL "
          "BACKTEST REPORT")
    print("═" * 50)
    print(f"  Period: "
          f"{df['date'].min()} → "
          f"{df['date'].max()}")
    print(f"  Total runs analysed: "
          f"{len(df)}")
    print(f"  Forward window: "
          f"{FORWARD_DAYS} trading days")
    print("═" * 50)

    # Overall stats
    print(f"\n📊 OVERALL")
    print(f"   Avg forward return: "
          f"{df['fwd_return'].mean():.2f}%")
    print(f"   Positive rate: "
          f"{(df['fwd_return'] > 0).mean()*100:.1f}%")

    # By signal type
    print(f"\n📊 BY SIGNAL")
    for sig in ["ADD", "HOLD",
                "REDUCE", "BLOCKED"]:
        subset = df[
            df["signal"] == sig
        ]
        if len(subset) < 3:
            continue

        returns = subset["fwd_return"]
        pos_rate = (
            returns > 0
        ).mean() * 100

        # T-test: is mean return
        # significantly different
        # from zero?
        t_stat, p_val = (
            stats.ttest_1samp(
                returns, 0
            )
        )

        sig_marker = (
            "✅" if p_val < 0.05
            else "⚠️ " if p_val < 0.10
            else "❌"
        )

        print(f"\n   {sig} "
              f"({len(subset)} runs)")
        print(f"   Avg return: "
              f"{returns.mean():.2f}%")
        print(f"   Positive rate: "
              f"{pos_rate:.1f}%")
        print(f"   Best/Worst: "
              f"{returns.max():.1f}% / "
              f"{returns.min():.1f}%")
        print(f"   p-value: "
              f"{p_val:.3f} "
              f"{sig_marker}")
        print(f"   Statistically "
              f"significant: "
              f"{'YES' if p_val < 0.05 else 'NO'}")

    # By confidence bucket
    print(f"\n📊 BY CONFIDENCE LEVEL")
    buckets = [
        (0.50, 0.60, "50-60%"),
        (0.60, 0.70, "60-70%"),
        (0.70, 0.80, "70-80%"),
        (0.80, 1.01, "80%+"),
    ]
    for lo, hi, label in buckets:
        subset = df[
            (df["confidence"] >= lo)
            & (df["confidence"] < hi)
        ]
        if len(subset) < 3:
            continue
        returns = subset["fwd_return"]
        print(f"   {label}: "
              f"{len(subset)} runs | "
              f"avg {returns.mean():.2f}% | "
              f"win rate "
              f"{(returns>0).mean()*100:.1f}%")

    # Add after confidence bucket section:
    print("\n📊 HIGH CONFIDENCE RUNS (70-80%)")
    high_conf = df[
        (df["confidence"] >= 0.70)
        & (df["confidence"] < 0.80)
        ].sort_values("date")

    for _, row in high_conf.iterrows():
        print(
        f"  {row['date']} | "
        f"conf={row['confidence']:.2f} | "
        f"signal={row['signal']} | "
        f"return={row['fwd_return']:.2f}%"
    )

    # By regime
    print(f"\n📊 BY REGIME")
    for regime in df["regime"].unique():
        subset = df[
            df["regime"] == regime
        ]
        if len(subset) < 3:
            continue
        returns = subset["fwd_return"]
        short = regime.replace(
            "_", " "
        )[:30]
        print(f"   {short}: "
              f"{len(subset)} runs | "
              f"avg {returns.mean():.2f}%"
              f" | win "
              f"{(returns>0).mean()*100:.1f}%")

    # Key finding
    print("\n" + "═" * 50)
    add_runs = df[df["signal"]=="ADD"]
    if len(add_runs) > 0:
        add_winrate = (
            add_runs["fwd_return"] > 0
        ).mean() * 100
        add_avg = add_runs[
            "fwd_return"
        ].mean()
        print(f"\n🎯 KEY FINDING:")
        print(f"   When Sentinel said"
              f" ADD:")
        print(f"   → Nifty was positive"
              f" {FORWARD_DAYS} days later"
              f" {add_winrate:.1f}% of"
              f" the time")
        print(f"   → Average return:"
              f" {add_avg:.2f}%")
        if add_winrate >= 60:
            print(f"   ✅ Signal has"
                  f" predictive value")
        else:
            print(f"   ⚠️  Signal needs"
                  f" refinement")
    print("═" * 50 + "\n")

# ── Main ─────────────────────────────

if __name__ == "__main__":
    # Load credentials from
    # .streamlit/secrets.toml
    # or environment
    try:
        import toml
        secrets = toml.load(
            ".streamlit/secrets.toml"
        )
        url = secrets.get(
            "SUPABASE_URL", ""
        )
        key = secrets.get(
            "SUPABASE_SERVICE_KEY",
            ""
        ) or secrets.get(
            "SERVICE_KEY", ""
        )
    except Exception:
        url  = SUPABASE_URL
        key  = SUPABASE_KEY

    if not url or not key:
        print("❌ Supabase credentials"
              " not found.")
        print("   Set SUPABASE_URL and"
              " SUPABASE_SERVICE_KEY"
              " in environment or"
              " .streamlit/secrets.toml")
        exit(1)

    sb = create_client(url, key)

    runs   = load_runs(sb)
    nifty  = load_nifty()
    results = compute_forward_returns(
        runs, nifty
    )
    analyse(results)
