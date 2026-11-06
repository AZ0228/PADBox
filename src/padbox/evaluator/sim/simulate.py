"""Scenario and Monte Carlo runs for one supplied team.

Scenario mode installs a fixed board at the start of each turn. Monte Carlo
mode runs seeded skyfall boards. Defaults are 10 turns, 1,000 simulations,
and seed 0. Simulation ``i`` uses ``seed + i``. Rates are policy approximations.
Recovery sufficiency is omitted because enemy damage is not simulated.
"""

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from padbox.evaluator.sim.policy import PolicyTarget, transform_monster
from padbox.evaluator.sim.schedule import MemberInput, open_battle, run, step
from padbox.evaluator.sim.state import BOARD_COLUMNS, BOARD_ROWS, GameState, TurnReport
from padbox.mechanics.skill_state import ActiveSlot

DEFAULT_TURNS = 10
DEFAULT_SIMULATIONS = 1000
DEFAULT_SEED = 0
_Z = 1.96
_GENERATORS = frozenset(
    {
        "create_orbs",
        "orb_replace",
        "orb_convert",
        "orb_change",
        "board_change",
        "column_change",
        "row_change",
        "board_refresh",
        "create_combo_orbs",
        "combo_convert",
        "skyfall",
    }
)
_MODE_NOTE = {
    "scenario": (
        "Scenario mode installs the supplied board at the start of each turn, before actives."
    ),
    "monte_carlo": "Monte Carlo simulation i uses seed + i. Each board is skyfall from that seed.",
}
_INTERVAL_NOTE = (
    "A 95% interval is the normal approximation across simulation rates "
    "and is omitted for a single rate."
)
_RECOVERY_NOTE = "Recovery sufficiency is omitted because enemy damage is not simulated."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FailureCount(_Strict):
    """How many times one reason kept a metric from succeeding."""

    code: str
    count: int


class RateInterval(_Strict):
    """Normal interval across per-simulation rates. Bounds lie in ``[0, 1]``."""

    low: float
    high: float
    level: float = 0.95


class Metric(_Strict):
    """One computed result. ``value`` is null when every sample was unresolved."""

    name: str
    value: float | None
    successes: int
    trials: int
    unresolved: int
    interval: RateInterval | None = None
    failures: list[FailureCount] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SimulationReport(_Strict):
    """The recorded run. Metrics the simulator did not compute are absent."""

    mode: Literal["scenario", "monte_carlo"]
    simulations: int
    turns: int
    seed: int
    seeds: list[int]
    assumptions: list[str]
    basis: Literal["policy_approximation"]
    metrics: list[Metric]


class _Rates:
    def __init__(self) -> None:
        self.leader = _Bucket()
        self.offense = _Bucket()
        self.defense = _Bucket()
        self.generated = _Bucket()
        self.healing = _Bucket()
        self.effects = _Bucket()
        self.mechanics = _Bucket()
        self.skills = _Bucket()
        self.gaps: list[int] = []


class _Bucket:
    def __init__(self) -> None:
        self.per_sim: list[tuple[int, int]] = []
        self.failures: dict[str, int] = {}
        self.unresolved = 0
        self.successes = 0
        self.trials = 0

    def add(self, successes: int, trials: int, unresolved: int, reasons: list[str]) -> None:
        self.per_sim.append((successes, trials))
        self.successes += successes
        self.trials += trials
        self.unresolved += unresolved
        for reason in reasons:
            self.failures[reason] = self.failures.get(reason, 0) + 1

    @property
    def rates(self) -> list[float]:
        return [success / trial for success, trial in self.per_sim if trial > 0]

    def metric(self, name: str, *, with_interval: bool, notes: list[str] | None = None) -> Metric:
        rates = self.rates
        value = sum(rates) / len(rates) if rates else None
        return Metric(
            name=name,
            value=value,
            successes=self.successes,
            trials=self.trials,
            unresolved=self.unresolved,
            interval=_interval(rates) if with_interval else None,
            failures=_failures(self.failures),
            notes=list(notes or []),
        )


