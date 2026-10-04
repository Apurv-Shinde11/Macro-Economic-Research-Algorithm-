import copy
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from NLP import IndianMacroNLP
from rbi_policy_intelligence import (
    build_rbi_policy_intelligence,
    extract_mpc_facts,
)


CURRENT_TEXT = (
    "The MPC decided to keep the policy repo rate unchanged at 5.25 per cent. "
    "The MPC also decided to continue with the neutral stance. "
    "The decision was taken by a majority of 4-2. "
    "CPI inflation is projected at 4.5 per cent for FY 2026-27. "
    "Real GDP growth is projected at 6.8 per cent for FY 2026-27. "
    "Rationale for Monetary Policy Decisions. The MPC highlighted that upside risks to inflation remain due to food prices and crude oil. "
    "Liquidity conditions remain tight. The rupee and global uncertainty pose risks."
)

PREVIOUS_TEXT = (
    "The MPC decided to keep the policy repo rate unchanged at 5.50 per cent. "
    "The stance of monetary policy remains neutral. "
    "The decision was taken unanimously. "
    "CPI inflation is projected at 4.8 per cent for FY 2026-27. "
    "Real GDP growth is projected at 6.5 per cent for FY 2026-27. "
    "Growth remains resilient."
)

REAL_AUGUST_FORECAST_TEXT = (
    "While CPI inflation increased to 4.4 per cent in June 2026, it was lower than what was earlier projected for Q1:2026-27. "
    "Considering all these factors, CPI inflation for 2026-27 is projected to be 5.0 per cent with Q2 at 4.7 per cent; Q3 at 5.9 per cent; and Q4 at 5.5 per cent. "
    "Inflation for Q1:2027-28 is projected at 5.3 per cent with risks being evenly balanced. "
    "Core inflation is projected at 4.3 per cent for 2026-27. "
    "Taking all these factors into consideration, real GDP growth for 2026-27 is projected at 6.7 per cent, with Q1 at 7.0 per cent; Q2 at 6.4 per cent; Q3 at 6.5 per cent; and Q4 at 6.8 per cent. "
    "Real GDP growth for Q1:2027-28 is projected at 7.3 per cent."
)


def _document(text, date, meeting_id, prid):
    return {
        "text": text[:3000],
        "full_text": text,
        "date": date,
        "meeting_date": date,
        "meeting_id": meeting_id,
        "prid": prid,
        "source": f"RBI MPC {date}",
        "url": f"https://www.rbi.org.in/release/{prid}",
        "fetched": True,
        "retrieved_at": "2026-10-03T12:00:00+00:00",
    }


class RbiPolicyIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.current = _document(CURRENT_TEXT, "August 2026", "2026-08", "63287")
        self.previous = _document(PREVIOUS_TEXT, "June 2026", "2026-06", "62863")

    def test_extracts_structured_facts_and_provenance(self):
        facts = extract_mpc_facts(self.current)

        self.assertEqual(facts["repo_rate_pct"], 5.25)
        self.assertEqual(facts["policy_decision"], "PAUSE")
        self.assertEqual(facts["policy_stance"], "NEUTRAL")
        self.assertEqual(facts["vote_split"], {"for": 4, "against": 2})
        self.assertEqual(facts["cpi_projections"][0]["value_pct"], 4.5)
        self.assertEqual(facts["gdp_projections"][0]["value_pct"], 6.8)
        self.assertEqual(facts["source"]["url"], self.current["url"])
        self.assertEqual(facts["source"]["retrieved_at"], self.current["retrieved_at"])

    def test_missing_facts_remain_unknown(self):
        facts = extract_mpc_facts({"text": "MPC reviewed domestic and global conditions."})

        self.assertIsNone(facts["repo_rate_pct"])
        self.assertIsNone(facts["policy_decision"])
        self.assertIsNone(facts["vote_split"])
        self.assertEqual(facts["cpi_projections"], [])
        self.assertEqual(facts["gdp_projections"], [])

    def test_repo_rate_does_not_borrow_a_later_projection_value(self):
        facts = extract_mpc_facts({
            "text": "The policy repo rate remains unchanged. CPI inflation is projected at 4.5 per cent for FY 2026-27."
        })

        self.assertIsNone(facts["repo_rate_pct"])
        self.assertEqual(facts["cpi_projections"][0]["value_pct"], 4.5)

    def test_hawkish_tone_has_matching_source_evidence(self):
        result = build_rbi_policy_intelligence(self.current)

        self.assertEqual(result["tone"]["label"], "HAWKISH")
        self.assertGreater(result["tone"]["score"], 0)
        self.assertTrue(result["tone"]["evidence"])
        self.assertIn("not a calibrated probability", result["tone"]["confidence_basis"])
        for evidence in result["tone"]["evidence"]:
            self.assertIn(evidence["excerpt"], CURRENT_TEXT)

    def test_foreign_central_bank_language_does_not_drive_rbi_tone(self):
        text = (
            "The MPC decided to keep the policy repo rate unchanged at 5.25 per cent. "
            "The MPC retained the neutral stance. "
            "Global Outlook. Persistent inflation has prompted several central banks to raise rates while others remain vigilant. "
            "Rationale for Monetary Policy Decisions. The MPC will continue with the neutral stance."
        )
        result = build_rbi_policy_intelligence(_document(text, "August 2026", "2026-08", "63287"))

        self.assertEqual(result["tone"]["label"], "NEUTRAL")
        self.assertEqual(result["tone"]["score"], 0.0)
        self.assertEqual(result["tone"]["evidence"], [])

    def test_neutral_hold_anchors_and_dampens_mild_hawkish_guidance(self):
        text = (
            "The MPC decided to keep the policy repo rate unchanged at 5.25 per cent. "
            "The MPC retained the neutral stance. "
            "Rationale for Monetary Policy Decisions. The MPC underscored that it will maintain a close vigil."
        )
        result = build_rbi_policy_intelligence(_document(text, "August 2026", "2026-08", "63287"))

        self.assertEqual(result["tone"]["label"], "HAWKISH")
        self.assertLess(result["tone"]["score"], 0.5)
        self.assertEqual(result["tone"]["anchor_alignment"], "MIXED")
        self.assertTrue(result["tone"]["anchor_note"])
        self.assertTrue(all(
            item["direction"] == "MIXED"
            for item in result["transmission"].values()
        ))

    def test_substantive_rbi_guidance_affects_tone_with_source_evidence(self):
        text = (
            "The MPC decided to keep the policy repo rate unchanged at 5.25 per cent. "
            "The MPC retained the neutral stance. "
            "Rationale for Monetary Policy Decisions. The MPC underscored that it will maintain a close vigil and remain resolute in its commitment to align inflation with the target."
        )
        result = build_rbi_policy_intelligence(_document(text, "August 2026", "2026-08", "63287"))

        self.assertTrue(result["tone"]["evidence"])
        self.assertTrue(all("MPC" in item["excerpt"] for item in result["tone"]["evidence"]))
        self.assertTrue(any("close vigil" in item["matched_phrase"] for item in result["tone"]["evidence"]))

    def test_explicit_pause_anchors_tone_when_stance_is_missing(self):
        text = (
            "The MPC decided to keep the policy repo rate unchanged at 5.25 per cent. "
            "Rationale for Monetary Policy Decisions. The MPC underscored that it will maintain a close vigil."
        )
        result = build_rbi_policy_intelligence(_document(text, "August 2026", "2026-08", "63287"))

        self.assertIsNone(result["facts"]["policy_stance"])
        self.assertEqual(result["tone"]["anchor_alignment"], "MIXED")
        self.assertLess(result["tone"]["score"], 0.5)
        self.assertTrue(all(item["direction"] == "MIXED" for item in result["transmission"].values()))

    def test_projection_records_preserve_metric_period_and_exclude_observed_cpi(self):
        facts = extract_mpc_facts({"text": REAL_AUGUST_FORECAST_TEXT})
        cpi = {(item["metric"], item["period"]): item["value_pct"] for item in facts["cpi_projections"]}
        gdp = {(item["metric"], item["period"]): item["value_pct"] for item in facts["gdp_projections"]}

        self.assertEqual(cpi, {
            ("CPI_HEADLINE", "FY2026-27"): 5.0,
            ("CPI_HEADLINE", "Q2_FY2026-27"): 4.7,
            ("CPI_HEADLINE", "Q3_FY2026-27"): 5.9,
            ("CPI_HEADLINE", "Q4_FY2026-27"): 5.5,
            ("CPI_HEADLINE", "Q1_FY2027-28"): 5.3,
            ("CPI_CORE", "FY2026-27"): 4.3,
        })
        self.assertEqual(gdp, {
            ("GDP_REAL", "FY2026-27"): 6.7,
            ("GDP_REAL", "Q1_FY2026-27"): 7.0,
            ("GDP_REAL", "Q2_FY2026-27"): 6.4,
            ("GDP_REAL", "Q3_FY2026-27"): 6.5,
            ("GDP_REAL", "Q4_FY2026-27"): 6.8,
            ("GDP_REAL", "Q1_FY2027-28"): 7.3,
        })
        self.assertNotIn(4.4, [item["value_pct"] for item in facts["cpi_projections"]])

    def test_unanimous_vote_is_preserved_without_inventing_numeric_split(self):
        facts = extract_mpc_facts({
            "text": "The MPC voted unanimously to keep the policy repo rate unchanged at 5.25 per cent."
        })

        self.assertEqual(facts["vote_result"], "UNANIMOUS")
        self.assertIsNone(facts["vote_split"])
        self.assertIn("unanimously", facts["vote_evidence"])

    def test_laf_repo_instrument_does_not_create_system_liquidity_driver(self):
        text = "The MPC voted unanimously to keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 5.25 per cent."
        result = build_rbi_policy_intelligence(_document(text, "August 2026", "2026-08", "63287"))

        self.assertEqual(result["facts"]["liquidity_references"], [])
        self.assertNotIn("LIQUIDITY", {item["driver"] for item in result["drivers"]})

    def test_primary_reason_uses_substantive_mpc_sentence_not_heading(self):
        text = (
            "Growth and Inflation Outlook Global Outlook 3. "
            "Rationale for Monetary Policy Decisions. "
            "The MPC underscored that it will maintain a close vigil and remain resolute in its commitment to align inflation with the target."
        )
        result = build_rbi_policy_intelligence(_document(text, "August 2026", "2026-08", "63287"))

        self.assertIn("The MPC underscored", result["interpretation"]["primary_reason"])
        self.assertNotIn("Global Outlook", result["interpretation"]["primary_reason"])

    def test_watch_items_identify_source_and_econiq_provenance(self):
        result = build_rbi_policy_intelligence(self.current, self.previous)
        source_items = [item for item in result["watch_triggers"] if item["source_type"] == "SOURCE_DERIVED"]
        monitor_items = [item for item in result["watch_triggers"] if item["source_type"] == "ECONIQ_MONITORING"]

        self.assertTrue(source_items)
        self.assertTrue(all(item["source_evidence"] for item in source_items))
        self.assertTrue(any(item["what_to_watch"] == "INR" for item in monitor_items))
        self.assertTrue(all(item["source_evidence"] is None for item in monitor_items))

    def test_dovish_tone_has_matching_source_evidence(self):
        text = (
            "Durable disinflation is underway and inflation has moderated. "
            "The MPC will support growth through an accommodative policy stance."
        )
        result = build_rbi_policy_intelligence(_document(text, "June 2026", "2026-06", "62863"))

        self.assertEqual(result["tone"]["label"], "DOVISH")
        self.assertLess(result["tone"]["score"], 0)
        self.assertTrue(result["tone"]["evidence"])
        for evidence in result["tone"]["evidence"]:
            self.assertIn(evidence["excerpt"], text)

    def test_comparison_detects_repo_change_and_projection_deltas(self):
        result = build_rbi_policy_intelligence(self.current, self.previous)

        self.assertEqual(result["comparison"]["status"], "AVAILABLE")
        self.assertEqual(result["comparison"]["repo"], "CUT")
        self.assertEqual(result["facts"]["repo_rate_change_bps"], -25)
        self.assertEqual(result["comparison"]["inflation_projection"], "DOWN")
        self.assertEqual(result["comparison"]["inflation_projection_delta_pp"], -0.3)
        self.assertEqual(result["comparison"]["growth_projection"], "UP")
        self.assertEqual(result["comparison"]["growth_projection_delta_pp"], 0.3)
        self.assertIn({
            "metric": "CPI_HEADLINE", "period": "FY2026-27",
            "current_pct": 4.5, "previous_pct": 4.8,
            "direction": "DOWN", "delta_pp": -0.3,
        }, result["comparison"]["projection_deltas"])

    def test_projection_comparison_handles_missing_and_mismatched_periods(self):
        result = build_rbi_policy_intelligence(self.current, self.previous)
        result["facts"]["cpi_projections"] = []

        missing = build_rbi_policy_intelligence(
            _document("The MPC decided to keep the policy repo rate at 5.25 per cent.", "August 2026", "2026-08", "63287"),
            self.previous,
        )
        self.assertEqual(missing["comparison"]["inflation_projection"], "UNKNOWN")
        self.assertEqual(result["comparison"]["inflation_projection"], "DOWN")

        mismatched = _document(
            "CPI inflation is projected at 4.8 per cent for FY 2025-26.",
            "June 2026", "2026-06", "62863",
        )
        result = build_rbi_policy_intelligence(self.current, mismatched)
        self.assertEqual(result["comparison"]["inflation_projection"], "UNKNOWN")

    def test_drivers_include_evidence(self):
        result = build_rbi_policy_intelligence(self.current)
        inflation = next(driver for driver in result["drivers"] if driver["driver"] == "INFLATION")

        self.assertTrue(inflation["evidence"])
        self.assertTrue(all(item in CURRENT_TEXT for item in inflation["evidence"]))

    def test_non_neutral_transmission_has_causal_explanation(self):
        result = build_rbi_policy_intelligence(self.current)

        non_neutral = [
            item for item in result["transmission"].values()
            if item["direction"] not in {"NEUTRAL", "UNKNOWN"}
        ]
        self.assertTrue(non_neutral)
        self.assertTrue(all(item["explanation"] for item in non_neutral))
        self.assertTrue(result["watch_triggers"])
        self.assertTrue(all(
            trigger["what_to_watch"] and trigger["why_it_matters"] and trigger["interpretation_change"]
            for trigger in result["watch_triggers"]
        ))

    def test_missing_source_and_analysis_failure_degrade_to_unknown(self):
        result = build_rbi_policy_intelligence({"text": "", "fetched": False})

        self.assertEqual(result["tone"]["label"], "UNKNOWN")
        self.assertIsNone(result["tone"]["score"])
        self.assertEqual(result["facts"]["repo_rate_pct"], None)
        self.assertEqual(result["comparison"]["status"], "UNAVAILABLE")
        self.assertEqual(result["provenance"]["acquisition"], "FALLBACK")
        self.assertTrue(all(
            item["direction"] == "UNKNOWN" and item["explanation"] is None
            for item in result["transmission"].values()
        ))

    def test_intelligence_remains_available_when_existing_nlp_provider_fails(self):
        nlp = IndianMacroNLP()
        with patch.object(nlp, "generate_text", return_value=None):
            legacy_intel = nlp.get_regime_scores_v2(CURRENT_TEXT, rbi_text=CURRENT_TEXT)
        result = build_rbi_policy_intelligence(self.current, self.previous)

        self.assertEqual(legacy_intel["rbi_policy_implication"], "PAUSE")
        self.assertEqual(result["facts"]["repo_rate_pct"], 5.25)
        self.assertEqual(result["tone"]["label"], "HAWKISH")

    def test_same_meeting_is_never_compared_to_itself(self):
        same_meeting = _document(CURRENT_TEXT, "August 2026", "2026-08", "63288")
        result = build_rbi_policy_intelligence(self.current, same_meeting)

        self.assertEqual(result["comparison"]["status"], "UNAVAILABLE")
        self.assertEqual(result["comparison"]["repo"], "UNKNOWN")

    def test_missing_intermediate_meeting_disables_comparison(self):
        february = _document(PREVIOUS_TEXT, "February 2026", "2026-02", "62169")
        june = _document(CURRENT_TEXT, "June 2026", "2026-06", "62863")
        result = build_rbi_policy_intelligence(june, february)

        self.assertEqual(result["comparison"]["status"], "UNAVAILABLE")
        self.assertEqual(result["comparison"]["repo"], "UNKNOWN")

    def test_intelligence_does_not_mutate_or_replace_model_outputs(self):
        model_outputs = {
            "regime": {"regime": "STABLE_GROWTH", "confidence": 0.72},
            "scenarios": {"scenarios": [{"probability": 0.5}, {"probability": 0.5}]},
            "allocation": {"equity": 0.6},
        }
        before = copy.deepcopy(model_outputs)

        intelligence = build_rbi_policy_intelligence(self.current, self.previous)

        self.assertEqual(model_outputs, before)
        self.assertIn("tone", intelligence)
        self.assertEqual(model_outputs["regime"]["confidence"], 0.72)
        self.assertEqual(model_outputs["scenarios"]["scenarios"][0]["probability"], 0.5)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to exercise the dashboard renderer")
    def test_sentinel_renderer_handles_missing_and_partial_intelligence(self):
        dashboard = Path(__file__).with_name("dashboard.html")
        script = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('function renderRbiPolicyIntelligence(intelligence) {');
