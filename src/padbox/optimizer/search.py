"""Staged beam search over a generated candidate pool.

The search builds complete teams of one leader, four subs, and one helper.
No copy is used twice. A cheap deterministic screen keeps a diverse beam.
Assists are chosen for that beam. ``evaluate_team`` scores those teams, and
each ranked team carries a report of why it places where it does.

The result is the best beam this screen retained for the named objective.
It is not a global optimum.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from padbox.database.store import Store
from padbox.evaluator import AssistSpec, Scenario, SubSpec, TeamSpec, evaluate_team
from padbox.evaluator.dungeon import DungeonProfile, answer_keys, unmodeled_mechanics
from padbox.evaluator.evaluate import EvaluationReport
from padbox.evaluator.score import OBJECTIVE_ORDER, ObjectiveName
from padbox.evaluator.sim import DEFAULT_SEED, DEFAULT_SIMULATIONS, DEFAULT_TURNS
from padbox.inventory.snapshots import current_box
from padbox.mechanics.coverage import STANDARD_SUB_SLOTS
from padbox.optimizer.candidates import Candidate, CandidatePool, generate_candidates

DEFAULT_BEAM_WIDTH = 200
HelperSupply = Literal["owned", "explicit"]
_DEFENSE = (
    "bind_clear",
    "awoken_bind_clear",
    "unmatchable_clear",
    "attribute_absorb",
    "damage_absorb",
    "combo_absorb",
    "void_pierce",
    "haste",
)
_GUARANTEE = (
    "The ranked teams are the diverse beam after a cheap screen. Assists on that beam "
    "are chosen jointly with the skill-timing plan, and evaluate_team scores each "
    "assisted team for the named objective, seed, turn count, and simulation count. "
    "Teams outside the beam were not simulated. This is not a global optimum."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamMember(_Strict):
    """One fielded copy. ``copy_index`` distinguishes duplicates of one monster."""

    owned_id: int
    copy_index: int
    form_id: int
    form_name: str
    borrowed: bool = False


class ChosenHelper(TeamMember):
    """The helper for one team. ``supply`` is owned discovery or an explicit id."""

    supply: HelperSupply


class FieldedSub(TeamMember):
    """One sub. ``assist`` spends a different copy and replaces this sub's active."""

    assist: TeamMember | None = None


class SearchedTeam(_Strict):
    """One complete lineup. ``evaluation`` is set only after ``evaluate_team``."""

    helper: ChosenHelper
    subs: list[FieldedSub]
    leader_assist: TeamMember | None = None
    helper_assist: TeamMember | None = None
    cheap_score: list[float]
    covered_roles: list[str]
    uncertainty: float
    assist_notes: list[str] = Field(default_factory=list)
    evaluation: EvaluationReport | None = None
    report: Any = None


class SearchResult(_Strict):
    """Beam search for one leader. ``ranked`` is the simulated beam in evaluator order."""

    leader_id: int
    objective: ObjectiveName
    beam_width: int
    seed: int
    turns: int
    simulations: int
    guarantee: str
    complete_teams_scored: int
    beam: list[SearchedTeam]
    ranked: list[SearchedTeam]
    notes: list[str] = Field(default_factory=list)
    dungeon: DungeonProfile | None = None


@dataclass
class _Lineup:
    helper: Candidate
    subs: list[Candidate]
    used: frozenset[tuple[int, int]]
    remaining: Counter[int]
    score: tuple[float, ...]
    covered: tuple[str, ...]
    uncertainty: float


