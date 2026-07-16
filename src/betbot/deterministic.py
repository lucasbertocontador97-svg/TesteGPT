from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeterministicSignal:
    approved: bool
    market_family: str
    selection: str
    line: float | None
    probability: float
    confidence: int
    score: int
    reason: str
    strategy: str


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _sum_stat(stats: dict[str, Any], names: tuple[str, ...]) -> float:
    wanted = {name.lower() for name in names}
    total = 0.0
    for team_stats in stats.values():
        for key, value in team_stats.items():
            if str(key).lower() in wanted:
                total += _num(value) or 0.0
    return total


def _poisson_at_least(mean: float, needed: int) -> float:
    if needed <= 0:
        return 1.0
    cumulative = 0.0
    for k in range(needed):
        cumulative += math.exp(-mean) * (mean**k) / math.factorial(k)
    return max(0.0, min(1.0, 1.0 - cumulative))


def _poisson_at_most(mean: float, maximum: int) -> float:
    if maximum < 0:
        return 0.0
    cumulative = 0.0
    for k in range(maximum + 1):
        cumulative += math.exp(-mean) * (mean**k) / math.factorial(k)
    return max(0.0, min(1.0, cumulative))


def _needed_over(current_total: int, line: float) -> int:
    return max(0, math.floor(line - current_total) + 1)


def _available_lines(
    available_markets: list[tuple[str, float | None]] | None,
    family: str,
    fallback: tuple[float, ...],
) -> list[float]:
    if available_markets is None:
        return list(fallback)
    lines = sorted(
        {
            float(line)
            for market_family, line in available_markets
            if market_family == family and line is not None
        }
    )
    generic_line_market = any(market_family == family and line is None for market_family, line in available_markets)
    if generic_line_market and family in {"goals", "corners"}:
        return sorted({*lines, *fallback})
    return lines


def _goal_over_threshold(minute: int, needed_goals: int) -> float:
    if needed_goals <= 1:
        if minute < 60:
            return 0.70
        return 0.62 if minute >= 70 else 0.66
    if needed_goals == 2:
        return 0.56 if minute >= 62 else 0.60
    return 0.50


def _goal_pressure_ok(needed_goals: int, total_shots: float, shots_on: float, pressure: float) -> bool:
    if needed_goals <= 1:
        return (total_shots >= 10 and shots_on >= 3 and pressure >= 50) or (
            total_shots >= 14 and shots_on >= 2 and pressure >= 60
        )
    if needed_goals == 2:
        return total_shots >= 14 and shots_on >= 5 and pressure >= 62
    return total_shots >= 18 and shots_on >= 7 and pressure >= 68


def _goal_over_strategy(line: float) -> str:
    return f"GOAL_OVER_{int(round(line * 10)):02d}_FT"


def _goal_under_strategy(line: float) -> str:
    return f"GOAL_UNDER_{int(round(line * 10)):02d}_FT"


def _dead_game(minute: int, total_shots: float, shots_on: float, dangerous: float) -> bool:
    if minute < 30:
        return False
    return total_shots <= 5 and shots_on <= 1 and dangerous <= 20


def _under_goal_conviction(probability_score: int, minute: int, total_shots: float, shots_on: float, pressure: float) -> int:
    bonus = 0
    if minute >= 75:
        bonus += 8
    elif minute >= 65:
        bonus += 4
    if total_shots <= 7:
        bonus += 9
    elif total_shots <= 10:
        bonus += 5
    if shots_on <= 2:
        bonus += 8
    elif shots_on <= 3:
        bonus += 4
    if pressure <= 30:
        bonus += 8
    elif pressure <= 45:
        bonus += 4
    return min(95, probability_score + bonus)


def _has_market_family(available_markets: list[tuple[str, float | None]] | None, family: str) -> bool:
    if available_markets is None:
        return True
    return any(market_family == family for market_family, _line in available_markets)


def _has_market_line(available_markets: list[tuple[str, float | None]] | None, family: str, line: float) -> bool:
    if available_markets is None:
        return True
    if any(market_family == family and market_line is None for market_family, market_line in available_markets):
        return family in {"goals", "corners"}
    return any(
        market_family == family and market_line is not None and abs(float(market_line) - line) <= 0.01
        for market_family, market_line in available_markets
    )


def _effective_pressure(dangerous: float, attacks: float, total_shots: float, shots_on: float) -> tuple[float, str]:
    if dangerous > 0:
        return dangerous, f"ataques perigosos {dangerous:g}"
    estimated = min(80.0, attacks * 0.35 + total_shots * 1.8 + shots_on * 3.0)
    return estimated, f"pressao estimada {estimated:g} por ataques {attacks:g}, chutes {total_shots:g}, no gol {shots_on:g}"


