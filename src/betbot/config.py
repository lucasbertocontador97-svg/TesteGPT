from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    odds_api_key: str
    api_football_key: str
    openai_api_key: str | None
    openai_model: str | None
    dry_run: bool
    poll_seconds: int
    min_odd: float
    min_confidence: int
    bookmakers: list[str]
    sport: str
    max_live_events: int
    database_path: Path


def load_settings() -> Settings:
    load_dotenv()
    db_path = Path(os.getenv("DATABASE_PATH", "bot.sqlite3"))
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        odds_api_key=os.getenv("ODDS_API_KEY", ""),
        api_football_key=os.getenv("API_FOOTBALL_KEY", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL") or None,
        dry_run=os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "sim"},
        poll_seconds=int(os.getenv("POLL_SECONDS", "45")),
        min_odd=float(os.getenv("MIN_ODD", "1.80")),
        min_confidence=int(os.getenv("MIN_CONFIDENCE", "70")),
        bookmakers=[b.strip() for b in os.getenv("BOOKMAKERS", "Bet365,Betano").split(",") if b.strip()],
        sport=os.getenv("SPORT", "football"),
        max_live_events=int(os.getenv("MAX_LIVE_EVENTS", "20")),
        database_path=db_path,
    )


def require_runtime_settings(settings: Settings) -> None:
    missing = []
    if not settings.odds_api_key:
        missing.append("ODDS_API_KEY")
    if not settings.api_football_key:
        missing.append("API_FOOTBALL_KEY")
    if not settings.telegram_bot_token and not settings.dry_run:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not settings.telegram_chat_id and not settings.dry_run:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise RuntimeError("Variaveis obrigatorias ausentes: " + ", ".join(missing))