def search_teams(
    store: Store,
    leader_id: int,
    helper_id: int | None = None,
    *,
    objective: ObjectiveName = "general",
    beam_width: int = DEFAULT_BEAM_WIDTH,
    seed: int = DEFAULT_SEED,
    turns: int = DEFAULT_TURNS,
    simulations: int = DEFAULT_SIMULATIONS,
    skill_index: dict | None = None,
    pool: CandidatePool | None = None,
    dungeon: DungeonProfile | None = None,
) -> SearchResult:
    """Search the owned box for complete teams and simulate the retained beam.

    Pass ``helper_id`` to require that helper. Otherwise the helper is one of
    the user's own copies. A helper that was not named and is not owned is
    not considered. ``dungeon`` makes each listed mechanic an essential
    requirement on the scenario passed to ``evaluate_team``. ``beam_width``
    caps the diverse beam at each sub slot and caps how many teams
    ``evaluate_team`` sees. The defaults match the evaluator: objective
    ``general``, seed 0, 10 turns, 1,000 simulations.
    """
    if beam_width < 1:
        raise ValueError("beam_width must be positive")
    scenario = Scenario(
        name=objective, seed=seed, turns=turns, simulations=simulations, dungeon=dungeon
    )
    built = pool if pool is not None else generate_candidates(
        store, leader_id, helper_id, skill_index
    )
    notes = _dungeon_notes(dungeon)
    leader = next((row for row in built.candidates if row.kind == "leader"), None)
    if leader is None:
        notes.append("No owned copy can become this leader.")
        return _empty(leader_id, scenario, beam_width, notes)
    counts = _box_counts(store)
    after_leader = _consume(counts, leader)
    if after_leader is None:
        notes.append("The leader's copy and evolution spends are not available together.")
        return _empty(leader_id, scenario, beam_width, notes)
    leader_used = frozenset({(leader.owned_id, leader.copy_index)})
    helpers = _helper_rows(built, helper_id)
    if not helpers:
        notes.append(
            "No helper is available. Pass an explicit helper, or own a copy with a leader skill."
        )
        return _empty(leader_id, scenario, beam_width, notes)
    essential = tuple(dict.fromkeys((*built.essential_roles, *answer_keys(dungeon))))
    transform_turns = built.transform_turns
    opening = _open_helpers(
        leader, helpers, leader_used, after_leader, essential, objective, transform_turns
    )
    if not opening:
        notes.append("Every helper copy conflicts with the reserved leader copy or its spends.")
        return _empty(leader_id, scenario, beam_width, notes)
    states = _keep_diverse(_dedupe(opening), beam_width)
    subs = [row for row in built.candidates if row.kind == "sub"]
    scored = 0
    for slot in range(STANDARD_SUB_SLOTS):
        expanded: list[_Lineup] = []
        for state in states:
            for sub in subs:
                nxt = _expand(leader, state, sub, essential, objective, transform_turns)
                if nxt is not None:
                    expanded.append(nxt)
        unique = _dedupe(expanded)
        if slot == STANDARD_SUB_SLOTS - 1:
            scored = len(unique)
        states = _keep_diverse(unique, beam_width)
        if not states:
            notes.append(
                f"No legal team reached {STANDARD_SUB_SLOTS} subs. "
                f"Search stopped while filling sub {slot + 1}."
            )
            return _empty(leader_id, scenario, beam_width, notes, scored)
    beam = [_public(state) for state in states]
    from padbox.optimizer.assists import attach_assists

    attach_assists(store, built, beam, turns=turns)
    for team in beam:
        team.evaluation = evaluate_team(
            _spec(leader_id, team),
            scenario,
            store=store,
            skill_index=skill_index,
            explain=False,
        )
    from padbox.optimizer.report import explain_ranked, order_key

    ranked = sorted(beam, key=lambda team: order_key(team, objective))
    guarantee = _guarantee(dungeon)
    explain_ranked(
        ranked,
        leader_id=leader_id,
        objective=objective,
        seed=seed,
        turns=turns,
        simulations=simulations,
        beam_width=beam_width,
        complete_teams_scored=scored,
        guarantee=guarantee,
    )
    return SearchResult(
        leader_id=leader_id,
        objective=objective,
        beam_width=beam_width,
        seed=seed,
        turns=turns,
        simulations=simulations,
        guarantee=guarantee,
        complete_teams_scored=scored,
        beam=beam,
        ranked=ranked,
        notes=notes,
        dungeon=dungeon,
    )


def _guarantee(dungeon: DungeonProfile | None) -> str:
    if dungeon is None:
        return _GUARANTEE
    return (
        _GUARANTEE + f" Dungeon mechanics on {dungeon.name} are essential requirements. "
        "A floor is handled only when every listed mechanic is modeled and met. "
        "Unmodeled mechanics stay uncertainties. This search does not say a team clears a floor."
    )


def _dungeon_notes(dungeon: DungeonProfile | None) -> list[str]:
    return [
        f"{name} is unmodeled. It is an uncertainty on the dungeon profile "
        "and is not a reason to drop a card."
        for name in unmodeled_mechanics(dungeon)
    ]


def _empty(
    leader_id: int,
    scenario: Scenario,
    beam_width: int,
    notes: list[str],
    scored: int = 0,
) -> SearchResult:
    return SearchResult(
        leader_id=leader_id,
        objective=scenario.name,
        beam_width=beam_width,
        seed=scenario.seed,
        turns=scenario.turns,
        simulations=scenario.simulations,
        guarantee=_guarantee(scenario.dungeon),
        complete_teams_scored=scored,
        beam=[],
        ranked=[],
        notes=notes,
        dungeon=scenario.dungeon,
    )


def _box_counts(store: Store) -> Counter[int]:
    box = current_box(store) or {}
    counts: Counter[int] = Counter()
    for row in box.get("monsters") or []:
        quantity = int(row.get("quantity") or 0)
        if quantity < 1:
            continue
        counts[int(row["monster_id"])] += quantity
    return counts


