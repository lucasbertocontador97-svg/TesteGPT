from __future__ import annotations

from telegram import Bot

from .models import Decision, GameSnapshot


def format_alert(game: GameSnapshot, decision: Decision) -> str:
    score = "x".join(
        [
            "?" if game.score_home is None else str(game.score_home),
            "?" if game.score_away is None else str(game.score_away),
        ]
    )
    line = "" if decision.line is None else f"\nLinha: {decision.line}"
    minute = "?" if game.minute is None else f"{game.minute}'"
    return (
        "Entrada encontrada\n\n"
        f"Jogo: {game.home} x {game.away}\n"
        f"Liga: {game.league}\n"
        f"Tempo: {minute}\n"
        f"Placar: {score}\n"
        f"Mercado: {decision.market}\n"
        f"Selecao: {decision.selection}{line}\n"
        f"Odd: {decision.odd:.2f}\n"
        f"Casa: {decision.bookmaker}\n"
        f"Confianca: {decision.confidence}%\n"
        f"Stake: {decision.stake}\n\n"
        f"Motivo: {decision.reason}"
    )


async def send_message(token: str, chat_id: str, text: str) -> None:
    bot = Bot(token)
    await bot.send_message(chat_id=chat_id, text=text)
