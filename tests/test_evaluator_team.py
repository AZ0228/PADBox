"""Hard-constraint evaluation of one supplied team.

The report reuses check_team. An unresolved requirement stays unknown, and a
known miss stays a miss beside it. The run records its ruleset, scenario,
seed, and data provenance. It does not score the team.
"""

import json
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from padbox.database.store import Store
from padbox.evaluator import evaluate_team
from padbox.mechanics.decode import DECODER_VERSION
from padbox.mechanics.result import CheckResult
from padbox.models import Monster, Provenance, Relationship, Skill


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


def _subs(count: int) -> list[dict[str, int]]:
    return [{"owned_id": 10, "form_id": 10} for _ in range(count)]


def _constraint(report, name: str):
    return next(item for item in report.constraints if item.name == name)


def _component(report, name: str):
    return next(item for item in report.components if item.name == name)


def _metric(report, name: str):
    return next(item for item in report.simulation.metrics if item.name == name)


def _fast(**extra: object) -> dict:
    scenario = {"turns": 2, "simulations": 2}
    scenario.update(extra)
    return scenario


def test_complete_team_passes_and_records_the_run(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=4)
    report = evaluate_team(
        {"leader_id": 1, "helper_id": 50, "subs": _subs(4)},
        {
            "name": "farming",
            "seed": 11,
            "turns": 3,
            "simulations": 2,
            "requirements": ["awoken_bind"],
        },
        store=store,
        skill_index=_index(),
    )
    assert report.feasibility is CheckResult.PASS
    assert report.strategic_quality == "unresolved"
    assert report.rank.objective == "farming"
    assert report.rank.order == [
        "activation_consistency",
        "flexibility",
        "survivability",
        "damage",
    ]
    assert report.rank.score is None
    activation = _component(report, "activation_consistency")
    leader = _metric(report, "leader_activation")
    gap = _metric(report, "longest_activation_gap")
    assert activation.value == leader.value
    assert activation.gap == gap.value
    assert activation.status == "resolved"
    assert _component(report, "flexibility").value == 0.5
    assert "unused_utility=3" in _component(report, "flexibility").detail
    assert _component(report, "survivability").status == "unresolved"
    assert _component(report, "damage").status == "unresolved"
    coverage = _component(report, "mechanic_coverage")
    assert coverage.value == _metric(report, "mechanic_response_availability").value
    assert coverage.status == "resolved"
    assert report.team_complete is True
    assert report.team_structure_valid is True
    assert report.ownership_valid is True
    assert report.evolution_feasible is CheckResult.PASS
    assert report.leader_legality is CheckResult.PASS
    assert report.helper_legality is CheckResult.PASS
    assert report.transform_feasible is CheckResult.PASS
    assert report.mechanics_coverage is CheckResult.PASS
    assert report.board_supported is True
    assert report.seed == 11
    assert report.scenario.name == "farming"
    assert report.scenario.turns == 3
    assert report.scenario.simulations == 2
    assert report.ruleset.matching_policy == "greedy"
    assert report.ruleset.activation_basis == "policy_approximation"
    assert report.ruleset.board_columns == 6
    assert report.ruleset.board_rows == 5
    assert report.provenance.reference_source == "test"
    assert report.provenance.reference_version == "1"
    assert report.provenance.box_revision == 1
    assert report.provenance.box_source_scan is None
    assert report.provenance.skill_index == "supplied"
    assert report.provenance.decoder_version == DECODER_VERSION
    assert "explicitly" in _constraint(report, "helper_legality").detail
    awoken = _constraint(report, "scenario:awoken_bind")
    assert awoken.result is CheckResult.PASS
    assert awoken.detail == "Decoder requirement 'awoken bind clear' is pass."
    assert report.validation["ok"] is True
    assert report.unknowns == []


def test_defaults_record_the_general_scenario(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=4)
    report = evaluate_team(
        {"leader_id": 1, "helper_id": 50, "subs": _subs(4)},
        store=store,
        skill_index=_index(),
    )
    assert report.scenario.name == "general"
    assert report.seed == 0
    assert report.scenario.turns == 10
    assert report.scenario.simulations == 1000
    assert report.ruleset.board_columns == 6
    assert report.ruleset.board_rows == 5


