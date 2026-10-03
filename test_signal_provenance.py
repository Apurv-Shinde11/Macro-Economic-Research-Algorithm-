import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from data_ingestion import DataIngestor
from nse_data import NSEDataFetcher
from schema_validator import SchemaValidator
from schemas import ECONIQ_RUN_CONTRACT_VERSION, ECONIQ_RUN_RESULT_SCHEMA
from signal_provenance import (
    SIGNAL_SOURCE_POLICY,
    build_data_quality_summary,
    build_signal_metadata,
    build_signal_provenance,
)
from test_econiq_run_contract import _representative_run
from yield_curve import get_yield_curve_data_with_reliability


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class SignalProvenanceTests(unittest.TestCase):
    def test_live_current_market_signal(self):
        item = build_signal_metadata(
            "india_vix", 17.4, source="NSE", source_type="PRIMARY",
            observed_at="2026-10-01", acquisition="LIVE", now=NOW,
        )

        self.assertEqual(item["freshness"], "CURRENT")
        self.assertEqual(item["acquisition"], "LIVE")
        self.assertEqual(item["source_type"], "PRIMARY")

    def test_cached_value_can_be_current(self):
        item = build_signal_metadata(
            "usd_inr", 83.2, source="Yahoo cache", source_type="CACHE",
            observed_at="2026-10-01", acquisition="CACHED", cached=True,
            now=NOW,
        )

        self.assertEqual(item["freshness"], "CURRENT")
        self.assertEqual(item["acquisition"], "CACHED")
        self.assertTrue(item["cached"])

    def test_live_old_macro_observation_is_stale(self):
        item = build_signal_metadata(
            "cpi", 5.1, source="data.gov.in", source_type="PRIMARY",
            observed_at="2026-01", acquisition="LIVE", now=NOW,
        )

        self.assertEqual(item["acquisition"], "LIVE")
        self.assertEqual(item["freshness"], "STALE")
        self.assertEqual(item["observation_precision"], "month")

    def test_fallback_is_explicit(self):
        item = build_signal_metadata(
            "crude_oil", 94.0, source="hardcoded emergency default",
            source_type="FALLBACK", observed_at=None,
            acquisition="FALLBACK", fallback_reason="Provider fetch failed",
            now=NOW,
        )

        self.assertEqual(item["acquisition"], "FALLBACK")
        self.assertTrue(item["fallback_used"])
        self.assertEqual(item["freshness"], "UNKNOWN")
        self.assertEqual(item["fallback_reason"], "Provider fetch failed")

    def test_missing_value_is_not_promoted_to_success(self):
        item = build_signal_metadata(
            "us_10y", None, source="Yahoo Finance", source_type="SECONDARY",
            observed_at=None, acquisition="LIVE", now=NOW,
        )

        self.assertIsNone(item["value"])
        self.assertEqual(item["acquisition"], "MISSING")
        self.assertEqual(item["freshness"], "UNKNOWN")

    def test_observation_and_retrieval_dates_remain_distinct(self):
        observed_period = "2026-09"
        retrieved_time = "2026-10-01T12:00:00+00:00"
        item = build_signal_metadata(
            "cpi", 4.9, source="data.gov.in", source_type="PRIMARY",
            observed_at=observed_period, retrieved_at=retrieved_time,
            acquisition="LIVE", now=NOW,
        )

        self.assertEqual(item["observed_at"], observed_period)
        self.assertEqual(item["retrieved_at"], retrieved_time)
        self.assertEqual(item["observation_precision"], "month")
        self.assertEqual(item["age_days"], 30)

    def test_provenance_does_not_change_engine_value(self):
        raw_inputs = {"crude_price": 81.75}
        before = copy.deepcopy(raw_inputs)
        metadata = build_signal_metadata(
            "crude_oil", raw_inputs["crude_price"], source="Yahoo Finance",
            source_type="SECONDARY", acquisition="LIVE", now=NOW,
        )

        self.assertEqual(raw_inputs, before)
        self.assertEqual(metadata["value"], raw_inputs["crude_price"])

    def test_data_quality_counts_and_coverage(self):
        provenance = build_signal_provenance({
            "cpi": {
                "value": 5.1, "source": "data.gov.in", "source_type": "PRIMARY",
                "observed_at": "2026-01", "acquisition": "LIVE",
            },
            "fii": {
                "value": -1200, "source": "BSE", "source_type": "PRIMARY",
                "observed_at": "2026-10-01", "acquisition": "LIVE",
            },
            "dii": {
                "value": 950, "source": "cache", "source_type": "CACHE",
                "observed_at": "2026-10-01", "acquisition": "CACHED", "cached": True,
            },
            "bank_credit_growth": {
                "value": 12.8, "source": "maintained RBI history",
                "source_type": "FALLBACK", "observed_at": "2026-05",
                "acquisition": "FALLBACK", "fallback_reason": "No live run fetch",
            },
            "crude_oil": {
                "value": 94.0, "source": "hardcoded default",
                "source_type": "FALLBACK", "acquisition": "FALLBACK",
            },
            "india_10y": {
                "value": 6.85, "source": "FBIL", "source_type": "PRIMARY",
                "acquisition": "LIVE",
            },
        }, now=NOW)
        summary = build_data_quality_summary(provenance)

        self.assertEqual(summary["total_signals"], 10)
        self.assertEqual(summary["current"], 2)
        self.assertEqual(summary["stale"], 2)
        self.assertEqual(summary["unknown"], 6)
        self.assertEqual(summary["live"], 3)
        self.assertEqual(summary["cached"], 1)
        self.assertEqual(summary["fallback"], 2)
        self.assertEqual(summary["missing"], 4)
        self.assertEqual(summary["coverage_pct"], 60)
        self.assertEqual(summary["quality"], "POOR")

    def test_provenance_does_not_mutate_model_outputs(self):
        model_outputs = {
            "regime_confidence": 0.72,
            "scenario_probabilities": [0.41, 0.36, 0.23],
            "allocation": {"equity": 0.6},
        }
        before = copy.deepcopy(model_outputs)
        provenance = build_signal_provenance({}, now=NOW)
        quality = build_data_quality_summary(provenance)
        run_payload = {**model_outputs, "signal_provenance": provenance, "data_quality": quality}

        self.assertEqual(
            {key: run_payload[key] for key in model_outputs}, before
        )

    def test_canonical_contract_accepts_provenance_additively(self):
        payload = _representative_run()

        self.assertEqual(ECONIQ_RUN_CONTRACT_VERSION, "1.1")
        self.assertIn("signal_provenance", ECONIQ_RUN_RESULT_SCHEMA["required"])
        self.assertIn("data_quality", ECONIQ_RUN_RESULT_SCHEMA["required"])
        self.assertEqual(
            SchemaValidator().validate_econiq_run_result(
                payload, ECONIQ_RUN_RESULT_SCHEMA
            ),
            [],
        )

    def test_unknown_observation_date_stays_unknown(self):
        item = build_signal_metadata(
            "india_vix", 17.1, source="NSE", source_type="PRIMARY",
            observed_at=None, acquisition="LIVE", now=NOW,
        )

        self.assertIsNone(item["observed_at"])
        self.assertIsNone(item["age_days"])
        self.assertEqual(item["freshness"], "UNKNOWN")

    def test_source_policy_maps_hardened_macro_signals(self):
        for signal in ("cpi", "repo_rate", "bank_credit_growth", "india_10y"):
            policy = SIGNAL_SOURCE_POLICY[signal]
            self.assertIn("preferred", policy)
            self.assertIn("fallback", policy)
            self.assertTrue(policy["fallback"])

    def test_bank_credit_hardcoded_history_is_not_live(self):
        ingestor = DataIngestor()
        with patch.object(ingestor, "_fetch_dbie_credit_growth", return_value=None):
            signals = ingestor.fetch_india_activity_signals()

        self.assertEqual(signals["bank_credit"]["source_type"], "FALLBACK")
        self.assertEqual(signals["bank_credit"]["acquisition"], "FALLBACK")
        self.assertTrue(signals["bank_credit"]["fallback_used"])
        self.assertNotEqual(signals["bank_credit"]["source"], "RBI DBIE (live)")

    def test_yield_curve_fallback_is_explicitly_labeled(self):
        with patch("yield_curve.fetch_india_yields", return_value=({"10Y": 6.85}, "hardcoded")):
            with patch("yield_curve.fetch_us_yields", return_value=({"10Y": 4.32}, "partial_fallback")):
                payload = get_yield_curve_data_with_reliability()

        self.assertEqual(payload["india_source_details"]["source_type"], "FALLBACK")
        self.assertEqual(payload["india_source_details"]["acquisition"], "FALLBACK")
        self.assertTrue(payload["india_source_details"]["fallback_used"])
        self.assertEqual(payload["us_source_details"]["acquisition"], "FALLBACK")

    def test_existing_macro_hardcoded_defaults_are_marked_fallback(self):
        ingestor = DataIngestor()
        ingestor.fred_api_key = None
        ingestor.datagov_key = "fixture-key"
        with patch.object(ingestor, "_safe_request", return_value={}):
            macro = ingestor.fetch_macro_indicators()

        self.assertEqual(macro["repo_rate"], 6.5)
        self.assertEqual(macro["inflation"]["headline"], 5.2)
        self.assertEqual(macro["signal_sources"]["repo_rate"]["acquisition"], "FALLBACK")
        self.assertEqual(macro["signal_sources"]["cpi"]["acquisition"], "FALLBACK")
        self.assertIsNone(macro["signal_sources"]["cpi"]["observed_at"])

    def test_live_macro_period_is_preserved_without_day_precision(self):
        ingestor = DataIngestor()
        ingestor.fred_api_key = None
        ingestor.datagov_key = "fixture-key"
        with patch.object(
            ingestor,
            "_safe_request",
            side_effect=[{}, {"records": [{"cpi_general": "4.7", "year": 2026, "month": 9}]}],
        ):
            macro = ingestor.fetch_macro_indicators()

        cpi_source = macro["signal_sources"]["cpi"]
        self.assertEqual(macro["inflation"]["headline"], 4.7)
        self.assertEqual(cpi_source["source_type"], "PRIMARY")
        self.assertEqual(cpi_source["acquisition"], "LIVE")
        self.assertEqual(cpi_source["observed_at"], "2026-09")

    def test_yahoo_market_quote_times_are_retained(self):
        ingestor = DataIngestor()
        market_time = int(NOW.timestamp())
        response = {
            "quoteResponse": {
                "result": [
                    {"symbol": "INR=X", "regularMarketPrice": 83.2, "regularMarketTime": market_time},
                    {"symbol": "^TNX", "regularMarketPrice": 4.1, "regularMarketTime": market_time},
                ]
            }
        }
        with patch.object(ingestor, "_safe_request_fast", return_value=response):
            market = ingestor.fetch_market_data()

        expected = NOW.isoformat()
        self.assertEqual(market["fx"]["usd_inr"], 83.2)
        self.assertEqual(market["rates"]["us10y"], 4.1)
        self.assertEqual(market["signal_observations"]["usd_inr"], expected)
        self.assertEqual(market["signal_observations"]["us10y"], expected)

    def test_nse_index_timestamp_is_retained(self):
        fetcher = NSEDataFetcher()
        response = {
            "data": [{
                "indexSymbol": "INDIA VIX",
                "last": "17.4",
                "previousClose": "17.0",
                "percentChange": "2.35",
                "yearHigh": "30.0",
                "yearLow": "10.0",
                "lastUpdateTime": "01-Oct-2026 15:30:00",
            }]
        }
        with patch.object(fetcher, "_fetch", return_value=response):
            indices = fetcher.get_indices()

        self.assertEqual(indices["india_vix"]["last"], 17.4)
        self.assertEqual(
            indices["india_vix"]["observed_at"], "01-Oct-2026 15:30:00"
        )


if __name__ == "__main__":
    unittest.main()
