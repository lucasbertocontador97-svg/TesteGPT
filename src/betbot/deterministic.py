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


def _goal_lambda(minute: int, current_goals: int, total_shots: float, shots_on: float, dangerous: float) -> float:
    remaining = max(0, 90 - minute)
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
    corners = _sum_stat(stats, ("Corner Kicks", "Corners"))

    if _dead_game(minute, total_shots, shots_on, dangerous):
        return DeterministicSignal(False, "none", "none", None, 0, 0, 0, "Jogo morto: baixo volume ofensivo e pouca pressao.", "dead_game")

    candidates: list[DeterministicSignal] = []

    goal_mean = _goal_lambda(minute, current_goals, total_shots, shots_on, dangerous)
    for line, threshold, name in (
        (1.5, 0.75, "Over 1.5 FT"),
        (2.5, 0.72, "Over 2.5 FT"),
    ):
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
                    f"Poisson {prob:.0%} para over {line:g}; chutes {total_shots:g}, no gol {shots_on:g}, ataques perigosos {dangerous:g}.",
                    name,
                )
            )

    if 70 <= minute <= 86:
        next_goal_line = current_goals + 0.5
        prob = _poisson_at_least(goal_mean, 1)
        score = round(prob * 100)
        if prob >= 0.50 and score >= min_confidence:
            candidates.append(
                DeterministicSignal(
                    True,
                    "goals",
                    "over",
                    next_goal_line,
                    prob,
                    score,
                    score,
                    f"Probabilidade {prob:.0%} de pelo menos mais um gol; pressao medida por chutes {total_shots:g} e ataques perigosos {dangerous:g}.",
                    "Asian Goal +0.5 FT",
                )
            )

    corner_mean = _corner_lambda(minute, corners, total_shots, dangerous)
    if 32 <= minute <= 40 or 78 <= minute <= 86:
        prob = _poisson_at_least(corner_mean, 1)
        threshold = 0.60 if minute <= 40 else 0.70
        score = round(prob * 100)
        if prob >= threshold and score >= min_confidence:
            candidates.append(
                DeterministicSignal(
                    True,
                    "corners",
                    "over",
                    corners + 0.5,
                    prob,
                    score,
                    score,
                    f"Poisson {prob:.0%} para mais um escanteio; escanteios atuais {corners:g}, ataques perigosos {dangerous:g}.",
                    "Asian Corner +0.5 HT" if minute <= 40 else "Asian Corner +0.5 FT",
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
            f"Nenhum mercado passou nos thresholds matematicos. Chutes {total_shots:g}, no gol {shots_on:g}, escanteios {corners:g}, ataques perigosos {dangerous:g}.",
            "no_signal",
        )

    return sorted(candidates, key=lambda item: (item.score, item.probability), reverse=True)[0]