def test_missing_skill_index_does_not_pass_decoder_requirements(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=4)
    report = evaluate_team(
        {"leader_id": 1, "helper_id": 50, "subs": _subs(4)},
        _fast(),
        store=store,
    )
    assert report.provenance.skill_index == "missing"
    assert report.mechanics_coverage is CheckResult.UNKNOWN
    assert report.feasibility is CheckResult.UNKNOWN
    requirements = [item for item in report.constraints if item.name.startswith("requirement:")]
    assert requirements
    assert all(item.result is CheckResult.UNKNOWN for item in requirements)


def test_five_subs_without_a_helper_are_not_feasible(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=5)
    report = evaluate_team(
        {"leader_id": 1, "subs": _subs(5)},
        _fast(),
        store=store,
        skill_index=_index(),
    )
    assert report.team_structure_valid is False
    assert report.team_complete is False
    assert report.ownership_valid is True
    assert report.leader_legality is CheckResult.PASS
    assert report.helper_legality is CheckResult.FAIL
    assert report.feasibility is CheckResult.FAIL
    assert "fifth sub" in _constraint(report, "team_structure").detail
    assert "not assumed" in _constraint(report, "helper_legality").detail
    assert report.strategic_quality == "excluded"
    assert report.rank.score is None


def test_one_sub_stays_partial(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=1)
    report = evaluate_team(
        {"leader_id": 1, "subs": _subs(1)},
        _fast(),
        store=store,
        skill_index=_index(),
    )
    assert report.team_structure_valid is True
    assert report.team_complete is False
    assert _constraint(report, "team_structure").result is CheckResult.PASS
    assert _constraint(report, "team_complete").result is CheckResult.FAIL
    assert report.feasibility is CheckResult.FAIL


def test_unowned_leader_fails_legality(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=1)
    with closing(store.connect()) as db, db:
        document = json.dumps(
            {
                "schema_version": 1,
                "server": "NA",
                "monsters": [{"monster_id": 10, "quantity": 1, "confirmed": True}],
                "unresolved": [],
                "complete": True,
            }
        )
        db.execute("UPDATE box_state SET document=? WHERE id=1", (document,))
    report = evaluate_team(
        {"leader_id": 1, "subs": _subs(1)},
        _fast(),
        store=store,
        skill_index=_index(),
    )
    assert report.leader_legality is CheckResult.FAIL
    assert report.ownership_valid is False
    assert report.feasibility is CheckResult.FAIL
    assert "No owned copy" in _constraint(report, "leader_legality").detail


def test_a_second_use_of_one_copy_fails_ownership_only(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=1)
    report = evaluate_team(
        {"leader_id": 1, "helper_id": 50, "subs": _subs(2)},
        _fast(),
        store=store,
        skill_index=_index(),
    )
    assert report.ownership_valid is False
    assert report.leader_legality is CheckResult.PASS
    assert "used 2 times" in _constraint(report, "ownership").detail
    assert report.feasibility is CheckResult.FAIL


def test_unresolved_transform_stays_unknown_on_a_complete_team(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=4)
    store.load_monsters(
        [
            _monster(
                50,
                "Helper",
                relationships=[Relationship(target_id=51, kind="transform")],
                active_skill=_skill(7, 191, [1], initial=5),
            ),
            _monster(51, "Helper combat"),
        ]
    )
    report = evaluate_team(
        {
            "leader_id": 1,
            "helper_id": 50,
            "subs": _subs(4),
        },
        _fast(requirements=["awoken_bind", "resolve"]),
        store=store,
        skill_index=_index(),
    )
    assert report.team_complete is True
    assert report.mechanics_coverage is CheckResult.PASS
    assert report.transform_feasible is CheckResult.UNKNOWN
    assert _constraint(report, "transform:Helper").result is CheckResult.UNKNOWN
    assert _constraint(report, "scenario:awoken_bind").result is CheckResult.PASS
    assert _constraint(report, "scenario:resolve").result is CheckResult.UNKNOWN
    assert report.feasibility is CheckResult.UNKNOWN
    assert any("Helper transform cooldown is unresolved" in line for line in report.unknowns)
    assert any("scenario:resolve" in line for line in report.unknowns)
    assert not any(item.result is CheckResult.FAIL for item in report.constraints)
    assert _component(report, "mechanic_coverage").status == "unresolved"


