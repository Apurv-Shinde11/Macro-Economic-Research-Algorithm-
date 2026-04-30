"""
sentinel_styles.py — SENTINEL UI Design System v1.0

System-adaptive dark/light theme for SENTINEL Macro Intelligence Terminal.
Inject via: st.markdown(SENTINEL_CSS, unsafe_allow_html=True)

Theme logic:
  Dark mode  — fires when OS is set to dark  (#06090f navy backgrounds)
  Light mode — fires when OS is set to light (#f4f6fb clean white surfaces)

Typography:
  Syne          — labels, headers, badges, section titles (display)
  Playfair Disp — regime narrative, NLP reasoning (editorial serif)
  DM Mono       — all numbers, prices, percentages, data (monospace)
  DM Sans       — body copy, descriptions, captions (sans)

Do NOT import this file in scheduler.py — it has no Streamlit dependency.
"""

SENTINEL_CSS = """
<style>

/* ============================================================
   1. FONT IMPORTS
   ============================================================ */
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=Playfair+Display:ital,wght@0,400;0,500;1,400;1,500&family=DM+Mono:ital,wght@0,400;0,500;1,400&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,400&display=swap');

/* ============================================================
   2. CSS CUSTOM PROPERTIES — DARK MODE (default)
   ============================================================ */
:root {
  --s-bg:          #06090f;
  --s-surface:     #0d1321;
  --s-panel:       #111c2d;
  --s-panel2:      #0f1929;
  --s-border:      rgba(67,97,238,0.14);
  --s-border-h:    rgba(67,97,238,0.30);
  --s-border-s:    rgba(67,97,238,0.22);

  --s-blue:        #4361ee;
  --s-blue-dim:    rgba(67,97,238,0.12);
  --s-blue-glow:   rgba(67,97,238,0.08);
  --s-green:       #22c97a;
  --s-green-dim:   rgba(34,201,122,0.10);
  --s-red:         #e84040;
  --s-red-dim:     rgba(232,64,64,0.10);
  --s-amber:       #f4a261;
  --s-amber-dim:   rgba(244,162,97,0.10);
  --s-purple:      #a78bfa;
  --s-purple-dim:  rgba(167,139,250,0.10);

  --s-text:        rgba(220,230,255,0.92);
  --s-text2:       rgba(160,185,230,0.64);
  --s-text3:       rgba(120,150,200,0.42);
  --s-text-inv:    #06090f;

  --s-mono:        'DM Mono', 'Cascadia Code', 'Fira Code', monospace;
  --s-sans:        'DM Sans', 'Helvetica Neue', sans-serif;
  --s-serif:       'Playfair Display', 'Georgia', serif;
  --s-display:     'Syne', 'Helvetica Neue', sans-serif;

  --s-radius:      6px;
  --s-radius-lg:   10px;
  --s-shadow:      0 2px 12px rgba(0,0,0,0.40);
  --s-shadow-blue: 0 0 0 1px rgba(67,97,238,0.25);

  --s-transition:  0.18s ease;
}

/* ============================================================
   3. CSS CUSTOM PROPERTIES — LIGHT MODE (OS override)
   ============================================================ */
@media (prefers-color-scheme: light) {
  :root {
    --s-bg:          #f0f3fa;
    --s-surface:     #ffffff;
    --s-panel:       #ffffff;
    --s-panel2:      #f8faff;
    --s-border:      rgba(0,0,0,0.07);
    --s-border-h:    rgba(52,81,209,0.22);
    --s-border-s:    rgba(52,81,209,0.15);

    --s-blue:        #3451d1;
    --s-blue-dim:    rgba(52,81,209,0.08);
    --s-blue-glow:   rgba(52,81,209,0.05);
    --s-green:       #16a34a;
    --s-green-dim:   rgba(22,163,74,0.08);
    --s-red:         #dc2626;
    --s-red-dim:     rgba(220,38,38,0.07);
    --s-amber:       #d97706;
    --s-amber-dim:   rgba(217,119,6,0.08);
    --s-purple:      #7c3aed;
    --s-purple-dim:  rgba(124,58,237,0.08);

    --s-text:        #0f172a;
    --s-text2:       #64748b;
    --s-text3:       #94a3b8;
    --s-text-inv:    #ffffff;

    --s-shadow:      0 2px 12px rgba(0,0,0,0.08);
    --s-shadow-blue: 0 0 0 1px rgba(52,81,209,0.15);
  }
}

/* ============================================================
   4. STREAMLIT APP SHELL
   ============================================================ */

/* Main app background */
.stApp,
.stApp > header + div,
[data-testid="stAppViewContainer"] {
  background: var(--s-bg) !important;
}

/* Main content block */
.main .block-container {
  background: var(--s-bg) !important;
  padding-top: 1.5rem !important;
  padding-bottom: 4rem !important;
  max-width: 1300px !important;
}

/* Hide Streamlit deploy button and menu */
[data-testid="stToolbar"],
[data-testid="stDecoration"],
#MainMenu,
footer,
.stDeployButton {
  display: none !important;
}

/* Top header bar */
header[data-testid="stHeader"] {
  background: var(--s-surface) !important;
  border-bottom: 0.5px solid var(--s-border) !important;
}

/* Remove default top padding */
[data-testid="stAppViewBlockContainer"] {
  padding-top: 0 !important;
}

/* ============================================================
   5. SIDEBAR
   ============================================================ */
section[data-testid="stSidebar"],
[data-testid="stSidebar"] {
  background: var(--s-surface) !important;
  border-right: 0.5px solid var(--s-border) !important;
}

[data-testid="stSidebar"] > div {
  background: var(--s-surface) !important;
  padding-top: 1.5rem !important;
}

/* Sidebar header */
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
  font-family: var(--s-display) !important;
  font-size: 10px !important;
  font-weight: 700 !important;
  letter-spacing: 1.8px !important;
  text-transform: uppercase !important;
  color: var(--s-text3) !important;
}

/* Sidebar caption */
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] small {
  color: var(--s-text3) !important;
  font-family: var(--s-sans) !important;
  font-size: 11px !important;
}

/* Sidebar divider */
[data-testid="stSidebar"] hr {
  border-color: var(--s-border) !important;
}

/* ============================================================
   6. TYPOGRAPHY
   ============================================================ */

/* Page title (st.title) */
h1,
[data-testid="stTitle"] h1 {
  font-family: var(--s-display) !important;
  font-size: 22px !important;
  font-weight: 800 !important;
  letter-spacing: 2px !important;
  text-transform: uppercase !important;
  color: var(--s-text) !important;
  margin-bottom: 0.25rem !important;
}

/* Section headers (st.subheader) */
h2, h3,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 {
  font-family: var(--s-display) !important;
  font-size: 11px !important;
  font-weight: 700 !important;
  letter-spacing: 1.5px !important;
  text-transform: uppercase !important;
  color: var(--s-text3) !important;
  padding-bottom: 8px !important;
  margin-top: 1.5rem !important;
  margin-bottom: 0.75rem !important;
  border-bottom: 0.5px solid var(--s-border) !important;
}

/* Body text */
p, .stMarkdown p {
  font-family: var(--s-sans) !important;
  font-size: 13px !important;
  color: var(--s-text2) !important;
  line-height: 1.65 !important;
}

/* Captions */
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p,
small {
  font-family: var(--s-sans) !important;
  font-size: 11px !important;
  color: var(--s-text3) !important;
}

/* ============================================================
   7. BUTTONS
   ============================================================ */
.stButton > button {
  font-family: var(--s-display) !important;
  font-size: 10px !important;
  font-weight: 700 !important;
  letter-spacing: 1.2px !important;
  text-transform: uppercase !important;
  background: var(--s-blue) !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: var(--s-radius) !important;
  padding: 10px 20px !important;
  transition: all var(--s-transition) !important;
  box-shadow: none !important;
}

.stButton > button:hover {
  background: var(--s-blue) !important;
  opacity: 0.88 !important;
  transform: translateY(-1px) !important;
  box-shadow: var(--s-shadow) !important;
}

.stButton > button:active {
  transform: translateY(0) !important;
  opacity: 1 !important;
}

/* Secondary buttons (non-primary) */
.stButton > button[kind="secondary"],
.stButton > button[data-baseweb="button"][kind="secondary"] {
  background: var(--s-panel) !important;
  color: var(--s-text2) !important;
  border: 0.5px solid var(--s-border) !important;
}

.stButton > button[kind="secondary"]:hover {
  border-color: var(--s-border-h) !important;
  color: var(--s-text) !important;
  background: var(--s-panel2) !important;
}

/* Download button */
[data-testid="stDownloadButton"] > button {
  font-family: var(--s-display) !important;
  font-size: 10px !important;
  font-weight: 700 !important;
  letter-spacing: 1.2px !important;
  text-transform: uppercase !important;
  background: var(--s-blue) !important;
  color: #fff !important;
  border: none !important;
  border-radius: var(--s-radius) !important;
}

/* ============================================================
   8. METRICS
   ============================================================ */
[data-testid="metric-container"],
[data-testid="stMetric"] {
  background: var(--s-panel) !important;
  border: 0.5px solid var(--s-border) !important;
  border-radius: var(--s-radius-lg) !important;
  padding: 12px 14px !important;
  transition: border-color var(--s-transition) !important;
}

[data-testid="metric-container"]:hover,
[data-testid="stMetric"]:hover {
  border-color: var(--s-border-h) !important;
}

/* Metric label */
[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] p,
[data-testid="metric-container"] label {
  font-family: var(--s-display) !important;
  font-size: 9px !important;
  font-weight: 700 !important;
  letter-spacing: 1.4px !important;
  text-transform: uppercase !important;
  color: var(--s-text3) !important;
}

/* Metric value */
[data-testid="stMetricValue"],
[data-testid="stMetricValue"] > div {
  font-family: var(--s-mono) !important;
  font-size: 22px !important;
  font-weight: 500 !important;
  color: var(--s-text) !important;
  line-height: 1.2 !important;
}

/* Metric delta */
[data-testid="stMetricDelta"],
[data-testid="stMetricDelta"] > div {
  font-family: var(--s-mono) !important;
  font-size: 11px !important;
}

/* ============================================================
   9. EXPANDERS
   ============================================================ */
[data-testid="stExpander"] {
  background: var(--s-surface) !important;
  border: 0.5px solid var(--s-border) !important;
  border-radius: var(--s-radius-lg) !important;
  margin-bottom: 8px !important;
  overflow: hidden !important;
}

[data-testid="stExpander"] summary {
  font-family: var(--s-display) !important;
  font-size: 10px !important;
  font-weight: 700 !important;
  letter-spacing: 1.5px !important;
  text-transform: uppercase !important;
  color: var(--s-text2) !important;
  padding: 12px 16px !important;
  background: var(--s-surface) !important;
  border-radius: var(--s-radius-lg) !important;
  transition: all var(--s-transition) !important;
}

[data-testid="stExpander"] summary:hover {
  background: var(--s-panel) !important;
  color: var(--s-text) !important;
}

[data-testid="stExpander"][open] summary {
  border-bottom: 0.5px solid var(--s-border) !important;
  border-radius: var(--s-radius-lg) var(--s-radius-lg) 0 0 !important;
}

[data-testid="stExpander"] > div > div {
  padding: 12px 16px !important;
  background: var(--s-surface) !important;
}

/* ============================================================
   10. DIVIDERS
   ============================================================ */
hr,
[data-testid="stDivider"] > hr {
  border: none !important;
  border-top: 0.5px solid var(--s-border) !important;
  margin: 1.5rem 0 !important;
}

/* ============================================================
   11. DATAFRAMES + TABLES
   ============================================================ */
[data-testid="stDataFrame"],
[data-testid="stTable"] {
  border: 0.5px solid var(--s-border) !important;
  border-radius: var(--s-radius-lg) !important;
  overflow: hidden !important;
  background: var(--s-surface) !important;
}

[data-testid="stDataFrame"] table,
[data-testid="stTable"] table {
  font-family: var(--s-mono) !important;
  font-size: 12px !important;
  width: 100% !important;
}

[data-testid="stDataFrame"] thead tr th,
[data-testid="stTable"] thead tr th {
  font-family: var(--s-display) !important;
  font-size: 9px !important;
  font-weight: 700 !important;
  letter-spacing: 1.2px !important;
  text-transform: uppercase !important;
  color: var(--s-text3) !important;
  background: var(--s-panel) !important;
  border-bottom: 0.5px solid var(--s-border) !important;
  padding: 10px 12px !important;
}

[data-testid="stDataFrame"] tbody tr td,
[data-testid="stTable"] tbody tr td {
  color: var(--s-text) !important;
  border-bottom: 0.5px solid var(--s-border) !important;
  padding: 8px 12px !important;
  font-size: 12px !important;
}

[data-testid="stDataFrame"] tbody tr:hover td,
[data-testid="stTable"] tbody tr:hover td {
  background: var(--s-panel) !important;
}

[data-testid="stDataFrame"] tbody tr:last-child td,
[data-testid="stTable"] tbody tr:last-child td {
  border-bottom: none !important;
}

/* ============================================================
   12. FORM INPUTS — SLIDERS, NUMBER INPUTS, RADIO, SELECTBOX
   ============================================================ */

/* Slider labels */
[data-testid="stSlider"] label,
[data-testid="stSlider"] [data-testid="stWidgetLabel"] {
  font-family: var(--s-display) !important;
  font-size: 9px !important;
  font-weight: 700 !important;
  letter-spacing: 1.2px !important;
  text-transform: uppercase !important;
  color: var(--s-text3) !important;
}

/* Slider value */
[data-testid="stSlider"] [data-testid="stThumbValue"] {
  font-family: var(--s-mono) !important;
  font-size: 11px !important;
  font-weight: 500 !important;
  color: var(--s-amber) !important;
}

/* Slider track */
[data-testid="stSlider"] [role="slider"] {
  background: var(--s-blue) !important;
}

/* Number input label */
[data-testid="stNumberInput"] label,
[data-testid="stNumberInput"] [data-testid="stWidgetLabel"] {
  font-family: var(--s-display) !important;
  font-size: 9px !important;
  font-weight: 700 !important;
  letter-spacing: 1.2px !important;
  text-transform: uppercase !important;
  color: var(--s-text3) !important;
}

/* Number input field */
[data-testid="stNumberInput"] input {
  font-family: var(--s-mono) !important;
  font-size: 13px !important;
  background: var(--s-panel) !important;
  color: var(--s-text) !important;
  border: 0.5px solid var(--s-border) !important;
  border-radius: var(--s-radius) !important;
}

[data-testid="stNumberInput"] input:focus {
  border-color: var(--s-blue) !important;
  box-shadow: var(--s-shadow-blue) !important;
}

/* Radio */
[data-testid="stRadio"] label {
  font-family: var(--s-display) !important;
  font-size: 9px !important;
  font-weight: 700 !important;
  letter-spacing: 1.2px !important;
  text-transform: uppercase !important;
  color: var(--s-text3) !important;
}

[data-testid="stRadio"] [data-testid="stWidgetLabel"] {
  font-family: var(--s-display) !important;
  font-size: 9px !important;
}

[data-testid="stRadio"] div[role="radiogroup"] label {
  font-family: var(--s-sans) !important;
  font-size: 11px !important;
  font-weight: 400 !important;
  letter-spacing: 0.5px !important;
  text-transform: none !important;
  color: var(--s-text2) !important;
}

/* Selectbox */
[data-testid="stSelectbox"] label {
  font-family: var(--s-display) !important;
  font-size: 9px !important;
  font-weight: 700 !important;
  letter-spacing: 1.2px !important;
  text-transform: uppercase !important;
  color: var(--s-text3) !important;
}

[data-testid="stSelectbox"] div[data-baseweb="select"] {
  font-family: var(--s-sans) !important;
  font-size: 12px !important;
  background: var(--s-panel) !important;
  border-color: var(--s-border) !important;
  border-radius: var(--s-radius) !important;
}

/* ============================================================
   13. ALERTS — INFO, WARNING, ERROR, SUCCESS
   ============================================================ */
[data-testid="stAlert"],
.stAlert {
  border-radius: var(--s-radius-lg) !important;
  border: 0.5px solid !important;
  font-family: var(--s-sans) !important;
  font-size: 13px !important;
}

/* st.info */
[data-testid="stAlert"][data-type="info"],
.stInfo {
  background: var(--s-blue-dim) !important;
  border-color: var(--s-border-s) !important;
  color: var(--s-text2) !important;
}

/* st.warning */
[data-testid="stAlert"][data-type="warning"],
.stWarning {
  background: var(--s-amber-dim) !important;
  border-color: rgba(244,162,97,0.30) !important;
  color: var(--s-text2) !important;
}

/* st.error */
[data-testid="stAlert"][data-type="error"],
.stError {
  background: var(--s-red-dim) !important;
  border-color: rgba(232,64,64,0.30) !important;
  color: var(--s-text2) !important;
}

/* st.success */
[data-testid="stAlert"][data-type="success"],
.stSuccess {
  background: var(--s-green-dim) !important;
  border-color: rgba(34,201,122,0.30) !important;
  color: var(--s-text2) !important;
}

/* ============================================================
   14. SPINNER
   ============================================================ */
.stSpinner > div {
  border-top-color: var(--s-blue) !important;
}

/* ============================================================
   15. CHARTS — BAR, LINE, AREA
   ============================================================ */
[data-testid="stArrowAltairChart"],
[data-testid="stVegaLiteChart"] {
  background: var(--s-surface) !important;
  border: 0.5px solid var(--s-border) !important;
  border-radius: var(--s-radius-lg) !important;
  padding: 12px !important;
}

/* ============================================================
   16. SENTINEL CUSTOM COMPONENTS
   All the st.markdown HTML blocks used throughout main.py
   ============================================================ */

/* ── TICKER STRIP ── */
.sentinel-ticker {
  background: var(--s-surface);
  border: 0.5px solid var(--s-border);
  border-radius: var(--s-radius);
  padding: 8px 16px;
  margin-bottom: 8px;
  overflow-x: auto;
  white-space: nowrap;
  font-family: var(--s-mono);
}

.sentinel-ticker-live {
  font-family: var(--s-display);
  font-size: 8px;
  font-weight: 800;
  letter-spacing: 2px;
  color: var(--s-blue);
  margin-right: 16px;
  text-transform: uppercase;
}

/* ── REGIME HERO BOX ── */
.regime-box {
  background: var(--s-panel);
  border-left: 3px solid var(--s-blue);
  border-radius: 0 var(--s-radius-lg) var(--s-radius-lg) 0;
  border-top: 0.5px solid var(--s-border);
  border-right: 0.5px solid var(--s-border);
  border-bottom: 0.5px solid var(--s-border);
  padding: 16px 20px;
  margin-bottom: 14px;
  font-family: var(--s-serif);
  font-size: 14px;
  font-style: italic;
  line-height: 1.75;
  color: var(--s-text2);
}

/* ── DECISION BOX ── */
.decision-box {
  background: var(--s-panel);
  border-left: 3px solid var(--s-purple);
  border-radius: 0 var(--s-radius-lg) var(--s-radius-lg) 0;
  border-top: 0.5px solid var(--s-border);
  border-right: 0.5px solid var(--s-border);
  border-bottom: 0.5px solid var(--s-border);
  padding: 14px 18px;
  margin-bottom: 12px;
  font-family: var(--s-sans);
  font-size: 13px;
  line-height: 1.7;
  color: var(--s-text2);
}

/* ── NLP REASONING BOX ── */
.reasoning-box {
  background: var(--s-panel2);
  border-left: 2px solid var(--s-border-s);
  border-radius: 0 var(--s-radius) var(--s-radius) 0;
  padding: 10px 14px;
  margin-bottom: 10px;
  font-family: var(--s-serif);
  font-size: 12px;
  font-style: italic;
  color: var(--s-text3);
  line-height: 1.65;
}

/* ── CHALLENGER BOX ── */
.challenger-box {
  background: var(--s-amber-dim);
  border-left: 2px solid var(--s-amber);
  border-radius: 0 var(--s-radius) var(--s-radius) 0;
  padding: 8px 12px;
  font-family: var(--s-sans);
  font-size: 12px;
  color: var(--s-text2);
  margin-top: 8px;
}

/* ── TRIGGER ROWS ── */
.trigger-row {
  background: var(--s-amber-dim);
  border-left: 2px solid var(--s-amber);
  padding: 8px 12px;
  border-radius: 0 var(--s-radius) var(--s-radius) 0;
  margin-bottom: 6px;
  font-family: var(--s-sans);
  font-size: 12px;
  color: var(--s-text2);
}

/* ── NSE / RBI / FII BOXES ── */
.nse-box {
  background: var(--s-green-dim);
  border-left: 3px solid var(--s-green);
  border-radius: 0 var(--s-radius-lg) var(--s-radius-lg) 0;
  padding: 12px 16px;
  margin-bottom: 10px;
  font-family: var(--s-sans);
  font-size: 13px;
  color: var(--s-text2);
}

.rbi-box {
  background: var(--s-amber-dim);
  border-left: 3px solid var(--s-amber);
  border-radius: 0 var(--s-radius-lg) var(--s-radius-lg) 0;
  padding: 12px 16px;
  margin-bottom: 10px;
  font-family: var(--s-sans);
  font-size: 13px;
  color: var(--s-text2);
}

/* ── BADGES & PILLS ── */
.ow-badge {
  display: inline-block;
  background: var(--s-green-dim);
  color: var(--s-green);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: var(--s-display);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.8px;
  border: 0.5px solid rgba(34,201,122,0.25);
}

.uw-badge {
  display: inline-block;
  background: var(--s-red-dim);
  color: var(--s-red);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: var(--s-display);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.8px;
  border: 0.5px solid rgba(232,64,64,0.25);
}

.neu-badge {
  display: inline-block;
  background: var(--s-panel);
  color: var(--s-text3);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: var(--s-display);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.8px;
  border: 0.5px solid var(--s-border);
}

/* ── SIGNAL / RISK / GLOBAL PILLS ── */
.signal-pill {
  display: inline-block;
  background: var(--s-blue-dim);
  color: var(--s-blue);
  border: 0.5px solid var(--s-border-s);
  border-radius: 20px;
  padding: 3px 10px;
  font-family: var(--s-sans);
  font-size: 11px;
  font-weight: 400;
  margin: 2px 4px 2px 0;
}

.risk-pill {
  display: inline-block;
  background: var(--s-red-dim);
  color: var(--s-red);
  border: 0.5px solid rgba(232,64,64,0.22);
  border-radius: 20px;
  padding: 3px 10px;
  font-family: var(--s-sans);
  font-size: 11px;
  margin: 2px 4px 2px 0;
}

.global-pill {
  display: inline-block;
  background: var(--s-amber-dim);
  color: var(--s-amber);
  border: 0.5px solid rgba(244,162,97,0.22);
  border-radius: 20px;
  padding: 3px 10px;
  font-family: var(--s-sans);
  font-size: 11px;
  margin: 2px 4px 2px 0;
}

.driver-pill {
  display: inline-block;
  background: var(--s-panel);
  color: var(--s-text2);
  border: 0.5px solid var(--s-border);
  border-radius: 20px;
  padding: 3px 10px;
  font-family: var(--s-sans);
  font-size: 11px;
  margin: 2px 4px 2px 0;
}

.sector-pill {
  display: inline-block;
  background: var(--s-green-dim);
  color: var(--s-green);
  border: 0.5px solid rgba(34,201,122,0.22);
  border-radius: 20px;
  padding: 3px 10px;
  font-family: var(--s-sans);
  font-size: 11px;
  margin: 2px 4px 2px 0;
}

/* Source badges */
.source-badge-llm {
  display: inline-block;
  background: var(--s-green-dim);
  color: var(--s-green);
  border: 0.5px solid rgba(34,201,122,0.25);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: var(--s-display);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.source-badge-keyword {
  display: inline-block;
  background: var(--s-amber-dim);
  color: var(--s-amber);
  border: 0.5px solid rgba(244,162,97,0.25);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: var(--s-display);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

/* FII badges */
.fii-buy, .fii-sell, .fii-neutral {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 12px;
  font-family: var(--s-display);
  font-size: 10px;
  font-weight: 700;
  margin: 2px 6px 2px 0;
}
.fii-buy    { background: var(--s-green-dim); color: var(--s-green); }
.fii-sell   { background: var(--s-red-dim);   color: var(--s-red);   }
.fii-neutral{ background: var(--s-panel);     color: var(--s-text3); }

/* ── USER CARD (sidebar) ── */
.user-card {
  background: var(--s-panel);
  border: 0.5px solid var(--s-border);
  border-radius: var(--s-radius-lg);
  padding: 12px 14px;
  margin-bottom: 14px;
  font-family: var(--s-sans);
  font-size: 12px;
  line-height: 1.7;
  color: var(--s-text2);
}

/* ── PLAYBOOK ITEMS ── */
.playbook-item {
  padding: 8px 0;
  border-bottom: 0.5px solid var(--s-border);
  font-family: var(--s-sans);
  font-size: 13px;
  line-height: 1.65;
  color: var(--s-text2);
}

.playbook-item:last-child {
  border-bottom: none;
}

/* ── REGIME CHANGE BANNER ── */
.regime-change-banner {
  background: var(--s-amber-dim);
  border-left: 4px solid var(--s-amber);
  border-top: 0.5px solid rgba(244,162,97,0.25);
  border-right: 0.5px solid rgba(244,162,97,0.25);
  border-bottom: 0.5px solid rgba(244,162,97,0.25);
  border-radius: 0 var(--s-radius-lg) var(--s-radius-lg) 0;
  padding: 16px 20px;
  margin-bottom: 16px;
  font-family: var(--s-sans);
  font-size: 14px;
  line-height: 1.7;
  color: var(--s-text);
}

.regime-change-banner.high {
  background: var(--s-red-dim);
  border-left-color: var(--s-red);
  border-top-color: rgba(232,64,64,0.20);
  border-right-color: rgba(232,64,64,0.20);
  border-bottom-color: rgba(232,64,64,0.20);
}

/* ── YIELD CURVE SIGNAL BOXES ── */
.yc-signal-box {
  border-radius: var(--s-radius);
  padding: 12px 16px;
  margin-bottom: 8px;
  font-family: var(--s-sans);
  font-size: 13px;
  color: var(--s-text2);
  border: 0.5px solid;
}

/* ============================================================
   17. SIDEBAR CALENDAR EVENT STRIPS
   ============================================================ */
.sidebar-event-card {
  border-radius: 5px;
  padding: 7px 10px;
  margin-bottom: 5px;
  border-left: 3px solid;
}

.sidebar-event-cat {
  font-family: var(--s-display);
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
  margin-bottom: 2px;
}

.sidebar-event-name {
  font-family: var(--s-sans);
  font-size: 11px;
  font-weight: 500;
  color: var(--s-text);
  line-height: 1.4;
  margin-bottom: 1px;
}

.sidebar-event-date {
  font-family: var(--s-mono);
  font-size: 9px;
  color: var(--s-text3);
}

/* ============================================================
   18. ANIMATIONS & MICRO-INTERACTIONS
   ============================================================ */

/* Page load fade-in */
@keyframes s-fadein {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0);   }
}

/* Live dot pulse */
@keyframes s-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.35; }
}

/* Apply fade-in to main content sections */
.main .block-container > div > div {
  animation: s-fadein 0.4s ease both;
}

/* Staggered section reveals */
.main .block-container > div > div:nth-child(1) { animation-delay: 0.05s; }
.main .block-container > div > div:nth-child(2) { animation-delay: 0.10s; }
.main .block-container > div > div:nth-child(3) { animation-delay: 0.15s; }
.main .block-container > div > div:nth-child(4) { animation-delay: 0.18s; }
.main .block-container > div > div:nth-child(5) { animation-delay: 0.20s; }

/* Metric hover lift */
[data-testid="metric-container"] {
  transition: transform var(--s-transition),
              border-color var(--s-transition),
              box-shadow var(--s-transition) !important;
}

[data-testid="metric-container"]:hover {
  transform: translateY(-2px) !important;
  box-shadow: var(--s-shadow) !important;
}

/* Expander smooth open */
[data-testid="stExpander"] {
  transition: border-color var(--s-transition) !important;
}

[data-testid="stExpander"]:hover {
  border-color: var(--s-border-h) !important;
}

/* ============================================================
   19. BOTTOM STATUS BAR
   Fixed at bottom. Shows pipeline status + data freshness.
   ============================================================ */
.sentinel-statusbar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 24px;
  background: var(--s-surface);
  border-top: 0.5px solid var(--s-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  z-index: 999;
  font-family: var(--s-mono);
  font-size: 10px;
  color: var(--s-text3);
}

.sentinel-statusbar-left {
  display: flex;
  gap: 20px;
  align-items: center;
}

.sentinel-statusbar-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 9px;
  color: var(--s-text3);
}

.sentinel-statusbar-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  flex-shrink: 0;
}

.sentinel-statusbar-dot.green  { background: var(--s-green); animation: s-pulse 2.5s infinite; }
.sentinel-statusbar-dot.amber  { background: var(--s-amber); }
.sentinel-statusbar-dot.red    { background: var(--s-red);   }
.sentinel-statusbar-dot.muted  { background: var(--s-text3); }

/* Add bottom padding to main content so status bar doesn't overlap */
.main .block-container {
  padding-bottom: 48px !important;
}

/* ============================================================
   20. LIGHT MODE ADJUSTMENTS FOR CUSTOM HTML COMPONENTS
   Override inline styles that use hardcoded dark colors
   ============================================================ */
@media (prefers-color-scheme: light) {
  .regime-box {
    background: #ffffff;
    border-left-color: var(--s-blue);
    border-top-color: var(--s-border);
    border-right-color: var(--s-border);
    border-bottom-color: var(--s-border);
    color: var(--s-text2);
  }

  .decision-box {
    background: #ffffff;
    border-left-color: #7c3aed;
    color: var(--s-text2);
  }

  .reasoning-box {
    background: #f8faff;
    border-left-color: rgba(52,81,209,0.2);
    color: var(--s-text3);
  }

  .challenger-box {
    background: rgba(217,119,6,0.06);
    color: var(--s-text2);
  }

  .trigger-row {
    background: rgba(217,119,6,0.06);
    color: var(--s-text2);
  }

  .user-card {
    background: #f0f3fa;
    border-color: var(--s-border);
    color: var(--s-text2);
  }

  .signal-pill  { background: rgba(52,81,209,0.06); color: var(--s-blue); }
  .risk-pill    { background: rgba(220,38,38,0.06); color: var(--s-red); }
  .global-pill  { background: rgba(217,119,6,0.06); color: var(--s-amber); }
  .driver-pill  { background: #f0f3fa; color: var(--s-text2); }

  .regime-change-banner {
    background: rgba(217,119,6,0.07);
    color: var(--s-text);
  }
  .regime-change-banner.high {
    background: rgba(220,38,38,0.06);
  }

  .playbook-item {
    border-bottom-color: var(--s-border);
    color: var(--s-text2);
  }
}

/* ============================================================
   21. SCROLLBAR STYLING
   ============================================================ */
::-webkit-scrollbar {
  width: 4px;
  height: 4px;
}

::-webkit-scrollbar-track {
  background: var(--s-bg);
}

::-webkit-scrollbar-thumb {
  background: var(--s-border-h);
  border-radius: 2px;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--s-blue);
}

/* ============================================================
   22. SELECTION
   ============================================================ */
::selection {
  background: var(--s-blue-dim);
  color: var(--s-text);
}

</style>
"""


