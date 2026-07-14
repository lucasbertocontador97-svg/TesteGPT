from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .bfbm_markets import find_bfbm_market, map_selection_to_event, market_family, market_line, normalize_text, row_market_family


BFBM_COLUMNS = [
    "Provider",
    "Handicap",
    "SelectionId",
    "MarketId",
    "EventId",
    "SelectionName",
    "MarketName",
    "EventName",
    "MarketType",
    "StartTime",
    "BetType",
    "Size",
    "Points",
    "Price",
    "MinPrice",
    "MaxPrice",
    "BSP",
]

BFBM_ACCEPTED_COLUMNS = ["Provider", "SelectionName", "MarketType", "EventName", "BetType", "Size"]
BFBM_RICH_COLUMNS = [
    "Provider",
    "Handicap",
    "SelectionName",
    "MarketName",
    "EventName",
    "MarketType",
    "BetType",
    "Size",
    "Points",
    "Price",
    "MinPrice",
    "MaxPrice",
    "BSP",
]


@dataclass(frozen=True)
class BfbmConfig:
    provider: str
    stake: float
    min_price: float
    max_price: float


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _line_text(line: float) -> str:
    return f"{line:g}"


def _bfbm_line_text(line: float) -> str:
    return _line_text(line).replace(".", ",")


def _over_under_market_type(line: float) -> str | None:
    doubled = line * 2
    if abs(doubled - round(doubled)) > 0.001:
        return None
    if int(round(doubled)) % 2 == 0:
        return None
    return f"OVER_UNDER_{int(round(line * 10)):02d}"


def _first_half_goals_market_type(line: float) -> str | None:
    doubled = line * 2
    if abs(doubled - round(doubled)) > 0.001:
        return None
    if int(round(doubled)) % 2 == 0:
        return None
    return f"FIRST_HALF_GOALS_{int(round(line * 10)):02d}"


def _corner_market_type(line: float) -> str | None:
    doubled = line * 2
    if abs(doubled - round(doubled)) > 0.001:
        return None
    if int(round(doubled)) % 2 == 0:
        return None
    return f"OVER_UNDER_{int(round(line * 10)):02d}_CORNR"


def _event_name(alert: dict[str, Any]) -> str:
    home = str(alert.get("home", "") or "").strip()
    away = str(alert.get("away", "") or "").strip()
    if home and away:
        return f"{home} v {away}"
    return home or away


def _bfbm_event_aliases(event_name: str) -> list[str]:
    aliases: list[str] = []
    if " v " in event_name:
        aliases.append(event_name.replace(" v ", " x "))
    for candidate in list([event_name, *aliases]):
        variants = {candidate}
        if "Nublense" in candidate:
            variants.add(candidate.replace("Nublense", "Ñublense"))
        if "O'Higgins" in candidate:
            variants.add(candidate.replace("O'Higgins", "OHiggins"))
        for variant in list(variants):
            if "Nublense" in variant and "O'Higgins" in variant:
                variants.add(variant.replace("Nublense", "Ñublense"))
                variants.add(variant.replace("O'Higgins", "OHiggins"))
                variants.add(variant.replace("Nublense", "Ñublense").replace("O'Higgins", "OHiggins"))
        aliases.extend(variants)
    seen: set[str] = set()
    return [alias for alias in aliases if alias and alias != event_name and not (alias in seen or seen.add(alias))]


def _bfbm_name_alias(value: str) -> str:
    return value.replace("Nublense", "Ñublense").replace("O'Higgins", "OHiggins")


def _default_start_time() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _id_or_zero(value: Any) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        return "0"
    return text if text else "0"


