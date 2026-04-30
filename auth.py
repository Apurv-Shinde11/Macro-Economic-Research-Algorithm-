"""
auth.py — SENTINEL Authentication Module

Handles login, registration, session management,
trial status, and access gating via Supabase.
"""

import streamlit as st
import datetime
from supabase import create_client, Client


# =========================
# 🔧 SUPABASE CLIENT
# =========================
def get_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL", "")
    key = st.secrets.get("SUPABASE_ANON_KEY", "")
    if not url or not key:
        st.error(
            "Supabase credentials missing from secrets.toml. "
            "Add SUPABASE_URL and SUPABASE_ANON_KEY."
        )
        st.stop()
    return create_client(url, key)


# =========================
# 🔐 SESSION HELPERS
# =========================
def is_logged_in():
    return (
        "user" in st.session_state and
        st.session_state["user"] is not None
    )

def get_current_user():
    return st.session_state.get("user", None)

def get_profile():
    return st.session_state.get("profile", {})

def clear_session():
    for key in ["user", "profile", "auth_token"]:
        if key in st.session_state:
            del st.session_state[key]


# =========================
# 🚦 ACCESS TIER CHECKER
# =========================
def check_access(profile):
    """
    Returns (can_access: bool, reason: str, days_left: int|None)
    """
    tier = profile.get("tier", "pending")

    if tier == "pending":
        return False, "pending", None

    if tier == "expired":
        return False, "expired", None

    if tier == "paid":
        return True, "paid", None

    if tier == "trial":
        trial_end = profile.get("trial_ends_at")
        if not trial_end:
            return True, "trial", 14
        try:
            if isinstance(trial_end, str):
                trial_end = datetime.datetime.fromisoformat(
                    trial_end.replace("Z", "+00:00")
                )
            now       = datetime.datetime.now(datetime.timezone.utc)
            days_left = (trial_end - now).days
            if days_left >= 0:
                return True, "trial", days_left
            else:
                return False, "expired", 0
        except Exception:
            return True, "trial", None

    return False, "unknown", None


# =========================
# 🔑 LOGIN
# =========================
def login(email, password):
    """
    Returns (success: bool, message: str).
    Stores user + profile in session_state on success.
    """
    supabase = get_supabase()

    try:
        response = supabase.auth.sign_in_with_password({
            "email":    email.strip().lower(),
            "password": password
        })

        if not response.user:
            return False, "Invalid email or password."

        user = response.user

        profile_resp = supabase.table("profiles") \
            .select("*") \
            .eq("id", user.id) \
            .single() \
            .execute()

        profile = profile_resp.data if profile_resp.data else {}

        supabase.table("profiles") \
            .update({"last_login": datetime.datetime.utcnow().isoformat()}) \
            .eq("id", user.id) \
            .execute()

        st.session_state["user"]       = user
        st.session_state["profile"]    = profile
        st.session_state["auth_token"] = response.session.access_token

        return True, "Login successful."

    except Exception as e:
        err = str(e)
        if "Invalid login credentials" in err:
            return False, "Invalid email or password."
        elif "Email not confirmed" in err:
            return False, "Please confirm your email before logging in."
        else:
            return False, f"Login error: {err[:120]}"


# =========================
# 📝 REGISTER
# =========================
def register(email, password, full_name, firm_name):
    """
    Registers a new user. Account starts as 'pending'.
    Admin must approve before access is granted.
    Returns (success: bool, message: str)
    """
    supabase = get_supabase()

    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not email or "@" not in email:
        return False, "Please enter a valid email address."
    if not full_name:
        return False, "Please enter your name."

    try:
        response = supabase.auth.sign_up({
            "email":    email.strip().lower(),
            "password": password,
            "options": {
                "data": {
                    "full_name": full_name,
                    "firm_name": firm_name
                }
            }
        })

        if not response.user:
            return False, "Registration failed. Please try again."

        supabase.table("profiles") \
            .update({
                "full_name": full_name,
                "firm_name": firm_name,
                "tier":      "pending"
            }) \
            .eq("id", response.user.id) \
            .execute()

        return True, (
            "Registration successful! Your account is pending approval. "
            "You will receive an email once access is granted."
        )

    except Exception as e:
        err = str(e)
        if "already registered" in err or "already exists" in err:
            return False, "This email is already registered. Please log in."
        else:
            return False, f"Registration error: {err[:120]}"


# =========================
# 🚪 LOGOUT
# =========================
def logout():
    try:
        supabase = get_supabase()
        supabase.auth.sign_out()
    except Exception:
        pass
    clear_session()
    st.rerun()


# =========================
# 🔑 PASSWORD RESET REQUEST
# =========================
def request_password_reset(email):
    supabase = get_supabase()
    try:
        supabase.auth.reset_password_email(email.strip().lower())
        return True, "Password reset email sent. Check your inbox."
    except Exception as e:
        return False, f"Reset error: {str(e)[:120]}"


