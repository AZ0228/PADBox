"""Versioned dungeon profiles.

The fixture is the schema example in brief §9. It is not real dungeon data.
A profile on a scenario makes each listed mechanic an essential requirement.
"""

import json
from contextlib import closing
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from padbox.database.store import Store
from padbox.evaluator import (
    DUNGEON_PROFILE_VERSION,
    ENCOUNTER_MECHANICS,
    DungeonProfile,
    evaluate_team,
    load_dungeon_profile,
)
from padbox.evaluator.dungeon import (
    DungeonFloor,
    EncounterMechanic,
    answer_keys,
    unmodeled_mechanics,
)
from padbox.mechanics.result import CheckResult
from padbox.models import Monster, Provenance, Skill

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "dungeons" / "example.json"
BRIEF_EXAMPLE = {
    "name": "Example Dungeon",
    "floors": [
        {
            "enemy_hp": 10000000000,
            "preemptive_damage": 1000000,
            "mechanics": ["damage_absorb", "awoken_bind"],
        }
    ],
}


def test_vocabulary_matches_the_declared_type() -> None:
    assert get_args(EncounterMechanic) == ENCOUNTER_MECHANICS
    assert DUNGEON_PROFILE_VERSION == 1
    profile = DungeonProfile.model_validate(
        {
            "name": "All",
            "floors": [
                {
                    "enemy_hp": 1,
                    "preemptive_damage": 0,
                    "mechanics": list(ENCOUNTER_MECHANICS),
                }
            ],
        }
    )
    modeled = {
        "damage_absorb",
        "attribute_absorb",
        "damage_void",
        "awoken_bind",
        "unmatchable",
    }
    assert set(unmodeled_mechanics(profile)) == {
        "skill_delay",
        "resolve",
        "enemy_shield",
        "hp_threshold",
    }
    assert modeled.isdisjoint(unmodeled_mechanics(profile))
    assert modeled | set(unmodeled_mechanics(profile)) == set(ENCOUNTER_MECHANICS)
    assert "damage_absorb" in answer_keys(profile)
    assert "awoken_bind_clear" in answer_keys(profile)


def test_brief_example_fixture_loads() -> None:
    raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert raw == BRIEF_EXAMPLE
    assert "schema_version" not in raw
    profile = load_dungeon_profile(EXAMPLE)
    floor = profile.floors[0]
    assert profile.schema_version == 1
    assert profile.name == "Example Dungeon"
    assert floor.enemy_hp == 10_000_000_000
    assert floor.preemptive_damage == 1_000_000
    assert floor.mechanics == ["damage_absorb", "awoken_bind"]


def test_every_vocabulary_name_is_accepted() -> None:
    profile = DungeonProfile.model_validate(
        {
            "schema_version": 1,
            "name": "Vocabulary",
            "floors": [
                {
                    "enemy_hp": 10,
                    "preemptive_damage": 0,
                    "mechanics": ["resolve"],
                },
                {
                    "enemy_hp": 20,
                    "preemptive_damage": 5,
                    "mechanics": list(ENCOUNTER_MECHANICS),
                },
            ],
        }
    )
    assert [floor.enemy_hp for floor in profile.floors] == [10, 20]
    assert profile.floors[0].mechanics == ["resolve"]
    assert profile.floors[1].mechanics == list(ENCOUNTER_MECHANICS)
    assert profile.floors[1].preemptive_damage == 5


def test_unknown_mechanic_name_is_rejected() -> None:
    floor = {
        "enemy_hp": 1,
        "preemptive_damage": 0,
        "mechanics": ["damage_absorb", "combo_absorb"],
    }
    with pytest.raises(ValidationError, match="Unknown mechanic names: combo_absorb"):
        DungeonFloor.model_validate(floor)
    labeled = {
        "enemy_hp": 1,
        "preemptive_damage": 0,
        "mechanics": ["damage absorption"],
    }
    with pytest.raises(ValidationError, match="Unknown mechanic names: damage absorption"):
        DungeonFloor.model_validate(labeled)


