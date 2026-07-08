from __future__ import annotations

from typing import Any

from .matching import sportmonks_participant_names


def extract_score(fixture: dict[str, Any] | None) -> tuple[int | None, int | None]:
    if not fixture:
        return None, None
    goals = fixture.get("goals", {})
    return goals.get("home"), goals.get("away")


def extract_minute(fixture: dict[str, Any] | None) -> int | None:
    if not fixture:
        return None
    return fixture.get("fixture", {}).get("status", {}).get("elapsed")


def compact_statistics(stats_response: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for team_stats in stats_response:
        team = team_stats.get("team", {}).get("name", "unknown")
        result[team] = {}
        for stat in team_stats.get("statistics", []):
            key = stat.get("type")
            value = stat.get("value")
            if key:
                result[team][key] = value
    return result


def compact_player_statistics(players_response: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for team_block in players_response:
        team = team_block.get("team", {}).get("name", "unknown")
        totals = {
            "Total Shots": 0,
            "Shots on Goal": 0,
            "Shots off Goal": 0,
        }
        found = False
        for player in team_block.get("players", []):
            stats_rows = player.get("statistics", [])
            if not stats_rows:
                continue
            row = stats_rows[0]
            shots = row.get("shots", {}) if isinstance(row.get("shots"), dict) else {}
            total = shots.get("total")
            on = shots.get("on")
            try:
                if total is not None:
                    totals["Total Shots"] += int(total)
                    found = True
                if on is not None:
                    totals["Shots on Goal"] += int(on)
                    found = True
                    if total is not None:
                        totals["Shots off Goal"] += max(0, int(total) - int(on))
            except (TypeError, ValueError):
                continue
        if found:
            result[team] = totals
    return result


SPORTMONKS_TYPE_ID_MAP = {
    34: "Corner Kicks",
    41: "Shots off Goal",
    42: "Total Shots",
    43: "Attacks",
    44: "Dangerous Attacks",
    45: "Ball Possession",
    86: "Shots on Goal",
}


def _sportmonks_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("value", "total", "count", "percentage"):
            if key in value:
                return value[key]
    return value


def _sportmonks_stat_name(stat: dict[str, Any]) -> str | None:
    type_data = stat.get("type") if isinstance(stat.get("type"), dict) else {}
    raw_name = type_data.get("name") or type_data.get("code") or stat.get("name")
    if raw_name:
        lowered = str(raw_name).lower()
        if "corner" in lowered:
            return "Corner Kicks"
        if "shot" in lowered and ("target" in lowered or "goal" in lowered):
            return "Shots on Goal"
        if "shot" in lowered and "off" in lowered:
            return "Shots off Goal"
        if "shot" in lowered:
            return "Total Shots"
        if "dangerous" in lowered and "attack" in lowered:
            return "Dangerous Attacks"
        if lowered.strip() == "attacks" or " attack" in lowered:
            return "Attacks"
        if "possession" in lowered:
            return "Ball Possession"
    type_id = stat.get("type_id")
    try:
        return SPORTMONKS_TYPE_ID_MAP.get(int(type_id))
    except (TypeError, ValueError):
        return None


def compact_sportmonks_statistics(fixture: dict[str, Any]) -> dict[str, Any]:
    home, away = sportmonks_participant_names(fixture)
    participants = fixture.get("participants", [])
    if isinstance(participants, dict):
        participants = participants.get("data", [])
    id_to_team = {}
    for participant in participants if isinstance(participants, list) else []:
        participant_id = participant.get("id")
        name = participant.get("name") or participant.get("display_name") or ""
        if participant_id and name:
            id_to_team[participant_id] = name
    result = {home: {}, away: {}} if home or away else {}
    statistics = fixture.get("statistics", [])
    if isinstance(statistics, dict):
        statistics = statistics.get("data", [])
    for stat in statistics if isinstance(statistics, list) else []:
        name = _sportmonks_stat_name(stat)
        if not name:
            continue
        participant_id = stat.get("participant_id") or stat.get("team_id")
        team_name = id_to_team.get(participant_id)
        if not team_name:
            location = str(stat.get("location") or "").lower()
            team_name = home if location == "home" else away if location == "away" else None
        if not team_name:
            continue
        result.setdefault(team_name, {})[name] = _sportmonks_value(stat.get("data") if "data" in stat else stat.get("value"))
    return {team: values for team, values in result.items() if values}


def total_stat(stats: dict[str, Any], names: tuple[str, ...]) -> int | None:
    total = 0
    found = False
    wanted = {name.lower() for name in names}
    for team_stats in stats.values():
        for key, value in team_stats.items():
            if str(key).lower() in wanted:
                try:
                    total += int(value)
                    found = True
                except (TypeError, ValueError):
                    pass
    return total if found else None


def stats_value(stats: dict[str, Any], team: str, names: tuple[str, ...]) -> Any:
    wanted = {name.lower() for name in names}
    team_stats = stats.get(team, {})
    for key, value in team_stats.items():
        if str(key).lower() in wanted:
            return value
    return None


def compact_stats_summary(stats: dict[str, Any]) -> str:
    if not stats:
        return "Estatisticas detalhadas indisponiveis."
    lines = []
    for team, team_stats in stats.items():
        shots_on = stats_value(stats, team, ("Shots on Goal", "Shots on target"))
        shots_off = stats_value(stats, team, ("Shots off Goal", "Shots off target"))
        total_shots = stats_value(stats, team, ("Total Shots",))
        attacks = stats_value(stats, team, ("Attacks",))
        dangerous = stats_value(stats, team, ("Dangerous Attacks",))
        corners = stats_value(stats, team, ("Corner Kicks", "Corners"))
        possession = stats_value(stats, team, ("Ball Possession",))
        pieces = []
        if possession is not None:
            pieces.append(f"posse {possession}")
        if total_shots is not None:
            pieces.append(f"chutes {total_shots}")
        if shots_on is not None:
            pieces.append(f"no gol {shots_on}")
        if shots_off is not None:
            pieces.append(f"fora {shots_off}")
        if corners is not None:
            pieces.append(f"escanteios {corners}")
        if attacks is not None:
            pieces.append(f"ataques {attacks}")
        if dangerous is not None:
            pieces.append(f"ataques perigosos {dangerous}")
        lines.append(f"{team}: " + (", ".join(pieces) if pieces else "sem estatisticas principais"))
    return "\n".join(lines)


def has_actionable_stats(stats: dict[str, Any]) -> bool:
    if not stats:
        return False
    important = {
        "shots on goal",
        "shots on target",
        "total shots",
        "corner kicks",
        "corners",
        "dangerous attacks",
        "attacks",
        "ball possession",
    }
    found = 0
    for team_stats in stats.values():
        for key, value in team_stats.items():
            if str(key).lower() in important and value not in (None, "", "0%"):
                found += 1
    return found >= 3


def is_high_variance_match(league: str, home: str, away: str) -> bool:
    text = f"{league} {home} {away}".lower()
    markers = ("friendly", "friendlies", "amistoso", "u20", "u19", "u21", "u23", "reserves", "women")
    return any(marker in text for marker in markers)
