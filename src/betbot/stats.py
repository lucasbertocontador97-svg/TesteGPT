from __future__ import annotations

import re
from typing import Any

from .matching import sofascore_team_names, sportmonks_participant_names


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


def _thestatsapi_pair(section: dict[str, Any], key: str) -> tuple[Any, Any]:
    value = section.get(key, {})
    all_value = value.get("all", {}) if isinstance(value, dict) else {}
    return all_value.get("home"), all_value.get("away")


def compact_thestatsapi_statistics(match: dict[str, Any], stats_response: dict[str, Any]) -> dict[str, Any]:
    home = match.get("home_team", {}).get("name", "home") if isinstance(match.get("home_team"), dict) else "home"
    away = match.get("away_team", {}).get("name", "away") if isinstance(match.get("away_team"), dict) else "away"
    overview = stats_response.get("overview", {}) if isinstance(stats_response.get("overview"), dict) else {}
    mapping = {
        "ball_possession": "Ball Possession",
        "total_shots": "Total Shots",
        "shots_on_target": "Shots on Goal",
        "corner_kicks": "Corner Kicks",
        "expected_goals": "Expected Goals",
        "big_chances": "Big Chances",
    }
    result = {home: {}, away: {}}
    for source, target in mapping.items():
        home_value, away_value = _thestatsapi_pair(overview, source)
        if home_value is not None:
            result[home][target] = home_value
        if away_value is not None:
            result[away][target] = away_value
    return {team: values for team, values in result.items() if values}


def _sportdb_side_name(match: dict[str, Any], side: str) -> str:
    for key in (f"{side}Name", f"{side}_name", f"{side}TeamName"):
        value = match.get(key)
        if value:
            return str(value)
    team = match.get(side) or match.get(f"{side}Team") or match.get(f"{side}_team")
    if isinstance(team, dict):
        for key in ("name", "shortName", "displayName"):
            value = team.get(key)
            if value:
                return str(value)
    return side


