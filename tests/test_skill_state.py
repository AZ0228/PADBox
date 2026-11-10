"""Skill state machines for the pinned-snapshot regression cards.

Expectations come from the fixture skill records and from the decoded
transform and evolving effects. Description text is not an input.
"""

from conftest import load_regression_cards, regression_skill_index

from padbox.mechanics.skill_state import (
    ActiveSlot,
    actives_this_turn,
    machine_for,
    with_inherited,
)


def _world():
    records_list = load_regression_cards()
    records = {int(record["monster_id"]): record for record in records_list}
    cards = {monster_id: record["card"] for monster_id, record in records.items()}
    return records, regression_skill_index(records_list), cards


def _skill(record: dict, skill_id: int) -> dict:
    return next(skill for skill in record["skills"] if skill["id"] == skill_id)


def _minimum(skill: dict) -> int:
    initial = skill["initialCooldown"]
    levels = int(skill["maxLevel"])
    assert isinstance(initial, int) and initial > 0
    return max(0, initial - max(0, levels - 1))


def _charge(slot: ActiveSlot) -> int:
    ticks = 0
    while slot.status() == "charging":
        slot.tick()
        ticks += 1
        if ticks > 100:
            raise AssertionError("cooldown did not reach ready")
    return ticks


def _kinds(slot_or_effects) -> set[str]:
    effects = slot_or_effects if isinstance(slot_or_effects, list) else slot_or_effects.effects
    return {effect.kind for effect in effects}


def _raw_skill(type_: int, skill_id: int, params: list[int], initial: int, levels: int) -> dict:
    return {
        "id": skill_id,
        "name": f"skill {skill_id}",
        "description": "this text must not be read",
        "type": type_,
        "params": params,
        "initialCooldown": initial,
        "maxLevel": levels,
    }


def test_roze_transform_is_one_active_and_waits_for_its_cooldown():
    records, skills, cards = _world()
    base_card = records[13966]["card"]
    machine = machine_for(base_card, skills, cards)
    base_skill = _skill(records[13966], base_card["activeSkillId"])
    target = base_card["henshinTo"][0]
    assert target == records[13967]["monster_id"]
    transformed = machine.states[f"transformed:{target}"]
    transformed_skill = _skill(records[13967], records[13967]["card"]["activeSkillId"])

    assert machine.sequence is None
    assert machine.order == ["base", f"transformed:{target}"]
    assert machine.states["base"].skill_id == base_skill["id"]
    assert machine.states["base"].cooldown.base == base_skill["initialCooldown"] == 28
    assert machine.states["base"].cooldown.minimum == _minimum(base_skill) == 24
    assert transformed.monster_id == target
    assert transformed.skill_id == transformed_skill["id"]
    assert transformed.name == "Fortress Mode"
    assert transformed.cooldown.base == transformed_skill["initialCooldown"] == 4
    assert transformed.cooldown.minimum == _minimum(transformed_skill) == 4

    slot = ActiveSlot.start(machine)
    assert slot.assumption == "max_skill"
    assert slot.entry_charge == "state_minimum"
    assert slot.use().continuation == "not_ready"
    assert slot.state_id == "base"
    assert actives_this_turn(slot) == []
    assert _charge(slot) == 24
    assert [item.kind for item in actives_this_turn(slot)] == ["base"]

    cast = slot.use()
    assert cast.used and cast.continuation == "transition"
    assert "transform" in _kinds(cast.effects)
    assert len(cast.effects) > 1
    assert "void_pierce" not in _kinds(cast.effects)
    assert slot.state().kind == "transformed"
    assert slot.state().skill_id == transformed.skill_id
    assert slot.remaining == 4
    assert actives_this_turn(slot) == []
    assert slot.use().continuation == "already_used"

    assert _charge(slot) == 4
    assert [item.kind for item in actives_this_turn(slot)] == ["transformed"]
    cycled = slot.use()
    assert cycled.continuation == "cycle"
    assert slot.state_id == transformed.state_id
    assert "void_pierce" in _kinds(cycled.effects)
    assert "transform" not in _kinds(cycled.effects)
    assert "linked_orb_leader" not in _kinds(cycled.effects)


