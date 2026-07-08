from __future__ import annotations

import asyncio
from typing import Any

import httpx


class HttpJsonClient:
    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_json(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        for attempt in range(3):
            response = await self._client.get(url, params=params, headers=headers)
            if response.status_code == 429 and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()
        return response.json()

    async def get_status_json(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        response = await self._client.get(url, params=params, headers=headers)
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}
        return response.status_code, data


class OddsApiClient:
    base_url = "https://api.odds-api.io/v3"

    def __init__(self, api_key: str, http: HttpJsonClient) -> None:
        self.api_key = api_key
        self.http = http

    async def live_events(self, sport: str = "football", limit: int = 20) -> list[dict[str, Any]]:
        data = await self.http.get_json(
            f"{self.base_url}/events",
            params={"apiKey": self.api_key, "sport": sport, "status": "live", "limit": limit},
        )
        return data if isinstance(data, list) else []

    async def odds_multi(self, event_ids: list[str], bookmakers: list[str]) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        data = await self.http.get_json(
            f"{self.base_url}/odds/multi",
            params={
                "apiKey": self.api_key,
                "eventIds": ",".join(event_ids[:10]),
                "bookmakers": ",".join(bookmakers),
            },
        )
        return data if isinstance(data, list) else []

    async def odds(self, event_id: str, bookmakers: list[str]) -> dict[str, Any] | None:
        data = await self.http.get_json(
            f"{self.base_url}/odds",
            params={
                "apiKey": self.api_key,
                "eventId": event_id,
                "bookmakers": ",".join(bookmakers),
            },
        )
        return data if isinstance(data, dict) else None


class ApiFootballClient:
    base_url = "https://v3.football.api-sports.io"

    def __init__(self, api_key: str, http: HttpJsonClient) -> None:
        self.api_key = api_key
        self.http = http

    @property
    def headers(self) -> dict[str, str]:
        return {"x-apisports-key": self.api_key}

    async def live_fixtures(self) -> list[dict[str, Any]]:
        data = await self.http.get_json(f"{self.base_url}/fixtures", params={"live": "all"}, headers=self.headers)
        return data.get("response", []) if isinstance(data, dict) else []

    async def fixture_statistics(self, fixture_id: int) -> list[dict[str, Any]]:
        data = await self.http.get_json(
            f"{self.base_url}/fixtures/statistics",
            params={"fixture": fixture_id},
            headers=self.headers,
        )
        return data.get("response", []) if isinstance(data, dict) else []

    async def fixture_by_id(self, fixture_id: int) -> dict[str, Any] | None:
        data = await self.http.get_json(f"{self.base_url}/fixtures", params={"id": fixture_id}, headers=self.headers)
        response = data.get("response", []) if isinstance(data, dict) else []
        return response[0] if response else None


class SportmonksClient:
    base_url = "https://api.sportmonks.com/api/v3/football"

    def __init__(self, api_token: str, http: HttpJsonClient) -> None:
        self.api_token = api_token
        self.http = http

    async def live_scores(self) -> list[dict[str, Any]]:
        data = await self.http.get_json(
            f"{self.base_url}/livescores/inplay",
            params={
                "api_token": self.api_token,
                "include": "participants,statistics.type,scores",
            },
        )
        return data.get("data", []) if isinstance(data, dict) else []

    async def diagnostic(self, today: str) -> list[dict[str, Any]]:
        checks = [
            (
                "livescores/inplay básico",
                f"{self.base_url}/livescores/inplay",
                {"api_token": self.api_token},
            ),
            (
                "livescores/inplay com stats",
                f"{self.base_url}/livescores/inplay",
                {"api_token": self.api_token, "include": "participants,statistics.type,scores"},
            ),
            (
                "fixtures/date hoje",
                f"{self.base_url}/fixtures/date/{today}",
                {"api_token": self.api_token, "include": "participants"},
            ),
        ]
        results = []
        for label, url, params in checks:
            status, data = await self.http.get_status_json(url, params=params)
            items = data.get("data", []) if isinstance(data, dict) else []
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or data.get("error") or "")
                errors = data.get("errors")
                if errors:
                    message = f"{message} {errors}".strip()
            results.append(
                {
                    "label": label,
                    "status": status,
                    "count": len(items) if isinstance(items, list) else 0,
                    "message": message[:300],
                    "sample": items[0] if isinstance(items, list) and items else None,
                }
            )
        return results
