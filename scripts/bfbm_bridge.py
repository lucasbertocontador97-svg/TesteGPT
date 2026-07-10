from __future__ import annotations

import argparse
import csv
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


RICH_COLUMNS = [
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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _read_url(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "TesteGPT-BFBM-Bridge/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8-sig")


def _post_url(url: str, params: dict[str, str], timeout: int = 15) -> None:
    separator = "&" if "?" in url else "?"
    full_url = url + separator + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers={"User-Agent": "TesteGPT-BFBM-Bridge/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        response.read()


def _csv_text(rows: list[dict[str, str]]) -> str:
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=RICH_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in rows:
        normalized = {column: str(row.get(column, "") or "") for column in RICH_COLUMNS}
        writer.writerow(normalized)
    return buffer.getvalue()


def _float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", "."))
    except ValueError:
        return default


def _normalize_name(value: str) -> str:
    aliases = {
        "Nublense": "\u00d1ublense",
        "O'Higgins": "OHiggins",
        "Nacional Potosi": "Nacional Potos\u00ed",
        "Club Aurora": "Aurora",
        "America de Cali": "Am\u00e9rica de Cali",
        "Shandong Luneng": "Shandong Taishan",
        "Shandong Luneng Taishan": "Shandong Taishan",
    }
    cleaned = str(value or "").strip()
    for source, target in aliases.items():
        cleaned = cleaned.replace(source, target)
    return cleaned


def _normalize_event_name(value: str) -> str:
    return _normalize_name(value).replace(" v ", " x ")


def _normalize_corner_market(row: dict[str, Any], selection: str, market_name: str, market_type: str) -> tuple[str, str, str]:
    combined = " ".join(
        [
            selection,
            market_name,
            market_type,
            str(row.get("line") or row.get("Line") or ""),
        ]
    )
    if "Escanteio" not in combined and "Corner" not in combined and not market_type.endswith("_CORNERS"):
        return selection, market_name, market_type
    line_match = re.search(r"(\d+(?:[,.]\d+)?)", combined)
    if not line_match:
        return selection, market_name, market_type.replace("_CORNERS", "")
    line = line_match.group(1).replace(",", ".")
    try:
        line_value = float(line)
    except ValueError:
        return selection, market_name, market_type.replace("_CORNERS", "")
    code = str(int(round(line_value * 10))).zfill(2)
    selection_source = selection or combined
    if re.search(r"\b(Menos|Under)\b", selection_source, re.IGNORECASE):
        side = "Under"
    elif re.search(r"\b(Mais|Over)\b", selection_source, re.IGNORECASE):
        side = "Over"
    else:
        side = "Under" if re.search(r"\b(Menos|Under)\b", combined, re.IGNORECASE) else "Over"
    label = f"{line_value:g}"
    return f"{side} {label} Corners", f"Over/Under {label} Corners", f"OVER_UNDER_{code}"


def _normalize_row(row: dict[str, Any], *, min_price: float, max_price: float) -> dict[str, str] | None:
    event = _normalize_event_name(str(row.get("EventName") or row.get("event") or ""))
    selection = _normalize_name(str(row.get("SelectionName") or row.get("selection") or ""))
    market_type = str(row.get("MarketType") or row.get("market_type") or "").strip()
    if not event or not selection or not market_type:
        return None
    if str(row.get("BetType") or "BACK").upper() not in {"BACK", "LAY"}:
        return None
    row_min = max(min_price, _float(str(row.get("MinPrice") or row.get("min_price") or min_price), min_price))
    row_max = _float(str(row.get("MaxPrice") or row.get("max_price") or max_price), max_price)
    if row_max < row_min:
        row_max = max_price
    market_name = str(row.get("MarketName") or row.get("market_name") or "Resultado da partida").strip()
    selection, market_name, market_type = _normalize_corner_market(row, selection, market_name, market_type)
    return {
        "Provider": str(row.get("Provider") or row.get("provider") or "TesteGPT").strip(),
        "Handicap": str(row.get("Handicap") or row.get("handicap") or "0").strip(),
        "SelectionName": selection,
        "MarketName": market_name,
        "EventName": event,
        "MarketType": market_type,
        "BetType": str(row.get("BetType") or row.get("bet_type") or "BACK").upper(),
        "Size": str(row.get("Size") or row.get("stake") or row.get("size") or "1.00").strip(),
        "Points": str(row.get("Points") or row.get("points") or "1").strip(),
        "Price": str(row.get("Price") or row.get("price") or "0").strip(),
        "MinPrice": f"{row_min:.2f}",
        "MaxPrice": f"{row_max:.2f}",
        "BSP": str(row.get("BSP") or row.get("bsp") or "False").strip(),
    }


class BridgeState:
    def __init__(self, state_path: Path, *, min_price: float, max_price: float, max_tips: int, tip_keep_seconds: int) -> None:
        self.state_path = state_path
        self.min_price = min_price
        self.max_price = max_price
        self.max_tips = max_tips
        self.tip_keep_seconds = tip_keep_seconds
        self.lock = threading.Lock()
        self.rows: list[dict[str, str]] = []
        self.last_source_ok: str | None = None
        self.last_source_error: str | None = None
        self.last_bet_notifications: list[dict[str, str]] = []
        self.source_history: list[dict[str, Any]] = []
        self.seen_bet_ids: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        with self.lock:
            loaded_rows = [row for row in data.get("rows", []) if isinstance(row, dict)]
            load_ts = str(_now_ts())
            for row in loaded_rows:
                row.setdefault("__last_seen", load_ts)
            self.rows = loaded_rows
            self.last_source_ok = data.get("last_source_ok")
            self.last_source_error = data.get("last_source_error")
            self.last_bet_notifications = [item for item in data.get("last_bet_notifications", []) if isinstance(item, dict)][-20:]
            self.source_history = [item for item in data.get("source_history", []) if isinstance(item, dict)][-200:]
            self.seen_bet_ids = set(data.get("seen_bet_ids", []))

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            data = {
                "rows": self.rows,
                "last_source_ok": self.last_source_ok,
                "last_source_error": self.last_source_error,
                "last_bet_notifications": self.last_bet_notifications[-20:],
                "source_history": self.source_history[-200:],
                "seen_bet_ids": sorted(self.seen_bet_ids)[-500:],
            }
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _row_key(self, row: dict[str, str]) -> tuple[str, str, str]:
        return (
            row.get("EventName", "").casefold(),
            row.get("MarketType", "").casefold(),
            row.get("SelectionName", "").casefold(),
        )

    def _visible_rows(self, rows: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
        rows = self.rows if rows is None else rows
        visible: list[dict[str, str]] = []
        now_ts = _now_ts()
        for row in rows:
            row.setdefault("__last_seen", str(now_ts))
            last_seen = _float(str(row.get("__last_seen") or now_ts), now_ts)
            if now_ts - last_seen <= self.tip_keep_seconds:
                visible.append(row)
        return visible

    def replace_rows(self, rows: list[dict[str, str]]) -> int:
        cleaned: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        now_ts = _now_ts()
        for row in rows:
            event = row.get("EventName", "").casefold()
            market = row.get("MarketType", "").casefold()
            key = (event, market)
            if not event or key in seen:
                continue
            seen.add(key)
            row["__last_seen"] = str(now_ts)
            cleaned.append(row)
            if len(cleaned) >= self.max_tips:
                break
        with self.lock:
            merged_by_key: dict[tuple[str, str, str], dict[str, str]] = {
                self._row_key(row): row for row in self._visible_rows()
            }
            for row in cleaned:
                merged_by_key[self._row_key(row)] = row
            self.rows = list(merged_by_key.values())[-self.max_tips :]
            self.last_source_ok = _now()
            self.last_source_error = None
            self.source_history.append(
                {
                    "at": self.last_source_ok,
                    "source_count": len(cleaned),
                    "active_count": len(self.rows),
                    "rows": [
                        {
                            "event": row.get("EventName", ""),
                            "market": row.get("MarketName", ""),
                            "selection": row.get("SelectionName", ""),
                            "market_type": row.get("MarketType", ""),
                        }
                        for row in self.rows
                    ],
                }
            )
            self.source_history = self.source_history[-200:]
        self.save()
        return len(self.rows)

    def add_row(self, raw: dict[str, Any]) -> bool:
        row = _normalize_row(raw, min_price=self.min_price, max_price=self.max_price)
        if not row:
            return False
        with self.lock:
            key = (row["EventName"].casefold(), row["MarketType"].casefold())
            self.rows = [existing for existing in self.rows if (existing["EventName"].casefold(), existing["MarketType"].casefold()) != key]
            self.rows.insert(0, row)
            self.rows = self.rows[: self.max_tips]
        self.save()
        return True

    def clear_rows(self) -> None:
        with self.lock:
            self.rows = []
        self.save()

    def csv(self) -> str:
        with self.lock:
            self.rows = self._visible_rows()
            return _csv_text(list(self.rows))

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                "tips": len(self._visible_rows()),
                "last_source_ok": self.last_source_ok,
                "last_source_error": self.last_source_error,
                "current_rows": [
                    {
                        "event": row.get("EventName", ""),
                        "market": row.get("MarketName", ""),
                        "selection": row.get("SelectionName", ""),
                        "market_type": row.get("MarketType", ""),
                    }
                    for row in self._visible_rows()
                ],
                "last_bet_notifications": self.last_bet_notifications[-5:],
            }

    def history(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.source_history[-50:])

    def source_error(self, message: str) -> None:
        with self.lock:
            self.last_source_error = f"{_now()} {message}"
        self.save()

    def register_bet_notification(self, item: dict[str, str]) -> bool:
        bet_id = item.get("bet_id") or item.get("line") or ""
        if not bet_id:
            return False
        with self.lock:
            if bet_id in self.seen_bet_ids:
                return False
            self.seen_bet_ids.add(bet_id)
            self.last_bet_notifications.append({"at": _now(), **item})
            self.last_bet_notifications = self.last_bet_notifications[-20:]
        self.save()
        return True


def parse_source_csv(text: str, *, min_price: float, max_price: float) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in csv.DictReader(text.splitlines()):
        row = _normalize_row(raw, min_price=min_price, max_price=max_price)
        if row:
            rows.append(row)
    return rows


def source_loop(state: BridgeState, source_url: str, poll_seconds: int) -> None:
    while True:
        try:
            text = _read_url(source_url)
            rows = parse_source_csv(text, min_price=state.min_price, max_price=state.max_price)
            state.replace_rows(rows)
            print(f"[source] {len(rows)} tip(s) lidas do Railway.")
        except Exception as exc:  # noqa: BLE001 - bridge must keep running.
            state.source_error(f"{type(exc).__name__}: {exc}")
            print(f"[source] erro: {type(exc).__name__}: {exc}")
        time.sleep(max(5, poll_seconds))


BET_RE = re.compile(
    r"HandleOnPlaceBets:\s*Placed bet,\s*betId:\s*(?P<bet_id>[^,]+),\s*sizeMatched:\s*(?P<size>[^,]+),\s*success:\s*(?P<success>[^,]+),\s*strategy:\s*(?P<strategy>[^,]+)",
    re.IGNORECASE,
)


def monitor_bfbm_log(state: BridgeState, log_path: Path, notify_url: str | None, poll_seconds: int) -> None:
    if not log_path.exists():
        print(f"[log] arquivo nao encontrado: {log_path}")
        return
    with log_path.open("r", encoding="utf-8", errors="ignore") as file:
        file.seek(0, 2)
        while True:
            line = file.readline()
            if not line:
                time.sleep(max(1, poll_seconds))
                continue
            match = BET_RE.search(line)
            if not match:
                continue
            item = {
                "bet_id": match.group("bet_id").strip(),
                "size_matched": match.group("size").strip(),
                "success": match.group("success").strip(),
                "strategy": match.group("strategy").strip(),
                "line": line.strip(),
            }
            if not state.register_bet_notification(item):
                continue
            print(f"[log] aposta detectada betId={item['bet_id']} matched={item['size_matched']}")
            if notify_url:
                try:
                    _post_url(notify_url, item)
                except Exception as exc:  # noqa: BLE001
                    print(f"[log] erro ao notificar Railway: {type(exc).__name__}: {exc}")


def make_handler(state: BridgeState, api_token: str | None) -> type[BaseHTTPRequestHandler]:
    class BridgeHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not api_token:
                return True
            parsed = urllib.parse.urlparse(self.path)
            token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
            return token == api_token

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/health":
                self._send(200, b"ok\n", "text/plain; charset=utf-8")
                return
            if parsed.path == "/status":
                body = json.dumps(state.status(), ensure_ascii=False, indent=2).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
            if parsed.path == "/history":
                body = json.dumps(state.history(), ensure_ascii=False, indent=2).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
            if parsed.path == "/tips.csv":
                body = ("\ufeff" + state.csv()).encode("utf-8")
                self._send(200, body, "text/csv; charset=utf-8")
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in {"/tip", "/clear"}:
                self._send(404, b"not found\n", "text/plain; charset=utf-8")
                return
            if not self._authorized():
                self._send(403, b"forbidden\n", "text/plain; charset=utf-8")
                return
            if parsed.path == "/clear":
                state.clear_rows()
                self._send(200, b"cleared\n", "text/plain; charset=utf-8")
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self._send(400, b"invalid json\n", "text/plain; charset=utf-8")
                return
            if state.add_row(payload):
                self._send(200, b"accepted\n", "text/plain; charset=utf-8")
                return
            self._send(400, b"invalid tip\n", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:
            print("[http] " + format % args)

    return BridgeHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Ponte local entre TesteGPT, BFBM e Telegram.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--source-poll-seconds", type=int, default=20)
    parser.add_argument("--notify-url", default="")
    parser.add_argument("--api-token", default="")
    parser.add_argument("--min-price", type=float, default=1.80)
    parser.add_argument("--max-price", type=float, default=100.00)
    parser.add_argument("--max-tips", type=int, default=4)
    parser.add_argument("--tip-keep-seconds", type=int, default=600)
    parser.add_argument(
        "--log-path",
        default=str(Path.home() / "AppData/Local/bfbotmanager.com/Bf Bot Manager V3/log.txt"),
    )
    parser.add_argument(
        "--state-path",
        default=str(Path.home() / "AppData/Local/TesteGPT/bfbm_bridge_state.json"),
    )
    args = parser.parse_args()

    state = BridgeState(
        Path(args.state_path),
        min_price=args.min_price,
        max_price=args.max_price,
        max_tips=args.max_tips,
        tip_keep_seconds=args.tip_keep_seconds,
    )
    if args.source_url:
        threading.Thread(target=source_loop, args=(state, args.source_url, args.source_poll_seconds), daemon=True).start()
    threading.Thread(target=monitor_bfbm_log, args=(state, Path(args.log_path), args.notify_url or None, 2), daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(state, args.api_token or None))
    print(f"Ponte BFBM ativa: http://{args.host}:{args.port}/tips.csv")
    print(f"Status: http://{args.host}:{args.port}/status")
    server.serve_forever()


if __name__ == "__main__":
    main()
