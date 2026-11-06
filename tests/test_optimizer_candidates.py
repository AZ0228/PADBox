"""Candidate generation from owned copies.

Apollo #7844 is the TB-07 case: a decoded two-turn Light conversion plus an
unrelated unverified effect. The pool keeps that card. An unverified effect
is an uncertainty score, and every removed row records why it was removed.
"""

import json
from contextlib import closing
from pathlib import Path

from padbox.database.store import Store
from padbox.models import Monster, Provenance, Relationship, Skill
from padbox.optimizer import generate_candidates

ROZE = 13967
ROZE_BASE = 13966
APOLLO = 7844
CARDS = Path(__file__).resolve().parent / "fixtures" / "cards"


def _raw(skill_id: int, kind: int, params: list[int], initial: int | None = None) -> dict:
    raw: dict = {
        "id": skill_id,
        "type": kind,
        "params": params,
        "name": "",
        "description": "",
    }
    if initial is not None:
        raw["initialCooldown"] = initial
        raw["maxLevel"] = 1
    return raw


def _skill(
    skill_id: int, kind: int, params: list[int], initial: int | None = None, levels: int = 1
) -> Skill:
    raw = _raw(skill_id, kind, params, initial)
    minimum = None
    if initial:
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


def _convert(skill_id: int, initial: int) -> Skill:
    return _skill(skill_id, 116, [1540], initial=initial)


def _index() -> dict[int, dict]:
    return {
        2: _raw(2, 196, [9999]),
        3: _raw(3, 117, [9999, 0, 0, 0, 9999]),
        4: _raw(4, 146, [2, 2]),
        5: _raw(5, 202, [13967]),
        12: _raw(12, 196, [9999]),
        13: _raw(13, 117, [0, 0, 0, 0, 9999]),
        14: _raw(14, 191, [1]),
        21: _raw(21, 182, [8, 5, 100, 83]),
        22: _raw(22, 271, [8, 20000, 0, 4, 17000000]),
        31: _raw(31, 173, [2, 1, 0, 1]),
        401: _raw(401, 178, [8]),
        801: _raw(801, 999, []),
        1540: _raw(1540, 154, [48, 8]),
    }


