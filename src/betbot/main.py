from __future__ import annotations

import asyncio
import hmac
import json
import logging
import sys
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
from telegram import Update
from telegram.error import BadRequest, Conflict
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .ai import analyze_game, analyze_live_game_without_odds, suggest_market_without_odds
from .bfbm import BfbmConfig, debug_event_csv, debug_lab_csv, debug_minimal_csv, fresh_event_csv, fresh_match_odds_csv, fresh_match_odds_full_csv, fresh_match_odds_ids_csv, fresh_match_odds_rich_csv, fresh_test_csv, full_rows_with_audit, rows_to_full_csv, tips_clean_match_odds_csv, tips_csv, tips_full_csv, tips_rich_csv
from .bfbm_markets import _event_score, event_score_for_row, find_bfbm_market, market_family, market_line, payload_to_markets, row_market_family
from .clients import ApiFootballClient, HttpJsonClient, OddsApiClient, SportmonksClient, TheStatsApiClient, TotalCornerClient
from .config import database_storage_status, load_settings, require_runtime_settings, require_telegram_settings, settings_presence
from .deterministic import evaluate_game
from .markets import flatten_all_markets, flatten_markets, market_matches_idea
from .matching import find_matching_odds_event, find_matching_sportmonks_fixture, find_matching_thestatsapi_match, find_matching_totalcorner_match, sportmonks_participant_names
from .models import Decision, GameSnapshot
from .settlement import settle_alert
from .stats import compact_player_statistics, compact_sportmonks_statistics, compact_statistics, compact_stats_summary, compact_thestatsapi_statistics, compact_totalcorner_statistics, extract_minute, extract_score, has_actionable_stats, is_blocked_match_type, is_high_variance_match
from .storage import Storage
from .telegram_io import alert_keyboard, format_alert, send_message


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("betbot")
BFBM_TOTALCORNER_MIN_LIVE_EVENTS = 150


MARKET_LABELS = {
    ("goals", "over"): "Mais gols",
    ("goals", "under"): "Menos gols",
    ("first_half_goals", "over"): "Mais gols HT",
    ("first_half_goals", "under"): "Menos gols HT",
    ("corners", "over"): "Mais escanteios",
    ("corners", "under"): "Menos escanteios",
    ("btts", "yes"): "Ambos marcam",
    ("btts", "no"): "Ambos marcam",
}


def market_label(market_family: str, selection: str) -> str:
    return MARKET_LABELS.get((market_family, selection), f"{market_family} {selection}")


def _record_bfbm_candidate_audit(
    storage: Storage,
    game: GameSnapshot,
    signal,
    *,
    status: str,
    reason: str,
    bfbm_market: dict | None = None,
) -> None:
    family = getattr(signal, "market_family", "none")
    selection = getattr(signal, "selection", "none")
    line = getattr(signal, "line", None)
    strategy = getattr(signal, "strategy", "unknown")
    alert_key = f"candidate|{game.event_id}|{family}|{selection}|{line}|{strategy}"
    storage.record_bfbm_export_audit(
        [
            {
                "alert_id": None,
                "alert_key": alert_key,
                "endpoint": "process_once",
                "status": status,
                "reason": reason,
                "home": game.home,
                "away": game.away,
                "event_name": f"{game.home} x {game.away}",
                "market": market_label(family, selection),
                "selection": selection,
                "line": line,
                "bfbm_event_name": (bfbm_market or {}).get("event_name", ""),
                "bfbm_market_name": (bfbm_market or {}).get("market_name", ""),
                "bfbm_selection_name": "",
                "bfbm_event_id": (bfbm_market or {}).get("event_id", ""),
                "bfbm_market_id": (bfbm_market or {}).get("market_id", ""),
                "bfbm_selection_id": "",
                "bfbm_start_time": (bfbm_market or {}).get("start_time", ""),
                "raw": {
                    "event_id": game.event_id,
                    "minute": game.minute,
                    "score_home": game.score_home,
                    "score_away": game.score_away,
                    "signal_reason": getattr(signal, "reason", ""),
                    "strategy": strategy,
                    "probability": getattr(signal, "probability", 0),
                    "confidence": getattr(signal, "confidence", 0),
                },
            }
        ]
    )


def _format_bfbm_confirmed_tip_candidates(storage: Storage, settings, limit: int = 3) -> str:
    tips = storage.bfbm_tips(settings.bfbm_max_tip_age_minutes, limit=limit)
    if not tips:
        return "Tip: nao encontrei tip ativa recente no banco."

    lines = ["Tip enviada ao BFBM:" if len(tips) == 1 else "Tips ativas enviadas ao BFBM:"]
    for tip in tips:
        line = tip.get("line")
        if line in (None, ""):
            line_text = ""
        elif isinstance(line, (int, float)):
            line_text = f" {line:g}"
        else:
            line_text = f" {line}"
        score = f"{tip.get('score_home', '?')}x{tip.get('score_away', '?')}"
        minute = tip.get("minute")
        minute_text = "?" if minute in (None, "") else f"{minute}'"
        lines.append(
            "- "
            f"{tip.get('home', '?')} x {tip.get('away', '?')} | "
            f"{minute_text} {score} | "
            f"{tip.get('market', '?')}{line_text} | "
            f"{tip.get('selection', '?')} | "
            f"conf {tip.get('confidence', '?')}"
        )
    return "\n".join(lines)


def _format_bfbm_snapshot(snapshot_text: str) -> str | None:
    if not snapshot_text:
        return None
    try:
        rows = json.loads(snapshot_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    lines = ["Tip ativa no BFBM:" if len(rows) == 1 else "Tips ativas no BFBM:"]
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        lines.append(
            "- "
            f"{row.get('event', '?')} | "
            f"{row.get('market', '?')} | "
            f"{row.get('selection', '?')}"
        )
    return "\n".join(lines)


def _format_result_notification(alert: dict) -> str:
    status = str(alert.get("status") or "").upper()
    if status == "WON":
        title = "\u2705 GREEN - RESULTADO CONFIRMADO"
    elif status == "LOST":
        title = "\U0001f534 RED - RESULTADO CONFIRMADO"
    else:
        title = "\U0001f7e1 VOID/PUSH - RESULTADO CONFIRMADO"
    line = alert.get("line")
    if line in (None, ""):
        line_text = ""
    else:
        try:
            line_text = f" {float(line):g}"
        except (TypeError, ValueError):
            line_text = f" {line}"
    odd = alert.get("odd")
    try:
        odd_text = f"{float(odd):.2f}" if float(odd or 0) > 0 else "BFBM"
    except (TypeError, ValueError):
        odd_text = "BFBM"
    bet_id = str(alert.get("bfbm_bet_id") or "").strip()
    note = str(alert.get("result_note") or "-").strip()
    return (
        f"{title}\n\n"
        f"Jogo: {alert.get('home', '?')} x {alert.get('away', '?')}\n"
        f"Mercado: {alert.get('market', '?')}{line_text}\n"
        f"Selecao: {alert.get('selection', '?')}\n"
        f"Odd registrada: {odd_text}\n"
        f"Resultado: {status}\n"
        f"Bet ID: {bet_id or '-'}\n\n"
        f"Fechamento: {note}"
    )


def verified_alert_key(game: GameSnapshot, market_family: str, selection: str, line: float | None) -> str:
    return f"verified|{game.fixture_id}|{market_family}|{selection}|{line}"


def odds_alert_key(game: GameSnapshot, market_family: str, selection: str, line: float | None) -> str:
    return f"odds|{game.fixture_id or game.event_id}|{market_family}|{selection}|{line}"


def bfbm_available_market_specs(catalog_rows: list[dict], event_name: str, min_score: int = 55) -> list[tuple[str, float | None]]:
    specs: list[tuple[str, float | None]] = []
    seen: set[tuple[str, float | None]] = set()
    for row in catalog_rows:
        status = str(row.get("status") or "").casefold()
        if "closed" in status or "fechado" in status:
            continue
        if _event_score(event_name, str(row.get("event_name") or "")) < min_score:
            continue
        family = row_market_family(row)
        line = market_line(str(row.get("market_name") or "")) or market_line(str(row.get("market_type") or ""))
        market_type = str(row.get("market_type") or "").casefold()
        market_name = str(row.get("market_name") or "").casefold()
        if family == "goals" and line is None and ("alt_total_goals" in market_type or "linhas de gol" in market_name):
            candidate_lines = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5]
        elif family == "corners" and line is None and ("corner" in market_type or "corners total" in market_name or "escanteio" in market_name):
            candidate_lines = [float(value) + 0.5 for value in range(0, 16)]
        else:
            candidate_lines = [line]
        for candidate_line in candidate_lines:
            key = (family, candidate_line)
            if key in seen:
                continue
            seen.add(key)
            specs.append(key)
    return specs


def _tc_int(match: dict, key: str, default: int = 0) -> int:
    try:
        return int(match.get(key) if match.get(key) is not None else default)
    except (TypeError, ValueError):
        return default


def _forced_live_decision(game: GameSnapshot) -> Decision:
    corners = 0
    shots = 0
    shots_on = 0
    for values in game.stats.values():
        corners += _tc_int(values, "Corner Kicks")
        shots += _tc_int(values, "Total Shots")
        shots_on += _tc_int(values, "Shots on Goal")
    current_goals = (game.score_home or 0) + (game.score_away or 0)

    return Decision(
        True,
        90,
        "Resultado da partida",
        game.home,
        "Conferir manualmente",
        0.0,
        None,
        f"TIP LIVE TESTE BFBM: jogo ao vivo real; usando Resultado da partida para casar com os mercados carregados no BFBM. Gols {current_goals}, escanteios {corners}, chutes {shots}, no gol {shots_on}.",
        "baixa",
        f"bfbm-live4|{game.event_id}|match_odds|{datetime.utcnow().strftime('%H%M%S')}",
        market_status=totalcorner_market_status(game.totalcorner_match, "goals"),
    )


async def create_live_bfbm_tips(settings, count: int = 4) -> tuple[int, list[str]]:
    http = HttpJsonClient()
    storage = Storage(settings.database_path)
    created: list[str] = []
    try:
        if not settings.totalcorner_token:
            return 0, ["TOTALCORNER_TOKEN ausente."]
        live = await TotalCornerClient(settings.totalcorner_token, http).today_inplay(max(settings.max_live_events, count * 3))
        accepted = [
            match
            for match in live
            if not is_blocked_match_type(str(match.get("l") or ""), str(match.get("h") or ""), str(match.get("a") or ""))
        ]
        for index, match in enumerate(accepted[:count]):
            stats = compact_totalcorner_statistics(match)
            home = str(match.get("h") or "?")
            away = str(match.get("a") or "?")
            game = GameSnapshot(
                event_id=f"tc-live-{match.get('id') or match.get('mid') or index}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                fixture_id=None,
                league=str(match.get("l") or "TotalCorner Live"),
                home=home,
                away=away,
                minute=_tc_int(match, "status", 75),
                score_home=_tc_int(match, "hg"),
                score_away=_tc_int(match, "ag"),
                stats=stats,
                markets=[],
                totalcorner_match=match,
            )
            decision = _forced_live_decision(game)
            alert_id = storage.save_manual_alert(game, decision)
            if alert_id:
                created.append(f"{home} v {away}")
        if not created:
            return 0, ["Nenhum jogo ao vivo aceito retornado pelo TotalCorner."]
        return len(created), created
    finally:
        storage.close()
        await http.close()


def _tc_event_label(match: dict) -> str:
    home = str(match.get("h") or "?").strip()
    away = str(match.get("a") or "?").strip()
    return f"{home} x {away}"


def _tc_score_label(match: dict) -> str:
    return f"{_tc_int(match, 'hg')}x{_tc_int(match, 'ag')}"


def _bfbm_totalcorner_live_limit(settings) -> int:
    return max(settings.max_live_events, BFBM_TOTALCORNER_MIN_LIVE_EVENTS)


