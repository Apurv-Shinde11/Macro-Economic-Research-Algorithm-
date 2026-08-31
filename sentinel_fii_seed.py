"""
sentinel_fii_seed.py
Run once from your local machine to seed
historical FII/DII data into Supabase.

Usage:
    python sentinel_fii_seed.py

Requirements:
    pip install requests supabase python-dotenv
"""

import os
import requests
import json
import time
from datetime import datetime, timedelta
from supabase import create_client

# ── Config ────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# ── Init Supabase ─────────────────────────────
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── NSE FII/DII Historical Data ───────────────
# NSE provides FII/DII data via their reports API
# We fetch month by month going back 24 months

def fetch_nse_fii_month(from_date, to_date):
    """
    Fetch FII/DII data from NSE for a date range.
    Returns list of {date, fii_net, dii_net} dicts.
    """
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    }
    params = {
        "startDate": from_date,  # DD-MM-YYYY
        "endDate":   to_date,
        "type":      "equities",
    }

    try:
        # NSE requires a session cookie first
        session = requests.Session()
        session.get(
            "https://www.nseindia.com",
            headers=headers,
            timeout=10
        )
        time.sleep(1)

        resp = session.get(
            url,
            headers=headers,
            params=params,
            timeout=15
        )

        if resp.status_code != 200:
            print(
                f"  NSE returned {resp.status_code} "
                f"for {from_date} to {to_date}"
            )
            return []

        data = resp.json()
        rows = []

        def _signal(val, threshold=500):
            if val is None:
                return None
            if val > threshold:
                return "BUYING"
            if val < -threshold:
                return "SELLING"
            return "NEUTRAL"

        for item in data:
            try:
                # Parse date
                date_str = item.get("date", "")
                dt = datetime.strptime(
                    date_str, "%d-%b-%Y"
                )
                date_iso = dt.strftime("%Y-%m-%d")

                # FII net = buy - sell in equities
                fii_net = float(
                    str(item.get(
                        "fiiNetEquity",
                        item.get("netFII", 0)
                    )).replace(",", "")
                )

                # DII net
                dii_net = float(
                    str(item.get(
                        "diiNetEquity",
                        item.get("netDII", 0)
                    )).replace(",", "")
                )

                rows.append({
                    "trade_date":      date_iso,
                    "fii_net_crore":   round(fii_net, 2),
                    "dii_net_crore":   round(dii_net, 2),
                    "fii_signal":      _signal(fii_net),
                    "dii_signal":      _signal(dii_net),
                    "combined_signal": _signal(fii_net + dii_net),
                    "source":          "nse_seed",
                })
            except Exception as e:
                print(f"  Parse error on row: {e}")
                continue

        return rows

    except Exception as e:
        print(f"  Fetch error: {e}")
        return []


def seed_historical_data(months_back=24):
    """
    Seeds historical FII/DII data into Supabase
    going back N months from today.
    """
    today = datetime.today()
    all_rows = []

    print(f"Fetching {months_back} months of "
          f"FII/DII history from NSE...")

    for i in range(months_back):
        # Calculate month range
        end   = today - timedelta(days=30*i)
        start = end   - timedelta(days=30)

        from_str = start.strftime("%d-%m-%Y")
        to_str   = end.strftime("%d-%m-%Y")

        print(f"  Fetching {from_str} to {to_str}...")
        rows = fetch_nse_fii_month(from_str, to_str)
        print(f"  Got {len(rows)} rows")
        all_rows.extend(rows)
        time.sleep(1.5)  # Rate limit NSE

    # Deduplicate by date
    seen  = set()
    dedup = []
    for row in all_rows:
        if row["trade_date"] not in seen:
            seen.add(row["trade_date"])
            dedup.append(row)

    print(f"\nTotal unique rows: {len(dedup)}")

    # Sort ascending
    dedup.sort(key=lambda x: x["trade_date"])

    # Insert into Supabase in batches of 50
    # Use upsert to avoid duplicates
    batch_size = 50
    inserted   = 0

    for i in range(0, len(dedup), batch_size):
        batch = dedup[i:i+batch_size]
        try:
            sb.table("fii_dii_daily")\
              .upsert(
                  batch,
                  on_conflict="trade_date"
              ).execute()
            inserted += len(batch)
            print(
                f"  Inserted batch "
                f"{i//batch_size + 1} "
                f"({inserted}/{len(dedup)} rows)"
            )
        except Exception as e:
            print(f"  Insert error: {e}")
            continue

    print(f"\n✅ Seeding complete. "
          f"{inserted} rows inserted.")

    # Show date range seeded
    if dedup:
        print(f"   Earliest: {dedup[0]['trade_date']}")
        print(f"   Latest:   {dedup[-1]['trade_date']}")


if __name__ == "__main__":
    seed_historical_data(months_back=24)