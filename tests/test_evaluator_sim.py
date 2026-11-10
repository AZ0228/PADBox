"""Scheduler invariants for one supplied team.

Property checks walk seeded battles and read the cast log. Expectations that
name a cooldown come from the skill record or from `cooldown_spec`, which is
the same max-skill minimum the machines use.
"""

import random

import pytest
from conftest import load_regression_cards, regression_skill_index
from pydantic import ValidationError

from padbox.evaluator.sim import (
    ASSUMPTIONS,
    BOARD_COLUMNS,
    BOARD_ROWS,
    MemberInput,
    PolicyTarget,
    apply_bind,
    apply_skill_delay,
    board_is_supported,
    effective_rcv,
    empty_board,
    open_battle,
    shield_percent,
    step,
    team_hp_max,
)
from padbox.evaluator.sim.state import BoardState
from padbox.mechanics.awakenings import NAMES, skill_boost
from padbox.mechanics.decode import decode_skill
from padbox.mechanics.skill_state import cooldown_spec


def _awakening(name: str) -> int:
    return next(awakening_id for awakening_id, label in NAMES.items() if label == name)


BOOST = _awakening("Skill Boost")
ANTI = _awakening("Anti-Skill Boost")
PLUS = _awakening("Skill Boost+")
RESIST = _awakening("Skill Delay Resistance")
CHARGE = _awakening("Skill Charge")


def _raw(type_: int, skill_id: int, params: list[int], initial: int, levels: int) -> dict:
    return {
        "id": skill_id,
        "name": f"skill {skill_id}",
        "description": "this text must not be read",
        "type": type_,
        "params": params,
        "initialCooldown": initial,
        "maxLevel": levels,
    }


def _card(
    monster_id: int,
    active_id: int,
    awakenings: list[int] | None,
    *,
    hp: int = 1000,
    atk: int = 100,
    rcv: int = 50,
    leader_id: int = 0,
) -> dict:
    card = {
        "id": monster_id,
        "name": f"Monster {monster_id}",
        "attrs": [3],
        "types": [1],
        "hp": {"max": hp},
        "atk": {"max": atk},
        "rcv": {"max": rcv},
        "activeSkillId": active_id,
        "leaderSkillId": leader_id,
    }
    if awakenings is not None:
        card["awakenings"] = awakenings
    return card


def _world():
    records = {int(record["monster_id"]): record for record in load_regression_cards()}
    cards = {monster_id: record["card"] for monster_id, record in records.items()}
    return records, regression_skill_index(list(records.values())), cards


def _fixture_skill(record: dict, skill_id: int) -> dict:
    return next(skill for skill in record["skills"] if skill["id"] == skill_id)


def _minimum(skill: dict) -> int:
    value = cooldown_spec(skill).minimum
    assert isinstance(value, int)
    return value


def _fired(state):
    return [cast for cast in state.casts if cast.fired]


def _ready_turn(opening: int, boost: int) -> int:
    return max(1, max(0, opening - boost))


def _open_delay(initial: int, levels: int, awakenings: list[int], *, skill_id: int = 10):
    skill = _raw(18, skill_id, [1], initial, levels)
    card = _card(1, skill_id, awakenings)
    state = open_battle(
        [MemberInput(role="leader", card=card, awakenings=awakenings)],
        {skill_id: skill},
    )
    return state, skill


def _first_fire(state, target: PolicyTarget, limit: int):
    for _ in range(limit):
        step(state, target)
        fired = _fired(state)
        if fired:
            return fired[0]
    return None


