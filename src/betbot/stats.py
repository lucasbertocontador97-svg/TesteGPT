from __future__ import annotations

from typing import Any


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
