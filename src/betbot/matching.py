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


def sportmonks_participant_names(fixture: dict[str, Any]) -> tuple[str, str]:
    participants = fixture.get("participants", [])
    if isinstance(participants, dict):
        participants = participants.get("data", [])
    home = ""
    away = ""
    for participant in participants if isinstance(participants, list) else []:
        name = participant.get("name") or participant.get("display_name") or ""
        meta = participant.get("meta", {}) if isinstance(participant.get("meta"), dict) else {}
        location = str(meta.get("location") or "").lower()
        if location == "home":
            home = name
        elif location == "away":
            away = name
    if not home and len(participants) > 0:
        home = participants[0].get("name") or participants[0].get("display_name") or ""
    if not away and len(participants) > 1:
        away = participants[1].get("name") or participants[1].get("display_name") or ""
    return home, away


def find_matching_sportmonks_fixture(api_football_fixture: dict[str, Any], sportmonks_fixtures: list[dict[str, Any]]) -> dict[str, Any] | None:
    teams = api_football_fixture.get("teams", {})
    home = teams.get("home", {}).get("name", "")
    away = teams.get("away", {}).get("name", "")
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for fixture in sportmonks_fixtures:
        sm_home, sm_away = sportmonks_participant_names(fixture)
        direct = (similarity(home, sm_home) + similarity(away, sm_away)) / 2
        swapped = (similarity(home, sm_away) + similarity(away, sm_home)) / 2
        score = max(direct, swapped)
        if score > best[0]:
            best = (score, fixture)
    return best[1] if best[0] >= 0.70 else None


def find_matching_thestatsapi_match(api_football_fixture: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    teams = api_football_fixture.get("teams", {})
    home = teams.get("home", {}).get("name", "")
    away = teams.get("away", {}).get("name", "")
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for match in matches:
        home_team = match.get("home_team", {}) if isinstance(match.get("home_team"), dict) else {}
        away_team = match.get("away_team", {}) if isinstance(match.get("away_team"), dict) else {}
        ts_home = home_team.get("name", "")
        ts_away = away_team.get("name", "")
        direct = (similarity(home, ts_home) + similarity(away, ts_away)) / 2
        swapped = (similarity(home, ts_away) + similarity(away, ts_home)) / 2
        score = max(direct, swapped)
        if score > best[0]:
            best = (score, match)
    return best[1] if best[0] >= 0.70 else None
