"""The pool lists candidates. The check accepts a named team and does not search."""

import json
from contextlib import closing
from pathlib import Path

import pytest

from padbox.database.store import Store
from padbox.mechanics.team import check_team, pool_box, shortlist_box
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


def _document(monsters: list[dict]) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "server": "NA",
            "monsters": monsters,
            "unresolved": [],
            "complete": True,
        }
    )


def _box(store: Store, monsters: list[dict]) -> None:
    with closing(store.connect()) as db, db:
        db.execute("INSERT INTO box_state VALUES (1, 1, ?, NULL)", (_document(monsters),))


def _replace_box(store: Store, monsters: list[dict]) -> None:
    with closing(store.connect()) as db, db:
        db.execute("UPDATE box_state SET document=? WHERE id=1", (_document(monsters),))


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


def _index() -> dict:
    return {
        2: _raw(2, 117, [9999, 0, 0, 0, 9999]),
        3: _raw(3, 196, [9999]),
        4: _raw(4, 173, [2, 1, 1, 1]),
        5: _raw(5, 191, [1]),
        6: _raw(6, 71, [3, -1]),
        7: _raw(7, 154, [32, 8]),
        8: _raw(8, 999, []),
        9: _raw(9, 182, [8, 5, 100, 83]),
    }


def _leader_box(tmp_path: Path, extras: list | None = None, rows: list | None = None) -> Store:
    store = Store(tmp_path)
    monsters = [
        _monster(1, "Leader", leader_skill=_skill(10, 138, [9])),
        _monster(10, "Answer", active_skill=_skill(1, 116, [2, 3, 4, 5, 6], initial=5)),
        _monster(30, "Unused"),
    ]
    owned = [
        {"monster_id": 1, "quantity": 1, "confirmed": True},
        {"monster_id": 10, "quantity": 1, "confirmed": True},
        {"monster_id": 30, "quantity": 1, "confirmed": True},
    ]
    if extras:
        monsters.extend(extras)
    if rows:
        owned.extend(rows)
    store.load_monsters(monsters)
    _box(store, owned)
    return store


def _forms(report: dict, owned_id: int) -> list[dict]:
    group = next(row for row in report["candidates"] if row["owned_id"] == owned_id)
    return group["forms"]


def test_pool_keeps_the_owned_form_beside_the_reachable_one(tmp_path: Path):
    store, index = _roze_store(tmp_path)
    store.load_monsters(
        [
            _monster(30, "Unused"),
            _monster(
                20,
                "Base",
                awakenings=[21],
                relationships=[Relationship(target_id=21, kind="evolution")],
            ),
            _monster(
                21,
                "Evolved",
                awakenings=[56],
                active_skill=_skill(40, 191, [2], initial=8),
                raw={"evoMaterials": [900], "is8Latent": True, "superAwakenings": []},
            ),
        ]
    )
    _replace_box(
        store,
        [
            {"monster_id": 13966, "quantity": 1, "confirmed": True},
            {"monster_id": 20, "quantity": 1, "confirmed": True},
            {"monster_id": 30, "quantity": 1, "confirmed": True},
        ],
    )
    report = pool_box(store, 13967, skill_index=index)
    roze = next(row for row in report["candidates"] if row["owned_id"] == 13966)
    assert roze["leader_copies"] == 1
    assert roze["copies"] == 1
    by_id = {form["form_id"]: form for form in roze["forms"]}
    assert by_id[13966]["availability"] == "in_box"
    assert by_id[13966]["equip"] is False
    assert by_id[13967]["availability"] == "in_box"
    assert by_id[13967]["stage"] == "battle"
    assert "void_pierce" in by_id[13967]["answers"]
    assert by_id[13968]["availability"] == "reachable"
    assert by_id[13968]["equip"] is True
    assert by_id[13968]["path"] == ["assist_evolution"]
    assert "attribute_absorb" in by_id[13968]["answers"]
    grown = {form["form_id"]: form for form in _forms(report, 20)}
    assert grown[20]["availability"] == "in_box"
    assert grown[20]["skill_boost"] == 1
    assert grown[21]["availability"] == "reachable"
    assert grown[21]["skill_boost"] == 2
    assert grown[21]["latent_slots"] == 8
    assert grown[21]["spends"] == []
    assert report["omitted_copies"] == 1
    assert any("does not assign slots" in line for line in report["contract"])
    assert report["requirements"]["transform_turns"] == 24
    assert any("cannot be a cross" in hole for hole in report["holes"])


