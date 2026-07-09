from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .clients import BetfairClient
from .matching import similarity
from .models import Decision, GameSnapshot


@dataclass(frozen=True)
class BetfairIds:
    market_id: str
    selection_id: str
    event_id: str | None
    start_time: str | None


def _market_type(market: str, line: float | None) -> str | None:
    lowered = str(market or "").lower()
    if line is None:
        return None
    code = str(int(round(float(line) * 10))).zfill(2)
    if any(word in lowered for word in ("goal", "gol")):
        return f"OVER_UNDER_{code}"
    if any(word in lowered for word in ("corner", "escanteio")):
        return f"OVER_UNDER_{code}_CORNERS"
    return None


def _selection_name(decision: Decision) -> str | None:
    if decision.line is None:
        return None
    side = "Over" if decision.selection.lower() == "over" else "Under"
    lowered = decision.market.lower()
    suffix = "Corners" if any(word in lowered for word in ("corner", "escanteio")) else "Goals"
    return f"{side} {decision.line:g} {suffix}"


def _event_score(game: GameSnapshot, event: dict[str, Any]) -> float:
    name = str(event.get("name") or "")
    if not name:
        return 0.0
    direct = similarity(name, f"{game.home} v {game.away}")
    swapped = similarity(name, f"{game.away} v {game.home}")
    team_avg = max(
        (similarity(game.home, name) + similarity(game.away, name)) / 2,
        (similarity(game.away, name) + similarity(game.home, name)) / 2,
    )
    return max(direct, swapped, team_avg)


async def resolve_betfair_ids(client: BetfairClient, game: GameSnapshot, decision: Decision) -> BetfairIds | None:
    market_type = _market_type(decision.market, decision.line)
    selection_name = _selection_name(decision)
    if not market_type or not selection_name:
        return None

    queries = [game.home, game.away, None]
    seen_market_ids: set[str] = set()
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for query in queries:
        markets = await client.list_market_catalogue(market_type_code=market_type, text_query=query)
        for market in markets:
            market_id = str(market.get("marketId") or "")
            if not market_id or market_id in seen_market_ids:
                continue
            seen_market_ids.add(market_id)
            event = market.get("event", {}) if isinstance(market.get("event"), dict) else {}
            score = _event_score(game, event)
            if score > best[0]:
                best = (score, market)

    if best[0] < 0.62 or not best[1]:
        return None

    market = best[1]
    for runner in market.get("runners", []) or []:
        runner_name = str(runner.get("runnerName") or "")
        if runner_name.lower() == selection_name.lower():
            event = market.get("event", {}) if isinstance(market.get("event"), dict) else {}
            return BetfairIds(
                market_id=str(market.get("marketId") or ""),
                selection_id=str(runner.get("selectionId") or ""),
                event_id=str(event.get("id") or "") or None,
                start_time=str(market.get("marketStartTime") or "") or None,
            )
    return None
