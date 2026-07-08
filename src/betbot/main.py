from __future__ import annotations

import asyncio
import logging
import sys

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .ai import analyze_game, analyze_live_game_without_odds
from .clients import ApiFootballClient, HttpJsonClient, OddsApiClient
from .config import load_settings, require_runtime_settings, require_telegram_settings, settings_presence
from .markets import flatten_all_markets, flatten_markets
from .matching import find_matching_odds_event
from .models import GameSnapshot
from .settlement import settle_alert
from .stats import compact_statistics, extract_minute, extract_score
from .storage import Storage
from .telegram_io import format_alert, send_message


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("betbot")


async def build_snapshots(settings, odds_api: OddsApiClient, api_football: ApiFootballClient) -> list[GameSnapshot]:
    football_fixtures = await api_football.live_fixtures()
    if not football_fixtures:
        logger.info("API-Football nao retornou jogos ao vivo.")
        return []

    odds_events = await odds_api.live_events(settings.sport, settings.max_live_events)
    matched_pairs: list[tuple[dict, dict]] = []
    used_event_ids: set[str] = set()
    for fixture in football_fixtures:
        event = find_matching_odds_event(fixture, odds_events)
        event_id = str(event.get("id") or "") if event else ""
        if not event or not event_id or event_id in used_event_ids:
            continue
        used_event_ids.add(event_id)
        matched_pairs.append((fixture, event))
        if len(matched_pairs) >= settings.odds_detail_limit:
            break

    if not matched_pairs:
        logger.info("Jogos ao vivo da API-Football sem correspondencia na Odds-API.")
        return []

    event_ids = [str(event.get("id")) for _, event in matched_pairs if event.get("id")]
    odds_by_id = {}
    if settings.odds_use_multi:
        try:
            odds_payloads = await odds_api.odds_multi(event_ids, settings.bookmakers)
            odds_by_id = {str(payload.get("id") or payload.get("eventId")): payload for payload in odds_payloads}
        except httpx.HTTPStatusError as exc:
            logger.warning("Odds multi falhou com HTTP %s; tentando evento por evento.", exc.response.status_code)

    if not odds_by_id:
        for event_id in event_ids[: settings.odds_detail_limit]:
            try:
                payload = await odds_api.odds(event_id, settings.bookmakers)
            except httpx.HTTPStatusError as exc:
                logger.warning("Odds do evento %s falhou com HTTP %s.", event_id, exc.response.status_code)
                continue
            if payload:
                odds_by_id[str(payload.get("id") or payload.get("eventId") or event_id)] = payload
    snapshots: list[GameSnapshot] = []

    for fixture, event in matched_pairs:
        event_id = str(event.get("id") or "")
        odds_payload = odds_by_id.get(event_id)
        if not odds_payload:
            continue
        fixture_id = fixture.get("fixture", {}).get("id") if fixture else None
        markets = flatten_markets(odds_payload, fixture_id=fixture_id, min_odd=settings.min_odd)
        if not markets:
            continue
        stats = compact_statistics(await api_football.fixture_statistics(int(fixture_id))) if fixture_id else {}
        score_home, score_away = extract_score(fixture)
        fixture_league = fixture.get("league", {}) if fixture else {}
        league = fixture_league.get("name") or (event.get("league", {}).get("name") if isinstance(event.get("league"), dict) else event.get("league", ""))
        teams = fixture.get("teams", {}) if fixture else {}
        snapshots.append(
            GameSnapshot(
                event_id=event_id,
                fixture_id=fixture_id,
                league=str(league or ""),
                home=str(teams.get("home", {}).get("name") or event.get("home") or ""),
                away=str(teams.get("away", {}).get("name") or event.get("away") or ""),
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
            await update.message.reply_text("Odds-API retornou 429 Too Many Requests. Aguarde alguns minutos ou aumente POLL_SECONDS.")
        else:
            await update.message.reply_text(f"Erro HTTP ao forcar varredura: {exc.response.status_code}")
    except Exception as exc:
        logger.exception("Erro ao forcar varredura")
        await update.message.reply_text(f"Erro ao forcar varredura: {type(exc).__name__}")
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


async def envcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    presence = settings_presence(settings)
    lines = ["Variaveis vistas pelo bot:"]
    for name, ok in presence.items():
        lines.append(f"{name}: {'OK' if ok else 'AUSENTE'}")
    await update.message.reply_text("\n".join(lines))


async def test_analysis_no_odds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    http = HttpJsonClient()
    try:
        await update.message.reply_text("Testando analise sem odds, usando apenas jogos ao vivo da API-Football...")
        api_football = ApiFootballClient(settings.api_football_key, http)
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("A API-Football nao retornou jogos ao vivo agora.")
            return

        fixture = fixtures[0]
        fixture_id = fixture.get("fixture", {}).get("id")
        stats = compact_statistics(await api_football.fixture_statistics(int(fixture_id))) if fixture_id else {}
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
    await send_message(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        "Bot iniciado no Railway. Use /status ou /scan para testar.",
    )


def run_bot() -> None:
    settings = load_settings()
    require_telegram_settings(settings)
    logger.info("Variaveis no startup: %s", settings_presence(settings))
    if settings.dry_run:
        logger.warning("DRY_RUN=true: o bot nao enviara mensagens. Use 'once' para testar a coleta.")

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("last", last_cmd))
    app.add_handler(CommandHandler("performance", performance_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("force_live_alert", force_live_alert_cmd))
    app.add_handler(CommandHandler("test_analysis_no_odds", test_analysis_no_odds_cmd))
    app.add_handler(CommandHandler("envcheck", envcheck_cmd))
    app.job_queue.run_repeating(scheduled_job, interval=settings.poll_seconds, first=min(60, settings.poll_seconds))
    if settings.startup_alert and not settings.dry_run:
        app.job_queue.run_once(startup_alert_job, when=1)
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
