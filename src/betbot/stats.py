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