def test_pool_requires_an_applied_box(tmp_path: Path):
    with pytest.raises(ValueError, match="No applied box"):
        pool_box(Store(tmp_path), 1)


def test_check_accepts_a_team_that_closes_the_holes(tmp_path: Path):
    store = _leader_box(tmp_path)
    report = check_team(
        store,
        1,
        {"subs": [{"owned_id": 10, "form_id": 10}]},
        skill_index=_index(),
    )
    assert report["problems"] == []
    assert report["open_holes"] == []
    assert report["ok"] is True
    assert report["investments"] == []
    assert report["skill_boost"]["badge"] == "excluded"


def test_a_full_board_does_not_close_a_cross_and_a_column_does(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader", leader_skill=_skill(10, 138, [9, 11])),
            _monster(10, "Board", active_skill=_skill(1, 116, [6], initial=5)),
            _monster(12, "Column", active_skill=_skill(2, 116, [12], initial=5)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 12, "quantity": 1, "confirmed": True},
        ],
    )
    index = {**_index(), 11: _raw(11, 271, [8, 20000, 0, 4, 17000000]), 12: _raw(12, 127, [32, 8])}
    board = check_team(
        store, 1, {"subs": [{"owned_id": 10, "form_id": 10}]}, skill_index=index
    )
    assert board["ok"] is False
    assert any("cannot be a cross" in hole for hole in board["open_holes"])
    column = check_team(
        store, 1, {"subs": [{"owned_id": 12, "form_id": 12}]}, skill_index=index
    )
    assert column["problems"] == []
    assert not any("cannot be a cross" in hole for hole in column["open_holes"])
    report = shortlist_box(store, 1, skill_index=index)
    assert _bucket(report, "light_orbs")["candidates"][0]["form_id"] == 12
    assert "full_light_board" not in {bucket["key"] for bucket in report["buckets"]}


def test_subs_have_to_be_the_linked_color_and_have_awakenings(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader", leader_skill=_skill(10, 138, [9]), raw={"attrs": [3]}),
            _monster(
                10,
                "Dark",
                awakenings=[21],
                active_skill=_skill(1, 191, [1], initial=5),
                raw={"attrs": [4]},
            ),
            _monster(
                11,
                "Light",
                awakenings=[21, 21],
                active_skill=_skill(2, 191, [1], initial=5),
                raw={"attrs": [3]},
            ),
            _monster(
                12,
                "Gem",
                active_skill=_skill(3, 191, [1], initial=5),
                raw={"attrs": [3]},
            ),
        ]
    )
    _box(
        store,
        [
            {"monster_id": monster_id, "quantity": 1, "confirmed": True}
            for monster_id in (1, 10, 11, 12)
        ],
    )
    dark = check_team(store, 1, {"subs": [{"owned_id": 10, "form_id": 10}]}, skill_index=_index())
    assert any("is dark" in problem for problem in dark["problems"])
    light = check_team(store, 1, {"subs": [{"owned_id": 11, "form_id": 11}]}, skill_index=_index())
    assert not any("deal damage" in problem for problem in light["problems"])
    gem = check_team(store, 1, {"subs": [{"owned_id": 12, "form_id": 12}]}, skill_index=_index())
    assert any("no awakenings" in problem for problem in gem["problems"])
    report = shortlist_box(store, 1, skill_index=_index())
    ids = [row["form_id"] for row in _bucket(report, "void_pierce")["candidates"]]
    assert 11 in ids
    assert 10 not in ids
    assert 12 not in ids