def _consume(remaining: Counter[int], candidate: Candidate) -> Counter[int] | None:
    need: Counter[int] = Counter(candidate.spends)
    if not candidate.borrowed:
        need[candidate.owned_id] += 1
    if any(remaining[monster_id] < count for monster_id, count in need.items()):
        return None
    updated = remaining.copy()
    updated.subtract(need)
    return updated


def _helper_rows(pool: CandidatePool, helper_id: int | None) -> list[Candidate]:
    rows = [row for row in pool.candidates if row.kind == "helper"]
    if helper_id is None:
        return [row for row in rows if row.helper_origin == "owned" and not row.borrowed]
    exact = [row for row in rows if row.form_id == helper_id and row.helper_origin == "explicit"]
    if exact:
        return exact
    return [row for row in rows if row.helper_origin == "explicit"]


def _open_helpers(
    leader: Candidate,
    helpers: list[Candidate],
    leader_used: frozenset[tuple[int, int]],
    remaining: Counter[int],
    essential: tuple[str, ...],
    objective: ObjectiveName,
    transform_turns: int | None,
) -> list[_Lineup]:
    opened: list[_Lineup] = []
    for helper in helpers:
        pair = (helper.owned_id, helper.copy_index)
        if not helper.borrowed and pair in leader_used:
            continue
        left = _consume(remaining, helper)
        if left is None:
            continue
        used = set(leader_used)
        if not helper.borrowed:
            used.add(pair)
        opened.append(
            _make(
                leader, helper, [], frozenset(used), left, essential, objective, transform_turns
            )
        )
    return opened


def _expand(
    leader: Candidate,
    state: _Lineup,
    sub: Candidate,
    essential: tuple[str, ...],
    objective: ObjectiveName,
    transform_turns: int | None,
) -> _Lineup | None:
    pair = (sub.owned_id, sub.copy_index)
    if pair in state.used:
        return None
    left = _consume(state.remaining, sub)
    if left is None:
        return None
    return _make(
        leader,
        state.helper,
        [*state.subs, sub],
        state.used | {pair},
        left,
        essential,
        objective,
        transform_turns,
    )


def _make(
    leader: Candidate,
    helper: Candidate,
    subs: list[Candidate],
    used: frozenset[tuple[int, int]],
    remaining: Counter[int],
    essential: tuple[str, ...],
    objective: ObjectiveName,
    transform_turns: int | None,
) -> _Lineup:
    ordered = sorted(subs, key=_copy_key)
    parts = _cheap_parts(leader, helper, ordered, essential, transform_turns)
    uncertainty = leader.uncertainty + helper.uncertainty + sum(sub.uncertainty for sub in ordered)
    covered = _covered(leader, helper, ordered, essential)
    return _Lineup(
        helper=helper,
        subs=ordered,
        used=used,
        remaining=remaining,
        score=_cheap_tuple(parts, objective, uncertainty, helper, ordered),
        covered=covered,
        uncertainty=uncertainty,
    )


def _cheap_parts(
    leader: Candidate,
    helper: Candidate,
    subs: list[Candidate],
    essential: tuple[str, ...],
    transform_turns: int | None,
) -> dict[str, float]:
    """Higher is better. This screen is not the evaluator scalar."""
    members = [leader, helper, *subs]
    answers: set[str] = set()
    for member in members:
        answers.update(member.answers)
    needed = [role for role in essential if role.endswith("_orbs") or role.endswith("_board")]
    color_hits = sum(1 for role in needed if role in answers)
    cooldowns = [
        member.minimum_cooldown
        for member in members
        if isinstance(member.minimum_cooldown, int)
        and any(role in member.answers for role in needed)
    ]
    cooldown_term = 0.0 if not cooldowns else 1.0 / (1 + min(cooldowns))
    if transform_turns is not None and transform_turns > 0:
        boost = sum(member.skill_boost for member in members)
        boost_term = min(boost, transform_turns) / transform_turns
    elif "skill_boost" not in essential:
        boost_term = 1.0
    else:
        boost = sum(member.skill_boost for member in members)
        boost_term = min(boost, STANDARD_SUB_SLOTS) / STANDARD_SUB_SLOTS
    defense = [role for role in _DEFENSE if role in essential]
    damage = sum(1 for sub in subs if sub.can_damage) + (1 if helper.can_damage else 0)
    return {
        "activation_consistency": color_hits + cooldown_term + boost_term,
        "survivability": float(sum(1 for role in defense if role in answers)),
        "damage": float(damage),
        "flexibility": _flexibility(leader, helper, subs),
    }


