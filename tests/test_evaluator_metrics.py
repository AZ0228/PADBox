"""Scenario and Monte Carlo reports.

Rates are means across simulations. A metric the battle cannot measure is
absent, and every reported miss names its reason. Two generator rotations
must separate activation and repeat exactly.
"""

import inspect

import pytest

from padbox.evaluator.sim import (
    ASSUMPTIONS,
    DEFAULT_SEED,
    DEFAULT_SIMULATIONS,
    DEFAULT_TURNS,
    MemberInput,
    PolicyTarget,
    apply_bind,
    apply_skill_delay,
    open_battle,
    report_battles,
    run_monte_carlo,
    run_scenario,
    step,
)
from padbox.mechanics.awakenings import NAMES
from padbox.mechanics.decode import decode_skill
from padbox.mechanics.skill_state import cooldown_spec

CELLS = 30


def _awakening(name: str) -> int:
    return next(awakening_id for awakening_id, label in NAMES.items() if label == name)


BOOST = _awakening("Skill Boost")
RESIST = _awakening("Skill Delay Resistance")


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


def _card(monster_id: int, active_id: int, leader_id: int = 0) -> dict:
    return {
        "id": monster_id,
        "name": f"Monster {monster_id}",
        "attrs": [3],
        "types": [1],
        "hp": {"max": 1000},
        "atk": {"max": 100},
        "rcv": {"max": 50},
        "activeSkillId": active_id,
        "leaderSkillId": leader_id,
        "awakenings": [],
    }


def _solid(color: str) -> list[str]:
    return [color] * CELLS


def _checker() -> list[str]:
    colors: list[str] = []
    for row in range(5):
        for column in range(6):
            colors.append("fire" if (row + column) % 2 == 0 else "water")
    return colors


def _hearts() -> list[str]:
    board = _checker()
    for column in range(3):
        board[4 * 6 + column] = "heal"
    return board


def _metric(report, name: str):
    return next(item for item in report.metrics if item.name == name)


def _value(report, name: str) -> float:
    value = _metric(report, name).value
    assert value is not None
    return value


def _names(report) -> set[str]:
    return {item.name for item in report.metrics}


def _codes(metric) -> list[str]:
    return [item.code for item in metric.failures]


def _linked_skills() -> dict[int, dict]:
    active = _raw(18, 1, [1], 99, 1)
    linked = _raw(182, 2, [8, 5, 200, 50], 0, 0)
    leader = _raw(138, 3, [2], 0, 0)
    skills = {skill["id"]: skill for skill in (active, linked, leader)}
    effects = decode_skill(leader, skills)
    assert effects[0].kind == "linked_orb_leader"
    assert effects[0].details["attributes"] == ["light"]
    assert effects[0].details["minimum_linked"] == 5
    assert effects[0].details["atk_multiplier_hundredths"] == 200
    assert effects[0].details["shield_percent"] == 50
    return skills


def _linked_members() -> list[MemberInput]:
    card = _card(10, 1, 3)
    return [MemberInput(role="leader", card=card, awakenings=[])]


def _generator(initial: int) -> tuple[list[MemberInput], dict[int, dict]]:
    skills = _linked_skills()
    generated = _raw(141, 4, [30, 8, 8], initial, 1)
    skills[generated["id"]] = generated
    effect = decode_skill(generated, skills)[0]
    assert effect.kind == "create_orbs"
    assert effect.details["count"] == 30
    assert effect.details["attributes"] == ["light"]
    members = [
        *_linked_members(),
        MemberInput(role="sub", card=_card(11, 4), awakenings=[]),
    ]
    return members, skills


def test_monte_carlo_defaults_are_the_decision_values():
    signature = inspect.signature(run_monte_carlo)
    assert signature.parameters["turns"].default == DEFAULT_TURNS == 10
    assert signature.parameters["simulations"].default == DEFAULT_SIMULATIONS == 1000
    assert signature.parameters["seed"].default == DEFAULT_SEED == 0


