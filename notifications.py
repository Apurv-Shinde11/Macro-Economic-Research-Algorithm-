"""
╔══════════════════════════════════════════════════════════════════════╗
║               SENTINEL — notifications.py                           ║
║               Intelligent Push Notification Engine                  ║
║                                                                      ║
║  Delivery-agnostic. Email now, WhatsApp slot-in later.              ║
║  All Tier 1 + Tier 2 alert types. Fatigue controls built-in.        ║
╚══════════════════════════════════════════════════════════════════════╝

SUPABASE SCHEMA — run these SQL statements before deploying:
─────────────────────────────────────────────────────────────────────

    -- Stores every notification sent (audit trail + fatigue control)
    CREATE TABLE IF NOT EXISTS notification_logs (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id         UUID REFERENCES auth.users(id) ON DELETE CASCADE,
        alert_type      TEXT NOT NULL,
        channel         TEXT NOT NULL DEFAULT 'email',   -- 'email' | 'whatsapp'
        subject         TEXT,
        body            TEXT,
        payload         JSONB,                           -- raw data that triggered it
        sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        status          TEXT DEFAULT 'sent'              -- 'sent' | 'failed' | 'suppressed'
    );

    -- Per-user alert preferences and thresholds
    CREATE TABLE IF NOT EXISTS notification_preferences (
        user_id                     UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
        email                       TEXT,
        whatsapp_number             TEXT,                -- e.g. '+919876543210' — for Phase 2
        channel                     TEXT DEFAULT 'email',

        -- Tier 1 toggles
        regime_shift_enabled        BOOLEAN DEFAULT TRUE,
        confidence_breach_enabled   BOOLEAN DEFAULT TRUE,
        extreme_signal_enabled      BOOLEAN DEFAULT TRUE,

        -- Tier 2 toggles
        fii_flow_enabled            BOOLEAN DEFAULT TRUE,
        rbi_event_enabled           BOOLEAN DEFAULT TRUE,
        weekly_summary_enabled      BOOLEAN DEFAULT TRUE,

        -- Tier 1 thresholds
        confidence_breach_threshold FLOAT   DEFAULT 45.0,   -- challenger % that triggers alert
        vix_threshold               FLOAT   DEFAULT 22.0,
        inr_threshold               FLOAT   DEFAULT 85.0,   -- INR/USD
        yield_spike_bps             FLOAT   DEFAULT 20.0,   -- 10Y bps jump in one session
        crude_threshold             FLOAT   DEFAULT 95.0,   -- USD/barrel

        -- Tier 2 thresholds
        fii_outflow_crore           FLOAT   DEFAULT 5000.0, -- consecutive-day net outflow
        fii_consecutive_days        INT     DEFAULT 3,

        -- Fatigue controls
        quiet_hours_start           INT     DEFAULT 22,     -- 10 PM IST
        quiet_hours_end             INT     DEFAULT 7,      -- 7 AM IST
        max_alerts_per_day          INT     DEFAULT 2,
        min_gap_hours               INT     DEFAULT 6,

        -- Tier 3 stub — custom user-defined alerts (UI to be built later)
        custom_alerts               JSONB   DEFAULT '[]'::jsonb,

        updated_at                  TIMESTAMPTZ DEFAULT NOW()
    );

    -- Indexes for efficient fatigue queries
    CREATE INDEX IF NOT EXISTS idx_notif_logs_user_sent
        ON notification_logs(user_id, sent_at DESC);
    CREATE INDEX IF NOT EXISTS idx_notif_logs_type
        ON notification_logs(user_id, alert_type, sent_at DESC);

─────────────────────────────────────────────────────────────────────
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz
import supabase as sb

logger = logging.getLogger("sentinel.notifications")

# ── IST timezone ──────────────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

# ── Alert type constants ───────────────────────────────────────────────────────
# Tier 1
ALERT_REGIME_SHIFT          = "regime_shift"
ALERT_CONFIDENCE_BREACH     = "confidence_breach"
ALERT_EXTREME_SIGNAL        = "extreme_signal"
# Tier 2
ALERT_FII_FLOW              = "fii_flow"
ALERT_RBI_EVENT             = "rbi_event"
ALERT_WEEKLY_SUMMARY        = "weekly_summary"
# Internal
ALERT_SUPPRESSED            = "suppressed"


# ══════════════════════════════════════════════════════════════════════════════
# DELIVERY LAYER
# Swap the internals of _send_email / _send_whatsapp without touching anything
# above. NotificationEngine never calls Twilio or SendGrid directly.
# ══════════════════════════════════════════════════════════════════════════════

def _send_email(to_address: str, subject: str, body_html: str) -> bool:
    """
    Send via your existing email provider (SendGrid / SMTP).
    Copy the send logic from your existing scheduler/email module here.
    Returns True on success, False on failure.
    """
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        import streamlit as st

        smtp_host   = os.environ.get("SMTP_HOST",  os.environ.get("GMAIL_SENDER", "smtp.gmail.com"))
        smtp_port   = int(os.environ.get("SMTP_PORT", 587))
        smtp_user   = os.environ.get("SMTP_USER",  os.environ.get("GMAIL_SENDER", ""))
        smtp_pass   = os.environ.get("SMTP_PASS",  os.environ.get("GMAIL_APP_PASS", ""))
        from_addr   = os.environ.get("FROM_EMAIL", smtp_user)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"SENTINEL Intelligence <{from_addr}>"
        msg["To"]      = to_address
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_address, msg.as_string())

        logger.info(f"Email sent → {to_address} | {subject}")
        return True

    except Exception as e:
        logger.error(f"Email send failed → {to_address}: {e}")
        return False


def _send_whatsapp(to_number: str, body_text: str) -> bool:
    """
    ── PHASE 2 SLOT-IN ──────────────────────────────────────────────
    Replace the stub below with Twilio / Meta Cloud API call.
    to_number format: '+919876543210'

    Example (Twilio):
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        client.messages.create(
            from_='whatsapp:+14155238886',
            to=f'whatsapp:{to_number}',
            body=body_text
        )
    ─────────────────────────────────────────────────────────────────
    """
    logger.info(f"[WhatsApp STUB] → {to_number}: {body_text[:80]}...")
    return True  # swap to actual send + return result


def _deliver(channel: str, address: str, subject: str,
             body_html: str, body_text: str) -> bool:
    """Route to the correct delivery channel."""
    if channel == "whatsapp":
        return _send_whatsapp(address, body_text)
    return _send_email(address, subject, body_html)


# ══════════════════════════════════════════════════════════════════════════════
# FATIGUE CONTROLLER
# ══════════════════════════════════════════════════════════════════════════════

class FatigueController:
    """
    Prevents notification overload per user.
    Rules:
      1. Minimum N hours between any two notifications (default 6h)
      2. Maximum M notifications per calendar day IST (default 2)
      3. No notifications during quiet hours IST (default 10pm–7am)
    """

    def __init__(self, client: sb.Client):
        self.db = client

    def is_quiet_hours(self, prefs: dict) -> bool:
        now_ist  = datetime.now(IST)
        hour     = now_ist.hour
        qs       = prefs.get("quiet_hours_start", 22)
        qe       = prefs.get("quiet_hours_end",   7)
        # Handles midnight wrap (e.g. 22 → 7)
        if qs > qe:
            return hour >= qs or hour < qe
        return qs <= hour < qe

    def last_notification_at(self, user_id: str) -> Optional[datetime]:
        result = (
            self.db.table("notification_logs")
            .select("sent_at")
            .eq("user_id", user_id)
            .neq("status", "suppressed")
            .order("sent_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return datetime.fromisoformat(result.data[0]["sent_at"])
        return None

    def notifications_today(self, user_id: str) -> int:
        now_ist     = datetime.now(IST)
        start_of_day = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc   = start_of_day.astimezone(timezone.utc).isoformat()
        result = (
            self.db.table("notification_logs")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .neq("status", "suppressed")
            .gte("sent_at", start_utc)
            .execute()
        )
        return result.count or 0

    def last_alert_of_type(self, user_id: str, alert_type: str) -> Optional[datetime]:
        result = (
            self.db.table("notification_logs")
            .select("sent_at")
            .eq("user_id", user_id)
            .eq("alert_type", alert_type)
            .neq("status", "suppressed")
            .order("sent_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return datetime.fromisoformat(result.data[0]["sent_at"])
        return None

    def can_send(self, user_id: str, prefs: dict, alert_type: str) -> tuple[bool, str]:
        """
        Returns (True, "") if notification is allowed.
        Returns (False, reason) if suppressed.
        """
        if self.is_quiet_hours(prefs):
            return False, "quiet_hours"

        today_count = self.notifications_today(user_id)
        if today_count >= prefs.get("max_alerts_per_day", 2):
            return False, f"daily_cap_reached ({today_count})"

        last_sent = self.last_notification_at(user_id)
        if last_sent:
            gap_hours = prefs.get("min_gap_hours", 6)
            elapsed   = (datetime.now(timezone.utc) - last_sent).total_seconds() / 3600
            if elapsed < gap_hours:
                return False, f"min_gap ({elapsed:.1f}h < {gap_hours}h)"

        return True, ""


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE FORMATTERS
# Each alert type has: _fmt_<type>_subject(), _fmt_<type>_html(), _fmt_<type>_text()
# ══════════════════════════════════════════════════════════════════════════════

def _regime_color(regime: str) -> str:
    palette = {
        "STABLE_GROWTH":                    "#22c55e",
        "LIQUIDITY_DRIVEN_EXPANSION":       "#3b82f6",
        "EARLY_CYCLE_RECOVERY":             "#a3e635",
        "GROWTH_SLOWDOWN_SUPPORT":          "#f59e0b",
        "MONETARY_TIGHTENING":              "#f97316",
        "LIQUIDITY_TIGHTENING":             "#ef4444",
        "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": "#dc2626",
        "STAGFLATION_RISK":                 "#7f1d1d",
    }
    return palette.get(regime, "#94a3b8")


def _base_html(content: str, title: str = "SENTINEL Alert") -> str:
    return f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body      {{ font-family: 'Georgia', serif; background: #0d0f1a; color: #e2e8f0; margin: 0; padding: 0; }}
  .wrap     {{ max-width: 600px; margin: 0 auto; padding: 32px 24px; }}
  .header   {{ border-bottom: 1px solid #1e2235; padding-bottom: 16px; margin-bottom: 24px; }}
  .logo     {{ font-size: 13px; letter-spacing: 0.2em; color: #64748b; font-family: monospace; }}
  .logo span{{ color: #f8fafc; }}
  .badge    {{ display: inline-block; font-size: 10px; letter-spacing: 0.15em;
               padding: 3px 10px; border-radius: 3px; margin-top: 6px;
               background: #1e2235; color: #94a3b8; border: 1px solid #2d3555; }}
  .content  {{ line-height: 1.8; }}
  .footer   {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #1e2235;
               font-size: 11px; color: #475569; }}
</style></head>
<body><div class="wrap">
  <div class="header">
    <div class="logo"><span>SENTINEL</span> INTELLIGENCE</div>
    <div class="badge">MACRO ALERT · {datetime.now(IST).strftime('%d %b %Y · %H:%M IST')}</div>
  </div>
  <div class="content">{content}</div>
  <div class="footer">
    This is an automated alert from SENTINEL Macroeconomic Intelligence.
    It is not investment advice. Past regime signals do not guarantee future performance.
    <br>Manage your alert preferences in the SENTINEL dashboard.
  </div>
</div></body></html>"""


