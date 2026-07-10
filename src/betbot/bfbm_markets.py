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


def markets_to_payload(markets: list[BfbmMarket]) -> dict[str, Any]:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "markets": [
            {
                "event_name": item.event_name,
                "market_name": item.market_name,
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
                "status": str(row.get("status") or "").strip(),
                "start_time": str(row.get("start_time") or "").strip(),
                "live_score": str(row.get("live_score") or "").strip(),
                "live_time": str(row.get("live_time") or "").strip(),
                "favorite": str(row.get("favorite") or "").strip(),
                "winner": str(row.get("winner") or "").strip(),
                "total_matched": str(row.get("total_matched") or "").strip(),
                "raw_json": json.dumps(row.get("raw") or {}, ensure_ascii=False),
            }
        )
    return parsed


def market_line(market_name: str) -> float | None:
    match = re.search(r"(\d+(?:[,.]\d+)?)", market_name)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def market_family(market_name: str) -> str:
    normalized = normalize_text(market_name)
    if "escanteio" in normalized or "corner" in normalized:
        return "corners"
    if "gol" in normalized or "goal" in normalized:
        return "goals"
    if "resultado" in normalized or "match odds" in normalized:
        return "match_odds"
    if "placar correto" in normalized or "correct score" in normalized:
        return "correct_score"
    return normalized


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


def _active_market(row: dict[str, Any]) -> bool:
    status = normalize_text(row.get("status", ""))
    return "closed" not in status and "fechado" not in status


def find_bfbm_market(
    catalog_rows: list[dict[str, Any]],
    event_name: str,
    desired_family: str,
    desired_line: float | None = None,
) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    for row in catalog_rows:
        if not _active_market(row):
            continue
        if market_family(str(row.get("market_name") or "")) != desired_family:
            continue
        if desired_line is not None:
            row_line = market_line(str(row.get("market_name") or ""))
            if row_line is None or abs(row_line - desired_line) > 0.01:
                continue
        score = _event_score(event_name, str(row.get("event_name") or ""))
        if score < 55:
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
    for team in teams:
        team_norm = normalize_text(team)
        if selection_norm == team_norm or selection_norm in team_norm or team_norm in selection_norm:
            return team
    lowered = normalize_text(selection)
    if lowered in {"draw", "empate", "x"}:
        return "Empate"
    return selection
