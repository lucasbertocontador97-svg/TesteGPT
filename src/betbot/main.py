from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .ai import analyze_game, analyze_live_game_without_odds, suggest_market_without_odds
from .clients import ApiFootballClient, HttpJsonClient, OddsApiClient, SportmonksClient
from .config import load_settings, require_runtime_settings, require_telegram_settings, settings_presence
from .deterministic import evaluate_game
from .markets import flatten_all_markets, flatten_markets, market_matches_idea
from .matching import find_matching_odds_event, find_matching_sportmonks_fixture, sportmonks_participant_names
from .models import Decision, GameSnapshot
from .settlement import settle_alert
from .stats import compact_sportmonks_statistics, compact_statistics, compact_stats_summary, extract_minute, extract_score, has_actionable_stats, is_high_variance_match
from .storage import Storage
from .telegram_io import format_alert, send_message


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("betbot")


async def load_sportmonks_live(settings, http: HttpJsonClient) -> list[dict]:
    if not settings.sportmonks_api_token:
        return []
    try:
        return await SportmonksClient(settings.sportmonks_api_token, http).live_scores()
    except httpx.HTTPStatusError as exc:
        logger.warning("Sportmonks live scores falhou com HTTP %s.", exc.response.status_code)
        return []


def make_sportmonks_client(settings, http: HttpJsonClient) -> SportmonksClient | None:
    if not settings.sportmonks_api_token:
        return None
    return SportmonksClient(settings.sportmonks_api_token, http)


async def fixture_stats_with_sportmonks_fallback(
    fixture: dict,
    api_football: ApiFootballClient,
    sportmonks_live: list[dict],
    sportmonks_client: SportmonksClient | None = None,
) -> dict:
    fixture_id = fixture.get("fixture", {}).get("id")
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
    return sportmonks_stats if has_actionable_stats(sportmonks_stats) else api_stats