def _flexibility(leader: Candidate, helper: Candidate, subs: list[Candidate]) -> float:
    covered = set(leader.answers) | set(helper.answers)
    unused = 0
    for sub in subs:
        fresh = [answer for answer in sub.answers if answer not in covered]
        if sub.answers and not fresh:
            unused += 1
        covered.update(sub.answers)
    if not subs:
        return 1.0
    value = 1 - max(0, unused - 1) / len(subs)
    return max(0.0, min(1.0, value))


def _cheap_tuple(
    parts: dict[str, float],
    objective: ObjectiveName,
    uncertainty: float,
    helper: Candidate,
    subs: list[Candidate],
) -> tuple[float, ...]:
    identity = _identity_penalty(helper, subs)
    ordered = tuple(parts[name] for name in OBJECTIVE_ORDER[objective])
    return (*ordered, -uncertainty, *identity)


def _identity_penalty(helper: Candidate, subs: list[Candidate]) -> tuple[float, ...]:
    ids = [helper.form_id, helper.owned_id, helper.copy_index]
    for sub in subs:
        ids.extend((sub.form_id, sub.owned_id, sub.copy_index))
    return tuple(-float(value) for value in ids)


def _covered(
    leader: Candidate,
    helper: Candidate,
    subs: list[Candidate],
    essential: tuple[str, ...],
) -> tuple[str, ...]:
    answers: set[str] = set(leader.answers) | set(helper.answers)
    for sub in subs:
        answers.update(sub.answers)
    return tuple(role for role in essential if role in answers)


def _dedupe(states: list[_Lineup]) -> list[_Lineup]:
    found: dict[tuple, _Lineup] = {}
    for state in states:
        key = (
            state.helper.owned_id,
            state.helper.copy_index,
            state.helper.form_id,
            state.helper.borrowed,
            tuple((sub.owned_id, sub.copy_index, sub.form_id) for sub in state.subs),
        )
        found.setdefault(key, state)
    return list(found.values())


def _keep_diverse(states: list[_Lineup], width: int) -> list[_Lineup]:
    if len(states) <= width:
        return sorted(states, key=lambda state: state.score, reverse=True)
    groups: dict[tuple[str, ...], list[_Lineup]] = defaultdict(list)
    for state in states:
        groups[state.covered].append(state)
    for rows in groups.values():
        rows.sort(key=lambda state: state.score, reverse=True)
    order = sorted(groups, key=lambda sig: groups[sig][0].score, reverse=True)
    picked: list[_Lineup] = []
    while len(picked) < width:
        grew = False
        for sig in order:
            if not groups[sig]:
                continue
            picked.append(groups[sig].pop(0))
            grew = True
            if len(picked) == width:
                break
        if not grew:
            break
    return picked


def _supply(helper: Candidate) -> HelperSupply:
    if helper.helper_origin == "explicit":
        return "explicit"
    return "owned"


def _member(candidate: Candidate) -> TeamMember:
    return TeamMember(
        owned_id=candidate.owned_id,
        copy_index=candidate.copy_index,
        form_id=candidate.form_id,
        form_name=candidate.form_name,
        borrowed=candidate.borrowed,
    )


def _public(state: _Lineup) -> SearchedTeam:
    helper = _member(state.helper)
    return SearchedTeam(
        helper=ChosenHelper(
            owned_id=helper.owned_id,
            copy_index=helper.copy_index,
            form_id=helper.form_id,
            form_name=helper.form_name,
            borrowed=helper.borrowed,
            supply=_supply(state.helper),
        ),
        subs=[
            FieldedSub(
                owned_id=sub.owned_id,
                copy_index=sub.copy_index,
                form_id=sub.form_id,
                form_name=sub.form_name,
                borrowed=sub.borrowed,
            )
            for sub in state.subs
        ],
        cheap_score=list(state.score),
        covered_roles=list(state.covered),
        uncertainty=state.uncertainty,
    )


def _assist_spec(member: TeamMember | None) -> AssistSpec | None:
    if member is None:
        return None
    return AssistSpec(owned_id=member.owned_id, form_id=member.form_id)


def _spec(leader_id: int, team: SearchedTeam) -> TeamSpec:
    return TeamSpec(
        leader_id=leader_id,
        helper_id=team.helper.form_id,
        leader_assist=_assist_spec(team.leader_assist),
        helper_assist=_assist_spec(team.helper_assist),
        subs=[
            SubSpec(
                owned_id=sub.owned_id,
                form_id=sub.form_id,
                assist=_assist_spec(sub.assist),
            )
            for sub in team.subs
        ],
    )


def _copy_key(candidate: Candidate) -> tuple[int, int, int]:
    return (candidate.owned_id, candidate.copy_index, candidate.form_id)
