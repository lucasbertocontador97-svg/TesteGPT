from __future__ import annotations

import os

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from .models import Decision, GameSnapshot


def _line_label(line: float | None) -> str:
    return "" if line is None else f" {line:g}"


def _selection_label(selection: str) -> str:
    labels = {"over": "Mais", "under": "Menos"}
    return labels.get(selection.lower(), selection)


def format_alert(game: GameSnapshot, decision: Decision) -> str:
    score = "x".join(
        [
            "?" if game.score_home is None else str(game.score_home),
            "?" if game.score_away is None else str(game.score_away),
        ]
    )
    minute = "?" if game.minute is None else f"{game.minute}'"
    market = f"{decision.market}{_line_label(decision.line)}"
    selection = _selection_label(decision.selection)
    return (
        f"🔴 AO VIVO {minute} {score}\n"
        "✅ ENTRADA APROVADA\n\n"
        f"⚽ {game.home} vs {game.away}\n"
        f"🏆 {game.league or '-'}\n\n"
        f"📊 Mercado: {market}\n"
        f"🎯 Direção: {selection}\n"
        f"🏦 Odd real: {decision.odd:.2f}\n"
        f"🏠 Casa encontrada: {decision.bookmaker}\n"
        f"📈 Score: {decision.confidence}/100\n"
        f"💰 Stake: {decision.stake}\n\n"
        f"🧠 Motivo: {decision.reason}"
    )


def bookmaker_keyboard() -> InlineKeyboardMarkup:
    bet365_url = os.getenv("BET365_URL", "https://www.bet365.bet.br/")
    betano_url = os.getenv("BETANO_URL", "https://br.betano.com/")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎯 Abrir Bet365", url=bet365_url),
                InlineKeyboardButton("🟢 Abrir Betano", url=betano_url),
            ]
        ]
    )


async def send_message(token: str, chat_id: str, text: str, *, with_bookmakers: bool = False) -> None:
    bot = Bot(token)
    reply_markup = bookmaker_keyboard() if with_bookmakers else None
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