# ── Tier 1: Regime Shift ──────────────────────────────────────────────────────

def _fmt_regime_shift(old_regime: str, new_regime: str,
                      confidence: float, playbook: dict) -> tuple[str, str, str]:
    rec         = playbook.get("recommendation", "Review allocation")
    eq_stance   = playbook.get("equity_stance", "—")
    top_actions = playbook.get("top_actions", [])
    actions_html = "".join(f"<li>{a}</li>" for a in top_actions[:3])
    color       = _regime_color(new_regime)

    subject = f"SENTINEL: Regime Shift — {new_regime.replace('_', ' ').title()}"

    html_body = f"""
    <h2 style="color:#f8fafc;font-size:18px;margin:0 0 4px">
        ⚡ Macro Regime Shift Detected
    </h2>
    <p style="color:#64748b;font-size:13px;margin:0 0 24px">
        SENTINEL has detected a confirmed regime transition.
    </p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px;width:40%">Previous Regime</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f59e0b;border-radius:0 6px 6px 0">{old_regime.replace('_',' ')}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">New Regime</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:14px;font-weight:bold;
                   color:{color};border-radius:0 6px 6px 0">{new_regime.replace('_',' ')}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Confidence</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f8fafc;border-radius:0 6px 6px 0">{confidence:.1f}%</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Equity Stance</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f8fafc;border-radius:0 6px 6px 0">{eq_stance}</td>
      </tr>
    </table>
    <p style="color:#94a3b8;font-size:13px;margin:0 0 8px"><strong style="color:#f8fafc">
        Recommended Actions:
    </strong></p>
    <ul style="color:#cbd5e1;font-size:13px;line-height:2;padding-left:18px;margin:0 0 20px">
        {actions_html}
    </ul>
    <p style="background:#1e2235;border-left:3px solid {color};padding:12px 16px;
              border-radius:0 6px 6px 0;color:#94a3b8;font-size:12px;margin:0">
        {rec}
    </p>"""

    text_body = (
        f"SENTINEL ALERT: Regime Shift\n\n"
        f"Previous: {old_regime}\nNew:      {new_regime}\n"
        f"Confidence: {confidence:.1f}%\nEquity: {eq_stance}\n\n"
        f"Recommendation: {rec}\n\n"
        f"Actions:\n" + "\n".join(f"- {a}" for a in top_actions[:3])
    )

    return subject, _base_html(html_body, subject), text_body


