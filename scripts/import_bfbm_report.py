from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from betbot.storage import Storage  # noqa: E402


MONEY_CHARS = str.maketrans({"R": "", "$": "", " ": "", "\u00a0": ""})


def parse_money(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("-")
    text = text.replace("-", "").translate(MONEY_CHARS).strip()
    if not text:
        return None
    text = text.replace(".", "").replace(",", ".")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return -parsed if negative else parsed


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(".", "").replace(",", ".") if "," in text else text
    try:
        return float(text)
    except ValueError:
        return None


def parse_dt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return text


def sniff_csv(path: Path) -> tuple[str, csv.Dialect]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            sample = path.read_text(encoding=encoding)[:4096]
        except UnicodeDecodeError:
            continue
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t,")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";"
        return encoding, dialect
    raise RuntimeError(f"Nao foi possivel ler o CSV: {path}")


def get(row: dict[str, str], *names: str) -> str:
    normalized = {k.strip().lower(): v for k, v in row.items() if k is not None}
    for name in names:
        value = normalized.get(name.strip().lower())
        if value is not None:
            return value
    return ""


def stable_bet_id(row: dict[str, str]) -> str:
    raw = "|".join(f"{k}={v}" for k, v in sorted(row.items()))
    return "csv-" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class ImportSummary:
    rows: int = 0
    settled: int = 0
    profit: float = 0.0
    stake: float = 0.0
    greens: int = 0
    reds: int = 0
    voids: int = 0


def row_to_order(row: dict[str, str]) -> dict[str, Any]:
    raw_line = get(row, "Descricao", "Descrição", "Evento/mercado/seleção", "Evento/mercado/selecao")
    bet_id = get(row, "Bet Id", "ID da aposta", "Id da aposta") or stable_bet_id(row)
    size = parse_money(get(row, "Valor correspondido", "Correspondido", "Stake"))
    price = parse_float(get(row, "Preco medio correspondido", "Preço médio correspondido", "Preco medio", "Preço m"))
    profit = parse_money(get(row, "L/P", "Lucro", "Profit"))
    status = get(row, "Status") or ("SETTLED" if profit is not None else "")
    placed = parse_dt(get(row, "Data da aposta", "Aposta realizada", "Placed Date"))
    matched = parse_dt(get(row, "Data correspondida", "Data correspondida", "Matched Date"))
    settled = parse_dt(get(row, "Data liquidada", "Settled Date"))
    market_id = get(row, "MarketId", "Market Id", "ID do mercado", "ID do m")
    selection_id = get(row, "SelectionId", "Selection Id", "ID da seleção", "ID da selecao")
    event_id = get(row, "EventId", "Event Id", "ID do Evento")
    strategy = get(row, "Estrategia", "Estratégia")
    side = get(row, "Tipo de aposta", "Tipo") or "BACK"

    return {
        "betId": bet_id,
        "marketId": market_id,
        "selectionId": selection_id,
        "eventId": event_id,
        "side": side,
        "price": price,
        "size": size,
        "profit": profit,
        "status": status,
        "placedDate": placed,
        "matchedDate": matched,
        "settledDate": settled,
        "strategy": strategy,
        "rawLine": raw_line,
        "selectionName": get(row, "Seleção", "Selecao", "Selection"),
        "tipster": get(row, "Tipster", "Tipster/fornecedor"),
        "shortDescription": get(row, "Descrição curta", "Descricao curta"),
    }


def load_orders(path: Path) -> tuple[list[dict[str, Any]], ImportSummary]:
    encoding, dialect = sniff_csv(path)
    orders: list[dict[str, Any]] = []
    summary = ImportSummary()
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle, dialect=dialect)
        for row in reader:
            if not row:
                continue
            order = row_to_order(row)
            if not order["rawLine"] and not order["betId"]:
                continue
            orders.append(order)
            summary.rows += 1
            if str(order.get("status") or "").upper() == "SETTLED" or order.get("profit") is not None:
                summary.settled += 1
            profit = order.get("profit")
            if profit is not None:
                summary.profit += float(profit)
                if profit > 0:
                    summary.greens += 1
                elif profit < 0:
                    summary.reds += 1
                else:
                    summary.voids += 1
            size = order.get("size")
            if size is not None:
                summary.stake += float(size)
    return orders, summary


def sync_remote(url: str, token: str, orders: list[dict[str, Any]]) -> dict[str, Any]:
    payload = json.dumps({"cleared": {"count": len(orders), "rows": orders}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Sync-Token": token,
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def import_local(db_path: Path, orders: list[dict[str, Any]]) -> dict[str, Any]:
    storage = Storage(db_path)
    imported = 0
    matched_alerts = 0
    for order in orders:
        alert_id = None
        market_id = str(order.get("marketId") or "")
        selection_id = str(order.get("selectionId") or "")
        if market_id and selection_id:
            alert = storage.find_alert_by_bfbm_order(market_id, selection_id)
            if alert:
                alert_id = int(alert["id"])
                matched_alerts += 1
        storage.record_bfbm_bet_notification(
            {
                "placed_at": str(order.get("placedDate") or order.get("matchedDate") or ""),
                "placed_at_iso": str(order.get("placedDate") or order.get("matchedDate") or ""),
                "bet_id": str(order.get("betId") or ""),
                "size_matched": order.get("size"),
                "success": True,
                "strategy": str(order.get("strategy") or ""),
                "sid": "csv-import",
                "line": str(order.get("rawLine") or ""),
                "alert_id": alert_id,
                "market_id": market_id,
                "selection_id": selection_id,
                "handicap": str(order.get("handicap") or ""),
                "side": str(order.get("side") or ""),
                "order_status": str(order.get("status") or ""),
                "price": order.get("price"),
                "profit": order.get("profit"),
                "settled_at": str(order.get("settledDate") or ""),
                "raw": order,
            }
        )
        imported += 1
    return {"ok": True, "imported": imported, "matched_alerts": matched_alerts, "db": str(db_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa relatorio CSV do BFBM para auditoria do TesteGPT.")
    parser.add_argument("--csv", required=True, help="Caminho do relatorio CSV exportado pelo BFBM.")
    parser.add_argument("--db", default=str(ROOT / "bot.sqlite3"), help="Banco SQLite local para importacao.")
    parser.add_argument("--sync-url", default="", help="Opcional: URL /api/bfbm/sync-orders da producao.")
    parser.add_argument("--sync-token", default="", help="Token X-Sync-Token para --sync-url.")
    parser.add_argument("--dry-run", action="store_true", help="Apenas mostra resumo, sem gravar/enviar.")
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"CSV nao encontrado: {path}")

    orders, summary = load_orders(path)
    roi = (summary.profit / summary.stake * 100.0) if summary.stake else 0.0
    print(
        "Resumo CSV: "
        f"linhas={summary.rows} liquidadas={summary.settled} "
        f"greens={summary.greens} reds={summary.reds} voids={summary.voids} "
        f"stake=R${summary.stake:.2f} lucro=R${summary.profit:.2f} ROI={roi:.2f}%"
    )

    if args.dry_run:
        print("Dry-run: nada gravado.")
        return 0

    if args.sync_url:
        if not args.sync_token:
            raise SystemExit("--sync-token e obrigatorio quando --sync-url e usado")
        result = sync_remote(args.sync_url, args.sync_token, orders)
    else:
        result = import_local(Path(args.db), orders)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
