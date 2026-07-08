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


def _extract_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("link", "url", "deeplink", "betslip")):
                found = _extract_url(child)
                if found:
                    return found
        for child in value.values():
            found = _extract_url(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _extract_url(child)
            if found:
                return found
    return None


def _extract_odd_and_link(value: Any) -> tuple[float | None, str | None]:
    if isinstance(value, dict):
        odd = None
        for key in ("odd", "odds", "price", "decimal", "value"):
            odd = _to_float(value.get(key))
            if odd is not None:
                break
        return odd, _extract_url(value)
    return _to_float(value), None


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
                    odd, value_link = _extract_odd_and_link(value)
                    if odd is None or odd < min_odd:
                        continue
                    link_url = value_link or _extract_url(row) or _extract_url(market)
                    options.append(
                        MarketOption(
                            event_id=event_id,
                            fixture_id=fixture_id,
                            bookmaker=str(bookmaker),
                            market_name=market_name,
                            selection=selection,
                            odd=odd,
                            line=line,
                            link_url=link_url,
                            updated_at=updated_at,
                            raw=row,
                        )
                    )
    return options


def flatten_all_markets(
    odds_payload: dict[str, Any],
    *,
    fixture_id: int | None,
    min_odd: float = 1.01,
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
                    odd, value_link = _extract_odd_and_link(value)
                    if odd is None or odd < min_odd:
                        continue
                    link_url = value_link or _extract_url(row) or _extract_url(market)
                    options.append(
                        MarketOption(
                            event_id=event_id,
                            fixture_id=fixture_id,
                            bookmaker=str(bookmaker),
                            market_name=market_name,
                            selection=selection,
                            odd=odd,
                            line=line,
                            link_url=link_url,
                            updated_at=updated_at,
                            raw=row,
                        )
                    )
    return options


def market_matches_idea(option: MarketOption, market_family: str, selection: str, line: float | None = None) -> bool:
    name = option.market_name.lower()
    if option.selection != selection:
        return False
    if line is not None and option.line is not None and abs(option.line - line) > 0.25:
        return False
    if market_family == "goals":
        return any(word in name for word in GOAL_WORDS)
    if market_family == "corners":
        return any(word in name for word in CORNER_WORDS)
    return False
