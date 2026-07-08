from __future__ import annotations

from typing import Any

from .clients import ApiFootballClient
from .stats import compact_statistics, total_stat


def _settle_total(total: int, selection: str, line: float | None) -> tuple[str, str] | None:
    if line is None:
        return None
    if selection == "over":
        if total > line:
            return "WON", f"Total {total} acima da linha {line}."
        if total == line:
            return "PUSH", f"Total {total} igual a linha {line}."
        return "LOST", f"Total {total} abaixo da linha {line}."
    if selection == "under":
        if total < line:
            return "WON", f"Total {total} abaixo da linha {line}."
        if total == line:
            return "PUSH", f"Total {total} igual a linha {line}."
        return "LOST", f"Total {total} acima da linha {line}."
    return None


async def settle_alert(alert: dict[str, Any], api_football: ApiFootballClient) -> tuple[str, str] | None:
    fixture_id = alert.get("fixture_id")
    if not fixture_id:
        return None
    fixture = await api_football.fixture_by_id(int(fixture_id))
    if not fixture:
        return None
    status = fixture.get("fixture", {}).get("status", {}).get("short")
    if status not in {"FT", "AET", "PEN"}:
        return None

    market = str(alert.get("market", "")).lower()
    line = alert.get("line")
    selection = str(alert.get("selection", "")).lower()

    if "corner" in market or "escanteio" in market:
        stats = compact_statistics(await api_football.fixture_statistics(int(fixture_id)))
        corners = total_stat(stats, ("Corner Kicks", "Corners"))
        if corners is None:
            return None
        return _settle_total(corners, selection, line)

    goals = fixture.get("goals", {})
    home = goals.get("home")
    away = goals.get("away")
    if home is None or away is None:
        return None
    return _settle_total(int(home) + int(away), selection, line)