async def bfbm_totalcorner_overlap(settings) -> dict:
    storage = Storage(settings.database_path)
    try:
        catalog_rows = storage.bfbm_markets(15)
    finally:
        storage.close()

    http = HttpJsonClient()
    totalcorner_error = ""
    totalcorner_limit = _bfbm_totalcorner_live_limit(settings)
    try:
        live = await TotalCornerClient(settings.totalcorner_token, http).today_inplay(totalcorner_limit) if settings.totalcorner_token else []
    except Exception as exc:
        logger.warning("TotalCorner overlap falhou: %s", exc)
        live = []
        totalcorner_error = str(exc)[:300] or type(exc).__name__
    finally:
        await http.close()

    accepted: list[dict] = []
    blocked: list[dict] = []
    for match in live:
        if is_blocked_match_type(str(match.get("l") or ""), str(match.get("h") or ""), str(match.get("a") or "")):
            blocked.append(match)
        else:
            accepted.append(match)

    active_catalog = [
        row
        for row in catalog_rows
        if "closed" not in str(row.get("status") or "").casefold()
        and "fechado" not in str(row.get("status") or "").casefold()
    ]

    matched: list[dict] = []
    unmatched: list[dict] = []
    learned_aliases: list[dict] = []
    for match in accepted:
        event_label = _tc_event_label(match)
        scored_rows: list[tuple[int, dict]] = []
        seen_markets: set[str] = set()
        for row in active_catalog:
            score = event_score_for_row(event_label, row)
            if score < 75:
                continue
            market_name = str(row.get("market_name") or "")
            key = f"{row.get('event_id') or ''}|{row.get('market_id') or ''}|{market_name}"
            if key in seen_markets:
                continue
            seen_markets.add(key)
            scored_rows.append((score, row))
        scored_rows.sort(key=lambda item: item[0], reverse=True)
        markets = [
            {
                "event": row.get("event_name") or "",
                "market": row.get("market_name") or "",
                "family": market_family(str(row.get("market_name") or "")),
                "event_id": row.get("event_id") or "",
                "market_id": row.get("market_id") or "",
                "status": row.get("status") or "",
                "score": score,
            }
            for score, row in scored_rows[:12]
        ]
        if scored_rows:
            best_score, best_row = scored_rows[0]
            raw_score = _event_score(event_label, str(best_row.get("event_name") or ""))
            if raw_score >= 75:
                learned_aliases.append(
                    {
                        "source_event_name": event_label,
                        "target_event_name": str(best_row.get("event_name") or ""),
                        "score": raw_score,
                    }
                )
        item = {
            "totalcorner_event": event_label,
            "league": str(match.get("l") or ""),
            "minute": _tc_int(match, "status"),
            "score": _tc_score_label(match),
            "bfbm_markets_count": len(scored_rows),
            "bfbm_markets": markets,
        }
        stats = compact_totalcorner_statistics(match)
        signal = evaluate_game(
            minute=item["minute"],
            score_home=_tc_int(match, "hg"),
            score_away=_tc_int(match, "ag"),
            stats=stats,
            min_confidence=settings.min_confidence,
            available_markets=bfbm_available_market_specs(active_catalog, event_label, 75),
        )
        item["signal"] = {
            "approved": signal.approved,
            "market_family": signal.market_family,
            "selection": signal.selection,
            "line": signal.line,
            "probability": signal.probability,
            "score": signal.score,
            "strategy": signal.strategy,
            "reason": signal.reason,
        }
        if signal.approved:
            exact_bfbm_market = find_bfbm_market(active_catalog, event_label, signal.market_family, signal.line)
            item["signal"]["bfbm_can_bet"] = bool(exact_bfbm_market)
            item["signal"]["bfbm_match_mode"] = "exact_line" if exact_bfbm_market else "none"
        if scored_rows:
            matched.append(item)
        else:
            unmatched.append(item)

    if learned_aliases:
        storage = Storage(settings.database_path)
        try:
            storage.record_bfbm_event_aliases(learned_aliases)
        finally:
            storage.close()

    return {
        "totalcorner_live": len(live),
        "totalcorner_limit": totalcorner_limit,
        "totalcorner_error": totalcorner_error,
        "totalcorner_accepted": len(accepted),
        "totalcorner_blocked": len(blocked),
        "bfbm_markets": len(catalog_rows),
        "bfbm_active_markets": len(active_catalog),
        "matched_games": len(matched),
        "unmatched_games": len(unmatched),
        "learned_aliases": len(learned_aliases),
        "matched": matched[:30],
        "unmatched": unmatched[:30],
    }


async def build_bfbm_totalcorner_snapshots(settings, http: HttpJsonClient, storage: Storage) -> list[GameSnapshot]:
    if not settings.bfbm_export or not settings.totalcorner_token:
        return []
    catalog_rows = storage.bfbm_markets(15)
    if not catalog_rows:
        return []
    live = await load_totalcorner_live(settings, http, limit=_bfbm_totalcorner_live_limit(settings))
    snapshots: list[GameSnapshot] = []
    learned_aliases: list[dict] = []
    for index, match in enumerate(live):
        event_label = _tc_event_label(match)
        best_score = 0
        best_event_name = ""
        for row in catalog_rows:
            score = event_score_for_row(event_label, row)
            if score > best_score:
                best_score = score
                best_event_name = str(row.get("event_name") or "")
        if best_score < 75:
            continue
        raw_score = _event_score(event_label, best_event_name)
        if raw_score >= 75:
            learned_aliases.append(
                {
                    "source_event_name": event_label,
                    "target_event_name": best_event_name,
                    "score": raw_score,
                }
            )
        stats = compact_totalcorner_statistics(match)
        if not has_actionable_stats(stats):
            continue
        match_id = str(match.get("id") or match.get("mid") or index)
        snapshots.append(
            GameSnapshot(
                event_id=f"tc-bfbm-{match_id}",
                fixture_id=None,
                league=str(match.get("l") or "TotalCorner Live"),
                home=str(match.get("h") or ""),
                away=str(match.get("a") or ""),
                minute=_tc_int(match, "status"),
                score_home=_tc_int(match, "hg"),
                score_away=_tc_int(match, "ag"),
                stats=stats,
                markets=[],
                totalcorner_match=match,
            )
        )
    if learned_aliases:
        storage.record_bfbm_event_aliases(learned_aliases)
    return snapshots


async def create_live_bfbm_zero_zero_goal_tests(settings, count: int = 4) -> tuple[int, list[str]]:
    http = HttpJsonClient()
    storage = Storage(settings.database_path)
    created: list[str] = []
    try:
        snapshots = await build_bfbm_totalcorner_snapshots(settings, http, storage)
        catalog_rows = storage.bfbm_markets(15)
        for game in snapshots:
            if len(created) >= count:
                break
            if (game.score_home or 0) != 0 or (game.score_away or 0) != 0:
                continue
            if not find_bfbm_market(catalog_rows, f"{game.home} x {game.away}", "goals", 0.5):
                continue
            stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            alert_key = f"bfbm-force-00-over05|{game.home}|{game.away}|{stamp}"
            decision = Decision(
                True,
                99,
                "Mais gols",
                "over",
                "BFBM teste 0x0",
                0.0,
                0.5,
                "TESTE FORCADO: jogo 0x0 ao vivo com mercado de gols detectado no BFBM; validar linha Mais/Menos de 0,5 Gols.",
                "teste",
                alert_key,
                market_status="Teste BFBM: mercado de gols detectado.",
            )
            alert_id = storage.save_manual_alert(game, decision)
            if alert_id:
                created.append(f"{game.home} x {game.away} {game.score_home}x{game.score_away} {game.minute}'")
        if not created:
            return 0, ["Nenhum 0x0 ao vivo com mercado de gols no BFBM agora."]
        return len(created), created
    finally:
        storage.close()
        await http.close()


