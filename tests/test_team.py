"""The pool lists candidates. The check accepts a named team and does not search."""

import json
from contextlib import closing
from pathlib import Path

import pytest

from padbox.database.store import Store
from padbox.mechanics.coverage import cover_box
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
        # TB-13: a 5-turn full board does not keep an ordinary link up. One turn does.
        _monster(10, "Answer", active_skill=_skill(1, 116, [2, 3, 4, 5, 6], initial=1)),
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
    assert grown[21]["assumed_materials"] == [{"monster_id": 900, "name": "Jewel"}]
    # TB-18: these counts are monster-ID groups. The old names are aliases.
    assert report["omitted_groups"] == 1
    assert report["candidate_groups"] == 2
    assert report["omitted_copies"] == report["omitted_groups"]
    assert report["candidate_copies"] == report["candidate_groups"]
    assert report["requirements"]["sub_slots"] == 4
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
    assert report["mechanics_coverage"] == "pass"
    assert report["team_structure_valid"] is True
    # TB-01: one sub and no helper used to set ok true. That team is partial.
    assert report["team_complete"] is False
    assert report["strategic_quality"] == "not_evaluated"
    assert report["ok"] is False
    assert report["investments"] == []
    assert report["skill_boost"]["badge"] == "excluded"


def _linked_store(tmp_path: Path, cooldown: int, copies: int) -> Store:
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader", leader_skill=_skill(10, 138, [9])),
            _monster(10, "Answer", active_skill=_skill(1, 116, [2, 3, 4, 5, 6], initial=cooldown)),
            _monster(50, "Helper"),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 10, "quantity": copies, "confirmed": True},
        ],
    )
    return store


def test_five_subs_without_a_helper_are_not_a_standard_team(tmp_path: Path):
    # TB-01: five subs and no helper used to be accepted as a full set of slots.
    store = _linked_store(tmp_path, cooldown=1, copies=5)
    report = check_team(
        store,
        1,
        {"subs": [{"owned_id": 10, "form_id": 10} for _ in range(5)]},
        skill_index=_index(),
    )
    assert report["team_structure_valid"] is False
    assert report["team_complete"] is False
    assert report["ownership_valid"] is True
    assert report["mechanics_coverage"] == "pass"
    assert any("does not become a fifth sub" in problem for problem in report["problems"])
    assert report["ok"] is False


def test_four_subs_without_a_helper_stay_partial(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=4)
    report = check_team(
        store,
        1,
        {"subs": [{"owned_id": 10, "form_id": 10} for _ in range(4)]},
        skill_index=_index(),
    )
    assert report["problems"] == []
    assert report["open_holes"] == []
    assert report["team_structure_valid"] is True
    assert report["team_complete"] is False
    assert report["ownership_valid"] is True
    # TB-12: pass, fail, or unknown, so a special evolution requirement cannot pass silently.
    assert report["evolution_feasible"] == "pass"
    # No transform is pass. An unresolved transform is unknown, not a zero-turn pass.
    assert report["transform_feasible"] == "pass"
    assert report["mechanics_coverage"] == "pass"
    assert report["strategic_quality"] == "not_evaluated"
    assert report["ok"] is False


def test_a_leader_four_subs_and_a_helper_can_pass_the_hard_checks(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=4)
    report = check_team(
        store,
        1,
        {
            "helper_id": 50,
            "subs": [{"owned_id": 10, "form_id": 10} for _ in range(4)],
        },
        skill_index=_index(),
    )
    assert report["problems"] == []
    assert report["open_holes"] == []
    assert report["unknowns"] == []
    assert report["unknown_effects"] == []
    assert report["ownership_valid"] is True
    # TB-12: pass, fail, or unknown, so a special evolution requirement cannot pass silently.
    assert report["evolution_feasible"] == "pass"
    assert report["team_structure_valid"] is True
    assert report["team_complete"] is True
    # No transform is pass. An unresolved transform is unknown, not a zero-turn pass.
    assert report["transform_feasible"] == "pass"
    assert report["mechanics_coverage"] == "pass"
    assert report["strategic_quality"] == "not_evaluated"
    assert report["ok"] is True