async def build_snapshots(settings, odds_api: OddsApiClient, api_football: ApiFootballClient) -> list[GameSnapshot]:
    football_fixtures = await api_football.live_fixtures()
    if not football_fixtures:
        logger.info("API-Football nao retornou jogos ao vivo.")
        return []

    odds_events = await odds_api.live_events(settings.sport, settings.max_live_events)
    sportmonks_live = await load_sportmonks_live(settings, odds_api.http)
    sportmonks_client = make_sportmonks_client(settings, odds_api.http)
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

    snapshots: list[GameSnapshot] = []

    for fixture, event in matched_pairs:
        event_id = str(event.get("id") or "")
        fixture_id = fixture.get("fixture", {}).get("id") if fixture else None
        stats = await fixture_stats_with_sportmonks_fallback(fixture, api_football, sportmonks_live, sportmonks_client)
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
                markets=[],
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
            if not has_actionable_stats(game.stats):
                logger.info("Sem estatisticas suficientes para %s x %s.", game.home, game.away)
                continue
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
            )
            if not math_signal.approved:
                logger.info("Motor matematico bloqueou %s x %s: %s", game.home, game.away, math_signal.reason)
                continue
            idea = await suggest_market_without_odds(
                game,
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                min_confidence=required_confidence,
            )
            if not idea.should_check_odds:
                logger.info("IA nao pediu odds: %s x %s - %s", game.home, game.away, idea.reason)
                continue
            if idea.market_family != math_signal.market_family or idea.selection != math_signal.selection:
                logger.info("IA divergiu do motor matematico em %s x %s.", game.home, game.away)
                continue
            try:
                odds_payload = await odds_api.odds(game.event_id, settings.bookmakers)
            except httpx.HTTPStatusError as exc:
                logger.warning("Odds apos ideia da IA falhou para %s com HTTP %s.", game.event_id, exc.response.status_code)
                continue
            markets = flatten_markets(odds_payload or {}, fixture_id=game.fixture_id, min_odd=settings.min_odd)
            compatible = [market for market in markets if market_matches_idea(market, idea.market_family, idea.selection, idea.line)]
            if not compatible:
                logger.info("Sem odd >= %.2f para ideia %s/%s em %s x %s", settings.min_odd, idea.market_family, idea.selection, game.home, game.away)
                continue
            chosen = sorted(compatible, key=lambda market: market.odd, reverse=True)[0]
            decision = Decision(
                True,
                idea.confidence,
                chosen.market_name,
                chosen.selection,
                chosen.bookmaker,
                chosen.odd,
                chosen.line or idea.line,
                idea.reason,
                idea.stake,
                chosen.alert_key,
            )
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
        sportmonks_live = await load_sportmonks_live(settings, http)
        sportmonks_client = make_sportmonks_client(settings, http)
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("A API-Football nao retornou jogos ao vivo agora.")
            return

        fixture = fixtures[0]
        fixture_id = fixture.get("fixture", {}).get("id")
        stats = await fixture_stats_with_sportmonks_fallback(fixture, api_football, sportmonks_live, sportmonks_client)
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
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("A API-Football nao retornou jogos ao vivo agora.")
            return

        for fixture in fixtures[: settings.odds_detail_limit]:
            fixture_id = fixture.get("fixture", {}).get("id")
            if not fixture_id:
                continue
            stats = await fixture_stats_with_sportmonks_fallback(fixture, api_football, sportmonks_live, sportmonks_client)
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
            if not idea.should_check_odds:
                logger.info("Sem entrada sem odds: %s x %s - %s", game.home, game.away, idea.reason)
                continue
            if idea.market_family != math_signal.market_family or idea.selection != math_signal.selection:
                logger.info("IA divergiu do motor matematico em %s x %s.", game.home, game.away)
                continue

            market_label = {
                ("goals", "over"): "Mais gols",
                ("goals", "under"): "Menos gols",
                ("corners", "over"): "Mais escanteios",
                ("corners", "under"): "Menos escanteios",
            }.get((idea.market_family, idea.selection), f"{idea.market_family} {idea.selection}")
            final_line = math_signal.line if math_signal.line is not None else idea.line
            alert_key = f"no-odds|{game.fixture_id}|{idea.market_family}|{idea.selection}|{final_line}|{game.minute or ''}"
            if storage.seen_alert(alert_key):
                await update.message.reply_text("A IA encontrou uma entrada sem odds, mas ela ja foi enviada antes.")
                return

            decision = Decision(
                True,
                idea.confidence,
                market_label,
                idea.selection,
                "Conferir manualmente",
                0.0,
                final_line,
                f"{math_signal.reason} IA: {idea.reason}",
                idea.stake,
                alert_key,
            )
            storage.save_manual_alert(game, decision)
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
                f"Mercado indicado: {market_label}{line_label}\n"
                f"Direcao: {idea.selection}\n"
                f"Confianca IA: {idea.confidence}%\n"
                f"Probabilidade matematica: {math_signal.probability:.0%}\n"
                f"Estrategia: {math_signal.strategy}\n"
                f"Stake: {idea.stake}\n"
                f"Filtro aplicado: confianca minima {required_confidence}%"
                f"{' (' + ', '.join(caution_notes) + ')' if caution_notes else ''}\n\n"
                f"Estatisticas usadas:\n{compact_stats_summary(game.stats)}\n\n"
                f"Motivo matematico: {math_signal.reason}\n"
                f"Leitura IA: {idea.reason}"
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
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("Nao ha jogos ao vivo agora na API-Football.")
            return

        candidates = []
        for fixture in fixtures[: max(settings.odds_detail_limit, 10)]:
            fixture_id = fixture.get("fixture", {}).get("id")
            if not fixture_id:
                continue
            stats = await fixture_stats_with_sportmonks_fallback(fixture, api_football, sportmonks_live, sportmonks_client)
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
        market_label = {
            ("goals", "over"): "Mais gols",
            ("goals", "under"): "Menos gols",
            ("corners", "over"): "Mais escanteios",
            ("corners", "under"): "Menos escanteios",
        }.get((signal.market_family, signal.selection), f"{signal.market_family} {signal.selection}")
        alert_key = f"verified|{game.fixture_id}|{signal.market_family}|{signal.selection}|{signal.line}|{game.minute or ''}"
        if storage.seen_alert(alert_key):
            await update.message.reply_text("A melhor entrada veridica encontrada ja foi enviada antes.")
            return

        ai_reading = await analyze_live_game_without_odds(game, api_key=settings.openai_api_key, model=settings.openai_model)
        decision = Decision(
            True,
            signal.confidence,
            market_label,
            signal.selection,
            "Conferir manualmente",
            0.0,
            signal.line,
            signal.reason,
            "baixa",
            alert_key,
        )
        storage.save_manual_alert(game, decision)

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
            f"Mercado: {market_label}{line_label}\n"
            f"Direcao: {signal.selection}\n"
            f"Probabilidade matematica: {signal.probability:.0%}\n"
            f"Score: {signal.score}\n"
            f"Estrategia: {signal.strategy}\n"
            f"Filtro minimo: {required_confidence}%\n"
            "Stake: baixa\n\n"
            f"Estatisticas usadas:\n{compact_stats_summary(game.stats)}\n\n"
            f"Motivo matematico: {signal.reason}\n\n"
            f"Leitura IA:\n{ai_reading}"
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
        fixtures = await api_football.live_fixtures()
        if not fixtures:
            await update.message.reply_text("A API-Football nao retornou jogos ao vivo agora.")
            return

        lines = [f"Jogos ao vivo analisados: {min(len(fixtures), 10)} | Sportmonks live: {len(sportmonks_live)}"]
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
            stats = await fixture_stats_with_sportmonks_fallback(fixture, api_football, sportmonks_live, sportmonks_client)
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
            stat_names = []
            for team_stats in raw:
                team_name = team_stats.get("team", {}).get("name", "?")
                names = [str(stat.get("type")) for stat in team_stats.get("statistics", []) if stat.get("type")]
                stat_names.append(f"{team_name}: {', '.join(names[:12]) if names else 'sem campos'}")
            score = f"{score_home if score_home is not None else '?'}x{score_away if score_away is not None else '?'}"
            lines.append(
                f"- {home} x {away} {score} {minute or '?'}' fixture={fixture_id}: "
                f"times_stats={len(raw)} acionavel={'sim' if has_actionable_stats(compact) else 'nao'}"
            )
            lines.extend([f"  {item}" for item in stat_names[:2]])
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
    app.add_handler(CommandHandler("official_no_odds", official_no_odds_cmd))
    app.add_handler(CommandHandler("force_verified_entry", force_verified_entry_cmd))
    app.add_handler(CommandHandler("debug_live_filters", debug_live_filters_cmd))
    app.add_handler(CommandHandler("debug_sportmonks", debug_sportmonks_cmd))
    app.add_handler(CommandHandler("debug_api_football_stats", debug_api_football_stats_cmd))
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
