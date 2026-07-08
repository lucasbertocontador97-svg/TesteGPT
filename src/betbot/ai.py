from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from .models import Decision, GameSnapshot, MarketOption


SYSTEM_PROMPT = """Voce e um analista conservador de apostas esportivas ao vivo.
Regras obrigatorias:
- Recomende no maximo uma entrada.
- So pode escolher mercado presente em markets.
- So pode escolher odd acima da odd minima ja filtrada.
- Se os dados forem fracos, confusos, sem pressao real ou mercado ruim, responda NO_BET.
- Nao invente placar, tempo, estatistica, odd ou bookmaker.
- Retorne somente JSON valido."""


def _market_to_dict(market: MarketOption) -> dict[str, Any]:
    return {
        "alert_key": market.alert_key,
        "bookmaker": market.bookmaker,
        "market": market.market_name,
        "selection": market.selection,
        "odd": market.odd,
        "line": market.line,
        "updated_at": market.updated_at,
    }


def _fallback_decision(game: GameSnapshot, min_confidence: int) -> Decision:
    if not game.markets:
        return Decision(False, 0, "", "", "", 0.0, None, "Sem mercados com odd minima.", "sem entrada")
    best = sorted(game.markets, key=lambda m: m.odd, reverse=True)[0]
    confidence = max(min_confidence, 70)
    reason = "Heuristica usada porque OPENAI_API_KEY nao foi configurada. Mercado passou no filtro de odd minima."
    return Decision(True, confidence, best.market_name, best.selection, best.bookmaker, best.odd, best.line, reason, "baixa", best.alert_key)


async def analyze_game(game: GameSnapshot, *, api_key: str | None, model: str | None, min_confidence: int) -> Decision:
    if not api_key:
        return _fallback_decision(game, min_confidence)

    client = AsyncOpenAI(api_key=api_key)
    payload = {
        "game": {
            "event_id": game.event_id,
            "fixture_id": game.fixture_id,
            "league": game.league,
            "home": game.home,
            "away": game.away,
            "minute": game.minute,
            "score": {"home": game.score_home, "away": game.score_away},
            "stats": game.stats,
        },
        "markets": [_market_to_dict(market) for market in game.markets[:60]],
        "output_schema": {
            "should_bet": "boolean",
            "alert_key": "string or null",
            "confidence": "integer 0-100",
            "reason": "short Portuguese explanation",
            "stake": "baixa, media, alta, or sem entrada",
        },
    }
    response = await client.chat.completions.create(
        model=model or "gpt-4o-mini",
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    if not data.get("should_bet"):
        return Decision(False, int(data.get("confidence", 0)), "", "", "", 0.0, None, data.get("reason", "IA decidiu sem entrada."), "sem entrada")

    alert_key = data.get("alert_key")
    chosen = next((market for market in game.markets if market.alert_key == alert_key), None)
    if not chosen:
        return Decision(False, 0, "", "", "", 0.0, None, "IA escolheu um mercado que nao estava na lista.", "sem entrada")

    confidence = int(data.get("confidence", 0))
    if confidence < min_confidence:
        return Decision(False, confidence, "", "", "", 0.0, None, "Confianca abaixo do minimo configurado.", "sem entrada")

    return Decision(
        True,
        confidence,
        chosen.market_name,
        chosen.selection,
        chosen.bookmaker,
        chosen.odd,
        chosen.line,
        str(data.get("reason", ""))[:700],
        str(data.get("stake", "baixa")),
        chosen.alert_key,
    )


async def analyze_live_game_without_odds(game: GameSnapshot, *, api_key: str | None, model: str | None) -> str:
    payload = {
        "game": {
            "fixture_id": game.fixture_id,
            "league": game.league,
            "home": game.home,
            "away": game.away,
            "minute": game.minute,
            "score": {"home": game.score_home, "away": game.score_away},
            "stats": game.stats,
        },
        "instruction": (
            "Analise somente o jogo ao vivo e as estatisticas. Ignore odds e nao recomende aposta real. "
            "Diga quais mercados fariam sentido observar depois, como gols ou escanteios, e explique em portugues."
        ),
    }
    if not api_key:
        return (
            "Analise sem IA: jogo ao vivo carregado com sucesso pela API-Football. "
            "Configure OPENAI_API_KEY para receber uma leitura qualitativa das estatisticas."
        )

    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model or "gpt-4o-mini",
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "Voce e um analista conservador de futebol ao vivo. "
                    "Nao recomende aposta real sem odds. Responda curto, pratico e em portugues."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    return (response.choices[0].message.content or "Sem analise retornada.").strip()[:1500]