def test_one_slow_full_board_does_not_keep_an_ordinary_link_up(tmp_path: Path):
    # TB-13: one 5-turn mono-board used to close the named-team color hole.
    store = _linked_store(tmp_path, cooldown=5, copies=1)
    report = check_team(
        store,
        1,
        {"subs": [{"owned_id": 10, "form_id": 10}]},
        skill_index=_index(),
    )
    assert any("do not keep a full board" in hole for hole in report["open_holes"])
    assert report["mechanics_coverage"] == "fail"
    assert report["team_complete"] is False
    assert report["ok"] is False


def _bind_result(report: dict) -> str:
    return next(
        row["result"] for row in report["decoder_requirements"] if row["name"] == "bind clear"
    )


def test_unresolved_transform_is_unknown_not_a_zero_turn_pass(tmp_path: Path):
    # TB-02: a transform relationship with no decoded transform active used to pass.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Base",
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 191, [1], initial=1),
            ),
            _monster(2, "Combat"),
        ]
    )
    _box(store, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    report = check_team(store, 2, {"subs": []}, skill_index={})
    boost = report["skill_boost"]
    assert boost["required"] is None
    assert boost["meets"] is False
    assert boost["result"] == "unknown"
    assert report["transform_feasible"] == "unknown"
    assert any("unresolved" in hole for hole in report["open_holes"])
    assert report["ok"] is False


def test_unresolved_helper_keeps_the_leader_transform_cost(tmp_path: Path):
    # TB-02: an unresolved helper used to null the leader's known cooldown.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Leader",
                awakenings=[21] * 12,
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 202, [2], initial=10),
            ),
            _monster(2, "Combat"),
            _monster(
                50,
                "Helper",
                relationships=[Relationship(target_id=51, kind="transform")],
                active_skill=_skill(7, 191, [1], initial=5),
            ),
            _monster(51, "Helper combat"),
        ]
    )
    _box(store, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    report = check_team(store, 2, {"helper_id": 50, "subs": []}, skill_index={})
    boost = report["skill_boost"]
    assert boost["required"] == 10
    assert boost["total"] >= 10
    assert boost["meets"] is False
    assert boost["result"] == "unknown"
    by_where = {member["where"]: member for member in boost["members"]}
    assert by_where["Leader"]["result"] == "pass"
    assert by_where["Leader"]["required_turns"] == 10
    assert by_where["Helper"]["result"] == "unknown"
    assert any("still requires 10" in hole for hole in report["open_holes"])
    covered = cover_box(store, 2, helper_id=50, skill_index={})
    assert covered["transform"]["required_turns"] == 10
    assert covered["transform"]["result"] == "unknown"
    assert covered["transform"]["ceiling"]["meets_requirement"] is False


def test_missing_component_index_does_not_pass_a_transform(tmp_path: Path):
    # TB-02: a component missing from the skill index is unknown, not "no transform".
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Base",
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 116, [99], initial=10),
            ),
            _monster(2, "Combat"),
        ]
    )
    _box(store, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    report = check_team(store, 2, {"subs": []}, skill_index={})
    assert report["transform_feasible"] == "unknown"
    assert report["skill_boost"]["meets"] is False
    assert report["skill_boost"]["result"] == "unknown"


def test_selected_sub_transform_can_cost_more_than_the_leader(tmp_path: Path):
    # TB-03: Saphira #7095 → #7096 is a 28-turn entry transform beside a 24-turn leader.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Leader",
                awakenings=[21] * 24,
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 202, [2], initial=28, levels=5),
            ),
            _monster(2, "Combat"),
            _monster(
                7095,
                "Saphira base",
                relationships=[Relationship(target_id=7096, kind="transform")],
                active_skill=_skill(3, 202, [7096], initial=28),
            ),
            _monster(7096, "Saphira"),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 7095, "quantity": 1, "confirmed": True},
        ],
    )
    report = check_team(
        store,
        2,
        {"subs": [{"owned_id": 7095, "form_id": 7096}]},
        skill_index={},
    )
    boost = report["skill_boost"]
    assert boost["total"] == 24
    assert boost["required"] == 28
    assert boost["meets"] is False
    assert boost["result"] == "fail"
    by_where = {member["where"]: member for member in boost["members"]}
    assert by_where["Leader"]["required_turns"] == 24
    assert by_where["Leader"]["result"] == "pass"
    assert by_where["Sub 1"]["required_turns"] == 28
    assert by_where["Sub 1"]["result"] == "fail"
    assert report["transform_feasible"] == "fail"


