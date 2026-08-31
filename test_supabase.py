import os
import requests

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

url = f"{SUPABASE_URL}/rest/v1/profiles?select=id,email,tier"
headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}"
}
r = requests.get(url, headers=headers)
print(r.status_code)
print(r.json())