def test_opening_charge_and_the_turn_tick():
    assert skill_boost([BOOST]) == 1
    assert skill_boost([PLUS]) == 2
    assert skill_boost([ANTI]) == -1
    assert skill_boost([CHARGE]) == 0
    delay = PolicyTarget(mechanics=["delay"])

    state, skill = _open_delay(1, 1, [])
    fired = _first_fire(state, delay, 3)
    assert fired is not None
    assert fired.turn == _ready_turn(_minimum(skill), 0) == 1
    assert fired.remaining_before == 0
    assert fired.overcharge_before == 0
    assert fired.used_before is False

    state, _skill = _open_delay(5, 1, [BOOST] * 4)
    assert state.opening_skill_boost == 4
    fired = _first_fire(state, delay, 4)
    assert fired is not None and fired.turn == _ready_turn(5, 4) == 1

    state, _skill = _open_delay(5, 1, [BOOST] * 3)
    fired = _first_fire(state, delay, 4)
    assert fired is not None and fired.turn == _ready_turn(5, 3) == 2

    state, _skill = _open_delay(5, 1, [BOOST] * 5)
    fired = _first_fire(state, delay, 2)
    assert fired is not None and fired.turn == 1

    plus = skill_boost([PLUS])
    state, _skill = _open_delay(3, 1, [PLUS])
    fired = _first_fire(state, delay, 3)
    assert fired is not None and fired.turn == _ready_turn(3, plus)

    state, _skill = _open_delay(1, 1, [ANTI])
    assert state.members[0].charges_remaining() == 2
    fired = _first_fire(state, delay, 3)
    assert fired is not None and fired.turn == 2

    state, _skill = _open_delay(5, 1, [BOOST] * 3 + [CHARGE])
    assert any("Skill Charge" in item for item in state.unknowns)
    fired = _first_fire(state, delay, 4)
    assert fired is not None and fired.turn == _ready_turn(5, skill_boost([BOOST] * 3))


def test_missing_awakenings_do_not_count_as_zero_boost():
    skill = _raw(18, 10, [1], 1, 1)
    card = _card(1, 10, None)
    state = open_battle([MemberInput(role="leader", card=card)], {10: skill})
    assert state.skill_boost_known is False
    assert state.opening_skill_boost is None
    assert any("awakenings are missing" in item for item in state.unknowns)
    for _ in range(3):
        step(state, PolicyTarget(mechanics=["delay"]))
    assert _fired(state) == []
    assert state.members[0].active.remaining == _minimum(skill)


def test_unverified_effect_does_not_answer_a_need():
    skill = _raw(999, 10, [1, 2, 3], 0, 0)
    card = _card(1, 10, [])
    state = open_battle([MemberInput(role="leader", card=card, awakenings=[])], {10: skill})
    assert state.members[0].charges_remaining() == 0
    step(state, PolicyTarget(mechanics=["unsupported", "delay"]))
    assert _fired(state) == []
    assert state.members[0].active.state_id == "base"


def test_haste_readies_a_later_slot_and_skips_the_caster():
    leader_skill = _raw(146, 10, [3, 3], 5, 1)
    sub_skill = _raw(18, 11, [1], 8, 1)
    skills = {10: leader_skill, 11: sub_skill}
    state = open_battle(
        [
            MemberInput(role="leader", card=_card(1, 10, [BOOST] * 4), awakenings=[BOOST] * 4),
            MemberInput(role="sub", card=_card(2, 11, []), awakenings=[]),
        ],
        skills,
    )
    step(state, PolicyTarget(mechanics=["charge_other_skills", "delay"]))
    fired = _fired(state)
    assert [cast.member_index for cast in fired] == [0, 1]
    assert all(cast.remaining_before == 0 and cast.used_before is False for cast in fired)
    assert state.members[0].charges_remaining() == _minimum(leader_skill)


