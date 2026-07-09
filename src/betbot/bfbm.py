from __future__ import annotations

import csv
import io
from dataclasses import dataclass
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
    "Price",
    "Size",
    "Points",
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
    return f"{alert.get('home', '')} v {alert.get('away', '')}".strip()


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
    if str(alert.get("league", "")).upper() == "BFBM TESTE SIMPLES":
        event_name = ""
    if not market or not market.get("SelectionName"):
        return None
    price = _num(alert.get("odd")) or 0.0
    price_text = f"{price:.2f}" if price > 0 else ""
    stake_text = f"{config.stake:.2f}"
    return {
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


def tips_csv(alerts: list[dict[str, Any]], config: BfbmConfig) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=BFBM_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for alert in alerts:
        row = alert_to_bfbm_row(alert, config)
        if row:
            writer.writerow(row)
    return buffer.getvalue()