def test_scenario_records_the_run_and_omits_a_single_interval():
    skills = _linked_skills()
    report = run_scenario(_linked_members(), skills, [_solid("light")], seed=7)
    assert report.mode == "scenario"
    assert report.simulations == 1
    assert report.turns == 1
    assert report.seed == 7
    assert report.seeds == [7]
    assert report.basis == "policy_approximation"
    assert report.assumptions[: len(ASSUMPTIONS)] == list(ASSUMPTIONS)
    assert any("supplied board" in item for item in report.assumptions)
    assert any("Recovery sufficiency is omitted" in item for item in report.assumptions)
    assert "recovery_sufficiency" not in _names(report)
    activation = _metric(report, "leader_activation")
    assert activation.value == 1
    assert activation.successes == 1
    assert activation.trials == 1
    assert activation.interval is None
    assert activation.failures == []
    assert _value(report, "offensive_activation") == 1
    assert _value(report, "defensive_activation") == 1
    assert "turns_to_transform" not in _names(report)


def test_fixed_sequence_gap_and_unmet_reasons():
    report = run_scenario(
        _linked_members(),
        _linked_skills(),
        [_checker(), _checker(), _solid("light")],
    )
    activation = _metric(report, "leader_activation")
    assert activation.value == pytest.approx(1 / 3)
    assert activation.successes == 1
    assert activation.trials == 3
    assert any(code.startswith("unmet:") for code in _codes(activation))
    assert _codes(activation) == sorted(_codes(activation))
    gap = _metric(report, "longest_activation_gap")
    assert gap.value == 2
    assert gap.interval is None
    assert gap.failures == activation.failures


def test_scenario_board_replaces_locks_before_the_match():
    state = open_battle(_linked_members(), _linked_skills(), seed=0)
    state.board.cells[0].locked = True
    state.board.cells[0].color = "dark"
    step(state, board=_solid("light"))
    assert state.turn_reports[0].matches[0].size == CELLS
    assert state.turn_reports[0].matches[0].color == "light"


def test_healing_counts_kept_hearts_and_a_board_with_none():
    kept = run_scenario(_linked_members(), _linked_skills(), [_hearts()])
    assert _value(kept, "healing_orb_availability") == 1
    assert _metric(kept, "healing_orb_availability").failures == []
    empty = run_scenario(_linked_members(), _linked_skills(), [_solid("fire")])
    healing = _metric(empty, "healing_orb_availability")
    assert healing.value == 0
    assert _codes(healing) == ["no_heal_orbs"]


def test_shield_uptime_and_absorb_availability_use_decoded_durations():
    shield = _raw(3, 1, [3, 40], 5, 1)
    shield_effect = decode_skill(shield, {1: shield})[0]
    assert shield_effect.kind == "damage_shield"
    assert shield_effect.details == {"turns": 3, "percent": 40}
    shielded = run_scenario(
        [MemberInput(role="leader", card=_card(1, 1), awakenings=[BOOST] * 4)],
        {1: shield},
        [_solid("wood")] * 4,
        target=PolicyTarget(mechanics=["damage_shield"]),
    )
    assert _value(shielded, "active_effect_uptime") == pytest.approx(0.75)
    assert _codes(_metric(shielded, "active_effect_uptime")) == ["no_active_effect"]
    response = _metric(shielded, "mechanic_response_availability")
    assert response.value == pytest.approx(0.75)
    assert _codes(response) == ["unavailable:damage_shield"]
    assert "leader_activation" not in _names(shielded)
    assert "recovery_sufficiency" not in _names(shielded)

    absorb = _raw(173, 2, [2, 1], 5, 1)
    absorb_effect = decode_skill(absorb, {2: absorb})[0]
    assert absorb_effect.kind == "absorb_void"
    assert absorb_effect.details["turns"] == 2
    absorbed = run_scenario(
        [MemberInput(role="leader", card=_card(2, 2), awakenings=[BOOST] * 4)],
        {2: absorb},
        [_solid("wood")] * 4,
        target=PolicyTarget(mechanics=["absorb_void"]),
    )
    assert _value(absorbed, "mechanic_response_availability") == pytest.approx(0.5)
    assert _codes(_metric(absorbed, "mechanic_response_availability")) == [
        "unavailable:absorb_void"
    ]


