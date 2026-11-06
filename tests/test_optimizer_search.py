"""Staged beam search over the owned pool.

Finalists are complete teams. Each has four subs, one helper, and no copy
used twice. ``evaluate_team`` runs only for the beam that the cheap screen
kept. A helper is owned or explicitly supplied.
"""

import json
from contextlib import closing

from padbox.database.store import Store
from padbox.evaluator.dungeon import DungeonProfile
from padbox.mechanics.result import CheckResult
from padbox.optimizer import DEFAULT_BEAM_WIDTH, search_teams

ROZE = 13967
ROZE_BASE = 13966
APOLLO = 7844
GAIA = 8336
VENUS = 10756
PALADIN = 7949
LEONA = 7712


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


def _copies(team) -> list[tuple[int, int]]:
    used = [(sub.owned_id, sub.copy_index) for sub in team.subs]
    if not team.helper.borrowed:
        used.append((team.helper.owned_id, team.helper.copy_index))
    used.append((ROZE_BASE, 0))
    return used


def test_beam_width_defaults_to_200():
    assert DEFAULT_BEAM_WIDTH == 200


def test_search_simulates_only_the_diverse_beam(regression_store):
    ids = [13966, 13967, 7844, 8336, 10756, 7949, 7095, 7096, 4834, 4206, 3939, 3821, 7712]
    _box(regression_store, [(monster_id, 1) for monster_id in ids])
    result = search_teams(
        regression_store,
        ROZE,
        objective="general",
        beam_width=2,
        seed=0,
        turns=1,
        simulations=1,
    )
    assert result.beam_width == 2
    assert result.seed == 0
    assert result.turns == 1
    assert result.simulations == 1
    assert result.complete_teams_scored > len(result.ranked)
    assert 1 <= len(result.ranked) <= 2
    assert len(result.beam) == len(result.ranked)
    assert "not a global optimum" in result.guarantee
    for team in result.ranked:
        assert len(team.subs) == 4
        assert team.evaluation is not None
        assert team.evaluation.scenario.seed == 0
        assert team.evaluation.scenario.turns == 1
        assert team.evaluation.scenario.simulations == 1
        assert team.evaluation.scenario.name == "general"
        assert team.helper.supply == "owned"
        assert team.helper.borrowed is False
        copies = _copies(team)
        assert len(copies) == len(set(copies))
        assert (ROZE_BASE, 0) not in {(sub.owned_id, sub.copy_index) for sub in team.subs}
    again = search_teams(
        regression_store,
        ROZE,
        objective="general",
        beam_width=2,
        seed=0,
        turns=1,
        simulations=1,
    )
    assert [
        (team.helper.form_id, [(sub.owned_id, sub.form_id) for sub in team.subs])
        for team in again.ranked
    ] == [
        (team.helper.form_id, [(sub.owned_id, sub.form_id) for sub in team.subs])
        for team in result.ranked
    ]


def test_a_dungeon_profile_is_the_search_scenario(regression_store):
    ids = [13966, 13967, 7844, 8336, 10756, 7949, 7095, 7096, 4834, 4206, 3939, 3821, 7712]
    _box(regression_store, [(monster_id, 1) for monster_id in ids])
    profile = DungeonProfile.model_validate(
        {
            "name": "Example Dungeon",
            "floors": [
                {
                    "enemy_hp": 100,
                    "preemptive_damage": 10,
                    "mechanics": ["damage_absorb", "resolve"],
                }
            ],
        }
    )
    result = search_teams(
        regression_store,
        ROZE,
        helper_id=PALADIN,
        dungeon=profile,
        beam_width=1,
        seed=0,
        turns=1,
        simulations=1,
    )
    assert result.dungeon == profile
    assert any("resolve is unmodeled" in note for note in result.notes)
    assert "not a reason to drop a card" in " ".join(result.notes)
    assert "does not say a team clears a floor" in result.guarantee
    assert result.ranked, result.notes
    for team in result.ranked:
        assert team.evaluation is not None
        assert team.evaluation.scenario.dungeon == profile
        floor = team.evaluation.dungeon.floors[0]
        by_name = {item.mechanic: item for item in floor.requirements}
        assert by_name["resolve"].modeled is False
        assert by_name["resolve"].result is CheckResult.UNKNOWN
        assert by_name["damage_absorb"].modeled is True
        assert floor.handled is False
        assert any("not cleared" in line for line in floor.uncertainties)


def test_explicit_helper_is_marked_supplied_even_when_unowned(regression_store):
    _box(
        regression_store,
        [(ROZE_BASE, 1), (APOLLO, 1), (GAIA, 1), (VENUS, 1), (PALADIN, 1)],
    )
    result = search_teams(
        regression_store,
        ROZE,
        helper_id=LEONA,
        beam_width=5,
        seed=0,
        turns=1,
        simulations=1,
    )
    assert result.ranked
    for team in result.ranked:
        assert len(team.subs) == 4
        assert team.helper.form_id == LEONA
        assert team.helper.supply == "explicit"
        assert team.helper.borrowed is True
        assert LEONA not in {sub.owned_id for sub in team.subs}
        assert team.evaluation is not None


def test_two_owned_copies_can_both_be_subs(regression_store):
    _box(
        regression_store,
        [
            (ROZE_BASE, 1),
            (APOLLO, 2),
            (GAIA, 1),
            (VENUS, 1),
            (PALADIN, 1),
        ],
    )
    result = search_teams(
        regression_store,
        ROZE,
        helper_id=PALADIN,
        beam_width=10,
        seed=1,
        turns=1,
        simulations=1,
    )
    assert result.ranked
    paired = [
        team
        for team in result.beam
        if {sub.copy_index for sub in team.subs if sub.owned_id == APOLLO} == {0, 1}
    ]
    assert paired
    for team in result.ranked:
        assert team.helper.form_id == PALADIN
        assert team.helper.supply == "explicit"
        assert team.helper.borrowed is False
        assert len(_copies(team)) == len(set(_copies(team)))
        assert not any(sub.owned_id == PALADIN for sub in team.subs)