def test_unsupported_version_is_rejected() -> None:
    payload = {**BRIEF_EXAMPLE, "schema_version": 2}
    with pytest.raises(ValidationError, match="Unsupported dungeon profile version 2"):
        DungeonProfile.model_validate(payload)


def test_floor_numbers_and_shape_are_checked() -> None:
    with pytest.raises(ValidationError):
        DungeonFloor.model_validate(
            {"enemy_hp": 0, "preemptive_damage": 0, "mechanics": []}
        )
    with pytest.raises(ValidationError):
        DungeonFloor.model_validate(
            {"enemy_hp": 1, "preemptive_damage": -1, "mechanics": []}
        )
    with pytest.raises(ValidationError):
        DungeonFloor.model_validate({"enemy_hp": 1, "preemptive_damage": 0})
    with pytest.raises(ValidationError):
        DungeonProfile.model_validate({"name": "Empty", "floors": []})
    with pytest.raises(ValidationError):
        DungeonProfile.model_validate({"name": "", "floors": BRIEF_EXAMPLE["floors"]})
    with pytest.raises(ValidationError):
        DungeonProfile.model_validate(
            {"name": "Extra", "note": "nope", "floors": BRIEF_EXAMPLE["floors"]}
        )


def test_saved_profile_records_its_version(tmp_path: Path) -> None:
    profile = load_dungeon_profile(EXAMPLE)
    path = tmp_path / "dungeon.json"
    path.write_text(profile.model_dump_json(), encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert load_dungeon_profile(path) == profile


def _raw(skill_id: int, kind: int, params: list[int]) -> dict:
    return {"id": skill_id, "type": kind, "params": params, "name": "", "description": ""}


def _skill(skill_id: int, kind: int, params: list[int], initial: int | None = None) -> Skill:
    raw = _raw(skill_id, kind, params)
    minimum = None
    if initial:
        raw["initialCooldown"] = initial
        raw["maxLevel"] = 1
        minimum = initial
    return Skill(
        skill_id=skill_id,
        name="",
        base_cooldown=initial,
        minimum_cooldown=minimum,
        raw=raw,
    )


def _monster(monster_id: int, name: str, **kwargs: object) -> Monster:
    return Monster(
        monster_id=monster_id,
        name=name,
        servers=["NA"],
        provenance=Provenance(source="test", version="1"),
        **kwargs,  # type: ignore[arg-type]
    )


def _box(store: Store, monsters: list[dict]) -> None:
    document = json.dumps(
        {
            "schema_version": 1,
            "server": "NA",
            "monsters": monsters,
            "unresolved": [],
            "complete": True,
        }
    )
    with closing(store.connect()) as db, db:
        db.execute("INSERT INTO box_state VALUES (1, 1, ?, NULL)", (document,))


def _index() -> dict:
    return {
        2: _raw(2, 117, [9999, 0, 0, 0, 9999]),
        3: _raw(3, 196, [9999]),
        4: _raw(4, 173, [2, 1, 1, 1]),
        5: _raw(5, 191, [1]),
        6: _raw(6, 71, [3, -1]),
        9: _raw(9, 182, [8, 5, 100, 83]),
    }


def _ready_store(tmp_path: Path, cooldown: int) -> Store:
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader", leader_skill=_skill(10, 138, [9])),
            _monster(
                10,
                "Answer",
                active_skill=_skill(1, 116, [2, 3, 4, 5, 6], initial=cooldown),
            ),
            _monster(50, "Helper"),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 10, "quantity": 4, "confirmed": True},
            {"monster_id": 50, "quantity": 1, "confirmed": True},
        ],
    )
    return store


def _team() -> dict:
    return {
        "leader_id": 1,
        "helper_id": 50,
        "subs": [{"owned_id": 10, "form_id": 10} for _ in range(4)],
    }


def _constraint(report, name: str):
    return next(item for item in report.constraints if item.name == name)