# ── Tier 1: Confidence Threshold Breach ──────────────────────────────────────

def _fmt_confidence_breach(dominant: str, challenger: str,
                           challenger_conf: float, threshold: float) -> tuple[str, str, str]:
    color   = _regime_color(challenger)
    subject = f"SENTINEL: Challenger Regime at {challenger_conf:.0f}% — Early Warning"

    html_body = f"""
    <h2 style="color:#f8fafc;font-size:18px;margin:0 0 4px">
        ⚠️ Challenger Regime Crossing Alert Threshold
    </h2>
    <p style="color:#64748b;font-size:13px;margin:0 0 24px">
        A challenger regime has crossed your {threshold:.0f}% early-warning threshold.
        No regime shift has occurred yet — but conditions are building.
    </p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px;width:40%">Dominant Regime</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#22c55e;border-radius:0 6px 6px 0">{dominant.replace('_',' ')}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Challenger Regime</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:14px;font-weight:bold;
                   color:{color};border-radius:0 6px 6px 0">{challenger.replace('_',' ')}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Challenger Confidence</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f59e0b;border-radius:0 6px 6px 0">{challenger_conf:.1f}%
                   <span style="color:#475569"> (threshold: {threshold:.0f}%)</span></td>
      </tr>
    </table>
    <p style="background:#1e2235;border-left:3px solid #f59e0b;padding:12px 16px;
              border-radius:0 6px 6px 0;color:#94a3b8;font-size:12px;margin:0">
        Monitor closely. If challenger confidence exceeds 50%, a regime shift may be imminent.
        No allocation change is recommended yet — stay alert.
    </p>"""

    text_body = (
        f"SENTINEL WARNING: Challenger Regime Alert\n\n"
        f"Dominant:   {dominant}\n"
        f"Challenger: {challenger} at {challenger_conf:.1f}% (threshold: {threshold:.0f}%)\n\n"
        f"No regime shift yet. Monitor closely."
    )

    return subject, _base_html(html_body, subject), text_body