def test_unverified_component_does_not_answer_a_mechanic():
    create = _raw(141, 2, [3, 8, 8], 1, 1)
    compound = _raw(116, 1, [2, 9999], 1, 1)
    skills = {1: compound, 2: create}
    decoded = decode_skill(compound, skills)
    assert [effect.kind for effect in decoded] == ["create_orbs", "unsupported"]
    report = run_scenario(
        [MemberInput(role="leader", card=_card(1, 1), awakenings=[])],
        skills,
        [_solid("wood")],
        target=PolicyTarget(mechanics=["create_orbs", "unsupported"]),
    )
    response = _metric(report, "mechanic_response_availability")
    assert response.value == pytest.approx(0.5)
    assert _codes(response) == ["unavailable:unsupported"]


def test_skill_readiness_bind_and_unknown_cooldown():
    waiting = _raw(18, 1, [1], 5, 1)
    members = [MemberInput(role="leader", card=_card(1, 1), awakenings=[])]
    report = run_scenario(members, {1: waiting}, [_solid("wood")] * 4)
    availability = _metric(report, "skill_availability")
    assert availability.value == 0
    assert availability.trials == 4
    assert _codes(availability) == ["not_ready"]

    ready = _raw(18, 1, [1], 0, 0)
    state = open_battle(
        [MemberInput(role="leader", card=_card(1, 1), awakenings=[])],
        {1: ready},
    )
    apply_bind(state, 0, 1)
    step(state, board=_solid("wood"))
    step(state, board=_solid("wood"))
    bound = report_battles([state], mode="scenario", seed=0, seeds=[0])
    availability = _metric(bound, "skill_availability")
    assert availability.value == pytest.approx(0.5)
    assert _codes(availability) == ["bound"]

    resisted_state = open_battle(
        [MemberInput(role="leader", card=_card(1, 1), awakenings=[RESIST])],
        {1: waiting},
    )
    apply_skill_delay(resisted_state, 1)
    for _turn in range(4):
        step(resisted_state, board=_solid("wood"))
    resisted = report_battles([resisted_state], mode="scenario", seed=0, seeds=[0])
    availability = _metric(resisted, "skill_availability")
    assert availability.value is None
    assert availability.trials == 0
    assert availability.unresolved == 4
    assert _codes(availability) == ["unknown_cooldown"]


def test_turns_to_transform_uses_the_first_verified_cast():
    skill = _raw(202, 5, [20], 1, 1)
    effect = decode_skill(skill, {5: skill})[0]
    assert effect.kind == "transform"
    assert effect.details["monster_id"] == 20
    report = run_scenario(
        [MemberInput(role="leader", card=_card(19, 5), awakenings=[])],
        {5: skill},
        [_solid("wood")],
    )
    transformed = _metric(report, "turns_to_transform")
    assert transformed.value == 1
    assert transformed.successes == 1
    assert transformed.trials == 1
    assert transformed.interval is None
    assert transformed.failures == []
    assert any("#19 transformed on turn 1" in note for note in transformed.notes)

    held = _raw(202, 5, [20], 8, 1)
    missed = run_scenario(
        [MemberInput(role="leader", card=_card(19, 5), awakenings=[])],
        {5: held},
        [_solid("wood")],
    )
    transformed = _metric(missed, "turns_to_transform")
    assert transformed.value is None
    assert transformed.successes == 0
    assert transformed.trials == 1
    assert _codes(transformed) == ["not_transformed"]


