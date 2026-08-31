"""
rotate_secrets.py — ONE-TIME USE

Prompts interactively (input is not echoed to the terminal) for the 5
rotated secret values and writes them into .streamlit/secrets.toml,
replacing the matching ROTATED_2026-08-22_PASTE_NEW_..._HERE placeholder
for each one.

Run this yourself directly in your own terminal:
    python rotate_secrets.py

Values are typed at the terminal prompt only — never pass them as command
line arguments (those can end up in shell history) and never paste them
into a chat.

The script deletes itself after a successful write. If self-deletion
fails for any reason, delete rotate_secrets.py by hand afterward.
"""
import getpass
import os
import sys

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
SECRETS_PATH = os.path.join(SCRIPT_DIR, ".streamlit", "secrets.toml")

# (env var name, placeholder currently sitting in secrets.toml)
PLACEHOLDERS = [
    ("GROQ_API_KEY",         "ROTATED_2026-08-22_PASTE_NEW_GROQ_KEY_HERE"),
    ("GEMINI_API_KEY",       "ROTATED_2026-08-22_PASTE_NEW_GEMINI_KEY_HERE"),
    ("MISTRAL_API_KEY",      "ROTATED_2026-08-22_PASTE_NEW_MISTRAL_KEY_HERE"),
    ("SUPABASE_SERVICE_KEY", "ROTATED_2026-08-22_PASTE_NEW_SUPABASE_SERVICE_ROLE_KEY_HERE"),
    ("GMAIL_APP_PASS",       "ROTATED_2026-08-22_PASTE_NEW_GMAIL_APP_PASSWORD_HERE"),
]


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def main():
    if not os.path.isfile(SECRETS_PATH):
        print(f"ERROR: {SECRETS_PATH} not found. Run this from the repo root.")
        sys.exit(1)

    with open(SECRETS_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    missing = [name for name, placeholder in PLACEHOLDERS if placeholder not in content]
    if missing:
        print("ERROR: these placeholders were not found in secrets.toml")
        print("(already replaced, or the file changed since this script was written):")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    print("Enter each rotated value below. Nothing you type here will be echoed")
    print("to the screen or shown back to you.\n")

    values = {}
    for name, _ in PLACEHOLDERS:
        while True:
            val = getpass.getpass(f"{name}: ").strip()
            if val:
                break
            print("  (empty input, try again)")
        values[name] = val

    for name, placeholder in PLACEHOLDERS:
        content = content.replace(placeholder, _toml_escape(values[name]))

    with open(SECRETS_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\nWrote {len(PLACEHOLDERS)} rotated values into {SECRETS_PATH}")

    try:
        os.remove(__file__)
        print("rotate_secrets.py deleted itself. Done.")
    except Exception as e:
        print(f"Could not self-delete ({e}).")
        print("Please delete rotate_secrets.py by hand now.")


if __name__ == "__main__":
    main()