# ── Tier 1: Extreme Signal Alert ─────────────────────────────────────────────

def _fmt_extreme_signal(signal_name: str, value: float, threshold: float,
                        direction: str, context: str) -> tuple[str, str, str]:
    arrow   = "↑" if direction == "above" else "↓"
    subject = f"SENTINEL: Extreme Signal — {signal_name} {arrow} {threshold}"

    html_body = f"""
    <h2 style="color:#f8fafc;font-size:18px;margin:0 0 4px">
        🔴 Extreme Market Signal
    </h2>
    <p style="color:#64748b;font-size:13px;margin:0 0 24px">
        A key macro input has crossed a critical threshold.
    </p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px;width:40%">Signal</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f8fafc;border-radius:0 6px 6px 0">{signal_name}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Current Value</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:14px;font-weight:bold;
                   color:#ef4444;border-radius:0 6px 6px 0">{value:.2f}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Threshold Crossed</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f59e0b;border-radius:0 6px 6px 0">{direction} {threshold}</td>
      </tr>
    </table>
    <p style="background:#1e2235;border-left:3px solid #ef4444;padding:12px 16px;
              border-radius:0 6px 6px 0;color:#94a3b8;font-size:12px;margin:0">
        {context}
    </p>"""

    text_body = (
        f"SENTINEL ALERT: Extreme Signal\n\n"
        f"Signal:    {signal_name}\n"
        f"Value:     {value:.2f}\n"
        f"Threshold: {direction} {threshold}\n\n"
        f"{context}"
    )

    return subject, _base_html(html_body, subject), text_body


# ── Tier 2: FII Flow Alert ────────────────────────────────────────────────────

def _fmt_fii_flow(net_flow_crore: float, consecutive_days: int,
                  rolling_5d: float) -> tuple[str, str, str]:
    direction = "Outflow" if net_flow_crore < 0 else "Inflow"
    color     = "#ef4444" if net_flow_crore < 0 else "#22c55e"
    arrow     = "↓" if net_flow_crore < 0 else "↑"
    subject   = f"SENTINEL: FII {direction} Alert — {consecutive_days} consecutive sessions"

    html_body = f"""
    <h2 style="color:#f8fafc;font-size:18px;margin:0 0 4px">
        📊 FII Flow Alert
    </h2>
    <p style="color:#64748b;font-size:13px;margin:0 0 24px">
        Sustained FII {direction.lower()} activity detected across {consecutive_days} consecutive sessions.
    </p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px;width:40%">Today's Net Flow</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:14px;font-weight:bold;
                   color:{color};border-radius:0 6px 6px 0">
                   {arrow} ₹{abs(net_flow_crore):,.0f} Cr</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Consecutive Days</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f8fafc;border-radius:0 6px 6px 0">{consecutive_days} sessions</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">5-Day Net Flow</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:{color};border-radius:0 6px 6px 0">₹{rolling_5d:,.0f} Cr</td>
      </tr>
    </table>
    <p style="background:#1e2235;border-left:3px solid {color};padding:12px 16px;
              border-radius:0 6px 6px 0;color:#94a3b8;font-size:12px;margin:0">
        Sustained FII {direction.lower()} over {consecutive_days}+ sessions is a leading indicator
        of liquidity regime transition. Review SENTINEL's current liquidity score.
    </p>"""

    text_body = (
        f"SENTINEL: FII {direction} Alert\n\n"
        f"Today: {arrow} Rs.{abs(net_flow_crore):,.0f} Cr\n"
        f"Consecutive sessions: {consecutive_days}\n"
        f"5-day rolling: Rs.{rolling_5d:,.0f} Cr"
    )

    return subject, _base_html(html_body, subject), text_body


# ── Tier 2: RBI Event Reminder ────────────────────────────────────────────────

