"""Turn scheduler for one supplied team.

Counters start at the max-skill minimum. Skill boosts land before turn 1.
Each turn fills empty cells, ticks, uses ready actives, then matches. Haste,
skill delay, assist overcharge, and effect durations are applied here.
The active-skill machine still owns the state transition and the refusal
to cast a skill that is not ready.
"""

import random
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from padbox.evaluator.sim.board import apply_board_effect, fill_empty, install_board, resolve_turn
from padbox.evaluator.sim.policy import Phase, PolicyTarget, choose, transform_monster
from padbox.evaluator.sim.state import (
    ASSUMPTIONS,
    BoardChange,
    CastReason,
    CastRecord,
    GameState,
    MemberState,
    SkillDelayEvent,
    TimedEffect,
    board_is_supported,
    effective_rcv,
    empty_board,
    team_hp_max,
)
from padbox.mechanics.awakenings import NAMES, skill_boost
from padbox.mechanics.clauses import LeaderClause, leader_clauses
from padbox.mechanics.decode import ATTRIBUTES, CARD_TYPES, Effect, decode_skill
from padbox.mechanics.skill_state import ActiveSlot, SequenceKind, machine_for, with_inherited

_ROLE_ORDER = {"leader": 0, "sub": 1, "helper": 2}


def _awakening_ids(*names: str) -> frozenset[int]:
    wanted = set(names)
    return frozenset(awakening_id for awakening_id, name in NAMES.items() if name in wanted)


_SKILL_CHARGE = _awakening_ids("Skill Charge", "Skill Charge+")
_DELAY_RESIST = _awakening_ids("Skill Delay Resistance")
_TIMED_KINDS = {
    "damage_shield",
    "attribute_guard",
    "rcv_buff",
    "void_pierce",
    "absorb_void",
    "no_skyfall",
    "self_attribute",
    "heal_over_time",
    "added_combos",
    "defense_break",
    "team_atk",
    "all_allies_atk",
    "attribute_atk",
    "type_atk",
    "skyfall",
    "damage_cap",
    "board_size",
}
_BOARD_KINDS = {
    "create_orbs",
    "orb_replace",
    "orb_convert",
    "orb_change",
    "board_change",
    "column_change",
    "row_change",
    "skyfall",
    "board_refresh",
    "orb_enhance",
    "create_combo_orbs",
    "combo_convert",
    "lock_clear",
}
_COLOR_KEYS = ("attributes", "to", "orbs", "colors", "destination", "destinations")
_SOURCE_KEYS = ("from", "excluded")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemberInput(_Strict):
    """One monster entering the battle, plus an optional assist."""

    role: Literal["leader", "sub", "helper"]
    card: dict[str, Any]
    assist: dict[str, Any] | None = None
    evolution_met: bool = True
    sequence: SequenceKind | None = None
    awakenings: list[int] | None = None
    assist_awakenings: list[int] | None = None


