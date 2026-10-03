import copy
import unittest

from schema_repair_engine import SchemaRepairEngine
from schema_validator import RunContractError, SchemaValidator
from schemas import ECONIQ_RUN_RESULT_SCHEMA
from signal_provenance import build_data_quality_summary, build_signal_provenance


def _representative_run():
    payload = {
        "regime": {"regime": "STABLE_GROWTH", "confidence": 0.73},
        "strategy": {"conviction": "MEDIUM", "playbook": ["Hold"]},
        "decision": {"summary": "Maintain current positioning"},
        "positioning": {"allocation": {"equity": 0.6}},
        "scenarios": {
            "scenarios": [
                {"type": "baseline", "probability": 0.41},
                {"type": "bullish", "probability": 0.36},
                {"type": "bearish", "probability": 0.23},
            ],
            "meta": {"dominant_scenario": "Base Case"},
        },
        "triggers": [],
        "liquidity": {},
        "intel": {},
        "nse": {"india_vix": 17.2},
        "macro": {"growth": {"gdp": 6.8}},
        "final_intel": {},
        "report": "Current report",
        "sector_heatmap": {},
        "narrative_delta": {"has_delta": False},
        "regime_stability": {"score": "HIGH"},
        "transition": {},
        "anticipatory": {},
        "leading_intelligence": {},
        "briefing_allowed": False,
        "briefing_blocked_reason": "Confidence below 60% gate",
        "regime_is_unstable": False,
        "challenger_delta": None,
        "intelligence_object": {"signals": [{"id": "growth", "value": 6.8}]},
        "story": {"status": "paused", "message": "Briefing paused"},
        "guidance": {"status": "withheld", "reason": "Briefing paused"},
        "contract_meta": {
            "contract_version": "1.1",
            "generated_at": "2026-10-01T12:00:00+00:00",
            "pipeline": "sentinel",
            "validation_status": "valid",
        },
    }
    payload["signal_provenance"] = build_signal_provenance({})
    payload["data_quality"] = build_data_quality_summary(
        payload["signal_provenance"]
    )
    return payload


class EconIQRunContractTests(unittest.TestCase):
    def setUp(self):
        self.validator = SchemaValidator()
        self.repair = SchemaRepairEngine()

    def test_valid_complete_run(self):
        self.assertEqual(
            self.validator.validate_econiq_run_result(
                _representative_run(), ECONIQ_RUN_RESULT_SCHEMA
            ),
            [],
        )

    def test_missing_optional_field_is_nonfatal_partial(self):
        payload = _representative_run()
        del payload["report"]

        warnings = self.validator.validate_econiq_run_result(
            payload, ECONIQ_RUN_RESULT_SCHEMA
        )
        status = self.validator.set_econiq_run_validation_status(
            payload, [], warnings
        )

        self.assertIn("optional field missing: report", warnings)
        self.assertEqual(status, "partial")
        self.assertEqual(payload["contract_meta"]["validation_status"], "partial")
        self.assertIn("regime", payload)

    def test_missing_optional_list_is_safely_repaired(self):
        payload = _representative_run()
        del payload["triggers"]

        repaired, changes = self.repair.repair_econiq_run_result(
            payload, ECONIQ_RUN_RESULT_SCHEMA
        )
        warnings = self.validator.validate_econiq_run_result(
            repaired, ECONIQ_RUN_RESULT_SCHEMA
        )
        status = self.validator.set_econiq_run_validation_status(
            repaired, changes, warnings
        )

        self.assertEqual(repaired["triggers"], [])
        self.assertIn("triggers", changes)
        self.assertEqual(status, "repaired")
        self.assertEqual(repaired["contract_meta"]["validation_status"], "repaired")
        self.assertEqual(
            self.validator.validate_econiq_run_result(
                repaired, ECONIQ_RUN_RESULT_SCHEMA
            ),
            [],
        )

    def test_missing_economic_value_is_not_fabricated(self):
        payload = _representative_run()
        del payload["regime"]["confidence"]
        del payload["macro"]["growth"]["gdp"]
        repaired, _ = self.repair.repair_econiq_run_result(
            payload, ECONIQ_RUN_RESULT_SCHEMA
        )

        with self.assertRaisesRegex(RunContractError, "regime.confidence"):
            self.validator.validate_econiq_run_result(
                repaired, ECONIQ_RUN_RESULT_SCHEMA
            )
        self.assertNotIn("confidence", repaired["regime"])
        self.assertNotIn("gdp", repaired["macro"]["growth"])

    def test_story_and_guidance_survive_unchanged(self):
        payload = _representative_run()
        story = copy.deepcopy(payload["story"])
        guidance = copy.deepcopy(payload["guidance"])

        self.validator.validate_econiq_run_result(
            payload, ECONIQ_RUN_RESULT_SCHEMA
        )

        self.assertEqual(payload["story"], story)
        self.assertEqual(payload["guidance"], guidance)

    def test_briefing_gate_survives_unchanged(self):
        payload = _representative_run()
        self.validator.validate_econiq_run_result(
            payload, ECONIQ_RUN_RESULT_SCHEMA
        )

        self.assertIs(payload["briefing_allowed"], False)
        self.assertEqual(
            payload["briefing_blocked_reason"], "Confidence below 60% gate"
        )

    def test_scenario_probabilities_are_not_recalculated(self):
        payload = _representative_run()
        original = copy.deepcopy(payload["scenarios"])

        self.validator.validate_econiq_run_result(
            payload, ECONIQ_RUN_RESULT_SCHEMA
        )

        self.assertEqual(payload["scenarios"], original)

    def test_dashboard_field_names_remain_available(self):
        payload = _representative_run()
        dashboard_fields = {
            "regime", "strategy", "decision", "scenarios", "triggers",
            "liquidity", "intel", "nse", "macro", "final_intel", "report",
            "narrative_delta", "regime_stability", "transition", "anticipatory",
            "leading_intelligence", "briefing_allowed",
            "briefing_blocked_reason", "intelligence_object", "story", "guidance",
        }

        self.assertTrue(dashboard_fields.issubset(payload))
        self.assertEqual(
            self.validator.validate_econiq_run_result(
                payload, ECONIQ_RUN_RESULT_SCHEMA
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()