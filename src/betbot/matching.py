from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any


def normalize_team(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\b(fc|sc|ac|cf|club|de|the)\b", " ", text.lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_team(left), normalize_team(right)).ratio()


def find_matching_fixture(odds_event: dict[str, Any], fixtures: list[dict[str, Any]]) -> dict[str, Any] | None:
    home = odds_event.get("home") or odds_event.get("homeTeam") or ""
    away = odds_event.get("away") or odds_event.get("awayTeam") or ""
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for fixture in fixtures:
        teams = fixture.get("teams", {})
        f_home = teams.get("home", {}).get("name", "")
        f_away = teams.get("away", {}).get("name", "")
        direct = (similarity(home, f_home) + similarity(away, f_away)) / 2
        swapped = (similarity(home, f_away) + similarity(away, f_home)) / 2
        score = max(direct, swapped)
        if score > best[0]:
            best = (score, fixture)
    return best[1] if best[0] >= 0.72 else None


def find_matching_odds_event(fixture: dict[str, Any], odds_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    teams = fixture.get("teams", {})
    home = teams.get("home", {}).get("name", "")
    away = teams.get("away", {}).get("name", "")
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for event in odds_events:
        e_home = event.get("home") or event.get("homeTeam") or ""
        e_away = event.get("away") or event.get("awayTeam") or ""
        direct = (similarity(home, e_home) + similarity(away, e_away)) / 2
        swapped = (similarity(home, e_away) + similarity(away, e_home)) / 2
        score = max(direct, swapped)
        if score > best[0]:
            best = (score, event)
    return best[1] if best[0] >= 0.72 else None