def open_battle(
    members: list[MemberInput],
    skills: dict[int, dict[str, Any]],
    cards: dict[int, dict[str, Any]] | None = None,
    *,
    seed: int = 0,
) -> GameState:
    """Build turn 0 and apply the team's skill boost. The first tick is turn 1."""
    if not members:
        raise ValueError("a battle needs at least one member")
    card_index = dict(cards or {})
    ordered = sorted(enumerate(members), key=lambda item: (_ROLE_ORDER[item[1].role], item[0]))
    built: list[MemberState] = []
    problems: list[str] = []
    boost_known = True
    boost_total = 0
    for play_index, (_, spec) in enumerate(ordered):
        card_index.setdefault(int(spec.card["id"]), spec.card)
        if spec.assist is not None:
            card_index.setdefault(int(spec.assist["id"]), spec.assist)
        member, member_boost, member_known = _build_member(
            spec, play_index, skills, card_index, problems
        )
        built.append(member)
        boost_known = boost_known and member_known
        boost_total += member_boost
    leaders = sum(member.role == "leader" for member in built)
    subs = sum(member.role == "sub" for member in built)
    helpers = sum(member.role == "helper" for member in built)
    state = GameState(
        seed=seed,
        members=built,
        board=empty_board(),
        unknowns=problems,
        assumptions=list(ASSUMPTIONS),
        opening_skill_boost=boost_total if boost_known else None,
        skill_boost_known=boost_known,
        team_complete=leaders == 1 and subs == 4 and helpers == 1,
        sub_count=subs,
        helper_count=helpers,
        hp=_opening_hp(built),
    )
    state._cards = card_index
    state._skills = skills
    state._rng = random.Random(seed)
    _install_clauses(state)
    if leaders > 1:
        state.unknowns.append(
            "Multiple leaders are present. Activation clauses use the last leader in slot order."
        )
    if helpers > 1:
        state.unknowns.append(
            "Multiple helpers are present. Activation clauses use the last helper in slot order."
        )
    if boost_known:
        for member in state.members:
            if not member.cooldown_exact:
                continue
            if not _apply_charge(member, boost_total):
                member.cooldown_exact = False
                state.unknowns.append(
                    f"{_label(member)} cooldown is unknown, so skill boost was not applied to it."
                )
    else:
        for member in state.members:
            member.cooldown_exact = False
    return state


def step(
    state: GameState,
    target: PolicyTarget | None = None,
    *,
    board: list[str] | None = None,
) -> list[CastRecord]:
    """Advance one turn: skyfall or a fixed board, tick, actives, then match.

    `board` is a row-major color list for the 6×5 grid. It replaces skyfall
    at the start of the turn. Actives still edit that board before the match.
    """
    needs = target if target is not None else PolicyTarget()
    state.turn += 1
    _expire(state)
    if board is not None:
        install_board(state, board)
        opening_skyfall = 0
    else:
        opening_skyfall = fill_empty(state) if board_is_supported(state) else 0
    _regen(state)
    for member in state.members:
        _tick(member)
    ready, unknown_cooldown, bound = _availability(state)
    start = len(state.casts)
    _resolve(state, needs)
    report = resolve_turn(state, needs, opening_skyfall)
    report.ready_members = ready
    report.unknown_cooldown_members = unknown_cooldown
    report.bound_members = bound
    report.applied_kinds = list(state._applied)
    report.active_effect_kinds = [
        effect.kind for effect in state.effects if not effect.unknown_duration
    ]
    report.responses, report.unknown_responses = _response_observations(state)
    state.turn_reports.append(report)
    state.leader_verdicts = report.leader_verdicts
    state.helper_verdicts = report.helper_verdicts
    for member in state.members:
        if member.bound_turns > 0:
            member.bound_turns -= 1
        if member.awoken_bound_turns > 0:
            member.awoken_bound_turns -= 1
    if state.board.unmatchable_turns > 0:
        state.board.unmatchable_turns -= 1
    return state.casts[start:]


def run(state: GameState, turns: int, target: PolicyTarget | None = None) -> GameState:
    """Run `turns` more steps on this battle."""
    if turns < 0:
        raise ValueError("turns must be zero or positive")
    for _ in range(turns):
        step(state, target)
    return state


def _response_observations(state: GameState) -> tuple[list[str], list[str]]:
    """Known-timing response keys, then keys whose duration is unknown."""
    known: list[str] = []
    unknown: list[str] = []
    for effect in state.effects:
        keys = _effect_responses(effect)
        target = unknown if effect.unknown_duration else known
        for key in keys:
            if key not in target:
                target.append(key)
    for kind in state._applied:
        if kind in {"awoken_bind_clear", "unmatchable_clear"} and kind not in known:
            known.append(kind)
    return known, unknown


def _effect_responses(effect: TimedEffect) -> list[str]:
    if effect.kind == "absorb_void":
        return [f"absorb_void:{name}" for name in effect.absorb_kinds]
    if effect.kind == "void_pierce":
        return ["void_pierce"]
    return []