def _fmt_rbi_event(event_name: str, event_date: str,
                   hours_ahead: int, current_regime: str,
                   regime_context: str) -> tuple[str, str, str]:
    color   = _regime_color(current_regime)
    subject = f"SENTINEL: RBI Event Tomorrow — {event_name}"

    html_body = f"""
    <h2 style="color:#f8fafc;font-size:18px;margin:0 0 4px">
        🏦 RBI Event Reminder
    </h2>
    <p style="color:#64748b;font-size:13px;margin:0 0 24px">
        A key RBI event is {hours_ahead} hours away. Current macro regime context below.
    </p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px;width:40%">Event</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f8fafc;border-radius:0 6px 6px 0">{event_name}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Date</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f8fafc;border-radius:0 6px 6px 0">{event_date}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Current Regime</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:{color};border-radius:0 6px 6px 0">{current_regime.replace('_',' ')}</td>
      </tr>
    </table>
    <p style="background:#1e2235;border-left:3px solid {color};padding:12px 16px;
              border-radius:0 6px 6px 0;color:#94a3b8;font-size:12px;margin:0">
        {regime_context}
    </p>"""

    text_body = (
        f"SENTINEL: RBI Event Reminder\n\n"
        f"Event: {event_name}\nDate:  {event_date}\n"
        f"Current Regime: {current_regime}\n\n{regime_context}"
    )

    return subject, _base_html(html_body, subject), text_body


# ── Tier 2: Weekly Summary ────────────────────────────────────────────────────

def _fmt_weekly_summary(regime: str, confidence: float, regime_stable: bool,
                        alerts_fired: int, week_signals: list,
                        watch_next_week: list) -> tuple[str, str, str]:
    color        = _regime_color(regime)
    stability    = "Stable" if regime_stable else "Transitioning"
    stab_color   = "#22c55e" if regime_stable else "#f59e0b"
    signals_html = "".join(f"<li>{s}</li>" for s in week_signals[:4])
    watch_html   = "".join(f"<li>{w}</li>" for w in watch_next_week[:3])
    week_str     = datetime.now(IST).strftime("Week ending %d %b %Y")
    subject      = f"SENTINEL Weekly Digest — {week_str}"

    html_body = f"""
    <h2 style="color:#f8fafc;font-size:18px;margin:0 0 4px">
        📋 Weekly Macro Digest
    </h2>
    <p style="color:#64748b;font-size:13px;margin:0 0 24px">{week_str}</p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px;width:40%">Regime</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:{color};border-radius:0 6px 6px 0">{regime.replace('_',' ')}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Confidence</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f8fafc;border-radius:0 6px 6px 0">{confidence:.1f}%</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Regime Stability</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:{stab_color};border-radius:0 6px 6px 0">{stability}</td>
      </tr>
      <tr><td colspan="2" style="padding:4px"></td></tr>
      <tr>
        <td style="padding:10px 14px;background:#1e2235;border-radius:6px 0 0 6px;
                   color:#64748b;font-size:12px">Alerts This Week</td>
        <td style="padding:10px 14px;background:#1e2235;font-size:13px;
                   color:#f8fafc;border-radius:0 6px 6px 0">{alerts_fired}</td>
      </tr>
    </table>
    <p style="color:#94a3b8;font-size:13px;margin:0 0 8px">
        <strong style="color:#f8fafc">Signals This Week:</strong></p>
    <ul style="color:#cbd5e1;font-size:13px;line-height:2;padding-left:18px;margin:0 0 20px">
        {signals_html}
    </ul>
    <p style="color:#94a3b8;font-size:13px;margin:0 0 8px">
        <strong style="color:#f8fafc">Watch Next Week:</strong></p>
    <ul style="color:#cbd5e1;font-size:13px;line-height:2;padding-left:18px;margin:0">
        {watch_html}
    </ul>"""

    text_body = (
        f"SENTINEL Weekly Digest — {week_str}\n\n"
        f"Regime:    {regime} ({confidence:.1f}%)\n"
        f"Stability: {stability}\n"
        f"Alerts fired this week: {alerts_fired}\n\n"
        f"Signals:\n" + "\n".join(f"- {s}" for s in week_signals[:4]) + "\n\n"
        f"Watch next week:\n" + "\n".join(f"- {w}" for w in watch_next_week[:3])
    )

    return subject, _base_html(html_body, subject), text_body


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION ENGINE  — main class
# ══════════════════════════════════════════════════════════════════════════════