# ============================================================
# STATUS BAR BUILDER
# Call this after the pipeline runs in main.py.
# Pass the pipeline output to generate live status items.
# ============================================================
def build_status_bar(
    pipeline_ran=False,
    nse_ok=False,
    rbi_source="fallback",
    nlp_provider="none",
    consecutive_days=0,
    persistence_adj=0.0
):
    """
    Returns HTML for the fixed bottom status bar.
    Inject via: st.markdown(build_status_bar(...), unsafe_allow_html=True)
    """
    import datetime

    now_str = datetime.datetime.now().strftime("%d %b %Y · %H:%M IST")

    pipeline_dot = "green"  if pipeline_ran else "muted"
    pipeline_txt = "Pipeline complete" if pipeline_ran else "Awaiting run"

    nse_dot = "green" if nse_ok  else "amber"
    nse_txt = "NSE: live" if nse_ok else "NSE: fallback"

    rbi_dot = "green" if rbi_source == "RBI DBIE" else "amber"
    rbi_txt = "RBI: live" if rbi_source == "RBI DBIE" else "RBI: fallback"

    nlp_dot = "green"  if nlp_provider not in ["none", "", None] else "muted"
    nlp_txt = f"LLM: {nlp_provider}" if nlp_provider not in ["none", "", None] else "LLM: keyword"

    persist_txt = ""
    if consecutive_days > 0 and pipeline_ran:
        sign = "+" if persistence_adj >= 0 else ""
        persist_txt = (
            f"<span class='sentinel-statusbar-item'>"
            f"Persistence: {consecutive_days} runs · "
            f"{sign}{persistence_adj:.2f} conf"
            f"</span>"
        )

    return f"""
<div class="sentinel-statusbar">
  <div class="sentinel-statusbar-left">
    <span class="sentinel-statusbar-item">
      <span class="sentinel-statusbar-dot {pipeline_dot}"></span>{pipeline_txt}
    </span>
    <span class="sentinel-statusbar-item">
      <span class="sentinel-statusbar-dot {nse_dot}"></span>{nse_txt}
    </span>
    <span class="sentinel-statusbar-item">
      <span class="sentinel-statusbar-dot {rbi_dot}"></span>{rbi_txt}
    </span>
    <span class="sentinel-statusbar-item">
      <span class="sentinel-statusbar-dot {nlp_dot}"></span>{nlp_txt}
    </span>
    {persist_txt}
  </div>
  <span class="sentinel-statusbar-item">{now_str}</span>
</div>
"""