const end = html.indexOf('\n    function renderRunResult(data, fromHistory)', start);
if (start < 0 || end < 0) throw new Error('RBI renderer not found');
const elements = new Map();
function element() {
    return {
        hidden: false, textContent: '', className: '', href: '', target: '', rel: '',
        children: [],
        replaceChildren(...children) { this.children = children; },
        appendChild(child) { this.children.push(child); return child; },
        append(...children) { this.children.push(...children); },
        get childElementCount() { return this.children.length; }
    };
}
const document = {
    getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
    },
    createElement() { return element(); }
};
const render = new Function('document', 'URL', html.slice(start, end) + '; return renderRbiPolicyIntelligence;')(document, URL);
render(null);
if (!document.getElementById('rbiPolicySection').hidden) throw new Error('Missing payload should hide the panel');
render({tone: {label: 'UNKNOWN'}, facts: {vote_split: 'malformed'}, drivers: [null], comparison: {}, transmission: {}, watch_triggers: [null]});
if (document.getElementById('rbiPolicySection').hidden) throw new Error('Partial payload should render safely');
if (document.getElementById('rbiPolicyChange').textContent.includes('undefined')) throw new Error('Undefined leaked into comparison');
if (document.getElementById('rbiPolicyWhy').textContent.includes('undefined')) throw new Error('Undefined leaked into explanation');
render({
    tone: {label: 'NEUTRAL'},
    facts: {
        vote_result: 'UNANIMOUS',
        cpi_projections: [{metric: 'CPI_HEADLINE', period: 'FY2026-27', value_pct: 5}],
        gdp_projections: [{metric: 'GDP_REAL', period: 'FY2026-27', value_pct: 6.7}]
    },
    comparison: {
        status: 'AVAILABLE', repo: 'UNCHANGED', stance: 'UNCHANGED',
        inflation_projection: 'DOWN', inflation_projection_delta_pp: -0.1,
        growth_projection: 'UP', growth_projection_delta_pp: 0.1
    },
    interpretation: {posture: 'NEUTRAL', primary_reason: 'The MPC maintained a close vigil on inflation.'},
    transmission: {
        long_duration_bonds: {direction: 'NEUTRAL'},
        banks: {direction: 'NEUTRAL'},
        inr: {direction: 'NEUTRAL'}
    },
    watch_triggers: [
        {what_to_watch: 'Next CPI print', source_type: 'SOURCE_DERIVED', why_it_matters: 'RBI discussed inflation.', interpretation_change: 'A reversal matters.'},
        {what_to_watch: 'INR', source_type: 'ECONIQ_MONITORING', why_it_matters: 'Monitor transmission.', interpretation_change: 'A sharp move matters.'}
    ]
});
if (!document.getElementById('rbiPolicyChange').textContent.includes('inflation forecast: down (-0.1 pp)')) throw new Error('Inflation delta missing from Sentinel');
if (!document.getElementById('rbiPolicyChange').textContent.includes('growth forecast: up (+0.1 pp)')) throw new Error('Growth delta missing from Sentinel');
if (document.getElementById('rbiPolicyWhy').textContent !== 'The MPC maintained a close vigil on inflation.') throw new Error('Sentinel Why did not use the policy rationale');
const detailText = node => node.textContent || (node.children || []).map(detailText).join(' ');
const renderedDetails = detailText(document.getElementById('rbiPolicyDetailBody'));
if (!renderedDetails.includes('UNANIMOUS')) throw new Error('Vote result missing from details');
if (!renderedDetails.includes('CPI HEADLINE · FY2026-27')) throw new Error('Forecast metric/period missing from details');
if (!renderedDetails.includes('ECONIQ MONITORING')) throw new Error('Watch provenance missing from details');
"""
        subprocess.run(
            [shutil.which("node"), "-e", script, str(dashboard)],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()