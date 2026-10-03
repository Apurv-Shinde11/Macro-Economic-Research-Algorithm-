import math

from signal_provenance import (
    ACQUISITION_STATES,
    FRESHNESS_STATES,
    SIGNAL_FREQUENCY_CLASSES,
    SOURCE_TYPES,
    build_data_quality_summary,
)


class RunContractError(ValueError):
    """A successful run is missing a required structural/economic value."""


class SchemaValidator:
    def __init__(self):
        pass

    def validate(self, data, schema, layer_name="Unknown"):
        if not isinstance(data, dict):
            raise TypeError(f"{layer_name} output must be dict")

        for key, rules in schema.items():

            if key not in data:
                raise KeyError(f"{layer_name} missing key: '{key}'")

            # ✅ Handles both flat and rich schema formats
            expected_type = rules if isinstance(rules, type) else rules.get("type")

            if expected_type and not isinstance(data[key], expected_type):
                raise TypeError(
                    f"{layer_name}.{key} must be {expected_type.__name__}, "
                    f"got {type(data[key]).__name__}"
                )

        return True

    def validate_regime_core(self, regime):
        if not isinstance(regime, dict):
            raise RunContractError("regime must be a dict")
        if not isinstance(regime.get("regime"), str) or not regime["regime"].strip():
            raise RunContractError("regime.regime must be a non-empty string")
        self._validate_number(regime.get("confidence"), "regime.confidence")

    def validate_econiq_run_result(self, data, schema):
        if not isinstance(data, dict):
            raise RunContractError("EconIQ run result must be a dict")

        for field, expected in schema["required"].items():
            if field not in data:
                raise RunContractError(f"Missing required run field: {field}")
            if not isinstance(data[field], expected):
                raise RunContractError(
                    f"Run field {field} has invalid type "
                    f"{type(data[field]).__name__}"
                )

        self.validate_regime_core(data["regime"])
        scenario_rows = data["scenarios"].get("scenarios")
        if not isinstance(scenario_rows, list) or not scenario_rows:
            raise RunContractError(
                "scenarios.scenarios must be a non-empty list"
            )
        for index, scenario in enumerate(scenario_rows):
            if not isinstance(scenario, dict):
                raise RunContractError(f"scenarios.scenarios[{index}] must be a dict")
            self._validate_number(
                scenario.get("probability"),
                f"scenarios.scenarios[{index}].probability",
            )

        provenance = data["signal_provenance"]
        for signal, expected_frequency in SIGNAL_FREQUENCY_CLASSES.items():
            item = provenance.get(signal)
            if not isinstance(item, dict):
                raise RunContractError(
                    f"signal_provenance.{signal} must be a dict"
                )
            if item.get("signal") != signal:
                raise RunContractError(
                    f"signal_provenance.{signal}.signal must match its key"
                )
            if item.get("acquisition") not in ACQUISITION_STATES:
                raise RunContractError(
                    f"signal_provenance.{signal}.acquisition is invalid"
                )
            if item.get("freshness") not in FRESHNESS_STATES:
                raise RunContractError(
                    f"signal_provenance.{signal}.freshness is invalid"
                )
            if item.get("source_type") not in SOURCE_TYPES:
                raise RunContractError(
                    f"signal_provenance.{signal}.source_type is invalid"
                )
            if item.get("frequency_class") != expected_frequency:
                raise RunContractError(
                    f"signal_provenance.{signal}.frequency_class is invalid"
                )
            for field in (
                "value", "unit", "source", "observed_at", "retrieved_at",
                "age_days", "quality",
            ):
                if field not in item:
                    raise RunContractError(
                        f"signal_provenance.{signal}.{field} is required"
                    )
            if not isinstance(item["unit"], str) or not isinstance(item["source"], str):
                raise RunContractError(
                    f"signal_provenance.{signal} unit/source must be strings"
                )
            if not isinstance(item["retrieved_at"], str):
                raise RunContractError(
                    f"signal_provenance.{signal}.retrieved_at must be a string"
                )
            if item["observed_at"] is not None and not isinstance(item["observed_at"], str):
                raise RunContractError(
                    f"signal_provenance.{signal}.observed_at must be a string or null"
                )
            if item["age_days"] is not None and (
                isinstance(item["age_days"], bool)
                or not isinstance(item["age_days"], int)
            ):
                raise RunContractError(
                    f"signal_provenance.{signal}.age_days must be an integer or null"
                )
            if item["quality"] not in {"GOOD", "DEGRADED", "POOR", "UNKNOWN"}:
                raise RunContractError(
                    f"signal_provenance.{signal}.quality is invalid"
                )
            if not isinstance(item.get("fallback_used"), bool):
                raise RunContractError(
                    f"signal_provenance.{signal}.fallback_used must be bool"
                )
            if not isinstance(item.get("cached"), bool):
                raise RunContractError(
                    f"signal_provenance.{signal}.cached must be bool"
                )
            if item["fallback_used"] != (item["acquisition"] == "FALLBACK"):
                raise RunContractError(
                    f"signal_provenance.{signal}.fallback_used conflicts with acquisition"
                )

        quality = data["data_quality"]
        quality_fields = (
            "total_signals", "current", "recent", "stale", "unknown",
            "live", "cached", "fallback", "missing", "coverage_pct", "quality",
        )
        for field in quality_fields:
            if field not in quality:
                raise RunContractError(f"data_quality.{field} is required")
            if field == "quality":
                valid_type = isinstance(quality[field], str)
            else:
                valid_type = (
                    not isinstance(quality[field], bool)
                    and isinstance(quality[field], int)
                )
            if not valid_type:
                raise RunContractError(f"data_quality.{field} has invalid type")
        if quality["quality"] not in {"GOOD", "DEGRADED", "POOR", "UNKNOWN"}:
            raise RunContractError("data_quality.quality is invalid")
        if not isinstance(quality.get("quality_basis"), str):
            raise RunContractError("data_quality.quality_basis must be a string")
        if quality != build_data_quality_summary(provenance):
            raise RunContractError(
                "data_quality must match the deterministic signal_provenance summary"
            )

        contract_meta = data["contract_meta"]
        for field in ("contract_version", "generated_at", "pipeline", "validation_status"):
            if not isinstance(contract_meta.get(field), str):
                raise RunContractError(f"contract_meta.{field} must be a string")
        if contract_meta["validation_status"] not in {"valid", "repaired", "partial"}:
            raise RunContractError("contract_meta.validation_status is invalid")

        warnings = []
        for field, expected in schema["optional"].items():
            if field not in data:
                warnings.append(f"optional field missing: {field}")
            elif not isinstance(data[field], expected):
                warnings.append(f"optional field has invalid type: {field}")

        return warnings

    @staticmethod
    def set_econiq_run_validation_status(data, repairs, warnings):
        if warnings:
            status = "partial"
        elif repairs:
            status = "repaired"
        else:
            status = "valid"
        data["contract_meta"]["validation_status"] = status
        return status

    @staticmethod
    def _validate_number(value, field):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RunContractError(f"{field} must be numeric")
        if not math.isfinite(value):
            raise RunContractError(f"{field} must be finite")