def run_scenario(
    members: list[MemberInput],
    skills: dict[int, dict[str, Any]],
    boards: list[list[str]],
    cards: dict[int, dict[str, Any]] | None = None,
    *,
    target: PolicyTarget | None = None,
    seed: int = DEFAULT_SEED,
) -> SimulationReport:
    """Play one fixed board per turn. ``simulations`` is 1 and there is no interval."""
    _require_boards(boards)
    state = open_battle(members, skills, cards, seed=seed)
    for board in boards:
        step(state, target, board=board)
    return report_battles(
        [state],
        mode="scenario",
        seed=seed,
        seeds=[seed],
        target=target,
    )


def run_monte_carlo(
    members: list[MemberInput],
    skills: dict[int, dict[str, Any]],
    cards: dict[int, dict[str, Any]] | None = None,
    *,
    target: PolicyTarget | None = None,
    turns: int = DEFAULT_TURNS,
    simulations: int = DEFAULT_SIMULATIONS,
    seed: int = DEFAULT_SEED,
) -> SimulationReport:
    """Run ``simulations`` skyfall battles. Simulation ``i`` uses ``seed + i``."""
    if turns < 1 or simulations < 1:
        raise ValueError("turns and simulations must be positive")
    seeds = [seed + index for index in range(simulations)]
    states: list[GameState] = []
    for sim_seed in seeds:
        state = open_battle(members, skills, cards, seed=sim_seed)
        run(state, turns, target)
        states.append(state)
    return report_battles(
        states,
        mode="monte_carlo",
        seed=seed,
        seeds=seeds,
        target=target,
    )


def report_battles(
    states: list[GameState],
    *,
    mode: Literal["scenario", "monte_carlo"],
    seed: int,
    seeds: list[int],
    target: PolicyTarget | None = None,
) -> SimulationReport:
    """Summarize finished battles. Every battle must have the same turn count."""
    if not states:
        raise ValueError("a report needs at least one battle")
    if len(seeds) != len(states):
        raise ValueError("each battle needs its seed")
    turns = len(states[0].turn_reports)
    if any(len(state.turn_reports) != turns for state in states):
        raise ValueError("every simulation must cover the same number of turns")
    needs = target if target is not None else PolicyTarget()
    rates = _Rates()
    transform_turns: list[int] = []
    transform_misses = 0
    transform_notes: list[str] = []
    for state in states:
        _battle_rates(state, needs, rates)
        hits, misses, notes = _transforms(state)
        transform_turns.extend(hits)
        transform_misses += misses
        transform_notes.extend(notes)
    metrics = [
        _present(rates.leader, "leader_activation"),
        _present(rates.offense, "offensive_activation"),
        _present(rates.defense, "defensive_activation"),
        rates.generated.metric("orb_generation_uptime", with_interval=True),
        _gap_metric(rates.gaps, rates.leader.failures),
        _transform_metric(transform_turns, transform_misses, transform_notes),
        rates.skills.metric("skill_availability", with_interval=True),
        rates.healing.metric("healing_orb_availability", with_interval=True),
        rates.effects.metric("active_effect_uptime", with_interval=True),
        _present(rates.mechanics, "mechanic_response_availability"),
    ]
    assumptions = list(states[0].assumptions)
    assumptions.append(_MODE_NOTE[mode])
    assumptions.append(_INTERVAL_NOTE)
    assumptions.append(_RECOVERY_NOTE)
    return SimulationReport(
        mode=mode,
        simulations=len(states),
        turns=turns,
        seed=seed,
        seeds=list(seeds),
        assumptions=assumptions,
        basis="policy_approximation",
        metrics=[metric for metric in metrics if metric is not None],
    )


def _present(bucket: _Bucket, name: str) -> Metric | None:
    if bucket.successes == 0 and bucket.trials == 0 and bucket.unresolved == 0:
        return None
    return bucket.metric(name, with_interval=True)


