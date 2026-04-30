"""
economic_calendar.py — SENTINEL Economic Calendar

Covers India macro events:
  - RBI MPC policy decisions
  - CPI inflation release (MOSPI)
  - GDP growth print (MOSPI)
  - Union Budget
  - SEBI Board meetings

Each event has: name, date, category, importance,
previous value, consensus estimate, actual (post-release),
and a market impact note for PMS/wealth context.
"""

import datetime

# =========================
# 🗓️ EVENT DEFINITIONS
# Dates confirmed from RBI, MOSPI, GOI calendars
# Updated through Dec 2026
# =========================

CALENDAR_EVENTS = [

    # ─────────────────────────────
    # RBI MPC — 6 meetings per year
    # Source: rbi.org.in
    # ─────────────────────────────
    {
        "name":          "RBI MPC Decision",
        "date":          "2026-04-09",
        "category":      "RBI MPC",
        "importance":    "HIGH",
        "previous":      "6.25%",
        "consensus":     "6.00% (25bps cut expected)",
        "actual":        "6.00%",
        "impact_note":   "Rate cut of 25bps — second consecutive cut. "
                         "Positive for rate-sensitive sectors: Banks, Real Estate, Autos.",
        "released":      True
    },
    {
        "name":          "RBI MPC Decision",
        "date":          "2026-06-05",
        "category":      "RBI MPC",
        "importance":    "HIGH",
        "previous":      "6.00%",
        "consensus":     "5.75% (25bps cut expected)",
        "actual":        None,
        "impact_note":   "Further easing expected if CPI stays below 4.5%. "
                         "Watch for change in stance — NEUTRAL to ACCOMMODATIVE "
                         "would signal a multi-cut cycle.",
        "released":      False
    },
    {
        "name":          "RBI MPC Decision",
        "date":          "2026-08-06",
        "category":      "RBI MPC",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "Data-dependent pause likely",
        "actual":        None,
        "impact_note":   "Mid-cycle review. Monsoon and kharif crop data "
                         "will heavily influence stance.",
        "released":      False
    },
    {
        "name":          "RBI MPC Decision",
        "date":          "2026-10-07",
        "category":      "RBI MPC",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Pre-festive season review. Credit growth and "
                         "system liquidity will be key inputs.",
        "released":      False
    },
    {
        "name":          "RBI MPC Decision",
        "date":          "2026-12-04",
        "category":      "RBI MPC",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Year-end policy review. Watch global spillovers "
                         "from US Fed December meeting.",
        "released":      False
    },

    # ─────────────────────────────
    # CPI RELEASES — MOSPI
    # Released ~12th of every month
    # for the previous month's data
    # ─────────────────────────────
    {
        "name":          "CPI Inflation — March 2026",
        "date":          "2026-04-14",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      "3.61% (Feb 2026)",
        "consensus":     "3.80%",
        "actual":        None,
        "impact_note":   "Below-target CPI gives RBI room for further cuts. "
                         "Watch food inflation component — key driver of headline.",
        "released":      False
    },
    {
        "name":          "CPI Inflation — April 2026",
        "date":          "2026-05-12",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "April data captures summer vegetable price impact. "
                         "Above 4.5% would pause RBI easing cycle.",
        "released":      False
    },
    {
        "name":          "CPI Inflation — May 2026",
        "date":          "2026-06-12",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Pre-monsoon print. Critical input for June MPC.",
        "released":      False
    },
    {
        "name":          "CPI Inflation — June 2026",
        "date":          "2026-07-13",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Monsoon onset impact on food prices.",
        "released":      False
    },
    {
        "name":          "CPI Inflation — July 2026",
        "date":          "2026-08-12",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Key input for August MPC decision.",
        "released":      False
    },
    {
        "name":          "CPI Inflation — August 2026",
        "date":          "2026-09-12",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Post-monsoon food inflation trend becomes visible.",
        "released":      False
    },
    {
        "name":          "CPI Inflation — September 2026",
        "date":          "2026-10-13",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Key input for October MPC.",
        "released":      False
    },
    {
        "name":          "CPI Inflation — October 2026",
        "date":          "2026-11-12",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Winter crop harvest effect on food prices.",
        "released":      False
    },
    {
        "name":          "CPI Inflation — November 2026",
        "date":          "2026-12-12",
        "category":      "CPI",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Key input for December MPC.",
        "released":      False
    },

    # ─────────────────────────────
    # GDP PRINTS — MOSPI
    # First advance estimate: Jan
    # Second advance estimate: Feb
    # Provisional: May/Jun
    # First revised: Jan (next year)
    # ─────────────────────────────
    {
        "name":          "GDP Growth — Q4 FY26 (Advance)",
        "date":          "2026-05-29",
        "category":      "GDP",
        "importance":    "HIGH",
        "previous":      "7.4% (Q3 FY26)",
        "consensus":     "7.1%",
        "actual":        None,
        "impact_note":   "Full-year FY26 GDP estimate released simultaneously. "
                         "Below 7.0% would shift regime toward GROWTH_SLOWDOWN_SUPPORT. "
                         "Above 7.5% would strengthen LIQUIDITY_DRIVEN_EXPANSION conviction.",
        "released":      False
    },
    {
        "name":          "GDP Growth — Q1 FY27 (First Advance)",
        "date":          "2026-08-28",
        "category":      "GDP",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "First look at FY27 momentum. "
                         "Capex cycle and credit growth are key lead indicators.",
        "released":      False
    },

    # ─────────────────────────────
    # UNION BUDGET
    # Always 1st February
    # ─────────────────────────────
    {
        "name":          "Union Budget FY27",
        "date":          "2026-02-01",
        "category":      "Union Budget",
        "importance":    "HIGH",
        "previous":      "Deficit 4.9% of GDP (FY26)",
        "consensus":     "Deficit 4.4% of GDP (FY27)",
        "actual":        "Deficit 4.4% of GDP — capex Rs.11.21L Cr",
        "impact_note":   "Fiscal consolidation on track. Capex growth moderated "
                         "vs prior year. Consumption-oriented vs investment-led shift.",
        "released":      True
    },
    {
        "name":          "Union Budget FY28",
        "date":          "2027-02-01",
        "category":      "Union Budget",
        "importance":    "HIGH",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Election-year budget dynamics to watch. "
                         "Rural and consumption spending likely to increase.",
        "released":      False
    },

    # ─────────────────────────────
    # SEBI BOARD MEETINGS
    # Approximately quarterly
    # Source: sebi.gov.in
    # ─────────────────────────────
    {
        "name":          "SEBI Board Meeting",
        "date":          "2026-03-24",
        "category":      "SEBI",
        "importance":    "MEDIUM",
        "previous":      None,
        "consensus":     "F&O framework review, SME IPO norms",
        "actual":        "SME IPO eligibility tightened. "
                         "New algo-trading framework for retail investors approved.",
        "impact_note":   "SME segment re-rating risk. "
                         "Algo trading changes affect HFT flow patterns.",
        "released":      True
    },
    {
        "name":          "SEBI Board Meeting",
        "date":          "2026-06-20",
        "category":      "SEBI",
        "importance":    "MEDIUM",
        "previous":      None,
        "consensus":     "FPI disclosure norms, derivatives regulation",
        "actual":        None,
        "impact_note":   "FPI disclosure changes could affect foreign "
                         "ownership reporting in large-caps.",
        "released":      False
    },
    {
        "name":          "SEBI Board Meeting",
        "date":          "2026-09-22",
        "category":      "SEBI",
        "importance":    "MEDIUM",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Pre-Budget regulatory calendar typically active. "
                         "Watch for mutual fund expense ratio changes.",
        "released":      False
    },
    {
        "name":          "SEBI Board Meeting",
        "date":          "2026-12-15",
        "category":      "SEBI",
        "importance":    "MEDIUM",
        "previous":      None,
        "consensus":     "TBD",
        "actual":        None,
        "impact_note":   "Year-end board. ESG disclosure framework "
                         "and REIT/InvIT regulations often reviewed.",
        "released":      False
    },
]


