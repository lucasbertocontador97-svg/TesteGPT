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


def _needed_over(current_total: int, line: float) -> int:
    return max(0, math.floor(line - current_total) + 1)


def _dead_game(minute: int, total_shots: float, shots_on: float, dangerous: float) -> bool:
    if minute < 30:
        return False
    return total_shots <= 5 and shots_on <= 1 and dangerous <= 20


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

    if 32 <= minute <= 39:
        bonus += 5
    elif 62 <= minute <= 80:
        bonus += 4

    return min(95, probability_score + bonus)


def _nil_nil_goal_window(minute: int, current_goals: int) -> tuple[str, int, float] | None:
    if current_goals != 0:
        return None
    if 32 <= minute <= 39:
        return "Over 0.5 HT", 45, 0.30
    if 62 <= minute <= 80:
        return "Over 0.5 FT", 90, 0.45
    return None


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

    if 32 <= minute <= 39:
        bonus += 3
    elif 78 <= minute <= 85:
        bonus += 5

    return min(95, probability_score + bonus)


def _next_corner_window(minute: int) -> tuple[str, float] | None:
    if 32 <= minute <= 39:
        return "Asian Corner +0.5 HT", 0.55
    if 78 <= minute <= 85:
        return "Asian Corner +0.5 FT", 0.55
    return None


def _candidate_priority(signal: DeterministicSignal) -> int:
    if signal.strategy in {"Over 0.5 HT", "Over 0.5 FT"}:
        return 4
    if signal.strategy.startswith("Asian Corner"):
        return 3
    if signal.strategy.startswith("Asian Goal"):
        return 2
    return 1


def evaluate_game(
    *,
    minute: int | None,
    score_home: int | None,
    score_away: int | None,
    stats: dict[str, Any],
    min_confidence: int,
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

    if _dead_game(minute, total_shots, shots_on, pressure):
        return DeterministicSignal(False, "none", "none", None, 0, 0, 0, "Jogo morto: baixo volume ofensivo e pouca pressao.", "dead_game")

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
        if prob >= threshold and conviction >= min_confidence:
            candidates.append(
                DeterministicSignal(
                    True,
                    "goals",
                    "over",
                    0.5,
                    prob,
                    conviction,
                    conviction,
                    f"Jogo 0x0 com probabilidade {prob:.0%} de sair o primeiro gol ate {'o intervalo' if end_minute == 45 else 'o fim'}; conviccao {conviction} por chutes {total_shots:g}, no gol {shots_on:g}, escanteios {corners:g} e {pressure_label}.",
                    strategy_name,
                )
            )

    for line, threshold, name in (
        (1.5, 0.75, "Over 1.5 FT"),
        (2.5, 0.72, "Over 2.5 FT"),
    ):
        if line == 1.5 and minute < 55:
            continue
        if line == 1.5 and current_goals >= 1 and minute < 70:
            continue
        needed_goals = _needed_over(current_goals, line)
        if needed_goals <= 0:
            continue
        prob = _poisson_at_least(goal_mean, needed_goals)
        score = round(prob * 100)
        if prob >= threshold and score >= min_confidence:
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
                    name,
                )
            )

    if 70 <= minute <= 86 and current_goals > 0:
        next_goal_line = current_goals + 0.5
        prob = _poisson_at_least(goal_mean, 1)
        score = round(prob * 100)
        conviction = _next_goal_conviction(score, total_shots, shots_on, corners, pressure)
        if prob >= 0.50 and conviction >= min_confidence:
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
                    "Asian Goal +0.5 FT",
                )
            )

    corner_window = _next_corner_window(minute)
    if corner_window:
        strategy_name, threshold = corner_window
        corner_mean = _corner_lambda(minute, corners, total_shots, pressure)
        prob = _poisson_at_least(corner_mean, 1)
        score = round(prob * 100)
        conviction = _next_corner_conviction(
            probability_score=score,
            minute=minute,
            corners=corners,
            total_shots=total_shots,
            shots_on=shots_on,
            pressure=pressure,
        )
        if prob >= threshold and conviction >= min_confidence:
            candidates.append(
                DeterministicSignal(
                    True,
                    "corners",
                    "over",
                    corners + 0.5,
                    prob,
                    conviction,
                    conviction,
                    f"Probabilidade {prob:.0%} de pelo menos mais um escanteio; linha sugerida over {corners + 0.5:g}; conviccao {conviction} por escanteios {corners:g}, chutes {total_shots:g}, no gol {shots_on:g} e {pressure_label}.",
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

    return sorted(candidates, key=lambda item: (_candidate_priority(item), item.score, item.probability), reverse=True)[0]
