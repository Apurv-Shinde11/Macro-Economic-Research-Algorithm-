import math


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