def test_example_dungeon_is_handled_when_the_responses_are_timed(tmp_path: Path) -> None:
    store = _ready_store(tmp_path, cooldown=1)
    report = evaluate_team(
        _team(),
        {
            "turns": 2,
            "simulations": 2,
            "dungeon": json.loads(EXAMPLE.read_text(encoding="utf-8")),
        },
        store=store,
        skill_index=_index(),
        explain=False,
    )
    floor = report.dungeon.floors[0]
    assert report.dungeon.name == "Example Dungeon"
    assert report.scenario.dungeon is not None
    assert floor.handled is True
    assert _constraint(report, "floor:1:damage_absorb").result is CheckResult.PASS
    assert _constraint(report, "floor:1:awoken_bind").result is CheckResult.PASS
    assert all(item.modeled and item.result is CheckResult.PASS for item in floor.requirements)
    assert any("not cleared" in line for line in floor.uncertainties)
    assert "cleared" not in report.dungeon.model_dump()
    assert report.feasibility is CheckResult.PASS


def test_a_slow_response_is_a_known_miss(tmp_path: Path) -> None:
    store = _ready_store(tmp_path, cooldown=30)
    report = evaluate_team(
        _team(),
        {"turns": 2, "simulations": 1, "dungeon": json.loads(EXAMPLE.read_text(encoding="utf-8"))},
        store=store,
        skill_index=_index(),
        explain=False,
    )
    floor = report.dungeon.floors[0]
    assert _constraint(report, "requirement:damage absorb void").result is CheckResult.PASS
    assert _constraint(report, "floor:1:damage_absorb").result is CheckResult.FAIL
    assert floor.requirements[0].modeled is True
    assert floor.handled is False
    assert report.feasibility is CheckResult.FAIL


def test_attribute_absorb_does_not_meet_damage_absorb(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader", active_skill=_skill(1, 173, [3, 1], initial=1)),
            _monster(50, "Helper"),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 50, "quantity": 1, "confirmed": True},
        ],
    )
    report = evaluate_team(
        {"leader_id": 1, "helper_id": 50},
        {
            "turns": 2,
            "simulations": 1,
            "dungeon": {
                "name": "Split",
                "floors": [
                    {
                        "enemy_hp": 10,
                        "preemptive_damage": 0,
                        "mechanics": ["damage_absorb", "attribute_absorb"],
                    }
                ],
            },
        },
        store=store,
        skill_index={1: _raw(1, 173, [3, 1]) | {"initialCooldown": 1, "maxLevel": 1}},
        explain=False,
    )
    floor = report.dungeon.floors[0]
    by_name = {item.mechanic: item for item in floor.requirements}
    assert by_name["attribute_absorb"].result is CheckResult.PASS
    assert by_name["damage_absorb"].result is CheckResult.FAIL
    assert floor.handled is False


def test_unmodeled_mechanics_stay_unknown_and_do_not_clear(tmp_path: Path) -> None:
    store = _ready_store(tmp_path, cooldown=1)
    report = evaluate_team(
        _team(),
        {
            "turns": 2,
            "simulations": 1,
            "dungeon": {
                "name": "Unknowns",
                "floors": [
                    {
                        "enemy_hp": 20,
                        "preemptive_damage": 5,
                        "mechanics": ["resolve", "skill_delay", "enemy_shield", "hp_threshold"],
                    }
                ],
            },
        },
        store=store,
        skill_index=_index(),
        explain=False,
    )
    floor = report.dungeon.floors[0]
    assert floor.handled is False
    assert all(
        item.modeled is False and item.result is CheckResult.UNKNOWN for item in floor.requirements
    )
    assert any("Resolve is not a modeled encounter" in line for line in floor.uncertainties)
    assert any("not cleared" in line for line in report.unknowns)
    assert report.feasibility is CheckResult.UNKNOWN
    dumped = json.dumps(report.dungeon.model_dump(mode="json"))
    assert '"cleared": true' not in dumped
    assert "does not say a floor is cleared" in report.dungeon.note