def test_transformed_sub_under_a_non_transform_leader_is_not_free(tmp_path: Path):
    # TB-03: a 99-turn transformed sub used to pass with zero skill boost.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader"),
            _monster(
                20,
                "Base",
                relationships=[Relationship(target_id=21, kind="transform")],
                active_skill=_skill(1, 202, [21], initial=99),
            ),
            _monster(21, "Battle"),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 20, "quantity": 1, "confirmed": True},
        ],
    )
    report = check_team(
        store,
        1,
        {"subs": [{"owned_id": 20, "form_id": 21}]},
        skill_index={},
    )
    assert report["skill_boost"]["required"] == 99
    assert report["skill_boost"]["total"] == 0
    assert report["skill_boost"]["meets"] is False
    assert report["transform_feasible"] == "fail"


def test_entry_active_does_not_cover_roles_after_transform(tmp_path: Path):
    # TB-04: the entry active and the transformed active are alternative states.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Base",
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 116, [3, 4], initial=5),
            ),
            _monster(2, "Combat", active_skill=_skill(2, 191, [4], initial=4)),
        ]
    )
    _box(store, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    index = {3: _raw(3, 117, [9999, 0, 0, 0, 9999]), 4: _raw(4, 202, [2])}
    report = check_team(store, 2, {"subs": []}, skill_index=index)
    assert _bind_result(report) == "fail"
    assert any("No chosen card has a verified bind clear" in hole for hole in report["open_holes"])


def test_inherited_active_replaces_the_base_active(tmp_path: Path):
    # TB-04: an assist and its host are not simultaneous coverage.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader"),
            _monster(
                10,
                "Sub",
                active_skill=_skill(1, 117, [9999, 0, 0, 0, 9999], initial=1),
            ),
            _monster(
                80,
                "Assist",
                awakenings=[49],
                active_skill=_skill(2, 191, [1], initial=1),
            ),
        ]
    )
    _box(
        store,
        [
            {"monster_id": monster_id, "quantity": 1, "confirmed": True}
            for monster_id in (1, 10, 80)
        ],
    )
    bare = check_team(store, 1, {"subs": [{"owned_id": 10, "form_id": 10}]}, skill_index={})
    assisted = check_team(
        store,
        1,
        {"subs": [{"owned_id": 10, "form_id": 10, "assist": {"owned_id": 80, "form_id": 80}}]},
        skill_index={},
    )
    assert _bind_result(bare) == "pass"
    assert _bind_result(assisted) == "fail"