# =========================
# 💾 SAVE RUN TO HISTORY
# ✅ ITEM 4 — now accepts allocation + stress_test
# =========================
def save_run(regime, confidence, conviction, repo, deficit,
             capex, summary, report_text="",
             allocation=None, stress_test=None):
    """
    Saves a pipeline run to the user's history.
    allocation  — dict of asset weights e.g. {"equities": 0.65, ...}
    stress_test — dict of sidebar inputs  e.g. {"repo_rate": 5.25, ...}
    """
    if not is_logged_in():
        return None
    user = get_current_user()
    try:
        supabase = get_supabase()
        result   = supabase.table("runs").insert({
            "user_id":     user.id,
            "regime":      regime,
            "confidence":  confidence,
            "conviction":  conviction,
            "repo_rate":   repo,
            "deficit":     deficit,
            "capex":       capex,
            "summary":     summary[:500]      if summary      else "",
            "report_text": report_text[:5000] if report_text  else "",
            "allocation":  allocation         if allocation   else {},
            "stress_test": stress_test        if stress_test  else {},
        }).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as e:
        print(f"[Auth] Save run error: {e}")
        return None


# =========================
# 📋 GET RUN HISTORY
# =========================
def get_run_history(limit=30):
    """Returns last N runs for current user, most recent first."""
    if not is_logged_in():
        return []
    user = get_current_user()
    try:
        supabase = get_supabase()
        result   = supabase.table("runs") \
            .select("*") \
            .eq("user_id", user.id) \
            .order("run_at", desc=True) \
            .limit(limit) \
            .execute()
        return result.data or []
    except Exception as e:
        print(f"[Auth] Get history error: {e}")
        return []


# =========================
# 🎨 LOGIN / REGISTER UI
# set_page_config removed — main.py owns it
# =========================
def render_auth_page():
    """
    Renders the login/register UI.
    Called from main.py inside a centred column.
    No set_page_config here — main.py handles that.
    """

    st.markdown("""
    <div style='text-align:center;padding:40px 0 20px 0;'>
        <div style='font-size:36px;font-weight:800;color:#1a1a2e;
                    letter-spacing:-1px;margin-bottom:6px;'>
            🏛️ SENTINEL
        </div>
        <div style='font-size:14px;color:#666;'>
            Macro Intelligence Terminal
        </div>
    </div>
    """, unsafe_allow_html=True)

    tab_login, tab_register, tab_reset = st.tabs([
        "Sign In", "Request Access", "Reset Password"
    ])

    # -------------------------
    # LOGIN TAB
    # -------------------------
    with tab_login:
        st.write("")
        with st.form("login_form"):
            email     = st.text_input("Email address")
            password  = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "Sign In", use_container_width=True
            )

        if submitted:
            if not email or not password:
                st.error("Please enter both email and password.")
            else:
                with st.spinner("Signing in..."):
                    success, message = login(email, password)

                if success:
                    profile = get_profile()
                    can_access, reason, days_left = check_access(profile)

                    if can_access:
                        if reason == "trial" and days_left is not None:
                            st.success(
                                f"Welcome back! "
                                f"Trial active — {days_left} days remaining."
                            )
                        else:
                            st.success("Welcome back!")
                        st.rerun()
                    elif reason == "pending":
                        clear_session()
                        st.warning(
                            "Your account is pending approval. "
                            "You will be notified by email once access is granted."
                        )
                    elif reason == "expired":
                        clear_session()
                        st.warning(
                            "Your trial has expired. "
                            "Please contact us to upgrade your account."
                        )
                    else:
                        clear_session()
                        st.error("Account access issue. Please contact support.")
                else:
                    st.error(message)

    # -------------------------
    # REGISTER TAB
    # -------------------------
    with tab_register:
        st.write("")
        st.caption(
            "SENTINEL is currently invite-only. Fill in your details "
            "and we will review your request within 24 hours."
        )
        st.write("")
        with st.form("register_form"):
            reg_name  = st.text_input("Full name")
            reg_firm  = st.text_input("Firm / Organisation")
            reg_email = st.text_input("Work email")
            reg_pass  = st.text_input(
                "Choose a password", type="password",
                help="Minimum 8 characters"
            )
            reg_pass2 = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button(
                "Request Access", use_container_width=True
            )

        if submitted:
            if reg_pass != reg_pass2:
                st.error("Passwords do not match.")
            else:
                with st.spinner("Submitting request..."):
                    success, message = register(
                        reg_email, reg_pass, reg_name, reg_firm
                    )
                if success:
                    st.success(message)
                else:
                    st.error(message)

    # -------------------------
    # RESET PASSWORD TAB
    # -------------------------
    with tab_reset:
        st.write("")
        st.caption("Enter your registered email to receive a password reset link.")
        st.write("")
        with st.form("reset_form"):
            reset_email = st.text_input("Email address")
            submitted   = st.form_submit_button(
                "Send Reset Link", use_container_width=True
            )

        if submitted:
            if not reset_email:
                st.error("Please enter your email address.")
            else:
                with st.spinner("Sending reset link..."):
                    success, message = request_password_reset(reset_email)
                if success:
                    st.success(message)
                else:
                    st.error(message)

    st.write("")
    st.markdown(
        "<div style='text-align:center;font-size:11px;color:#aaa;'>"
        "SENTINEL Macro Intelligence — For authorised users only<br>"
        "Not investment advice. SEBI registration not required for internal research tools."
        "</div>",
        unsafe_allow_html=True
    )