def apply_skill_delay(state: GameState, turns: int) -> None:
    """Add enemy skill delay to each member.

    A member whose Skill Delay Resistance has no checklist magnitude keeps
    the counter unchanged. The result is recorded as unknown.
    """
    if turns < 0:
        raise ValueError("skill delay turns must be zero or positive")
    if turns == 0:
        return
    for member in state.members:
        if not member.cooldown_exact or member.charges_remaining() is None:
            state.skill_delays.append(
                SkillDelayEvent(
                    turn=state.turn,
                    member_index=member.index,
                    turns=turns,
                    applied=False,
                    reason="unknown_cooldown",
                )
            )
            continue
        if _has_delay_resist(member):
            member.cooldown_exact = False
            state.unknowns.append(
                f"Turn {state.turn}: #{member.monster_id} has Skill Delay Resistance "
                f"with no magnitude, so {turns} turns of skill delay were not applied."
            )
            state.skill_delays.append(
                SkillDelayEvent(
                    turn=state.turn,
                    member_index=member.index,
                    turns=turns,
                    applied=False,
                    reason="unknown_resistance",
                )
            )
            continue
        _add_wait(member, turns)
        state.skill_delays.append(
            SkillDelayEvent(
                turn=state.turn,
                member_index=member.index,
                turns=turns,
                applied=True,
                reason="applied",
            )
        )


def apply_bind(state: GameState, member_index: int, turns: int, *, awoken: bool = False) -> None:
    """Set the remaining bind on one member. The count falls at the end of a turn."""
    if turns < 0:
        raise ValueError("bind turns must be zero or positive")
    member = state.members[member_index]
    if awoken:
        member.awoken_bound_turns = turns
    else:
        member.bound_turns = turns


def _build_member(
    spec: MemberInput,
    index: int,
    skills: dict[int, dict[str, Any]],
    cards: dict[int, dict[str, Any]],
    problems: list[str],
) -> tuple[MemberState, int, bool]:
    label = _spec_label(spec, index)
    card = spec.card
    awakenings, known = _read_awakenings(card, spec.awakenings, label, problems)
    assist_awakenings: list[int] = []
    assist_id: int | None = None
    if spec.assist is not None:
        assist_id = int(spec.assist["id"])
        assist_awakenings, assist_known = _read_awakenings(
            spec.assist, spec.assist_awakenings, f"Assist on {label}", problems
        )
        known = known and assist_known
    _note_unmodeled_charge(awakenings + assist_awakenings, label, problems)
    active, gate = _slot_for(spec, skills, cards)
    exact = gate is not None and active.remaining is not None
    if not exact:
        problems.append(f"{label} cooldown is unknown.")
    described = _describe(card, label, problems)
    member = MemberState(
        index=index,
        role=spec.role,
        monster_id=int(card["id"]),
        entered_monster_id=int(card["id"]),
        name=str(card.get("name") or f"#{card['id']}"),
        hp_max=described[0],
        atk=described[1],
        rcv=described[2],
        attributes=described[3],
        card_types=described[4],
        awakenings=awakenings,
        assist_monster_id=assist_id,
        assist_awakenings=assist_awakenings,
        active=active,
        overcharge_remaining=gate if gate is not None else None,
        evolution_met=spec.evolution_met,
        cooldown_exact=exact,
    )
    boost = skill_boost(awakenings) + skill_boost(assist_awakenings)
    return member, boost, known


def _slot_for(
    spec: MemberInput,
    skills: dict[int, dict[str, Any]],
    cards: dict[int, dict[str, Any]],
) -> tuple[ActiveSlot, int | None]:
    if spec.sequence is None:
        machine = machine_for(spec.card, skills, cards)
    else:
        machine = machine_for(spec.card, skills, cards, sequence=spec.sequence)
    host = ActiveSlot.start(machine)
    if spec.assist is None:
        return host, 0
    assist = spec.assist
    skill_id = int(assist["activeSkillId"])
    skill = skills.get(skill_id)
    if skill is None:
        raise ValueError(f"assist active {skill_id} on #{assist['id']} is not in the skill index")
    inherited = with_inherited(host, skill, skills, int(assist["id"]))
    return inherited, host.remaining


