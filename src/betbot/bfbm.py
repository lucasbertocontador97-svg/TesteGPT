from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


BFBM_COLUMNS = [
    "Provider",
    "Handicap",
    "SelectionId",
    "MarketId",
    "EventId",
    "SelectionName",
    "MarketName",
    "EventName",
    "MarketType",
    "StartTime",
    "BetType",
    "Size",
    "Points",
    "Price",
    "MinPrice",
    "MaxPrice",
    "BSP",
]

BFBM_ACCEPTED_COLUMNS = ["Provider", "SelectionName", "MarketType", "EventName", "BetType", "Size"]
BFBM_RICH_COLUMNS = [
    "Provider",
    "Handicap",
    "SelectionName",
    "MarketName",
    "EventName",
    "MarketType",
    "BetType",
    "Size",
    "Points",
    "Price",
    "MinPrice",
    "MaxPrice",
    "BSP",
]


@dataclass(frozen=True)
class BfbmConfig:
    provider: str
    stake: float
    min_price: float
    max_price: float


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _line_text(line: float) -> str:
    return f"{line:g}"


def _bfbm_line_text(line: float) -> str:
    return _line_text(line).replace(".", ",")


def _event_name(alert: dict[str, Any]) -> str:
    home = str(alert.get("home", "") or "").strip()
    away = str(alert.get("away", "") or "").strip()
    if home and away:
        return f"{home} v {away}"
    return home or away


def _bfbm_event_aliases(event_name: str) -> list[str]:
    aliases: list[str] = []
    if " v " in event_name:
        aliases.append(event_name.replace(" v ", " x "))
    for candidate in list([event_name, *aliases]):
        variants = {candidate}
        if "Nublense" in candidate:
            variants.add(candidate.replace("Nublense", "Ñublense"))
        if "O'Higgins" in candidate:
            variants.add(candidate.replace("O'Higgins", "OHiggins"))
        for variant in list(variants):
            if "Nublense" in variant and "O'Higgins" in variant:
                variants.add(variant.replace("Nublense", "Ñublense"))
                variants.add(variant.replace("O'Higgins", "OHiggins"))
                variants.add(variant.replace("Nublense", "Ñublense").replace("O'Higgins", "OHiggins"))
        aliases.extend(variants)
    seen: set[str] = set()
    return [alias for alias in aliases if alias and alias != event_name and not (alias in seen or seen.add(alias))]


def _bfbm_name_alias(value: str) -> str:
    return value.replace("Nublense", "Ñublense").replace("O'Higgins", "OHiggins")


def _default_start_time() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bfbm_clean_name(value: str) -> str:
    return value.replace("Nublense", "\u00d1ublense").replace("O'Higgins", "OHiggins")


def _bfbm_clean_event_name(event_name: str) -> str:
    return _bfbm_clean_name(event_name.replace(" v ", " x "))


def _goal_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    line = _num(alert.get("line"))
    if line is None:
        return None
    supported = {0.5: "OVER_UNDER_05", 1.5: "OVER_UNDER_15", 2.5: "OVER_UNDER_25", 3.5: "OVER_UNDER_35"}
    market_type = supported.get(line)
    if not market_type:
        return None
    side = "Mais" if str(alert.get("selection", "")).lower() == "over" else "Menos"
    display_line = _bfbm_line_text(line)
    return {
        "MarketType": market_type,
        "MarketName": f"Mais/Menos de {display_line} Gols",
        "SelectionName": f"{side} de {display_line} Gols",
    }


def _corner_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    line = _num(alert.get("line"))
    if line is None:
        return None
    side = "Mais" if str(alert.get("selection", "")).lower() == "over" else "Menos"
    line_label = _line_text(line)
    display_line = _bfbm_line_text(line)
    market_code = str(int(round(line * 10))).zfill(2)
    return {
        "MarketType": f"OVER_UNDER_{market_code}_CORNERS",
        "MarketName": f"Mais/Menos de {display_line} Escanteios",
        "SelectionName": f"{side} de {display_line} Escanteios",
    }


def _match_odds_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    selection = str(alert.get("selection", "") or "").strip()
    if not selection:
        return None
    return {
        "MarketType": "MATCH_ODDS",
        "MarketName": "Resultado da partida",
        "SelectionName": selection,
    }


def _tip_market(alert: dict[str, Any]) -> dict[str, str] | None:
    market = str(alert.get("market", "")).lower()
    if any(word in market for word in ("resultado", "match odds", "vitoria", "vitória")):
        return _match_odds_tip(alert)
    if any(word in market for word in ("goal", "gol")):
        return _goal_tip(alert)
    if any(word in market for word in ("corner", "escanteio")):
        return _corner_tip(alert)
    return None