def test_changing_some_orbs_does_not_close_the_color_hole(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader", leader_skill=_skill(10, 138, [9])),
            _monster(
                10,
                "Partial",
                active_skill=_skill(1, 116, [2, 3, 4, 5, 7], initial=5),
            ),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 10, "quantity": 1, "confirmed": True},
        ],
    )
    report = check_team(
        store,
        1,
        {"subs": [{"owned_id": 10, "form_id": 10}]},
        skill_index=_index(),
    )
    assert report["ok"] is False
    assert any("full board of light" in hole for hole in report["open_holes"])


def test_check_reports_copy_conflicts_and_unreachable_forms(tmp_path: Path):
    store, index = _roze_store(tmp_path)
    store.load_monsters([_monster(50, "Plain", active_skill=_skill(60, 191, [1], initial=5))])
    _replace_box(
        store,
        [
            {"monster_id": 13966, "quantity": 1, "confirmed": True},
            {"monster_id": 50, "quantity": 1, "confirmed": True},
        ],
    )
    spent = check_team(
        store,
        13967,
        {"leader_assist": {"owned_id": 13966, "form_id": 13968}},
        skill_index=index,
    )
    assert spent["ok"] is False
    assert any("used 2 times" in problem for problem in spent["problems"])
    assert spent["investments"][0]["form_id"] == 13968

    missing = check_team(
        store,
        13967,
        {"subs": [{"owned_id": 13966, "form_id": 50}]},
        skill_index=index,
    )
    assert any("not a reachable form" in problem for problem in missing["problems"])

    not_equip = check_team(
        store,
        13967,
        {"subs": [{"owned_id": 50, "form_id": 50, "assist": {"owned_id": 50, "form_id": 50}}]},
        skill_index=index,
    )
    assert any("not an assist equip" in problem for problem in not_equip["problems"])
    assert any("used 2 times" in problem for problem in not_equip["problems"])


def test_unknown_effect_keeps_a_role_open_and_an_evolution_is_an_investment(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader", awakenings=[21], leader_skill=_skill(10, 138, [9])),
            _monster(
                20,
                "Base",
                relationships=[Relationship(target_id=21, kind="evolution")],
            ),
            _monster(
                21,
                "Evolved",
                awakenings=[56, 56],
                active_skill=_skill(1, 116, [2, 3, 4, 5, 6, 8], initial=5),
                raw={"evoMaterials": [900]},
            ),
            _monster(900, "Jewel", raw={"stackable": True}),
            _monster(10, "Clean", active_skill=_skill(11, 116, [2, 3, 5, 6], initial=5)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 20, "quantity": 1, "confirmed": True},
            {"monster_id": 10, "quantity": 1, "confirmed": True},
        ],
    )
    report = check_team(
        store,
        1,
        {"subs": [{"owned_id": 20, "form_id": 21}, {"owned_id": 10, "form_id": 10}]},
        skill_index=_index(),
    )
    assert report["problems"] == []
    assert report["investments"][0]["path"] == ["evolution"]
    assert report["skill_boost"]["total"] >= 4
    assert any("unknown effects" in hole for hole in report["open_holes"])
    assert report["unknown_effects"][0]["form_id"] == 21
    assert report["ok"] is False


def test_only_a_form_with_awoken_assist_can_be_equipped(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader"),
            _monster(
                70,
                "Base",
                relationships=[Relationship(target_id=71, kind="assist_evolution")],
            ),
            _monster(
                71,
                "Listed assist",
                relationships=[Relationship(target_id=72, kind="transform")],
                active_skill=_skill(1, 191, [2], initial=5),
            ),
            _monster(72, "Battle"),
            _monster(
                80,
                "Real assist",
                awakenings=[49],
                active_skill=_skill(2, 191, [2], initial=5),
            ),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 70, "quantity": 1, "confirmed": True},
            {"monster_id": 80, "quantity": 1, "confirmed": True},
        ],
    )
    report = pool_box(store, 1, skill_index={})
    listed = {form["form_id"]: form for form in _forms(report, 70)}
    assert listed[71]["path"] == ["assist_evolution"]
    assert listed[71]["equip"] is False
    assert listed[72]["equip"] is False
    owned_assist = {form["form_id"]: form for form in _forms(report, 80)}
    assert owned_assist[80]["equip"] is True
    assert owned_assist[80]["availability"] == "in_box"
    checked = check_team(
        store,
        1,
        {"subs": [{"owned_id": 80, "form_id": 80, "assist": {"owned_id": 70, "form_id": 71}}]},
        skill_index={},
    )
    assert any("not an assist equip" in problem for problem in checked["problems"])


