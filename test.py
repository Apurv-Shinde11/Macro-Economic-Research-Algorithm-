import requests

url = "https://api.tradingeconomics.com/country/india/indicator/interest%20rate?c=guest:guest"

headers = {
    "User-Agent": "Mozilla/5.0"
}

res = requests.get(url, headers=headers)

print("STATUS:", res.status_code)
print("TEXT:", res.text[:500])