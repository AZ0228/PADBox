"""Assists on the shortlisted beam.

An assist spends a free copy. It does not replace a base active the timing
plan casts, and the assisted team is what ``evaluate_team`` ranks.
"""

import json
from contextlib import closing
from pathlib import Path

from padbox.database.store import Store
from padbox.mechanics.awakenings import NAMES
from padbox.models import Monster, Provenance, Skill
from padbox.optimizer import search_teams

ROZE = 13967
ROZE_BASE = 13966
HELPER = 10
FAST = 20
SLOW = 21
LIGHT_SUB = 30
FIRE_SUB = 31
EQUIP = 40
CLOUD = 41
DARK_CHANGE = 42
LIGHT_CHANGE = 43
CARDS = Path(__file__).resolve().parent / "fixtures" / "cards"
_DARK_SUB = next(i for i, name in NAMES.items() if name == "Change Sub Attribute: Dark")
_LIGHT_SUB = next(i for i, name in NAMES.items() if name == "Change Sub Attribute: Light")


def _raw(skill_id: int, kind: int, params: list[int], initial: int | None = None) -> dict:
    raw: dict = {"id": skill_id, "type": kind, "params": params, "name": "", "description": ""}
    if initial is not None:
        raw["initialCooldown"] = initial
        raw["maxLevel"] = 1
    return raw


def _skill(skill_id: int, kind: int, params: list[int], initial: int | None = None) -> Skill:
    raw = _raw(skill_id, kind, params, initial)
    minimum = None if not initial else initial
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


def _box(store: Store, rows: list[tuple[int, int]]) -> None:
    document = json.dumps(
        {
            "schema_version": 1,
            "server": "NA",
            "monsters": [
                {"monster_id": monster_id, "quantity": quantity, "confirmed": True}
                for monster_id, quantity in rows
            ],
            "unresolved": [],
            "complete": True,
        }
    )
    with closing(store.connect()) as db, db:
        db.execute("INSERT INTO box_state VALUES (1, 1, ?, NULL)", (document,))