def test_pool_group_counts_stay_separate_from_copy_quantities(tmp_path: Path):
    # TB-18: duplicate quantities must not be reported as extra monster-ID groups.
    store = _linked_store(tmp_path, cooldown=1, copies=3)
    _replace_box(
        store,
        [
            {"monster_id": 1, "quantity": 2, "confirmed": True},
            {"monster_id": 10, "quantity": 3, "confirmed": True},
            {"monster_id": 30, "quantity": 4, "confirmed": True},
        ],
    )
    store.load_monsters([_monster(30, "Unused")])
    report = pool_box(store, 1, skill_index=_index())
    assert report["candidate_groups"] == 2
    assert report["omitted_groups"] == 1
    assert report["candidate_copies"] == 2
    assert report["omitted_copies"] == 1
    answer = next(row for row in report["candidates"] if row["owned_id"] == 10)
    assert answer["copies"] == 3


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
    assert spent["ownership_valid"] is False
    assert any("used 2 times" in problem for problem in spent["problems"])
    assert any("mutually exclusive" in problem for problem in spent["problems"])
    leader_use = next(use for use in spent["copy_uses"] if use["kind"] == "reserved_leader")
    assert leader_use["owned_id"] == 13966
    assert leader_use["battle_form_id"] == 13967
    assist_use = next(use for use in spent["copy_uses"] if use["kind"] == "assist")
    assert assist_use["owned_id"] == 13966
    assert assist_use["form_id"] == 13968
    assert spent["investments"][0]["form_id"] == 13968
    assert spent["investments"][0]["spends"] == []
    assert any(
        item["kind"] == "stackable_material" and item["monster_id"] == 900
        for item in spent["assumptions"]
    )

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
    assert report["evolution_feasible"] == "pass"
    assert report["investments"][0]["where"] == "Sub 1"
    assert report["investments"][0]["path"] == ["evolution"]
    assert report["investments"][0]["spends"] == []
    stackable = next(
        item for item in report["assumptions"] if item["kind"] == "stackable_material"
    )
    assert stackable["monster_id"] == 900
    assert stackable["exact"] is False
    skill = next(item for item in report["assumptions"] if item["kind"] == "max_skill")
    assert skill["exact"] is False
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


def test_leader_evolution_investment_is_listed_with_the_subs(tmp_path: Path):
    # TB-19: a leader reached by evolution used to be missing from investments.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Prevo", relationships=[Relationship(target_id=2, kind="evolution")]),
            _monster(2, "Leader", raw={"evoMaterials": [9]}),
            _monster(9, "Specific"),
            _monster(20, "Base", relationships=[Relationship(target_id=21, kind="evolution")]),
            _monster(21, "Sub", raw={"evoMaterials": [30]}),
            _monster(30, "Sub material"),
        ]
    )
    _box(
        store,
        [
            {"monster_id": monster_id, "quantity": 1, "confirmed": True}
            for monster_id in (1, 9, 20, 30)
        ],
    )
    report = check_team(
        store,
        2,
        {"subs": [{"owned_id": 20, "form_id": 21}]},
        skill_index={},
    )
    assert report["ownership_valid"] is True
    assert report["evolution_feasible"] == "pass"
    leader, sub = report["investments"]
    assert leader["where"] == "Leader"
    assert leader["owned_id"] == 1
    assert leader["form_id"] == 2
    assert leader["path"] == ["evolution"]
    assert leader["spends"] == [9]
    assert sub["where"] == "Sub 1"
    assert sub["owned_id"] == 20
    assert sub["form_id"] == 21
    assert sub["spends"] == [30]
    assert report["copy_uses"][0]["kind"] == "reserved_leader"
    assert report["copy_uses"][0]["owned_id"] == 1


