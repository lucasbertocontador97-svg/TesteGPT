from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from betbot.bfbm_markets import markets_to_payload, parse_exported_markets_csv  # noqa: E402


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def post_json(url: str, payload: dict, timeout: int = 15) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def scan_once(export_path: Path, snapshot_path: Path, post_url: str | None) -> tuple[int, str]:
    text = read_text(export_path)
    markets = parse_exported_markets_csv(text)
    stat = export_path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    age_seconds = max(0.0, time.time() - stat.st_mtime)
    payload = markets_to_payload(
        markets,
        source_path=str(export_path),
        source_modified_at=modified_at,
        source_age_seconds=age_seconds,
    )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result = "local-only"
    if post_url:
        result = post_json(post_url, payload).strip()
    return len(markets), result


def main() -> int:
    parser = argparse.ArgumentParser(description="Scanner local de mercados exportados do BFBM.")
    parser.add_argument("--export-path", required=True, help="CSV exportado pelo BFBM com os mercados visiveis.")
    parser.add_argument(
        "--snapshot-path",
        default=str(Path.home() / "AppData/Local/TesteGPT/bfbm_markets_snapshot.json"),
        help="Arquivo local de snapshot JSON.",
    )
    parser.add_argument("--post-url", default="", help="URL Railway /bfbm/markets/snapshot?token=...")
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--post-interval-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    export_path = Path(args.export_path)
    snapshot_path = Path(args.snapshot_path)
    post_url = args.post_url.strip() or None
    last_signature: tuple[int, int] | None = None
    last_post_at = 0.0
    backoff = 1

    while True:
        try:
            if not export_path.exists():
                raise FileNotFoundError(str(export_path))
            stat = export_path.stat()
            signature = (int(stat.st_mtime), stat.st_size)
            now = time.time()
            should_post = args.once or signature != last_signature or (post_url and now - last_post_at >= max(5, args.post_interval_seconds))
            if should_post:
                count, result = scan_once(export_path, snapshot_path, post_url)
                print(f"[scanner] mercados={count} envio={result}", flush=True)
                last_signature = signature
                last_post_at = now
                backoff = 1
            if args.once:
                return 0
            time.sleep(max(1, args.poll_seconds))
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            print(f"[scanner] erro={type(exc).__name__}: {exc}", flush=True)
            if args.once:
                return 1
            time.sleep(min(60, backoff))
            backoff = min(60, backoff * 2)


if __name__ == "__main__":
    raise SystemExit(main())
