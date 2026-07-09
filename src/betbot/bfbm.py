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


def _event_name(alert: dict[str, Any]) -> str:
    home = str(alert.get("home", "") or "").strip()
    away = str(alert.get("away", "") or "").strip()
    if home and away:
        return f"{home} v {away}"
    return home or away


def _default_start_time() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _goal_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    line = _num(alert.get("line"))
    if line is None:
        return None
    supported = {0.5: "OVER_UNDER_05", 1.5: "OVER_UNDER_15", 2.5: "OVER_UNDER_25", 3.5: "OVER_UNDER_35"}
    market_type = supported.get(line)
    if not market_type:
        return None
    side = "Over" if str(alert.get("selection", "")).lower() == "over" else "Under"
    line_label = _line_text(line)
    return {
        "MarketType": market_type,
        "MarketName": f"Over/Under {line_label} Goals",
        "SelectionName": f"{side} {line_label} Goals",
    }


def _corner_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    line = _num(alert.get("line"))
    if line is None:
        return None
    side = "Over" if str(alert.get("selection", "")).lower() == "over" else "Under"
    line_label = _line_text(line)
    market_code = str(int(round(line * 10))).zfill(2)
    return {
        "MarketType": f"OVER_UNDER_{market_code}_CORNERS",
        "MarketName": f"Over/Under {line_label} Corners",
        "SelectionName": f"{side} {line_label} Corners",
    }


def _tip_market(alert: dict[str, Any]) -> dict[str, str] | None:
    market = str(alert.get("market", "")).lower()
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
    stake_text = f"{config.stake:.2f}"
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
        "MaxPrice": f"{config.max_price:.2f}",
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
    writer = csv.DictWriter(buffer, fieldnames=BFBM_RICH_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in rows:
        if not row.get("Price"):
            row["Price"] = "0"
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
) -> str:
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": selection_id or "0",
        "MarketId": market_id or "0",
        "EventId": event_id or "0",
        "SelectionName": selection_name,
        "MarketName": "Match Odds",
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