def test_equal_cost_routes_and_cumulative_materials(tmp_path: Path):
    # TB-12: an equal-spend route stays available when the other material is in use.
    store = Store(tmp_path / "routes")
    store.load_monsters(
        [
            _monster(
                1,
                "Base",
                relationships=[
                    Relationship(target_id=4, kind="evolution"),
                    Relationship(target_id=5, kind="evolution"),
                ],
            ),
            _monster(
                4,
                "Mid A",
                relationships=[Relationship(target_id=2, kind="evolution")],
                raw={"evoMaterials": [8]},
            ),
            _monster(
                5,
                "Mid B",
                relationships=[Relationship(target_id=2, kind="evolution")],
                raw={"evoMaterials": [9]},
            ),
            _monster(2, "Leader"),
            _monster(8, "Material A"),
            _monster(9, "Material B"),
        ]
    )
    _box(
        store,
        [
            {"monster_id": monster_id, "quantity": 1, "confirmed": True}
            for monster_id in (1, 8, 9)
        ],
    )
    open_route = check_team(
        store, 2, {"subs": [{"owned_id": 8, "form_id": 8}]}, skill_index={}
    )
    assert open_route["ownership_valid"] is True
    assert open_route["evolution_feasible"] == "pass"
    assert open_route["investments"][0]["where"] == "Leader"
    assert open_route["investments"][0]["spends"] == [9]
    blocked = check_team(
        store,
        2,
        {"subs": [{"owned_id": 8, "form_id": 8}, {"owned_id": 9, "form_id": 9}]},
        skill_index={},
    )
    assert blocked["evolution_feasible"] == "fail"
    assert any("evolution material" in problem for problem in blocked["problems"])

    shared = Store(tmp_path / "shared")
    shared.load_monsters(
        [
            _monster(1, "Leader"),
            _monster(10, "Base A", relationships=[Relationship(target_id=11, kind="evolution")]),
            _monster(11, "Evo A", raw={"evoMaterials": [8]}),
            _monster(20, "Base B", relationships=[Relationship(target_id=21, kind="evolution")]),
            _monster(21, "Evo B", raw={"evoMaterials": [8]}),
            _monster(8, "Material"),
        ]
    )
    _box(
        shared,
        [
            {"monster_id": monster_id, "quantity": 1, "confirmed": True}
            for monster_id in (1, 10, 20, 8)
        ],
    )
    report = check_team(
        shared,
        1,
        {
            "subs": [
                {"owned_id": 10, "form_id": 11},
                {"owned_id": 20, "form_id": 21},
            ]
        },
        skill_index={},
    )
    assert report["ownership_valid"] is True
    assert report["evolution_feasible"] == "fail"
    assert any(
        "required 2 times as evolution material" in problem for problem in report["problems"]
    )


def test_special_evolution_requirements_stay_unknown(tmp_path: Path):
    # TB-12: ordeal id 65535 and a missing material id used to delete the path.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader"),
            _monster(10, "Base", relationships=[Relationship(target_id=11, kind="evolution")]),
            _monster(11, "Ordeal", raw={"evoMaterials": [65535]}),
            _monster(20, "Other", relationships=[Relationship(target_id=21, kind="evolution")]),
            _monster(21, "Missing", raw={"evoMaterials": [424242]}),
        ]
    )
    _box(
        store,
        [
            {"monster_id": monster_id, "quantity": 1, "confirmed": True}
            for monster_id in (1, 10, 20)
        ],
    )
    ordeal = check_team(
        store, 1, {"subs": [{"owned_id": 10, "form_id": 11}]}, skill_index={}
    )
    assert ordeal["problems"] == []
    assert ordeal["evolution_feasible"] == "unknown"
    assert ordeal["ok"] is False
    assert ordeal["investments"][0]["spends"] == []
    assert ordeal["special_requirements"][0]["monster_id"] == 65535
    assert not any(item["kind"] == "stackable_material" for item in ordeal["assumptions"])
    missing = check_team(
        store, 1, {"subs": [{"owned_id": 20, "form_id": 21}]}, skill_index={}
    )
    assert missing["evolution_feasible"] == "unknown"
    assert missing["special_requirements"][0]["monster_id"] == 424242
    assert "reference set" in missing["special_requirements"][0]["reason"]