def _battle_rates(state: GameState, target: PolicyTarget, rates: _Rates) -> None:
    leader_s = leader_t = leader_u = 0
    offense_s = offense_t = offense_u = 0
    defense_s = defense_t = defense_u = 0
    gen_s = gen_t = 0
    heal_s = heal_t = heal_u = 0
    effect_s = effect_t = 0
    mech_s = mech_t = 0
    leader_reasons: list[str] = []
    offense_reasons: list[str] = []
    defense_reasons: list[str] = []
    gen_reasons: list[str] = []
    heal_reasons: list[str] = []
    effect_reasons: list[str] = []
    mech_reasons: list[str] = []
    outcomes: list[str] = []
    for report in state.turn_reports:
        status, reasons = _clause_status(report.leader_tags, report.leader_verdicts, None)
        reasons = _with_board(report, status, reasons)
        leader_s, leader_t, leader_u, outcome = _count(status, leader_s, leader_t, leader_u)
        outcomes.append(outcome)
        if outcome != "met":
            leader_reasons.extend(reasons)
        off_status, off_reasons = _clause_status(
            report.leader_tags + report.helper_tags,
            report.leader_verdicts + report.helper_verdicts,
            "offensive",
        )
        off_reasons = _with_board(report, off_status, off_reasons)
        offense_s, offense_t, offense_u, off_outcome = _count(
            off_status, offense_s, offense_t, offense_u
        )
        if off_outcome != "met":
            offense_reasons.extend(off_reasons)
        def_status, def_reasons = _clause_status(
            report.leader_tags + report.helper_tags,
            report.leader_verdicts + report.helper_verdicts,
            "defensive",
        )
        def_reasons = _with_board(report, def_status, def_reasons)
        defense_s, defense_t, defense_u, def_outcome = _count(
            def_status, defense_s, defense_t, defense_u
        )
        if def_outcome != "met":
            defense_reasons.extend(def_reasons)
        kinds = set(report.applied_kinds) | set(report.active_effect_kinds)
        gen_t += 1
        if kinds & _GENERATORS:
            gen_s += 1
        else:
            gen_reasons.append("no_generator")
        if not report.board_observed:
            heal_u += 1
            heal_reasons.append("board_unobserved")
        elif report.heal_orbs_matched + report.heal_orbs_kept > 0:
            heal_s += 1
            heal_t += 1
        else:
            heal_t += 1
            heal_reasons.append("no_heal_orbs")
        effect_t += 1
        if report.active_effect_kinds:
            effect_s += 1
        else:
            effect_reasons.append("no_active_effect")
        for mechanic in target.mechanics:
            mech_t += 1
            if mechanic in kinds:
                mech_s += 1
            else:
                mech_reasons.append(f"unavailable:{mechanic}")
    skill_s, skill_t, skill_u, skill_reasons = _skill_counts(state, state.turn_reports)
    rates.leader.add(leader_s, leader_t, leader_u, leader_reasons)
    rates.offense.add(offense_s, offense_t, offense_u, offense_reasons)
    rates.defense.add(defense_s, defense_t, defense_u, defense_reasons)
    rates.generated.add(gen_s, gen_t, 0, gen_reasons)
    rates.healing.add(heal_s, heal_t, heal_u, heal_reasons)
    rates.effects.add(effect_s, effect_t, 0, effect_reasons)
    if target.mechanics:
        rates.mechanics.add(mech_s, mech_t, 0, mech_reasons)
    rates.skills.add(skill_s, skill_t, skill_u, skill_reasons)
    gap = _longest(outcomes)
    if gap is not None:
        rates.gaps.append(gap)


def _skill_counts(
    state: GameState, reports: list[TurnReport]
) -> tuple[int, int, int, list[str]]:
    successes = trials = unresolved = 0
    reasons: list[str] = []
    for report in reports:
        for member in state.members:
            if member.index in report.unknown_cooldown_members:
                unresolved += 1
                reasons.append("unknown_cooldown")
            elif member.index in report.bound_members:
                trials += 1
                reasons.append("bound")
            elif member.index in report.ready_members:
                successes += 1
                trials += 1
            else:
                trials += 1
                reasons.append("not_ready")
    return successes, trials, unresolved, reasons


def _count(status: str, successes: int, trials: int, unresolved: int) -> tuple[int, int, int, str]:
    if status == "met":
        return successes + 1, trials + 1, unresolved, "met"
    if status == "unmet":
        return successes, trials + 1, unresolved, "unmet"
    if status == "unresolved":
        return successes, trials, unresolved + 1, "unresolved"
    return successes, trials, unresolved, "absent"