def _goal_lambda_to(minute: int, current_goals: int, total_shots: float, shots_on: float, dangerous: float, end_minute: int) -> float:
    remaining = max(0, end_minute - minute)
    base = 2.55 / 90 * remaining
    pace = 1.0
    if shots_on >= 4:
        pace += 0.22
    if total_shots >= 12:
        pace += 0.16
    if dangerous >= 45:
        pace += 0.18
    if current_goals >= 1 and minute <= 65:
        pace += 0.08
    if minute >= 70 and current_goals <= 2:
        pace += 0.05
    return max(0.05, base * pace)


def _goal_lambda(minute: int, current_goals: int, total_shots: float, shots_on: float, dangerous: float) -> float:
    return _goal_lambda_to(minute, current_goals, total_shots, shots_on, dangerous, 90)


def _corner_lambda(minute: int, corners: float, total_shots: float, dangerous: float) -> float:
    remaining = max(0, 90 - minute)
    base = 9.5 / 90 * remaining
    pace = 1.0
    expected_so_far = max(1.0, 9.5 * minute / 90)
    if corners > expected_so_far:
        pace += min(0.35, (corners - expected_so_far) / 10)
    if dangerous >= 45:
        pace += 0.2
    if total_shots >= 12:
        pace += 0.1
    return max(0.05, base * pace)


def _next_goal_conviction(probability_score: int, total_shots: float, shots_on: float, corners: float, pressure: float) -> int:
    bonus = 0
    if shots_on >= 6:
        bonus += 10
    elif shots_on >= 4:
        bonus += 6
    if total_shots >= 14:
        bonus += 8
    elif total_shots >= 10:
        bonus += 4
    if corners >= 5:
        bonus += 4
    if pressure >= 65:
        bonus += 8
    elif pressure >= 45:
        bonus += 5
    return min(95, probability_score + bonus)


def _nil_nil_goal_conviction(
    *,
    probability_score: int,
    minute: int,
    total_shots: float,
    shots_on: float,
    corners: float,
    pressure: float,
) -> int:
    bonus = 0
    if shots_on >= 5:
        bonus += 16
    elif shots_on >= 3:
        bonus += 10
    elif shots_on >= 2:
        bonus += 6

    if total_shots >= 12:
        bonus += 12
    elif total_shots >= 8:
        bonus += 7

    if pressure >= 65:
        bonus += 14
    elif pressure >= 50:
        bonus += 9
    elif pressure >= 40:
        bonus += 5

    if corners >= 5:
        bonus += 6
    elif corners >= 3:
        bonus += 3

    if 18 <= minute <= 25:
        bonus += 5
    elif 62 <= minute <= 80:
        bonus += 4

    return min(95, probability_score + bonus)


def _nil_nil_goal_window(minute: int, current_goals: int) -> tuple[str, int, float] | None:
    if current_goals != 0:
        return None
    if 18 <= minute <= 25:
        return "GOAL_OVER_05_HT", 45, 0.48
    if 58 <= minute <= 82:
        return "GOAL_OVER_05_FT", 90, 0.56
    return None


def _bfbm_sparse_nil_nil_fallback(
    *,
    strategy_name: str,
    available_markets: list[tuple[str, float | None]] | None,
    attacks: float,
    total_shots: float,
    corners: float,
    pressure: float,
) -> bool:
    if available_markets is None:
        return False
    if strategy_name == "GOAL_OVER_05_HT":
        if not _has_market_line(available_markets, "first_half_goals", 0.5):
            return False
        return corners >= 1 or total_shots >= 2 or attacks >= 20 or pressure >= 10
    if strategy_name == "GOAL_OVER_05_FT":
        if not _has_market_line(available_markets, "goals", 0.5):
            return False
        return corners >= 2 or total_shots >= 4 or attacks >= 40 or pressure >= 20
    return False


def _next_corner_conviction(
    *,
    probability_score: int,
    minute: int,
    corners: float,
    total_shots: float,
    shots_on: float,
    pressure: float,
) -> int:
    bonus = 0
    expected_so_far = max(1.0, 9.5 * minute / 90)
    corner_pace = corners / expected_so_far

    if corner_pace >= 1.25:
        bonus += 10
    elif corner_pace >= 1.0:
        bonus += 6

    if pressure >= 70:
        bonus += 10
    elif pressure >= 55:
        bonus += 7
    elif pressure >= 40:
        bonus += 4

    if total_shots >= 14:
        bonus += 7
    elif total_shots >= 10:
        bonus += 4

    if shots_on >= 5:
        bonus += 4

    if 37 <= minute <= 39:
        bonus += 3
    elif 78 <= minute <= 85:
        bonus += 5

    return min(95, probability_score + bonus)


