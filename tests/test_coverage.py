"""Coverage checks owned copies. It does not assemble a team."""

import json
from contextlib import closing
from pathlib import Path

import pytest

from padbox.database.store import Store
from padbox.mechanics.awakenings import NAMES, describe
from padbox.mechanics.coverage import cover_box
from padbox.models import Monster, Provenance, Relationship, Skill


def _raw(skill_id: int, kind: int, params: list[int], **extra: object) -> dict:
    return {"id": skill_id, "type": kind, "params": params, "name": "", "description": "", **extra}


def _skill(
    skill_id: int, kind: int, params: list[int], initial: int | None = None, levels: int = 1
) -> Skill:
    raw = _raw(skill_id, kind, params)
    minimum = None
    if initial:
        raw["initialCooldown"] = initial
        raw["maxLevel"] = levels
        minimum = max(0, initial - max(0, levels - 1))
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
    document = {
        "schema_version": 1,
        "server": "NA",
        "monsters": monsters,
        "unresolved": [],
        "complete": True,
    }
    with closing(store.connect()) as db, db:
        db.execute("INSERT INTO box_state VALUES (1, 1, ?, NULL)", (json.dumps(document),))


def _providers(report: dict, role: str, form_id: int) -> list[dict]:
    return [item for item in report["roles"][role]["providers"] if item["form_id"] == form_id]


def test_awakening_catalog_matches_this_snapshot():
    assert len(NAMES) == 153
    assert describe(49)["name"] == "Awoken Assist"
    assert describe(17)["name"] == "Enhanced Light Orbs"
    assert describe(25)["name"] == "Enhanced Light Rows"
    assert describe(94)["name"] == "Change Sub Attribute: Light"
    assert describe(102)["name"] == "Enhanced Light Orbs+"
    assert describe(119)["name"] == "Triple Enhanced Light Rows"
    assert describe(149)["name"] == "Fire Column"
    assert describe(21)["effects"] == [{"kind": "skill_boost", "value": 1}]
    assert describe(56)["effects"] == [{"kind": "skill_boost", "value": 2}]
    assert describe(105)["effects"] == [{"kind": "skill_boost", "value": -1}]
    assert describe(55)["effects"] == [{"kind": "tape_resist", "value": 1}]
    assert describe(46)["effects"] == [{"kind": "team_hp", "value": 1}]
    assert describe(999)["name"] is None
    assert describe(999)["evidence"] == "unknown"


def test_cover_requires_an_applied_box(tmp_path: Path):
    with pytest.raises(ValueError, match="No applied box"):
        cover_box(Store(tmp_path), 1)


def _roze_store(tmp_path: Path) -> tuple[Store, dict]:
    store = Store(tmp_path)
    index = {
        2: _raw(2, 196, [9999]),
        3: _raw(3, 117, [9999, 0, 0, 0, 9999]),
        4: _raw(4, 146, [2, 2]),
        5: _raw(5, 202, [13967]),
        12: _raw(12, 196, [9999]),
        13: _raw(13, 117, [0, 0, 0, 0, 9999]),
        14: _raw(14, 191, [4]),
        21: _raw(21, 182, [8, 5, 100, 83]),
        22: _raw(22, 271, [8, 20000, 0, 4, 17000000]),
        31: _raw(31, 173, [2, 1, 0, 1]),
    }
    store.load_monsters(
        [
            _monster(
                13966,
                "Base",
                awakenings=[21, 21],
                relationships=[
                    Relationship(target_id=13967, kind="transform"),
                    Relationship(target_id=13968, kind="assist_evolution"),
                ],
                active_skill=_skill(1, 116, [2, 3, 4, 5], initial=28, levels=5),
                raw={"syncAwakening": 138},
            ),
            _monster(
                13967,
                "Combat",
                awakenings=[48],
                active_skill=_skill(11, 116, [12, 13, 14], initial=4),
                leader_skill=_skill(20, 138, [21, 22]),
            ),
            _monster(
                13968,
                "Assist",
                awakenings=[49, 56, 56],
                active_skill=_skill(30, 116, [31], initial=18, levels=11),
                raw={"evoMaterials": [900]},
            ),
            _monster(900, "Jewel", raw={"stackable": True}),
        ]
    )
    _box(store, [{"monster_id": 13966, "quantity": 1, "confirmed": True}])
    return store, index