def _clause_status(
    tags: list,
    verdicts: list,
    role: Literal["offensive", "defensive"] | None,
) -> tuple[str, list[str]]:
    if len(tags) != len(verdicts):
        return "unresolved", ["verdict_mismatch"]
    chosen = []
    for tag, verdict in zip(tags, verdicts, strict=True):
        if role == "offensive" and not tag.offensive:
            continue
        if role == "defensive" and not tag.defensive:
            continue
        chosen.append((tag, verdict))
    if not chosen:
        return "absent", []
    reasons: list[str] = []
    unresolved = False
    unmet = False
    for tag, verdict in chosen:
        if verdict.status == "unknown":
            unresolved = True
            reasons.append(f"unknown:{tag.clause_id}")
        elif verdict.status == "unmet":
            unmet = True
            reasons.append(f"unmet:{tag.clause_id}")
    if unresolved:
        return "unresolved", reasons
    if unmet:
        return "unmet", reasons
    return "met", []


def _with_board(report: TurnReport, status: str, reasons: list[str]) -> list[str]:
    if report.board_observed or status in {"met", "absent"}:
        return reasons
    return [*reasons, "board_unobserved"]


def _longest(outcomes: list[str]) -> int | None:
    if not outcomes or all(item == "absent" for item in outcomes):
        return None
    best = 0
    current = 0
    for outcome in outcomes:
        if outcome == "met":
            best = max(best, current)
            current = 0
        else:
            current += 1
    return max(best, current)


def _transforms(state: GameState) -> tuple[list[int], int, list[str]]:
    hits: list[int] = []
    notes: list[str] = []
    misses = 0
    first: dict[int, tuple[int, int]] = {}
    for cast in state.casts:
        if not cast.fired or cast.transform_to is None or cast.member_index in first:
            continue
        first[cast.member_index] = (cast.turn, cast.monster_id)
    for member in state.members:
        if not _can_transform(member.active):
            continue
        found = first.get(member.index)
        if found is None:
            misses += 1
            notes.append(f"#{member.entered_monster_id} did not transform")
            continue
        turn, monster_id = found
        hits.append(turn)
        notes.append(f"#{monster_id} transformed on turn {turn}")
    return hits, misses, notes


def _can_transform(slot: ActiveSlot) -> bool:
    return any(
        transform_monster(active.effects) is not None for active in slot.machine.states.values()
    )


def _gap_metric(gaps: list[int], failures: dict[str, int]) -> Metric | None:
    if not gaps:
        return None
    return Metric(
        name="longest_activation_gap",
        value=float(max(gaps)),
        successes=0,
        trials=0,
        unresolved=0,
        failures=_failures(failures),
        notes=["Longest run of turns, in any simulation, that were not leader activations."],
    )


def _transform_metric(turns: list[int], misses: int, notes: list[str]) -> Metric | None:
    trials = len(turns) + misses
    if trials == 0:
        return None
    value = sum(turns) / len(turns) if turns else None
    failures = [FailureCount(code="not_transformed", count=misses)] if misses else []
    return Metric(
        name="turns_to_transform",
        value=value,
        successes=len(turns),
        trials=trials,
        unresolved=0,
        failures=failures,
        notes=notes,
    )


def _interval(rates: list[float]) -> RateInterval | None:
    count = len(rates)
    if count < 2:
        return None
    mean = sum(rates) / count
    variance = sum((rate - mean) ** 2 for rate in rates) / (count - 1)
    margin = _Z * math.sqrt(variance / count)
    return RateInterval(low=max(0.0, mean - margin), high=min(1.0, mean + margin))


def _failures(counts: dict[str, int]) -> list[FailureCount]:
    return [FailureCount(code=code, count=counts[code]) for code in sorted(counts)]


def _require_boards(boards: list[list[str]]) -> None:
    if not boards:
        raise ValueError("a scenario needs at least one board")
    width = BOARD_COLUMNS * BOARD_ROWS
    for board in boards:
        if len(board) != width or any(not isinstance(color, str) or not color for color in board):
            raise ValueError("a scenario board is 30 non-empty color names")