class NotificationEngine:
    """
    Central orchestrator. Call check_and_send_all() from the scheduler
    after every pipeline run. Call send_weekly_summary() from a Friday cron.

    Usage:
        engine = NotificationEngine(supabase_client)
        engine.check_and_send_all(pipeline_output)
    """

    def __init__(self, supabase_client: sb.Client):
        self.db      = supabase_client
        self.fatigue = FatigueController(supabase_client)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_all_prefs(self) -> list[dict]:
        result = self.db.table("notification_preferences").select("*").execute()
        return result.data or []

    def _log(self, user_id: str, alert_type: str, channel: str,
             subject: str, body: str, payload: dict, status: str = "sent"):
        self.db.table("notification_logs").insert({
            "user_id":    user_id,
            "alert_type": alert_type,
            "channel":    channel,
            "subject":    subject,
            "body":       body[:4000],
            "payload":    payload,
            "status":     status,
            "sent_at":    datetime.now(timezone.utc).isoformat(),
        }).execute()

    def _try_send(self, user_id: str, prefs: dict, alert_type: str,
                  subject: str, html: str, text: str, payload: dict) -> bool:
        """
        Runs fatigue check → delivers → logs. Returns True if sent.
        """
        channel = prefs.get("channel", "email")
        address = (
            prefs.get("whatsapp_number") if channel == "whatsapp"
            else prefs.get("email")
        )
        if not address:
            logger.warning(f"No delivery address for user {user_id}, skipping.")
            return False

        ok, reason = self.fatigue.can_send(user_id, prefs, alert_type)
        if not ok:
            logger.info(f"Suppressed [{alert_type}] for {user_id}: {reason}")
            self._log(user_id, alert_type, channel, subject, "", payload,
                      status=f"suppressed:{reason}")
            return False

        success = _deliver(channel, address, subject, html, text)
        status  = "sent" if success else "failed"
        self._log(user_id, alert_type, channel, subject, html, payload, status)
        return success

    # ── Public interface ──────────────────────────────────────────────────────

    def check_and_send_all(self, pipeline_output: dict):
        """
        Main entry point. Call this from your scheduler after every pipeline run.

        pipeline_output is the dict that main.py already produces. Expected keys:
            regime              str   — current dominant regime
            confidence          float — 0-100
            challenger_regime   str   — second-place regime
            challenger_conf     float — 0-100
            previous_regime     str   — from notification_logs or regime_archive
            playbook            dict  — recommendation, equity_stance, top_actions
            ticker_data         dict  — {"VIX": {...}, "INR": {...}, "Crude": {...}, ...}
            yield_data          dict  — {"10Y": {...}}
            fii_data            dict  — net_flow_crore, consecutive_outflow_days, rolling_5d
            rbi_events          list  — [{"name":..., "date":..., "hours_ahead":...}]
        """
        all_prefs = self._get_all_prefs()
        if not all_prefs:
            logger.info("No notification preferences found. Skipping.")
            return

        for prefs in all_prefs:
            user_id = prefs["user_id"]
            try:
                self._check_tier1(user_id, prefs, pipeline_output)
                self._check_tier2(user_id, prefs, pipeline_output)
            except Exception as e:
                logger.error(f"Notification check failed for {user_id}: {e}")

    def _check_tier1(self, user_id: str, prefs: dict, po: dict):
        regime          = po.get("regime", "")
        confidence      = po.get("confidence", 0.0)
        challenger      = po.get("challenger_regime", "")
        challenger_conf = po.get("challenger_conf", 0.0)
        previous_regime = po.get("previous_regime", "")
        playbook        = po.get("playbook", {})
        ticker          = po.get("ticker_data", {})
        yield_data      = po.get("yield_data", {})

        # ── T1-A: Regime Shift ────────────────────────────────────────────────
        if (prefs.get("regime_shift_enabled", True)
                and previous_regime
                and regime != previous_regime):
            subj, html, text = _fmt_regime_shift(
                previous_regime, regime, confidence, playbook)
            self._try_send(user_id, prefs, ALERT_REGIME_SHIFT, subj, html, text,
                           {"old": previous_regime, "new": regime,
                            "confidence": confidence})

        # ── T1-B: Confidence Threshold Breach ────────────────────────────────
        threshold = prefs.get("confidence_breach_threshold", 45.0)
        if (prefs.get("confidence_breach_enabled", True)
                and challenger
                and challenger_conf >= threshold):
            # Only alert if we haven't already for this challenger at this level
            last = self.fatigue.last_alert_of_type(user_id, ALERT_CONFIDENCE_BREACH)
            hours_since = (
                (datetime.now(timezone.utc) - last).total_seconds() / 3600
                if last else 999
            )
            if hours_since > 24:   # max once/day for confidence breach
                subj, html, text = _fmt_confidence_breach(
                    regime, challenger, challenger_conf, threshold)
                self._try_send(user_id, prefs, ALERT_CONFIDENCE_BREACH,
                               subj, html, text,
                               {"challenger": challenger, "conf": challenger_conf})

        # ── T1-C: Extreme Signal Alerts ───────────────────────────────────────
        if prefs.get("extreme_signal_enabled", True):
            extreme_checks = [
                # (signal_name, value_path, threshold_key, direction, context)
                (
                    "India VIX",
                    ticker.get("VIX", {}).get("price"),
                    prefs.get("vix_threshold", 22.0),
                    "above",
                    "Elevated VIX signals heightened equity uncertainty. "
                    "Consider reducing exposure to high-beta positions."
                ),
                (
                    "INR/USD",
                    ticker.get("INR", {}).get("price"),
                    prefs.get("inr_threshold", 85.0),
                    "above",
                    "INR weakness beyond 85 heightens imported inflation risk "
                    "and pressures RBI to tighten. FII flows may be impacted."
                ),
                (
                    "Crude Oil (USD/bbl)",
                    ticker.get("Crude", {}).get("price"),
                    prefs.get("crude_threshold", 95.0),
                    "above",
                    "Crude above threshold widens India's current account deficit "
                    "and raises stagflation risk. Monitor energy-linked sectors."
                ),
            ]

            # 10Y Yield spike (bps change, not absolute level)
            yield_10y_change = yield_data.get("10Y", {}).get("change_bps", 0.0)
            yield_threshold  = prefs.get("yield_spike_bps", 20.0)
            if yield_10y_change and abs(yield_10y_change) >= yield_threshold:
                direction = "above" if yield_10y_change > 0 else "below"
                extreme_checks.append((
                    "10Y G-Sec Yield (bps move)",
                    abs(yield_10y_change),
                    yield_threshold,
                    direction,
                    f"10Y yield moved {yield_10y_change:+.1f} bps in one session. "
                    f"Significant yield moves indicate shifting rate expectations — "
                    f"review fixed income and rate-sensitive equity positions."
                ))

            for sig_name, value, threshold, direction, context in extreme_checks:
                if value is None:
                    continue
                triggered = (
                    value >= threshold if direction == "above" else value <= threshold
                )
                if triggered:
                    # One extreme alert per signal per 12 hours
                    last = self.fatigue.last_alert_of_type(
                        user_id, f"{ALERT_EXTREME_SIGNAL}_{sig_name}")
                    hours_since = (
                        (datetime.now(timezone.utc) - last).total_seconds() / 3600
                        if last else 999
                    )
                    if hours_since >= 12:
                        subj, html, text = _fmt_extreme_signal(
                            sig_name, value, threshold, direction, context)
                        self._try_send(
                            user_id, prefs,
                            f"{ALERT_EXTREME_SIGNAL}_{sig_name}",
                            subj, html, text,
                            {"signal": sig_name, "value": value,
                             "threshold": threshold}
                        )

    def _check_tier2(self, user_id: str, prefs: dict, po: dict):
        regime     = po.get("regime", "")
        fii        = po.get("fii_data", {})
        rbi_events = po.get("rbi_events", [])

        # ── T2-A: FII Flow Alert ──────────────────────────────────────────────
        if prefs.get("fii_flow_enabled", True) and fii:
            net_flow      = fii.get("net_flow_crore", 0.0)
            consec_days   = fii.get("consecutive_outflow_days", 0)
            rolling_5d    = fii.get("rolling_5d", 0.0)
            threshold_amt = prefs.get("fii_outflow_crore", 5000.0)
            threshold_days = prefs.get("fii_consecutive_days", 3)

            if (abs(net_flow) >= threshold_amt
                    and consec_days >= threshold_days):
                last = self.fatigue.last_alert_of_type(user_id, ALERT_FII_FLOW)
                hours_since = (
                    (datetime.now(timezone.utc) - last).total_seconds() / 3600
                    if last else 999
                )
                if hours_since >= 24:
                    subj, html, text = _fmt_fii_flow(
                        net_flow, consec_days, rolling_5d)
                    self._try_send(user_id, prefs, ALERT_FII_FLOW,
                                   subj, html, text,
                                   {"net_flow": net_flow,
                                    "days": consec_days})

        # ── T2-B: RBI Event Reminder (24h ahead) ──────────────────────────────
        if prefs.get("rbi_event_enabled", True):
            for event in rbi_events:
                hours_ahead = event.get("hours_ahead", 999)
                if 20 <= hours_ahead <= 28:   # ~24h window
                    regime_context = _rbi_regime_context(
                        regime, event.get("name", ""))
                    subj, html, text = _fmt_rbi_event(
                        event["name"],
                        event.get("date", ""),
                        hours_ahead,
                        regime,
                        regime_context
                    )
                    self._try_send(user_id, prefs, ALERT_RBI_EVENT,
                                   subj, html, text,
                                   {"event": event["name"],
                                    "hours_ahead": hours_ahead})

    def send_weekly_summary(self, pipeline_output: dict):
        """
        Call this from a Friday 6pm IST cron job.
        Separate from check_and_send_all — not subject to daily fatigue cap.
        """
        all_prefs = self._get_all_prefs()
        regime    = pipeline_output.get("regime", "")
        conf      = pipeline_output.get("confidence", 0.0)
        stable    = pipeline_output.get("regime_stable", True)

        for prefs in all_prefs:
            if not prefs.get("weekly_summary_enabled", True):
                continue
            user_id = prefs["user_id"]
            if self.is_quiet_hours_for_user(prefs):
                continue

            # Count alerts sent this week
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            result   = (
                self.db.table("notification_logs")
                .select("id, alert_type", count="exact")
                .eq("user_id", user_id)
                .eq("status", "sent")
                .gte("sent_at", week_ago)
                .execute()
            )
            alerts_fired = result.count or 0
            week_signals = pipeline_output.get("week_signals", [
                "Regime held stable throughout the week",
                "No extreme signal breaches detected",
                "FII flows within normal range",
            ])
            watch_list   = pipeline_output.get("watch_next_week", [
                "Monitor challenger regime confidence trajectory",
                "RBI MPC minutes release scheduled",
                "US Fed meeting — global liquidity implications",
            ])

            subj, html, text = _fmt_weekly_summary(
                regime, conf, stable, alerts_fired, week_signals, watch_list)
            # Weekly summary bypasses daily cap — log directly
            channel = prefs.get("channel", "email")
            address = (
                prefs.get("whatsapp_number") if channel == "whatsapp"
                else prefs.get("email")
            )
            if address and not self.fatigue.is_quiet_hours(prefs):
                success = _deliver(channel, address, subj, html, text)
                self._log(user_id, ALERT_WEEKLY_SUMMARY, channel, subj, html,
                          {"regime": regime, "confidence": conf},
                          status="sent" if success else "failed")

    def is_quiet_hours_for_user(self, prefs: dict) -> bool:
        return self.fatigue.is_quiet_hours(prefs)