def _read_awakenings(
    card: dict[str, Any],
    override: list[int] | None,
    label: str,
    problems: list[str],
) -> tuple[list[int], bool]:
    if override is not None:
        return [int(item) for item in override], True
    raw = card.get("awakenings") if "awakenings" in card else None
    if "awakenings" not in card or not isinstance(raw, list):
        problems.append(f"{label} awakenings are missing.")
        return [], False
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in raw):
        problems.append(f"{label} awakenings are missing.")
        return [], False
    return [int(item) for item in raw], True


def _note_unmodeled_charge(awakenings: list[int], label: str, problems: list[str]) -> None:
    if any(awakening_id in _SKILL_CHARGE for awakening_id in awakenings):
        problems.append(
            f"{label} has Skill Charge with no checklist magnitude, so it adds no charge."
        )


def _has_delay_resist(member: MemberState) -> bool:
    owned = member.awakenings + member.assist_awakenings
    return any(awakening_id in _DELAY_RESIST for awakening_id in owned)


def _describe(
    card: dict[str, Any], label: str, problems: list[str]
) -> tuple[int | None, int | None, int | None, list[str], list[str]]:
    hp = _stat(card, "hp")
    atk = _stat(card, "atk")
    rcv = _stat(card, "rcv")
    attributes = _named(card.get("attrs"), ATTRIBUTES, f"{label} attribute", problems)
    types = _named(card.get("types"), CARD_TYPES, f"{label} type", problems)
    return hp, atk, rcv, attributes, types


def _stat(card: dict[str, Any], key: str) -> int | None:
    block = card.get(key)
    if isinstance(block, dict) and isinstance(block.get("max"), int):
        return int(block["max"])
    return None


def _named(
    raw: Any,
    table: dict[int, str],
    label: str,
    problems: list[str],
) -> list[str]:
    if not isinstance(raw, list):
        problems.append(f"{label} is missing.")
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, int) and not isinstance(item, bool) and item in table:
            names.append(table[item])
        else:
            problems.append(f"{label} {item} is unknown.")
    return names


def _opening_hp(members: list[MemberState]) -> int | None:
    values = [member.hp_max for member in members]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _install_clauses(state: GameState) -> None:
    for member in state.members:
        if member.role not in {"leader", "helper"}:
            continue
        card = state._cards.get(member.monster_id)
        if card is None:
            _set_clauses(state, member, [], False)
            state.unknowns.append(f"{_label(member)} card is not in the card index.")
            continue
        clauses, known, problem = _clauses(card, state._skills)
        if problem:
            state.unknowns.append(problem)
        _set_clauses(state, member, clauses, known)


def _clauses(
    card: dict[str, Any], skills: dict[int, dict[str, Any]]
) -> tuple[list[LeaderClause], bool, str | None]:
    skill_id = int(card.get("leaderSkillId") or 0)
    if skill_id <= 0:
        return [], True, None
    skill = skills.get(skill_id)
    if skill is None:
        return [], False, f"Leader skill {skill_id} on #{card.get('id')} is not in the skill index."
    return leader_clauses(decode_skill(skill, skills)), True, None


def _set_clauses(
    state: GameState, member: MemberState, clauses: list[LeaderClause], known: bool
) -> None:
    if member.role == "leader":
        state.leader_clauses = clauses
        state.leader_clauses_known = known
    elif member.role == "helper":
        state.helper_clauses = clauses
        state.helper_clauses_known = known


def _resolve(state: GameState, target: PolicyTarget) -> None:
    state._applied = []
    for phase in ("transform", "utility"):
        _pass(state, target, phase)