def _collection(tmp_path: Path) -> tuple[Store, dict[int, dict]]:
    """A Rozé leader plus the forms the pruner has to keep or explain."""
    store = Store(tmp_path)
    index = _index()
    store.load_monsters(
        [
            _monster(
                ROZE_BASE,
                "Base",
                awakenings=[21],
                relationships=[
                    Relationship(target_id=ROZE, kind="transform"),
                    Relationship(target_id=13968, kind="assist_evolution"),
                ],
                active_skill=_skill(1, 116, [2, 3, 4, 5], initial=28, levels=5),
                raw={"attrs": [3], "syncAwakening": 138},
            ),
            _monster(
                ROZE,
                "Combat",
                awakenings=[48],
                active_skill=_skill(11, 116, [12, 13, 14], initial=4),
                leader_skill=_skill(20, 138, [21, 22]),
                raw={"attrs": [3]},
            ),
            _monster(
                13968,
                "Assist",
                awakenings=[49, 21],
                active_skill=_skill(30, 116, [31], initial=18, levels=11),
                raw={"attrs": [3], "evoMaterials": [900]},
            ),
            _monster(900, "Jewel", raw={"stackable": True, "attrs": [3]}),
            _monster(
                20,
                "Slow",
                awakenings=[28],
                relationships=[Relationship(target_id=21, kind="evolution")],
                active_skill=_convert(2000, 8),
                raw={"attrs": [3]},
            ),
            _monster(
                21,
                "Fast form",
                awakenings=[21, 21],
                active_skill=_convert(2100, 8),
                raw={"attrs": [3], "evoMaterials": []},
            ),
            _monster(
                30,
                "Duplicate",
                awakenings=[21],
                active_skill=_convert(3000, 4),
                raw={"attrs": [3]},
            ),
            _monster(
                40,
                "Off color",
                active_skill=_skill(4000, 116, [401], initial=5),
                raw={"attrs": [0]},
            ),
            _monster(
                50,
                "Friend",
                awakenings=[21],
                leader_skill=_skill(5000, 138, [21]),
                raw={"attrs": [3]},
            ),
            _monster(
                60,
                "Near",
                awakenings=[28],
                relationships=[Relationship(target_id=61, kind="evolution")],
                active_skill=_convert(6000, 6),
                raw={"attrs": [3]},
            ),
            _monster(61, "Distant", raw={"attrs": [3], "evoMaterials": [62]}),
            _monster(62, "Material", raw={"attrs": [3], "stackable": False}),
            _monster(
                70,
                "Dark leader",
                awakenings=[21],
                active_skill=_convert(7000, 5),
                leader_skill=_skill(7100, 138, [21]),
                raw={"attrs": [4]},
            ),
            _monster(
                80,
                "Undecoded",
                active_skill=_skill(8000, 116, [801]),
                raw={"attrs": [0]},
            ),
            _monster(
                99,
                "Unowned",
                awakenings=[21],
                leader_skill=_skill(9900, 138, [21]),
                raw={"attrs": [3]},
            ),
        ]
    )
    _box(
        store,
        [
            {"monster_id": ROZE_BASE, "quantity": 2, "confirmed": True},
            {"monster_id": 20, "quantity": 1, "confirmed": True},
            {"monster_id": 30, "quantity": 2, "confirmed": True},
            {"monster_id": 40, "quantity": 1, "confirmed": True},
            {"monster_id": 60, "quantity": 1, "confirmed": True},
            {"monster_id": 70, "quantity": 1, "confirmed": True},
            {"monster_id": 80, "quantity": 1, "confirmed": True},
        ],
    )
    return store, index


def _of(pool, *, form_id: int, kind: str) -> list:
    return [row for row in pool.candidates if row.form_id == form_id and row.kind == kind]


