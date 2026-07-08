from __future__ import annotations

import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .ai import analyze_game
from .clients import ApiFootballClient, HttpJsonClient, OddsApiClient
from .config import load_settings, require_runtime_settings
from .markets import flatten_markets
from .matching import find_matching_fixture
from .models import GameSnapshot
from .settlement import settle_alert
from .stats import compact_statistics, extract_minute, extract_score
from .storage import Storage
from .telegram_io import format_alert, send_message


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("betbot")


async def build_snapshots(settings, odds_api: OddsApiClient, api_football: ApiFootballClient) -> list[GameSnapshot]:
    odds_events = await odds_api.live_events(settings.sport, settings.max_live_events)
    football_fixtures = await api_football.live_fixtures()
    odds_payloads = await odds_api.odds_multi([str(event.get("id")) for event in odds_events if event.get("id")], settings.bookmakers)
    odds_by_id = {str(payload.get("id") or payload.get("eventId")): payload for payload in odds_payloads}
    snapshots: list[GameSnapshot] = []

    for event in odds_events:
        event_id = str(event.get("id") or "")
        odds_payload = odds_by_id.get(event_id)
        if not odds_payload:
            continue
        fixture = find_matching_fixture(event, football_fixtures)
        fixture_id = fixture.get("fixture", {}).get("id") if fixture else None
        markets = flatten_markets(odds_payload, fixture_id=fixture_id, min_odd=settings.min_odd)
        if not markets:
            continue
        stats = compact_statistics(await api_football.fixture_statistics(int(fixture_id))) if fixture_id else {}
        score_home, score_away = extract_score(fixture)
        league = event.get("league", {}).get("name") if isinstance(event.get("league"), dict) else event.get("league", "")
        snapshots.append(
            GameSnapshot(
                event_id=event_id,
                fixture_id=fixture_id,
                league=str(league or ""),
                home=str(event.get("home") or ""),
                away=str(event.get("away") or ""),
                minute=extract_minute(fixture),
                score_home=score_home,
                score_away=score_away,
                stats=stats,
                markets=markets,
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
        for game in await build_snapshots(settings, odds_api, api_football):
            decision = await analyze_game(
                game,
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                min_confidence=settings.min_confidence,
            )
            if not decision.should_bet or not decision.alert_key:
                logger.info("Sem entrada: %s x %s - %s", game.home, game.away, decision.reason)
                continue
            if storage.seen_alert(decision.alert_key):
                logger.info("Entrada repetida ignorada: %s", decision.alert_key)
                continue
            storage.save_alert(game, decision)
            message = format_alert(game, decision)
            if settings.dry_run or not send_alerts:
                logger.info("DRY_RUN alerta:\n%s", message)
            else:
                await send_message(settings.telegram_bot_token, settings.telegram_chat_id, message)
            sent += 1

        for alert in storage.pending_alerts():
            result = await settle_alert(alert, api_football)
            if result:
                storage.settle_alert(int(alert["id"]), result[0], result[1])
        return sent
    finally:
        await http.close()


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    try:
        perf = storage.performance()
        await update.message.reply_text(
            f"Status: online\nAlertas: {perf['summary']}\nWin rate: {perf['win_rate']}%\nLucro unidades: {perf['profit_units']}"
        )
    finally:
        storage.close()


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
                f"{alert['line']} @ {alert['odd']} | {alert['status']}"
            )
        await update.message.reply_text("\n".join(lines))
    finally:
        storage.close()


async def performance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await status_cmd(update, context)


async def scheduled_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    storage = Storage(settings.database_path)
    try:
        await process_once(settings, storage)
    except Exception:
        logger.exception("Erro no ciclo de monitoramento")
    finally:
        storage.close()


def run_bot() -> None:
    settings = load_settings()
    require_runtime_settings(settings)
    if settings.dry_run:
        logger.warning("DRY_RUN=true: o bot nao enviara mensagens. Use 'once' para testar a coleta.")

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("last", last_cmd))
    app.add_handler(CommandHandler("performance", performance_cmd))
    app.job_queue.run_repeating(scheduled_job, interval=settings.poll_seconds, first=5)
    app.run_polling()


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