def test_saphira_entry_transform_replaces_the_base_active():
    records, skills, cards = _world()
    base_card = records[7095]["card"]
    machine = machine_for(base_card, skills, cards)
    base_skill = _skill(records[7095], base_card["activeSkillId"])
    target = base_card["henshinTo"][0]
    assert target == records[7096]["monster_id"]
    transformed_skill = _skill(records[7096], records[7096]["card"]["activeSkillId"])
    transformed = machine.states[f"transformed:{target}"]

    assert machine.states["base"].cooldown.base == base_skill["initialCooldown"] == 28
    assert machine.states["base"].cooldown.minimum == _minimum(base_skill) == 28
    assert transformed.skill_id == transformed_skill["id"]
    assert transformed.name == "Bright Solution"
    assert transformed.cooldown.base == transformed_skill["initialCooldown"] == 2
    assert transformed.cooldown.minimum == _minimum(transformed_skill) == 2
    assert "transform" not in _kinds(transformed)

    slot = ActiveSlot.start(machine)
    assert _charge(slot) == 28
    cast = slot.use()
    assert cast.continuation == "transition"
    assert {"transform", "create_orbs"} <= _kinds(cast.effects)
    assert slot.state().skill_id == transformed.skill_id
    assert actives_this_turn(slot) == []
    assert _charge(slot) == 2
    assert [item.skill_id for item in actives_this_turn(slot)] == [transformed.skill_id]
    assert "transform" not in _kinds(slot.use().effects)


def test_missing_transform_card_does_not_invent_the_active():
    records, skills, _cards = _world()
    card = records[13966]["card"]
    machine = machine_for(card, skills, {})
    target = card["henshinTo"][0]
    state_id = f"transformed:{target}"
    assert machine.states[state_id].monster_id == target
    assert machine.states[state_id].cooldown.known is False
    assert machine.states[state_id].effects == []

    slot = ActiveSlot.start(machine)
    _charge(slot)
    cast = slot.use()
    assert cast.used and "transform" in _kinds(cast.effects)
    assert slot.state_id == state_id
    assert slot.remaining is None
    assert slot.usable() is None
    slot.tick()
    assert slot.use().continuation == "unknown"


def test_gaia_stages_stay_separate_and_the_sequence_is_unknown():
    records, skills, cards = _world()
    card = records[8336]["card"]
    active = _skill(records[8336], card["activeSkillId"])
    machine = machine_for(card, skills, cards)
    stage_skills = [_skill(records[8336], skill_id) for skill_id in active["params"]]

    assert active["type"] == 232
    assert active["params"] == [26757, 26760]
    assert machine.skill_id == active["id"] == 26756
    assert machine.sequence == "unknown"
    assert [machine.states[state_id].skill_id for state_id in machine.order] == active["params"]
    assert machine.skill_id not in {state.skill_id for state in machine.states.values()}
    assert {state.monster_id for state in machine.states.values()} == {8336}
    assert [item.target for item in machine.transitions] == ["stage:1", None]
    assert machine.transitions[-1].known is False

    first, second = (machine.states[state_id] for state_id in machine.order)
    assert first.cooldown.base == stage_skills[0]["initialCooldown"] == 15
    assert first.cooldown.minimum == _minimum(stage_skills[0]) == 10
    assert _kinds(first) == {"delay"}
    assert second.name == "Gaia Slash"
    assert second.cooldown.base == stage_skills[1]["initialCooldown"] == 2
    assert second.cooldown.minimum == _minimum(stage_skills[1]) == 2
    assert "delay" not in _kinds(second)
    assert {"lock_clear", "orb_replace"} <= _kinds(second)

    rewritten = {
        skill_id: dict(skill, description="rewritten") for skill_id, skill in skills.items()
    }
    assert machine_for(card, rewritten, cards).model_dump() == machine.model_dump()

    slot = ActiveSlot.start(machine)
    assert slot.use().used is False
    assert slot.state_id == "stage:0"
    assert _charge(slot) == 10
    opening = slot.use()
    assert opening.continuation == "transition"
    assert _kinds(opening.effects) == {"delay"}
    assert slot.state_id == "stage:1"
    assert slot.remaining == 2
    assert actives_this_turn(slot) == []

    assert _charge(slot) == 2
    assert [item.skill_id for item in actives_this_turn(slot)] == [26760]
    closing = slot.use()
    assert closing.continuation == "unknown"
    assert "delay" not in _kinds(closing.effects)
    assert "orb_replace" in _kinds(closing.effects)
    assert slot.state_id == "stage:1"
    assert slot.remaining is None
    slot.tick()
    assert slot.use().used is False
    assert actives_this_turn(slot) == []