def test_known_transform_miss_stays_beside_an_unresolved_helper(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(
                1,
                "Leader",
                awakenings=[21],
                relationships=[Relationship(target_id=2, kind="transform")],
                active_skill=_skill(1, 202, [2], initial=20),
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
    report = evaluate_team(
        {"leader_id": 2, "helper_id": 50, "subs": []},
        _fast(),
        store=store,
        skill_index={},
    )
    assert _constraint(report, "transform:Leader").result is CheckResult.FAIL
    assert _constraint(report, "transform:Helper").result is CheckResult.UNKNOWN
    assert report.transform_feasible is CheckResult.FAIL
    assert report.feasibility is CheckResult.FAIL
    assert any("Helper transform cooldown is unresolved" in line for line in report.unknowns)
    members = {row["where"]: row for row in report.validation["skill_boost"]["members"]}
    leader = members["Leader"]
    helper = members["Helper"]
    assert leader["result"] == "fail"
    assert leader["required_turns"] == 20
    assert helper["result"] == "unknown"


def test_special_evolution_requirement_stays_unknown(tmp_path: Path):
    store = Store(tmp_path)
    store.load_monsters(
        [
            _monster(1, "Leader"),
            _monster(10, "Base", relationships=[Relationship(target_id=11, kind="evolution")]),
            _monster(11, "Ordeal", raw={"evoMaterials": [65535]}),
        ]
    )
    _box(
        store,
        [
            {"monster_id": 1, "quantity": 1, "confirmed": True},
            {"monster_id": 10, "quantity": 1, "confirmed": True},
        ],
    )
    report = evaluate_team(
        {"leader_id": 1, "subs": [{"owned_id": 10, "form_id": 11}]},
        _fast(),
        store=store,
        skill_index={},
    )
    assert report.evolution_feasible is CheckResult.UNKNOWN
    assert _constraint(report, "evolution").result is CheckResult.UNKNOWN
    assert report.feasibility is CheckResult.FAIL
    assert report.validation["special_requirements"][0]["monster_id"] == 65535


def test_scenario_keeps_a_known_color_miss(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=5, copies=1)
    report = evaluate_team(
        {"leader_id": 1, "helper_id": 50, "subs": _subs(1)},
        _fast(requirements=["full light board", "resolve"]),
        store=store,
        skill_index=_index(),
    )
    assert _constraint(report, "scenario:full light board").result is CheckResult.FAIL
    assert _constraint(report, "scenario:resolve").result is CheckResult.UNKNOWN
    assert report.mechanics_coverage is CheckResult.FAIL
    assert report.feasibility is CheckResult.FAIL


def test_unsupported_board_fails_and_still_checks_the_team(tmp_path: Path):
    store = _linked_store(tmp_path, cooldown=1, copies=4)
    report = evaluate_team(
        {"leader_id": 1, "helper_id": 50, "subs": _subs(4)},
        ruleset={"board_columns": 5, "board_rows": 6},
        store=store,
        skill_index=_index(),
    )
    assert report.board_supported is False
    assert _constraint(report, "board").result is CheckResult.FAIL
    assert "unsupported" in _constraint(report, "board").detail
    assert report.ruleset.board_columns == 5
    assert report.ruleset.board_rows == 6
    assert report.team_complete is True
    assert report.ownership_valid is True
    assert report.feasibility is CheckResult.FAIL
    assert report.strategic_quality == "excluded"
    assert _component(report, "activation_consistency").status == "unresolved"
    assert report.simulation is None


def test_a_missing_box_or_leader_is_rejected(tmp_path: Path):
    empty = Store(tmp_path / "empty")
    with pytest.raises(ValueError, match="No applied box"):
        evaluate_team({"leader_id": 1}, store=empty)
    store = _linked_store(tmp_path / "cards", cooldown=1, copies=1)
    with pytest.raises(ValueError, match="not in the local reference set"):
        evaluate_team({"leader_id": 999}, store=store, skill_index=_index())


def test_ruleset_and_scenario_reject_unspecified_choices(tmp_path: Path):
    store = Store(tmp_path)
    with pytest.raises(ValidationError):
        evaluate_team({"leader_id": 1}, {"name": "ranked"}, store=store)
    with pytest.raises(ValidationError):
        evaluate_team({"leader_id": 1}, ruleset={"matching_policy": "solver"}, store=store)
    with pytest.raises(ValidationError):
        evaluate_team({"leader_id": 1}, {"turns": 0}, store=store)