def _start_time_or_empty_default(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "0001-01-01 00:00:00"


def _bfbm_clean_name(value: str) -> str:
    aliases = {
        "Nublense": "\u00d1ublense",
        "O'Higgins": "OHiggins",
        "Nacional Potosi": "Nacional Potos\u00ed",
        "Club Aurora": "Aurora",
        "America de Cali": "Am\u00e9rica de Cali",
        "Shandong Luneng": "Shandong Taishan",
        "Shandong Luneng Taishan": "Shandong Taishan",
    }
    cleaned = value
    for source, target in aliases.items():
        cleaned = cleaned.replace(source, target)
    cleaned = cleaned.strip()
    if cleaned.casefold().startswith("fc "):
        cleaned = cleaned[3:].strip()
    if cleaned.casefold().endswith(" fc"):
        cleaned = cleaned[:-3].strip()
    return cleaned


def _bfbm_clean_event_name(event_name: str) -> str:
    return _bfbm_clean_name(event_name.replace(" v ", " x "))


def _goal_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    line = _num(alert.get("line"))
    if line is None:
        return None
    strategy = str(alert.get("strategy") or "").upper()
    is_first_half = strategy.endswith("_HT") or "FIRST_HALF" in strategy
    market_type = _first_half_goals_market_type(line) if is_first_half else _over_under_market_type(line)
    if not market_type:
        return None
    side = "Mais" if str(alert.get("selection", "")).lower() == "over" else "Menos"
    display_line = _bfbm_line_text(line)
    market_name = f"Mais/Menos de {display_line} Gols"
    if is_first_half:
        market_name = f"Mais/Menos de {display_line} Gols no 1º Tempo"
    return {
        "MarketType": market_type,
        "MarketName": market_name,
        "SelectionName": f"{side} de {display_line} Gols",
        "__line": str(line),
    }


def _corner_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    line = _num(alert.get("line"))
    if line is None:
        return None
    side = "Over" if str(alert.get("selection", "")).lower() == "over" else "Under"
    return {
        "MarketType": _corner_market_type(line) or "CORNER_ODDS",
        "MarketName": f"Mais/Menos de {_bfbm_line_text(line)} Escanteios",
        "SelectionName": f"{'Mais' if side == 'Over' else 'Menos'} de {_bfbm_line_text(line)} escanteios",
        "__line": str(line),
    }


def _match_odds_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    selection = str(alert.get("selection", "") or "").strip()
    if not selection:
        return None
    return {
        "MarketType": "MATCH_ODDS",
        "MarketName": "Resultado da partida",
        "SelectionName": selection,
    }


def _btts_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    selection = str(alert.get("selection", "") or "").strip().lower()
    if selection in {"yes", "sim"}:
        selection_name = "Sim"
    elif selection in {"no", "nao", "não"}:
        selection_name = "Não"
    else:
        return None
    return {
        "MarketType": "BOTH_TEAMS_TO_SCORE",
        "MarketName": "Ambos os times marcam?",
        "SelectionName": selection_name,
    }


def _draw_no_bet_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    selection = str(alert.get("selection", "") or "").strip()
    if not selection:
        return None
    return {
        "MarketType": "DRAW_NO_BET",
        "MarketName": "Empate Anula Aposta",
        "SelectionName": selection,
    }


def _double_chance_tip(alert: dict[str, Any]) -> dict[str, str] | None:
    selection = normalize_text(alert.get("selection", ""))
    aliases = {
        "home draw": "Home or Draw",
        "home_draw": "Home or Draw",
        "casa empate": "Home or Draw",
        "1x": "Home or Draw",
        "draw away": "Draw or Away",
        "draw_away": "Draw or Away",
        "empate fora": "Draw or Away",
        "x2": "Draw or Away",
        "home away": "Home or Away",
        "home_away": "Home or Away",
        "casa fora": "Home or Away",
        "12": "Home or Away",
    }
    selection_name = aliases.get(selection, str(alert.get("selection", "") or "").strip())
    if not selection_name:
        return None
    return {
        "MarketType": "DOUBLE_CHANCE",
        "MarketName": "Chance Dupla",
        "SelectionName": selection_name,
    }


def _tip_market(alert: dict[str, Any]) -> dict[str, str] | None:
    market = str(alert.get("market", "")).lower()
    if market in {"draw_no_bet", "draw no bet"} or any(word in market for word in ("empate anula", "anula")):
        return _draw_no_bet_tip(alert)
    if market in {"double_chance", "double chance"} or any(word in market for word in ("chance dupla",)):
        return _double_chance_tip(alert)
    if market in {"match_odds", "match odds"} or any(word in market for word in ("resultado", "vitoria", "vitória")):
        return _match_odds_tip(alert)
    if market in {"btts", "both_teams_to_score"} or any(word in market for word in ("ambos", "marcam")):
        return _btts_tip(alert)
    if market in {"goals", "first_half_goals"} or any(word in market for word in ("goal", "gol")):
        return _goal_tip(alert)
    if market in {"corners", "corner_odds"} or any(word in market for word in ("corner", "escanteio")):
        return _corner_tip(alert)
    return None


def alert_to_bfbm_row(alert: dict[str, Any], config: BfbmConfig) -> dict[str, str] | None:
    market = _tip_market(alert)
    event_name = _event_name(alert)
    if str(alert.get("league", "")).upper() == "BFBM TESTE SIMPLES" or str(alert.get("event_id", "")).startswith("bfbm-simple-"):
        event_name = ""
    if not market or not market.get("SelectionName"):
        return None
    price = _num(alert.get("odd")) or 0.0
    price_text = f"{price:.2f}" if price > 0 else ""
    is_match_odds = market.get("MarketType") == "MATCH_ODDS"
    stake_text = "1.00" if is_match_odds else f"{config.stake:.2f}"
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": _id_or_zero(alert.get("selection_id") or alert.get("SelectionId")),
        "MarketId": _id_or_zero(alert.get("market_id") or alert.get("MarketId")),
        "EventId": _id_or_zero(alert.get("betfair_event_id") or alert.get("EventId")),
        "SelectionName": market["SelectionName"],
        "MarketName": market["MarketName"],
        "EventName": event_name,
        "MarketType": market["MarketType"],
        "StartTime": _start_time_or_empty_default(alert.get("start_time") or alert.get("StartTime")),
        "BetType": "BACK",
        "Price": price_text,
        "Size": stake_text,
        "Points": "1",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": "100.00" if is_match_odds else f"{config.max_price:.2f}",
        "BSP": "False",
        "__line": market.get("__line", ""),
    }
    return row