def _regression_index() -> dict[int, dict]:
    index: dict[int, dict] = {}
    for path in sorted(CARDS.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for skill in record["skills"]:
            index[int(skill["id"])] = skill
    return index


def test_apollo_survives_into_the_roze_pool(regression_store):
    """A faster fully decoded Light card does not push Apollo #7844 out."""
    index = _regression_index()
    index[9001] = _raw(9001, 154, [48, 8])
    regression_store.load_monsters(
        [
            _monster(
                9001,
                "Fast Light",
                attributes=[3],
                awakenings=[21, 21],
                active_skill=_skill(9000, 116, [9001], initial=1),
                raw={"attrs": [3]},
            )
        ]
    )
    _box(
        regression_store,
        [
            {"monster_id": ROZE_BASE, "quantity": 1, "confirmed": True},
            {"monster_id": APOLLO, "quantity": 2, "confirmed": True},
            {"monster_id": 9001, "quantity": 1, "confirmed": True},
        ],
    )
    pool = generate_candidates(regression_store, ROZE, skill_index=index)
    apollo = _of(pool, form_id=APOLLO, kind="sub")
    fast = _of(pool, form_id=9001, kind="sub")
    assert {row.copy_index for row in apollo} == {0, 1}
    assert fast
    assert fast[0].uncertainty == 0
    assert fast[0].minimum_cooldown is not None
    assert apollo[0].minimum_cooldown is not None
    assert fast[0].minimum_cooldown < apollo[0].minimum_cooldown
    assert apollo[0].uncertainty > 0
    assert "light_orbs" in apollo[0].answers
    assert any(effect.source_type == 230 for effect in apollo[0].unknown_effects)
    assert all(row.owned_id == APOLLO for row in apollo)
    assert not any(row.form_id == APOLLO for row in pool.pruned)
    leader = next(row for row in pool.candidates if row.kind == "leader")
    assert leader.owned_id == ROZE_BASE
    assert leader.form_id == ROZE
    assert leader.copy_index == 0
    assert "transform" in leader.path
    assert pool.reserved_owned_id == ROZE_BASE
    assert any("uncertainty score" in note for note in pool.notes)
    assert all(row.borrowed is False for row in pool.candidates)
    for row in pool.pruned:
        assert "unknown" not in row.detail.lower()
        assert "undecoded" not in row.detail.lower()


def test_pool_keeps_reachable_forms_and_records_every_prune(tmp_path: Path):
    store, index = _collection(tmp_path)
    pool = generate_candidates(store, ROZE, helper_id=50, skill_index=index)

    leader = next(row for row in pool.candidates if row.kind == "leader")
    assert leader.form_id == ROZE
    assert leader.owned_id == ROZE_BASE
    assert leader.stage == "battle"
    assert not any(
        row.owned_id == ROZE_BASE and row.copy_index == 0 and row.kind != "leader"
        for row in pool.candidates
    )

    transform = _of(pool, form_id=ROZE, kind="sub")
    assert transform
    assert transform[0].copy_index == 1
    assert "transform" in transform[0].path

    assist = _of(pool, form_id=13968, kind="assist")
    assert assist
    assert assist[0].owned_id == ROZE_BASE
    assert assist[0].copy_index == 1
    assert assist[0].availability == "reachable"
    assert assist[0].equip is True

    evolved = _of(pool, form_id=21, kind="sub")
    assert evolved
    assert evolved[0].owned_id == 20
    assert evolved[0].availability == "reachable"
    assert "evolution" in evolved[0].path
    assert not _of(pool, form_id=20, kind="sub")

    duplicates = _of(pool, form_id=30, kind="sub")
    assert {row.copy_index for row in duplicates} == {0, 1}
    assert all(row.owned_id == 30 for row in duplicates)

    friend = _of(pool, form_id=50, kind="helper")
    assert friend
    assert friend[0].borrowed is True
    assert friend[0].helper_origin == "explicit"
    owned_helper = _of(pool, form_id=ROZE, kind="helper")
    assert owned_helper
    assert owned_helper[0].borrowed is False
    assert owned_helper[0].helper_origin == "owned"
    assert owned_helper[0].copy_index == 1

    dark = _of(pool, form_id=70, kind="sub")
    assert dark
    assert dark[0].uncertainty == 0
    assert not _of(pool, form_id=70, kind="helper")

    undecoded = _of(pool, form_id=80, kind="sub")
    assert undecoded
    assert undecoded[0].uncertainty > 0
    assert not any(row.form_id == 80 for row in pool.pruned)

    assert all(row.form_id != 99 and row.owned_id != 99 for row in pool.candidates)
    assert all(row.form_id != 99 for row in pool.pruned)

    reasons = {row.reason for row in pool.pruned}
    assert reasons == {
        "hard_incompatibility",
        "unreachable",
        "ownership_conflict",
        "missing_essential_role",
        "dominated",
    }
    unreachable = next(row for row in pool.pruned if row.form_id == 61)
    assert unreachable.reason == "unreachable"
    assert unreachable.owned_id == 60
    assert "#62" in unreachable.detail
    dominated = next(row for row in pool.pruned if row.form_id == 20)
    assert dominated.reason == "dominated"
    assert dominated.dominated_by == 21
    assert "unknown" not in dominated.detail.lower()
    missing = next(row for row in pool.pruned if row.form_id == 40)
    assert missing.reason == "missing_essential_role"
    incompatible = next(row for row in pool.pruned if row.form_id == 70 and row.kind == "helper")
    assert incompatible.reason == "hard_incompatibility"
    reserved = next(
        row
        for row in pool.pruned
        if row.owned_id == ROZE_BASE and row.copy_index == 0 and row.form_id == 13968
    )
    assert reserved.reason == "ownership_conflict"
    for row in pool.pruned:
        assert row.reason in reasons
        assert "unknown" not in row.detail.lower()
        assert "undecoded" not in row.detail.lower()