# =========================
# 🔧 HELPER FUNCTIONS
# =========================

def get_all_events():
    """Returns all events sorted by date."""
    today = datetime.date.today()
    events = []
    for e in CALENDAR_EVENTS:
        event_date = datetime.date.fromisoformat(e["date"])
        days_until  = (event_date - today).days
        events.append({**e, "event_date": event_date, "days_until": days_until})
    return sorted(events, key=lambda x: x["event_date"])


def get_upcoming_events(n=3):
    """Returns next N unreleased events from today."""
    today = datetime.date.today()
    upcoming = [
        e for e in get_all_events()
        if e["event_date"] >= today and not e["released"]
    ]
    return upcoming[:n]


def get_events_by_window(days_ahead=90):
    """Returns all events within the next N days."""
    today  = datetime.date.today()
    cutoff = today + datetime.timedelta(days=days_ahead)
    return [
        e for e in get_all_events()
        if today - datetime.timedelta(days=30) <= e["event_date"] <= cutoff
    ]


def days_until_label(days):
    """Human-readable label for days until event."""
    if days < 0:
        return f"{abs(days)}d ago"
    elif days == 0:
        return "TODAY"
    elif days == 1:
        return "Tomorrow"
    elif days <= 7:
        return f"In {days} days"
    elif days <= 30:
        weeks = days // 7
        return f"In {weeks}w {days % 7}d"
    else:
        return f"In {days} days"


# Category colours
CATEGORY_COLORS = {
    "RBI MPC":      {"bg": "#e8f0fe", "border": "#4361ee", "text": "#1a237e"},
    "CPI":          {"bg": "#fff3e0", "border": "#f4a261", "text": "#7a4000"},
    "GDP":          {"bg": "#e8f5e9", "border": "#2d7a3a", "text": "#1b5e20"},
    "Union Budget": {"bg": "#fce8ff", "border": "#9c27b0", "text": "#4a148c"},
    "SEBI":         {"bg": "#f3e5f5", "border": "#7c3aed", "text": "#311b92"},
}

IMPORTANCE_COLORS = {
    "HIGH":   {"bg": "#fce8e8", "text": "#8b1a1a"},
    "MEDIUM": {"bg": "#fff8e1", "text": "#7a4000"},
}