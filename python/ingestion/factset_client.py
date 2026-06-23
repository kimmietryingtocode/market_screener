import os
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv()

username = os.getenv("FACTSET_USERNAME_SERIAL")
api_key = os.getenv("FACTSET_API_KEY")

if not username or not api_key:
    raise RuntimeError("Missing FACTSET_USERNAME_SERIAL or FACTSET_API_KEY in .env")

url = "https://api.factset.com/formula-api/health"

response = requests.get(
    url,
    auth=HTTPBasicAuth(username, api_key),
    headers={"Accept": "application/json"},
    timeout=30,
)

print("Status:", response.status_code)
print(response.text)

response.raise_for_status()