"""The top tested team beats the others under the stated objective and scope."""

import json
from contextlib import closing

from typer.testing import CliRunner

from padbox.cli import app
from padbox.database.store import Store
from padbox.evaluator.score import OBJECTIVE_ORDER
from padbox.mechanics.result import CheckResult
from padbox.optimizer import search_teams

ROZE = 13967
FIXTURE_IDS = [
    13966,
    13967,
    7844,
    8336,
    10756,
    7949,
    7095,
    7096,
    4834,
    4206,
    3939,
    3821,
    7712,
]


def _box(store: Store) -> None:
    document = json.dumps(
        {
            "schema_version": 1,
            "server": "NA",
            "monsters": [
                {"monster_id": monster_id, "quantity": 1, "confirmed": True}
                for monster_id in FIXTURE_IDS
            ],
            "unresolved": [],
            "complete": True,
        }
    )
    with closing(store.connect()) as db, db:
        db.execute("INSERT INTO box_state VALUES (1, 1, ?, NULL)", (document,))


def _key(team, objective: str) -> tuple:
    """The evaluator order, recomputed here so the report cannot grade itself."""
    report = team.evaluation
    feasibility = {CheckResult.PASS: 0, CheckResult.UNKNOWN: 1, CheckResult.FAIL: 2}[
        report.feasibility
    ]
    by_name = {component.name: component for component in report.components}
    terms = []
    for name in OBJECTIVE_ORDER[objective]:
        component = by_name.get(name)
        if component is None or component.value is None:
            terms.append((1, 0.0))
        else:
            terms.append((0, -float(component.value)))
    return (feasibility, *terms, team.uncertainty, tuple(team.cheap_score))


def _gap(left_key: tuple, right_key: tuple, objective: str) -> str:
    if left_key == right_key:
        return "tie"
    if left_key[0] != right_key[0]:
        return "feasibility"
    order = OBJECTIVE_ORDER[objective]
    for offset, name in enumerate(order, start=1):
        if left_key[offset] != right_key[offset]:
            return name
    if left_key[1 + len(order)] != right_key[1 + len(order)]:
        return "uncertainty"
    return "cheap_screen"


def test_team_optimize_cli_returns_ranked_reports(regression_store, monkeypatch):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    _box(regression_store)
    result = CliRunner().invoke(
        app,
        [
            "--home",
            str(regression_store.home),
            "team",
            "optimize",
            "--leader",
            str(ROZE),
            "--objective",
            "general",
            "--beam-width",
            "2",
            "--seed",
            "0",
            "--turns",
            "1",
            "--simulations",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["leader_id"] == ROZE
    assert payload["beam_width"] == 2
    assert "not a global optimum" in payload["guarantee"]
    assert payload["ranked"]
    top = payload["ranked"][0]["report"]
    assert top["rank"] == 1
    assert {
        "lineup",
        "helper",
        "assists",
        "role_coverage",
        "transform_plan",
        "skill_rotation",
        "activation",
        "survival",
        "damage",
        "weaknesses",
        "unknowns",
        "selected_because",
        "tradeoffs",
        "scope",
    } <= set(top)
    assert "Objective general" in top["scope"]
    assert "not a global optimum" in top["scope"]


def test_top_fixture_team_beats_the_other_tested_teams(regression_store):
    _box(regression_store)
    result = search_teams(
        regression_store,
        ROZE,
        objective="general",
        beam_width=4,
        seed=0,
        turns=1,
        simulations=1,
    )
    ranked = result.ranked
    assert len(ranked) >= 2
    assert result.complete_teams_scored >= len(ranked)
    assert "not a global optimum" in result.guarantee
    keys = [_key(team, "general") for team in ranked]
    assert keys == sorted(keys)
    assert all(keys[0] < key for key in keys[1:])
    top = ranked[0].report
    assert top is not None
    assert top.rank == 1
    assert "Objective general" in top.scope
    assert "seed 0" in top.scope
    assert "1 turn," in top.scope
    assert "1 simulation," in top.scope
    assert "beam width 4" in top.scope
    assert "not a global optimum" in top.scope
    assert top.scope in top.selected_because
    assert {row.other_rank for row in top.tradeoffs} == {team.report.rank for team in ranked[1:]}
    assert all(row.ahead and not row.tied for row in top.tradeoffs)
    for other, key in zip(ranked[1:], keys[1:], strict=True):
        gap = _gap(keys[0], key, "general")
        reason = next(row.reason for row in top.tradeoffs if row.other_rank == other.report.rank)
        if gap == "cheap_screen":
            assert "cheap screen" in reason.lower()
        elif gap == "uncertainty":
            assert "uncertainty" in reason.lower()
        elif gap == "feasibility":
            assert "feasibility" in reason.lower()
        else:
            assert gap in reason
    for team in ranked:
        report = team.report
        assert report is not None
        assert len(team.subs) == 4
        roles = [card.role for card in report.lineup]
        assert roles[0] == "leader"
        assert roles[-1] == "helper"
        assert sum(role.startswith("sub ") for role in roles) == 4
        assert report.helper.form_id == team.helper.form_id
        assert report.helper.supply in {"owned", "explicit"}
        assert isinstance(report.assists, list)
        assert report.role_coverage
        assert report.transform_plan
        assert any("leader, subs" in line for line in report.skill_rotation)
        assert [line.name for line in report.activation] == [
            "leader_activation",
            "offensive_activation",
            "defensive_activation",
            "orb_generation_uptime",
            "longest_activation_gap",
        ]
        assert "unresolved" in report.survival.lower()
        assert "unresolved" in report.damage.lower()
        assert report.weaknesses
        assert isinstance(report.unknowns, list)
        assert "not a global optimum" in report.scope
