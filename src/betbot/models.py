from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MarketOption:
    event_id: str
    fixture_id: int | None
    bookmaker: str
    market_name: str
    selection: str
    odd: float
    line: float | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def alert_key(self) -> str:
        line = "" if self.line is None else str(self.line)
        return "|".join([str(self.event_id), str(self.fixture_id or ""), self.bookmaker, self.market_name, self.selection, line])


@dataclass(frozen=True)
class GameSnapshot:
    event_id: str
    fixture_id: int | None
    league: str
    home: str
    away: str
    minute: int | None
    score_home: int | None
    score_away: int | None
    stats: dict[str, Any]
    markets: list[MarketOption]


@dataclass(frozen=True)
class Decision:
    should_bet: bool
    confidence: int
    market: str
    selection: str
    bookmaker: str
    odd: float
    line: float | None
    reason: str
    stake: str
    alert_key: str | None = None


@dataclass(frozen=True)
class MarketIdea:
    should_check_odds: bool
    market_family: str
    selection: str
    confidence: int
    reason: str
    stake: str
