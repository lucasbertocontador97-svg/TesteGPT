from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class BfbmMarket:
    event_name: str
    market_name: str
    event_id: str = ""
    market_id: str = ""
    market_type: str = ""
    status: str = ""
    start_time: str = ""
    live_score: str = ""
    live_time: str = ""
    favorite: str = ""
    winner: str = ""
    total_matched: str = ""
    raw: dict[str, Any] | None = None


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", "").replace("`", "")
    text = re.sub(r"\bfc\b", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_event(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"\s+(v|vs|x)\s+", " x ", text)
    return text


def _split_event_market(value: str) -> tuple[str, str]:
    if "\\" not in value:
        return value.strip(), ""
    event, market = value.split("\\", 1)
    return event.strip(), market.strip()


def _row_value(row: dict[str, Any], *names: str) -> str:
    lowered = {str(key).strip().casefold(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.casefold())
        if value is not None:
            return str(value).strip()
    return ""


def parse_exported_markets_csv(text: str) -> list[BfbmMarket]:
    if text.startswith("\ufeff"):
        text = text[1:]
    sample = text[:4096]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    markets: list[BfbmMarket] = []
    seen: set[tuple[str, str]] = set()
    for row in reader:
        event_market = _row_value(row, "Evento/mercado", "Event/market", "Market")
        event_name, market_name = _split_event_market(event_market)
        if not event_name or not market_name:
            continue
        key = (normalize_event(event_name), normalize_text(market_name))
        if key in seen:
            continue
        seen.add(key)
        markets.append(
            BfbmMarket(
                event_name=event_name,
                market_name=market_name,
                event_id=_row_value(row, "EventId", "ID do Evento"),
                market_id=_row_value(row, "MarketId", "ID do mercado"),
                market_type=_row_value(row, "MarketType", "Tipo de mercado"),
                status=_row_value(row, "Status"),
                start_time=_row_value(row, "Hora de início", "Start time"),
                live_score=_row_value(row, "Placar ao vivo", "Live score"),
                live_time=_row_value(row, "Tempo", "Time"),
                favorite=_row_value(row, "1º favorito", "1st favourite", "1st favorite"),
                winner=_row_value(row, "Vencedor(es)", "Winner(s)"),
                total_matched=_row_value(row, "Total correspondido", "Total matched"),
                raw={str(k): v for k, v in row.items()},
            )
        )
    return markets


def parse_exported_market_catalog_csv(text: str) -> list[BfbmMarket]:
    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    markets: list[BfbmMarket] = []
    seen: set[tuple[str, str, str]] = set()
    for row in reader:
        event_name = _row_value(row, "EventName")
        market_name = _row_value(row, "MarketName")
        market_id = _row_value(row, "MarketId")
        if not event_name or not market_name or not market_id:
            continue
        key = (normalize_event(event_name), normalize_text(market_name), market_id)
        if key in seen:
            continue
        seen.add(key)
        markets.append(
            BfbmMarket(
                event_name=event_name,
                market_name=market_name,
                event_id=_row_value(row, "EventId"),
                market_id=market_id,
                market_type=_row_value(row, "MarketType"),
                start_time=_row_value(row, "StartTime"),
                total_matched=_row_value(row, "TotalMatched"),
                raw={str(k): v for k, v in row.items()},
            )
        )
    return markets


def merge_market_catalog(base: list[BfbmMarket], catalog: list[BfbmMarket]) -> list[BfbmMarket]:
    if not catalog:
        return base
    catalog_by_key: dict[tuple[str, str], BfbmMarket] = {}
    for item in catalog:
        catalog_by_key[(normalize_event(item.event_name), normalize_text(item.market_name))] = item
    merged: list[BfbmMarket] = []
    used: set[tuple[str, str]] = set()
    for item in base:
        key = (normalize_event(item.event_name), normalize_text(item.market_name))
        extra = catalog_by_key.get(key)
        used.add(key)
        if not extra:
            merged.append(item)
            continue
        raw = dict(extra.raw or {})
        raw.update(item.raw or {})
        merged.append(
            BfbmMarket(
                event_name=extra.event_name or item.event_name,
                market_name=extra.market_name or item.market_name,
                event_id=extra.event_id or item.event_id,
                market_id=extra.market_id or item.market_id,
                market_type=extra.market_type or item.market_type,
                status=item.status,
                start_time=extra.start_time or item.start_time,
                live_score=item.live_score,
                live_time=item.live_time,
                favorite=item.favorite,
                winner=item.winner,
                total_matched=extra.total_matched or item.total_matched,
                raw=raw,
            )
        )
    for key, item in catalog_by_key.items():
        if key not in used:
            merged.append(item)
    return merged


def markets_to_payload(
    markets: list[BfbmMarket],
    *,
    source_path: str = "",
    source_modified_at: str = "",
    source_age_seconds: float | None = None,
) -> dict[str, Any]:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_path": source_path,
        "source_modified_at": source_modified_at,
        "source_age_seconds": source_age_seconds,
        "markets": [
            {
                "event_name": item.event_name,
                "market_name": item.market_name,
                "event_id": item.event_id,
                "market_id": item.market_id,
                "market_type": item.market_type,
                "status": item.status,
                "start_time": item.start_time,
                "live_score": item.live_score,
                "live_time": item.live_time,
                "favorite": item.favorite,
                "winner": item.winner,
                "total_matched": item.total_matched,
                "raw": item.raw or {},
            }
            for item in markets
        ],
    }


def payload_to_markets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("markets")
    if not isinstance(rows, list):
        return []
    source_path = str(payload.get("source_path") or "")
    source_modified_at = str(payload.get("source_modified_at") or "")
    try:
        source_age_seconds = float(payload.get("source_age_seconds"))
    except (TypeError, ValueError):
        source_age_seconds = None
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_name = str(row.get("event_name") or "").strip()
        market_name = str(row.get("market_name") or "").strip()
        if not event_name or not market_name:
            continue
        parsed.append(
            {
                "event_name": event_name,
                "market_name": market_name,
                "event_id": str(row.get("event_id") or "").strip(),
                "market_id": str(row.get("market_id") or "").strip(),
                "market_type": str(row.get("market_type") or "").strip(),
                "status": str(row.get("status") or "").strip(),
                "start_time": str(row.get("start_time") or "").strip(),
                "live_score": str(row.get("live_score") or "").strip(),
                "live_time": str(row.get("live_time") or "").strip(),
                "favorite": str(row.get("favorite") or "").strip(),
                "winner": str(row.get("winner") or "").strip(),
                "total_matched": str(row.get("total_matched") or "").strip(),
                "raw_json": json.dumps(row.get("raw") or {}, ensure_ascii=False),
                "source_path": source_path,
                "source_modified_at": source_modified_at,
                "source_age_seconds": source_age_seconds,
            }
        )
    return parsed


def _string_or_empty(value: Any) -> str:
    return str(value or "").strip()


def _floatish_or_empty(value: Any) -> str:
    if value is None or value == "":
        return ""
    return str(value).strip()


def betfair_ingest_payload_to_markets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    source = _string_or_empty(payload.get("source")) or "betfair_ingest"
    captured_at = _string_or_empty(payload.get("captured_at")) or datetime.now(timezone.utc).isoformat()
    parsed: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = _string_or_empty(event.get("event_id") or event.get("eventId") or event.get("id"))
        event_name = _string_or_empty(event.get("event_name") or event.get("eventName") or event.get("name"))
        start_time = _string_or_empty(event.get("start_time") or event.get("startTime") or event.get("openDate"))
        live_score = _string_or_empty(event.get("live_score") or event.get("score"))
        live_time = _string_or_empty(event.get("live_time") or event.get("time"))
        markets = event.get("markets")
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            market_id = _string_or_empty(market.get("market_id") or market.get("marketId"))
            market_type = _string_or_empty(market.get("market_type") or market.get("marketType"))
            market_name = _string_or_empty(market.get("market_name") or market.get("marketName"))
            if not event_name or not market_name or not market_id:
                continue
            runners_raw = market.get("runners")
            runners: list[dict[str, Any]] = []
            if isinstance(runners_raw, list):
                for runner in runners_raw:
                    if not isinstance(runner, dict):
                        continue
                    runners.append(
                        {
                            "selectionId": _string_or_empty(
                                runner.get("selection_id") or runner.get("selectionId")
                            ),
                            "runnerName": _string_or_empty(
                                runner.get("runner_name") or runner.get("runnerName")
                            ),
                            "handicap": runner.get("handicap"),
                            "status": _string_or_empty(runner.get("status")),
                            "bestBackPrice": runner.get("best_back_price", runner.get("back")),
                            "bestBackSize": runner.get("best_back_size", runner.get("back_size")),
                            "bestLayPrice": runner.get("best_lay_price", runner.get("lay")),
                            "bestLaySize": runner.get("best_lay_size", runner.get("lay_size")),
                            "lastPriceTraded": runner.get("last_price_traded", runner.get("lastPriceTraded")),
                        }
                    )
            raw = {
                "source": source,
                "captured_at": captured_at,
                "event": {
                    "id": event_id,
                    "name": event_name,
                    "competition": _string_or_empty(event.get("competition")),
                    "countryCode": _string_or_empty(event.get("country_code") or event.get("countryCode")),
                    "startTime": start_time,
                    "inplay": bool(event.get("inplay")),
                    "liveScore": live_score,
                    "liveTime": live_time,
                },
                "description": {"marketType": market_type},
                "book": {
                    "status": _string_or_empty(market.get("status")),
                    "inplay": bool(market.get("inplay")),
                    "totalMatched": market.get("total_matched", market.get("totalMatched")),
                },
                "runners": runners,
            }
            parsed.append(
                {
                    "event_name": event_name,
                    "market_name": market_name,
                    "event_id": event_id,
                    "market_id": market_id,
                    "market_type": market_type,
                    "status": _string_or_empty(market.get("status")),
                    "start_time": start_time,
                    "live_score": live_score,
                    "live_time": live_time,
                    "favorite": "",
                    "winner": "",
                    "total_matched": _floatish_or_empty(market.get("total_matched", market.get("totalMatched"))),
                    "raw_json": json.dumps(raw, ensure_ascii=False),
                    "source_path": source,
                    "source_modified_at": captured_at,
                    "source_age_seconds": 0.0,
                }
            )
    return parsed


def market_line(market_name: str) -> float | None:
    normalized = normalize_text(market_name).upper()
    type_match = re.search(r"(?:OVER_UNDER|FIRST_HALF_GOALS)_(\d{2,3})", normalized)
    if type_match:
        try:
            return float(type_match.group(1)) / 10
        except ValueError:
            return None
    match = re.search(r"(\d+(?:[,.]\d+)?)", market_name)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _raw_market(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw")
    if isinstance(raw, dict):
        return raw
    raw_json = row.get("raw_json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def market_runners(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _raw_market(row)
    runners = raw.get("runners") or row.get("runners") or []
    return [runner for runner in runners if isinstance(runner, dict)]


def _runner_line(runner: dict[str, Any]) -> float | None:
    handicap = _num(runner.get("handicap"))
    if handicap is not None and abs(handicap) > 0.001:
        return handicap
    runner_name = str(runner.get("runnerName") or runner.get("runner_name") or runner.get("name") or "")
    return market_line(runner_name)


def market_lines(row: dict[str, Any]) -> set[float]:
    lines: set[float] = set()
    for value in (
        row.get("market_name"),
        row.get("MarketName"),
        row.get("market_type"),
        row.get("MarketType"),
    ):
        line = market_line(str(value or ""))
        if line is not None:
            lines.add(round(float(line), 2))
    for runner in market_runners(row):
        line = _runner_line(runner)
        if line is not None:
            lines.add(round(float(line), 2))
    return lines


def market_family(market_name: str) -> str:
    normalized = normalize_text(market_name)
    compact = normalized.upper()
    if re.search(r"OVER_UNDER_\d{2,3}_CORNR", compact):
        return "corners"
    if re.search(r"FIRST_HALF_GOALS_\d{2,3}", compact):
        return "first_half_goals"
    if re.search(r"OVER_UNDER_\d{2,3}", compact) or compact == "ALT_TOTAL_GOALS":
        return "goals"
    if compact == "MATCH_ODDS":
        return "match_odds"
    if compact == "BOTH_TEAMS_TO_SCORE":
        return "btts"
    if compact == "DOUBLE_CHANCE":
        return "double_chance"
    if compact == "DRAW_NO_BET":
        return "draw_no_bet"
    if compact == "ASIAN_HANDICAP":
        return "asian_handicap"
    if compact in {"CORNER_ODDS", "COMBINED_TOTAL"}:
        return "corners"
    if "escanteio" in normalized or "corner" in normalized:
        return "corners"
    if (
        ("primeiro tempo" in normalized or "first half" in normalized or "intervalo" in normalized)
        and ("gol" in normalized or "goal" in normalized)
    ):
        return "first_half_goals"
    if "gol" in normalized or "goal" in normalized:
        return "goals"
    if "ambos os times marcam" in normalized or "both teams" in normalized:
        return "btts"
    if "chance dupla" in normalized or "double chance" in normalized:
        return "double_chance"
    if "empate anula" in normalized or "draw no bet" in normalized:
        return "draw_no_bet"
    if "handicap asiatico" in normalized or "asian handicap" in normalized:
        return "asian_handicap"
    if "cartao" in normalized or "booking" in normalized:
        return "cards"
    if "resultado" in normalized or "match odds" in normalized:
        return "match_odds"
    if "placar correto" in normalized or "correct score" in normalized:
        return "correct_score"
    return normalized


KNOWN_MARKET_FAMILIES = {
    "asian_handicap",
    "btts",
    "cards",
    "corners",
    "correct_score",
    "double_chance",
    "draw_no_bet",
    "first_half_goals",
    "goals",
    "match_odds",
}


def row_market_family(row: dict[str, Any]) -> str:
    type_family = market_family(str(row.get("market_type") or row.get("MarketType") or ""))
    if type_family in KNOWN_MARKET_FAMILIES:
        return type_family
    return market_family(str(row.get("market_name") or row.get("MarketName") or ""))


def _event_score(left: str, right: str) -> int:
    left_n = normalize_event(left)
    right_n = normalize_event(right)
    left_tokens = set(left_n.split())
    right_tokens = set(right_n.split())
    if left_n == right_n:
        return 100
    if left_n in right_n or right_n in left_n:
        return 85
    overlap = len(left_tokens & right_tokens)
    return int(overlap * 100 / max(1, min(len(left_tokens), len(right_tokens))))


def event_score_for_row(event_name: str, row: dict[str, Any]) -> int:
    score = _event_score(event_name, str(row.get("event_name") or ""))
    alias = str(row.get("alias_event_name") or "").strip()
    if alias:
        score = max(score, _event_score(event_name, alias))
    return score


def _active_market(row: dict[str, Any]) -> bool:
    status = normalize_text(row.get("status") or row.get("Status") or "")
    return "closed" not in status and "fechado" not in status


def find_bfbm_market(
    catalog_rows: list[dict[str, Any]],
    event_name: str,
    desired_family: str,
    desired_line: float | None = None,
) -> dict[str, Any] | None:
    best: tuple[int, int, float, dict[str, Any]] | None = None
    for row in catalog_rows:
        if not _active_market(row):
            continue
        row_family = row_market_family(row)
        if row_family != desired_family:
            continue
        line_priority = 0
        matched_line_delta = 99.0
        if desired_line is not None:
            row_lines = market_lines(row)
            market_type = normalize_text(row.get("market_type") or row.get("MarketType") or "")
            market_name = normalize_text(row.get("market_name") or row.get("MarketName") or "")
            generic_line_market = (
                desired_family == "goals"
                and ("alt_total_goals" in market_type or "linhas de gol" in market_name)
            ) or (
                desired_family == "corners"
                and ("corner" in market_type or "corners total" in market_name or "escanteio" in market_name)
            )
            if row_lines:
                matched_line_delta = min(abs(row_line - desired_line) for row_line in row_lines)
                if matched_line_delta <= 0.01:
                    line_priority = 2
                else:
                    continue
            elif generic_line_market:
                line_priority = 1
            else:
                continue
        score = event_score_for_row(event_name, row)
        if score < 55:
            continue
        candidate = (score, line_priority, -matched_line_delta, row)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    return best[3] if best else None


def find_bfbm_event_family_market(
    catalog_rows: list[dict[str, Any]],
    event_name: str,
    desired_family: str,
    min_score: int = 75,
) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    for row in catalog_rows:
        if not _active_market(row):
            continue
        row_family = row_market_family(row)
        if row_family != desired_family:
            continue
        score = event_score_for_row(event_name, row)
        if score < min_score:
            continue
        if best is None or score > best[0]:
            best = (score, row)
    return best[1] if best else None


def split_event_teams(event_name: str) -> tuple[str, str] | None:
    match = re.split(r"\s+(?:x|v|vs)\s+", event_name, maxsplit=1, flags=re.IGNORECASE)
    if len(match) != 2:
        return None
    return match[0].strip(), match[1].strip()


def map_selection_to_event(selection: str, bfbm_event_name: str) -> str:
    teams = split_event_teams(bfbm_event_name)
    if not teams:
        return selection
    selection_norm = normalize_text(selection)
    if selection_norm in {"home", "mandante", "casa", "1"}:
        return teams[0]
    if selection_norm in {"away", "visitante", "fora", "2"}:
        return teams[1]
    for team in teams:
        team_norm = normalize_text(team)
        if selection_norm == team_norm or selection_norm in team_norm or team_norm in selection_norm:
            return team
    lowered = normalize_text(selection)
    if lowered in {"draw", "empate", "x"}:
        return "Empate"
    return selection