def _pass(state: GameState, target: PolicyTarget, phase: Phase) -> None:
    for member in state.members:
        if choose(member, target, phase):
            _try_cast(state, member)


def _try_cast(state: GameState, member: MemberState) -> None:
    current = member.active.state()
    monster_id = member.monster_id
    remaining = member.charges_remaining()
    overcharge = member.overcharge_remaining
    used = member.active.used_this_turn
    formed = transform_monster(current.effects)
    kinds = [effect.kind for effect in current.effects]

    def record(fired: bool, reason: str, effect_kinds: list[str], transform_to: int | None) -> None:
        state.casts.append(
            _cast_record(
                state,
                member,
                monster_id,
                current,
                fired,
                reason,
                remaining,
                overcharge,
                used,
                effect_kinds,
                transform_to,
            )
        )

    if not state.skill_boost_known or not member.cooldown_exact:
        record(False, "unknown", kinds, formed)
        return
    if member.bound_turns > 0:
        record(False, "bound", kinds, formed)
        return
    if remaining is None:
        record(False, "unknown", kinds, formed)
        return
    if used:
        record(False, "already_used", kinds, formed)
        return
    if remaining > 0:
        record(False, "not_ready", kinds, formed)
        return
    if formed is not None and not member.evolution_met:
        record(False, "evolution", kinds, formed)
        return
    cast = member.active.use()
    if not cast.used:
        reason = "already_used" if cast.continuation == "already_used" else "not_ready"
        if cast.continuation == "unknown":
            reason = "unknown"
        record(False, reason, kinds, formed)
        return
    effect_kinds = [effect.kind for effect in cast.effects]
    transform_to = transform_monster(cast.effects)
    _apply_effects(state, member, cast.effects)
    if transform_to is not None:
        _adopt_form(state, member, transform_to)
    if member.active.remaining is None:
        member.cooldown_exact = False
        state.unknowns.append(
            f"Turn {state.turn}: #{monster_id} has no known cooldown after {current.state_id}."
        )
    record(True, "ready", effect_kinds, transform_to)


def _cast_record(
    state: GameState,
    member: MemberState,
    monster_id: int,
    current: Any,
    fired: bool,
    reason: CastReason,
    remaining: int | None,
    overcharge: int | None,
    used: bool,
    effect_kinds: list[str],
    transform_to: int | None,
) -> CastRecord:
    return CastRecord(
        turn=state.turn,
        member_index=member.index,
        monster_id=monster_id,
        state_id=current.state_id,
        skill_id=current.skill_id,
        state_kind=current.kind,
        fired=fired,
        reason=reason,
        remaining_before=remaining,
        overcharge_before=overcharge,
        used_before=used,
        evolution_met=member.evolution_met,
        effect_kinds=effect_kinds,
        transform_to=transform_to,
    )


def _availability(state: GameState) -> tuple[list[int], list[int], list[int]]:
    """Member indexes that are ready, unknown, or bound after the turn tick."""
    ready: list[int] = []
    unknown: list[int] = []
    bound: list[int] = []
    for member in state.members:
        if not member.cooldown_exact or member.charges_remaining() is None:
            unknown.append(member.index)
        elif member.bound_turns > 0:
            bound.append(member.index)
        elif member.charges_remaining() == 0:
            ready.append(member.index)
    return ready, unknown, bound


def _tick(member: MemberState) -> None:
    if not member.cooldown_exact:
        member.active.used_this_turn = False
        return
    if member.overcharge_remaining is None or member.active.remaining is None:
        member.cooldown_exact = False
        member.active.used_this_turn = False
        return
    if member.overcharge_remaining > 0:
        member.overcharge_remaining -= 1
        member.active.used_this_turn = False
        return
    member.active.tick()


