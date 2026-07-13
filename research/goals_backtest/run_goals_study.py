from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "cache"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "output"
BASE_URL = "https://v3.football.api-sports.io"

FINISHED_STATUS = {"FT", "AET", "PEN"}
GOAL_DETAILS = {"Normal Goal", "Own Goal", "Penalty"}


def ensure_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def read_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.getenv("API_FOOTBALL_KEY", "").strip()
    if not key:
        raise SystemExit("API_FOOTBALL_KEY nao configurada em research/goals_backtest/.env")
    return key


class ApiFootballClient:
    def __init__(self, key: str, use_cache: bool = True) -> None:
        self.key = key
        self.use_cache = use_cache
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={"x-apisports-key": key},
            timeout=30.0,
        )
        self.last_headers: dict[str, str] = {}

    def _cache_path(self, endpoint: str, params: dict[str, Any]) -> Path:
        raw = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        safe = endpoint.strip("/").replace("/", "_") or "root"
        return CACHE_DIR / f"{safe}_{digest}.json"

    def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        path = self._cache_path(endpoint, clean)
        if self.use_cache and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

        for attempt in range(5):
            try:
                response = self.client.get(endpoint, params=clean)
                self.last_headers = dict(response.headers)
                if response.status_code == 429:
                    time.sleep(10 + attempt * 10)
                    continue
                response.raise_for_status()
                payload = response.json()
                if "errors" in payload and payload["errors"]:
                    # Cache API-level empty/no-result responses, but not auth/rate-limit failures.
                    text_errors = json.dumps(payload["errors"], ensure_ascii=False).lower()
                    if "key" in text_errors or "rate" in text_errors or "subscription" in text_errors:
                        raise RuntimeError(f"API errors: {payload['errors']}")
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                return payload
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2**attempt)
        raise RuntimeError("unreachable")


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def normalize_team(name: str) -> str:
    return " ".join((name or "").strip().lower().replace("'", "").split())


