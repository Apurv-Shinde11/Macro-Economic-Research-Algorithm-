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