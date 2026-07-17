from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from betbot.betfair import BetfairAuthError, BetfairClient, credentials_from_env  # noqa: E402


def chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def best_price(prices: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    if not prices:
        return None, None
    price = prices[0].get("price")
    volume = prices[0].get("size")
    try:
        price_value = float(price)
    except (TypeError, ValueError):
        price_value = None
    try:
        volume_value = float(volume)
    except (TypeError, ValueError):
        volume_value = None
    return price_value, volume_value


def iso_z(value: str | None) -> str:
    if not value:
        return ""
    return str(value).replace("+00:00", "Z")


def fetch_payload(client: BetfairClient, *, hours_ahead: int, max_results: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    time_filter = {
        "from": now.isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(hours=max(1, hours_ahead))).isoformat().replace("+00:00", "Z"),
    }
    catalogue = client.call(
        "listMarketCatalogue",
        {
            "filter": {
                "eventTypeIds": ["1"],
                "marketStartTime": time_filter,
            },
            "marketProjection": [
                "EVENT",
                "COMPETITION",
                "RUNNER_DESCRIPTION",
                "MARKET_START_TIME",
                "MARKET_DESCRIPTION",
            ],
            "sort": "FIRST_TO_START",
            "maxResults": str(max(1, max_results)),
        },
    ) or []
    live_catalogue = client.call(
        "listMarketCatalogue",
        {
            "filter": {
                "eventTypeIds": ["1"],
                "inPlayOnly": True,
            },
            "marketProjection": [
                "EVENT",
                "COMPETITION",
                "RUNNER_DESCRIPTION",
                "MARKET_START_TIME",
                "MARKET_DESCRIPTION",
            ],
            "sort": "FIRST_TO_START",
            "maxResults": str(max(1, min(max_results, 200))),
        },
        request_id=2,
    ) or []

    by_market_id: dict[str, dict[str, Any]] = {}
    for row in [*live_catalogue, *catalogue]:
        market_id = str(row.get("marketId") or "")
        if market_id:
            by_market_id[market_id] = row

    market_ids = list(by_market_id)
    market_books: dict[str, dict[str, Any]] = {}
    for request_id, batch in enumerate(chunks(market_ids, 40), start=10):
        books = client.call(
            "listMarketBook",
            {
                "marketIds": batch,
                "priceProjection": {
                    "priceData": ["EX_BEST_OFFERS"],
                    "virtualise": True,
                },
            },
            request_id=request_id,
        ) or []
        for book in books:
            market_books[str(book.get("marketId") or "")] = book

    events: dict[str, dict[str, Any]] = {}
    markets_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for market_id, row in by_market_id.items():
        event = row.get("event") or {}
        description = row.get("description") or {}
        competition = row.get("competition") or {}
        event_id = str(event.get("id") or "")
        if not event_id:
            continue
        book = market_books.get(market_id) or {}
        runner_books = {str(item.get("selectionId")): item for item in (book.get("runners") or [])}
        runners: list[dict[str, Any]] = []
        for runner in row.get("runners") or []:
            selection_id = str(runner.get("selectionId") or "")
            runner_book = runner_books.get(selection_id) or {}
            ex = runner_book.get("ex") or {}
            back, back_size = best_price(ex.get("availableToBack") or [])
            lay, lay_size = best_price(ex.get("availableToLay") or [])
            runners.append(
                {
                    "selection_id": selection_id,
                    "runner_name": str(runner.get("runnerName") or ""),
                    "back": back,
                    "lay": lay,
                    "back_size": back_size,
                    "lay_size": lay_size,
                    "status": str(runner_book.get("status") or ""),
                }
            )
        events[event_id] = {
            "event_id": event_id,
            "event_name": str(event.get("name") or ""),
            "competition": str(competition.get("name") or ""),
            "country_code": str(event.get("countryCode") or ""),
            "start_time": iso_z(str(row.get("marketStartTime") or description.get("marketTime") or event.get("openDate") or "")),
            "inplay": bool(book.get("inplay")),
            "markets": markets_by_event[event_id],
        }
        markets_by_event[event_id].append(
            {
                "market_id": market_id,
                "market_type": str(description.get("marketType") or ""),
                "market_name": str(row.get("marketName") or ""),
                "status": str(book.get("status") or ""),
                "inplay": bool(book.get("inplay")),
                "total_matched": book.get("totalMatched"),
                "runners": runners,
            }
        )

    payload_events = list(events.values())
    payload_events.sort(key=lambda item: (not item.get("inplay"), item.get("start_time") or "", item.get("event_name") or ""))
    return {
        "source": "testegpt-local-betfair-br",
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "events": payload_events,
    }


def post_payload(url: str, token: str, payload: dict[str, Any]) -> str:
    response = requests.post(
        url,
        json=payload,
        headers={"X-Ingest-Token": token, "Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Envia snapshot Betfair BR para endpoint de ingestao.")
    parser.add_argument(
        "--ingest-url",
        action="append",
        required=True,
        help="Endpoint /api/betfair/ingest. Pode ser informado mais de uma vez.",
    )
    parser.add_argument("--token", required=True)
    parser.add_argument("--hours-ahead", type=int, default=48)
    parser.add_argument("--max-results", type=int, default=200)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--auth-error-cooldown-seconds", type=int, default=3600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    client = BetfairClient(credentials_from_env())
    while True:
        try:
            payload = fetch_payload(client, hours_ahead=args.hours_ahead, max_results=args.max_results)
            event_count = len(payload.get("events") or [])
            market_count = sum(len(event.get("markets") or []) for event in payload.get("events") or [])
            results = []
            successful_posts = 0
            for ingest_url in args.ingest_url:
                try:
                    result = post_payload(ingest_url, args.token, payload)
                except Exception as exc:
                    results.append(f"{ingest_url} => ERRO {type(exc).__name__}: {exc}")
                    continue
                successful_posts += 1
                results.append(f"{ingest_url} => {result[:120]}")
            if successful_posts == 0:
                raise RuntimeError("Nenhum endpoint de ingestao aceitou o payload")
            print(
                f"[ingest] eventos={event_count} mercados={market_count} envios={' | '.join(results)}",
                flush=True,
            )
            if args.once:
                return 0
            time.sleep(max(5, args.poll_seconds))
        except BetfairAuthError as exc:
            print(
                f"[ingest] erro_auth={exc}. Pausando por {max(300, args.auth_error_cooldown_seconds)}s para evitar excesso de logins.",
                flush=True,
            )
            client = BetfairClient(credentials_from_env())
            if args.once:
                return 1
            time.sleep(max(300, args.auth_error_cooldown_seconds))
        except Exception as exc:
            print(f"[ingest] erro={type(exc).__name__}: {exc}", flush=True)
            if args.once:
                return 1
            time.sleep(max(60, min(300, args.poll_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