def _apply_charge(member: MemberState, turns: int) -> bool:
    """Move a counter by `turns` charges. Negative turns add wait. Unknown stays unknown."""
    if member.overcharge_remaining is None or member.active.remaining is None:
        return False
    if turns == 0:
        return True
    if turns < 0:
        _add_wait(member, -turns)
        return True
    left = turns
    if member.overcharge_remaining > 0:
        take = min(member.overcharge_remaining, left)
        member.overcharge_remaining -= take
        left -= take
    if left:
        member.active.remaining = max(0, member.active.remaining - left)
    return True


def _add_wait(member: MemberState, turns: int) -> None:
    if member.overcharge_remaining is None or member.active.remaining is None:
        return
    if member.overcharge_remaining > 0:
        member.overcharge_remaining += turns
    else:
        member.active.remaining += turns


def _apply_effects(state: GameState, member: MemberState, effects: list[Effect]) -> None:
    for effect in effects:
        if effect.evidence != "verified":
            state.unknowns.append(
                f"Turn {state.turn}: #{member.monster_id} skipped unverified "
                f"effect {effect.kind}."
            )
            continue
        if effect.provenance is not None and effect.provenance.unknown_parameters:
            listed = ", ".join(effect.provenance.unknown_parameters)
            state.unknowns.append(
                f"Turn {state.turn}: #{member.monster_id} {effect.kind} leaves {listed} unknown."
            )
        state._applied.append(effect.kind)
        _apply_one(state, member, effect)


def _apply_one(state: GameState, member: MemberState, effect: Effect) -> None:
    if effect.kind == "charge_other_skills":
        _haste_others(state, member, effect)
        return
    if effect.kind == "delay":
        _delay_enemies(state, effect)
        return
    if effect.kind == "lock_clear":
        _clear_locks(state, member, effect)
    if effect.kind in {"bind_clear", "awoken_bind_clear", "unmatchable_clear"}:
        _clear_debuff(state, member, effect)
    if effect.kind in {"heal_flat", "heal_percent", "heal_rcv"}:
        _heal_now(state, effect)
    if effect.kind == "heal_over_time":
        _heal_now(state, effect)
    if effect.kind == "hp_cost":
        _pay_hp(state, effect)
    if effect.kind in _BOARD_KINDS:
        _record_board(state, member, effect)
        apply_board_effect(state, effect)
    if effect.kind in _TIMED_KINDS or effect.kind == "heal_over_time":
        _store_timed(state, member, effect)


def _haste_others(state: GameState, source: MemberState, effect: Effect) -> None:
    turns = effect.details.get("turns")
    if not isinstance(turns, int) or turns <= 0:
        state.unknowns.append(
            f"Turn {state.turn}: #{source.monster_id} haste has no turn count."
        )
        return
    for member in state.members:
        if member.index == source.index:
            continue
        if not member.cooldown_exact:
            continue
        if not _apply_charge(member, turns):
            member.cooldown_exact = False
            state.unknowns.append(
                f"Turn {state.turn}: #{member.monster_id} cooldown is unknown, "
                "so haste was not applied."
            )


def _delay_enemies(state: GameState, effect: Effect) -> None:
    turns = effect.details.get("turns")
    if not isinstance(turns, int) or turns <= 0:
        state.unknowns.append(f"Turn {state.turn}: enemy delay has no turn count.")
        return
    through = state.turn + turns - 1
    if through > state.enemy_delayed_through:
        state.enemy_delayed_through = through


def _clear_locks(state: GameState, member: MemberState, effect: Effect) -> None:
    if effect.details.get("full") is True:
        for cell in state.board.cells:
            cell.locked = False
        return
    state.unknowns.append(
        f"Turn {state.turn}: #{member.monster_id} lock clear has no known extent."
    )