def _next_corner_window(minute: int) -> tuple[str, float] | None:
    if 37 <= minute <= 39:
        return "CORNER_PLUS_05_HT", 0.55
    if 55 <= minute <= 70:
        return "CORNER_PRESSURE_2H", 0.62
    if 78 <= minute <= 85:
        return "CORNER_PLUS_05_FT", 0.55
    return None


def _btts_yes_conviction(
    probability_score: int,
    minute: int,
    current_goals: int,
    total_shots: float,
    shots_on: float,
    pressure: float,
) -> int:
    bonus = 0
    if 45 <= minute <= 78:
        bonus += 5
    if current_goals >= 1:
        bonus += 6
    if shots_on >= 6:
        bonus += 12
    elif shots_on >= 4:
        bonus += 8
    if total_shots >= 16:
        bonus += 10
    elif total_shots >= 12:
        bonus += 6
    if pressure >= 70:
        bonus += 10
    elif pressure >= 55:
        bonus += 6
    return min(95, probability_score + bonus)


def _btts_no_conviction(
    probability_score: int,
    minute: int,
    total_shots: float,
    shots_on: float,
    pressure: float,
) -> int:
    bonus = 0
    if minute >= 72:
        bonus += 10
    elif minute >= 65:
        bonus += 6
    if total_shots <= 9:
        bonus += 8
    if shots_on <= 3:
        bonus += 8
    if pressure <= 45:
        bonus += 6
    return min(95, probability_score + bonus)


def _candidate_priority(signal: DeterministicSignal) -> int:
    if signal.strategy in {"GOAL_OVER_05_HT", "GOAL_OVER_05_FT"}:
        return 5
    if signal.strategy == "GOAL_NEXT_LINE_FT":
        return 4
    if signal.strategy.startswith("BTTS_"):
        return 4
    if signal.strategy.startswith("CORNER_"):
        return 3
    if signal.strategy.startswith("GOAL_OVER_") or signal.strategy.startswith("GOAL_UNDER_"):
        return 2
    return 1


def _candidate_is_available(signal: DeterministicSignal, available_markets: list[tuple[str, float | None]] | None) -> bool:
    if available_markets is None:
        return True
    for family, line in available_markets:
        if family != signal.market_family:
            continue
        if line is None:
            return signal.line is None or family in {"goals", "corners"}
        if signal.line is None:
            return False
        if abs(line - signal.line) <= 0.01:
            return True
    return False