def test_one_copy_covers_its_forms_without_charging_its_own_transform(tmp_path: Path):
    store, index = _roze_store(tmp_path)
    report = cover_box(store, 13967, skill_index=index)
    assert report["leader"]["entry_id"] == 13966
    assert report["leader"]["available_copies"] == 1
    assert report["leader"]["skill_boost"] == 2
    assert report["transform"]["required_turns"] == 24
    card = report["transform"]["cards"][0]
    assert card["haste_on_this_skill"] == 2
    assert card["skill_boost"] == 2
    ceiling = report["transform"]["ceiling"]
    assert ceiling["total"] == 2
    assert ceiling["meets_requirement"] is False
    assert ceiling["is_team"] is False
    assert ceiling["badge"] == "excluded"
    assert _providers(report, "bind_clear", 13966)
    assert not _providers(report, "bind_clear", 13967)
    awoken = _providers(report, "awoken_bind_clear", 13967)
    assert awoken and awoken[0]["stage"] == "battle"
    assist = _providers(report, "attribute_absorb", 13968)[0]
    assert assist["path"] == ["assist_evolution"]
    assert assist["spends"] == []
    assert assist["copies"] == 1
    assert _providers(report, "damage_absorb", 13968)
    pierce = _providers(report, "void_pierce", 13967)[0]
    assert pierce["stage"] == "battle"
    assert report["roles"]["void_pierce"]["total"] == 1
    assert report["awakening_checks"]["void_pierce_awakening"] == 0
    color = report["color_force"][0]
    assert color["attributes"] == ["light"]
    assert color["minimum_linked"] == 5
    assert color["match"] == "cross"
    assert color["loop"] is None
    assert color["status"] == "hole"
    text = "\n".join(report["holes"])
    assert "24-turn transform" in text
    assert "combo absorb" in text
    assert "cannot be a cross" in text
    assert report["loadout"]["latents"] == "not selected"
    assert report["loadout"]["sync_awakening"]["name"] == "Assist Resonance"
    assert "Not assumed" in report["loadout"]["sync_awakening"]["note"]


def test_leader_forms_stay_visible_when_other_answers_are_shorter(tmp_path: Path):
    store, index = _roze_store(tmp_path)
    extras = []
    rows = [{"monster_id": 13966, "quantity": 1, "confirmed": True}]
    for offset in range(40):
        monster_id = 2000 + offset
        extras.append(
            _monster(
                monster_id,
                f"Fast {offset}",
                active_skill=_skill(3000 + offset, 173, [1, 1, 0, 0], initial=1),
            )
        )
        rows.append({"monster_id": monster_id, "quantity": 1, "confirmed": True})
    store.load_monsters(extras)
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE box_state SET document=? WHERE id=1",
            (
                json.dumps(
                    {
                        "schema_version": 1,
                        "server": "NA",
                        "monsters": rows,
                        "unresolved": [],
                        "complete": True,
                    }
                ),
            ),
        )
    report = cover_box(store, 13967, skill_index=index)
    assist = _providers(report, "attribute_absorb", 13968)
    assert assist and assist[0]["pinned"] is True
    assert assist[0]["path"] == ["assist_evolution"]


def test_skill_boost_ceiling_uses_other_copies_and_not_a_team(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Leader",
                awakenings=[21, 21],
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 202, [2], initial=24),
            ),
            _monster(2, "Combat"),
            _monster(80, "Boost", awakenings=[56] * 8),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 80, "quantity": 5, "confirmed": True},
        ],
    )
    report = cover_box(store, 2, skill_index={})
    ceiling = report["transform"]["ceiling"]
    assert ceiling["total"] == 82
    assert ceiling["chosen"] == [
        {
            "owned_id": 80,
            "owned_name": "Boost",
            "form_id": 80,
            "form_name": "Boost",
            "skill_boost": 16,
            "copies": 5,
        }
    ]
    assert ceiling["meets_requirement"] is True
    assert ceiling["is_team"] is False


