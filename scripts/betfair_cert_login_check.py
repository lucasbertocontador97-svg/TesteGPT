import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value


def main() -> int:
    username = env("BETFAIR_USERNAME")
    password = env("BETFAIR_PASSWORD")
    app_key = env("BETFAIR_APP_KEY")
    cert_path = Path(env("BETFAIR_CERT_PATH"))
    key_path = Path(env("BETFAIR_KEY_PATH"))

    if not cert_path.exists():
        raise SystemExit(f"Certificate not found: {cert_path}")
    if not key_path.exists():
        raise SystemExit(f"Private key not found: {key_path}")

    try:
        import requests
    except Exception as exc:
        raise SystemExit(f"requests is required: {type(exc).__name__}: {exc}") from exc

    response = requests.post(
        "https://identitysso-cert.betfair.com/api/certlogin",
        data={"username": username, "password": password},
        headers={
            "X-Application": app_key,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        cert=(str(cert_path), str(key_path)),
        timeout=30,
    )
    print(f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception:
        print(response.text[:1000])
        return 1
    safe_payload = dict(payload)
    if "sessionToken" in safe_payload:
        safe_payload["sessionToken"] = "***"
    print(json.dumps(safe_payload, ensure_ascii=False, indent=2))
    if payload.get("loginStatus") != "SUCCESS":
        return 2

    session = payload["sessionToken"]
    market_filter = {
        "eventTypeIds": ["1"],
        "marketCountries": ["BR", "US", "EC", "AR", "CL", "PE", "UY", "BO", "PY"],
        "inPlayOnly": True,
    }
    body = {
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketCatalogue",
        "params": {
            "filter": market_filter,
            "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME"],
            "maxResults": "5",
        },
        "id": 1,
    }
    markets = requests.post(
        "https://api.betfair.com/exchange/betting/json-rpc/v1",
        json=body,
        headers={
            "X-Application": app_key,
            "X-Authentication": session,
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    print(f"Catalogue HTTP {markets.status_code}")
    print(json.dumps(markets.json(), ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