def test_evolution_depth_and_one_transform_step(tmp_path: Path):
    store = Store(tmp_path / "depth")
    monsters = []
    for monster_id in range(1, 11):
        relationships = []
        if monster_id < 10:
            relationships.append(Relationship(target_id=monster_id + 1, kind="evolution"))
        monsters.append(_monster(monster_id, f"Step {monster_id}", relationships=relationships))
    store.load_monsters(monsters)
    _box(store, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    report = pool_box(store, 1, skill_index={})
    ids = {form["form_id"] for form in _forms(report, 1)}
    assert 9 in ids
    assert 10 not in ids

    chained = Store(tmp_path / "chain")
    chained.load_monsters(
        [
            _monster(
                1,
                "Base",
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 202, [2], initial=5),
            ),
            _monster(
                2,
                "Once",
                relationships=[Relationship(target_id=3, kind="transform")],
                active_skill=_skill(2, 202, [3], initial=5),
            ),
            _monster(3, "Twice"),
        ]
    )
    _box(chained, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    forms = {form["form_id"]: form for form in _forms(pool_box(chained, 1, skill_index={}), 1)}
    assert forms[2]["stage"] == "battle"
    assert forms[2]["path"] == ["transform"]
    assert 3 not in forms


def test_transform_base_is_one_copy(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader"),
            _monster(
                10,
                "Base",
                relationships=[
                    Relationship(target_id=11, kind="transform"),
                    Relationship(target_id=12, kind="assist_evolution"),
                ],
                active_skill=_skill(1, 202, [11], initial=5),
            ),
            _monster(11, "Battle", active_skill=_skill(2, 191, [1], initial=1)),
            _monster(
                12,
                "Equip",
                awakenings=[49],
                active_skill=_skill(3, 191, [1], initial=1),
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
    battle = check_team(
        store, 1, {"subs": [{"owned_id": 10, "form_id": 11}]}, skill_index={}
    )
    assert battle["ownership_valid"] is True
    assert battle["problems"] == []
    use = next(item for item in battle["copy_uses"] if item["where"] == "Sub 1")
    assert use["kind"] == "transform_base"
    assert use["owned_id"] == 10
    assert use["form_id"] == 11
    both = check_team(
        store,
        1,
        {
            "subs": [
                {
                    "owned_id": 10,
                    "form_id": 11,
                    "assist": {"owned_id": 10, "form_id": 12},
                }
            ]
        },
        skill_index={},
    )
    assert both["ownership_valid"] is False
    assert any("used 2 times" in problem for problem in both["problems"])


def test_pinned_alternate_forms_need_a_spare_copy(tmp_path: Path):
    # TB-08: one reserved copy used to pin every form of that card into the bucket.
    def build(quantity: int) -> dict:
        store = Store(tmp_path / str(quantity))
        monsters = [
            _monster(
                1,
                "Leader",
                awakenings=[21],
                relationships=[
                    Relationship(target_id=2, kind="transform"),
                    Relationship(target_id=3, kind="assist_evolution"),
                ],
                active_skill=_skill(1, 191, [1], initial=8),
            ),
            _monster(2, "Combat", active_skill=_skill(2, 191, [1], initial=4)),
            _monster(
                3,
                "Equip",
                awakenings=[49],
                active_skill=_skill(3, 191, [1], initial=4),
            ),
        ]
        rows = [{"monster_id": 1, "quantity": quantity, "confirmed": True}]
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
        store.load_monsters(monsters)
        _box(store, rows)
        return shortlist_box(store, 1, skill_index={})

    sole = [row["form_id"] for row in _bucket(build(1), "void_pierce")["candidates"]]
    assert sole[0] == 1
    assert 2 not in sole
    assert 3 not in sole
    assert len(sole) == 8
    spare = [row["form_id"] for row in _bucket(build(2), "void_pierce")["candidates"]]
    assert spare[0] == 1
    assert 2 in spare
    assert 3 in spare
    assert len(spare) == 8


def test_skill_readiness_separates_base_cooldown_from_max_skill(tmp_path: Path):
    # TB-15: the box has no per-copy skill level or awakening unlock.
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Leader",
                awakenings=[21],
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 202, [2], initial=28, levels=5),
            ),
            _monster(2, "Combat"),
        ]
    )
    _box(store, [{"monster_id": 1, "quantity": 1, "confirmed": True}])
    report = check_team(store, 1, {"subs": []}, skill_index={})
    assert report["investments"] == []
    assert report["skill_boost"]["required"] == 24
    assert report["skill_boost"]["required_at_base_skill"] == 28
    assert report["skill_boost"]["basis"] == {
        "cooldown": "max_skill",
        "awakenings": "all_reference_normal_awakenings",
        "exact": False,
    }
    kinds = {item["kind"] for item in report["assumptions"]}
    assert {"max_skill", "normal_awakenings"} <= kinds
    assert all(item["exact"] is False for item in report["assumptions"])


_BATTLE_FIELDS = {"score", "damage", "healing", "survival", "optimal", "ranked_teams"}