def test_transform_haste_on_roze_leaves_the_new_cooldown():
    records, skills, cards = _world()
    roze = records[13966]
    fortress = records[13967]
    base_skill = _fixture_skill(roze, roze["card"]["activeSkillId"])
    fort_skill = _fixture_skill(fortress, fortress["card"]["activeSkillId"])
    base_min = _minimum(base_skill)
    fort_min = _minimum(fort_skill)
    partner_initial = base_min + 6
    partner_skill = _raw(18, 880001, [1], partner_initial, 1)
    skills = dict(skills)
    skills[880001] = partner_skill
    state = open_battle(
        [
            MemberInput(
                role="leader",
                card=roze["card"],
                awakenings=[BOOST] * (base_min - 1),
            ),
            MemberInput(role="sub", card=_card(2, 880001, []), awakenings=[]),
        ],
        skills,
        cards,
    )
    apply_bind(state, 1, 3)
    apply_bind(state, 1, 3, awoken=True)
    state.board.unmatchable_turns = 4
    leader = state.members[0]
    before_hp = state.hp
    before_max = leader.hp_max
    before_clauses = [clause.clause_id for clause in state.leader_clauses]
    step(state)
    haste_turns = next(
        effect.details["turns"]
        for effect in leader.active.machine.states["base"].effects
        if effect.kind == "charge_other_skills"
    )
    fired = _fired(state)
    assert len(fired) == 1
    assert fired[0].turn == 1
    assert fired[0].remaining_before == 0
    assert fired[0].transform_to == fortress["monster_id"]
    assert "transform" in fired[0].effect_kinds
    assert "charge_other_skills" in fired[0].effect_kinds
    assert "void_pierce" not in fired[0].effect_kinds
    assert leader.monster_id == fortress["monster_id"]
    assert leader.name == fortress["card"]["name"]
    assert leader.entered_monster_id == roze["monster_id"]
    assert leader.active.remaining == fort_min
    assert leader.awakenings == fortress["card"]["awakenings"]
    assert leader.hp_max == fortress["card"]["hp"]["max"]
    assert before_hp is not None and before_max is not None and leader.hp_max is not None
    assert state.hp == before_hp + (leader.hp_max - before_max)
    assert [clause.clause_id for clause in state.leader_clauses] != before_clauses
    assert state.leader_clauses_known is True
    partner_left = partner_initial - (base_min - 1) - 1 - haste_turns
    assert state.members[1].charges_remaining() == partner_left
    assert state.members[1].bound_turns == 0
    assert state.members[1].awoken_bound_turns == 0
    assert state.board.unmatchable_turns == 0
    assert not any(change.kind == "board_size" for change in state.board_changes)


def test_saphira_records_orb_quantities_and_clears_locks():
    records, skills, cards = _world()
    saphira = records[7095]
    base_skill = _fixture_skill(saphira, saphira["card"]["activeSkillId"])
    base_min = _minimum(base_skill)
    target = saphira["card"]["henshinTo"][0]
    state = open_battle(
        [
            MemberInput(
                role="leader",
                card=saphira["card"],
                awakenings=[BOOST] * (base_min - 1),
            )
        ],
        skills,
        cards,
    )
    for cell in state.board.cells:
        cell.locked = True
    step(state)
    member = state.members[0]
    created = next(
        effect
        for effect in member.active.machine.states["base"].effects
        if effect.kind == "create_orbs"
    )
    change = next(item for item in state.board_changes if item.kind == "create_orbs")
    assert change.count == created.details["count"]
    assert change.colors == created.details["attributes"]
    assert all(cell.locked is False for cell in state.board.cells)
    assert member.monster_id == target
    transformed = member.active.machine.states[f"transformed:{target}"]
    assert member.active.remaining == transformed.cooldown.minimum
    assert board_is_supported(state) is True
    assert len(state.board.cells) == BOARD_COLUMNS * BOARD_ROWS


def test_gaia_stages_fire_on_separate_turns():
    records, skills, cards = _world()
    gaia = records[8336]
    active = _fixture_skill(gaia, gaia["card"]["activeSkillId"])
    stage_skills = [_fixture_skill(gaia, skill_id) for skill_id in active["params"]]
    opening = _minimum(stage_skills[0])
    assert opening > 1
    state = open_battle(
        [
            MemberInput(
                role="leader",
                card=gaia["card"],
                awakenings=[BOOST] * (opening - 1),
            )
        ],
        skills,
        cards,
    )
    member = state.members[0]
    delay_turns = next(
        effect.details["turns"]
        for effect in member.active.machine.states["stage:0"].effects
        if effect.kind == "delay"
    )
    stage1_min = member.active.machine.states["stage:1"].cooldown.minimum
    assert isinstance(stage1_min, int)
    target = PolicyTarget(mechanics=["delay", "orb_replace", "lock_clear"])
    for _ in range(1 + stage1_min + 2):
        step(state, target)
    fired = _fired(state)
    assert [cast.turn for cast in fired] == [1, 1 + stage1_min]
    assert "delay" in fired[0].effect_kinds
    assert "orb_replace" in fired[1].effect_kinds
    assert member.monster_id == 8336
    assert member.stage_index() == 1
    assert state.enemy_delayed_through == delay_turns
    assert any("no known cooldown" in item for item in state.unknowns)


