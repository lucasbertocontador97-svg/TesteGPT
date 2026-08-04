from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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

    async def odds_multi(self, event_ids: list[str], bookmakers: list[str], *, include_links: bool = True) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        params = {
            "apiKey": self.api_key,
            "eventIds": ",".join(event_ids[:10]),
        }
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        if include_links:
            params["includeLinks"] = "true"
        try:
            data = await self.http.get_json(f"{self.base_url}/odds/multi", params=params)
        except httpx.HTTPStatusError as exc:
            if include_links and exc.response.status_code == 403:
                return await self.odds_multi(event_ids, bookmakers, include_links=False)
            raise
        return data if isinstance(data, list) else []

    async def odds(self, event_id: str, bookmakers: list[str], *, include_links: bool = True) -> dict[str, Any] | None:
        params = {
            "apiKey": self.api_key,
            "eventId": event_id,
        }
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        if include_links:
            params["includeLinks"] = "true"
        try:
            data = await self.http.get_json(f"{self.base_url}/odds", params=params)
        except httpx.HTTPStatusError as exc:
            if include_links and exc.response.status_code == 403:
                return await self.odds(event_id, bookmakers, include_links=False)
            raise
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

    async def fixture_players(self, fixture_id: int) -> list[dict[str, Any]]:
        data = await self.http.get_json(
            f"{self.base_url}/fixtures/players",
            params={"fixture": fixture_id},
            headers=self.headers,
        )
        return data.get("response", []) if isinstance(data, dict) else []

    async def fixture_by_id(self, fixture_id: int) -> dict[str, Any] | None:
        data = await self.http.get_json(f"{self.base_url}/fixtures", params={"id": fixture_id}, headers=self.headers)
        response = data.get("response", []) if isinstance(data, dict) else []
        return response[0] if response else None


class SportmonksClient:
    base_url = "https://api.sportmonks.com/v3/football"

    def __init__(self, api_token: str, http: HttpJsonClient) -> None:
        self.api_token = api_token
        self.http = http

    async def live_scores(self) -> list[dict[str, Any]]:
        data = await self.http.get_json(
            f"{self.base_url}/livescores/inplay",
            params={
                "api_token": self.api_token,
                "include": "participants;statistics.type;scores",
            },
        )
        return data.get("data", []) if isinstance(data, dict) else []

    async def fixture_by_id(self, fixture_id: int) -> dict[str, Any] | None:
        data = await self.http.get_json(
            f"{self.base_url}/fixtures/{fixture_id}",
            params={
                "api_token": self.api_token,
                "include": "participants;statistics",
            },
        )
        fixture = data.get("data") if isinstance(data, dict) else None
        return fixture if isinstance(fixture, dict) else None

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
                {"api_token": self.api_token, "include": "participants;statistics.type;scores"},
            ),
            (
                "fixtures/date hoje",
                f"{self.base_url}/fixtures/date/{today}",
                {"api_token": self.api_token, "include": "participants"},
            ),
            (
                "fixtures/date hoje com stats",
                f"{self.base_url}/fixtures/date/{today}",
                {"api_token": self.api_token, "include": "participants;statistics"},
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


class TheStatsApiClient:
    base_url = "https://api.thestatsapi.com/api"

    def __init__(self, api_key: str, http: HttpJsonClient) -> None:
        self.api_key = api_key
        self.http = http

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def live_matches(self, limit: int = 100) -> list[dict[str, Any]]:
        data = await self.http.get_json(
            f"{self.base_url}/football/matches",
            params={"status": "live", "per_page": min(limit, 100)},
            headers=self.headers,
        )
        return data.get("data", []) if isinstance(data, dict) else []

    async def match_stats(self, match_id: str) -> dict[str, Any] | None:
        data = await self.http.get_json(
            f"{self.base_url}/football/matches/{match_id}/stats",
            headers=self.headers,
        )
        stats = data.get("data") if isinstance(data, dict) else None
        return stats if isinstance(stats, dict) else None

    async def diagnostic(self) -> list[dict[str, Any]]:
        checks = [
            ("health", f"{self.base_url}/health", {}),
            ("football live matches", f"{self.base_url}/football/matches", {"status": "live", "per_page": 20}),
        ]
        results = []
        for label, url, params in checks:
            status, data = await self.http.get_status_json(url, params=params, headers=self.headers)
            items = data.get("data", []) if isinstance(data, dict) else []
            message = ""
            if isinstance(data, dict):
                error = data.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or error.get("code") or "")
                else:
                    message = str(data.get("message") or "")
            results.append(
                {
                    "label": label,
                    "status": status,
                    "count": len(items) if isinstance(items, list) else (1 if data else 0),
                    "message": message[:300],
                }
            )
        return results