def _index() -> dict[int, dict]:
    index: dict[int, dict] = {}
    for path in sorted(CARDS.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for skill in record["skills"]:
            index[int(skill["id"])] = skill
    index[101] = _raw(101, 154, [48, 8])
    index[201] = _raw(201, 178, [8])
    index[301] = _raw(301, 182, [8, 5, 100, 83])
    return index


def _load(store: Store, extras: list[Monster]) -> dict[int, dict]:
    store.load_monsters(
        [
            _monster(
                HELPER,
                "Helper",
                awakenings=[21],
                active_skill=_skill(200, 116, [201], initial=8),
                leader_skill=_skill(300, 138, [301]),
                raw={"attrs": [3]},
            ),
            _monster(
                FAST,
                "Fast light",
                active_skill=_skill(100, 116, [101], initial=1),
                raw={"attrs": [3]},
            ),
            _monster(
                SLOW,
                "Slow light",
                active_skill=_skill(110, 116, [101], initial=12),
                raw={"attrs": [3]},
            ),
            _monster(
                LIGHT_SUB,
                "Light sub",
                awakenings=[21],
                active_skill=_skill(210, 116, [201], initial=6),
                raw={"attrs": [3, 3]},
            ),
            _monster(
                FIRE_SUB,
                "Fire sub",
                awakenings=[21],
                active_skill=_skill(220, 116, [201], initial=6),
                raw={"attrs": [0, 0]},
            ),
            *extras,
        ]
    )
    return _index()


def _equip(monster_id: int, name: str, awakenings: list[int]) -> Monster:
    return _monster(
        monster_id,
        name,
        awakenings=awakenings,
        active_skill=_skill(400 + monster_id, 116, [201], initial=15),
        raw={"attrs": [3]},
    )


def _copies(team) -> list[tuple[int, int]]:
    used = [(ROZE_BASE, 0)]
    if not team.helper.borrowed:
        used.append((team.helper.owned_id, team.helper.copy_index))
    for sub in team.subs:
        used.append((sub.owned_id, sub.copy_index))
        if sub.assist is not None:
            used.append((sub.assist.owned_id, sub.assist.copy_index))
    for assist in (team.leader_assist, team.helper_assist):
        if assist is not None:
            used.append((assist.owned_id, assist.copy_index))
    return used


def _equipped(team) -> list[tuple[str, int]]:
    found = []
    if team.leader_assist is not None:
        found.append(("leader", team.leader_assist.form_id))
    for sub in team.subs:
        if sub.assist is not None:
            found.append((sub.form_name, sub.assist.form_id))
    if team.helper_assist is not None:
        found.append(("helper", team.helper_assist.form_id))
    return found


def test_rotation_active_is_kept_and_the_assist_is_reevaluated(regression_store):
    index = _load(regression_store, [_equip(EQUIP, "Boost equip", [49, 21, 21])])
    _box(
        regression_store,
        [
            (ROZE_BASE, 1),
            (HELPER, 1),
            (FAST, 1),
            (SLOW, 1),
            (LIGHT_SUB, 1),
            (FIRE_SUB, 1),
            (EQUIP, 1),
        ],
    )
    result = search_teams(
        regression_store,
        ROZE,
        beam_width=20,
        seed=0,
        turns=10,
        simulations=1,
        skill_index=index,
    )
    assisted = [
        team
        for team in result.ranked
        if any(form_id == EQUIP for _, form_id in _equipped(team))
    ]
    assert assisted
    for team in result.ranked:
        assert len(_copies(team)) == len(set(_copies(team)))
        assert team.evaluation is not None
        assert team.evaluation.scenario.simulations == 1
        assert team.leader_assist is None
    for team in assisted:
        assert team.evaluation is not None
        assert team.evaluation.ownership_valid is True
        hosts = {name for name, form_id in _equipped(team) if form_id == EQUIP}
        assert "Fast light" not in hosts
        assert any("rotation casts" in note and f"#{FAST}" in note for note in team.assist_notes)
        # Leader entry has 2, the helper 1, the two subs with Skill Boost 1 each, and the equip 2.
        assert team.evaluation.validation["skill_boost"]["total"] == 7


def test_a_copy_used_as_a_sub_is_not_also_the_assist(regression_store):
    index = _load(regression_store, [_equip(EQUIP, "Only equip", [49, 21])])
    _box(
        regression_store,
        [
            (ROZE_BASE, 1),
            (HELPER, 1),
            (FAST, 1),
            (SLOW, 1),
            (LIGHT_SUB, 1),
            (EQUIP, 1),
        ],
    )
    result = search_teams(
        regression_store,
        ROZE,
        beam_width=10,
        seed=0,
        turns=10,
        simulations=1,
        skill_index=index,
    )
    assert result.ranked
    for team in result.ranked:
        assert len(_copies(team)) == len(set(_copies(team)))
        assert any(sub.owned_id == EQUIP for sub in team.subs)
        assert _equipped(team) == []


def test_hazard_resist_and_attribute_change_choose_the_equip(regression_store):
    index = _load(
        regression_store,
        [
            _equip(CLOUD, "Cloud equip", [49, 54]),
            _equip(DARK_CHANGE, "Dark sub change", [49, _DARK_SUB]),
            _equip(LIGHT_CHANGE, "Light sub change", [49, _LIGHT_SUB]),
        ],
    )
    _box(
        regression_store,
        [
            (ROZE_BASE, 1),
            (HELPER, 1),
            (FAST, 1),
            (SLOW, 1),
            (LIGHT_SUB, 1),
            (FIRE_SUB, 1),
            (CLOUD, 1),
            (DARK_CHANGE, 1),
            (LIGHT_CHANGE, 1),
        ],
    )
    result = search_teams(
        regression_store,
        ROZE,
        beam_width=40,
        seed=0,
        turns=10,
        simulations=1,
        skill_index=index,
    )
    open_lineup = [
        team
        for team in result.ranked
        if {sub.form_id for sub in team.subs} == {FAST, SLOW, LIGHT_SUB, FIRE_SUB}
    ]
    assert len(open_lineup) == 1
    equipped = dict(_equipped(open_lineup[0]))
    assert equipped["Slow light"] == CLOUD
    assert equipped["Fire sub"] == LIGHT_CHANGE
    for team in result.ranked:
        assert len(_copies(team)) == len(set(_copies(team)))
        slots = _equipped(team)
        assert all(form_id != DARK_CHANGE for _, form_id in slots)
        assert all(name != "Fast light" for name, _form_id in slots)
        assert all(name != "Light sub" or form_id != DARK_CHANGE for name, form_id in slots)
        assert team.leader_assist is None
        assert team.evaluation is not None
        assert team.evaluation.ownership_valid is True