def test_unsupported_board_is_unresolved_rather_than_a_miss():
    state = open_battle(_linked_members(), _linked_skills())
    state.board.columns = 7
    state.board.rows = 6
    step(state)
    report = report_battles([state], mode="scenario", seed=0, seeds=[0])
    activation = _metric(report, "leader_activation")
    assert activation.value is None
    assert activation.trials == 0
    assert activation.unresolved == 1
    assert "board_unobserved" in _codes(activation)
    assert any(code.startswith("unknown:") for code in _codes(activation))
    healing = _metric(report, "healing_orb_availability")
    assert healing.value is None
    assert _codes(healing) == ["board_unobserved"]
    assert _value(report, "longest_activation_gap") == 1


def test_monte_carlo_records_seeds_intervals_and_repeats():
    members = [MemberInput(role="leader", card=_card(1, 1), awakenings=[])]
    skills = {1: _raw(18, 1, [1], 99, 1)}
    report = run_monte_carlo(members, skills, turns=2, simulations=3, seed=4)
    assert report.mode == "monte_carlo"
    assert report.simulations == 3
    assert report.turns == 2
    assert report.seed == 4
    assert report.seeds == [4, 5, 6]
    assert any("seed + i" in item for item in report.assumptions)
    availability = _metric(report, "skill_availability")
    assert availability.interval is not None
    assert availability.interval.level == 0.95
    assert availability.value is not None
    assert availability.interval.low <= availability.value <= availability.interval.high
    assert "mechanic_response_availability" not in _names(report)
    assert "recovery_sufficiency" not in _names(report)
    again = run_monte_carlo(members, skills, turns=2, simulations=3, seed=4)
    assert report.model_dump() == again.model_dump()


def test_generator_rotation_changes_activation_and_repeats():
    fast_members, fast_skills = _generator(1)
    slow_members, slow_skills = _generator(8)
    assert cooldown_spec(fast_skills[4]).minimum == 1
    assert cooldown_spec(slow_skills[4]).minimum == 8
    fast_skill = {key: value for key, value in fast_skills[4].items() if key != "initialCooldown"}
    slow_skill = {key: value for key, value in slow_skills[4].items() if key != "initialCooldown"}
    assert fast_skill == slow_skill
    target = PolicyTarget(mechanics=["create_orbs"])
    fast = run_monte_carlo(
        fast_members, fast_skills, target=target, turns=10, simulations=20, seed=0
    )
    slow = run_monte_carlo(
        slow_members, slow_skills, target=target, turns=10, simulations=20, seed=0
    )
    assert _value(fast, "leader_activation") == 1
    assert _value(fast, "offensive_activation") == _value(fast, "leader_activation")
    assert _value(fast, "defensive_activation") == _value(fast, "leader_activation")
    assert _value(slow, "leader_activation") <= _value(fast, "leader_activation") - 0.25
    assert _value(slow, "offensive_activation") == _value(slow, "leader_activation")
    assert _value(slow, "defensive_activation") == _value(slow, "leader_activation")
    assert _metric(fast, "leader_activation").failures == []
    assert any(code.startswith("unmet:") for code in _codes(_metric(slow, "leader_activation")))
    assert _value(fast, "orb_generation_uptime") == 1
    assert _value(slow, "orb_generation_uptime") == pytest.approx(0.1)
    assert _metric(fast, "leader_activation").interval is not None
    assert _metric(slow, "leader_activation").interval is not None
    again = run_monte_carlo(
        slow_members, slow_skills, target=target, turns=10, simulations=20, seed=0
    )
    assert slow.model_dump() == again.model_dump()


def test_run_rejects_an_empty_or_short_request():
    members = _linked_members()
    skills = _linked_skills()
    with pytest.raises(ValueError):
        run_monte_carlo(members, skills, turns=0)
    with pytest.raises(ValueError):
        run_monte_carlo(members, skills, simulations=0)
    with pytest.raises(ValueError):
        run_scenario(members, skills, [])
    with pytest.raises(ValueError):
        run_scenario(members, skills, [["fire"] * 29])
    with pytest.raises(ValueError):
        run_scenario(members, skills, [_solid("fire")[:-1] + [""]])