def diagnostic(args: argparse.Namespace) -> None:
    ensure_dirs()
    client = ApiFootballClient(read_key(), use_cache=False)
    status = client.get("/status")
    sample = client.get("/fixtures", date=str(date.today()))
    out = {
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "status": status,
        "today_fixtures_count": len(sample.get("response", [])),
        "rate_headers": {
            k: v
            for k, v in client.last_headers.items()
            if "rate" in k.lower() or "request" in k.lower() or "limit" in k.lower()
        },
    }
    (OUTPUT_DIR / "diagnostic.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def collect(args: argparse.Namespace) -> None:
    ensure_dirs()
    client = ApiFootballClient(read_key())
    start = parse_date(args.date_from)
    end = parse_date(args.date_to)
    fixtures: list[dict[str, Any]] = []
    events_by_fixture: dict[str, list[dict[str, Any]]] = {}
    odds_by_fixture: dict[str, list[dict[str, Any]]] = {}

    checkpoint_path = PROCESSED_DIR / "dataset.partial.json"

    def save_checkpoint() -> None:
        checkpoint_path.write_text(
            json.dumps(
                {
                    "date_from": str(start),
                    "date_to": str(end),
                    "fixtures": fixtures,
                    "events_by_fixture": events_by_fixture,
                    "odds_by_fixture": odds_by_fixture,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    for day in daterange(start, end):
        payload = client.get("/fixtures", date=str(day))
        day_rows = payload.get("response", [])
        selected_today = 0
        for item in day_rows:
            fixture = item.get("fixture", {})
            status = (fixture.get("status") or {}).get("short")
            if status not in FINISHED_STATUS:
                continue
            fixtures.append(item)
            selected_today += 1
            if args.max_per_day and selected_today >= args.max_per_day:
                break
            if args.max_fixtures and len(fixtures) >= args.max_fixtures:
                break
        print(f"{day}: fixtures finalizadas acumuladas={len(fixtures)}", flush=True)
        if args.max_fixtures and len(fixtures) >= args.max_fixtures:
            break

    for idx, item in enumerate(fixtures, start=1):
        fid = str(item["fixture"]["id"])
        events_by_fixture[fid] = client.get("/fixtures/events", fixture=fid).get("response", [])
        try:
            odds_by_fixture[fid] = client.get("/odds", fixture=fid).get("response", [])
        except Exception as exc:
            odds_by_fixture[fid] = [{"_error": str(exc)}]
        if idx % 25 == 0:
            save_checkpoint()
            print(f"enriquecidas {idx}/{len(fixtures)}", flush=True)

    raw = {
        "date_from": str(start),
        "date_to": str(end),
        "fixtures": fixtures,
        "events_by_fixture": events_by_fixture,
        "odds_by_fixture": odds_by_fixture,
    }
    (PROCESSED_DIR / "dataset.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    print(f"dataset salvo: {PROCESSED_DIR / 'dataset.json'} | fixtures={len(fixtures)}", flush=True)


def goal_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    goals = []
    home_goals = 0
    away_goals = 0
    for ev in events:
        if ev.get("type") != "Goal":
            continue
        detail = ev.get("detail")
        if detail not in GOAL_DETAILS:
            continue
        minute = safe_int((ev.get("time") or {}).get("elapsed"))
        extra = safe_int((ev.get("time") or {}).get("extra"))
        goals.append(
            {
                "minute": minute,
                "extra": extra,
                "absolute_minute": minute + extra,
                "team": (ev.get("team") or {}).get("name", ""),
                "detail": detail,
            }
        )
    return sorted(goals, key=lambda g: (g["absolute_minute"], g["team"]))


def fixture_row(item: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    fixture = item.get("fixture", {})
    league = item.get("league", {})
    teams = item.get("teams", {})
    goals = item.get("goals", {})
    score = item.get("score", {})
    ht = (score.get("halftime") or {})
    ft = (score.get("fulltime") or {})
    timeline = goal_timeline(events)
    return {
        "fixture_id": fixture.get("id"),
        "date": fixture.get("date"),
        "league_id": league.get("id"),
        "league": league.get("name"),
        "country": league.get("country"),
        "season": league.get("season"),
        "home": (teams.get("home") or {}).get("name"),
        "away": (teams.get("away") or {}).get("name"),
        "home_goals": safe_int(goals.get("home", ft.get("home"))),
        "away_goals": safe_int(goals.get("away", ft.get("away"))),
        "ht_home": safe_int(ht.get("home")),
        "ht_away": safe_int(ht.get("away")),
        "total_goals": safe_int(goals.get("home", ft.get("home"))) + safe_int(goals.get("away", ft.get("away"))),
        "ht_goals": safe_int(ht.get("home")) + safe_int(ht.get("away")),
        "goal_minutes": ";".join(str(g["absolute_minute"]) for g in timeline),
        "has_goal_after_60": any(g["absolute_minute"] > 60 for g in timeline),
        "has_goal_after_70": any(g["absolute_minute"] > 70 for g in timeline),
        "has_goal_after_75": any(g["absolute_minute"] > 75 for g in timeline),
        "has_goal_after_80": any(g["absolute_minute"] > 80 for g in timeline),
        "score_60_goals": sum(1 for g in timeline if g["absolute_minute"] <= 60),
        "score_70_goals": sum(1 for g in timeline if g["absolute_minute"] <= 70),
        "score_75_goals": sum(1 for g in timeline if g["absolute_minute"] <= 75),
    }


def normalize_odds(fixture_id: str, odds_response: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for top in odds_response:
        if "_error" in top:
            continue
        for bookmaker in top.get("bookmakers", []) or []:
            bookmaker_name = bookmaker.get("name")
            for bet in bookmaker.get("bets", []) or []:
                bet_name = bet.get("name", "")
                for value in bet.get("values", []) or []:
                    val = str(value.get("value", ""))
                    odd = value.get("odd")
                    try:
                        odd_f = float(str(odd).replace(",", "."))
                    except Exception:
                        continue
                    market = map_market(bet_name, val)
                    if market:
                        rows.append(
                            {
                                "fixture_id": fixture_id,
                                "bookmaker": bookmaker_name,
                                "bet_name": bet_name,
                                "value": val,
                                "market": market,
                                "odd": odd_f,
                            }
                        )
    return rows


def map_market(bet_name: str, value: str) -> str | None:
    b = " ".join(bet_name.lower().split())
    v = value.lower().replace(" ", "")
    if b == "goals over/under":
        for line in ["0.5", "1.5", "2.5", "3.5", "4.5"]:
            if f"over{line}" in v or v == f"over {line}".replace(" ", ""):
                return f"MATCH_OVER_{line.replace('.', '_')}"
            if f"under{line}" in v:
                return f"MATCH_UNDER_{line.replace('.', '_')}"
    if b in {"both teams score", "both teams to score"}:
        if value.strip().lower() in {"yes", "sim"}:
            return "BTTS_YES"
        if value.strip().lower() in {"no", "nao", "não"}:
            return "BTTS_NO"
    return None


def settle(row: dict[str, Any], market: str) -> bool:
    goals = row["total_goals"]
    if market.startswith("MATCH_OVER_"):
        line = float(market.replace("MATCH_OVER_", "").replace("_", "."))
        return goals > line
    if market.startswith("MATCH_UNDER_"):
        line = float(market.replace("MATCH_UNDER_", "").replace("_", "."))
        return goals < line
    if market == "BTTS_YES":
        return row["home_goals"] > 0 and row["away_goals"] > 0
    if market == "BTTS_NO":
        return row["home_goals"] == 0 or row["away_goals"] == 0
    raise ValueError(market)


def summarize_bets(bets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bet in bets:
        grouped[bet["strategy_id"]].append(bet)
    rows = []
    for sid, items in grouped.items():
        profits = [float(x["profit"]) for x in items]
        wins = sum(1 for x in items if x["result"] == "WON")
        losses = sum(1 for x in items if x["result"] == "LOST")
        n = len(items)
        avg_odd = statistics.mean(float(x["odd"]) for x in items) if items else 0
        profit = sum(profits)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        streak = 0
        longest_losing = 0
        for p in profits:
            equity += p
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)
            if p < 0:
                streak += 1
                longest_losing = max(longest_losing, streak)
            else:
                streak = 0
        roi = profit / n if n else 0
        rows.append(
            {
                "strategy_id": sid,
                "entries": n,
                "wins": wins,
                "losses": losses,
                "hit_rate": round(wins / n, 4) if n else 0,
                "avg_odd": round(avg_odd, 4),
                "profit_units": round(profit, 4),
                "roi": round(roi, 4),
                "max_drawdown_units": round(abs(max_dd), 4),
                "longest_losing_streak": longest_losing,
            }
        )
    return sorted(rows, key=lambda r: (r["roi"], r["entries"]), reverse=True)


def split_name(row: dict[str, Any]) -> str:
    try:
        dt = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00")).date()
    except Exception:
        return "unknown"
    return "validation" if dt >= date(2026, 6, 12) else "train"


def frequency_strategy_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strategies = [
        ("FREQ_OVER_0_5_FT", "Over 0.5 FT", lambda r: r["total_goals"] > 0),
        ("FREQ_OVER_1_5_FT", "Over 1.5 FT", lambda r: r["total_goals"] > 1),
        ("FREQ_OVER_2_5_FT", "Over 2.5 FT", lambda r: r["total_goals"] > 2),
        ("FREQ_OVER_3_5_FT", "Over 3.5 FT", lambda r: r["total_goals"] > 3),
        ("FREQ_BTTS_YES", "BTTS Sim", lambda r: r["home_goals"] > 0 and r["away_goals"] > 0),
        ("FREQ_OVER_0_5_HT", "Over 0.5 HT", lambda r: r["ht_goals"] > 0),
        ("FREQ_OVER_1_5_HT", "Over 1.5 HT", lambda r: r["ht_goals"] > 1),
        (
            "LIVE_0_GOALS_60_NEXT_GOAL",
            "0 gols ate 60' -> mais um gol",
            lambda r: r["score_60_goals"] == 0 and r["has_goal_after_60"],
            lambda r: r["score_60_goals"] == 0,
        ),
        (
            "LIVE_1_GOAL_60_NEXT_GOAL",
            "1 gol ate 60' -> mais um gol",
            lambda r: r["score_60_goals"] == 1 and r["has_goal_after_60"],
            lambda r: r["score_60_goals"] == 1,
        ),
        (
            "LIVE_ANY_70_NEXT_GOAL",
            "Qualquer placar aos 70' -> mais um gol",
            lambda r: r["has_goal_after_70"],
            lambda r: True,
        ),
        (
            "LIVE_0_OR_1_GOAL_70_NEXT_GOAL",
            "0 ou 1 gol ate 70' -> mais um gol",
            lambda r: r["score_70_goals"] <= 1 and r["has_goal_after_70"],
            lambda r: r["score_70_goals"] <= 1,
        ),
    ]
    out = []
    for item in strategies:
        sid, label, win_fn = item[:3]
        universe_fn = item[3] if len(item) > 3 else (lambda r: True)
        universe = [r for r in rows if universe_fn(r)]
        for split in ["all", "train", "validation"]:
            split_rows = universe if split == "all" else [r for r in universe if split_name(r) == split]
            if not split_rows:
                continue
            wins = sum(1 for r in split_rows if win_fn(r))
            n = len(split_rows)
            out.append(
                {
                    "strategy_id": sid,
                    "description": label,
                    "split": split,
                    "entries": n,
                    "wins": wins,
                    "losses": n - wins,
                    "hit_rate": round(wins / n, 4),
                    "break_even_odd": round(n / wins, 4) if wins else "",
                    "note": "Frequencia historica sem ROI; precisa odd real no momento da entrada.",
                }
            )
    return sorted(out, key=lambda r: (r["split"] == "all", r["hit_rate"], r["entries"]), reverse=True)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def backtest(args: argparse.Namespace) -> None:
    ensure_dirs()
    dataset_path = PROCESSED_DIR / "dataset.json"
    if not dataset_path.exists():
        raise SystemExit("Rode collect antes do backtest.")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    fixture_rows = []
    odds_rows = []
    for item in dataset["fixtures"]:
        fid = str(item["fixture"]["id"])
        row = fixture_row(item, dataset["events_by_fixture"].get(fid, []))
        fixture_rows.append(row)
        odds_rows.extend(normalize_odds(fid, dataset["odds_by_fixture"].get(fid, [])))

    by_fixture_market: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for odd in odds_rows:
        by_fixture_market[(str(odd["fixture_id"]), odd["market"])].append(odd)

    bets = []
    for row in fixture_rows:
        fid = str(row["fixture_id"])
        for market in sorted({m for f, m in by_fixture_market if f == fid}):
            odds = by_fixture_market[(fid, market)]
            if not odds:
                continue
            best = max(odds, key=lambda x: x["odd"])
            if best["odd"] < args.min_odd or best["odd"] > args.max_odd:
                continue
            won = settle(row, market)
            profit = best["odd"] - 1 if won else -1.0
            bets.append(
                {
                    "strategy_id": f"PREMATCH_{market}_ODD_{args.min_odd}_{args.max_odd}",
                    "fixture_id": fid,
                    "date": row["date"],
                    "league": row["league"],
                    "country": row["country"],
                    "home": row["home"],
                    "away": row["away"],
                    "market": market,
                    "selection": market,
                    "odd": best["odd"],
                    "bookmaker": best["bookmaker"],
                    "result": "WON" if won else "LOST",
                    "profit": round(profit, 4),
                    "entry_basis": f"best_available_prematch_odd_between_{args.min_odd}_and_{args.max_odd}",
                }
            )

    summary = summarize_bets(bets)
    freq = frequency_strategy_results(fixture_rows)
    write_csv(OUTPUT_DIR / "fixtures.csv", fixture_rows)
    write_csv(OUTPUT_DIR / "odds_normalized.csv", odds_rows)
    write_csv(OUTPUT_DIR / "bet_level_results.csv", bets)
    write_csv(OUTPUT_DIR / "strategy_results.csv", summary)
    write_csv(OUTPUT_DIR / "frequency_strategy_results.csv", freq)
    (OUTPUT_DIR / "strategy_results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(fixture_rows, odds_rows, bets, summary, freq)
    print(f"fixtures={len(fixture_rows)} odds={len(odds_rows)} bets={len(bets)} strategies={len(summary)}", flush=True)
    print(f"relatorio: {OUTPUT_DIR / 'FINAL_REPORT.md'}", flush=True)


def rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def pattern_analysis(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns = []
    for minute in [60, 70, 75, 80]:
        for goals_so_far in [0, 1, 2]:
            subset = [r for r in rows if r[f"score_{minute if minute in [60,70,75] else 75}_goals"] == goals_so_far] if minute != 80 else [r for r in rows if sum(1 for x in str(r["goal_minutes"]).split(";") if x and int(x) <= 80) == goals_so_far]
            if not subset:
                continue
            next_goal = sum(1 for r in subset if any(x and int(x) > minute for x in str(r["goal_minutes"]).split(";")))
            patterns.append(
                {
                    "scenario": f"{goals_so_far} gols ate {minute}'",
                    "fixtures": len(subset),
                    "next_goal_rate": rate(next_goal, len(subset)),
                    "next_goal_count": next_goal,
                }
            )
    return patterns


def write_report(fixtures: list[dict[str, Any]], odds: list[dict[str, Any]], bets: list[dict[str, Any]], summary: list[dict[str, Any]], freq: list[dict[str, Any]]) -> None:
    patterns = pattern_analysis(fixtures)
    write_csv(OUTPUT_DIR / "live_goal_patterns.csv", patterns)
    top = summary[:10]
    lines = [
        "# PESQUISA DE ESTRATEGIAS DE GOLS - API-FOOTBALL",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Resumo executivo",
        "",
        f"- Fixtures analisadas: {len(fixtures)}",
        f"- Odds normalizadas: {len(odds)}",
        f"- Entradas financeiras testadas: {len(bets)}",
        f"- Estrategias com pelo menos uma entrada: {len(summary)}",
        f"- Estrategias de frequencia historica: {len({r['strategy_id'] for r in freq})}",
        "",
    ]
    if top:
        best = top[0]
        lines += [
            "## Melhor estrategia bruta encontrada",
            "",
            f"- Estrategia: `{best['strategy_id']}`",
            f"- Entradas: {best['entries']}",
            f"- Acertos: {best['wins']}",
            f"- Erros: {best['losses']}",
            f"- Hit rate: {best['hit_rate']}",
            f"- Odd media: {best['avg_odd']}",
            f"- Lucro unidades: {best['profit_units']}",
            f"- ROI por entrada: {best['roi']}",
            f"- Max drawdown: {best['max_drawdown_units']}",
            f"- Maior sequencia de reds: {best['longest_losing_streak']}",
            "",
            "Importante: esta primeira versao ranqueia por resultado bruto das odds pre-jogo encontradas. ",
            "Ela ainda deve ser validada com amostra maior, divisao temporal e filtros de robustez antes de virar regra operacional.",
            "",
        ]
    lines += [
        "## Top estrategias",
        "",
        "| Estrategia | Entradas | Hit rate | Odd media | Lucro | ROI | DD | Reds seguidos |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in top:
        lines.append(
            f"| `{row['strategy_id']}` | {row['entries']} | {row['hit_rate']} | {row['avg_odd']} | {row['profit_units']} | {row['roi']} | {row['max_drawdown_units']} | {row['longest_losing_streak']} |"
        )
    lines += [
        "",
        "## Frequencias historicas de gols",
        "",
        "Estas linhas mostram se o evento aconteceu historicamente. O `break_even_odd` e a odd minima teorica para empatar no longo prazo antes de margem/comissao.",
        "",
        "| Estrategia | Split | Entradas | Hit rate | Odd break-even |",
        "|---|---|---:|---:|---:|",
    ]
    for row in [r for r in freq if r["split"] == "all"][:30]:
        lines.append(
            f"| {row['description']} | {row['split']} | {row['entries']} | {row['hit_rate']} | {row['break_even_odd']} |"
        )
    lines += [
        "",
        "## Padroes live sem ROI",
        "",
        "Estes cenarios usam a linha temporal dos gols. Eles medem frequencia do evento, nao lucro, porque a API-Football geralmente nao fornece odd live historica minuto a minuto.",
        "",
        "| Cenario | Jogos | Taxa de novo gol | Gols posteriores |",
        "|---|---:|---:|---:|",
    ]
    for p in patterns[:30]:
        lines.append(f"| {p['scenario']} | {p['fixtures']} | {p['next_goal_rate']} | {p['next_goal_count']} |")
    lines += [
        "",
        "## Arquivos",
        "",
        "- `strategy_results.csv`: ranking por estrategia",
        "- `bet_level_results.csv`: auditoria aposta a aposta",
        "- `fixtures.csv`: fixtures normalizadas",
        "- `odds_normalized.csv`: odds mapeadas",
        "- `live_goal_patterns.csv`: frequencias live sem ROI",
        "",
        "## Proxima etapa",
        "",
        "Aumentar a coleta, aplicar split temporal treino/validacao e testar filtros por liga/time para evitar overfitting.",
    ]
    (OUTPUT_DIR / "FINAL_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("diagnostic")
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("--from", dest="date_from", default="2026-04-12")
    p_collect.add_argument("--to", dest="date_to", default="2026-07-12")
    p_collect.add_argument("--max-fixtures", type=int, default=300)
    p_collect.add_argument("--max-per-day", type=int, default=20)
    p_backtest = sub.add_parser("backtest")
    p_backtest.add_argument("--min-odd", type=float, default=1.80)
    p_backtest.add_argument("--max-odd", type=float, default=8.00)
    args = parser.parse_args()
    if args.cmd == "diagnostic":
        diagnostic(args)
    elif args.cmd == "collect":
        collect(args)
    elif args.cmd == "backtest":
        backtest(args)


if __name__ == "__main__":
    main()