class BfbmRequestHandler(BaseHTTPRequestHandler):
    def _check_bfbm_token(self, settings, parsed) -> bool:
        token = parse_qs(parsed.query).get("token", [""])[0]
        if settings.bfbm_token and token != settings.bfbm_token:
            self.send_error(403)
            return False
        return True

    def _check_betfair_cache_key(self, settings) -> bool:
        if not settings.betfair_cache_api_key:
            self.send_error(503, "BETFAIR_CACHE_API_KEY not configured")
            return False
        provided = self.headers.get("X-Api-Key", "").strip()
        if not hmac.compare_digest(provided, settings.betfair_cache_api_key):
            self.send_error(401)
            return False
        return True

    def do_POST(self) -> None:
        settings = load_settings()
        parsed = urlparse(self.path)
        if parsed.path != "/bfbm/markets/snapshot":
            self.send_error(404)
            return
        if not settings.bfbm_export:
            self.send_error(404, "BFBM export disabled")
            return
        if not self._check_bfbm_token(settings, parsed):
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 50_000_000:
                self.send_error(413, "snapshot too large")
                return
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            rows = payload_to_markets(payload if isinstance(payload, dict) else {})
            storage = Storage(settings.database_path)
            try:
                count = storage.replace_bfbm_markets(rows)
            finally:
                storage.close()
            body = f"ok markets={count}\n".encode("utf-8")
            self.send_response(200)
        except Exception as exc:
            logger.exception("Erro ao receber snapshot BFBM")
            body = f"error={type(exc).__name__}\n".encode("utf-8")
            self.send_response(500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        settings = load_settings()
        parsed = urlparse(self.path)
        if parsed.path not in {
            "/bfbm/tips.csv",
            "/bfbm/live.csv",
            "/bfbm/fresh.csv",
            "/bfbm/fresh-event.csv",
            "/bfbm/fresh-match.csv",
            "/bfbm/fresh-match-full.csv",
            "/bfbm/fresh-match-ids.csv",
            "/bfbm/fresh-match-rich.csv",
            "/bfbm/live-full.csv",
            "/bfbm/live-rich.csv",
            "/bfbm/live-clean.csv",
            "/bfbm/debug-minimal.csv",
            "/bfbm/debug-event.csv",
            "/bfbm/lab.csv",
            "/bfbm/create-test",
            "/bfbm/create-live-4",
            "/bfbm/create-live-00-goals",
            "/bfbm/markets.json",
            "/bfbm/aliases.json",
            "/bfbm/live-overlap.json",
            "/bfbm/strategy-report.json",
            "/bfbm/export-audit.json",
            "/bfbm/system-health.json",
            "/bfbm/notify-bet",
            "/api/betfair/cache",
            "/health",
        }:
            self.send_error(404)
            return
        if parsed.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if parsed.path == "/api/betfair/cache":
            if not self._check_betfair_cache_key(settings):
                return
            query = parse_qs(parsed.query)
            try:
                max_age_minutes = int(query.get("max_age_minutes", ["15"])[0])
            except ValueError:
                max_age_minutes = 15
            try:
                limit = int(query.get("limit", ["5000"])[0])
            except ValueError:
                limit = 5000
            market_type = query.get("market_type", [""])[0].strip().upper()
            event_search = query.get("event", [""])[0].strip().casefold()
            include_raw = query.get("include_raw", ["0"])[0].lower() in {"1", "true", "yes", "sim"}
            storage = Storage(settings.database_path)
            try:
                rows = storage.bfbm_markets(max_age_minutes=max(1, min(1440, max_age_minutes)))
            finally:
                storage.close()
            if market_type:
                rows = [row for row in rows if str(row.get("market_type") or "").upper() == market_type]
            if event_search:
                rows = [row for row in rows if event_search in str(row.get("event_name") or "").casefold()]
            rows = rows[: max(1, min(10000, limit))]
            for row in rows:
                raw_json = row.get("raw_json")
                raw = None
                if isinstance(raw_json, str) and raw_json:
                    try:
                        raw = json.loads(raw_json)
                    except json.JSONDecodeError:
                        raw = None
                compact_runners = []
                if isinstance(raw, dict):
                    for runner in raw.get("runners") or []:
                        if not isinstance(runner, dict):
                            continue
                        compact_runners.append(
                            {
                                "selection_id": str(runner.get("selectionId") or runner.get("selection_id") or ""),
                                "runner_name": str(runner.get("runnerName") or runner.get("runner_name") or ""),
                                "handicap": runner.get("handicap"),
                                "status": str(runner.get("status") or ""),
                                "best_back_price": runner.get("bestBackPrice") or runner.get("back") or 0,
                                "best_back_size": runner.get("bestBackSize") or runner.get("back_size") or 0,
                                "best_lay_price": runner.get("bestLayPrice") or runner.get("lay") or 0,
                                "best_lay_size": runner.get("bestLaySize") or runner.get("lay_size") or 0,
                                "last_price_traded": runner.get("lastPriceTraded") or 0,
                            }
                        )
                row["runners"] = compact_runners
                if include_raw:
                    row["raw"] = raw
                if not include_raw:
                    row.pop("raw_json", None)
            data = {
                "ok": True,
                "source": "bfbm_betfair_cache",
                "count": len(rows),
                "max_age_minutes": max(1, min(1440, max_age_minutes)),
                "filters": {
                    "market_type": market_type,
                    "event": event_search,
                    "include_raw": include_raw,
                },
                "markets": rows,
            }
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "private, max-age=5")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not settings.bfbm_export:
            self.send_error(404, "BFBM export disabled")
            return
        if not self._check_bfbm_token(settings, parsed):
            return
        if parsed.path == "/bfbm/markets.json":
            storage = Storage(settings.database_path)
            try:
                rows = storage.bfbm_markets(15)
            finally:
                storage.close()
            body = json.dumps({"markets": rows}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/aliases.json":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["200"])[0])
            except ValueError:
                limit = 200
            storage = Storage(settings.database_path)
            try:
                rows = storage.bfbm_event_aliases(max(1, min(1000, limit)))
            finally:
                storage.close()
            body = json.dumps({"aliases": rows}, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/live-overlap.json":
            try:
                data = asyncio.run(bfbm_totalcorner_overlap(settings))
                body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
            except Exception as exc:
                logger.exception("Erro ao cruzar TotalCorner com BFBM")
                body = json.dumps({"error": type(exc).__name__}, ensure_ascii=False).encode("utf-8")
                self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/strategy-report.json":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["80"])[0])
            except ValueError:
                limit = 80
            storage = Storage(settings.database_path)
            try:
                data = storage.strategy_report(limit=max(10, min(250, limit)))
            finally:
                storage.close()
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/system-health.json":
            storage_status = database_storage_status(settings)
            data = {
                "ok": bool(storage_status["persistent"]),
                "database": storage_status,
                "bfbm_export": settings.bfbm_export,
                "bfbm_token_configured": bool(settings.bfbm_token),
                "poll_seconds": settings.poll_seconds,
                "bfbm_max_tip_age_minutes": settings.bfbm_max_tip_age_minutes,
                "warning": "" if storage_status["persistent"] else "Banco temporario: historico e auditoria podem sumir em redeploy/restart.",
            }
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/export-audit.json":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            try:
                hours = int(query.get("hours", ["24"])[0])
            except ValueError:
                hours = 24
            storage = Storage(settings.database_path)
            try:
                data = storage.bfbm_export_report(limit=max(10, min(500, limit)), hours=max(1, min(168, hours)))
            finally:
                storage.close()
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/create-live-00-goals":
            try:
                created_count, events = asyncio.run(create_live_bfbm_zero_zero_goal_tests(settings, 4))
                body_text = (
                    f"created={created_count}\n"
                    f"live_csv=/bfbm/live-full.csv?token={settings.bfbm_token or ''}\n"
                    + "\n".join(f"- {event}" for event in events)
                    + "\n"
                )
                body = body_text.encode("utf-8")
                self.send_response(200)
            except Exception as exc:
                logger.exception("Erro ao criar teste 0x0 BFBM")
                body = f"error={type(exc).__name__}\n".encode("utf-8")
                self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/notify-bet":
            query = parse_qs(parsed.query)
            bet_id = query.get("bet_id", [""])[0].strip()
            size_matched = query.get("size_matched", [""])[0].strip()
            success = query.get("success", [""])[0].strip()
            strategy = query.get("strategy", [""])[0].strip()
            placed_at = query.get("placed_at", [""])[0].strip()
            sid = query.get("sid", [""])[0].strip()
            silent = query.get("silent", [""])[0].strip().lower() in {"1", "true", "yes", "sim"}
            raw_line = query.get("line", [""])[0].strip()
            tips_snapshot = query.get("tips_snapshot", [""])[0].strip()
            placed_at_iso = ""
            if placed_at:
                try:
                    placed_at_iso = datetime.strptime(placed_at, "%d/%m/%Y %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    placed_at_iso = ""
            matched = False
            try:
                matched = float(size_matched.replace(",", ".") or "0") > 0
            except ValueError:
                matched = False
            title = "\u2705 APOSTA FEITA NO BFBM" if matched else "\u26a0\ufe0f APOSTA NAO CASADA NO BFBM"
            text = (
                f"{title}\n\n"
                f"Bet ID: {bet_id or '-'}\n"
                f"Matched: {size_matched or '0'}\n"
                f"Status: {success or '-'}\n"
                f"Estrategia: {strategy or '-'}\n"
                f"SID: {sid or '-'}\n"
                f"Horario BFBM: {placed_at or '-'}"
            )
            if raw_line:
                text += f"\n\nLog: {raw_line[:500]}"
            try:
                storage = Storage(settings.database_path)
                try:
                    active_tips = storage.bfbm_tips(settings.bfbm_max_tip_age_minutes, limit=5)
                    tips_text = _format_bfbm_confirmed_tip_candidates(storage, settings, limit=5)
                    storage.record_bfbm_bet_notification(
                        {
                            "bet_id": bet_id,
                            "placed_at": placed_at,
                            "placed_at_iso": placed_at_iso,
                            "size_matched": size_matched,
                            "success": success,
                            "strategy": strategy,
                            "sid": sid,
                            "line": raw_line,
                        }
                    )
                    linked_tip = None
                    link_note = ""
                    if matched:
                        if len(active_tips) == 1:
                            linked_tip = active_tips[0]
                            storage.mark_bfbm_bet(int(linked_tip["id"]), bet_id, placed_at_iso or placed_at)
                            link_note = f"\n\nTip vinculada: alert_id={linked_tip['id']}"
                        elif not active_tips:
                            link_note = "\n\nAviso: nao encontrei tip ativa para vincular resultado automaticamente."
                        else:
                            link_note = (
                                "\n\nAviso: havia mais de uma tip ativa; nao vinculei resultado automaticamente "
                                "para evitar marcar a entrada errada."
                            )
                finally:
                    storage.close()
                if matched and not silent:
                    snapshot_text = _format_bfbm_snapshot(tips_snapshot)
                    text += f"\n\n{snapshot_text or tips_text}{link_note}"
                    require_telegram_settings(settings)
                    asyncio.run(send_message(settings.telegram_bot_token, settings.telegram_chat_id, text))
                body = b"sent\n"
                self.send_response(200)
            except Exception as exc:
                logger.exception("Erro ao notificar aposta BFBM")
                body = f"error={type(exc).__name__}\n".encode("utf-8")
                self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/create-test":
            query = parse_qs(parsed.query)
            event_name = query.get("event", ["Flamengo v Botafogo"])[0].strip() or "Flamengo v Botafogo"
            selection = query.get("selection", ["over"])[0].strip().lower()
            if selection not in {"over", "under"}:
                selection = "over"
            try:
                line = float(query.get("line", ["2.5"])[0])
            except ValueError:
                line = 2.5
            if " v " in event_name:
                home, away = [part.strip() for part in event_name.split(" v ", 1)]
            elif " vs " in event_name:
                home, away = [part.strip() for part in event_name.split(" vs ", 1)]
            elif " x " in event_name:
                home, away = event_name, ""
            else:
                home, away = event_name, "BFBM Test"
            stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            storage = Storage(settings.database_path)
            try:
                game = GameSnapshot(
                    event_id=f"bfbm-http-test-{stamp}",
                    fixture_id=None,
                    league="BFBM TESTE",
                    home=home,
                    away=away,
                    minute=75,
                    score_home=1,
                    score_away=1,
                    stats={},
                    markets=[],
                )
                decision = Decision(
                    True,
                    99,
                    "Mais gols",
                    selection,
                    "BFBM TESTE",
                    0.0,
                    line,
                    "TIP DE TESTE BFBM: valida importacao oficial com stake.",
                    "teste",
                    f"bfbm-http-test|{stamp}|{selection}|{line:g}",
                )
                alert_id = storage.save_manual_alert(game, decision)
            finally:
                storage.close()
            body = f"created={alert_id or ''}\nevent={home} v {away}\n".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/bfbm/create-live-4":
            try:
                created_count, events = asyncio.run(create_live_bfbm_tips(settings, 4))
                body_text = (
                    f"created={created_count}\n"
                    f"live_csv=/bfbm/live-clean.csv?token={settings.bfbm_token or ''}\n"
                    + "\n".join(f"- {event}" for event in events)
                    + "\n"
                )
                body = body_text.encode("utf-8")
                self.send_response(200)
            except Exception as exc:
                logger.exception("Erro ao criar tips live BFBM")
                body = f"error={type(exc).__name__}\n".encode("utf-8")
                self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        config = BfbmConfig(
            provider=settings.bfbm_provider,
            stake=settings.bfbm_stake,
            min_price=settings.bfbm_min_price,
            max_price=settings.bfbm_max_price,
        )
        if parsed.path == "/bfbm/debug-minimal.csv":
            body = debug_minimal_csv(config).encode("utf-8-sig")
        elif parsed.path == "/bfbm/debug-event.csv":
            event_name = parse_qs(parsed.query).get("event", ["TesteGPT Debug Match"])[0].strip()
            body = debug_event_csv(config, event_name or "TesteGPT Debug Match").encode("utf-8-sig")
        elif parsed.path == "/bfbm/lab.csv":
            query = parse_qs(parsed.query)
            event_name = query.get("event", ["TesteGPT Debug Match"])[0].strip()
            mode = query.get("mode", ["3"])[0]
            body = debug_lab_csv(config, event_name or "TesteGPT Debug Match", mode).encode("utf-8-sig")
        elif parsed.path == "/bfbm/fresh.csv":
            suffix = datetime.utcnow().strftime("%H%M%S")
            body = fresh_test_csv(config, suffix).encode("utf-8-sig")
        elif parsed.path == "/bfbm/fresh-event.csv":
            event_name = parse_qs(parsed.query).get("event", ["SV Wehen v FC 08 Homburg"])[0].strip()
            body = fresh_event_csv(config, event_name or "SV Wehen v FC 08 Homburg").encode("utf-8-sig")
        elif parsed.path == "/bfbm/fresh-match.csv":
            query = parse_qs(parsed.query)
            event_name = query.get("event", ["Atletic Club Escaldes x FK Mornar"])[0].strip()
            selection_name = query.get("selection", ["FK Mornar"])[0].strip()
            body = fresh_match_odds_csv(
                config,
                event_name or "Atletic Club Escaldes x FK Mornar",
                selection_name or "FK Mornar",
            ).encode("utf-8-sig")
        elif parsed.path == "/bfbm/fresh-match-full.csv":
            query = parse_qs(parsed.query)
            event_name = query.get("event", ["Atletic Club Escaldes x FK Mornar"])[0].strip()
            selection_name = query.get("selection", ["FK Mornar"])[0].strip()
            body = fresh_match_odds_full_csv(
                config,
                event_name or "Atletic Club Escaldes x FK Mornar",
                selection_name or "FK Mornar",
            ).encode("utf-8-sig")
        elif parsed.path == "/bfbm/fresh-match-ids.csv":
            query = parse_qs(parsed.query)
            event_name = query.get("event", ["Atletic Club Escaldes x FK Mornar"])[0].strip()
            selection_name = query.get("selection", ["FK Mornar"])[0].strip()
            body = fresh_match_odds_ids_csv(
                config,
                event_name or "Atletic Club Escaldes x FK Mornar",
                selection_name or "FK Mornar",
                query.get("event_id", ["0"])[0].strip(),
                query.get("market_id", ["0"])[0].strip(),
                query.get("selection_id", ["0"])[0].strip(),
                query.get("start_time", [""])[0].strip(),
                query.get("price", [""])[0].strip(),
                query.get("selection_alias", [""])[0].strip(),
                query.get("market_name", [""])[0].strip(),
            ).encode("utf-8-sig")
        elif parsed.path == "/bfbm/fresh-match-rich.csv":
            query = parse_qs(parsed.query)
            event_name = query.get("event", ["Atletic Club Escaldes x FK Mornar"])[0].strip()
            selection_name = query.get("selection", ["FK Mornar"])[0].strip()
            body = fresh_match_odds_rich_csv(
                config,
                event_name or "Atletic Club Escaldes x FK Mornar",
                selection_name or "FK Mornar",
            ).encode("utf-8-sig")
        else:
            storage = Storage(settings.database_path)
            try:
                alerts = storage.bfbm_tips(settings.bfbm_max_tip_age_minutes)
                catalog_rows = storage.bfbm_markets(15)
            finally:
                storage.close()
            if parsed.path in {"/bfbm/live-full.csv", "/bfbm/tips.csv"}:
                rows, audits = full_rows_with_audit(alerts, config, catalog_rows, parsed.path)
                storage = Storage(settings.database_path)
                try:
                    storage.record_bfbm_export_audit(audits)
                finally:
                    storage.close()
                body = rows_to_full_csv(rows).encode("utf-8-sig")
            elif parsed.path == "/bfbm/live-rich.csv":
                body = tips_rich_csv(alerts, config, catalog_rows).encode("utf-8-sig")
            elif parsed.path == "/bfbm/live-clean.csv":
                body = tips_clean_match_odds_csv(alerts, config).encode("utf-8-sig")
            else:
                body = tips_csv(alerts, config).encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        logger.info("BFBM HTTP: " + format, *args)


def start_bfbm_server(settings) -> None:
    if not settings.bfbm_export:
        return
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), BfbmRequestHandler)
    thread = threading.Thread(target=server.serve_forever, name="bfbm-http", daemon=True)
    thread.start()
    logger.info("BFBM CSV ativo em /bfbm/tips.csv na porta %s.", settings.port)


async def load_sportmonks_live(settings, http: HttpJsonClient) -> list[dict]:
    if not settings.sportmonks_api_token:
        return []
    try:
        return await SportmonksClient(settings.sportmonks_api_token, http).live_scores()
    except httpx.HTTPStatusError as exc:
        logger.warning("Sportmonks live scores falhou com HTTP %s.", exc.response.status_code)
        return []


async def load_thestatsapi_live(settings, http: HttpJsonClient) -> list[dict]:
    if not settings.thestatsapi_key:
        return []
    try:
        return await TheStatsApiClient(settings.thestatsapi_key, http).live_matches(settings.max_live_events)
    except httpx.HTTPStatusError as exc:
        logger.warning("TheStatsAPI live matches falhou com HTTP %s.", exc.response.status_code)
        return []


async def load_totalcorner_live(settings, http: HttpJsonClient, limit: int | None = None) -> list[dict]:
    if not settings.totalcorner_token:
        return []
    try:
        live = await TotalCornerClient(settings.totalcorner_token, http).today_inplay(limit or settings.max_live_events)
        return [
            match
            for match in live
            if not is_blocked_match_type(str(match.get("l") or ""), str(match.get("h") or ""), str(match.get("a") or ""))
        ]
    except httpx.HTTPStatusError as exc:
        logger.warning("TotalCorner live matches falhou com HTTP %s.", exc.response.status_code)
        return []


async def load_totalcorner_today(settings, http: HttpJsonClient) -> list[dict]:
    if not settings.totalcorner_token:
        return []
    try:
        matches = await TotalCornerClient(settings.totalcorner_token, http).today_all(max(settings.max_live_events, 100))
        return [
            match
            for match in matches
            if not is_blocked_match_type(str(match.get("l") or ""), str(match.get("h") or ""), str(match.get("a") or ""))
        ]
    except httpx.HTTPStatusError as exc:
        logger.warning("TotalCorner today matches falhou com HTTP %s.", exc.response.status_code)
        return []


def make_sportmonks_client(settings, http: HttpJsonClient) -> SportmonksClient | None:
    if not settings.sportmonks_api_token:
        return None
    return SportmonksClient(settings.sportmonks_api_token, http)


def make_thestatsapi_client(settings, http: HttpJsonClient) -> TheStatsApiClient | None:
    if not settings.thestatsapi_key:
        return None
    return TheStatsApiClient(settings.thestatsapi_key, http)


def make_totalcorner_client(settings, http: HttpJsonClient) -> TotalCornerClient | None:
    if not settings.totalcorner_token:
        return None
    return TotalCornerClient(settings.totalcorner_token, http)


def _first_totalcorner_line(match: dict, keys: tuple[str, ...]) -> tuple[str, str] | None:
    for key in keys:
        value = match.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item not in (None, "", []):
                return key, str(item)
    return None


def totalcorner_market_status(match: dict, market_family: str) -> str:
    if not match:
        return "⚠️ Bet365/Betano não confirmadas: sem odds/link e sem match correspondente na TotalCorner."
    if market_family == "goals":
        found = _first_totalcorner_line(match, ("i_goal", "goalLine", "i_goal_h", "goalLineHalf"))
        if found:
            _, line = found
            return f"⚠️ Bet365/Betano não confirmadas. TotalCorner mostra linha live de gols {line}, mas confira se a casa abriu esse jogo."
        return "⚠️ Bet365/Betano não confirmadas: TotalCorner também não trouxe linha live de gols."
    if market_family == "corners":
        found = _first_totalcorner_line(
            match,
            ("i_corner", "cornerLine", "asian_corner", "asianCorner", "i_corner_h", "cornerLineHalf"),
        )
        if found:
            _, line = found
            return f"⚠️ Bet365/Betano não confirmadas. TotalCorner mostra linha live de escanteios {line}, mas confira se a casa abriu esse jogo."
        return "⚠️ Bet365/Betano não confirmadas: TotalCorner também não trouxe linha live de escanteios."
    return "⚠️ Bet365/Betano não confirmadas: conferir manualmente antes de entrar."


async def fixture_stats_with_sportmonks_fallback(
    fixture: dict,
    api_football: ApiFootballClient,
    sportmonks_live: list[dict],
    sportmonks_client: SportmonksClient | None = None,
    thestatsapi_live: list[dict] | None = None,
    thestatsapi_client: TheStatsApiClient | None = None,
    totalcorner_live: list[dict] | None = None,
) -> dict:
    fixture_id = fixture.get("fixture", {}).get("id")
    totalcorner_match = find_matching_totalcorner_match(fixture, totalcorner_live or []) if totalcorner_live else None
    totalcorner_stats = compact_totalcorner_statistics(totalcorner_match or {}) if totalcorner_match else {}
    if has_actionable_stats(totalcorner_stats):
        return totalcorner_stats
    api_stats = compact_statistics(await api_football.fixture_statistics(int(fixture_id))) if fixture_id else {}
    sportmonks_fixture = find_matching_sportmonks_fixture(fixture, sportmonks_live) if sportmonks_live else None
    sportmonks_stats = compact_sportmonks_statistics(sportmonks_fixture or {}) if sportmonks_fixture else {}
    if not has_actionable_stats(sportmonks_stats) and sportmonks_fixture and sportmonks_client:
        sportmonks_fixture_id = sportmonks_fixture.get("id")
        if sportmonks_fixture_id:
            try:
                detailed = await sportmonks_client.fixture_by_id(int(sportmonks_fixture_id))
                sportmonks_stats = compact_sportmonks_statistics(detailed or {})
            except httpx.HTTPStatusError as exc:
                logger.warning("Sportmonks fixture %s falhou com HTTP %s.", sportmonks_fixture_id, exc.response.status_code)
    if has_actionable_stats(sportmonks_stats):
        return sportmonks_stats
    if has_actionable_stats(api_stats):
        return api_stats
    player_stats = compact_player_statistics(await api_football.fixture_players(int(fixture_id))) if fixture_id else {}
    if has_actionable_stats(player_stats):
        return player_stats
    ts_match = find_matching_thestatsapi_match(fixture, thestatsapi_live or []) if thestatsapi_live else None
    if ts_match and thestatsapi_client:
        try:
            ts_stats_raw = await thestatsapi_client.match_stats(str(ts_match.get("id")))
            ts_stats = compact_thestatsapi_statistics(ts_match, ts_stats_raw or {})
            if has_actionable_stats(ts_stats):
                return ts_stats
        except httpx.HTTPStatusError as exc:
            logger.warning("TheStatsAPI match stats falhou com HTTP %s.", exc.response.status_code)
    return api_stats


async def build_snapshots(settings, odds_api: OddsApiClient, api_football: ApiFootballClient) -> list[GameSnapshot]:
    football_fixtures = await api_football.live_fixtures()
    if not football_fixtures:
        logger.info("API-Football nao retornou jogos ao vivo.")
        return []

    try:
        odds_events = await odds_api.live_events(settings.sport, settings.max_live_events)
    except httpx.HTTPStatusError as exc:
        logger.warning("Odds-API live events falhou com HTTP %s; continuando sem odds.", exc.response.status_code)
        odds_events = []
    sportmonks_live = await load_sportmonks_live(settings, odds_api.http)
    sportmonks_client = make_sportmonks_client(settings, odds_api.http)
    thestatsapi_live = await load_thestatsapi_live(settings, odds_api.http)
    thestatsapi_client = make_thestatsapi_client(settings, odds_api.http)
    totalcorner_live = await load_totalcorner_live(settings, odds_api.http)
    fixture_event_pairs: list[tuple[dict, dict | None]] = []
    used_event_ids: set[str] = set()
    for fixture in football_fixtures[: max(settings.odds_detail_limit, 10)]:
        event = find_matching_odds_event(fixture, odds_events)
        event_id = str(event.get("id") or "") if event else ""
        if event_id:
            if event_id in used_event_ids:
                continue
            used_event_ids.add(event_id)
        fixture_event_pairs.append((fixture, event))

    snapshots: list[GameSnapshot] = []

    for fixture, event in fixture_event_pairs:
        event_id = str(event.get("id") or "") if event else ""
        fixture_id = fixture.get("fixture", {}).get("id") if fixture else None
        totalcorner_match = find_matching_totalcorner_match(fixture, totalcorner_live or []) if totalcorner_live else None
        stats = await fixture_stats_with_sportmonks_fallback(
            fixture, api_football, sportmonks_live, sportmonks_client, thestatsapi_live, thestatsapi_client, totalcorner_live
        )
        score_home, score_away = extract_score(fixture)
        fixture_league = fixture.get("league", {}) if fixture else {}
        league = fixture_league.get("name") or (
            event.get("league", {}).get("name") if event and isinstance(event.get("league"), dict) else event.get("league", "") if event else ""
        )
        teams = fixture.get("teams", {}) if fixture else {}
        snapshots.append(
            GameSnapshot(
                event_id=event_id,
                fixture_id=fixture_id,
                league=str(league or ""),
                home=str(teams.get("home", {}).get("name") or (event.get("home") if event else "") or ""),
                away=str(teams.get("away", {}).get("name") or (event.get("away") if event else "") or ""),
                minute=extract_minute(fixture),
                score_home=score_home,
                score_away=score_away,
                stats=stats,
                markets=[],
                totalcorner_match=totalcorner_match or {},
            )
        )
    return snapshots


async def process_once(settings, storage: Storage, *, send_alerts: bool = True) -> int:
    require_runtime_settings(settings)
    http = HttpJsonClient()
    try:
        odds_api = OddsApiClient(settings.odds_api_key, http)
        api_football = ApiFootballClient(settings.api_football_key, http)
        sent = 0
        snapshots = await build_snapshots(settings, odds_api, api_football)
        bfbm_snapshots = await build_bfbm_totalcorner_snapshots(settings, http, storage)
        if bfbm_snapshots:
            existing_keys = {f"{game.home.casefold()} x {game.away.casefold()}" for game in snapshots}
            for game in bfbm_snapshots:
                key = f"{game.home.casefold()} x {game.away.casefold()}"
                if key not in existing_keys:
                    snapshots.append(game)
                    existing_keys.add(key)
        odds_payloads: dict[str, dict] = {}
        if settings.odds_use_multi:
            event_ids = [game.event_id for game in snapshots if game.event_id and not str(game.event_id).startswith("tc-bfbm-")]
            for index in range(0, len(event_ids), 10):
                chunk = event_ids[index : index + 10]
                try:
                    for payload in await odds_api.odds_multi(chunk, settings.bookmakers):
                        event_id = str(payload.get("id") or payload.get("eventId") or "")
                        if event_id:
                            odds_payloads[event_id] = payload
                except httpx.HTTPStatusError as exc:
                    logger.warning("Odds multi falhou com HTTP %s; usando fallback individual.", exc.response.status_code)

        for game in snapshots:
            if not has_actionable_stats(game.stats):
                logger.info("Sem estatisticas suficientes para %s x %s.", game.home, game.away)
                continue
            bfbm_source = str(game.event_id or "").startswith("tc-bfbm-")
            bfbm_catalog_rows = storage.bfbm_markets(15) if bfbm_source else []
            bfbm_available_markets = (
                bfbm_available_market_specs(bfbm_catalog_rows, f"{game.home} x {game.away}")
                if bfbm_source
                else None
            )
            required_confidence = settings.min_confidence
            if is_high_variance_match(game.league, game.home, game.away):
                required_confidence = max(required_confidence, 85)
            if game.minute is not None and game.minute < 25:
                required_confidence = max(required_confidence, 85)
            math_signal = evaluate_game(
                minute=game.minute,
                score_home=game.score_home,
                score_away=game.score_away,
                stats=game.stats,
                min_confidence=required_confidence,
                available_markets=bfbm_available_markets,
            )
            if not math_signal.approved:
                logger.info("Motor matematico bloqueou %s x %s: %s", game.home, game.away, math_signal.reason)
                if bfbm_source:
                    _record_bfbm_candidate_audit(
                        storage,
                        game,
                        math_signal,
                        status="SKIPPED",
                        reason=f"math_blocked:{math_signal.strategy}",
                    )
                continue
            if bfbm_source:
                bfbm_market = find_bfbm_market(
                    bfbm_catalog_rows,
                    f"{game.home} x {game.away}",
                    math_signal.market_family,
                    math_signal.line,
                )
                if not bfbm_market:
                    logger.info(
                        "BFBM bloqueou %s x %s: mercado %s linha %s nao existe no catalogo.",
                        game.home,
                        game.away,
                        math_signal.market_family,
                        math_signal.line,
                    )
                    _record_bfbm_candidate_audit(
                        storage,
                        game,
                        math_signal,
                        status="BLOCKED",
                        reason="candidate_no_bfbm_market_match",
                    )
                    continue
                _record_bfbm_candidate_audit(
                    storage,
                    game,
                    math_signal,
                    status="CANDIDATE",
                    reason="candidate_has_bfbm_market",
                    bfbm_market=bfbm_market,
                )
            idea = await suggest_market_without_odds(
                game,
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                min_confidence=required_confidence,
            )
            target_family = math_signal.market_family
            target_selection = math_signal.selection
            target_line = math_signal.line
            target_confidence = math_signal.confidence
            target_reason = math_signal.reason
            target_stake = "baixa"
            if idea.should_check_odds and idea.market_family == math_signal.market_family and idea.selection == math_signal.selection:
                target_confidence = max(math_signal.confidence, idea.confidence)
                target_reason = f"{math_signal.reason} IA: {idea.reason}"
                target_stake = idea.stake
            else:
                logger.info(
                    "IA nao confirmou o sinal matematico em %s x %s; seguindo pelo motor matematico. IA: %s",
                    game.home,
                    game.away,
                    idea.reason,
                )
            odds_payload = None
            if game.event_id and not bfbm_source:
                odds_payload = odds_payloads.get(game.event_id)
                if odds_payload is None:
                    try:
                        odds_payload = await odds_api.odds(game.event_id, settings.bookmakers)
                    except httpx.HTTPStatusError as exc:
                        logger.warning("Odds apos sinal matematico falhou para %s com HTTP %s.", game.event_id, exc.response.status_code)
            markets = flatten_markets(odds_payload or {}, fixture_id=game.fixture_id, min_odd=settings.min_odd)
            compatible = [market for market in markets if market_matches_idea(market, target_family, target_selection, target_line)]
            if not compatible:
                logger.info("Sem odd >= %.2f para sinal %s/%s em %s x %s", settings.min_odd, target_family, target_selection, game.home, game.away)
                if not settings.hybrid_no_odds:
                    continue
                alert_key = verified_alert_key(game, target_family, target_selection, target_line)
                decision = Decision(
                    True,
                    target_confidence,
                    market_label(target_family, target_selection),
                    target_selection,
                    "Conferir manualmente",
                    0.0,
                    target_line,
                    target_reason,
                    target_stake,
                    alert_key,
                    market_status=totalcorner_market_status(game.totalcorner_match, target_family),
                    strategy=math_signal.strategy,
                )
            else:
                chosen = sorted(compatible, key=lambda market: market.odd, reverse=True)[0]
                bookmaker_links = {}
                for market in sorted(compatible, key=lambda item: item.odd, reverse=True):
                    if market.link_url and market.bookmaker not in bookmaker_links:
                        bookmaker_links[market.bookmaker] = market.link_url
                decision = Decision(
                    True,
                    target_confidence,
                    chosen.market_name,
                    chosen.selection,
                    chosen.bookmaker,
                    chosen.odd,
                    chosen.line or target_line,
                    target_reason,
                    target_stake,
                    odds_alert_key(game, target_family, target_selection, chosen.line or target_line),
                    bookmaker_links,
                    strategy=math_signal.strategy,
                )
            if storage.seen_alert(decision.alert_key):
                logger.info("Entrada repetida ignorada: %s", decision.alert_key)
                continue
            if storage.seen_recent_game_alert(game, settings.game_cooldown_minutes):
                logger.info(
                    "Entrada ignorada por cooldown de %s min no jogo %s x %s.",
                    settings.game_cooldown_minutes,
                    game.home,
                    game.away,
                )
                continue
            if storage.seen_similar_alert(game, decision):
                logger.info(
                    "Entrada similar repetida ignorada: %s x %s | %s %s %s",
                    game.home,
                    game.away,
                    decision.market,
                    decision.selection,
                    decision.line,
                )
                continue
            if decision.odd > 0:
                alert_id = storage.save_alert(game, decision)
            else:
                alert_id = storage.save_manual_alert(game, decision)
            if alert_id is None:
                logger.info("Entrada repetida ignorada apos insert: %s", decision.alert_key)
                continue
            message = format_alert(game, decision)
            if settings.bfbm_export:
                logger.info("Alerta salvo para BFBM sem Telegram ate confirmacao de aposta:\n%s", message)
            elif settings.dry_run or not send_alerts:
                logger.info("DRY_RUN alerta:\n%s", message)
            else:
                await send_message(
                    settings.telegram_bot_token,
                    settings.telegram_chat_id,
                    message,
                    with_bookmakers=bool(decision.bookmaker_links),
                    bookmaker_links=decision.bookmaker_links,
                    alert_id=alert_id,
                )
            sent += 1

        pending_alerts = storage.pending_alerts()
        totalcorner_today = []
        needs_totalcorner_settlement = any(
            not alert.get("fixture_id")
            or "corner" in str(alert.get("market", "")).lower()
            or "escanteio" in str(alert.get("market", "")).lower()
            for alert in pending_alerts
        )
        if needs_totalcorner_settlement:
            totalcorner_today = await load_totalcorner_today(settings, http)
        for alert in pending_alerts:
            result = await settle_alert(alert, api_football, totalcorner_today)
            if result:
                storage.settle_alert(int(alert["id"]), result[0], result[1])
        if not settings.dry_run:
            result_notifications = storage.pending_result_notifications()
            if result_notifications:
                require_telegram_settings(settings)
                for alert in result_notifications:
                    await send_message(
                        settings.telegram_bot_token,
                        settings.telegram_chat_id,
                        _format_result_notification(alert),
                    )
                    storage.mark_result_notified(int(alert["id"]))
        return sent
    finally:
        await http.close()


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    try:
        perf = storage.performance()
        db_status = database_storage_status(settings)
        db_mode = "persistente" if db_status["persistent"] else "TEMPORARIO - corrigir volume"
        await update.message.reply_text(
            f"Status: online\n"
            f"Banco: {db_mode}\n"
            f"Banco path: {db_status['database_path']}\n"
            f"BFBM: {'ativo' if settings.bfbm_export else 'inativo'}\n"
            f"Apostadas: {perf['actions'].get('BET', 0)}\n"
            f"Pendentes: {perf['actions'].get('PENDING', 0)}\n"
            f"Ignoradas: {perf['actions'].get('IGNORED', 0)}\n"
            f"Resultados: {perf['summary']}\n"
            f"Win rate: {perf['win_rate']}%\n"
            f"Lucro unidades: {perf['profit_units']}"
        )
    finally:
        storage.close()


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")


async def last_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    try:
        alerts = storage.last_alerts(5)
        if not alerts:
            await update.message.reply_text("Ainda nao ha entradas salvas.")
            return
        lines = []
        for alert in alerts:
            lines.append(
                f"{alert['created_at']} | {alert['home']} x {alert['away']} | {alert['market']} {alert['selection']} "
                f"{alert['line']} @ {alert['odd']} | {alert.get('user_action', 'PENDING')} | {alert['status']}"
            )
        await update.message.reply_text("\n".join(lines))
    finally:
        storage.close()


async def performance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await status_cmd(update, context)


async def _mark_alert_message(query, label: str) -> None:
    if not query.message:
        return
    text = query.message.text or ""
    status_line = f"Status: {label}"
    if "Status: APOSTEI" in text or "Status: IGNORADA" in text:
        new_text = text
    else:
        new_text = f"{text}\n\n{status_line}" if text else status_line
    try:
        await query.edit_message_text(text=new_text, reply_markup=None)
    except BadRequest as exc:
        logger.warning("Nao foi possivel editar alerta marcado: %s", exc)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
            logger.exception("Nao foi possivel remover botoes do alerta.")


async def alert_action_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    action, _, raw_id = query.data.partition(":")
    if action not in {"bet", "ignore"} or not raw_id.isdigit():
        await query.answer("Acao invalida.")
        return
    settings = load_settings()
    storage = Storage(settings.database_path)
    try:
        saved = storage.set_user_action(int(raw_id), "BET" if action == "bet" else "IGNORED")
    finally:
        storage.close()
    if not saved:
        await query.answer("Entrada nao encontrada.")
        return
    if action == "bet":
        await query.answer("Registrado: voce apostou.")
        await _mark_alert_message(query, "APOSTEI")
    else:
        await query.answer("Registrado: entrada ignorada.")
        await _mark_alert_message(query, "IGNORADA")


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    try:
        await update.message.reply_text("Forcando varredura agora...")
        sent = await process_once(settings, storage)
        await update.message.reply_text(f"Varredura concluida. Alertas enviados: {sent}")
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP ao forcar varredura: %s", exc.response.status_code)
        if exc.response.status_code == 429:
            await update.message.reply_text(
                "Odds-API retornou 429 Too Many Requests. Isso geralmente e limite da Odds-API, muitas vezes por hora. "
                "A varredura principal agora tenta continuar sem odds quando esse erro acontece."
            )
        else:
            await update.message.reply_text(f"Erro HTTP ao forcar varredura: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro ao forcar varredura")
        await update.message.reply_text(f"Erro ao forcar varredura: {type(exc).__name__}")
    finally:
        storage.close()


async def bfbm_test_tip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    event_name = " ".join(context.args).strip() if context.args else "Flamengo v Palmeiras"
    if " v " in event_name:
        home, away = [part.strip() for part in event_name.split(" v ", 1)]
    elif " vs " in event_name:
        home, away = [part.strip() for part in event_name.split(" vs ", 1)]
    elif " x " in event_name:
        home, away = event_name, ""
    else:
        home, away = event_name, "BFBM Test"
    try:
        game = GameSnapshot(
            event_id=f"bfbm-test-{stamp}",
            fixture_id=None,
            league="BFBM TESTE",
            home=home,
            away=away,
            minute=75,
            score_home=1,
            score_away=1,
            stats={},
            markets=[],
        )
        decision = Decision(
            True,
            99,
            "Mais gols",
            "over",
            "BFBM TESTE",
            0.0,
            2.5,
            "TIP DE TESTE BFBM: usada apenas para validar importacao CSV no BF Bot Manager.",
            "teste",
            f"bfbm-test|{stamp}|over|2.5",
        )
        alert_id = storage.save_manual_alert(game, decision)
        if alert_id is None:
            await update.message.reply_text("Tip de teste BFBM ja existe na janela atual. Ela deve aparecer no CSV.")
            return
        await update.message.reply_text(
            "Tip de teste BFBM criada.\n\n"
            f"Evento no CSV: {home} v {away}\n"
            "Agora mande o BFBM importar a URL novamente. Ela expira conforme BFBM_MAX_TIP_AGE_MINUTES."
        )
    finally:
        storage.close()


async def bfbm_test_simple_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    try:
        game = GameSnapshot(
            event_id=f"bfbm-simple-{stamp}",
            fixture_id=None,
            league="BFBM TESTE SIMPLES",
            home="",
            away="",
            minute=75,
            score_home=1,
            score_away=1,
            stats={},
            markets=[],
        )
        decision = Decision(
            True,
            99,
            "Mais gols",
            "over",
            "BFBM TESTE",
            0.0,
            2.5,
            "TIP DE TESTE SIMPLES BFBM: valida importacao CSV sem EventName.",
            "teste",
            f"bfbm-simple|{stamp}|over|2.5",
        )
        alert_id = storage.save_manual_alert(game, decision)
        if alert_id is None:
            await update.message.reply_text("Tip simples BFBM ja existe. Ela deve aparecer no CSV.")
            return
        await update.message.reply_text(
            "Tip simples BFBM criada.\n\n"
            "Ela vai sem EventName para testar se o BFBM importa o CSV pelo MarketType/SelectionName."
        )
    finally:
        storage.close()


async def force_live_alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Buscando jogo ao vivo para alerta de teste...")
        odds_api = OddsApiClient(settings.odds_api_key, http)
        api_football = ApiFootballClient(settings.api_football_key, http)
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("Nao encontrei jogos ao vivo agora na API-Football.")
            return
        try:
            live_events = await odds_api.live_events(settings.sport, settings.max_live_events)
        except httpx.HTTPStatusError as exc:
            logger.warning("Live events falhou com HTTP %s.", exc.response.status_code)
            if exc.response.status_code == 429:
                await update.message.reply_text("Odds-API retornou 429 Too Many Requests. Aguarde alguns minutos e tente de novo.")
            else:
                await update.message.reply_text(f"Odds-API retornou HTTP {exc.response.status_code} ao buscar jogos ao vivo.")
            return
        if not live_events:
            await update.message.reply_text("A API-Football tem jogos ao vivo, mas a Odds-API nao retornou eventos ao vivo para comparar odds.")
            return

        for fixture in fixtures:
            event = find_matching_odds_event(fixture, live_events)
            if not event:
                continue
            event_id = str(event.get("id") or "")
            if not event_id:
                continue
            fixture_id = fixture.get("fixture", {}).get("id") if fixture else None
            try:
                odds_payload = await odds_api.odds(event_id, settings.bookmakers)
            except httpx.HTTPStatusError as exc:
                logger.warning("Force live alert odds falhou para %s com HTTP %s.", event_id, exc.response.status_code)
                continue
            if not odds_payload:
                continue
            markets = flatten_all_markets(odds_payload, fixture_id=fixture_id, min_odd=1.01)
            if not markets:
                continue
            market = sorted(markets, key=lambda item: item.odd, reverse=True)[0]
            score_home, score_away = extract_score(fixture)
            fixture_league = fixture.get("league", {}) if fixture else {}
            league = fixture_league.get("name") or (event.get("league", {}).get("name") if isinstance(event.get("league"), dict) else event.get("league", ""))
            teams = fixture.get("teams", {}) if fixture else {}
            minute = extract_minute(fixture)
            score = f"{score_home if score_home is not None else '?'}x{score_away if score_away is not None else '?'}"
            line = "" if market.line is None else f"\nLinha: {market.line}"
            await update.message.reply_text(
                "ALERTA DE TESTE - JOGO AO VIVO\n\n"
                "Este alerta foi forcado apenas para validar o envio. Nao e recomendacao oficial da IA.\n\n"
                f"Jogo: {teams.get('home', {}).get('name') or event.get('home', '')} x {teams.get('away', {}).get('name') or event.get('away', '')}\n"
                f"Liga: {league or '-'}\n"
                f"Tempo: {minute if minute is not None else '?'}'\n"
                f"Placar: {score}\n"
                f"Mercado: {market.market_name}\n"
                f"Selecao: {market.selection}{line}\n"
                f"Odd: {market.odd:.2f}\n"
                f"Casa: {market.bookmaker}"
            )
            return

        await update.message.reply_text("Encontrei jogos ao vivo na API-Football, mas nenhum casou com odds utilizaveis agora.")
    except Exception as exc:
        logger.exception("Erro ao forcar alerta ao vivo")
        await update.message.reply_text(f"Erro ao forcar alerta ao vivo: {type(exc).__name__}")
    finally:
        await http.close()


async def force_home_win_test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Buscando teste de vitoria da casa com odd >= 1.80...")
        odds_api = OddsApiClient(settings.odds_api_key, http)
        live_events = await odds_api.live_events(settings.sport, settings.max_live_events)
        if not live_events:
            await update.message.reply_text("Odds-API nao retornou jogos ao vivo agora.")
            return

        forbidden_events = 0
        checked_events = 0
        for event in live_events[: settings.odds_detail_limit]:
            event_id = str(event.get("id") or "")
            if not event_id:
                continue
            checked_events += 1
            try:
                odds_payload = await odds_api.odds(event_id, settings.bookmakers)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 403:
                    raise
                forbidden_events += 1
                try:
                    odds_payload = await odds_api.odds(event_id, [], include_links=False)
                except httpx.HTTPStatusError as fallback_exc:
                    if fallback_exc.response.status_code != 403:
                        raise
                    continue
            markets = flatten_all_markets(odds_payload or {}, fixture_id=None, min_odd=settings.min_odd)
            home_markets = [
                market
                for market in markets
                if market.selection == "home"
                and any(word in market.market_name.lower() for word in ("winner", "moneyline", "match odds", "1x2", "h2h"))
            ]
            if not home_markets:
                continue
            chosen = sorted(home_markets, key=lambda market: market.odd, reverse=True)[0]
            bookmaker_links = {}
            for market in sorted(home_markets, key=lambda item: item.odd, reverse=True):
                if market.link_url and market.bookmaker not in bookmaker_links:
                    bookmaker_links[market.bookmaker] = market.link_url
            home = str(event.get("home") or event.get("homeTeam") or "Casa")
            away = str(event.get("away") or event.get("awayTeam") or "Fora")
            league_data = event.get("league", {}) if isinstance(event.get("league"), dict) else {}
            league = str(league_data.get("name") or event.get("league") or "-")
            game = GameSnapshot(
                event_id=event_id,
                fixture_id=None,
                league=league,
                home=home,
                away=away,
                minute=None,
                score_home=None,
                score_away=None,
                stats={},
                markets=[],
            )
            decision = Decision(
                True,
                0,
                "Vitoria casa",
                "home",
                chosen.bookmaker,
                chosen.odd,
                chosen.line,
                "TESTE FORCADO: usado apenas para validar alerta, odds e botao direto da casa. Nao e entrada oficial.",
                "teste",
                chosen.alert_key,
                bookmaker_links,
            )
            message = "TESTE DE ENVIO - NAO APOSTAR\n\n" + format_alert(game, decision)
            await send_message(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                message,
                with_bookmakers=True,
                bookmaker_links=decision.bookmaker_links,
            )
            link_count = len(decision.bookmaker_links)
            suffix = "" if link_count else " A Odds-API retornou odds, mas sem deep-link liberado para este plano/evento."
            await update.message.reply_text(f"Teste enviado. Links diretos recebidos da Odds-API: {link_count}.{suffix}")
            return

        await update.message.reply_text(
            "Nao achei vitoria da casa com odd >= 1.80 nos jogos ao vivo retornados pela Odds-API. "
            f"Eventos checados: {checked_events}. Bloqueios 403: {forbidden_events}."
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP no teste de vitoria casa: %s", exc.response.status_code)
        await update.message.reply_text(f"Erro HTTP no teste de vitoria casa: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro no teste de vitoria casa")
        await update.message.reply_text(f"Erro no teste de vitoria casa: {type(exc).__name__}")
    finally:
        await http.close()


async def envcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    presence = settings_presence(settings)
    lines = ["Variaveis vistas pelo bot:"]
    for name, ok in presence.items():
        lines.append(f"{name}: {'OK' if ok else 'AUSENTE'}")
    db_status = database_storage_status(settings)
    lines.extend(
        [
            "",
            "Banco:",
            f"persistente: {'SIM' if db_status['persistent'] else 'NAO'}",
            f"path: {db_status['database_path']}",
            f"volume: {db_status['volume_mount_path'] or '-'}",
            f"motivo: {db_status['reason']}",
        ]
    )
    await update.message.reply_text("\n".join(lines))


async def test_analysis_no_odds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Testando analise sem odds, usando apenas jogos ao vivo da API-Football...")
        api_football = ApiFootballClient(settings.api_football_key, http)
        sportmonks_live = await load_sportmonks_live(settings, http)
        sportmonks_client = make_sportmonks_client(settings, http)
        thestatsapi_live = await load_thestatsapi_live(settings, http)
        thestatsapi_client = make_thestatsapi_client(settings, http)
        totalcorner_live = await load_totalcorner_live(settings, http)
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("A API-Football nao retornou jogos ao vivo agora.")
            return

        fixture = fixtures[0]
        fixture_id = fixture.get("fixture", {}).get("id")
        stats = await fixture_stats_with_sportmonks_fallback(
            fixture, api_football, sportmonks_live, sportmonks_client, thestatsapi_live, thestatsapi_client, totalcorner_live
        )
        score_home, score_away = extract_score(fixture)
        teams = fixture.get("teams", {})
        league = fixture.get("league", {}).get("name", "")
        game = GameSnapshot(
            event_id="api-football-only",
            fixture_id=fixture_id,
            league=str(league or ""),
            home=str(teams.get("home", {}).get("name") or ""),
            away=str(teams.get("away", {}).get("name") or ""),
            minute=extract_minute(fixture),
            score_home=score_home,
            score_away=score_away,
            stats=stats,
            markets=[],
        )
        analysis = await analyze_live_game_without_odds(game, api_key=settings.openai_api_key, model=settings.openai_model)
        minute = "?" if game.minute is None else f"{game.minute}'"
        score = f"{game.score_home if game.score_home is not None else '?'}x{game.score_away if game.score_away is not None else '?'}"
        await update.message.reply_text(
            "TESTE DE ANALISE - SEM ODDS\n\n"
            f"Jogo: {game.home} x {game.away}\n"
            f"Liga: {game.league or '-'}\n"
            f"Tempo: {minute}\n"
            f"Placar: {score}\n\n"
            f"Analise:\n{analysis}"
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP no teste sem odds: %s", exc.response.status_code)
        await update.message.reply_text(f"Erro HTTP no teste sem odds: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro no teste de analise sem odds")
        await update.message.reply_text(f"Erro no teste de analise sem odds: {type(exc).__name__}")
    finally:
        await http.close()


async def official_no_odds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Buscando entrada oficial sem consultar odds...")
        api_football = ApiFootballClient(settings.api_football_key, http)
        sportmonks_live = await load_sportmonks_live(settings, http)
        sportmonks_client = make_sportmonks_client(settings, http)
        thestatsapi_live = await load_thestatsapi_live(settings, http)
        thestatsapi_client = make_thestatsapi_client(settings, http)
        totalcorner_live = await load_totalcorner_live(settings, http)
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("A API-Football nao retornou jogos ao vivo agora.")
            return

        for fixture in fixtures[: settings.odds_detail_limit]:
            fixture_id = fixture.get("fixture", {}).get("id")
            if not fixture_id:
                continue
            stats = await fixture_stats_with_sportmonks_fallback(
                fixture, api_football, sportmonks_live, sportmonks_client, thestatsapi_live, thestatsapi_client, totalcorner_live
            )
            score_home, score_away = extract_score(fixture)
            teams = fixture.get("teams", {})
            league = fixture.get("league", {}).get("name", "")
            game = GameSnapshot(
                event_id=f"api-football-{fixture_id}",
                fixture_id=fixture_id,
                league=str(league or ""),
                home=str(teams.get("home", {}).get("name") or ""),
                away=str(teams.get("away", {}).get("name") or ""),
                minute=extract_minute(fixture),
                score_home=score_home,
                score_away=score_away,
                stats=stats,
                markets=[],
            )
            if not has_actionable_stats(game.stats):
                logger.info("Pulando %s x %s: estatisticas detalhadas insuficientes.", game.home, game.away)
                continue
            required_confidence = settings.min_confidence
            caution_notes = []
            if is_high_variance_match(game.league, game.home, game.away):
                required_confidence = max(required_confidence, 85)
                caution_notes.append("jogo de maior variancia")
            if game.minute is not None and game.minute < 25:
                required_confidence = max(required_confidence, 85)
                caution_notes.append("jogo muito cedo")
            math_signal = evaluate_game(
                minute=game.minute,
                score_home=game.score_home,
                score_away=game.score_away,
                stats=game.stats,
                min_confidence=required_confidence,
            )
            if not math_signal.approved:
                logger.info("Motor matematico bloqueou entrada sem odds em %s x %s: %s", game.home, game.away, math_signal.reason)
                continue
            idea = await suggest_market_without_odds(
                game,
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                min_confidence=required_confidence,
            )
            if idea.should_check_odds and idea.market_family == math_signal.market_family and idea.selection == math_signal.selection:
                final_confidence = max(math_signal.confidence, idea.confidence)
                final_reason = f"{math_signal.reason} IA: {idea.reason}"
                final_stake = idea.stake
            else:
                logger.info(
                    "Entrada sem odds seguindo motor matematico em %s x %s; IA nao confirmou: %s",
                    game.home,
                    game.away,
                    idea.reason,
                )
                final_confidence = math_signal.confidence
                final_reason = math_signal.reason
                final_stake = "baixa"

            market_label_text = market_label(math_signal.market_family, math_signal.selection)
            final_line = math_signal.line
            alert_key = verified_alert_key(game, math_signal.market_family, math_signal.selection, final_line)
            if storage.seen_alert(alert_key):
                await update.message.reply_text("O motor encontrou uma entrada sem odds, mas ela ja foi enviada antes.")
                return

            decision = Decision(
                True,
                final_confidence,
                market_label_text,
                math_signal.selection,
                "Conferir manualmente",
                0.0,
                final_line,
                final_reason,
                final_stake,
                alert_key,
                strategy=math_signal.strategy,
            )
            alert_id = storage.save_manual_alert(game, decision)
            minute = "?" if game.minute is None else f"{game.minute}'"
            score = f"{game.score_home if game.score_home is not None else '?'}x{game.score_away if game.score_away is not None else '?'}"
            line_label = "" if final_line is None else f" {final_line:g}"
            await update.message.reply_text(
                "ENTRADA OFICIAL - SEM ODD\n\n"
                "A IA escolheu o mercado pela leitura do jogo ao vivo. Confira a odd manualmente antes de entrar.\n\n"
                f"Jogo: {game.home} x {game.away}\n"
                f"Liga: {game.league or '-'}\n"
                f"Tempo: {minute}\n"
                f"Placar: {score}\n"
                f"Mercado indicado: {market_label_text}{line_label}\n"
                f"Direcao: {math_signal.selection}\n"
                f"Probabilidade Poisson: {math_signal.probability:.0%}\n"
                f"Score de conviccao: {final_confidence}/100\n"
                f"Estrategia: {math_signal.strategy}\n"
                f"Stake: {final_stake}\n"
                f"Filtro aplicado: confianca minima {required_confidence}%"
                f"{' (' + ', '.join(caution_notes) + ')' if caution_notes else ''}\n\n"
                f"Estatisticas usadas:\n{compact_stats_summary(game.stats)}\n\n"
                f"Motivo matematico: {math_signal.reason}\n"
                f"Leitura IA: {idea.reason}",
                reply_markup=alert_keyboard(alert_id=alert_id),
            )
            return

        await update.message.reply_text(
            "Nao enviei entrada oficial: os jogos ao vivo nao tinham estatisticas suficientes ou confianca minima para apostar com criterio."
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP na entrada sem odds: %s", exc.response.status_code)
        await update.message.reply_text(f"Erro HTTP na entrada sem odds: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro na entrada oficial sem odds")
        await update.message.reply_text(f"Erro na entrada oficial sem odds: {type(exc).__name__}")
    finally:
        await http.close()
        storage.close()


async def force_verified_entry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Buscando entrada veridica em jogos ao vivo...")
        api_football = ApiFootballClient(settings.api_football_key, http)
        sportmonks_live = await load_sportmonks_live(settings, http)
        sportmonks_client = make_sportmonks_client(settings, http)
        thestatsapi_live = await load_thestatsapi_live(settings, http)
        thestatsapi_client = make_thestatsapi_client(settings, http)
        totalcorner_live = await load_totalcorner_live(settings, http)
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("Nao ha jogos ao vivo agora na API-Football.")
            return

        candidates = []
        for fixture in fixtures[: max(settings.odds_detail_limit, 10)]:
            fixture_id = fixture.get("fixture", {}).get("id")
            if not fixture_id:
                continue
            stats = await fixture_stats_with_sportmonks_fallback(
                fixture, api_football, sportmonks_live, sportmonks_client, thestatsapi_live, thestatsapi_client, totalcorner_live
            )
            score_home, score_away = extract_score(fixture)
            teams = fixture.get("teams", {})
            league = fixture.get("league", {}).get("name", "")
            game = GameSnapshot(
                event_id=f"verified-api-football-{fixture_id}",
                fixture_id=fixture_id,
                league=str(league or ""),
                home=str(teams.get("home", {}).get("name") or ""),
                away=str(teams.get("away", {}).get("name") or ""),
                minute=extract_minute(fixture),
                score_home=score_home,
                score_away=score_away,
                stats=stats,
                markets=[],
            )
            if not has_actionable_stats(game.stats):
                continue
            required_confidence = settings.min_confidence
            if is_high_variance_match(game.league, game.home, game.away):
                required_confidence = max(required_confidence, 85)
            if game.minute is not None and game.minute < 25:
                required_confidence = max(required_confidence, 85)
            signal = evaluate_game(
                minute=game.minute,
                score_home=game.score_home,
                score_away=game.score_away,
                stats=game.stats,
                min_confidence=required_confidence,
            )
            if signal.approved:
                candidates.append((signal, game, required_confidence))

        if not candidates:
            await update.message.reply_text(
                "Nao ha entrada veridica agora: os jogos ao vivo nao passaram nos filtros de estatistica e probabilidade."
            )
            return

        signal, game, required_confidence = sorted(candidates, key=lambda item: (item[0].score, item[0].probability), reverse=True)[0]
        market_label_text = market_label(signal.market_family, signal.selection)
        alert_key = verified_alert_key(game, signal.market_family, signal.selection, signal.line)
        if storage.seen_alert(alert_key):
            await update.message.reply_text("A melhor entrada veridica encontrada ja foi enviada antes.")
            return

        ai_reading = await analyze_live_game_without_odds(game, api_key=settings.openai_api_key, model=settings.openai_model)
        decision = Decision(
            True,
            signal.confidence,
            market_label_text,
            signal.selection,
            "Conferir manualmente",
            0.0,
            signal.line,
            signal.reason,
            "baixa",
            alert_key,
            strategy=signal.strategy,
        )
        alert_id = storage.save_manual_alert(game, decision)

        minute = "?" if game.minute is None else f"{game.minute}'"
        score = f"{game.score_home if game.score_home is not None else '?'}x{game.score_away if game.score_away is not None else '?'}"
        line_label = "" if signal.line is None else f" {signal.line:g}"
        await update.message.reply_text(
            "ENTRADA VERIFICADA - SEM ODD\n\n"
            "Entrada baseada em jogo ao vivo real, estatisticas disponiveis e motor matematico. Confira a odd manualmente antes de entrar.\n\n"
            f"Jogo: {game.home} x {game.away}\n"
            f"Liga: {game.league or '-'}\n"
            f"Tempo: {minute}\n"
            f"Placar: {score}\n"
            f"Mercado: {market_label_text}{line_label}\n"
            f"Direcao: {signal.selection}\n"
            f"Probabilidade Poisson: {signal.probability:.0%}\n"
            f"Score de conviccao: {signal.score}/100\n"
            f"Estrategia: {signal.strategy}\n"
            f"Filtro minimo: {required_confidence}%\n"
            "Stake: baixa\n\n"
            f"Estatisticas usadas:\n{compact_stats_summary(game.stats)}\n\n"
            f"Motivo matematico: {signal.reason}\n\n"
            f"Leitura IA:\n{ai_reading}",
            reply_markup=alert_keyboard(alert_id=alert_id),
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP na entrada verificada: %s", exc.response.status_code)
        await update.message.reply_text(f"Erro HTTP na entrada verificada: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro ao buscar entrada verificada")
        await update.message.reply_text(f"Erro ao buscar entrada verificada: {type(exc).__name__}")
    finally:
        await http.close()
        storage.close()


async def debug_live_filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Diagnosticando jogos ao vivo e filtros...")
        api_football = ApiFootballClient(settings.api_football_key, http)
        sportmonks_live = await load_sportmonks_live(settings, http)
        sportmonks_client = make_sportmonks_client(settings, http)
        thestatsapi_live = await load_thestatsapi_live(settings, http)
        thestatsapi_client = make_thestatsapi_client(settings, http)
        totalcorner_live = await load_totalcorner_live(settings, http)
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("A API-Football nao retornou jogos ao vivo agora.")
            return

        lines = [
            f"Jogos ao vivo analisados: {min(len(fixtures), 10)} | TotalCorner live: {len(totalcorner_live)} | Sportmonks live: {len(sportmonks_live)} | TheStatsAPI live: {len(thestatsapi_live)}"
        ]
        for fixture in fixtures[:10]:
            fixture_id = fixture.get("fixture", {}).get("id")
            teams = fixture.get("teams", {})
            home = str(teams.get("home", {}).get("name") or "")
            away = str(teams.get("away", {}).get("name") or "")
            league = str(fixture.get("league", {}).get("name", "") or "")
            minute = extract_minute(fixture)
            score_home, score_away = extract_score(fixture)
            if not fixture_id:
                lines.append(f"- {home} x {away}: sem fixture_id")
                continue
            stats = await fixture_stats_with_sportmonks_fallback(
                fixture, api_football, sportmonks_live, sportmonks_client, thestatsapi_live, thestatsapi_client, totalcorner_live
            )
            required_confidence = settings.min_confidence
            flags = []
            if is_high_variance_match(league, home, away):
                required_confidence = max(required_confidence, 85)
                flags.append("alta variancia")
            if minute is not None and minute < 25:
                required_confidence = max(required_confidence, 85)
                flags.append("muito cedo")
            if not has_actionable_stats(stats):
                summary = "sem stats acionaveis"
            else:
                signal = evaluate_game(
                    minute=minute,
                    score_home=score_home,
                    score_away=score_away,
                    stats=stats,
                    min_confidence=required_confidence,
                )
                if signal.approved:
                    summary = f"APROVADO {signal.strategy} {signal.probability:.0%}"
                else:
                    summary = f"bloqueado: {signal.reason}"
            score = f"{score_home if score_home is not None else '?'}x{score_away if score_away is not None else '?'}"
            flag_text = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"- {home} x {away} {score} {minute or '?'}'{flag_text}: {summary}")

        await update.message.reply_text("\n".join(lines)[:3900])
    except Exception as exc:
        logger.exception("Erro no diagnostico de filtros")
        await update.message.reply_text(f"Erro no diagnostico de filtros: {type(exc).__name__}")
    finally:
        await http.close()


async def debug_odds_flow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Diagnosticando fluxo com odds...")
        odds_api = OddsApiClient(settings.odds_api_key, http)
        api_football = ApiFootballClient(settings.api_football_key, http)
        fixtures = await api_football.live_fixtures()
        odds_events = await odds_api.live_events(settings.sport, settings.max_live_events)
        totalcorner_live = await load_totalcorner_live(settings, http)
        sportmonks_live = await load_sportmonks_live(settings, http)
        sportmonks_client = make_sportmonks_client(settings, http)
        thestatsapi_live = await load_thestatsapi_live(settings, http)
        thestatsapi_client = make_thestatsapi_client(settings, http)

        lines = [
            f"API-Football live: {len(fixtures)} | Odds-API live: {len(odds_events)} | TotalCorner aceitos: {len(totalcorner_live)}"
        ]
        checked_odds = 0
        for fixture in fixtures[:10]:
            teams = fixture.get("teams", {})
            home = str(teams.get("home", {}).get("name") or "")
            away = str(teams.get("away", {}).get("name") or "")
            minute = extract_minute(fixture)
            score_home, score_away = extract_score(fixture)
            score = f"{score_home if score_home is not None else '?'}x{score_away if score_away is not None else '?'}"
            event = find_matching_odds_event(fixture, odds_events)
            if not event:
                lines.append(f"- {home} x {away} {score} {minute or '?'}': sem match na Odds-API")
                continue
            event_id = str(event.get("id") or "")
            stats = await fixture_stats_with_sportmonks_fallback(
                fixture, api_football, sportmonks_live, sportmonks_client, thestatsapi_live, thestatsapi_client, totalcorner_live
            )
            if not has_actionable_stats(stats):
                lines.append(f"- {home} x {away} {score} {minute or '?'}': odds match, sem stats acionaveis")
                continue
            league = str(fixture.get("league", {}).get("name", "") or "")
            required_confidence = settings.min_confidence
            if is_high_variance_match(league, home, away):
                required_confidence = max(required_confidence, 85)
            if minute is not None and minute < 25:
                required_confidence = max(required_confidence, 85)
            signal = evaluate_game(
                minute=minute,
                score_home=score_home,
                score_away=score_away,
                stats=stats,
                min_confidence=required_confidence,
            )
            if not signal.approved:
                lines.append(f"- {home} x {away} {score} {minute or '?'}': motor bloqueou: {signal.reason}")
                continue
            if not event_id:
                lines.append(f"- {home} x {away} {score} {minute or '?'}': sinal aprovado, mas event_id ausente")
                continue
            if checked_odds >= settings.odds_detail_limit:
                lines.append(f"- {home} x {away} {score} {minute or '?'}': sinal aprovado, mas limite de odds debug atingido")
                continue
            checked_odds += 1
            odds_payload = await odds_api.odds(event_id, settings.bookmakers)
            markets_all = flatten_all_markets(odds_payload or {}, fixture_id=fixture.get("fixture", {}).get("id"), min_odd=1.01)
            markets_min = flatten_markets(odds_payload or {}, fixture_id=fixture.get("fixture", {}).get("id"), min_odd=settings.min_odd)
            compatible = [market for market in markets_min if market_matches_idea(market, signal.market_family, signal.selection, signal.line)]
            examples = []
            for market in markets_all[:5]:
                line = "" if market.line is None else f" {market.line:g}"
                link_flag = "link=sim" if market.link_url else "link=nao"
                examples.append(f"{market.bookmaker}:{market.market_name}/{market.selection}{line}@{market.odd:.2f} {link_flag}")
            if compatible:
                best = sorted(compatible, key=lambda market: market.odd, reverse=True)[0]
                link_count = sum(1 for market in compatible if market.link_url)
                lines.append(
                    f"- {home} x {away} {score} {minute or '?'}': APROVADO {signal.strategy} linha {signal.line:g}; "
                    f"odd compativel {best.bookmaker} {best.market_name}/{best.selection} {best.line or signal.line}@{best.odd:.2f}; "
                    f"links diretos: {link_count}"
                )
            else:
                lines.append(
                    f"- {home} x {away} {score} {minute or '?'}': sinal {signal.market_family}/{signal.selection} {signal.line:g}, "
                    f"mercados >= {settings.min_odd:.2f}: {len(markets_min)}, compativeis: 0"
                )
                if examples:
                    lines.append("  exemplos odds: " + " | ".join(examples)[:900])
        await update.message.reply_text("\n".join(lines)[:3900])
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP no diagnostico odds flow: %s", exc.response.status_code)
        await update.message.reply_text(f"Erro HTTP odds flow: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro no diagnostico odds flow")
        await update.message.reply_text(f"Erro no diagnostico odds flow: {type(exc).__name__}")
    finally:
        await http.close()


async def debug_totalcorner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Diagnosticando TotalCorner...")
        if not settings.totalcorner_token:
            await update.message.reply_text("TOTALCORNER_TOKEN ausente no Railway.")
            return
        client = TotalCornerClient(settings.totalcorner_token, http)
        diagnostics = await client.diagnostic()
        lines = ["Diagnostico TotalCorner:"]
        for item in diagnostics:
            suffix = f" | {item['message']}" if item["message"] else ""
            lines.append(f"- {item['label']}: HTTP {item['status']} | itens={item['count']}{suffix}")

        live = await client.today_inplay(settings.max_live_events)
        if live:
            accepted = [
                match
                for match in live
                if not is_blocked_match_type(str(match.get("l") or ""), str(match.get("h") or ""), str(match.get("a") or ""))
            ]
            blocked_count = len(live) - len(accepted)
            lines.append(f"\nTotalCorner jogos ao vivo parseados: {len(live)} | aceitos: {len(accepted)} | bloqueados: {blocked_count}")
            for match in accepted[:10]:
                stats = compact_totalcorner_statistics(match)
                stat_count = sum(len(values) for values in stats.values())
                summary = compact_stats_summary(stats).replace("\n", " | ") if stats else "sem stats parseadas"
                score = f"{match.get('hg', '?')}x{match.get('ag', '?')}"
                raw_keys = ",".join(sorted(str(key) for key in match.keys() if "danger" in str(key).lower()))
                key_note = f" | dangerous_keys={raw_keys}" if raw_keys else ""
                lines.append(
                    f"- {match.get('h', '?')} x {match.get('a', '?')} {score} {match.get('status', '?')}': "
                    f"stats={stat_count} | {summary}{key_note}"
                )
            if blocked_count:
                lines.append("\nBloqueados por tipo de jogo:")
                for match in live:
                    if is_blocked_match_type(str(match.get("l") or ""), str(match.get("h") or ""), str(match.get("a") or "")):
                        lines.append(f"- {match.get('h', '?')} x {match.get('a', '?')} | {match.get('l', '-')}")
                        if len(lines) >= 24:
                            break
        await update.message.reply_text("\n".join(lines)[:3900])
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP no diagnostico TotalCorner: %s", exc.response.status_code)
        await update.message.reply_text(f"Erro HTTP TotalCorner: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro no diagnostico TotalCorner")
        await update.message.reply_text(f"Erro no diagnostico TotalCorner: {type(exc).__name__}")
    finally:
        await http.close()


async def debug_thestatsapi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Diagnosticando TheStatsAPI...")
        if not settings.thestatsapi_key:
            await update.message.reply_text("THESTATSAPI_KEY ausente no Railway.")
            return
        client = TheStatsApiClient(settings.thestatsapi_key, http)
        diagnostics = await client.diagnostic()
        lines = ["Diagnostico TheStatsAPI:"]
        for item in diagnostics:
            suffix = f" | {item['message']}" if item["message"] else ""
            lines.append(f"- {item['label']}: HTTP {item['status']} | itens={item['count']}{suffix}")
        live = await client.live_matches(settings.max_live_events)
        if live:
            lines.append("\nJogos live retornados:")
            for match in live[:8]:
                home = match.get("home_team", {}).get("name", "?") if isinstance(match.get("home_team"), dict) else "?"
                away = match.get("away_team", {}).get("name", "?") if isinstance(match.get("away_team"), dict) else "?"
                status = match.get("status", "?")
                stats_flag = "xG" if match.get("xg_available") else "sem xG"
                lines.append(f"- {home} x {away} | {status} | {stats_flag} | id={match.get('id')}")
        await update.message.reply_text("\n".join(lines)[:3900])
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP no diagnostico TheStatsAPI: %s", exc.response.status_code)
        await update.message.reply_text(f"Erro HTTP TheStatsAPI: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro no diagnostico TheStatsAPI")
        await update.message.reply_text(f"Erro no diagnostico TheStatsAPI: {type(exc).__name__}")
    finally:
        await http.close()


async def debug_sportmonks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Diagnosticando Sportmonks...")
        if not settings.sportmonks_api_token:
            await update.message.reply_text("SPORTMONKS_API_TOKEN ausente no Railway.")
            return
        client = SportmonksClient(settings.sportmonks_api_token, http)
        diagnostics = await client.diagnostic(date.today().isoformat())
        diag_lines = ["Diagnostico Sportmonks:"]
        for item in diagnostics:
            suffix = f" | {item['message']}" if item["message"] else ""
            diag_lines.append(f"- {item['label']}: HTTP {item['status']} | itens={item['count']}{suffix}")

        try:
            live = await client.live_scores()
        except httpx.HTTPStatusError as exc:
            await update.message.reply_text(
                ("\n".join(diag_lines) + f"\n\nlive_scores parse falhou com HTTP {exc.response.status_code}.")[:3900]
            )
            return
        if not live:
            await update.message.reply_text("\n".join(diag_lines)[:3900])
            return

        lines = diag_lines + [f"\nSportmonks jogos ao vivo parseados: {len(live)}"]
        for fixture in live[:10]:
            home, away = sportmonks_participant_names(fixture)
            stats = compact_sportmonks_statistics(fixture)
            stat_count = sum(len(values) for values in stats.values())
            sample = compact_stats_summary(stats).replace("\n", " | ") if stats else "sem stats parseadas"
            lines.append(f"- {home or '?'} x {away or '?'}: stats={stat_count} | {sample}")
        await update.message.reply_text("\n".join(lines)[:3900])
    except Exception as exc:
        logger.exception("Erro no diagnostico Sportmonks")
        await update.message.reply_text(f"Erro no diagnostico Sportmonks: {type(exc).__name__}")
    finally:
        await http.close()


async def debug_api_football_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Diagnosticando estatisticas da API-Football...")
        api_football = ApiFootballClient(settings.api_football_key, http)
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("API-Football nao retornou jogos ao vivo.")
            return

        lines = [f"API-Football jogos ao vivo: {len(fixtures)}"]
        for fixture in fixtures[:5]:
            fixture_id = fixture.get("fixture", {}).get("id")
            teams = fixture.get("teams", {})
            home = teams.get("home", {}).get("name", "")
            away = teams.get("away", {}).get("name", "")
            minute = extract_minute(fixture)
            score_home, score_away = extract_score(fixture)
            if not fixture_id:
                lines.append(f"- {home} x {away}: sem fixture_id")
                continue
            raw = await api_football.fixture_statistics(int(fixture_id))
            compact = compact_statistics(raw)
            players_raw = await api_football.fixture_players(int(fixture_id))
            players_compact = compact_player_statistics(players_raw)
            stat_names = []
            for team_stats in raw:
                team_name = team_stats.get("team", {}).get("name", "?")
                names = [str(stat.get("type")) for stat in team_stats.get("statistics", []) if stat.get("type")]
                stat_names.append(f"{team_name}: {', '.join(names[:12]) if names else 'sem campos'}")
            score = f"{score_home if score_home is not None else '?'}x{score_away if score_away is not None else '?'}"
            lines.append(
                f"- {home} x {away} {score} {minute or '?'}' fixture={fixture_id}: "
                f"times_stats={len(raw)} player_stats={len(players_raw)} "
                f"acionavel={'sim' if has_actionable_stats(compact) or has_actionable_stats(players_compact) else 'nao'}"
            )
            lines.extend([f"  {item}" for item in stat_names[:2]])
            if players_compact and not compact:
                lines.append(f"  fallback players: {compact_stats_summary(players_compact).replace(chr(10), ' | ')}")
        await update.message.reply_text("\n".join(lines)[:3900])
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP no diagnostico API-Football: %s", exc.response.status_code)
        await update.message.reply_text(f"Erro HTTP API-Football: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro no diagnostico API-Football")
        await update.message.reply_text(f"Erro no diagnostico API-Football: {type(exc).__name__}")
    finally:
        await http.close()


async def scheduled_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    try:
        await process_once(settings, storage)
    except httpx.HTTPStatusError as exc:
        logger.warning("Erro HTTP no ciclo de monitoramento: %s", exc.response.status_code)
    except Exception:
        logger.exception("Erro no ciclo de monitoramento")
    finally:
        storage.close()


async def startup_alert_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    db_status = database_storage_status(settings)
    warning = ""
    if not db_status["persistent"]:
        warning = (
            "\n\nATENCAO: banco temporario detectado. "
            "Historico/auditoria podem sumir em redeploy. Configure Volume no Railway em /data."
        )
    await send_message(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        "Bot iniciado no Railway. Use /status ou /scan para testar." + warning,
    )


def with_timeout(handler, seconds: float, label: str):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await asyncio.wait_for(handler(update, context), timeout=seconds)
        except TimeoutError:
            logger.warning("Comando %s passou de %.0fs e foi cancelado.", label, seconds)
            if update.message:
                await update.message.reply_text(f"{label} demorou demais e foi cancelado. Tente novamente em alguns segundos.")

    return wrapped


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.error("Conflito no Telegram getUpdates: existe outra instancia do mesmo bot em polling.")
        return
    logger.exception("Erro nao tratado no Telegram handler", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text(f"Erro interno: {type(context.error).__name__}")


def run_bot() -> None:
    settings = load_settings()
    require_telegram_settings(settings)
    logger.info("Variaveis no startup: %s", settings_presence(settings))
    if settings.dry_run:
        logger.warning("DRY_RUN=true: o bot nao enviara mensagens. Use 'once' para testar a coleta.")
    if settings.bfbm_export and settings.telegram_webhook_url:
        raise RuntimeError("BFBM_EXPORT usa a porta HTTP; desative TELEGRAM_WEBHOOK_URL ou use polling.")
    start_bfbm_server(settings)

    app = Application.builder().token(settings.telegram_bot_token).concurrent_updates(True).build()
    app.add_error_handler(error_handler)
    app.add_handler(CallbackQueryHandler(alert_action_cmd, pattern="^(bet|ignore):"))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("last", last_cmd))
    app.add_handler(CommandHandler("performance", performance_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("bfbm_test_tip", bfbm_test_tip_cmd))
    app.add_handler(CommandHandler("bfbm_test_simple", bfbm_test_simple_cmd))
    app.add_handler(CommandHandler("force_live_alert", force_live_alert_cmd))
    app.add_handler(CommandHandler("force_home_win_test", with_timeout(force_home_win_test_cmd, 60, "/force_home_win_test")))
    app.add_handler(CommandHandler("test_analysis_no_odds", test_analysis_no_odds_cmd))
    app.add_handler(CommandHandler("official_no_odds", with_timeout(official_no_odds_cmd, 60, "/official_no_odds")))
    app.add_handler(CommandHandler("force_verified_entry", with_timeout(force_verified_entry_cmd, 60, "/force_verified_entry")))
    app.add_handler(CommandHandler("debug_live_filters", with_timeout(debug_live_filters_cmd, 45, "/debug_live_filters")))
    app.add_handler(CommandHandler("debug_odds_flow", with_timeout(debug_odds_flow_cmd, 60, "/debug_odds_flow")))
    app.add_handler(CommandHandler("debug_sportmonks", debug_sportmonks_cmd))
    app.add_handler(CommandHandler("debug_api_football_stats", debug_api_football_stats_cmd))
    app.add_handler(CommandHandler("debug_thestatsapi", debug_thestatsapi_cmd))
    app.add_handler(CommandHandler("debug_totalcorner", debug_totalcorner_cmd))
    app.add_handler(CommandHandler("envcheck", envcheck_cmd))
    app.job_queue.run_repeating(scheduled_job, interval=settings.poll_seconds, first=min(60, settings.poll_seconds))
    if settings.startup_alert and not settings.dry_run:
        app.job_queue.run_once(startup_alert_job, when=1)
    if settings.telegram_webhook_url:
        webhook_url = f"{settings.telegram_webhook_url}/{settings.telegram_webhook_path}"
        logger.info("Iniciando Telegram via webhook em %s na porta %s.", webhook_url, settings.port)
        app.run_webhook(
            listen="0.0.0.0",
            port=settings.port,
            url_path=settings.telegram_webhook_path,
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
    else:
        if settings.railway_service_id and not settings.allow_polling:
            logger.warning(
                "Railway detectado sem TELEGRAM_WEBHOOK_URL/RAILWAY_PUBLIC_DOMAIN. "
                "Usando polling como fallback para manter o bot online. "
                "Se aparecer Conflict/getUpdates, deixe somente uma instancia rodando ou configure webhook."
            )
        logger.info(
            "Iniciando Telegram via polling. Use TELEGRAM_WEBHOOK_URL para evitar conflitos de getUpdates."
        )
        app.run_polling(drop_pending_updates=True)


async def run_once() -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    try:
        sent = await process_once(settings, storage, send_alerts=False)
        logger.info("Ciclo unico concluido. Alertas encontrados: %s", sent)
    finally:
        storage.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        asyncio.run(run_once())
    else:
        run_bot()