def _clear_debuff(state: GameState, member: MemberState, effect: Effect) -> None:
    amount = _clear_amount(effect)
    if amount is None:
        state.unknowns.append(
            f"Turn {state.turn}: #{member.monster_id} {effect.kind} has no known amount."
        )
        return
    if effect.kind == "unmatchable_clear":
        if amount == "full":
            state.board.unmatchable_turns = 0
        else:
            state.board.unmatchable_turns = max(0, state.board.unmatchable_turns - amount)
        return
    for other in state.members:
        if effect.kind == "bind_clear":
            other.bound_turns = 0 if amount == "full" else max(0, other.bound_turns - amount)
        else:
            other.awoken_bound_turns = (
                0 if amount == "full" else max(0, other.awoken_bound_turns - amount)
            )


def _clear_amount(effect: Effect) -> int | Literal["full"] | None:
    if effect.details.get("full") is True:
        return "full"
    turns = effect.details.get("turns")
    if isinstance(turns, int) and turns >= 0:
        return turns
    return None


def _heal_now(state: GameState, effect: Effect) -> None:
    maximum = team_hp_max(state)
    if effect.kind == "heal_flat":
        amount = effect.details.get("hp")
        if not isinstance(amount, int) or state.hp is None or maximum is None:
            state.unknowns.append(f"Turn {state.turn}: flat heal amount is unresolved.")
            return
        state.hp = min(maximum, state.hp + amount)
        return
    if effect.kind == "heal_rcv":
        hundredths = effect.details.get("multiplier_hundredths")
        recovered = effective_rcv(state)
        if (
            not isinstance(hundredths, int)
            or recovered is None
            or state.hp is None
            or maximum is None
        ):
            state.unknowns.append(f"Turn {state.turn}: RCV heal amount is unresolved.")
            return
        state.hp = min(maximum, state.hp + recovered * hundredths // 100)
        return
    percent = effect.details.get("percent")
    if not isinstance(percent, int) or state.hp is None or maximum is None:
        state.unknowns.append(f"Turn {state.turn}: percent heal amount is unresolved.")
        return
    state.hp = min(maximum, state.hp + maximum * percent // 100)


def _pay_hp(state: GameState, effect: Effect) -> None:
    if state.hp is None:
        state.unknowns.append(f"Turn {state.turn}: HP cost is unresolved.")
        return
    if effect.details.get("to_one") is True:
        state.hp = 1 if state.hp > 0 else state.hp
        return
    percent = effect.details.get("remaining_percent")
    if not isinstance(percent, int):
        state.unknowns.append(f"Turn {state.turn}: HP cost is unresolved.")
        return
    state.hp = state.hp * percent // 100


def _record_board(state: GameState, member: MemberState, effect: Effect) -> None:
    count = effect.details.get("count")
    state.board_changes.append(
        BoardChange(
            turn=state.turn,
            member_index=member.index,
            kind=effect.kind,
            summary=effect.summary,
            count=count if isinstance(count, int) else None,
            colors=_detail_strings(effect, _COLOR_KEYS),
            source_colors=_detail_strings(effect, _SOURCE_KEYS),
        )
    )


def _detail_strings(effect: Effect, keys: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for key in keys:
        value = effect.details.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(item) for item in value)
    return found


def _store_timed(state: GameState, member: MemberState, effect: Effect) -> None:
    turns, full, known = _duration(effect)
    if not known and not full:
        state.unknowns.append(
            f"Turn {state.turn}: #{member.monster_id} {effect.kind} duration is unknown."
        )
    percent = effect.details.get("percent")
    multiplier = effect.details.get("multiplier_hundredths")
    attribute = effect.details.get("attribute")
    absorb = effect.details.get("kinds")
    columns = effect.details.get("columns")
    rows = effect.details.get("rows")
    orbs = effect.details.get("orbs")
    rate = effect.details.get("rate")
    mode = effect.details.get("mode")
    state.effects.append(
        TimedEffect(
            effect_id=state.next_effect_id,
            kind=effect.kind,
            summary=effect.summary,
            source_index=member.index,
            source_skill_id=effect.skill_id,
            started_on=state.turn,
            expires_on=None if full or not isinstance(turns, int) else state.turn + turns,
            full=full,
            unknown_duration=not known and not full,
            percent=percent if isinstance(percent, int) else None,
            multiplier_hundredths=multiplier if isinstance(multiplier, int) else None,
            attribute=attribute if isinstance(attribute, str) else None,
            absorb_kinds=[str(item) for item in absorb] if isinstance(absorb, list) else [],
            columns=columns if isinstance(columns, int) else None,
            rows=rows if isinstance(rows, int) else None,
            orbs=[str(item) for item in orbs] if isinstance(orbs, list) else [],
            rate=rate if isinstance(rate, int) else None,
            mode=mode if isinstance(mode, str) else None,
        )
    )
    state.next_effect_id += 1


def _duration(effect: Effect) -> tuple[int | None, bool, bool]:
    if effect.details.get("full") is True and "turns" not in effect.details:
        return None, True, True
    turns = effect.details.get("turns")
    if isinstance(turns, int) and turns > 0:
        return turns, False, True
    start = effect.details.get("start_turn")
    end = effect.details.get("end_turn")
    if isinstance(start, int) and start == end and start > 0:
        return start, False, True
    if isinstance(start, int) and isinstance(end, int):
        return None, False, False
    return None, False, False


def _expire(state: GameState) -> None:
    state.effects = [
        effect
        for effect in state.effects
        if effect.expires_on is None or effect.expires_on > state.turn
    ]


def _regen(state: GameState) -> None:
    for effect in state.effects:
        if effect.kind != "heal_over_time" or effect.unknown_duration:
            continue
        if effect.started_on < state.turn and effect.percent is not None:
            _heal_percent(state, effect.percent)


def _heal_percent(state: GameState, percent: int) -> None:
    maximum = team_hp_max(state)
    if state.hp is None or maximum is None:
        state.unknowns.append(f"Turn {state.turn}: percent heal amount is unresolved.")
        return
    state.hp = min(maximum, state.hp + maximum * percent // 100)


def _adopt_form(state: GameState, member: MemberState, monster_id: int) -> None:
    member.monster_id = monster_id
    card = state._cards.get(monster_id)
    if card is None:
        state.unknowns.append(f"Transformed form #{monster_id} is not in the card index.")
        member.name = f"#{monster_id}"
        member.hp_max = None
        member.atk = None
        member.rcv = None
        member.attributes = []
        member.card_types = []
        member.awakenings = []
        state.hp = None
        _set_clauses(state, member, [], False)
        return
    old_max = member.hp_max
    described = _describe(card, f"#{monster_id}", state.unknowns)
    member.name = str(card.get("name") or f"#{monster_id}")
    member.hp_max = described[0]
    member.atk = described[1]
    member.rcv = described[2]
    member.attributes = described[3]
    member.card_types = described[4]
    awakenings, known = _read_awakenings(card, None, f"#{monster_id}", state.unknowns)
    member.awakenings = awakenings
    if not known:
        member.cooldown_exact = False
    _shift_hp(state, old_max, described[0])
    clauses, clause_known, problem = _clauses(card, state._skills)
    if problem:
        state.unknowns.append(problem)
    _set_clauses(state, member, clauses, clause_known)


def _shift_hp(state: GameState, old: int | None, new: int | None) -> None:
    if state.hp is None or old is None or new is None:
        state.hp = None
        return
    state.hp = max(0, state.hp + (new - old))
    maximum = team_hp_max(state)
    if maximum is None:
        state.hp = None
    else:
        state.hp = min(state.hp, maximum)


def _spec_label(spec: MemberInput, index: int) -> str:
    if spec.role == "leader":
        return "Leader"
    if spec.role == "helper":
        return "Helper"
    return f"Sub {index}"


def _label(member: MemberState) -> str:
    if member.role == "leader":
        return "Leader"
    if member.role == "helper":
        return "Helper"
    return f"Sub {member.index}"