def test_sub_transform_can_wait_longer_than_the_leader():
    leader_skill = _raw(202, 10, [11], 3, 1)
    sub_skill = _raw(202, 12, [13], 9, 1)
    skills = {10: leader_skill, 12: sub_skill}
    state = open_battle(
        [
            MemberInput(role="leader", card=_card(1, 10, [BOOST] * 2), awakenings=[BOOST] * 2),
            MemberInput(role="sub", card=_card(2, 12, []), awakenings=[]),
        ],
        skills,
    )
    for _ in range(7):
        step(state)
    fired = _fired(state)
    leader = [cast.turn for cast in fired if cast.member_index == 0]
    sub = [cast.turn for cast in fired if cast.member_index == 1]
    assert leader == [_ready_turn(3, 2)]
    assert sub == [_ready_turn(9, 2)]
    assert all(cast.evolution_met and cast.remaining_before == 0 for cast in fired)


def test_same_turn_blocks_the_active_a_transform_just_entered():
    base = _raw(202, 10, [2], 1, 2)
    delay = _raw(18, 11, [1], 1, 2)
    skills = {10: base, 11: delay}
    cards = {2: _card(2, 11, [])}
    state = open_battle(
        [MemberInput(role="leader", card=_card(1, 10, []), awakenings=[])],
        skills,
        cards,
    )
    target = PolicyTarget(mechanics=["delay"])
    first = step(state, target)
    fired = [cast for cast in first if cast.fired]
    assert len(fired) == 1
    assert fired[0].transform_to == 2
    assert any(cast.reason == "already_used" and cast.fired is False for cast in first)
    second = step(state, target)
    fired = [cast for cast in second if cast.fired]
    assert len(fired) == 1
    assert fired[0].transform_to is None
    assert "delay" in fired[0].effect_kinds
    assert state.members[0].monster_id == 2


def test_assist_overcharge_waits_for_the_host_then_the_assist():
    records, skills, cards = _world()
    roze = records[13966]
    leona = records[4834]
    host_min = _minimum(_fixture_skill(roze, roze["card"]["activeSkillId"]))
    assist_min = _minimum(_fixture_skill(leona, leona["card"]["activeSkillId"]))
    state = open_battle(
        [
            MemberInput(
                role="leader",
                card=roze["card"],
                assist=leona["card"],
                awakenings=[],
                assist_awakenings=[],
            )
        ],
        skills,
        cards,
    )
    member = state.members[0]
    assert member.charges_remaining() == host_min + assist_min
    assert member.active.state().kind == "inherited"
    assert "base" in member.active.machine.states
    target = PolicyTarget(recovery=True)
    for _ in range(host_min + assist_min):
        step(state, target)
    fired = _fired(state)
    assert [cast.turn for cast in fired] == [host_min + assist_min]
    assert fired[0].state_kind == "inherited"
    assert fired[0].transform_to is None
    assert fired[0].remaining_before == 0
    assert member.monster_id == roze["monster_id"]

    charged = open_battle(
        [
            MemberInput(
                role="leader",
                card=roze["card"],
                assist=leona["card"],
                awakenings=[BOOST] * (host_min + assist_min - 1),
                assist_awakenings=[],
            )
        ],
        skills,
        cards,
    )
    step(charged, target)
    charged_fires = _fired(charged)
    assert [cast.turn for cast in charged_fires] == [1]
    assert charged_fires[0].state_kind == "inherited"


def test_zero_cooldown_assist_still_waits_for_the_host():
    host_skill = _raw(18, 10, [1], 4, 1)
    assist_skill = _raw(18, 11, [1], 1, 2)
    skills = {10: host_skill, 11: assist_skill}
    state = open_battle(
        [
            MemberInput(
                role="leader",
                card=_card(1, 10, []),
                assist=_card(5, 11, []),
                awakenings=[],
                assist_awakenings=[],
            )
        ],
        skills,
    )
    assert _minimum(host_skill) == 4
    assert _minimum(assist_skill) == 0
    assert state.members[0].overcharge_remaining == 4
    assert state.members[0].active.remaining == 0
    for _ in range(4):
        step(state, PolicyTarget(mechanics=["delay"]))
    fired = _fired(state)
    assert [cast.turn for cast in fired] == [4]
    assert all(cast.state_kind == "inherited" for cast in state.casts if cast.fired)
    assert not any(cast.fired and cast.state_kind == "base" for cast in state.casts)