def test_evolving_sequence_can_loop_or_end_when_that_is_supplied():
    root = _raw_skill(232, 1, [2, 3], 1, 1)
    first = _raw_skill(18, 2, [1], 2, 2)
    second = _raw_skill(18, 3, [1], 2, 2)
    skills = {1: root, 2: first, 3: second}
    card = {"id": 9, "activeSkillId": 1}

    looping = machine_for(card, skills, sequence="loops")
    assert looping.sequence == "loops"
    assert looping.transitions[-1].target == "stage:0"
    loop_slot = ActiveSlot.start(looping)
    assert _charge(loop_slot) == 1
    assert loop_slot.use().continuation == "transition"
    assert loop_slot.state_id == "stage:1"
    assert _charge(loop_slot) == 1
    assert loop_slot.use().continuation == "transition"
    assert loop_slot.state_id == "stage:0"
    assert loop_slot.remaining == 1

    ending = machine_for(card, skills, sequence="ends")
    assert ending.sequence == "ends"
    assert [item.target for item in ending.transitions] == ["stage:1"]
    end_slot = ActiveSlot.start(ending)
    _charge(end_slot)
    end_slot.use()
    _charge(end_slot)
    assert end_slot.use().continuation == "cycle"
    assert end_slot.state_id == "stage:1"
    assert end_slot.remaining == 1

    unknown = machine_for(card, skills)
    assert unknown.sequence == "unknown"
    assert unknown.transitions[-1].known is False
    unknown_slot = ActiveSlot.start(unknown)
    _charge(unknown_slot)
    unknown_slot.use()
    _charge(unknown_slot)
    assert unknown_slot.use().continuation == "unknown"
    assert unknown_slot.remaining is None
    assert unknown_slot.state_id == "stage:1"


def test_same_turn_blocks_a_second_active_even_when_the_next_minimum_is_zero():
    root = _raw_skill(232, 1, [2, 3], 1, 1)
    # initial 1 and max level 2 -> minimum 0, so the next state would be ready.
    first = _raw_skill(18, 2, [1], 1, 2)
    second = _raw_skill(18, 3, [1], 1, 2)
    skills = {1: root, 2: first, 3: second}
    slot = ActiveSlot.start(machine_for({"id": 9, "activeSkillId": 1}, skills, sequence="loops"))
    assert slot.remaining == 0
    assert slot.use().continuation == "transition"
    assert slot.state_id == "stage:1"
    assert slot.remaining == 0
    assert slot.usable() is None
    assert actives_this_turn(slot) == []
    slot.tick()
    assert [item.state_id for item in actives_this_turn(slot)] == ["stage:1"]


def test_a_missing_evolving_stage_is_not_skipped():
    root = _raw_skill(232, 1, [2, 3], 1, 1)
    first = _raw_skill(18, 2, [1], 1, 1)
    skills = {1: root, 2: first}
    machine = machine_for({"id": 9, "activeSkillId": 1}, skills)
    assert [machine.states[state_id].skill_id for state_id in machine.order] == [2, 3]
    assert machine.states["stage:1"].cooldown.known is False
    slot = ActiveSlot.start(machine)
    _charge(slot)
    assert slot.use().continuation == "transition"
    assert slot.state_id == "stage:1"
    assert slot.remaining is None
    assert slot.usable() is None


def test_inherited_active_replaces_the_base_active():
    records, skills, cards = _world()
    host_card = records[13966]["card"]
    host = ActiveSlot.start(machine_for(host_card, skills, cards))
    assist = records[4834]
    assert assist["card"]["canAssist"] is True
    assist_skill = _skill(assist, assist["card"]["activeSkillId"])
    inherited = with_inherited(host, assist_skill, skills, assist["monster_id"])

    assert host.state().kind == "base"
    assert all(state.kind != "inherited" for state in host.machine.states.values())
    assert "base" in inherited.machine.states
    assert inherited.state().kind == "inherited"
    assert inherited.state().skill_id == assist_skill["id"]
    assert inherited.state().cooldown.base == assist_skill["initialCooldown"] == 13
    assert inherited.state().cooldown.minimum == _minimum(assist_skill) == 5
    assert inherited.machine.order == host.machine.order
    assert not any(item.startswith("inherited:") for item in inherited.machine.order)
    assert not any(item.source.startswith("inherited:") for item in inherited.machine.transitions)

    assert _charge(inherited) == 5
    assert [item.kind for item in actives_this_turn(inherited)] == ["inherited"]
    assert actives_this_turn(inherited)[0].skill_id == assist_skill["id"]
    assert actives_this_turn(inherited)[0].skill_id != host.state().skill_id
    cast = inherited.use()
    assert cast.continuation == "cycle"
    assert inherited.state().kind == "inherited"
    assert "transform" not in _kinds(cast.effects)
