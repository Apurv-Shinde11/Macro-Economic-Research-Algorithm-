import datetime


class ReportGenerator:
    def __init__(self):
        pass

    def generate_report(self, final_intel):
        timestamp = final_intel.get(
            "timestamp",
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        regime_data    = final_intel.get("macro_regime",       {})
        scenario_block = final_intel.get("scenarios",          {})
        asset_block    = final_intel.get("asset_implications", {})
        positioning    = final_intel.get("positioning",        {})
        triggers       = final_intel.get("risk_triggers",      [])
        strategy       = final_intel.get("strategy",           {})
        decision       = final_intel.get("decision",           {})
        nlp_intel      = final_intel.get("nlp_intelligence",   {})

        scenarios = scenario_block.get("scenarios", [])
        meta      = scenario_block.get("meta",      {})
        assets    = asset_block.get("assets",       {})
        sectors   = asset_block.get("sectors",      {})

        report = f"""
==============================
 SENTINEL: MACRO INTELLIGENCE REPORT
==============================
 Generated: {timestamp}
==============================

------------------------------
 MACRO REGIME
------------------------------
 Regime:     {regime_data.get("regime", "N/A").replace("_", " ")}
 Confidence: {int(regime_data.get("confidence", 0) * 100)}%
 Challenger: {regime_data.get("challenger", "None").replace("_", " ")}

 Narrative:
 {regime_data.get("narrative", "N/A")}

 Key Drivers:
{self._format_list(regime_data.get("drivers", []))}
"""

        if nlp_intel:
            report += f"""
------------------------------
 NLP INTELLIGENCE
------------------------------
 Dominant Theme:  {nlp_intel.get("dominant_theme", "N/A")}
 RBI Implication: {regime_data.get("components", {}).get("rbi_signal", "UNKNOWN")}
 Equity Bias:     {regime_data.get("components", {}).get("equity_bias", "NEUTRAL")}
 NLP Confidence:  {int(nlp_intel.get("nlp_confidence", 0) * 100)}%
 Source:          {nlp_intel.get("source", "keyword")}

 Key Signals:
{self._format_list(nlp_intel.get("key_signals", []))}

 India Risks:
{self._format_list(nlp_intel.get("india_risks", []))}

 Global Factors:
{self._format_list(nlp_intel.get("global_factors", []))}

 Reasoning:
 {nlp_intel.get("reasoning", "N/A")}
"""

        report += f"""
------------------------------
 SCENARIO LANDSCAPE
------------------------------
 Dominant Scenario: {meta.get("dominant_scenario", "N/A")}
 Dispersion:        {round(meta.get("dispersion", 0), 2)}
 Scenario Confidence: {int(meta.get("confidence", 0) * 100)}%

{self._format_scenarios(scenarios)}
"""

        report += f"""
------------------------------
 PORTFOLIO POSITIONING
------------------------------
 Stance:     {positioning.get("stance", "N/A")}
 Conviction: {round(positioning.get("meta", {}).get("conviction", 0) * 100)}%

 Asset Allocation:
{self._format_allocation(positioning.get("allocation", {}))}

 Sector Positioning:
{self._format_sector_positioning(positioning.get("sector_positioning", []))}
"""

        if strategy:
            report += f"""
------------------------------
 STRATEGY INTELLIGENCE
------------------------------
 Type:      {strategy.get("strategy_type", "N/A")}
 Horizon:   {strategy.get("time_horizon", "N/A")}
 Conviction:{strategy.get("conviction", "N/A")}

 Playbook:
{self._format_list(strategy.get("playbook", []))}

 Risk Framework:
{self._format_dict(strategy.get("risk_framework", {}))}
"""

        if decision:
            dec_risk = decision.get("risk", {})
            report += f"""
------------------------------
 DECISION INTELLIGENCE
------------------------------
 {decision.get("summary", "")}

 Risk Level:        {dec_risk.get("risk_level", "N/A")}
 Expected Drawdown: {dec_risk.get("expected_drawdown", 0)}%
 Worst Case:        {dec_risk.get("worst_case", 0)}%

 Decision Allocation:
{self._format_decision_allocation(decision.get("allocation", {}))}

 Sector Bets: {", ".join(decision.get("sector_bets", []))}
"""

        report += f"""
------------------------------
 KEY TRIGGERS
------------------------------
{self._format_triggers(triggers)}

------------------------------
 ACTIONABLE INSIGHT
------------------------------
{self._generate_actionable_insight(regime_data, meta, positioning, strategy)}

==============================
 END OF REPORT
==============================
"""
        return report.strip()

    def _format_list(self, items):
        if not items:
            return "  None identified"
        return "\n".join(f"  • {item}" for item in items)

    def _format_dict(self, d):
        if not d:
            return "  None"
        return "\n".join(f"  • {k.replace('_',' ').title()}: {v}" for k, v in d.items())

    def _format_scenarios(self, scenarios):
        lines = []
        for s in scenarios:
            name = s.get("name", "Unknown")
            prob = int(s.get("probability", 0) * 100)
            tag  = s.get("dominance", "")
            desc = s.get("description", "")
            lines.append(f"  [{tag}] {name}: {prob}%")
            if desc:
                lines.append(f"  → {desc[:120]}...")
        return "\n".join(lines)

    def _format_allocation(self, allocation):
        if not allocation:
            return "  No allocation data"
        lines = []
        for k, v in allocation.items():
            pct = round(v * 100, 1) if v <= 1.0 else round(v, 1)
            lines.append(f"  • {k.capitalize()}: {pct}%")
        return "\n".join(lines)

    def _format_decision_allocation(self, allocation):
        if not allocation:
            return "  No allocation data"
        return "\n".join(f"  • {k.capitalize()}: {v}%" for k, v in allocation.items())

    def _format_sector_positioning(self, sector_pos):
        if not sector_pos:
            return "  No sector data"
        lines = []
        for sp in sector_pos:
            if isinstance(sp, dict):
                lines.append(f"  • [{sp.get('badge','N')}] {sp.get('sector','')}: {sp.get('stance','')}")
        return "\n".join(lines)

    def _format_triggers(self, triggers):
        top = sorted(triggers, key=lambda x: x.get("priority", 0), reverse=True)[:5]
        if not top:
            return "  No active triggers"
        lines = []
        for t in top:
            lines.append(
                f"  • {t.get('name','')}: {t.get('action','')} "
                f"(If {t.get('condition','')})"
            )
        return "\n".join(lines)

    def _generate_actionable_insight(self, regime_data, meta, positioning, strategy):
        regime     = regime_data.get("regime", "").replace("_", " ").lower()
        dominant   = meta.get("dominant_scenario", "Base Case")
        conviction = positioning.get("meta", {}).get("conviction", 0.6)
        stance     = positioning.get("stance", "neutral")
        playbook   = strategy.get("playbook", [])[:2] if strategy else []

        deploy_msg = (
            "aggressive deployment into risk assets"
            if conviction > 0.7 else
            "measured allocation with active risk controls"
        )

        lines = [
            f"Base case remains {dominant} within a {regime} environment.",
            f"Portfolio stance is {stance} with conviction of {round(conviction * 100)}%,",
            f"suggesting {deploy_msg}.",
            "",
            "Priority actions:"
        ]
        for p in playbook:
            lines.append(f"  • {p}")

        lines += [
            "",
            "Key monitoring items:",
            "  • RBI policy signals and liquidity conditions",
            "  • FII flow momentum — regime dependent on foreign participation",
            "  • Crude oil and INR for external risk escalation",
            "  • Earnings season revisions for conviction confirmation"
        ]

        return "\n".join(lines)