def test_battle_quality_stays_unevaluated(tmp_path: Path):
    # TB-05: a passing checklist and an available cross are not a battle result.
    store = _linked_store(tmp_path / "complete", cooldown=1, copies=4)
    report = check_team(
        store,
        1,
        {
            "helper_id": 50,
            "subs": [{"owned_id": 10, "form_id": 10} for _ in range(4)],
        },
        skill_index=_index(),
    )
    assert report["ok"] is True
    assert report["team_complete"] is True
    assert report["strategic_quality"] == "not_evaluated"
    assert _BATTLE_FIELDS.isdisjoint(report)

    cross = Store(tmp_path / "cross")
    cross.load_monsters(
        [
            _monster(10, "Leader", leader_skill=_skill(1, 138, [2, 3])),
            _monster(11, "Column", active_skill=_skill(4, 116, [5], initial=1)),
            _monster(12, "Board", active_skill=_skill(6, 116, [7], initial=1)),
        ]
    )
    _box(
        cross,
        [
            {"monster_id": 10, "quantity": 1, "confirmed": True},
            {"monster_id": 11, "quantity": 1, "confirmed": True},
            {"monster_id": 12, "quantity": 1, "confirmed": True},
        ],
    )
    index = {
        2: _raw(2, 182, [8, 5, 100, 83]),
        3: _raw(3, 271, [8, 20000, 0, 4, 17000000]),
        5: _raw(5, 127, [32, 8]),
        7: _raw(7, 71, [3, -1]),
    }
    covered = cover_box(cross, 10, skill_index=index)
    color = covered["color_force"][0]
    assert color["match"] == "cross"
    assert color["status"] == "available"
    assert color["loop"] is None
    assert _BATTLE_FIELDS.isdisjoint(covered)
    checked = check_team(
        cross,
        10,
        {"subs": [{"owned_id": 11, "form_id": 11}]},
        skill_index=index,
    )
    cross_req = next(row for row in checked["decoder_requirements"] if "cross" in row["name"])
    assert cross_req["result"] == "pass"
    assert checked["strategic_quality"] == "not_evaluated"
    assert _BATTLE_FIELDS.isdisjoint(checked)


def test_mechanics_does_not_name_an_optimal_team(tmp_path: Path):
    # TB-06: pool, shortlist, and check-team do not score or search.
    store = _linked_store(tmp_path, cooldown=1, copies=4)
    report = check_team(
        store,
        1,
        {
            "helper_id": 50,
            "subs": [{"owned_id": 10, "form_id": 10} for _ in range(4)],
        },
        skill_index=_index(),
    )
    assert report["ok"] is True
    assert report["strategic_quality"] == "not_evaluated"
    assert _BATTLE_FIELDS.isdisjoint(report)
    pool = pool_box(store, 1, skill_index=_index())
    assert any("name a best team" in line for line in pool["contract"])
    assert _BATTLE_FIELDS.isdisjoint(pool)
    short = shortlist_box(store, 1, skill_index=_index())
    assert any("does not name a team" in line for line in short["contract"])
    assert _BATTLE_FIELDS.isdisjoint(short)
    for bucket in short["buckets"]:
        for row in bucket["candidates"]:
            assert "score" not in row
    from padbox.cli import app

    names = {command.name for command in app.registered_commands}
    assert {"optimize", "score", "team"}.isdisjoint(names)
    team_group = next(group for group in app.registered_groups if group.name == "team")
    assert {"evaluate", "optimize", "compare"} <= {
        command.name for command in team_group.typer_instance.registered_commands
    }


def test_team_optimize_refuses_a_dungeon_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    from typer.testing import CliRunner

    from padbox.cli import app

    dungeon = tmp_path / "dungeon.json"
    dungeon.write_text("{}")
    result = CliRunner().invoke(
        app,
        [
            "--home",
            str(tmp_path / "home"),
            "team",
            "optimize",
            "--leader",
            "1",
            "--dungeon",
            str(dungeon),
        ],
    )
    assert result.exit_code != 0
    assert "team optimize" in result.output
    assert "not read a dungeon profile" in result.output
