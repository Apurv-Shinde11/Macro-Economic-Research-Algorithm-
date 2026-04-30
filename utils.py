import json

# =========================
# 🛡️ SAFE DICT ENFORCER
# =========================
def ensure_dict(obj, name="Unknown"):
    """
    Ensures object is always a dictionary.
    Handles:
    - string JSON
    - None
    - invalid types
    """

    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return {}

    if not isinstance(obj, dict):
        return {}

    return obj


# =========================
# 🛡️ SAFE GET (NO CRASH)
# =========================
def safe_get(data, key, default=None):
    """
    Safe dictionary access.
    Prevents: 'str' object has no attribute 'get'
    """
    if not isinstance(data, dict):
        return default
    return data.get(key, default)


# =========================
# 🛡️ SAFE LIST
# =========================
def ensure_list(obj):
    if isinstance(obj, list):
        return obj
    return []


# =========================
# 🛡️ SAFE FLOAT
# =========================
def safe_float(value, default=0.0):
    try:
        return float(value)
    except:
        return default