from __future__ import annotations

from typing import Any

from .clients import ApiFootballClient
from .matching import find_matching_totalcorner_match, similarity
from .stats import compact_statistics, compact_totalcorner_statistics, total_stat


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


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _totalcorner_score(match: dict[str, Any]) -> tuple[int, int] | None:
    home = _to_int(match.get("hg"))
    away = _to_int(match.get("ag"))
    if home is None or away is None:
        return None
    return home, away


def _totalcorner_halftime_score(match: dict[str, Any]) -> tuple[int, int] | None:
    for home_key, away_key in (("h_hg", "h_ag"), ("ht_hg", "ht_ag"), ("half_hg", "half_ag")):
        home = _to_int(match.get(home_key))
        away = _to_int(match.get(away_key))
        if home is not None and away is not None:
            return home, away
    return None


def _is_first_half_market(market: str) -> bool:
    normalized = market.casefold()
    return any(token in normalized for token in ("first_half", "first half", "1º tempo", "1o tempo", "ht"))


def _totalcorner_finished(match: dict[str, Any]) -> bool:
    status = str(match.get("status") or match.get("time") or "").strip().lower()
    status_text = str(match.get("status_text") or match.get("state") or "").strip().lower()
    final_values = {"-1", "ft", "aet", "pen", "full", "full-time", "finished", "ended"}
    if status in final_values or status_text in final_values:
        return True
    if any(token in status for token in ("full", "finish", "ended")):
        return True
    if any(token in status_text for token in ("full", "finish", "ended")):
        return True
    return False


def _find_totalcorner_alert_match(alert: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    alert_home = str(alert.get("home") or "")
    alert_away = str(alert.get("away") or "")
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for match in matches:
        tc_home = str(match.get("h") or "")
        tc_away = str(match.get("a") or "")
        direct = (similarity(alert_home, tc_home) + similarity(alert_away, tc_away)) / 2
        swapped = (similarity(alert_home, tc_away) + similarity(alert_away, tc_home)) / 2
        score = max(direct, swapped)
        if score > best[0]:
            best = (score, match)
    return best[1] if best[0] >= 0.72 else None


def _settle_from_score(
    home: int,
    away: int,
    market: str,
    selection: str,
    line: float | None,
    home_name: str = "",
    away_name: str = "",
) -> tuple[str, str] | None:
    if "ambos" in market or "btts" in market or "marcam" in market:
        both_scored = home > 0 and away > 0
        if selection in {"yes", "sim"}:
            return ("WON", "Ambos os times marcaram.") if both_scored else ("LOST", "Um dos times nao marcou.")
        if selection in {"no", "nao", "não", "nÃ£o"}:
            return ("LOST", "Ambos os times marcaram.") if both_scored else ("WON", "Um dos times nao marcou.")
    if "chance dupla" in market or "double_chance" in market or "double chance" in market:
        normalized = selection.replace("_", " ").casefold()
        won = (
            (normalized in {"home draw", "home or draw", "casa empate", "1x"} and home >= away)
            or (normalized in {"draw away", "draw or away", "empate fora", "x2"} and away >= home)
            or (normalized in {"home away", "home or away", "casa fora", "12"} and home != away)
        )
        return ("WON", "Chance dupla vencedora.") if won else ("LOST", "Chance dupla perdedora.")
    if "empate anula" in market or "draw_no_bet" in market or "draw no bet" in market:
        if home == away:
            return "PUSH", "Empate: aposta anulada."
        selected_home = selection in {"home", "casa"} or (home_name and similarity(selection, home_name) >= 0.8)
        selected_away = selection in {"away", "fora"} or (away_name and similarity(selection, away_name) >= 0.8)
        won = (selected_home and home > away) or (selected_away and away > home)
        return ("WON", "Equipe selecionada venceu.") if won else ("LOST", "Equipe selecionada nao venceu.")
    if any(token in market for token in ("resultado", "vitoria", "vitória", "match_odds", "match odds")):
        if selection in {"draw", "empate", "x"}:
            won = home == away
        else:
            selected_home = selection in {"home", "casa", "1"} or (home_name and similarity(selection, home_name) >= 0.8)
            selected_away = selection in {"away", "fora", "2"} or (away_name and similarity(selection, away_name) >= 0.8)
            won = (selected_home and home > away) or (selected_away and away > home)
        return ("WON", "Resultado selecionado confirmado.") if won else ("LOST", "Resultado selecionado nao ocorreu.")
    return _settle_total(home + away, selection, line)


def _settle_from_totalcorner(
    match: dict[str, Any],
    market: str,
    selection: str,
    line: float | None,
) -> tuple[str, str] | None:
    market = market.lower()
    selection = selection.lower()
    if not _totalcorner_finished(match):
        return None
    if "corner" in market or "escanteio" in market:
        totalcorner_stats = compact_totalcorner_statistics(match)
        totalcorner_corners = total_stat(totalcorner_stats, ("Corner Kicks", "Corners"))
        if totalcorner_corners is None:
            return None
        settled = _settle_total(totalcorner_corners, selection, line)
        if settled:
            status, note = settled
            return status, f"{note} Fonte: TotalCorner."
        return None
    score = _totalcorner_halftime_score(match) if _is_first_half_market(market) else _totalcorner_score(match)
    if not score:
        return None
    settled = _settle_from_score(
        score[0], score[1], market, selection, line,
        str(match.get("h") or ""), str(match.get("a") or ""),
    )
    if settled:
        status, note = settled
        return status, f"{note} Placar final TotalCorner: {score[0]}x{score[1]}."
    return None


async def settle_alert(
    alert: dict[str, Any],
    api_football: ApiFootballClient,
    totalcorner_matches: list[dict[str, Any]] | None = None,
) -> tuple[str, str] | None:
    market = str(alert.get("market", "")).lower()
    line = alert.get("line")
    selection = str(alert.get("selection", "")).lower()

    if totalcorner_matches:
        totalcorner_match = _find_totalcorner_alert_match(alert, totalcorner_matches)
        if totalcorner_match:
            settled = _settle_from_totalcorner(totalcorner_match, market, selection, line)
            if settled:
                return settled

    fixture_id = alert.get("fixture_id")
    if not fixture_id:
        return None
    fixture = await api_football.fixture_by_id(int(fixture_id))
    if not fixture:
        return None
    status = fixture.get("fixture", {}).get("status", {}).get("short")
    if status not in {"FT", "AET", "PEN"}:
        return None

    if "corner" in market or "escanteio" in market:
        if totalcorner_matches:
            totalcorner_match = find_matching_totalcorner_match(fixture, totalcorner_matches)
            if totalcorner_match:
                settled = _settle_from_totalcorner(totalcorner_match, market, selection, line)
                if settled:
                    return settled
        stats = compact_statistics(await api_football.fixture_statistics(int(fixture_id)))
        corners = total_stat(stats, ("Corner Kicks", "Corners"))
        if corners is None:
            return None
        return _settle_total(corners, selection, line)

    goals = fixture.get("score", {}).get("halftime", {}) if _is_first_half_market(market) else fixture.get("goals", {})
    home = goals.get("home")
    away = goals.get("away")
    if home is None or away is None:
        return None
    teams = fixture.get("teams", {})
    return _settle_from_score(
        int(home), int(away), market, selection, line,
        str(teams.get("home", {}).get("name") or alert.get("home") or ""),
        str(teams.get("away", {}).get("name") or alert.get("away") or ""),
    )