def _sportdb_number(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return value
    number = float(match.group(0))
    return int(number) if number.is_integer() else number


def _sportdb_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("data", "stats", "statistics", "periods", "items", "response"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _sportdb_stat_rows(stats_response: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    items = _sportdb_items(stats_response)
    if not items:
        return []
    period_candidates = []
    for item in items:
        rows = item.get("stats") or item.get("statistics") or item.get("items")
        if isinstance(rows, list):
            label = str(item.get("period") or item.get("periodName") or item.get("name") or "").lower()
            period_candidates.append((label, [row for row in rows if isinstance(row, dict)]))
    if not period_candidates:
        return items
    preferred = ("match", "full time", "fulltime", "total", "all")
    for label, rows in period_candidates:
        if any(name in label for name in preferred):
            return rows
    return period_candidates[0][1]


SPORTDB_STAT_MAP = {
    "ball possession": "Ball Possession",
    "expected goals (xg)": "Expected goals (xG)",
    "xg on target (xgot)": "xG on target (xGOT)",
    "total shots": "Total Shots",
    "shots on target": "Shots on Goal",
    "shots off target": "Shots off Goal",
    "blocked shots": "Blocked Shots",
    "corner kicks": "Corner Kicks",
    "big chances": "Big Chances",
    "big chances missed": "Big Chances Missed",
    "shots inside the box": "Shots inside the box",
    "shots outside the box": "Shots outside the box",
    "touches in opposition box": "Touches in opposition box",
    "passes in final third": "Passes in final third",
    "fouls": "Fouls",
    "goalkeeper saves": "Goalkeeper saves",
}


def compact_sportdb_statistics(match: dict[str, Any], stats_response: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
    home = _sportdb_side_name(match, "home")
    away = _sportdb_side_name(match, "away")
    result: dict[str, dict[str, Any]] = {home: {}, away: {}}
    for row in _sportdb_stat_rows(stats_response):
        raw_name = row.get("statName") or row.get("name") or row.get("title") or row.get("type")
        if not raw_name:
            continue
        name = SPORTDB_STAT_MAP.get(str(raw_name).strip().lower(), str(raw_name).strip())
        home_value = row.get("homeValue", row.get("home", row.get("home_value")))
        away_value = row.get("awayValue", row.get("away", row.get("away_value")))
        if home_value is not None:
            result[home][name] = _sportdb_number(home_value)
        if away_value is not None:
            result[away][name] = _sportdb_number(away_value)

    # Do not manufacture provider fields. Missing live metrics must remain
    # missing so the decision engine can fail closed.
    return {team: values for team, values in result.items() if values}


SOFASCORE_STAT_MAP = {
    "ballpossession": "Ball Possession",
    "ball possession": "Ball Possession",
    "expectedgoals": "Expected goals (xG)",
    "expected goals": "Expected goals (xG)",
    "xg": "Expected goals (xG)",
    "totalshotsongoal": "Total Shots",
    "total shots": "Total Shots",
    "shotsongoal": "Shots on Goal",
    "shots on target": "Shots on Goal",
    "shots off target": "Shots off Goal",
    "shotsoffgoal": "Shots off Goal",
    "blockedshots": "Blocked Shots",
    "blocked shots": "Blocked Shots",
    "cornerkicks": "Corner Kicks",
    "corner kicks": "Corner Kicks",
    "corners": "Corner Kicks",
    "bigchances": "Big Chances",
    "big chances": "Big Chances",
    "shotsinsidethebox": "Shots inside the box",
    "shots inside the box": "Shots inside the box",
    "shotsoutsidethebox": "Shots outside the box",
    "shots outside the box": "Shots outside the box",
    "fouls": "Fouls",
    "yellowcards": "Yellow Cards",
    "yellow cards": "Yellow Cards",
    "redcards": "Red Cards",
    "red cards": "Red Cards",
    "goalkeepersaves": "Goalkeeper saves",
    "goalkeeper saves": "Goalkeeper saves",
    "passes": "Total passes",
    "accuratepasses": "Accurate passes",
    "accurate passes": "Accurate passes",
    "tackles": "Tackles",
}


def _sofascore_number(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "").replace(",", ".")
        if cleaned == "":
            return value
        try:
            number = float(cleaned)
            return int(number) if number.is_integer() else number
        except ValueError:
            return value
    return value


def _sofascore_stats_blocks(stats_response: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(stats_response, list):
        return [item for item in stats_response if isinstance(item, dict)]
    if not isinstance(stats_response, dict):
        return []
    for key in ("statistics", "data", "response"):
        value = stats_response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _sofascore_stats_blocks(value)
            if nested:
                return nested
    return []


def _sofascore_items(stats_response: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    blocks = _sofascore_stats_blocks(stats_response)
    if not blocks:
        return []
    selected = blocks[0]
    for block in blocks:
        period = str(block.get("period") or block.get("periodName") or block.get("name") or "").upper()
        if period in {"ALL", "MATCH", "FULLTIME", "REGULAR"}:
            selected = block
            break
    groups = selected.get("groups") or selected.get("statisticsGroups") or selected.get("items") or []
    if isinstance(groups, dict):
        groups = groups.get("data", []) or groups.get("groups", [])
    items: list[dict[str, Any]] = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        group_items = group.get("statisticsItems") or group.get("items") or []
        if isinstance(group_items, list):
            items.extend(item for item in group_items if isinstance(item, dict))
    return items


def compact_sofascore_statistics(
    match: dict[str, Any],
    stats_response: dict[str, Any] | list[dict[str, Any]] | None,
) -> dict[str, Any]:
    home, away = sofascore_team_names(match)
    home = home or "home"
    away = away or "away"
    result: dict[str, dict[str, Any]] = {home: {}, away: {}}

    for item in _sofascore_items(stats_response):
        raw_name = item.get("key") or item.get("name") or item.get("title")
        if not raw_name:
            continue
        lowered = str(raw_name).strip().lower()
        name = SOFASCORE_STAT_MAP.get(lowered) or SOFASCORE_STAT_MAP.get(re.sub(r"[^a-z0-9]+", "", lowered))
        if not name:
            continue
        home_value = item.get("home", item.get("homeValue", item.get("home_value")))
        away_value = item.get("away", item.get("awayValue", item.get("away_value")))
        if home_value is not None:
            result[home][name] = _sofascore_number(home_value)
        if away_value is not None:
            result[away][name] = _sofascore_number(away_value)

    # Preserve only values actually returned by SofaScore.
    return {team: values for team, values in result.items() if values}


def _sofascore_package_data(package: dict[str, Any], source: str) -> Any:
    sources = package.get("sources", {}) if isinstance(package, dict) else {}
    payload = sources.get(source, {}) if isinstance(sources, dict) else {}
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    return payload.get("data")


def _walk_dict_items(data: Any, preferred_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in preferred_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _walk_dict_items(value, preferred_keys)
            if nested:
                return nested
    return []


def _shot_team_name(shot: dict[str, Any]) -> str:
    team = shot.get("team") or shot.get("playerTeam") or shot.get("participant")
    if isinstance(team, dict):
        for key in ("name", "shortName", "displayName"):
            value = team.get(key)
            if value:
                return str(value)
    return ""


def _shot_is_on_target(shot: dict[str, Any]) -> bool:
    for key in ("onGoalShot", "isOnTarget", "onTarget"):
        if isinstance(shot.get(key), bool):
            return bool(shot.get(key))
    shot_type = str(shot.get("shotType") or shot.get("type") or "").lower()
    situation = str(shot.get("goalType") or shot.get("bodyPart") or "").lower()
    return any(token in shot_type or token in situation for token in ("goal", "save", "post", "on target"))


def _shot_xg(shot: dict[str, Any]) -> float:
    for key in ("xg", "expectedGoals", "expectedGoal", "xG"):
        try:
            if shot.get(key) is not None:
                return float(str(shot.get(key)).replace(",", "."))
        except (TypeError, ValueError):
            continue
    return 0.0


def _shotmap_summary(match: dict[str, Any], shotmap_response: Any) -> dict[str, dict[str, Any]]:
    home, away = sofascore_team_names(match)
    home = home or "home"
    away = away or "away"
    result: dict[str, dict[str, Any]] = {
        home: {"shots": 0, "on_target": 0, "goals": 0, "xg": 0.0},
        away: {"shots": 0, "on_target": 0, "goals": 0, "xg": 0.0},
    }
    shots = _walk_dict_items(shotmap_response, ("shotmap", "shots", "data", "items", "response"))
    for shot in shots:
        team = None
        if isinstance(shot.get("isHome"), bool):
            team = home if shot.get("isHome") else away
        else:
            shot_team = _shot_team_name(shot).lower()
            if shot_team and shot_team in home.lower():
                team = home
            elif shot_team and shot_team in away.lower():
                team = away
        if team not in result:
            continue
        result[team]["shots"] += 1
        if _shot_is_on_target(shot):
            result[team]["on_target"] += 1
        if shot.get("isGoal") is True or str(shot.get("shotType") or "").lower() == "goal":
            result[team]["goals"] += 1
        result[team]["xg"] = round(float(result[team]["xg"]) + _shot_xg(shot), 3)
    return {team: values for team, values in result.items() if values.get("shots")}


def compact_sofascore_package(match: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    stats = compact_sofascore_statistics(match, _sofascore_package_data(package, "statistics"))
    shot_summary = _shotmap_summary(match, _sofascore_package_data(package, "shotmap"))
    for team, values in shot_summary.items():
        team_stats = stats.setdefault(team, {})
        team_stats.setdefault("Shotmap Shots", values.get("shots"))
        team_stats.setdefault("Shotmap On Target", values.get("on_target"))
        team_stats.setdefault("Shotmap xG", values.get("xg"))
        team_stats.setdefault("Total Shots", values.get("shots"))
        team_stats.setdefault("Shots on Goal", values.get("on_target"))
        team_stats.setdefault("Expected goals (xG)", values.get("xg"))

    return {team: values for team, values in stats.items() if values}


def _limit_dict(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: data.get(key) for key in keys if data.get(key) is not None}


def _compact_incidents(data: Any, limit: int = 18) -> list[dict[str, Any]]:
    items = _walk_dict_items(data, ("incidents", "events", "data", "items", "response"))
    compacted = []
    for item in items[:limit]:
        player = item.get("player") or item.get("playerIn") or item.get("playerOut")
        team = item.get("team")
        compacted.append(
            {
                "time": item.get("time") or item.get("minute"),
                "type": item.get("incidentType") or item.get("type"),
                "detail": item.get("text") or item.get("reason") or item.get("incidentClass"),
                "home_score": item.get("homeScore"),
                "away_score": item.get("awayScore"),
                "player": player.get("name") if isinstance(player, dict) else player,
                "team": team.get("name") if isinstance(team, dict) else team,
            }
        )
    return compacted


def _compact_odds(data: Any) -> dict[str, Any]:
    items = _walk_dict_items(data, ("markets", "odds", "data", "items", "response"))
    sample = []
    for item in items[:10]:
        sample.append(
            _limit_dict(
                item,
                ("marketName", "name", "choice", "value", "odd", "fractionalValue", "decimalValue"),
            )
        )
    return {"available": bool(items), "markets_seen": len(items), "sample": sample}


def _compact_lineups(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"available": False}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    home = root.get("home") or root.get("homeLineup") or {}
    away = root.get("away") or root.get("awayLineup") or {}
    return {
        "available": bool(home or away),
        "confirmed": root.get("confirmed") or root.get("isConfirmed"),
        "home_formation": home.get("formation") if isinstance(home, dict) else None,
        "away_formation": away.get("formation") if isinstance(away, dict) else None,
    }


def _compact_named_items(data: Any, preferred: tuple[str, ...], limit: int = 8) -> list[dict[str, Any]]:
    rows = _walk_dict_items(data, preferred)
    result = []
    for row in rows[:limit]:
        player = row.get("player") if isinstance(row.get("player"), dict) else row
        result.append(_limit_dict(player, ("name", "shortName", "position", "rating", "averageRating")))
    return result


def summarize_sofascore_package(match: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    sources = package.get("sources", {}) if isinstance(package, dict) else {}
    coverage = package.get("coverage", {}) if isinstance(package, dict) else {}
    details = _sofascore_package_data(package, "details")
    detail_root = details.get("event", details.get("data", details)) if isinstance(details, dict) else {}
    return {
        "provider": "zyla_sofascore",
        "match_id": package.get("match_id"),
        "coverage": coverage,
        "endpoints_ok": [name for name, ok in coverage.items() if ok],
        "endpoints_failed": [
            {"name": name, "status": payload.get("status"), "error": payload.get("error")}
            for name, payload in sources.items()
            if isinstance(payload, dict) and not payload.get("ok")
        ][:10],
        "details": _limit_dict(
            detail_root if isinstance(detail_root, dict) else {},
            ("id", "slug", "status", "startTimestamp", "homeScore", "awayScore", "tournament", "venue"),
        ),
        "incidents": _compact_incidents(_sofascore_package_data(package, "incidents")),
        "shotmap": _shotmap_summary(match, _sofascore_package_data(package, "shotmap")),
        "lineups": _compact_lineups(_sofascore_package_data(package, "lineups")),
        "odds": _compact_odds(_sofascore_package_data(package, "odds")),
        "pregame_form": {
            "available": _sofascore_package_data(package, "pregame_form") is not None,
            "sample": _limit_dict(
                _sofascore_package_data(package, "pregame_form")
                if isinstance(_sofascore_package_data(package, "pregame_form"), dict)
                else {},
                ("homeTeam", "awayTeam", "home", "away", "form"),
            ),
        },
        "best_players": _compact_named_items(_sofascore_package_data(package, "best_players"), ("players", "data", "items", "response")),
        "player_average_positions_available": _sofascore_package_data(package, "player_average_positions") is not None,
    }


def _to_int_or_raw(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _totalcorner_pair(match: dict[str, Any], *keys: str) -> tuple[Any, Any]:
    for key in keys:
        value = match.get(key)
        if isinstance(value, list) and len(value) >= 2:
            return _to_int_or_raw(value[0]), _to_int_or_raw(value[1])
    return None, None


def compact_totalcorner_statistics(match: dict[str, Any]) -> dict[str, Any]:
    home = str(match.get("h") or "home")
    away = str(match.get("a") or "away")
    result = {home: {}, away: {}}

    if match.get("hc") is not None:
        result[home]["Corner Kicks"] = _to_int_or_raw(match.get("hc"))
    if match.get("ac") is not None:
        result[away]["Corner Kicks"] = _to_int_or_raw(match.get("ac"))

    pair_fields = [
        (("shot_on", "shotOn"), "Shots on Goal"),
        (("shot_off", "shotOff"), "Shots off Goal"),
        (("attacks",), "Attacks"),
        (("dang_attacks", "dangerous_attacks", "dangerousAttacks", "dangerous"), "Dangerous Attacks"),
        (("possess", "possession"), "Ball Possession"),
    ]
    for keys, target in pair_fields:
        home_value, away_value = _totalcorner_pair(match, *keys)
        if home_value is not None:
            result[home][target] = home_value
        if away_value is not None:
            result[away][target] = away_value

    for team in (home, away):
        shots_on = result[team].get("Shots on Goal")
        shots_off = result[team].get("Shots off Goal")
        if isinstance(shots_on, int) and isinstance(shots_off, int):
            result[team]["Total Shots"] = shots_on + shots_off

    return {team: values for team, values in result.items() if values}


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
    pressure_stats = {
        "shots on goal",
        "shots on target",
        "total shots",
        "corner kicks",
        "corners",
        "dangerous attacks",
        "attacks",
    }

    def meaningful(value: Any) -> bool:
        if value in (None, ""):
            return False
        if isinstance(value, str):
            cleaned = value.strip().replace("%", "")
            if cleaned in ("", "0", "0.0"):
                return False
            try:
                return float(cleaned) > 0
            except ValueError:
                return True
        try:
            return float(value) > 0
        except (TypeError, ValueError):
            return True

    found = 0
    pressure_found = 0
    for team_stats in stats.values():
        for key, value in team_stats.items():
            lowered = str(key).lower()
            if lowered in important and meaningful(value):
                found += 1
                if lowered in pressure_stats:
                    pressure_found += 1
    return found >= 3 and pressure_found >= 2


def is_high_variance_match(league: str, home: str, away: str) -> bool:
    text = f"{league} {home} {away}".lower()
    markers = ("friendly", "friendlies", "amistoso", "u20", "u19", "u21", "u23", "reserves", "women")
    return any(marker in text for marker in markers)


def is_blocked_match_type(league: str, home: str, away: str) -> bool:
    text = f"{league} {home} {away}".lower()
    blocked_markers = (
        "(w)",
        " women",
        " feminino",
        "feminino ",
        " esoccer",
        "e-soccer",
        " esports",
        " cyber",
        " simulated",
        " simulation",
        "srl",
        " ebasket",
    )
    if any(marker in text for marker in blocked_markers):
        return True
    if "(" in home and ")" in home:
        return True
    if "(" in away and ")" in away:
        return True
    return False
