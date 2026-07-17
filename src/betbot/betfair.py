from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .bfbm_markets import BfbmMarket, markets_to_payload


class BetfairAuthError(RuntimeError):
    """Erro de autenticacao que nao deve ser retentado em loop curto."""


@dataclass(frozen=True)
class BetfairCredentials:
    username: str
    password: str
    app_key: str
    cert_path: Path
    key_path: Path
    login_url: str = "https://identitysso-cert.betfair.bet.br/api/certlogin"
    betting_url: str = "https://api.betfair.bet.br/exchange/betting/json-rpc/v1"


def credentials_from_env() -> BetfairCredentials:
    def required(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise RuntimeError(f"Variavel ausente: {name}")
        return value

    return BetfairCredentials(
        username=required("BETFAIR_USERNAME"),
        password=required("BETFAIR_PASSWORD"),
        app_key=required("BETFAIR_APP_KEY"),
        cert_path=Path(required("BETFAIR_CERT_PATH")),
        key_path=Path(required("BETFAIR_KEY_PATH")),
        login_url=os.getenv("BETFAIR_CERT_LOGIN_URL", "https://identitysso-cert.betfair.bet.br/api/certlogin"),
        betting_url=os.getenv("BETFAIR_BETTING_API_URL", "https://api.betfair.bet.br/exchange/betting/json-rpc/v1"),
    )


class BetfairClient:
    def __init__(self, credentials: BetfairCredentials):
        self.credentials = credentials
        self.session_token = ""

    def login(self) -> str:
        cert = self.credentials.cert_path
        key = self.credentials.key_path
        if not cert.exists():
            raise RuntimeError(f"Certificado nao encontrado: {cert}")
        if not key.exists():
            raise RuntimeError(f"Chave privada nao encontrada: {key}")
        response = requests.post(
            self.credentials.login_url,
            data={"username": self.credentials.username, "password": self.credentials.password},
            headers={
                "X-Application": self.credentials.app_key,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            cert=(str(cert), str(key)),
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("loginStatus") != "SUCCESS":
            raise BetfairAuthError(f"Betfair loginStatus={payload.get('loginStatus')}")
        self.session_token = str(payload["sessionToken"])
        return self.session_token

    def _headers(self) -> dict[str, str]:
        if not self.session_token:
            self.login()
        return {
            "X-Application": self.credentials.app_key,
            "X-Authentication": self.session_token,
            "Content-Type": "application/json",
        }

    def call(self, method: str, params: dict[str, Any], request_id: int = 1) -> Any:
        body = {
            "jsonrpc": "2.0",
            "method": f"SportsAPING/v1.0/{method}",
            "params": params,
            "id": request_id,
        }
        for attempt in range(2):
            response = requests.post(
                self.credentials.betting_url,
                json=body,
                headers=self._headers(),
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if "error" not in payload:
                return payload.get("result")
            error_text = json.dumps(payload["error"], ensure_ascii=False)
            if attempt == 0 and ("INVALID_SESSION_INFORMATION" in error_text or "NO_SESSION" in error_text):
                self.session_token = ""
                continue
            raise RuntimeError(error_text)
        return None

    def football_market_catalogue(
        self,
        *,
        hours_ahead: int = 48,
        max_results: int = 1000,
        market_type_codes: list[str] | None = None,
        request_offset: int = 0,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        time_filter = {
            "from": now.isoformat().replace("+00:00", "Z"),
            "to": (now + timedelta(hours=max(1, hours_ahead))).isoformat().replace("+00:00", "Z"),
        }
        common = {
            "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME", "MARKET_DESCRIPTION"],
            "maxResults": str(max(1, max_results)),
            "sort": "FIRST_TO_START",
        }
        results: list[dict[str, Any]] = []
        seen_market_ids: set[str] = set()
        filters: list[dict[str, Any]] = [
            {"eventTypeIds": ["1"], "inPlayOnly": True},
            {"eventTypeIds": ["1"], "marketStartTime": time_filter},
        ]
        for index, market_filter in enumerate(filters, start=1 + request_offset):
            if market_type_codes:
                market_filter = {**market_filter, "marketTypeCodes": market_type_codes}
            rows = self.call("listMarketCatalogue", {**common, "filter": market_filter}, request_id=index) or []
            for row in rows:
                market_id = str(row.get("marketId") or "")
                if not market_id or market_id in seen_market_ids:
                    continue
                seen_market_ids.add(market_id)
                results.append(row)
        return results

    def market_books(self, market_ids: list[str], *, batch_size: int = 40) -> dict[str, dict[str, Any]]:
        books_by_market_id: dict[str, dict[str, Any]] = {}
        clean_ids = [market_id for market_id in dict.fromkeys(str(item) for item in market_ids) if market_id]
        for offset in range(0, len(clean_ids), max(1, batch_size)):
            batch = clean_ids[offset : offset + max(1, batch_size)]
            books = self.call(
                "listMarketBook",
                {
                    "marketIds": batch,
                    "priceProjection": {
                        "priceData": ["EX_BEST_OFFERS"],
                        "virtualise": True,
                    },
                },
                request_id=1000 + offset,
            ) or []
            for book in books:
                market_id = str(book.get("marketId") or "")
                if market_id:
                    books_by_market_id[market_id] = book
        return books_by_market_id

    def current_orders(
        self,
        *,
        bet_ids: list[str] | None = None,
        market_ids: list[str] | None = None,
        order_projection: str = "ALL",
        date_range: dict[str, str] | None = None,
        from_record: int = 0,
        record_count: int = 1000,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "orderProjection": order_projection,
            "fromRecord": max(0, from_record),
            "recordCount": max(1, min(1000, record_count)),
        }
        if bet_ids:
            params["betIds"] = [str(item) for item in bet_ids if str(item).strip()]
        if market_ids:
            params["marketIds"] = [str(item) for item in market_ids if str(item).strip()]
        if date_range:
            params["dateRange"] = date_range
        return self.call("listCurrentOrders", params, request_id=3001) or {}

    def cleared_orders(
        self,
        *,
        bet_ids: list[str] | None = None,
        market_ids: list[str] | None = None,
        bet_status: str = "SETTLED",
        settled_date_range: dict[str, str] | None = None,
        group_by: str = "BET",
        from_record: int = 0,
        record_count: int = 1000,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "betStatus": bet_status,
            "groupBy": group_by,
            "fromRecord": max(0, from_record),
            "recordCount": max(1, min(1000, record_count)),
        }
        if bet_ids:
            params["betIds"] = [str(item) for item in bet_ids if str(item).strip()]
        if market_ids:
            params["marketIds"] = [str(item) for item in market_ids if str(item).strip()]
        if settled_date_range:
            params["settledDateRange"] = settled_date_range
        return self.call("listClearedOrders", params, request_id=3002) or {}

    def market_profit_and_loss(
        self,
        market_ids: list[str],
        *,
        include_settled_bets: bool = True,
        include_bsp_bets: bool = True,
        net_of_commission: bool = True,
    ) -> list[dict[str, Any]]:
        clean_ids = [market_id for market_id in dict.fromkeys(str(item) for item in market_ids) if market_id]
        if not clean_ids:
            return []
        return self.call(
            "listMarketProfitAndLoss",
            {
                "marketIds": clean_ids,
                "includeSettledBets": include_settled_bets,
                "includeBspBets": include_bsp_bets,
                "netOfCommission": net_of_commission,
            },
            request_id=3003,
        ) or []


def _best_price(items: list[dict[str, Any]]) -> tuple[float, float]:
    if not items:
        return 0.0, 0.0
    first = items[0] or {}
    try:
        price = float(first.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    try:
        size = float(first.get("size") or 0)
    except (TypeError, ValueError):
        size = 0.0
    return price, size


def enrich_catalogue_with_books(catalogue: list[dict[str, Any]], books_by_market_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in catalogue:
        item = dict(row)
        market_id = str(item.get("marketId") or "")
        book = books_by_market_id.get(market_id) or {}
        runner_books = {str(runner.get("selectionId") or ""): runner for runner in (book.get("runners") or [])}
        runners: list[dict[str, Any]] = []
        for runner in item.get("runners") or []:
            runner_item = dict(runner)
            selection_id = str(runner_item.get("selectionId") or "")
            runner_book = runner_books.get(selection_id) or {}
            ex = runner_book.get("ex") or {}
            best_back, best_back_size = _best_price(ex.get("availableToBack") or [])
            best_lay, best_lay_size = _best_price(ex.get("availableToLay") or [])
            runner_item.update(
                {
                    "status": str(runner_book.get("status") or runner_item.get("status") or ""),
                    "lastPriceTraded": runner_book.get("lastPriceTraded"),
                    "bestBackPrice": best_back,
                    "bestBackSize": best_back_size,
                    "bestLayPrice": best_lay,
                    "bestLaySize": best_lay_size,
                }
            )
            runners.append(runner_item)
        item["runners"] = runners
        item["book"] = {
            "status": str(book.get("status") or ""),
            "inplay": bool(book.get("inplay")),
            "totalMatched": book.get("totalMatched"),
            "betDelay": book.get("betDelay"),
            "isMarketDataDelayed": bool(book.get("isMarketDataDelayed")),
        }
        enriched.append(item)
    return enriched


def catalogue_to_bfbm_markets(catalogue: list[dict[str, Any]]) -> list[BfbmMarket]:
    markets: list[BfbmMarket] = []
    for row in catalogue:
        event = row.get("event") or {}
        description = row.get("description") or {}
        book = row.get("book") or {}
        runners = row.get("runners") or []
        markets.append(
            BfbmMarket(
                event_name=str(event.get("name") or ""),
                market_name=str(row.get("marketName") or ""),
                event_id=str(event.get("id") or ""),
                market_id=str(row.get("marketId") or ""),
                market_type=str(description.get("marketType") or ""),
                status=str(book.get("status") or "OPEN"),
                start_time=str(row.get("marketStartTime") or description.get("marketTime") or ""),
                total_matched=str(book.get("totalMatched") or row.get("totalMatched") or ""),
                raw={
                    "source": "betfair_api",
                    "event": event,
                    "description": description,
                    "book": book,
                    "runners": runners,
                },
            )
        )
    return markets


def catalogue_payload(catalogue: list[dict[str, Any]]) -> dict[str, Any]:
    return markets_to_payload(
        catalogue_to_bfbm_markets(catalogue),
        source_path="betfair_api",
        source_modified_at=datetime.now(timezone.utc).isoformat(),
        source_age_seconds=0,
    )