def test_bind_blocks_the_active_and_awoken_bind_does_not():
    skill = _raw(18, 10, [1], 0, 0)
    card = _card(1, 10, [])
    skills = {10: skill}
    state = open_battle(
        [MemberInput(role="leader", card=card, awakenings=[])],
        skills,
    )
    apply_bind(state, 0, 1)
    target = PolicyTarget(mechanics=["delay"])
    first = step(state, target)
    assert not any(cast.fired for cast in first)
    assert any(cast.reason == "bound" for cast in first)
    assert state.members[0].bound_turns == 0
    second = step(state, target)
    assert any(cast.fired for cast in second)

    awoken = open_battle(
        [MemberInput(role="leader", card=card, awakenings=[])],
        skills,
    )
    apply_bind(awoken, 0, 2, awoken=True)
    step(awoken, target)
    assert _fired(awoken)
    assert awoken.members[0].awoken_bound_turns == 1


def test_skill_delay_and_unknown_resistance():
    plain = _raw(18, 10, [1], 1, 1)
    resisted = _raw(18, 11, [1], 1, 1)
    skills = {10: plain, 11: resisted}
    state = open_battle(
        [
            MemberInput(role="leader", card=_card(1, 10, []), awakenings=[]),
            MemberInput(role="sub", card=_card(2, 11, [RESIST]), awakenings=[RESIST]),
        ],
        skills,
    )
    apply_skill_delay(state, 3)
    assert state.members[0].charges_remaining() == 4
    assert state.members[1].charges_remaining() == 1
    assert state.members[1].cooldown_exact is False
    assert any(event.reason == "unknown_resistance" for event in state.skill_delays)
    assert any("Skill Delay Resistance" in item for item in state.unknowns)
    for _ in range(4):
        step(state, PolicyTarget(mechanics=["delay"]))
    fired = _fired(state)
    assert [(cast.member_index, cast.turn) for cast in fired] == [(0, 4)]

    ready = _raw(18, 12, [1], 1, 2)
    held = open_battle(
        [MemberInput(role="leader", card=_card(3, 12, []), awakenings=[])],
        {12: ready},
    )
    apply_skill_delay(held, 2)
    assert held.members[0].charges_remaining() == 2
    step(held, PolicyTarget(mechanics=["delay"]))
    assert _fired(held) == []
    step(held, PolicyTarget(mechanics=["delay"]))
    assert [cast.turn for cast in _fired(held)] == [2]


def test_durations_shields_recovery_and_rcv():
    shield = _raw(3, 10, [2, 40], 0, 0)
    heal = _raw(179, 11, [2, 0, 10], 0, 0)
    rcv = _raw(50, 12, [2, 5, 200], 0, 0)
    # Minimum 8 and 7 boosts: ready on turn 1, then quiet for the duration window.
    parent = _raw(116, 13, [10, 11, 12], 8, 1)
    short = _raw(3, 14, [1, 70], 8, 1)
    skills = {10: shield, 11: heal, 12: rcv, 13: parent, 14: short}
    boosts = [BOOST] * 7
    state = open_battle(
        [
            MemberInput(
                role="leader",
                card=_card(1, 13, boosts, hp=1000, rcv=80),
                awakenings=boosts,
            ),
            MemberInput(role="sub", card=_card(2, 14, [], hp=1000, rcv=20), awakenings=[]),
        ],
        skills,
    )
    effects = decode_skill(parent, skills)
    long_shield = next(effect for effect in effects if effect.kind == "damage_shield")
    heal_effect = next(effect for effect in effects if effect.kind == "heal_over_time")
    rcv_effect = next(effect for effect in effects if effect.kind == "rcv_buff")
    short_shield = decode_skill(short, skills)[0]
    long_percent = long_shield.details["percent"]
    short_percent = short_shield.details["percent"]
    gain = team_hp_max(state) * heal_effect.details["percent"] // 100
    base_rcv = 100
    buffed = base_rcv * rcv_effect.details["multiplier_hundredths"] // 100
    state.hp = 200
    target = PolicyTarget(mechanics=["damage_shield", "heal_over_time", "rcv_buff"])
    step(state, target)
    assert shield_percent(state) == max(long_percent, short_percent)
    assert state.hp == 200 + gain
    assert effective_rcv(state) == buffed
    step(state, target)
    assert shield_percent(state) == long_percent
    assert state.hp == 200 + 2 * gain
    assert effective_rcv(state) == buffed
    step(state, target)
    assert shield_percent(state) == 0
    assert state.hp == 200 + 2 * gain
    assert effective_rcv(state) == base_rcv