def alert_to_bfbm_row(alert: dict[str, Any], config: BfbmConfig) -> dict[str, str] | None:
    market = _tip_market(alert)
    event_name = _event_name(alert)
    if str(alert.get("league", "")).upper() == "BFBM TESTE SIMPLES" or str(alert.get("event_id", "")).startswith("bfbm-simple-"):
        event_name = ""
    if not market or not market.get("SelectionName"):
        return None
    price = _num(alert.get("odd")) or 0.0
    price_text = f"{price:.2f}" if price > 0 else ""
    is_match_odds = market.get("MarketType") == "MATCH_ODDS"
    stake_text = "1.00" if is_match_odds else f"{config.stake:.2f}"
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": "",
        "MarketId": "",
        "EventId": "",
        "SelectionName": market["SelectionName"],
        "MarketName": market["MarketName"],
        "EventName": event_name,
        "MarketType": market["MarketType"],
        "StartTime": "",
        "BetType": "BACK",
        "Price": price_text,
        "Size": stake_text,
        "Points": "1",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": "100.00" if is_match_odds else f"{config.max_price:.2f}",
        "BSP": "False",
    }
    return row


def tips_csv(alerts: list[dict[str, Any]], config: BfbmConfig) -> str:
    buffer = io.StringIO(newline="")
    rows = [row for alert in alerts if (row := alert_to_bfbm_row(alert, config))]
    writer = csv.DictWriter(buffer, fieldnames=BFBM_ACCEPTED_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def tips_full_csv(alerts: list[dict[str, Any]], config: BfbmConfig) -> str:
    buffer = io.StringIO(newline="")
    rows = [row for alert in alerts if (row := alert_to_bfbm_row(alert, config))]
    writer = csv.DictWriter(buffer, fieldnames=BFBM_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def tips_rich_csv(alerts: list[dict[str, Any]], config: BfbmConfig) -> str:
    buffer = io.StringIO(newline="")
    rows = [row for alert in alerts if (row := alert_to_bfbm_row(alert, config))]
    if any(row.get("MarketType") == "MATCH_ODDS" for row in rows):
        rows = [row for row in rows if row.get("MarketType") == "MATCH_ODDS"]
    for row in list(rows):
        for alias in _bfbm_event_aliases(row.get("EventName", "")):
            alias_row = row.copy()
            alias_row["EventName"] = alias
            alias_row["SelectionName"] = _bfbm_name_alias(alias_row.get("SelectionName", ""))
            rows.append(alias_row)
    writer = csv.DictWriter(buffer, fieldnames=BFBM_RICH_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in rows:
        if not row.get("Price"):
            row["Price"] = "0"
        writer.writerow(row)
    return buffer.getvalue()


def tips_clean_match_odds_csv(alerts: list[dict[str, Any]], config: BfbmConfig, limit: int = 4) -> str:
    buffer = io.StringIO(newline="")
    rows: list[dict[str, str]] = []
    seen_events: set[str] = set()
    for alert in alerts:
        row = alert_to_bfbm_row(alert, config)
        if not row or row.get("MarketType") != "MATCH_ODDS":
            continue
        row["EventName"] = _bfbm_clean_event_name(row.get("EventName", ""))
        row["SelectionName"] = _bfbm_clean_name(row.get("SelectionName", ""))
        event_key = row["EventName"].casefold()
        if not row["EventName"] or event_key in seen_events:
            continue
        seen_events.add(event_key)
        if not row.get("Price"):
            row["Price"] = "0"
        rows.append(row)
        if len(rows) >= limit:
            break
    writer = csv.DictWriter(buffer, fieldnames=BFBM_RICH_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def debug_minimal_csv(config: BfbmConfig) -> str:
    buffer = io.StringIO(newline="")
    columns = ["Provider", "SelectionName", "MarketType", "BetType", "Size", "MinPrice", "MaxPrice"]
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerow(
        {
            "Provider": config.provider,
            "SelectionName": "Over 2.5 Goals",
            "MarketType": "OVER_UNDER_25",
            "BetType": "BACK",
            "Size": f"{config.stake:.2f}",
            "MinPrice": f"{config.min_price:.2f}",
            "MaxPrice": f"{config.max_price:.2f}",
        }
    )
    return buffer.getvalue()


def debug_event_csv(config: BfbmConfig, event_name: str) -> str:
    alert = {
        "id": "debug-event",
        "event_id": "debug-event",
        "home": event_name,
        "away": "",
        "market": "Mais gols",
        "selection": "over",
        "line": 2.5,
        "odd": 0,
    }
    row = alert_to_bfbm_row(alert, config)
    if row:
        row["EventName"] = event_name
    return tips_csv([alert], config) if not row else _single_row_csv(row)


def debug_lab_csv(config: BfbmConfig, event_name: str, mode: str) -> str:
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": "",
        "MarketId": "",
        "EventId": "",
        "SelectionName": "Over 2.5 Goals",
        "MarketName": "Over/Under 2.5 Goals",
        "EventName": event_name,
        "MarketType": "OVER_UNDER_25",
        "StartTime": "",
        "BetType": "BACK",
        "Price": "0",
        "Size": f"{config.stake:.2f}",
        "Points": "1",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": f"{config.max_price:.2f}",
        "BSP": "False",
    }
    mode_columns = {
        "1": ["Provider", "SelectionName"],
        "2": ["Provider", "SelectionName", "MarketType"],
        "3": ["Provider", "SelectionName", "MarketType", "EventName"],
        "4": ["Provider", "SelectionName", "MarketType", "EventName", "BetType"],
        "5": ["Provider", "SelectionName", "MarketName", "MarketType", "EventName", "BetType", "Size"],
        "6": ["Provider", "SelectionName", "MarketName", "MarketType", "EventName", "BetType", "Size", "MinPrice", "MaxPrice"],
        "7": BFBM_COLUMNS,
        "8": ["Provider", "SelectionName", "MarketType", "EventName", "Size"],
    }
    columns = mode_columns.get(str(mode).strip().lower(), mode_columns["3"])
    return _custom_row_csv(row, columns)


def fresh_test_csv(config: BfbmConfig, suffix: str) -> str:
    row = {
        "Provider": config.provider,
        "SelectionName": "Over 2.5 Goals",
        "MarketType": "OVER_UNDER_25",
        "EventName": f"TesteGPT v {suffix}",
        "BetType": "BACK",
        "Size": f"{config.stake:.2f}",
    }
    return _custom_row_csv(row, BFBM_ACCEPTED_COLUMNS)


def fresh_event_csv(config: BfbmConfig, event_name: str) -> str:
    row = {
        "Provider": config.provider,
        "SelectionName": "Over 2.5 Goals",
        "MarketType": "OVER_UNDER_25",
        "EventName": event_name,
        "BetType": "BACK",
        "Size": f"{config.stake:.2f}",
    }
    return _custom_row_csv(row, BFBM_ACCEPTED_COLUMNS)


def fresh_match_odds_csv(config: BfbmConfig, event_name: str, selection_name: str) -> str:
    row = {
        "Provider": config.provider,
        "SelectionName": selection_name,
        "MarketType": "MATCH_ODDS",
        "EventName": event_name,
        "BetType": "BACK",
        "Size": f"{config.stake:.2f}",
    }
    return _custom_row_csv(row, BFBM_ACCEPTED_COLUMNS)


def fresh_match_odds_full_csv(config: BfbmConfig, event_name: str, selection_name: str) -> str:
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": "0",
        "MarketId": "0",
        "EventId": "0",
        "SelectionName": selection_name,
        "MarketName": "Match Odds",
        "EventName": event_name,
        "MarketType": "MATCH_ODDS",
        "StartTime": _default_start_time(),
        "BetType": "BACK",
        "Price": "0",
        "Size": f"{config.stake:.2f}",
        "Points": "1",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": f"{config.max_price:.2f}",
        "BSP": "False",
    }
    return _custom_row_csv(row, BFBM_COLUMNS)


def fresh_match_odds_ids_csv(
    config: BfbmConfig,
    event_name: str,
    selection_name: str,
    event_id: str,
    market_id: str,
    selection_id: str,
    start_time: str = "",
    price: str = "",
    selection_alias: str = "",
    market_name: str = "",
) -> str:
    csv_selection_name = selection_alias or selection_name
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": selection_id or "0",
        "MarketId": market_id or "0",
        "EventId": event_id or "0",
        "SelectionName": csv_selection_name,
        "MarketName": market_name or "Match Odds",
        "EventName": event_name,
        "MarketType": "MATCH_ODDS",
        "StartTime": start_time or _default_start_time(),
        "BetType": "BACK",
        "Price": price or f"{config.min_price:.2f}",
        "Size": f"{config.stake:.2f}",
        "Points": "1",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": f"{config.max_price:.2f}",
        "BSP": "False",
    }
    return _custom_row_csv(row, BFBM_COLUMNS)


def fresh_match_odds_rich_csv(config: BfbmConfig, event_name: str, selection_name: str) -> str:
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionName": selection_name,
        "MarketName": "Match Odds",
        "EventName": event_name,
        "MarketType": "MATCH_ODDS",
        "BetType": "BACK",
        "Size": f"{config.stake:.2f}",
        "Points": "1",
        "Price": "0",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": f"{config.max_price:.2f}",
        "BSP": "False",
    }
    return _custom_row_csv(row, BFBM_RICH_COLUMNS)


def _single_row_csv(row: dict[str, str]) -> str:
    return _custom_row_csv(row, BFBM_COLUMNS)


def _custom_row_csv(row: dict[str, str], columns: list[str]) -> str:
    buffer = io.StringIO(newline="")
    quoting = csv.QUOTE_ALL if columns in (BFBM_COLUMNS, BFBM_RICH_COLUMNS) else csv.QUOTE_MINIMAL
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", quoting=quoting)
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue()