def evaluate_game(
    *,
    minute: int | None,
    score_home: int | None,
    score_away: int | None,
    stats: dict[str, Any],
    min_confidence: int,
    available_markets: list[tuple[str, float | None]] | None = None,
) -> DeterministicSignal:
    if minute is None or score_home is None or score_away is None:
        return DeterministicSignal(False, "none", "none", None, 0, 0, 0, "Minuto ou placar indisponivel.", "blocked")
    if minute < 15 or minute >= 88:
        return DeterministicSignal(False, "none", "none", None, 0, 0, 0, "Fora da janela operacional.", "time_filter")

    current_goals = int(score_home) + int(score_away)
    shots_on = _sum_stat(stats, ("Shots on Goal", "Shots on target"))
    total_shots = _sum_stat(stats, ("Total Shots",))
    dangerous = _sum_stat(stats, ("Dangerous Attacks",))
    attacks = _sum_stat(stats, ("Attacks",))
    corners = _sum_stat(stats, ("Corner Kicks", "Corners"))
    pressure, pressure_label = _effective_pressure(dangerous, attacks, total_shots, shots_on)

    dead_game = _dead_game(minute, total_shots, shots_on, pressure)

    candidates: list[DeterministicSignal] = []

    goal_mean = _goal_lambda(minute, current_goals, total_shots, shots_on, pressure)
    nil_nil_goal_window = _nil_nil_goal_window(minute, current_goals)
    if nil_nil_goal_window:
        strategy_name, end_minute, threshold = nil_nil_goal_window
        target_goal_mean = _goal_lambda_to(minute, current_goals, total_shots, shots_on, pressure, end_minute)
        prob = _poisson_at_least(target_goal_mean, 1)
        score = round(prob * 100)
        conviction = _nil_nil_goal_conviction(
            probability_score=score,
            minute=minute,
            total_shots=total_shots,
            shots_on=shots_on,
            corners=corners,
            pressure=pressure,
        )
        nil_nil_confidence = max(min_confidence, 84)
        strong_live_flow = total_shots >= 9 and shots_on >= 3 and pressure >= 50
        bfbm_sparse_fallback = _bfbm_sparse_nil_nil_fallback(
            strategy_name=strategy_name,
            available_markets=available_markets,
            attacks=attacks,
            total_shots=total_shots,
            corners=corners,
            pressure=pressure,
        )
        if not strong_live_flow and not bfbm_sparse_fallback:
            conviction = 0
        elif bfbm_sparse_fallback and conviction < nil_nil_confidence:
            conviction = nil_nil_confidence
        if prob >= threshold and conviction >= nil_nil_confidence:
            data_label = "estatisticas fortes" if strong_live_flow else "mercado BFBM confirmado com leitura parcial"
            candidates.append(
                DeterministicSignal(
                    True,
                    "first_half_goals" if strategy_name == "GOAL_OVER_05_HT" else "goals",
                    "over",
                    0.5,
                    prob,
                    conviction,
                    conviction,
                    f"Jogo 0x0 com probabilidade {prob:.0%} de sair o primeiro gol ate {'o intervalo' if end_minute == 45 else 'o fim'}; conviccao {conviction} por {data_label}, chutes {total_shots:g}, no gol {shots_on:g}, escanteios {corners:g} e {pressure_label}.",
                    strategy_name,
                )
            )

    goal_lines = _available_lines(available_markets, "goals", (0.5, 1.5, 2.5, 3.5, 4.5, 5.5))
    for line in goal_lines:
        if line < 0.5 or line > 8.5:
            continue
        needed_goals = _needed_over(current_goals, line)
        if needed_goals <= 0:
            continue
        if line == 0.5 and current_goals == 0 and not nil_nil_goal_window:
            continue
        if line == 1.5 and minute < 48:
            continue
        if line == 1.5 and current_goals >= 1 and minute < 50:
            continue
        if line >= 2.5 and minute < (48 if current_goals >= 1 else 55):
            continue
        if line >= 3.5 and minute < (58 if current_goals >= 2 else 62):
            continue
        if line >= 3.5 and current_goals < line - 2.0:
            continue
        if needed_goals >= 3 and not (total_shots >= 14 and shots_on >= 5 and pressure >= 60):
            continue
        prob = _poisson_at_least(goal_mean, needed_goals)
        score = round(prob * 100)
        threshold = _goal_over_threshold(minute, needed_goals)
        goal_confidence = max(min_confidence, 82 if needed_goals <= 1 else 86)
        if not _goal_pressure_ok(needed_goals, total_shots, shots_on, pressure):
            continue
        if prob >= threshold and score >= goal_confidence:
            candidates.append(
                DeterministicSignal(
                    True,
                    "goals",
                    "over",
                    line,
                    prob,
                    score,
                    score,
                    f"Poisson {prob:.0%} para over {line:g}; chutes {total_shots:g}, no gol {shots_on:g}, {pressure_label}.",
                    _goal_over_strategy(line),
                )
            )

    if minute >= 60 and (dead_game or (total_shots <= 10 and shots_on <= 3 and pressure <= 45)):
        for line in goal_lines:
            if line <= current_goals or line > current_goals + 3.5:
                continue
            additional_allowed = max(-1, math.ceil(line - current_goals) - 1)
            prob = _poisson_at_most(goal_mean, additional_allowed)
            score = round(prob * 100)
            conviction = _under_goal_conviction(score, minute, total_shots, shots_on, pressure)
            if prob >= 0.70 and conviction >= max(min_confidence, 76):
                candidates.append(
                    DeterministicSignal(
                        True,
                        "goals",
                        "under",
                        line,
                        prob,
                        conviction,
                        conviction,
                        f"Under {line:g} com probabilidade {prob:.0%}; jogo frio com chutes {total_shots:g}, no gol {shots_on:g} e {pressure_label}.",
                        _goal_under_strategy(line),
                    )
                )
                break

    if _has_market_family(available_markets, "btts"):
        both_scored = score_home > 0 and score_away > 0
        one_side_blank = (score_home == 0) != (score_away == 0)
        if one_side_blank and 45 <= minute <= 78:
            prob_other_scores = _poisson_at_least(goal_mean, 1)
            score = round(prob_other_scores * 100)
            conviction = _btts_yes_conviction(score, minute, current_goals, total_shots, shots_on, pressure)
            if total_shots < 14 or shots_on < 5 or pressure < 58:
                conviction = 0
            if prob_other_scores >= 0.58 and conviction >= max(min_confidence, 84):
                candidates.append(
                    DeterministicSignal(
                        True,
                        "btts",
                        "yes",
                        None,
                        prob_other_scores,
                        conviction,
                        conviction,
                        f"BTTS sim: falta um time marcar e ha probabilidade {prob_other_scores:.0%} de mais gol; chutes {total_shots:g}, no gol {shots_on:g} e {pressure_label}.",
                        "BTTS_YES_LIVE",
                    )
                )
        if not both_scored and minute >= 65 and (dead_game or (total_shots <= 10 and shots_on <= 3 and pressure <= 45)):
            prob_no_more_goal = _poisson_at_most(goal_mean, 0)
            score = round(prob_no_more_goal * 100)
            conviction = _btts_no_conviction(score, minute, total_shots, shots_on, pressure)
            if prob_no_more_goal >= 0.70 and conviction >= max(min_confidence, 82):
                candidates.append(
                    DeterministicSignal(
                        True,
                        "btts",
                        "no",
                        None,
                        prob_no_more_goal,
                        conviction,
                        conviction,
                        f"BTTS nao: jogo frio e probabilidade {prob_no_more_goal:.0%} de nao sair novo gol; chutes {total_shots:g}, no gol {shots_on:g} e {pressure_label}.",
                        "BTTS_NO_LIVE",
                    )
                )

    if 50 <= minute <= 86 and current_goals > 0:
        next_goal_line = current_goals + 0.5
        prob = _poisson_at_least(goal_mean, 1)
        score = round(prob * 100)
        conviction = _next_goal_conviction(score, total_shots, shots_on, corners, pressure)
        threshold = 0.68 if minute < 60 else 0.66 if minute < 70 else 0.62
        if not _goal_pressure_ok(1, total_shots, shots_on, pressure):
            conviction = 0
        if prob >= threshold and conviction >= max(min_confidence, 84):
            candidates.append(
                DeterministicSignal(
                    True,
                    "goals",
                    "over",
                    next_goal_line,
                    prob,
                    conviction,
                    conviction,
                    f"Probabilidade {prob:.0%} de pelo menos mais um gol; conviccao {conviction} por chutes {total_shots:g}, no gol {shots_on:g}, escanteios {corners:g} e {pressure_label}.",
                    "GOAL_NEXT_LINE_FT",
                )
            )

    corner_window = _next_corner_window(minute)
    if corner_window:
        strategy_name, threshold = corner_window
        corner_lines = _available_lines(available_markets, "corners", (corners + 0.5,))
        viable_corner_lines = [line for line in corner_lines if line > corners and line <= corners + 2.5]
        target_corner_line = min(viable_corner_lines) if viable_corner_lines else corners + 0.5
        needed_corners = _needed_over(int(corners), target_corner_line)
        corner_mean = _corner_lambda(minute, corners, total_shots, pressure)
        prob = _poisson_at_least(corner_mean, needed_corners)
        score = round(prob * 100)
        conviction = _next_corner_conviction(
            probability_score=score,
            minute=minute,
            corners=corners,
            total_shots=total_shots,
            shots_on=shots_on,
            pressure=pressure,
        )
        if strategy_name == "CORNER_PRESSURE_2H" and not (pressure >= 60 and total_shots >= 10 and corners >= 3):
            conviction = 0
        if prob >= threshold and conviction >= min_confidence:
            candidates.append(
                DeterministicSignal(
                    True,
                    "corners",
                    "over",
                    target_corner_line,
                    prob,
                    conviction,
                    conviction,
                    f"Probabilidade {prob:.0%} para over {target_corner_line:g} escanteios; conviccao {conviction} por escanteios {corners:g}, chutes {total_shots:g}, no gol {shots_on:g} e {pressure_label}.",
                    strategy_name,
                )
            )

    if not candidates:
        return DeterministicSignal(
            False,
            "none",
            "none",
            None,
            0,
            0,
            0,
            f"Nenhum mercado passou nos thresholds matematicos. Chutes {total_shots:g}, no gol {shots_on:g}, escanteios {corners:g}, {pressure_label}.",
            "no_signal",
        )

    candidates = [
        candidate
        for candidate in candidates
        if _candidate_is_available(candidate, available_markets)
    ]
    if not candidates:
        return DeterministicSignal(
            False,
            "none",
            "none",
            None,
            0,
            0,
            0,
            "Sinais matematicos existem, mas nenhum bate com mercado/linha disponivel no BFBM.",
            "no_bfbm_market",
        )

    return sorted(candidates, key=lambda item: (_candidate_priority(item), item.score, item.probability), reverse=True)[0]