class SportDBClient:
    base_url = "https://api.sportdb.dev/api/flashscore"
    live_cache_ttl_seconds = 25
    _live_cache: list[dict[str, Any]] = []
    _live_cache_ts: float = 0.0

    def __init__(self, api_key: str, http: HttpJsonClient) -> None:
        self.api_key = api_key
        self.http = http

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    @staticmethod
    def _items(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []
        for key in ("data", "events", "matches", "results", "response"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @classmethod
    def _cache_age(cls) -> float:
        return time.time() - cls._live_cache_ts if cls._live_cache_ts else 10**9

    async def football_live(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._live_cache and self._cache_age() <= self.live_cache_ttl_seconds:
            return self._live_cache[:limit]
        data = await self.http.get_json(f"{self.base_url}/football/live", headers=self.headers)
        items = self._items(data)
        self.__class__._live_cache = items
        self.__class__._live_cache_ts = time.time()
        return items[:limit]

    async def match_stats(self, match_id: str) -> dict[str, Any] | list[dict[str, Any]] | None:
        data = await self.http.get_json(f"{self.base_url}/match/{match_id}/stats", headers=self.headers)
        return data

    async def diagnostic(self) -> list[dict[str, Any]]:
        results = []
        status, data = await self.http.get_status_json(f"{self.base_url}/football/live", headers=self.headers)
        live_items = self._items(data)
        results.append(
            {
                "label": "football/live",
                "status": status,
                "count": len(live_items),
                "message": str(data.get("message") or data.get("error") or "")[:300] if isinstance(data, dict) else "",
                "sample": live_items[0] if live_items else None,
            }
        )
        sample_id = None
        if live_items:
            sample = live_items[0]
            sample_id = sample.get("eventId") or sample.get("id") or sample.get("matchId") or sample.get("event_id")
        if sample_id:
            stats_status, stats_data = await self.http.get_status_json(
                f"{self.base_url}/match/{sample_id}/stats",
                headers=self.headers,
            )
            stat_items = self._items(stats_data)
            results.append(
                {
                    "label": "match/{id}/stats",
                    "status": stats_status,
                    "count": len(stat_items),
                    "message": str(stats_data.get("message") or stats_data.get("error") or "")[:300]
                    if isinstance(stats_data, dict)
                    else "",
                    "sample": stat_items[0] if stat_items else None,
                }
            )
        return results


class SofaScoreClient:
    base_url = "https://zylalabs.com/api/12787/sofascore+-+live+api"
    live_cache_ttl_seconds = 60
    stats_cache_ttl_seconds = 120
    package_cache_ttl_seconds = 120
    _live_cache: list[dict[str, Any]] = []
    _live_cache_ts: float = 0.0
    _stats_cache: dict[str, Any] = {}
    _stats_cache_ts: dict[str, float] = {}
    _package_cache: dict[str, dict[str, Any]] = {}
    _package_cache_ts: dict[str, float] = {}
    match_package_endpoints = {
        "details": "25094/get+match+details",
        "statistics": "25099/get+match+statistics",
        "incidents": "25100/get+match+incidents",
        "lineups": "25096/get+match+lineups",
        "odds": "25097/get+match+odds",
        "shotmap": "25845/get+match+shotmap",
        "pregame_form": "25842/get+match+pregame+form",
        "best_players": "25840/get+match+best+players",
        "player_average_positions": "25844/get+match+player+average+positions",
    }

    def __init__(self, api_key: str, http: HttpJsonClient) -> None:
        self.api_key = api_key
        self.http = http

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def _items(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []
        for key in ("data", "events", "matches", "results", "response"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @classmethod
    def _live_cache_age(cls) -> float:
        return time.time() - cls._live_cache_ts if cls._live_cache_ts else 10**9

    @classmethod
    def _stats_cache_age(cls, match_id: str) -> float:
        ts = cls._stats_cache_ts.get(match_id)
        return time.time() - ts if ts else 10**9

    @classmethod
    def _package_cache_age(cls, match_id: str) -> float:
        ts = cls._package_cache_ts.get(match_id)
        return time.time() - ts if ts else 10**9

    async def live_matches(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._live_cache and self._live_cache_age() <= self.live_cache_ttl_seconds:
            return self._live_cache[:limit]
        data = await self.http.get_json(
            f"{self.base_url}/25092/get+live+matches",
            params={"sport_slug": "football"},
            headers=self.headers,
        )
        items = self._items(data)
        self.__class__._live_cache = items
        self.__class__._live_cache_ts = time.time()
        return items[:limit]

    async def match_statistics(self, match_id: str) -> dict[str, Any] | list[dict[str, Any]] | None:
        cache_key = str(match_id)
        if cache_key in self._stats_cache and self._stats_cache_age(cache_key) <= self.stats_cache_ttl_seconds:
            return self._stats_cache[cache_key]
        data = await self.http.get_json(
            f"{self.base_url}/25099/get+match+statistics",
            params={"match_id": cache_key},
            headers=self.headers,
        )
        self.__class__._stats_cache[cache_key] = data
        self.__class__._stats_cache_ts[cache_key] = time.time()
        return data

    async def _match_endpoint(self, match_id: str, name: str, path: str) -> tuple[str, dict[str, Any]]:
        try:
            data = await self.http.get_json(
                f"{self.base_url}/{path}",
                params={"match_id": str(match_id)},
                headers=self.headers,
            )
            return name, {"ok": True, "data": data}
        except httpx.HTTPStatusError as exc:
            return name, {
                "ok": False,
                "status": exc.response.status_code,
                "error": str(exc)[:300],
                "data": None,
            }
        except Exception as exc:
            return name, {"ok": False, "status": None, "error": str(exc)[:300], "data": None}

    async def match_package(self, match_id: str) -> dict[str, Any]:
        cache_key = str(match_id)
        if cache_key in self._package_cache and self._package_cache_age(cache_key) <= self.package_cache_ttl_seconds:
            return self._package_cache[cache_key]

        pairs = await asyncio.gather(
            *[
                self._match_endpoint(cache_key, name, path)
                for name, path in self.match_package_endpoints.items()
            ]
        )
        sources = {name: payload for name, payload in pairs}
        package = {
            "provider": "zyla_sofascore",
            "match_id": cache_key,
            "coverage": {
                name: bool(payload.get("ok") and payload.get("data") is not None)
                for name, payload in sources.items()
            },
            "sources": sources,
        }
        self.__class__._package_cache[cache_key] = package
        self.__class__._package_cache_ts[cache_key] = time.time()
        if sources.get("statistics", {}).get("ok"):
            self.__class__._stats_cache[cache_key] = sources["statistics"].get("data")
            self.__class__._stats_cache_ts[cache_key] = time.time()
        return package

    async def diagnostic(self) -> list[dict[str, Any]]:
        results = []
        status, data = await self.http.get_status_json(
            f"{self.base_url}/25092/get+live+matches",
            params={"sport_slug": "football"},
            headers=self.headers,
        )
        live_items = self._items(data)
        results.append(
            {
                "label": "get live matches",
                "status": status,
                "count": len(live_items),
                "message": str(data.get("message") or data.get("error") or "")[:300] if isinstance(data, dict) else "",
                "sample": live_items[0] if live_items else None,
            }
        )
        sample_id = None
        if live_items:
            sample = live_items[0]
            sample_id = sample.get("id") or sample.get("eventId") or sample.get("matchId") or sample.get("event_id")
        if sample_id:
            package = await self.match_package(str(sample_id))
            stat_data = package.get("sources", {}).get("statistics", {}).get("data")
            stat_items = self._items(stat_data)
            ok_sources = [name for name, ok in package.get("coverage", {}).items() if ok]
            failed_sources = [name for name, ok in package.get("coverage", {}).items() if not ok]
            results.append(
                {
                    "label": "get match package completo",
                    "status": 200 if ok_sources else 500,
                    "count": len(ok_sources),
                    "message": f"ok={','.join(ok_sources)} | falhou={','.join(failed_sources)}",
                    "sample": stat_items[0] if stat_items else None,
                }
            )
        return results


class TotalCornerClient:
    base_url = "https://api.totalcorner.com/v1"
    # TotalCorner can return 0/1 rows when unsupported column combinations are
    # requested. dangerousAttacks was verified to keep the full in-play list and
    # gives the motor a useful pressure signal beyond score/corners.
    live_columns = "dangerousAttacks"
    live_cache_ttl_seconds = 45
    live_stale_ttl_seconds = 600
    _inplay_cache: list[dict[str, Any]] = []
    _inplay_cache_ts: float = 0.0

    def __init__(self, token: str, http: HttpJsonClient) -> None:
        self.token = token
        self.http = http

    @staticmethod
    def _data_items(data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        error = data.get("error")
        if data.get("success") == 0 and error:
            message = ""
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("code") or "")
            raise RuntimeError(message or "TotalCorner returned error payload")
        items = data.get("data", [])
        return items if isinstance(items, list) else []

    @classmethod
    def _cache_age(cls) -> float:
        return time.time() - cls._inplay_cache_ts if cls._inplay_cache_ts else 10**9

    @classmethod
    def _cached_items(cls, limit: int, *, max_age: int | None = None) -> list[dict[str, Any]]:
        ttl = max_age if max_age is not None else cls.live_cache_ttl_seconds
        if cls._inplay_cache and cls._cache_age() <= ttl:
            return cls._inplay_cache[:limit]
        return []

    @staticmethod
    def _looks_rate_limited(exc: Exception) -> bool:
        text = str(exc).lower()
        return "rate limit" in text or "too many" in text or "429" in text

    async def today_inplay(self, limit: int = 100) -> list[dict[str, Any]]:
        cached = self._cached_items(limit)
        if cached:
            return cached

        try:
            enriched_data = await self.http.get_json(
                f"{self.base_url}/match/today",
                params={"token": self.token, "type": "inplay", "columns": self.live_columns},
            )
            enriched_items = self._data_items(enriched_data)
            if enriched_items:
                self.__class__._inplay_cache = enriched_items
                self.__class__._inplay_cache_ts = time.time()
                return enriched_items[:limit]
        except Exception as exc:
            logger.warning("TotalCorner enriched in-play failed; falling back to basic feed: %s", exc)
            stale = self._cached_items(limit, max_age=self.live_stale_ttl_seconds)
            if stale and self._looks_rate_limited(exc):
                logger.warning("TotalCorner rate-limited; using %.0fs stale cache.", self._cache_age())
                return stale
            await asyncio.sleep(3)
        try:
            basic_data = await self.http.get_json(
                f"{self.base_url}/match/today",
                params={"token": self.token, "type": "inplay"},
            )
            basic_items = self._data_items(basic_data)
            if basic_items:
                self.__class__._inplay_cache = basic_items
                self.__class__._inplay_cache_ts = time.time()
            return basic_items[:limit]
        except Exception as exc:
            cache_age = self._cache_age()
            if self.__class__._inplay_cache and cache_age <= self.live_stale_ttl_seconds:
                logger.warning("TotalCorner basic in-play failed; using %.0fs cache: %s", cache_age, exc)
                return self.__class__._inplay_cache[:limit]
            raise

    async def today_all(self, limit: int = 200) -> list[dict[str, Any]]:
        data = await self.http.get_json(
            f"{self.base_url}/match/today",
            params={"token": self.token, "columns": self.live_columns},
        )
        return self._data_items(data)[:limit]

    async def diagnostic(self) -> list[dict[str, Any]]:
        checks = [
            ("match/today inplay básico", f"{self.base_url}/match/today", {"token": self.token, "type": "inplay"}),
            (
                "match/today inplay com stats",
                f"{self.base_url}/match/today",
                {"token": self.token, "type": "inplay", "columns": self.live_columns},
            ),
        ]
        results = []
        for label, url, params in checks:
            status, data = await self.http.get_status_json(url, params=params)
            items = data.get("data", []) if isinstance(data, dict) else []
            message = ""
            if isinstance(data, dict):
                error = data.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or error.get("code") or "")
                else:
                    message = str(data.get("message") or "")
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