def test_hp_cost_sets_remaining_hp():
    skill = _raw(195, 10, [], 0, 0)
    state = open_battle(
        [MemberInput(role="leader", card=_card(1, 10, [], hp=500), awakenings=[])],
        {10: skill},
    )
    assert state.hp == 500
    step(state, PolicyTarget(mechanics=["hp_cost"]))
    assert state.hp == 1


def test_other_board_sizes_are_unsupported():
    wide = empty_board(7, 6)
    assert wide.is_standard() is False
    assert len(wide.cells) == 42
    assert empty_board().is_standard() is True
    with pytest.raises(ValidationError):
        BoardState(columns=BOARD_COLUMNS, rows=BOARD_ROWS, cells=[])

    odd = _raw(244, 10, [2, 1], 5, 1)
    standard = _raw(244, 11, [2, 3], 5, 1)
    boosts = [BOOST] * 4
    odd_state = open_battle(
        [MemberInput(role="leader", card=_card(1, 10, boosts), awakenings=boosts)],
        {10: odd},
    )
    step(odd_state, PolicyTarget(mechanics=["board_size"]))
    stored = next(effect for effect in odd_state.effects if effect.kind == "board_size")
    decoded = decode_skill(odd, {10: odd})[0]
    assert (stored.columns, stored.rows) == (decoded.details["columns"], decoded.details["rows"])
    assert (stored.columns, stored.rows) != (BOARD_COLUMNS, BOARD_ROWS)
    assert board_is_supported(odd_state) is False
    step(odd_state, PolicyTarget(mechanics=["board_size"]))
    step(odd_state, PolicyTarget(mechanics=["board_size"]))
    assert odd_state.effects == []
    assert board_is_supported(odd_state) is True

    even_state = open_battle(
        [MemberInput(role="leader", card=_card(2, 11, boosts), awakenings=boosts)],
        {11: standard},
    )
    step(even_state, PolicyTarget(mechanics=["board_size"]))
    assert board_is_supported(even_state) is True


def test_slot_order_team_shape_and_reproducible_casts():
    def member(role: str, monster_id: int) -> tuple[MemberInput, dict]:
        skill_id = 1000 + monster_id
        skill = _raw(18, skill_id, [1], 0, 0)
        card = _card(monster_id, skill_id, [])
        return MemberInput(role=role, card=card, awakenings=[]), skill

    leader, leader_skill = member("leader", 1)
    subs = [member("sub", index) for index in range(2, 6)]
    helper, helper_skill = member("helper", 6)
    skills = {leader_skill["id"]: leader_skill, helper_skill["id"]: helper_skill}
    for _spec, skill in subs:
        skills[skill["id"]] = skill
    ordered = [helper, *[spec for spec, _skill in subs], leader]
    state = open_battle(ordered, skills, seed=4)
    assert [item.role for item in state.members] == [
        "leader",
        "sub",
        "sub",
        "sub",
        "sub",
        "helper",
    ]
    assert state.team_complete is True
    assert state.sub_count == 4
    assert state.helper_count == 1
    assert state.seed == 4
    assert state.assumptions == list(ASSUMPTIONS)
    assert state.leader_verdicts is None
    target = PolicyTarget(mechanics=["delay"])
    step(state, target)
    assert [cast.member_index for cast in _fired(state)] == [0, 1, 2, 3, 4, 5]

    again = open_battle(ordered, skills, seed=4)
    step(again, target)
    first_casts = [cast.model_dump() for cast in state.casts]
    assert first_casts == [cast.model_dump() for cast in again.casts]

    extra = [member("sub", 7)[0]]
    extra_skill = member("sub", 7)[1]
    skills[extra_skill["id"]] = extra_skill
    wide = open_battle([leader, *[spec for spec, _skill in subs], extra[0]], skills)
    assert wide.sub_count == 5
    assert wide.helper_count == 0
    assert wide.team_complete is False
    assert not any(item.role == "helper" for item in wide.members)


