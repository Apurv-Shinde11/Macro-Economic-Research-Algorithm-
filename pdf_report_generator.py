"""
pdf_report_generator.py
Generates a branded, investor-grade PDF report from SENTINEL's final_intel packet.
"""

import datetime
import io
from reportlab.lib.pagesizes  import A4
from reportlab.lib.units      import mm
from reportlab.lib            import colors
from reportlab.lib.styles     import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums      import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus       import (
    SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, HRFlowable
)

# =========================
# 🎨 BRAND COLOURS
# =========================
SENTINEL_BLUE      = colors.HexColor("#4361ee")
SENTINEL_DARK      = colors.HexColor("#1a1a2e")
SENTINEL_LIGHT     = colors.HexColor("#f0f4ff")
SENTINEL_GREEN     = colors.HexColor("#1e6823")
SENTINEL_GREEN_BG  = colors.HexColor("#e6f4ea")
SENTINEL_RED       = colors.HexColor("#8b1a1a")
SENTINEL_RED_BG    = colors.HexColor("#fce8e8")
SENTINEL_AMBER     = colors.HexColor("#7a4000")
SENTINEL_AMBER_BG  = colors.HexColor("#fff3e0")
SENTINEL_GREY      = colors.HexColor("#555555")
SENTINEL_GREY_LIGHT= colors.HexColor("#f8f9ff")
WHITE              = colors.white
BLACK              = colors.HexColor("#1a1a2e")


