from __future__ import annotations

import re
from typing import Any

from .models import MarketOption


GOAL_WORDS = ("over/under", "total", "goal", "gol", "goals")
CORNER_WORDS = ("corner", "corners", "escanteio", "escanteios")
ASIAN_WORDS = ("asian", "asiatico", "asiatico")
ALLOWED_SELECTIONS = {"over", "under", "home", "away", "yes", "no"}


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_allowed(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in GOAL_WORDS + CORNER_WORDS + ASIAN_WORDS)


def _extract_line(item: dict[str, Any]) -> float | None:
    for key in ("line", "max", "total", "points", "point", "hdp", "handicap"):
        value = _to_float(item.get(key))
        if value is not None:
            return value
    return None


def _clean_selection(key: str) -> str:
    lowered = re.sub(r"[^a-zA-Z]+", "_", key).strip("_").lower()
    if lowered in {"o", "over", "mais"}:
        return "over"
    if lowered in {"u", "under", "menos"}:
        return "under"
    return lowered


def flatten_markets(
    odds_payload: dict[str, Any],
    *,
    fixture_id: int | None,
    min_odd: float,
) -> list[MarketOption]:
    event_id = str(odds_payload.get("id") or odds_payload.get("eventId") or "")
    options: list[MarketOption] = []
    bookmakers = odds_payload.get("bookmakers", {})
    if not isinstance(bookmakers, dict):
        return options

    for bookmaker, markets in bookmakers.items():
        if not isinstance(markets, list):
            continue
        for market in markets:
            market_name = str(market.get("name") or market.get("key") or "")
            if not _market_allowed(market_name):
                continue
            updated_at = market.get("updatedAt")
            odds_rows = market.get("odds") if isinstance(market.get("odds"), list) else [market.get("odds")]
            for row in odds_rows:
                if not isinstance(row, dict):
                    continue
                line = _extract_line(row)
                for key, value in row.items():
                    selection = _clean_selection(str(key))
                    if selection not in ALLOWED_SELECTIONS:
                        continue
                    odd = _to_float(value)
                    if odd is None or odd < min_odd:
                        continue
                    options.append(
                        MarketOption(
                            event_id=event_id,
                            fixture_id=fixture_id,
                            bookmaker=str(bookmaker),
                            market_name=market_name,
                            selection=selection,
                            odd=odd,
                            line=line,
                            updated_at=updated_at,
                            raw=row,
                        )
                    )
    return options
