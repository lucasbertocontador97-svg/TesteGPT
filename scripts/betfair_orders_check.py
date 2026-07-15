from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from betbot.betfair import BetfairClient, credentials_from_env  # noqa: E402


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "betId": row.get("betId"),
        "marketId": row.get("marketId"),
        "selectionId": row.get("selectionId"),
        "handicap": row.get("handicap"),
        "price": row.get("priceMatched") or row.get("priceRequested") or row.get("averagePriceMatched"),
        "size": row.get("sizeSettled") or row.get("sizeMatched") or row.get("sizeRemaining") or row.get("sizeCancelled"),
        "profit": row.get("profit"),
        "side": row.get("side"),
        "status": row.get("status") or row.get("orderStatus"),
        "placedDate": row.get("placedDate"),
        "settledDate": row.get("settledDate"),
        "persistenceType": row.get("persistenceType"),
        "orderType": row.get("orderType"),
    }


def _summary(label: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    rows = payload.get(key) or []
    total_profit = 0.0
    for row in rows:
        try:
            total_profit += float(row.get("profit") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "label": label,
        "count": len(rows),
        "moreAvailable": bool(payload.get("moreAvailable")),
        "totalProfit": round(total_profit, 2),
        "rows": [_safe_order(row) for row in rows[:20] if isinstance(row, dict)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Consulta segura de apostas Betfair sem enviar ordens.")
    parser.add_argument("--hours", type=int, default=24, help="Janela de liquidadas em horas.")
    parser.add_argument("--limit", type=int, default=50, help="Maximo de registros por consulta.")
    parser.add_argument("--bet-id", action="append", default=[], help="Filtrar por betId especifico.")
    parser.add_argument("--market-id", action="append", default=[], help="Filtrar por marketId especifico.")
    parser.add_argument("--json-out", default="", help="Caminho opcional para salvar JSON completo.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    client = BetfairClient(credentials_from_env())
    client.login()

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=max(1, args.hours))
    date_range = {"from": _iso_utc(since), "to": _iso_utc(now)}

    current = client.current_orders(
        bet_ids=args.bet_id or None,
        market_ids=args.market_id or None,
        record_count=max(1, min(1000, args.limit)),
    )
    cleared = client.cleared_orders(
        bet_ids=args.bet_id or None,
        market_ids=args.market_id or None,
        settled_date_range=date_range,
        record_count=max(1, min(1000, args.limit)),
    )

    output = {
        "ok": True,
        "window": date_range,
        "current": _summary("current", current, "currentOrders"),
        "cleared": _summary("cleared", cleared, "clearedOrders"),
    }

    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
