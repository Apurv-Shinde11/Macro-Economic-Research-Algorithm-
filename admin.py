"""
admin.py — SENTINEL Admin Panel
Run with: streamlit run admin.py

Only accessible to accounts with role = 'admin'.
Allows approving users, managing tiers, viewing usage.
"""

import streamlit as st
import datetime
import pandas as pd
from auth import get_supabase, is_logged_in, get_profile, render_auth_page
from supabase import create_client


st.set_page_config(
    page_title="SENTINEL Admin",
    layout="wide"
)

# =========================
# 🔐 AUTH GATE
# =========================
if not is_logged_in():
    render_auth_page()
    st.stop()

profile = get_profile()
if profile.get("role") != "admin":
    st.error("Access denied. Admin accounts only.")
    st.stop()

# =========================
# 🏛️ HEADER
# =========================
st.title("🏛️ SENTINEL Admin Panel")
st.caption(f"Logged in as: {profile.get('email', '')} — Admin")
st.divider()

supabase = get_supabase()

# =========================
# 📊 USAGE SUMMARY
# =========================
st.subheader("📊 Usage Summary")

try:
    all_profiles = supabase.table("profiles")\
        .select("*")\
        .order("created_at", desc=True)\
        .execute()
    profiles     = all_profiles.data or []

    all_runs     = supabase.table("runs")\
        .select("user_id, run_at, regime")\
        .order("run_at", desc=True)\
        .execute()
    runs         = all_runs.data or []

    total_users   = len(profiles)
    pending       = sum(1 for p in profiles if p.get("tier") == "pending")
    trial_users   = sum(1 for p in profiles if p.get("tier") == "trial")
    paid_users    = sum(1 for p in profiles if p.get("tier") == "paid")
    expired_users = sum(1 for p in profiles if p.get("tier") == "expired")
    total_runs    = len(runs)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total Users",   total_users)
    c2.metric("Pending",       pending,       delta="needs review" if pending else None)
    c3.metric("Trial",         trial_users)
    c4.metric("Paid",          paid_users)
    c5.metric("Expired",       expired_users)
    c6.metric("Total Runs",    total_runs)

except Exception as e:
    st.error(f"Error loading data: {e}")
    profiles = []
    runs     = []

st.divider()

# =========================
# 👥 USER MANAGEMENT
# =========================
st.subheader("👥 User Management")

# Filter controls
filter_tier = st.selectbox(
    "Filter by tier",
    ["All", "pending", "trial", "paid", "expired"],
    index=0
)

filtered = profiles if filter_tier == "All" else [
    p for p in profiles if p.get("tier") == filter_tier
]

if not filtered:
    st.info("No users found for this filter.")
