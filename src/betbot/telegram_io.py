from __future__ import annotations

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
        f"\U0001f534 AO VIVO {minute} {score}\n"
        "\u2705 ENTRADA APROVADA\n\n"
        f"\u26bd {game.home} vs {game.away}\n"
        f"\U0001f3c6 {game.league or '-'}\n\n"
        f"\U0001f4ca Mercado: {market}\n"
        f"\U0001f3af Direcao: {selection}\n"
        f"\U0001f3e6 Odd real: {decision.odd:.2f}\n"
        f"\U0001f3e0 Casa encontrada: {decision.bookmaker}\n"
        f"\U0001f4c8 Score: {decision.confidence}/100\n"
        f"\U0001f4b0 Stake: {decision.stake}\n\n"
        f"\U0001f9e0 Motivo: {decision.reason}"
    )


def _bookmaker_link(links: dict[str, str], wanted: str) -> str | None:
    wanted_lower = wanted.lower()
    for bookmaker, url in links.items():
        if wanted_lower in bookmaker.lower():
            return url
    return None


def bookmaker_keyboard(links: dict[str, str] | None = None) -> InlineKeyboardMarkup | None:
    links = links or {}
    bet365_url = _bookmaker_link(links, "bet365")
    betano_url = _bookmaker_link(links, "betano")
    buttons = []
    if bet365_url:
        buttons.append(InlineKeyboardButton("\U0001f3af Abrir Bet365", url=bet365_url))
    if betano_url:
        buttons.append(InlineKeyboardButton("\U0001f7e2 Abrir Betano", url=betano_url))
    return InlineKeyboardMarkup([buttons]) if buttons else None


async def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    with_bookmakers: bool = False,
    bookmaker_links: dict[str, str] | None = None,
) -> None:
    bot = Bot(token)
    reply_markup = bookmaker_keyboard(bookmaker_links) if with_bookmakers else None
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