class PDFReportGenerator:

    def __init__(self, firm_name="SENTINEL Intelligence"):
        self.firm_name = firm_name
        self.styles    = self._build_styles()

    # =========================
    # ✅ FIX 1 — TEXT SANITISER
    # Replaces characters Helvetica cannot render
    # =========================
    def _clean(self, text):
        if not isinstance(text, str):
            text = str(text)
        replacements = {
            "₹":  "Rs.",
            "▸":  "->",
            "▼":  "v",
            "█":  "|",
            "→":  "->",
            "←":  "<-",
            "↑":  "^",
            "↓":  "v",
            "\u2013": "-",   # en dash
            "\u2014": "--",  # em dash
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
        }
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        return text

    def _p(self, text, style_key="body"):
        """Clean text and return a Paragraph."""
        return Paragraph(self._clean(text), self.styles[style_key])

    # =========================
    # 🎨 STYLE DEFINITIONS
    # =========================
    def _build_styles(self):
        base = getSampleStyleSheet()
        return {
            "section": ParagraphStyle(
                "section",
                parent      = base["Normal"],
                fontName    = "Helvetica-Bold",
                fontSize    = 11,
                textColor   = SENTINEL_BLUE,
                spaceBefore = 12,
                spaceAfter  = 4
            ),
            "body": ParagraphStyle(
                "body",
                parent    = base["Normal"],
                fontName  = "Helvetica",
                fontSize  = 9,
                textColor = BLACK,
                leading   = 14,
                spaceAfter= 3
            ),
            "body_bold": ParagraphStyle(
                "body_bold",
                parent    = base["Normal"],
                fontName  = "Helvetica-Bold",
                fontSize  = 9,
                textColor = BLACK,
                spaceAfter= 2
            ),
            "small": ParagraphStyle(
                "small",
                parent    = base["Normal"],
                fontName  = "Helvetica",
                fontSize  = 8,
                textColor = SENTINEL_GREY,
                leading   = 11,
                spaceAfter= 2
            ),
            "narrative": ParagraphStyle(
                "narrative",
                parent      = base["Normal"],
                fontName    = "Helvetica",
                fontSize    = 9,
                textColor   = BLACK,
                leading     = 14,
                leftIndent  = 8,
                rightIndent = 8,
                spaceBefore = 4,
                spaceAfter  = 8,
                backColor   = SENTINEL_LIGHT,
                borderPad   = 6
            ),
            "playbook": ParagraphStyle(
                "playbook",
                parent     = base["Normal"],
                fontName   = "Helvetica",
                fontSize   = 9,
                textColor  = BLACK,
                leading    = 13,
                leftIndent = 10,
                spaceAfter = 3
            ),
            "small_italic": ParagraphStyle(
                "small_italic",
                parent    = base["Normal"],
                fontName  = "Helvetica-Oblique",
                fontSize  = 8,
                textColor = SENTINEL_GREY,
                leading   = 12,
                spaceAfter= 4
            ),
        }

    # =========================
    # 🧰 SAFE HELPERS
    # =========================
    def _safe_float(self, val, default=0.0):
        try:
            return float(val)
        except Exception:
            return default

    def _fmt_pct(self, val, is_decimal=True):
        """Format a value as percentage string."""
        f = self._safe_float(val)
        if is_decimal:
            # val is 0.65 → show 65%
            return f"{round(f * 100)}%"
        else:
            # val is 65 → show 65%
            return f"{round(f)}%"

    def _regime_color(self, regime):
        r = str(regime).upper()
        if any(x in r for x in ["EXPANSION", "STABLE", "RECOVERY"]):
            return SENTINEL_GREEN_BG, SENTINEL_GREEN
        elif any(x in r for x in ["TIGHTENING", "STAGFLATION", "INFLATION", "SLOWDOWN"]):
            return SENTINEL_RED_BG, SENTINEL_RED
        else:
            return SENTINEL_AMBER_BG, SENTINEL_AMBER

    def _section_rule(self):
        return HRFlowable(
            width="100%", thickness=0.5,
            color=SENTINEL_BLUE,
            spaceAfter=4, spaceBefore=0
        )

    def _divider(self):
        return HRFlowable(
            width="100%", thickness=0.3,
            color=colors.HexColor("#dddddd"),
            spaceAfter=4, spaceBefore=6
        )

    # =========================
    # 📋 TABLE BUILDERS
    # =========================
    def _metric_table(self, metrics):
        """2-column label/value table."""
        data = []
        for label, value in metrics:
            data.append([
                Paragraph(self._clean(label), self.styles["small"]),
                Paragraph(self._clean(str(value)), self.styles["body_bold"])
            ])
        t = Table(data, colWidths=[55*mm, 120*mm])
        t.setStyle(TableStyle([
            ("ROWBACKGROUNDS", (0,0), (-1,-1), [WHITE, SENTINEL_GREY_LIGHT]),
            ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
            ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ]))
        return t

    def _allocation_table(self, allocation, is_decimal=True):
        """Asset allocation table."""
        data = [[
            Paragraph("Asset",      self.styles["body_bold"]),
            Paragraph("Allocation", self.styles["body_bold"])
        ]]
        for asset, val in allocation.items():
            data.append([
                Paragraph(self._clean(asset.capitalize()), self.styles["body"]),
                Paragraph(self._fmt_pct(val, is_decimal),  self.styles["body"])
            ])
        t = Table(data, colWidths=[65*mm, 45*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), SENTINEL_BLUE),
            ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, SENTINEL_GREY_LIGHT]),
            ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
            ("ALIGN",         (1,0), (1,-1),  "CENTER"),
        ]))
        return t

    def _sector_table(self, sector_positioning):
        """
        ✅ FIX 2 — wider stance column so Overweight/Underweight don't truncate
        """
        data = [[
            Paragraph("Sector",  self.styles["body_bold"]),
            Paragraph("Stance",  self.styles["body_bold"])
        ]]
        for sp in sector_positioning:
            if not isinstance(sp, dict):
                continue
            data.append([
                Paragraph(self._clean(sp.get("sector", "")), self.styles["body"]),
                Paragraph(sp.get("stance", "Neutral"),        self.styles["body"])
            ])

        # ✅ FIX 2 — colWidths increased for stance column: 80mm + 60mm
        t = Table(data, colWidths=[80*mm, 60*mm])
        style = [
            ("BACKGROUND",    (0,0), (-1,0), SENTINEL_BLUE),
            ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
            ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ]
        for i, sp in enumerate(sector_positioning, start=1):
            if not isinstance(sp, dict):
                continue
            stance = sp.get("stance", "Neutral")
            if stance == "Overweight":
                style.append(("BACKGROUND", (0,i), (-1,i), SENTINEL_GREEN_BG))
                style.append(("TEXTCOLOR",  (1,i), (1,i),  SENTINEL_GREEN))
                style.append(("FONTNAME",   (1,i), (1,i),  "Helvetica-Bold"))
            elif stance == "Underweight":
                style.append(("BACKGROUND", (0,i), (-1,i), SENTINEL_RED_BG))
                style.append(("TEXTCOLOR",  (1,i), (1,i),  SENTINEL_RED))
                style.append(("FONTNAME",   (1,i), (1,i),  "Helvetica-Bold"))
        t.setStyle(TableStyle(style))
        return t

    def _scenario_table(self, scenarios):
        data = [[
            Paragraph("Scenario",    self.styles["body_bold"]),
            Paragraph("Probability", self.styles["body_bold"]),
            Paragraph("Type",        self.styles["body_bold"])
        ]]
        for sc in scenarios:
            data.append([
                Paragraph(self._clean(sc.get("name",      "")), self.styles["body"]),
                Paragraph(f"{int(self._safe_float(sc.get('probability', 0)) * 100)}%",
                          self.styles["body"]),
                Paragraph(sc.get("dominance", ""), self.styles["body"])
            ])
        t = Table(data, colWidths=[75*mm, 28*mm, 72*mm])
        style = [
            ("BACKGROUND",    (0,0), (-1,0), SENTINEL_BLUE),
            ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
            ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
            ("ALIGN",         (1,0), (1,-1),  "CENTER"),
        ]
        for i, sc in enumerate(scenarios, start=1):
            sc_type = sc.get("type", "")
            if sc_type == "bullish":
                style.append(("BACKGROUND", (0,i), (-1,i), SENTINEL_GREEN_BG))
            elif sc_type == "bearish":
                style.append(("BACKGROUND", (0,i), (-1,i), SENTINEL_RED_BG))
        t.setStyle(TableStyle(style))
        return t

    # =========================
    # 📄 PAGE HEADER / FOOTER
    # =========================
    def _on_page(self, canvas, doc):
        canvas.saveState()
        w, h = A4

        # Dark header bar
        canvas.setFillColor(SENTINEL_DARK)
        canvas.rect(0, h - 18*mm, w, 18*mm, fill=1, stroke=0)

        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(15*mm, h - 11*mm, "SENTINEL")

        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#b0b8f0"))
        canvas.drawString(44*mm, h - 11*mm, "Macro Intelligence Terminal")

        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#8888aa"))
        ts = datetime.datetime.now().strftime("%d %b %Y, %H:%M IST")
        canvas.drawRightString(w - 15*mm, h - 11*mm, ts)

        # Light footer bar
        canvas.setFillColor(colors.HexColor("#eeeeee"))
        canvas.rect(0, 0, w, 10*mm, fill=1, stroke=0)
        canvas.setFillColor(SENTINEL_GREY)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(
            w / 2, 4*mm,
            f"SENTINEL Macro Intelligence  |  {self.firm_name}  |  "
            f"For internal use only. Not investment advice.  |  "
            f"Page {doc.page}"
        )
        canvas.restoreState()

    # =========================
    # 🚀 MAIN GENERATOR
    # =========================
    def generate(self, final_intel):
        """
        Generates branded PDF from final_intel dict.
        Returns bytes for st.download_button.
        """
        buffer = io.BytesIO()
        doc    = SimpleDocTemplate(
            buffer,
            pagesize     = A4,
            leftMargin   = 15*mm,
            rightMargin  = 15*mm,
            topMargin    = 22*mm,
            bottomMargin = 14*mm,
        )

        story = []

        # -------------------------
        # Extract data
        # -------------------------
        regime_data    = final_intel.get("macro_regime",       {})
        scenario_block = final_intel.get("scenarios",          {})
        positioning    = final_intel.get("positioning",        {})
        strategy       = final_intel.get("strategy",           {})
        decision       = final_intel.get("decision",           {})
        nlp_intel      = final_intel.get("nlp_intelligence",   {})
        triggers       = final_intel.get("risk_triggers",      [])

        scenarios   = scenario_block.get("scenarios", [])
        sc_meta     = scenario_block.get("meta",      {})
        pos_meta    = positioning.get("meta",         {})

        regime_name = regime_data.get("regime",    "UNKNOWN").replace("_", " ")
        confidence  = int(regime_data.get("confidence", 0) * 100)
        challenger  = regime_data.get("challenger", "").replace("_", " ")
        rbi_signal  = regime_data.get("components", {}).get("rbi_signal",  "UNKNOWN")
        equity_bias = regime_data.get("components", {}).get("equity_bias", "NEUTRAL")
        narrative   = regime_data.get("narrative",  "")
        drivers     = regime_data.get("drivers",    [])

        # ✅ FIX 3 — conviction from strategy engine (correctly capped)
        conviction_raw = self._safe_float(
            strategy.get(
                "conviction_score",
                pos_meta.get("conviction", 0)
            )
        )
        conviction_pct = f"{round(conviction_raw * 100)}%"
        conviction_label = strategy.get("conviction", "—")

        # -------------------------
        # SECTION 1 — MACRO REGIME
        # -------------------------
        story.append(Spacer(1, 4*mm))
        story.append(self._p("Macro Intelligence Report", "section"))
        story.append(self._section_rule())

        # Regime badge
        bg_color, text_color = self._regime_color(regime_name)
        regime_row = Table(
            [[
                Paragraph(
                    f"<b>{self._clean(regime_name)}</b>",
                    ParagraphStyle("rb", fontName="Helvetica-Bold", fontSize=12,
                                   textColor=text_color, alignment=TA_LEFT)
                ),
                Paragraph(
                    f"Confidence: <b>{confidence}%</b>",
                    ParagraphStyle("rc", fontName="Helvetica", fontSize=9,
                                   textColor=SENTINEL_GREY, alignment=TA_RIGHT)
                ),
            ]],
            colWidths=[110*mm, 65*mm]
        )
        regime_row.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), bg_color),
            ("TOPPADDING",    (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
            ("LEFTPADDING",   (0,0), (-1,-1), 10),
            ("RIGHTPADDING",  (0,0), (-1,-1), 10),
        ]))
        story.append(regime_row)
        story.append(Spacer(1, 4*mm))

        # Key metrics
        story.append(self._metric_table([
            ("RBI Signal",        rbi_signal),
            ("Equity Bias",       equity_bias),
            ("Challenger Regime", challenger if challenger else "None"),
            ("Dominant Scenario", sc_meta.get("dominant_scenario", "N/A")),
            ("Portfolio Stance",  positioning.get("stance", "N/A")),
            # ✅ FIX 3 — shows e.g. "MEDIUM (39%)" not raw 40%
            ("Conviction",        f"{conviction_label} ({conviction_pct})"),
        ]))
        story.append(Spacer(1, 4*mm))

        # Narrative
        if narrative:
            story.append(self._p(narrative, "narrative"))

        # Drivers
        if drivers:
            story.append(self._p("Key Macro Drivers", "body_bold"))
            for d in drivers:
                story.append(self._p(f"- {d}", "body"))
        story.append(Spacer(1, 2*mm))

        # -------------------------
        # SECTION 2 — NLP INTELLIGENCE
        # -------------------------
        if nlp_intel and nlp_intel.get("dominant_theme"):
            story.append(self._divider())
            story.append(self._p("NLP Intelligence", "section"))
            story.append(self._section_rule())

            dom_theme   = nlp_intel.get("dominant_theme", "")
            key_signals = nlp_intel.get("key_signals",    [])
            india_risks = nlp_intel.get("india_risks",    [])
            global_facs = nlp_intel.get("global_factors", [])
            reasoning   = nlp_intel.get("reasoning",      "")
            source      = nlp_intel.get("source",         "keyword")
            nlp_conf    = int(self._safe_float(nlp_intel.get("nlp_confidence", 0)) * 100)

            if dom_theme:
                story.append(self._p(f"<b>Dominant Theme:</b> {self._clean(dom_theme)}", "body"))
            story.append(self._p(
                f"<b>Source:</b> {'LLM + Keyword' if source == 'llm+keyword' else 'Keyword only'}  "
                f"<b>NLP Confidence:</b> {nlp_conf}%", "small"
            ))
            if key_signals:
                story.append(self._p(
                    f"<b>Key Signals:</b> {', '.join(self._clean(s) for s in key_signals)}",
                    "body"
                ))
            if india_risks:
                story.append(self._p(
                    f"<b>India Risks:</b> {', '.join(self._clean(r) for r in india_risks)}",
                    "body"
                ))
            if global_facs:
                story.append(self._p(
                    f"<b>Global Factors:</b> {', '.join(self._clean(f) for f in global_facs)}",
                    "body"
                ))
            if reasoning and "LLM unavailable" not in reasoning:
                story.append(Spacer(1, 2*mm))
                story.append(self._p(self._clean(reasoning), "small_italic"))

        # -------------------------
        # SECTION 3 — SCENARIOS
        # -------------------------
        story.append(self._divider())
        story.append(self._p("Scenario Outlook", "section"))
        story.append(self._section_rule())

        if scenarios:
            story.append(self._scenario_table(scenarios))
            story.append(Spacer(1, 3*mm))
            for sc in scenarios:
                desc = self._clean(sc.get("description", ""))
                if desc:
                    story.append(self._p(
                        f"<b>{self._clean(sc.get('name',''))}:</b> {desc}",
                        "small"
                    ))

        # -------------------------
        # SECTION 4 — POSITIONING
        # -------------------------
        story.append(self._divider())
        story.append(self._p("Portfolio Positioning", "section"))
        story.append(self._section_rule())

        alloc_raw   = positioning.get("allocation",         {})
        sector_pos  = positioning.get("sector_positioning", [])
        tactical    = positioning.get("tactical_actions",   [])
        key_drivers = positioning.get("key_drivers",        [])

        left_col  = []
        right_col = []

        if alloc_raw:
            left_col.append(self._p("<b>Asset Allocation</b>", "body_bold"))
            left_col.append(self._allocation_table(alloc_raw, is_decimal=True))

        if key_drivers:
            left_col.append(Spacer(1, 3*mm))
            left_col.append(self._p("<b>Positioning Rationale</b>", "body_bold"))
            for d in key_drivers[:3]:
                left_col.append(self._p(f"- {self._clean(d)}", "small"))

        if sector_pos:
            right_col.append(self._p("<b>Sector Positioning</b>", "body_bold"))
            right_col.append(self._sector_table(sector_pos))

        if left_col or right_col:
            layout = Table(
                [[left_col, right_col]],
                colWidths=[88*mm, 88*mm]
            )
            layout.setStyle(TableStyle([
                ("VALIGN",       (0,0), (-1,-1), "TOP"),
                ("LEFTPADDING",  (0,0), (-1,-1), 0),
                ("RIGHTPADDING", (0,0), (-1,-1), 4),
                ("TOPPADDING",   (0,0), (-1,-1), 0),
                ("BOTTOMPADDING",(0,0), (-1,-1), 0),
            ]))
            story.append(layout)

        if tactical:
            story.append(Spacer(1, 3*mm))
            story.append(self._p("<b>Tactical Actions</b>", "body_bold"))
            for t in tactical:
                if isinstance(t, dict):
                    story.append(self._p(
                        f"- <b>{self._clean(t.get('action',''))}</b> "
                        f"-- If {self._clean(t.get('condition',''))}",
                        "small"
                    ))

        # -------------------------
        # SECTION 5 — STRATEGY
        # -------------------------
        if strategy:
            story.append(self._divider())
            story.append(self._p("Strategy Intelligence", "section"))
            story.append(self._section_rule())

            story.append(self._p(
                f"<b>{self._clean(strategy.get('strategy_type',''))}</b>  |  "
                f"Time Horizon: {strategy.get('time_horizon','')}  |  "
                f"Conviction: {strategy.get('conviction','')}",
                "body"
            ))
            story.append(Spacer(1, 2*mm))

            playbook = strategy.get("playbook", [])
            if playbook:
                story.append(self._p("<b>Playbook</b>", "body_bold"))
                for p in playbook:
                    story.append(self._p(
                        f"- {self._clean(p)}", "playbook"
                    ))

            risk_fw = strategy.get("risk_framework", {})
            if isinstance(risk_fw, dict) and risk_fw:
                story.append(Spacer(1, 3*mm))
                story.append(self._p("<b>Risk Framework</b>", "body_bold"))
                for k, v in risk_fw.items():
                    story.append(self._p(
                        f"- <b>{k.replace('_',' ').title()}:</b> {self._clean(str(v))}",
                        "small"
                    ))

        # -------------------------
        # SECTION 6 — DECISION
        # -------------------------
        if decision:
            story.append(self._divider())
            story.append(self._p("Decision Intelligence", "section"))
            story.append(self._section_rule())

            dec_summary = decision.get("summary",    "")
            dec_risk    = decision.get("risk",        {})
            dec_alloc   = decision.get("allocation",  {})
            dec_sectors = decision.get("sector_bets", [])

            if dec_summary:
                story.append(self._p(self._clean(dec_summary), "narrative"))

            story.append(self._metric_table([
                ("Risk Level",        dec_risk.get("risk_level",       "—")),
                ("Expected Drawdown", f"{dec_risk.get('expected_drawdown', 0)}%"),
                ("Worst Case",        f"{dec_risk.get('worst_case', 0)}%"),
            ]))
            story.append(Spacer(1, 3*mm))

            dec_left  = []
            dec_right = []

            if dec_alloc:
                dec_left.append(self._p("<b>Decision Allocation</b>", "body_bold"))
                # ✅ FIX 4 — dec_alloc values are already integers (70, 20 etc)
                # so is_decimal=False and _fmt_pct rounds to int not 70.0
                dec_left.append(self._allocation_table(dec_alloc, is_decimal=False))

            if dec_sectors:
                dec_right.append(self._p("<b>Sector Bets</b>", "body_bold"))
                for s in dec_sectors:
                    dec_right.append(self._p(f"- <b>{self._clean(s)}</b>", "body"))

            if dec_left or dec_right:
                layout = Table(
                    [[dec_left, dec_right]],
                    colWidths=[88*mm, 88*mm]
                )
                layout.setStyle(TableStyle([
                    ("VALIGN",       (0,0), (-1,-1), "TOP"),
                    ("LEFTPADDING",  (0,0), (-1,-1), 0),
                    ("RIGHTPADDING", (0,0), (-1,-1), 4),
                    ("TOPPADDING",   (0,0), (-1,-1), 0),
                    ("BOTTOMPADDING",(0,0), (-1,-1), 0),
                ]))
                story.append(layout)

        # -------------------------
        # SECTION 7 — TRIGGERS
        # -------------------------
        if triggers:
            story.append(self._divider())
            story.append(self._p("Active Triggers", "section"))
            story.append(self._section_rule())

            top = sorted(
                [t for t in triggers if isinstance(t, dict)],
                key=lambda x: x.get("priority", 0),
                reverse=True
            )[:5]

            for t in top:
                story.append(self._p(
                    f"- <b>{self._clean(t.get('name',''))}</b>: "
                    f"{self._clean(t.get('action',''))} "
                    f"<i>(If {self._clean(t.get('condition',''))})</i>",
                    "small"
                ))

        # -------------------------
        # DISCLAIMER
        # -------------------------
        story.append(Spacer(1, 6*mm))
        story.append(self._divider())
        story.append(self._p(
            "Disclaimer: This report is generated by SENTINEL, an algorithmic macro "
            "intelligence system. It is intended for internal research purposes only and "
            "does not constitute investment advice. Past regime classifications do not "
            "guarantee future performance. All allocation recommendations are indicative "
            "only. Consult a SEBI-registered advisor before making investment decisions.",
            "small"
        ))

        # -------------------------
        # BUILD
        # -------------------------
        doc.build(story, onFirstPage=self._on_page, onLaterPages=self._on_page)
        buffer.seek(0)
        return buffer.read()