def _bucket(report: dict, key: str) -> dict:
    return next(bucket for bucket in report["buckets"] if bucket["key"] == key)


def test_shortlist_buckets_roles_and_pins_the_leader(tmp_path: Path):
    store = Store(tmp_path)
    monsters = [
        _monster(
            1,
            "Leader",
            awakenings=[21],
            active_skill=_skill(1, 191, [4], initial=20),
            leader_skill=_skill(2, 182, [8, 5, 100, 83]),
        )
    ]
    rows = [{"monster_id": 1, "quantity": 1, "confirmed": True}]
    for offset in range(10):
        monster_id = 100 + offset
        monsters.append(
            _monster(
                monster_id,
                f"Fast {offset}",
                active_skill=_skill(1000 + offset, 191, [1], initial=1),
            )
        )
        rows.append({"monster_id": monster_id, "quantity": 1, "confirmed": True})
    monsters.append(
        _monster(30, "Board", active_skill=_skill(30, 71, [3, -1], initial=8))
    )
    rows.append({"monster_id": 30, "quantity": 1, "confirmed": True})
    store.load_monsters(monsters)
    _box(store, rows)
    report = shortlist_box(store, 1, skill_index={})
    void = _bucket(report, "void_pierce")
    ids = [row["form_id"] for row in void["candidates"]]
    assert 1 in ids
    assert len(ids) == 8
    assert ids[0] == 1
    board = _bucket(report, "full_light_board")
    assert [row["form_id"] for row in board["candidates"]] == [30]
    assert "skill_boost" not in {bucket["key"] for bucket in report["buckets"]}


def test_shortlist_orders_skill_boost_and_recommends_awoken_assists(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Leader",
                awakenings=[21],
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 202, [2], initial=10),
            ),
            _monster(2, "Combat"),
            _monster(3, "Low", awakenings=[21], active_skill=_skill(3, 191, [1], initial=5)),
            _monster(
                4,
                "High",
                awakenings=[56, 56],
                active_skill=_skill(4, 191, [1], initial=5),
            ),
            _monster(
                80,
                "Equip",
                awakenings=[49, 56],
                active_skill=_skill(80, 173, [2, 1, 0, 1], initial=8),
            ),
            _monster(81, "Not equip", active_skill=_skill(81, 173, [2, 1, 0, 1], initial=1)),
        ]
    )
    _box(
        store,
        [
            {"monster_id": monster_id, "quantity": 1, "confirmed": True}
            for monster_id in (1, 3, 4, 80, 81)
        ],
    )
    report = shortlist_box(store, 1, skill_index={})
    boosts = [row["form_id"] for row in _bucket(report, "skill_boost")["candidates"]]
    assert boosts[0] == 1
    assert boosts.index(4) < boosts.index(3)
    void = _bucket(report, "void_pierce")
    low = next(row for row in void["candidates"] if row["form_id"] == 3)
    assert low["also_fights_for"]
    assist_ids = [row["form_id"] for row in low["assists"]]
    assert 80 in assist_ids
    assert 81 not in assist_ids
    assert all(row["owned_id"] != 3 for row in low["assists"])
    equip = next(row for row in low["assists"] if row["form_id"] == 80)
    assert "attribute_absorb" in equip["adds"]
    assert "damage_absorb" in equip["adds"]