else:
    for p in filtered:
        with st.expander(
            f"{p.get('email', 'Unknown')} — "
            f"{p.get('full_name', '')} — "
            f"{p.get('firm_name', '')} — "
            f"**{p.get('tier', 'pending').upper()}**"
        ):
            col1, col2 = st.columns([2, 1])

            with col1:
                st.markdown(f"**Email:** {p.get('email', '—')}")
                st.markdown(f"**Name:** {p.get('full_name', '—')}")
                st.markdown(f"**Firm:** {p.get('firm_name', '—')}")
                st.markdown(f"**Tier:** {p.get('tier', '—')}")
                st.markdown(f"**Joined:** {p.get('created_at', '—')[:10]}")
                st.markdown(f"**Last login:** {p.get('last_login', '—')[:16] if p.get('last_login') else 'Never'}")
                trial_end = p.get("trial_ends_at", "")
                if trial_end:
                    st.markdown(f"**Trial ends:** {trial_end[:10]}")
                if p.get("notes"):
                    st.markdown(f"**Notes:** {p.get('notes')}")

                # Run count for this user
                user_runs = [r for r in runs if r.get("user_id") == p.get("id")]
                st.markdown(f"**Total runs:** {len(user_runs)}")

            with col2:
                uid  = p.get("id")
                tier = p.get("tier", "pending")

                # Approve as trial
                if tier in ["pending", "expired"]:
                    trial_days = st.number_input(
                        "Trial days",
                        min_value=7, max_value=90,
                        value=14, key=f"days_{uid}"
                    )
                    if st.button("✅ Approve (Trial)", key=f"approve_{uid}",
                                 use_container_width=True):
                        trial_end = (
                            datetime.datetime.utcnow() +
                            datetime.timedelta(days=trial_days)
                        ).isoformat()
                        supabase.table("profiles").update({
                            "tier":          "trial",
                            "trial_ends_at": trial_end,
                            "approved_by":   profile.get("email", "admin")
                        }).eq("id", uid).execute()
                        st.success(f"Approved for {trial_days}-day trial.")
                        st.rerun()

                # Upgrade to paid
                if tier in ["trial", "expired", "pending"]:
                    if st.button("💳 Mark as Paid", key=f"paid_{uid}",
                                 use_container_width=True):
                        supabase.table("profiles").update({
                            "tier":          "paid",
                            "trial_ends_at": None
                        }).eq("id", uid).execute()
                        st.success("Marked as paid.")
                        st.rerun()

                # Suspend
                if tier not in ["pending", "expired"]:
                    if st.button("🚫 Suspend", key=f"suspend_{uid}",
                                 use_container_width=True):
                        supabase.table("profiles").update({
                            "tier": "expired"
                        }).eq("id", uid).execute()
                        st.warning("Account suspended.")
                        st.rerun()

                # Notes
                new_note = st.text_input(
                    "Add note", key=f"note_{uid}",
                    placeholder="e.g. Referred by Ajay, SEBI reg pending"
                )
                if st.button("Save note", key=f"savenote_{uid}"):
                    supabase.table("profiles").update({
                        "notes": new_note
                    }).eq("id", uid).execute()
                    st.success("Note saved.")
                    st.rerun()

st.divider()

# =========================
# 📈 RECENT ACTIVITY
# =========================
st.subheader("📈 Recent Pipeline Runs")

if runs:
    email_map = {p["id"]: p.get("email", "—") for p in profiles}
    run_rows  = []
    for r in runs[:50]:
        run_rows.append({
            "User":      email_map.get(r.get("user_id"), "—"),
            "Run at":    r.get("run_at", "")[:16],
            "Regime":    str(r.get("regime", "")).replace("_", " ").title(),
        })
    st.dataframe(pd.DataFrame(run_rows), use_container_width=True, hide_index=True)
else:
    st.info("No runs recorded yet.")

st.divider()

# =========================
# ➕ MANUALLY ADD USER
# =========================
st.subheader("➕ Manually Add User")
st.caption("Use this to add a user without going through registration.")

with st.form("add_user_form"):
    add_email = st.text_input("Email")
    add_name  = st.text_input("Full name")
    add_firm  = st.text_input("Firm name")
    add_tier  = st.selectbox("Starting tier", ["trial", "paid"])
    add_days  = st.number_input("Trial days (if trial)", value=14, min_value=1)
    add_pass  = st.text_input("Temporary password", type="password")
    submitted = st.form_submit_button("Add User", use_container_width=True)

if submitted:
    if not add_email or not add_pass:
        st.error("Email and password are required.")
    else:
        try:
            # Create auth user via admin API
            resp = supabase.auth.admin.create_user({
                "email":            add_email.strip().lower(),
                "password":         add_pass,
                "email_confirm":    True,
                "user_metadata": {
                    "full_name": add_name,
                    "firm_name": add_firm
                }
            })
            if resp.user:
                trial_end = (
                    datetime.datetime.utcnow() +
                    datetime.timedelta(days=add_days)
                ).isoformat() if add_tier == "trial" else None

                supabase.table("profiles").upsert({
                    "id":            resp.user.id,
                    "email":         add_email.strip().lower(),
                    "full_name":     add_name,
                    "firm_name":     add_firm,
                    "tier":          add_tier,
                    "trial_ends_at": trial_end,
                    "approved_by":   profile.get("email", "admin")
                }).execute()
                st.success(f"User {add_email} created successfully.")
                st.rerun()
        except Exception as e:
            st.error(f"Error creating user: {str(e)[:200]}")