# ══════════════════════════════════════════════════════════════════════════════
# RBI REGIME CONTEXT helper
# Generates a contextual sentence about the RBI event given current regime
# ══════════════════════════════════════════════════════════════════════════════

def _rbi_regime_context(regime: str, event_name: str) -> str:
    contexts = {
        "STABLE_GROWTH": (
            f"Current regime is STABLE_GROWTH. A status-quo hold at tomorrow's "
            f"{event_name} would confirm the supportive macro backdrop."
        ),
        "MONETARY_TIGHTENING": (
            f"Current regime is MONETARY_TIGHTENING. Tomorrow's {event_name} "
            f"could either confirm the tightening bias or signal a pivot — "
            f"either outcome has significant equity and bond implications."
        ),
        "LIQUIDITY_DRIVEN_EXPANSION": (
            f"Regime is LIQUIDITY_DRIVEN_EXPANSION. A dovish {event_name} "
            f"tomorrow would reinforce the liquidity tailwind. Any hawkish "
            f"surprise could put this regime under pressure."
        ),
        "LIQUIDITY_TIGHTENING": (
            f"Regime is LIQUIDITY_TIGHTENING. Tomorrow's {event_name} is a "
            f"critical event — signals of further tightening could deepen the "
            f"regime, while a pause could trigger a rapid regime transition."
        ),
        "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": (
            f"High inflation regime. Tomorrow's {event_name} is under pressure "
            f"to signal credible tightening. A dovish surprise would likely "
            f"widen rate-sensitive spreads."
        ),
        "STAGFLATION_RISK": (
            f"STAGFLATION_RISK regime — the most difficult environment for the "
            f"RBI. Tomorrow's {event_name} offers no easy policy options. "
            f"Watch for guidance on sequencing growth vs inflation priority."
        ),
        "GROWTH_SLOWDOWN_SUPPORT": (
            f"Growth slowdown regime. Market expects a supportive stance from "
            f"tomorrow's {event_name}. Any hint of tightening would be a "
            f"negative surprise."
        ),
        "EARLY_CYCLE_RECOVERY": (
            f"Early recovery regime. Tomorrow's {event_name} — the RBI is "
            f"likely to hold and signal accommodation to sustain the recovery."
        ),
    }
    return contexts.get(
        regime,
        f"Monitor tomorrow's {event_name} closely — any policy surprise "
        f"could shift regime confidence materially."
    )


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULER WIRING
# Add these two calls to your existing scheduler (scheduler.py / main.py)
# ══════════════════════════════════════════════════════════════════════════════
"""
HOW TO WIRE INTO YOUR EXISTING SCHEDULER
──────────────────────────────────────────────────────────────────────────────

1. Import at top of scheduler.py:

    from notifications import NotificationEngine

2. Initialise once (alongside your existing Supabase client):

    notif_engine = NotificationEngine(supabase_client)

3. After your existing daily pipeline run, add:

    # Attach previous regime from your regime_archive table
    pipeline_output["previous_regime"] = get_last_regime_from_archive(user_id)

    notif_engine.check_and_send_all(pipeline_output)

4. Add a separate Friday 6pm IST job for the weekly summary:

    from apscheduler.triggers.cron import CronTrigger

    scheduler.add_job(
        lambda: notif_engine.send_weekly_summary(pipeline_output),
        CronTrigger(day_of_week="fri", hour=18, minute=0, timezone=IST)
    )

──────────────────────────────────────────────────────────────────────────────
PIPELINE OUTPUT KEYS SENTINEL MUST POPULATE
──────────────────────────────────────────────────────────────────────────────

pipeline_output = {
    "regime":             "STABLE_GROWTH",
    "confidence":         72.4,
    "challenger_regime":  "GROWTH_SLOWDOWN_SUPPORT",
    "challenger_conf":    38.1,
    "previous_regime":    "STABLE_GROWTH",        # from archive
    "regime_stable":      True,
    "playbook": {
        "recommendation": "Maintain allocation, monitor yield curve.",
        "equity_stance":  "Overweight",
        "top_actions":    ["Hold large-cap equity", "Reduce duration risk", "Watch FII"],
    },
    "ticker_data": {
        "VIX":   {"price": 14.2},
        "INR":   {"price": 83.5},
        "Crude": {"price": 88.0},
    },
    "yield_data": {
        "10Y": {"price": 6.95, "change_bps": 8.0},
    },
    "fii_data": {
        "net_flow_crore":          -3200.0,
        "consecutive_outflow_days": 2,
        "rolling_5d":              -7400.0,
    },
    "rbi_events": [
        {"name": "MPC Interest Rate Decision", "date": "06 Jun 2025",
         "hours_ahead": 22},
    ],
    "week_signals":      ["Regime stable for 5 sessions", "VIX compressed"],
    "watch_next_week":   ["CPI data release", "US Fed minutes"],
}
"""