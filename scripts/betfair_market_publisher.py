from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from betbot.betfair import (  # noqa: E402
    BetfairAuthError,
    BetfairClient,
    catalogue_payload,
    credentials_from_env,
    enrich_catalogue_with_books,
)


MARKET_TYPE_GROUPS = [
    ["MATCH_ODDS", "DRAW_NO_BET", "DOUBLE_CHANCE"],
    ["OVER_UNDER", "ALT_TOTAL_GOALS", "OVER_UNDER_05", "OVER_UNDER_15", "OVER_UNDER_25", "OVER_UNDER_35"],
    ["OVER_UNDER_45", "OVER_UNDER_55", "OVER_UNDER_65", "OVER_UNDER_75", "OVER_UNDER_85"],
    ["FIRST_HALF_GOALS_05", "FIRST_HALF_GOALS_15", "FIRST_HALF_GOALS_25", "HALF_TIME"],
    ["BOTH_TEAMS_TO_SCORE", "CORRECT_SCORE"],
    ["CORNER_ODDS", "OVER_UNDER_85_CORNR", "OVER_UNDER_105_CORNR"],
    ["ASIAN_HANDICAP", "BOOKING_ODDS"],
]


def post_json(url: str, payload: dict, timeout: int = 20) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def publish_once(client: BetfairClient, post_url: str, hours_ahead: int, max_results: int) -> tuple[int, int, str]:
    catalogue = []
    seen_market_ids: set[str] = set()
    for offset, market_type_codes in enumerate(MARKET_TYPE_GROUPS):
        rows = client.football_market_catalogue(
            hours_ahead=hours_ahead,
            max_results=max_results,
            market_type_codes=market_type_codes,
            request_offset=offset * 10,
        )
        for row in rows:
            market_id = str(row.get("marketId") or "")
            if not market_id or market_id in seen_market_ids:
                continue
            seen_market_ids.add(market_id)
            catalogue.append(row)
    books = client.market_books(list(seen_market_ids))
    enriched_catalogue = enrich_catalogue_with_books(catalogue, books)
    payload = catalogue_payload(enriched_catalogue)
    result = post_json(post_url, payload).strip()
    live_count = sum(1 for item in enriched_catalogue if ((item.get("book") or {}).get("inplay") or (item.get("description") or {}).get("turnInPlayEnabled")))
    priced_count = sum(
        1
        for item in enriched_catalogue
        for runner in (item.get("runners") or [])
        if float(runner.get("bestBackPrice") or 0) > 0 or runner.get("lastPriceTraded")
    )
    return len(enriched_catalogue), live_count, priced_count, result


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica catalogo Betfair BR no Railway.")
    parser.add_argument("--post-url", required=True)
    parser.add_argument("--hours-ahead", type=int, default=48)
    parser.add_argument("--max-results", type=int, default=1000)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--auth-error-cooldown-seconds", type=int, default=3600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    client = BetfairClient(credentials_from_env())
    while True:
        try:
            count, live_count, priced_count, result = publish_once(client, args.post_url, args.hours_ahead, args.max_results)
            print(f"[betfair] markets={count} inplay_or_capable={live_count} priced_runners={priced_count} envio={result}", flush=True)
            if args.once:
                return 0
            time.sleep(max(15, args.poll_seconds))
        except BetfairAuthError as exc:
            print(
                f"[betfair] erro_auth={exc}. Pausando por {max(300, args.auth_error_cooldown_seconds)}s para evitar excesso de logins.",
                flush=True,
            )
            client = BetfairClient(credentials_from_env())
            if args.once:
                return 1
            time.sleep(max(300, args.auth_error_cooldown_seconds))
        except Exception as exc:
            print(f"[betfair] erro={type(exc).__name__}: {exc}", flush=True)
            if args.once:
                return 1
            time.sleep(max(60, min(300, args.poll_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