def test_borrowed_helper_adds_skill_boost_without_being_owned(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Leader",
                awakenings=[21, 21],
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 202, [2], initial=24),
            ),
            _monster(2, "Combat"),
            _monster(50, "Friend", awakenings=[56, 56]),
        ]
    )
    _box(store, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    report = cover_box(store, 2, helper_id=50, skill_index={})
    assert report["helper"]["borrowed"] is True
    assert report["helper"]["skill_boost"] == 4
    ceiling = report["transform"]["ceiling"]
    assert ceiling["helper_skill_boost"] == 4
    assert ceiling["sub_slots"] == 4
    assert ceiling["total"] == 6


def test_full_color_board_can_loop_and_random_orbs_cannot(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(10, "Leader", leader_skill=_skill(1, 138, [2])),
            _monster(11, "Maker", active_skill=_skill(3, 116, [4], initial=1)),
            _monster(12, "Board", active_skill=_skill(5, 116, [6], initial=2)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 11, "quantity": 1, "confirmed": True},
            {"monster_id": 12, "quantity": 2, "confirmed": True},
        ],
    )
    index = {2: _raw(2, 182, [8, 5, 100, 83]), 4: _raw(4, 141, [6, 8, 8]), 6: _raw(6, 71, [3, -1])}
    report = cover_box(store, 10, skill_index=index)
    color = report["color_force"][0]
    assert color["loop"]["fits"] is True
    assert color["loop"]["used"][0]["owned_id"] == 12
    assert color["also_produces_total"] == 1
    assert color["also_produces"][0]["form_id"] == 11

    store = Store(tmp_path / "orbs")
    store.load_monsters(
        [
            _monster(10, "Leader", leader_skill=_skill(1, 138, [2])),
            _monster(11, "Maker", active_skill=_skill(3, 116, [4], initial=1)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 11, "quantity": 3, "confirmed": True},
        ],
    )
    report = cover_box(store, 10, skill_index=index)
    assert report["color_force"][0]["loop"]["fits"] is False
    assert report["color_force"][0]["also_produces_total"] == 1


def test_orb_conversion_produces_a_color_without_forcing_a_loop(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(10, "Leader", leader_skill=_skill(1, 138, [2])),
            _monster(11, "Convert", active_skill=_skill(3, 116, [4], initial=1)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 11, "quantity": 5, "confirmed": True},
        ],
    )
    report = cover_box(
        store,
        10,
        skill_index={2: _raw(2, 182, [8, 5, 100, 83]), 4: _raw(4, 154, [32, 8])},
    )
    color = report["color_force"][0]
    assert color["loop"]["fits"] is False
    assert color["providers_total"] == 0
    assert color["also_produces"][0]["summary"] == "Changes heal to light"


def test_direct_orb_change_produces_a_color_without_forcing_a_loop(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(10, "Leader", leader_skill=_skill(1, 138, [2])),
            _monster(11, "Change", active_skill=_skill(3, 116, [4], initial=1)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 11, "quantity": 1, "confirmed": True},
        ],
    )
    report = cover_box(
        store,
        10,
        skill_index={2: _raw(2, 182, [8, 5, 100, 83]), 4: _raw(4, 20, [4, 3, 1, 3])},
    )
    color = report["color_force"][0]
    assert color["loop"]["fits"] is False
    assert color["providers_total"] == 0
    assert color["also_produces"][0]["summary"] == "Changes dark, water to light"


def test_cross_leader_counts_orb_makers_and_not_a_full_board(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(10, "Leader", leader_skill=_skill(1, 138, [2, 3])),
            _monster(11, "Column", active_skill=_skill(4, 116, [5], initial=1)),
            _monster(12, "Board", active_skill=_skill(6, 116, [7], initial=1)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 11, "quantity": 1, "confirmed": True},
            {"monster_id": 12, "quantity": 1, "confirmed": True},
        ],
    )
    report = cover_box(
        store,
        10,
        skill_index={
            2: _raw(2, 182, [8, 5, 100, 83]),
            3: _raw(3, 271, [8, 20000, 0, 4, 17000000]),
            5: _raw(5, 127, [32, 8]),
            7: _raw(7, 71, [3, -1]),
        },
    )
    color = report["color_force"][0]
    assert color["match"] == "cross"
    assert color["status"] == "available"
    assert color["loop"] is None
    assert [item["form_id"] for item in color["providers"]] == [11]
    assert [item["form_id"] for item in color["mono_boards"]] == [12]
    assert not any("cannot be a cross" in hole for hole in report["holes"])

    board_only = Store(tmp_path / "board")
    board_only.load_monsters(
        [
            _monster(10, "Leader", leader_skill=_skill(1, 138, [2, 3])),
            _monster(12, "Board", active_skill=_skill(6, 116, [7], initial=1)),
        ]
    )
    _box(
        board_only,
        [
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 12, "quantity": 1, "confirmed": True},
        ],
    )
    report = cover_box(
        board_only,
        10,
        skill_index={
            2: _raw(2, 182, [8, 5, 100, 83]),
            3: _raw(3, 271, [8, 20000, 0, 4, 17000000]),
            7: _raw(7, 71, [3, -1]),
        },
    )
    color = report["color_force"][0]
    assert color["status"] == "hole"
    assert color["providers_total"] == 0
    assert color["mono_boards"][0]["form_id"] == 12
    assert any("cannot be a cross" in hole for hole in report["holes"])


