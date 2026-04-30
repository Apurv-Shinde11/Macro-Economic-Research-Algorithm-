class SchemaRepairEngine:
    def __init__(self):
        pass

    def repair(self, data, schema):
        if not isinstance(data, dict):
            data = {}

        repaired = dict(data)

        for key, rules in schema.items():

            # ✅ Flat schema: {"regime": str, "confidence": float}
            if isinstance(rules, type):
                if key not in repaired or not isinstance(repaired.get(key), rules):
                    repaired[key] = self._default_for_type(rules)
                continue

            # ✅ Rich schema: {"key": {"type": str, "default": "N/A"}}
            if key not in repaired:
                repaired[key] = rules.get("default")

            value = repaired.get(key)
            expected_type = rules.get("type")

            if expected_type and not isinstance(value, expected_type):
                repaired[key] = self._cast(value, expected_type, rules)

            if isinstance(rules.get("schema"), dict):
                repaired[key] = self.repair(
                    repaired.get(key, {}),
                    rules.get("schema")
                )

        return repaired

    def _default_for_type(self, t):
        defaults = {
            str:   "",
            int:   0,
            float: 0.0,
            list:  [],
            dict:  {},
            bool:  False
        }
        return defaults.get(t, None)

    def _cast(self, value, expected_type, rules):
        try:
            return expected_type(value)
        except Exception:
            return rules.get("default")