def _catalog_raw(market: dict[str, Any]) -> dict[str, Any]:
    raw = market.get("raw")
    if isinstance(raw, dict):
        return raw
    raw_json = market.get("raw_json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _catalog_runners(market: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _catalog_raw(market)
    runners = raw.get("runners") or market.get("runners") or []
    return [runner for runner in runners if isinstance(runner, dict)]


def _runner_name(runner: dict[str, Any]) -> str:
    return str(runner.get("runnerName") or runner.get("runner_name") or runner.get("name") or "")


def _runner_selection_id(runner: dict[str, Any]) -> str:
    return str(runner.get("selectionId") or runner.get("selection_id") or runner.get("id") or "")


def _selection_aliases_for_runner(row: dict[str, str], market: dict[str, Any]) -> list[str]:
    family = row_market_family(market)
    selection = str(row.get("SelectionName") or "").strip()
    aliases = {selection}
    line = (market_line(str(market.get("market_name") or market.get("MarketName") or "")) or market_line(str(market.get("market_type") or market.get("MarketType") or "")) or _num(row.get("__line")))
    line_dot = f"{line:g}" if line is not None else ""
    line_comma = line_dot.replace(".", ",")
    selection_norm = normalize_text(selection)
    is_under = any(token in selection_norm for token in ("menos", "under", "no", "nao", "não"))
    if family in {"goals", "first_half_goals"} and line_dot:
        side_en = "Under" if is_under else "Over"
        side_pt = "Menos" if is_under else "Mais"
        aliases.update(
            {
                f"{side_en} {line_dot} Goals",
                f"{side_en} {line_dot} goals",
                f"{side_pt} de {line_comma} Gols",
                f"{side_pt} de {line_dot} Gols",
                f"{side_pt} de {line_comma} gols",
                f"{side_pt} de {line_dot} gols",
            }
        )
    if family == "corners" and line_dot:
        side_en = "Under" if is_under else "Over"
        side_pt = "Menos" if is_under else "Mais"
        aliases.update(
            {
                f"{side_en} {line_dot} Corners",
                f"{side_en} {line_dot} corners",
                f"{side_pt} de {line_comma} Escanteios",
                f"{side_pt} de {line_dot} Escanteios",
                f"{side_pt} de {line_comma} escanteios",
                f"{side_pt} de {line_dot} escanteios",
            }
        )
    if family == "btts":
        aliases.update({"No", "Nao", "Não", "NÃ£o"} if is_under else {"Yes", "Sim"})
    if family in {"match_odds", "draw_no_bet"}:
        aliases.add(map_selection_to_event(selection, str(market.get("event_name") or "")))
    if family == "double_chance":
        normalized = normalize_text(selection)
        event_name = str(market.get("event_name") or market.get("EventName") or "")
        home = away = ""
        if " x " in event_name:
            home, away = [part.strip() for part in event_name.split(" x ", 1)]
        elif " v " in event_name:
            home, away = [part.strip() for part in event_name.split(" v ", 1)]
        if normalized in {"home or draw", "casa empate", "1x"}:
            aliases.update({"Home or Draw", "1X"})
            if home:
                aliases.add(f"{home} or Draw")
        elif normalized in {"draw or away", "empate fora", "x2"}:
            aliases.update({"Draw or Away", "X2"})
            if away:
                aliases.add(f"Draw or {away}")
        elif normalized in {"home or away", "casa fora", "12"}:
            aliases.update({"Home or Away", "12"})
            if home and away:
                aliases.add(f"{home} or {away}")
    return [alias for alias in aliases if alias]


def _runner_for_catalog_market(row: dict[str, str], market: dict[str, Any]) -> dict[str, Any] | None:
    runners = _catalog_runners(market)
    if not runners:
        return None
    desired = {normalize_text(alias) for alias in _selection_aliases_for_runner(row, market)}
    for runner in runners:
        runner_name = _runner_name(runner)
        if normalize_text(runner_name) in desired:
            return runner
    for runner in runners:
        runner_name_norm = normalize_text(_runner_name(runner))
        if any(alias and (alias in runner_name_norm or runner_name_norm in alias) for alias in desired):
            return runner
    return None


def _selection_for_catalog_market(row: dict[str, str], market: dict[str, Any]) -> str:
    runner = _runner_for_catalog_market(row, market)
    if runner:
        return _runner_name(runner) or str(row.get("SelectionName", ""))
    family = row_market_family(market)
    selection = row.get("SelectionName", "")
    if family == "match_odds":
        return map_selection_to_event(selection, str(market.get("event_name") or ""))
    if family == "btts":
        original = selection.lower()
        return "Não" if "nao" in original or "não" in original or original == "no" else "Sim"
    if family in {"goals", "first_half_goals", "corners"}:
        line = (market_line(str(market.get("market_name") or market.get("MarketName") or "")) or market_line(str(market.get("market_type") or market.get("MarketType") or "")) or _num(row.get("__line") or row.get("line") or row.get("Line")))
        line_text = _bfbm_line_text(line) if line is not None else ""
        original = selection.lower()
        side = "Menos" if "menos" in original or "under" in original else "Mais"
        unit = "escanteios" if family == "corners" else "gols"
        return f"{side} de {line_text} {unit}".strip()
    return selection


def enrich_row_from_bfbm_catalog(row: dict[str, str], catalog_rows: list[dict[str, Any]]) -> dict[str, str] | None:
    if not catalog_rows:
        return row
    family = row_market_family(row)
    desired_line = market_line(row.get("MarketName", "")) or market_line(row.get("MarketType", ""))
    match = find_bfbm_market(catalog_rows, row.get("EventName", ""), family, desired_line)
    if not match:
        return None
    enriched = row.copy()
    enriched["EventName"] = str(match.get("event_name") or row.get("EventName", ""))
    enriched["MarketName"] = str(match.get("market_name") or row.get("MarketName", ""))
    enriched["EventId"] = str(match.get("event_id") or row.get("EventId", "0") or "0")
    enriched["MarketId"] = str(match.get("market_id") or row.get("MarketId", "0") or "0")
    enriched["MarketType"] = str(match.get("market_type") or row.get("MarketType", ""))
    enriched["SelectionName"] = _selection_for_catalog_market(enriched, match)
    runner = _runner_for_catalog_market(enriched, match)
    if runner:
        enriched["SelectionId"] = _runner_selection_id(runner) or str(enriched.get("SelectionId", "0") or "0")
    start_time = str(match.get("start_time") or "")
    if len(start_time) >= 10 and start_time[:4].isdigit():
        enriched["StartTime"] = str(match.get("start_time"))
    return enriched


def _has_valid_export_ids(row: dict[str, str]) -> bool:
    event_id = str(row.get("EventId") or "").strip()
    market_id = str(row.get("MarketId") or "").strip()
    selection_id = str(row.get("SelectionId") or "").strip()
    return (
        event_id.isdigit()
        and event_id != "0"
        and market_id not in {"", "0"}
        and selection_id.isdigit()
        and selection_id != "0"
    )


def _has_matchable_names(row: dict[str, str]) -> bool:
    return all(str(row.get(field) or "").strip() for field in ("EventName", "MarketName", "SelectionName"))


def _audit_row(
    alert: dict[str, Any],
    endpoint: str,
    status: str,
    reason: str,
    row: dict[str, str] | None = None,
) -> dict[str, Any]:
    row = row or {}
    return {
        "alert_id": alert.get("id"),
        "alert_key": alert.get("alert_key", ""),
        "endpoint": endpoint,
        "status": status,
        "reason": reason,
        "home": alert.get("home", ""),
        "away": alert.get("away", ""),
        "event_name": _event_name(alert),
        "market": alert.get("market", ""),
        "selection": alert.get("selection", ""),
        "line": alert.get("line"),
        "bfbm_event_name": row.get("EventName", ""),
        "bfbm_market_name": row.get("MarketName", ""),
        "bfbm_selection_name": row.get("SelectionName", ""),
        "bfbm_event_id": row.get("EventId", ""),
        "bfbm_market_id": row.get("MarketId", ""),
        "bfbm_selection_id": row.get("SelectionId", ""),
        "bfbm_start_time": row.get("StartTime", ""),
        "raw": {"alert": alert, "row": row},
    }


def full_rows_with_audit(
    alerts: list[dict[str, Any]],
    config: BfbmConfig,
    catalog_rows: list[dict[str, Any]] | None = None,
    endpoint: str = "/bfbm/live-full.csv",
    allow_name_fallback: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    rows: list[dict[str, str]] = []
    audits: list[dict[str, Any]] = []
    catalog_rows = catalog_rows or []
    for alert in alerts:
        row = alert_to_bfbm_row(alert, config)
        if not row:
            audits.append(_audit_row(alert, endpoint, "SKIPPED", "unsupported_market_or_selection"))
            continue
        if catalog_rows:
            matched = enrich_row_from_bfbm_catalog(row, catalog_rows)
            if not matched:
                if allow_name_fallback and _has_matchable_names(row):
                    rows.append(row)
                    audits.append(_audit_row(alert, endpoint, "EXPORTED", "exported_without_catalog_match_bfbm_may_fill", row))
                    continue
                audits.append(_audit_row(alert, endpoint, "SKIPPED", "no_bfbm_market_match", row))
                continue
            row = matched
        if not _has_valid_export_ids(row):
            if allow_name_fallback and _has_matchable_names(row):
                rows.append(row)
                audits.append(_audit_row(alert, endpoint, "EXPORTED", "exported_without_ids_bfbm_may_fill", row))
                continue
            audits.append(_audit_row(alert, endpoint, "SKIPPED", "missing_required_bfbm_ids", row))
            continue
        rows.append(row)
        selection_id = str(row.get("SelectionId") or "").strip()
        reason = "exported_with_ids"
        if selection_id in {"", "0"}:
            reason = "exported_without_selection_id_bfbm_may_fill"
        audits.append(_audit_row(alert, endpoint, "EXPORTED", reason, row))
    return rows, audits


def rows_to_full_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=BFBM_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def tips_csv(alerts: list[dict[str, Any]], config: BfbmConfig) -> str:
    buffer = io.StringIO(newline="")
    rows = [row for alert in alerts if (row := alert_to_bfbm_row(alert, config))]
    writer = csv.DictWriter(buffer, fieldnames=BFBM_ACCEPTED_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def tips_full_csv(alerts: list[dict[str, Any]], config: BfbmConfig, catalog_rows: list[dict[str, Any]] | None = None) -> str:
    rows, _audits = full_rows_with_audit(alerts, config, catalog_rows)
    return rows_to_full_csv(rows)


def tips_rich_csv(alerts: list[dict[str, Any]], config: BfbmConfig, catalog_rows: list[dict[str, Any]] | None = None) -> str:
    buffer = io.StringIO(newline="")
    rows = [row for alert in alerts if (row := alert_to_bfbm_row(alert, config))]
    if catalog_rows:
        rows = [matched for row in rows if (matched := enrich_row_from_bfbm_catalog(row, catalog_rows))]
    if any(row.get("MarketType") == "MATCH_ODDS" for row in rows):
        rows = [row for row in rows if row.get("MarketType") == "MATCH_ODDS"]
    for row in list(rows):
        for alias in _bfbm_event_aliases(row.get("EventName", "")):
            alias_row = row.copy()
            alias_row["EventName"] = alias
            alias_row["SelectionName"] = _bfbm_name_alias(alias_row.get("SelectionName", ""))
            rows.append(alias_row)
    writer = csv.DictWriter(buffer, fieldnames=BFBM_RICH_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in rows:
        if not row.get("Price"):
            row["Price"] = "0"
        writer.writerow(row)
    return buffer.getvalue()


def tips_clean_match_odds_csv(alerts: list[dict[str, Any]], config: BfbmConfig, limit: int = 4) -> str:
    buffer = io.StringIO(newline="")
    rows: list[dict[str, str]] = []
    seen_events: set[str] = set()
    for alert in alerts:
        row = alert_to_bfbm_row(alert, config)
        if not row or row.get("MarketType") != "MATCH_ODDS":
            continue
        row["EventName"] = _bfbm_clean_event_name(row.get("EventName", ""))
        row["SelectionName"] = _bfbm_clean_name(row.get("SelectionName", ""))
        event_key = row["EventName"].casefold()
        if not row["EventName"] or event_key in seen_events:
            continue
        seen_events.add(event_key)
        if not row.get("Price"):
            row["Price"] = "0"
        rows.append(row)
        if len(rows) >= limit:
            break
    writer = csv.DictWriter(buffer, fieldnames=BFBM_RICH_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def debug_minimal_csv(config: BfbmConfig) -> str:
    buffer = io.StringIO(newline="")
    columns = ["Provider", "SelectionName", "MarketType", "BetType", "Size", "MinPrice", "MaxPrice"]
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerow(
        {
            "Provider": config.provider,
            "SelectionName": "Over 2.5 Goals",
            "MarketType": "OVER_UNDER_25",
            "BetType": "BACK",
            "Size": f"{config.stake:.2f}",
            "MinPrice": f"{config.min_price:.2f}",
            "MaxPrice": f"{config.max_price:.2f}",
        }
    )
    return buffer.getvalue()


def debug_event_csv(config: BfbmConfig, event_name: str) -> str:
    alert = {
        "id": "debug-event",
        "event_id": "debug-event",
        "home": event_name,
        "away": "",
        "market": "Mais gols",
        "selection": "over",
        "line": 2.5,
        "odd": 0,
    }
    row = alert_to_bfbm_row(alert, config)
    if row:
        row["EventName"] = event_name
    return tips_csv([alert], config) if not row else _single_row_csv(row)


def debug_lab_csv(config: BfbmConfig, event_name: str, mode: str) -> str:
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": "0",
        "MarketId": "0",
        "EventId": "0",
        "SelectionName": "Over 2.5 Goals",
        "MarketName": "Over/Under 2.5 Goals",
        "EventName": event_name,
        "MarketType": "OVER_UNDER_25",
        "StartTime": "",
        "BetType": "BACK",
        "Price": "0",
        "Size": f"{config.stake:.2f}",
        "Points": "1",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": f"{config.max_price:.2f}",
        "BSP": "False",
    }
    mode_columns = {
        "1": ["Provider", "SelectionName"],
        "2": ["Provider", "SelectionName", "MarketType"],
        "3": ["Provider", "SelectionName", "MarketType", "EventName"],
        "4": ["Provider", "SelectionName", "MarketType", "EventName", "BetType"],
        "5": ["Provider", "SelectionName", "MarketName", "MarketType", "EventName", "BetType", "Size"],
        "6": ["Provider", "SelectionName", "MarketName", "MarketType", "EventName", "BetType", "Size", "MinPrice", "MaxPrice"],
        "7": BFBM_COLUMNS,
        "8": ["Provider", "SelectionName", "MarketType", "EventName", "Size"],
    }
    columns = mode_columns.get(str(mode).strip().lower(), mode_columns["3"])
    return _custom_row_csv(row, columns)


def fresh_test_csv(config: BfbmConfig, suffix: str) -> str:
    row = {
        "Provider": config.provider,
        "SelectionName": "Over 2.5 Goals",
        "MarketType": "OVER_UNDER_25",
        "EventName": f"TesteGPT v {suffix}",
        "BetType": "BACK",
        "Size": f"{config.stake:.2f}",
    }
    return _custom_row_csv(row, BFBM_ACCEPTED_COLUMNS)


def fresh_event_csv(config: BfbmConfig, event_name: str) -> str:
    row = {
        "Provider": config.provider,
        "SelectionName": "Over 2.5 Goals",
        "MarketType": "OVER_UNDER_25",
        "EventName": event_name,
        "BetType": "BACK",
        "Size": f"{config.stake:.2f}",
    }
    return _custom_row_csv(row, BFBM_ACCEPTED_COLUMNS)


def fresh_match_odds_csv(config: BfbmConfig, event_name: str, selection_name: str) -> str:
    row = {
        "Provider": config.provider,
        "SelectionName": selection_name,
        "MarketType": "MATCH_ODDS",
        "EventName": event_name,
        "BetType": "BACK",
        "Size": f"{config.stake:.2f}",
    }
    return _custom_row_csv(row, BFBM_ACCEPTED_COLUMNS)


def fresh_match_odds_full_csv(config: BfbmConfig, event_name: str, selection_name: str) -> str:
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": "0",
        "MarketId": "0",
        "EventId": "0",
        "SelectionName": selection_name,
        "MarketName": "Match Odds",
        "EventName": event_name,
        "MarketType": "MATCH_ODDS",
        "StartTime": _default_start_time(),
        "BetType": "BACK",
        "Price": "0",
        "Size": f"{config.stake:.2f}",
        "Points": "1",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": f"{config.max_price:.2f}",
        "BSP": "False",
    }
    return _custom_row_csv(row, BFBM_COLUMNS)


def fresh_match_odds_ids_csv(
    config: BfbmConfig,
    event_name: str,
    selection_name: str,
    event_id: str,
    market_id: str,
    selection_id: str,
    start_time: str = "",
    price: str = "",
    selection_alias: str = "",
    market_name: str = "",
) -> str:
    csv_selection_name = selection_alias or selection_name
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionId": selection_id or "0",
        "MarketId": market_id or "0",
        "EventId": event_id or "0",
        "SelectionName": csv_selection_name,
        "MarketName": market_name or "Match Odds",
        "EventName": event_name,
        "MarketType": "MATCH_ODDS",
        "StartTime": start_time or _default_start_time(),
        "BetType": "BACK",
        "Price": price or f"{config.min_price:.2f}",
        "Size": f"{config.stake:.2f}",
        "Points": "1",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": f"{config.max_price:.2f}",
        "BSP": "False",
    }
    return _custom_row_csv(row, BFBM_COLUMNS)


def fresh_match_odds_rich_csv(config: BfbmConfig, event_name: str, selection_name: str) -> str:
    row = {
        "Provider": config.provider,
        "Handicap": "0",
        "SelectionName": _bfbm_clean_name(selection_name),
        "MarketName": "Resultado da partida",
        "EventName": _bfbm_clean_event_name(event_name),
        "MarketType": "MATCH_ODDS",
        "BetType": "BACK",
        "Size": "1.00",
        "Points": "1",
        "Price": "0",
        "MinPrice": f"{config.min_price:.2f}",
        "MaxPrice": "100.00",
        "BSP": "False",
    }
    return _custom_row_csv(row, BFBM_RICH_COLUMNS)


def _single_row_csv(row: dict[str, str]) -> str:
    return _custom_row_csv(row, BFBM_COLUMNS)


def _custom_row_csv(row: dict[str, str], columns: list[str]) -> str:
    buffer = io.StringIO(newline="")
    quoting = csv.QUOTE_ALL if columns in (BFBM_COLUMNS, BFBM_RICH_COLUMNS) else csv.QUOTE_MINIMAL
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", quoting=quoting)
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue()
