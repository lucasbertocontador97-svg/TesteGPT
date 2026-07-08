from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_webhook_url: str | None
    telegram_webhook_path: str
    port: int
    odds_api_key: str
    api_football_key: str
    sportmonks_api_token: str | None
    thestatsapi_key: str | None
    totalcorner_token: str | None
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
    startup_alert: bool
    odds_use_multi: bool
    odds_detail_limit: int
    hybrid_no_odds: bool
    game_cooldown_minutes: int


def load_settings() -> Settings:
    load_dotenv()
    volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    default_db_path = Path(volume_path) / "bot.sqlite3" if volume_path else Path("bot.sqlite3")
    db_path = Path(os.getenv("DATABASE_PATH", str(default_db_path)))
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    webhook_base_url = os.getenv("TELEGRAM_WEBHOOK_URL") or (f"https://{railway_domain}" if railway_domain else None)
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_webhook_url=webhook_base_url.rstrip("/") if webhook_base_url else None,
        telegram_webhook_path=os.getenv("TELEGRAM_WEBHOOK_PATH", "telegram/webhook").strip("/"),
        port=int(os.getenv("PORT", "8080")),
        odds_api_key=os.getenv("ODDS_API_KEY", ""),
        api_football_key=os.getenv("API_FOOTBALL_KEY", ""),
        sportmonks_api_token=os.getenv("SPORTMONKS_API_TOKEN") or None,
        thestatsapi_key=os.getenv("THESTATSAPI_KEY") or None,
        totalcorner_token=os.getenv("TOTALCORNER_TOKEN") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL") or None,
        dry_run=os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "sim"},
        poll_seconds=max(60, int(os.getenv("POLL_SECONDS", "180"))),
        min_odd=float(os.getenv("MIN_ODD", "1.80")),
        min_confidence=int(os.getenv("MIN_CONFIDENCE", "70")),
        bookmakers=[b.strip() for b in os.getenv("BOOKMAKERS", "Bet365,Betano").split(",") if b.strip()],
        sport=os.getenv("SPORT", "football"),
        max_live_events=int(os.getenv("MAX_LIVE_EVENTS", "20")),
        database_path=db_path,
        startup_alert=os.getenv("STARTUP_ALERT", "true").lower() in {"1", "true", "yes", "sim"},
        odds_use_multi=os.getenv("ODDS_USE_MULTI", "true").lower() in {"1", "true", "yes", "sim"},
        odds_detail_limit=max(1, int(os.getenv("ODDS_DETAIL_LIMIT", "5"))),
        hybrid_no_odds=os.getenv("HYBRID_NO_ODDS", "true").lower() in {"1", "true", "yes", "sim"},
        game_cooldown_minutes=max(0, int(os.getenv("GAME_COOLDOWN_MINUTES", "30"))),
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


def require_telegram_settings(settings: Settings) -> None:
    missing = []
    if not settings.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not settings.telegram_chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise RuntimeError("Variaveis obrigatorias ausentes: " + ", ".join(missing))


def settings_presence(settings: Settings) -> dict[str, bool]:
    return {
        "TELEGRAM_BOT_TOKEN": bool(settings.telegram_bot_token),
        "TELEGRAM_CHAT_ID": bool(settings.telegram_chat_id),
        "TELEGRAM_WEBHOOK_URL": bool(settings.telegram_webhook_url),
        "ODDS_API_KEY": bool(settings.odds_api_key),
        "API_FOOTBALL_KEY": bool(settings.api_football_key),
        "SPORTMONKS_API_TOKEN": bool(settings.sportmonks_api_token),
        "THESTATSAPI_KEY": bool(settings.thestatsapi_key),
        "TOTALCORNER_TOKEN": bool(settings.totalcorner_token),
        "OPENAI_API_KEY": bool(settings.openai_api_key),
        "DRY_RUN_FALSE": not settings.dry_run,
        "ODDS_USE_MULTI": settings.odds_use_multi,
        "HYBRID_NO_ODDS": settings.hybrid_no_odds,
        "GAME_COOLDOWN_MINUTES": bool(settings.game_cooldown_minutes),
    }