def test_column_change_produces_a_color_without_forcing_a_loop(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(10, "Leader", leader_skill=_skill(1, 138, [2])),
            _monster(11, "Column", active_skill=_skill(3, 116, [4], initial=1)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 11, "quantity": 1, "confirmed": True},
        ],
    )
    report = cover_box(
        store,
        10,
        skill_index={2: _raw(2, 182, [8, 5, 100, 83]), 4: _raw(4, 127, [32, 8])},
    )
    color = report["color_force"][0]
    assert color["loop"]["fits"] is False
    assert color["providers_total"] == 0
    assert color["also_produces"][0]["summary"] == "Changes the rightmost column to light"


def test_unknown_opcode_keeps_a_verified_role_partial(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters([_monster(7, "Mixed", active_skill=_skill(1, 116, [2, 3], initial=5))])
    _box(store, [{"monster_id": 7, "quantity": 1, "confirmed": True}])
    report = cover_box(store, 7, skill_index={2: _raw(2, 191, [4]), 3: _raw(3, 999, [])})
    assert report["roles"]["void_pierce"]["status"] == "partial"
    assert report["roles"]["void_pierce"]["providers"][0]["unknown_effects"] == 1
    assert any("unknown skill effects" in hole for hole in report["holes"])


def test_stackable_material_is_free_and_a_specific_monster_is_spent(tmp_path: Path):
    blocked = Store(tmp_path / "blocked")
    blocked.load_monsters(
        [
            _monster(1, "Prevo", relationships=[Relationship(target_id=2, kind="evolution")]),
            _monster(2, "Leader", raw={"evoMaterials": [9]}),
            _monster(9, "Specific", raw={"stackable": False}),
        ]
    )
    _box(blocked, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    report = cover_box(blocked, 2, skill_index={})
    assert report["leader"]["available_copies"] == 0
    assert any("not reachable" in hole for hole in report["holes"])

    free = Store(tmp_path / "free")
    free.load_monsters(
        [
            _monster(1, "Prevo", relationships=[Relationship(target_id=2, kind="evolution")]),
            _monster(2, "Leader", raw={"evoMaterials": [9]}),
            _monster(9, "Jewel", raw={"stackable": True}),
        ]
    )
    _box(free, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    report = cover_box(free, 2, skill_index={})
    assert report["leader"]["available_copies"] == 1
    assert report["leader"]["reserved_from"] == 1
    assert report["leader"]["spends"] == []

    spent = Store(tmp_path / "spent")
    spent.load_monsters(
        [
            _monster(1, "Prevo", relationships=[Relationship(target_id=2, kind="evolution")]),
            _monster(
                2,
                "Entry",
                relationships=[Relationship(target_id=3, kind="transform")],
                active_skill=_skill(1, 202, [3], initial=10),
                raw={"evoMaterials": [9]},
            ),
            _monster(3, "Combat"),
            _monster(9, "Specific", awakenings=[56] * 8, raw={"stackable": False}),
        ]
    )
    _box(
        spent,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 9, "quantity": 1, "confirmed": True},
        ],
    )
    report = cover_box(spent, 3, skill_index={})
    assert report["leader"]["available_copies"] == 1
    assert report["leader"]["spends"] == [9]
    assert report["transform"]["ceiling"]["chosen"] == []
    assert report["transform"]["ceiling"]["total"] == 0


def test_unknown_leader_is_rejected(tmp_path: Path):
    store = Store(tmp_path)
    _box(store, [])
    with pytest.raises(ValueError, match="not in the local reference set"):
        cover_box(store, 404, skill_index={})