def test_evolution_blocks_a_ready_transform():
    skill = _raw(202, 10, [2], 1, 1)
    state = open_battle(
        [
            MemberInput(
                role="leader",
                card=_card(1, 10, []),
                awakenings=[],
                evolution_met=False,
            )
        ],
        {10: skill},
    )
    for _ in range(4):
        step(state)
    assert _fired(state) == []
    assert state.members[0].active.state_id == "base"
    assert state.members[0].monster_id == 1
    assert any(cast.reason == "evolution" for cast in state.casts)


def _assert_ready(cast) -> None:
    assert cast.fired is False or (
        cast.remaining_before == 0 and cast.overcharge_before == 0 and cast.used_before is False
    )


def test_no_active_fires_before_it_is_ready():
    rng = random.Random(0)
    delay = PolicyTarget(mechanics=["delay", "charge_other_skills"])
    for _ in range(30):
        cooldown = rng.randint(1, 8)
        boost = rng.randint(0, 10)
        skill = _raw(18, 10, [1], cooldown, 1)
        state = open_battle(
            [
                MemberInput(
                    role="leader",
                    card=_card(1, 10, [BOOST] * boost),
                    awakenings=[BOOST] * boost,
                )
            ],
            {10: skill},
        )
        opening = state.members[0].charges_remaining()
        assert opening is not None
        for _turn in range(cooldown + 2):
            step(state, delay)
        fired = _fired(state)
        assert fired
        assert fired[0].turn == _ready_turn(opening, 0)
        for cast in state.casts:
            _assert_ready(cast)

    for _ in range(20):
        host_cd = rng.randint(1, 6)
        assist_cd = rng.randint(0, 4)
        boost = rng.randint(0, 8)
        host_skill = _raw(18, 10, [1], host_cd, 1)
        if assist_cd == 0:
            assist_skill = _raw(18, 11, [1], 1, 2)
        else:
            assist_skill = _raw(18, 11, [1], assist_cd, 1)
        skills = {10: host_skill, 11: assist_skill}
        state = open_battle(
            [
                MemberInput(
                    role="leader",
                    card=_card(1, 10, [BOOST] * boost),
                    assist=_card(5, 11, []),
                    awakenings=[BOOST] * boost,
                    assist_awakenings=[],
                )
            ],
            skills,
        )
        opening = state.members[0].charges_remaining()
        assert opening is not None
        for _turn in range(host_cd + assist_cd + 2):
            step(state, delay)
        fired = _fired(state)
        assert fired
        assert fired[0].turn == max(1, opening)
        assert all(cast.state_kind == "inherited" for cast in fired)
        assert not any(cast.fired and cast.state_kind == "base" for cast in state.casts)
        for cast in state.casts:
            _assert_ready(cast)


def test_no_transform_before_its_prerequisites():
    rng = random.Random(1)
    for trial in range(30):
        cooldown = rng.randint(1, 8)
        boost = rng.randint(0, 10)
        evolution = trial % 2 == 0
        haste = trial % 3 == 0
        transform = _raw(202, 10, [50], cooldown, 1)
        skills = {10: transform}
        members = [
            MemberInput(
                role="sub" if haste else "leader",
                card=_card(1, 10, [BOOST] * boost),
                awakenings=[BOOST] * boost,
                evolution_met=evolution,
            )
        ]
        if haste:
            amount = rng.randint(1, 4)
            skills[11] = _raw(146, 11, [amount, amount], 1, 2)
            members.insert(
                0,
                MemberInput(role="leader", card=_card(2, 11, []), awakenings=[]),
            )
        state = open_battle(members, skills)
        transform_index = 1 if haste else 0
        opening = state.members[transform_index].charges_remaining()
        assert opening is not None
        for _turn in range(cooldown + 2):
            step(state, PolicyTarget(mechanics=["charge_other_skills"]))
        transforms = [cast for cast in _fired(state) if cast.transform_to is not None]
        for cast in state.casts:
            _assert_ready(cast)
            if cast.fired and cast.transform_to is not None:
                assert cast.evolution_met is True
        if not evolution:
            assert transforms == []
            member = state.members[transform_index]
            assert member.active.state_id == "base"
            assert member.monster_id == 1
        elif not haste:
            assert transforms
            assert transforms[0].turn == max(1, opening)
            assert transforms[0].remaining_before == 0
