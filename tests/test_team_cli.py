"""CLI reports for the fixture collection.

``padbox team`` prints the text sections unless ``--json`` is set.
``padbox evaluate`` and ``padbox check-team`` keep printing JSON.
"""

import json
from contextlib import closing
from pathlib import Path

from typer.testing import CliRunner

from padbox.cli import app

ROZE = 13967
LEONA = 7712
GAIA = 8336
APOLLO = 7844
VENUS = 10756
PALADIN = 7949
CIEL = 4834
KUSHI = 4206
RATHIOS = 3939
PIXEL = 3821
_HEADINGS = (
    "TEAM EVALUATION",
    "Legality",
    "Structure:",
    "Ownership:",
    "Evolution:",
    "Transformation:",
    "Activation",
    "Offensive activation:",
    "Defensive activation:",
    "Orb supply:",
    "Longest activation gap:",
    "Utility",
    "Damage absorption:",
    "Attribute absorption:",
    "Damage void:",
    "Awoken bind:",
    "Unmatchable:",
    "Survivability",
    "Effective HP:",
    "Healing:",
    "Shield uptime:",
    "Damage",
    "Estimated output:",
    "Damage cap limitations:",
    "Uncertainty",
    "Unsupported mechanics:",
    "Unverified assumptions:",
    "Recommendations",
    "Ranked alternatives:",
    "Suggested assists:",
    "Skill rotation:",
)


def _box(store) -> None:
    cards = Path(__file__).resolve().parent / "fixtures" / "cards"
    document = json.dumps(
        {
            "schema_version": 1,
            "server": "NA",
            "monsters": [
                {"monster_id": int(path.stem), "quantity": 1, "confirmed": True}
                for path in cards.glob("*.json")
            ],
            "unresolved": [],
            "complete": True,
        }
    )
    with closing(store.connect()) as db, db:
        db.execute("INSERT INTO box_state VALUES (1, 1, ?, NULL)", (document,))


def _team(path: Path, subs: list[int], helper: int = LEONA) -> Path:
    path.write_text(
        json.dumps(
            {
                "leader_id": ROZE,
                "helper_id": helper,
                "subs": [{"owned_id": monster_id, "form_id": monster_id} for monster_id in subs],
            }
        )
    )
    return path


def _invoke(store, *args: str):
    return CliRunner().invoke(app, ["--home", str(store.home), *args])


def _sections(text: str) -> None:
    cursor = 0
    for heading in _HEADINGS:
        found = text.find(heading, cursor)
        assert found != -1, heading
        cursor = found + len(heading)


def test_team_evaluate_prints_sections_and_json(regression_store, tmp_path, monkeypatch):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    _box(regression_store)
    team = _team(tmp_path / "roze.json", [GAIA, APOLLO, VENUS, PALADIN])
    output = tmp_path / "report.json"
    text = _invoke(
        regression_store,
        "team",
        "evaluate",
        "--team",
        str(team),
        "--seed",
        "4",
        "--turns",
        "1",
        "--simulations",
        "1",
        "--output",
        str(output),
    )
    assert text.exit_code == 0, text.output
    assert not text.stdout.lstrip().startswith("{")
    _sections(text.stdout)
    assert "Seed: 4" in text.stdout
    assert "Turns: 1" in text.stdout
    assert "Simulations: 1" in text.stdout
    assert "Activation basis: policy_approximation" in text.stdout
    assert "policy approximation" in text.stdout
    assert "No alternative team was evaluated" in text.stdout
    assert "Evolving skill with 2 stages" in text.stdout
    assert "optimal" not in text.stdout.lower()
    saved = json.loads(output.read_text())
    assert saved["seed"] == 4
    assert saved["scenario"]["turns"] == 1
    assert saved["scenario"]["simulations"] == 1
    assert saved["ruleset"]["activation_basis"] == "policy_approximation"

    raw = _invoke(
        regression_store,
        "team",
        "evaluate",
        "--team",
        str(team),
        "--seed",
        "4",
        "--turns",
        "1",
        "--simulations",
        "1",
        "--json",
    )
    assert raw.exit_code == 0, raw.output
    payload = json.loads(raw.stdout)
    assert payload["seed"] == 4
    assert payload["scenario"]["name"] == "general"
    assert payload["feasibility"] in {"pass", "fail", "unknown"}
    assert "TEAM EVALUATION" not in raw.stdout


def test_team_compare_prints_both_teams_and_selects_neither(
    regression_store, tmp_path, monkeypatch
):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    _box(regression_store)
    left = _team(tmp_path / "roze.json", [GAIA, APOLLO, VENUS, PALADIN])
    right = _team(tmp_path / "earlier.json", [CIEL, KUSHI, RATHIOS, PIXEL])
    text = _invoke(
        regression_store,
        "team",
        "compare",
        str(left),
        str(right),
        "--seed",
        "5",
        "--turns",
        "1",
        "--simulations",
        "1",
    )
    assert text.exit_code == 0, text.output
    assert text.stdout.count("TEAM EVALUATION") == 2
    left_text, right_text = text.stdout.split("Right:", maxsplit=1)
    _sections(left_text)
    _sections("TEAM EVALUATION" + right_text.split("TEAM EVALUATION", maxsplit=1)[1])
    assert "Seed: 5" in text.stdout
    assert "Turns: 1" in text.stdout
    assert "Simulations: 1" in text.stdout
    assert "neither team is selected" in text.stdout
    assert text.stdout.count("Selected team: none") == 2
    assert "optimal" not in text.stdout.lower()

    raw = _invoke(
        regression_store,
        "team",
        "compare",
        str(left),
        str(right),
        "--seed",
        "5",
        "--turns",
        "1",
        "--simulations",
        "1",
        "--json",
    )
    assert raw.exit_code == 0, raw.output
    payload = json.loads(raw.stdout)
    assert payload["selected_team"] is None
    assert payload["seed"] == 5
    assert payload["turns"] == 1
    assert payload["simulations"] == 1
    assert payload["left_report"]["seed"] == 5
    assert payload["right_report"]["scenario"]["simulations"] == 1


def test_team_optimize_text_lists_scope_and_rejections(regression_store, monkeypatch):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    _box(regression_store)
    text = _invoke(
        regression_store,
        "team",
        "optimize",
        "--leader",
        str(ROZE),
        "--helper",
        str(LEONA),
        "--objective",
        "general",
        "--beam-width",
        "1",
        "--seed",
        "6",
        "--turns",
        "1",
        "--simulations",
        "1",
    )
    assert text.exit_code == 0, text.output
    assert text.stdout.startswith("TEAM OPTIMIZATION")
    _sections(text.stdout)
    assert "Seed: 6" in text.stdout
    assert "Turns: 1" in text.stdout
    assert "Simulations: 1" in text.stdout
    assert "Beam width: 1" in text.stdout
    assert "not a global optimum" in text.stdout
    assert "Rejected candidates" in text.stdout
    assert "(explicit)" in text.stdout

    raw = _invoke(
        regression_store,
        "team",
        "optimize",
        "--leader",
        str(ROZE),
        "--helper",
        str(LEONA),
        "--objective",
        "general",
        "--beam-width",
        "1",
        "--seed",
        "6",
        "--turns",
        "1",
        "--simulations",
        "1",
        "--json",
    )
    assert raw.exit_code == 0, raw.output
    payload = json.loads(raw.stdout)
    assert payload["seed"] == 6
    assert payload["turns"] == 1
    assert payload["simulations"] == 1
    assert payload["ranked"]
    assert payload["ranked"][0]["helper"]["supply"] == "explicit"
    assert not any(row["form_id"] == APOLLO for row in payload["pruned"])
    for row in payload["pruned"]:
        assert row["reason"]
        assert row["detail"]
        assert f"#{row['form_id']}" in text.stdout
        assert row["reason"] in text.stdout


def test_check_team_still_prints_validation_json(regression_store, tmp_path, monkeypatch):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    _box(regression_store)
    team = _team(tmp_path / "partial.json", [GAIA])
    result = _invoke(regression_store, "check-team", str(ROZE), str(team))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ownership_valid"] in {True, False}
    assert payload["team_complete"] is False
    assert payload["strategic_quality"] == "not_evaluated"
    assert "team_structure_valid" in payload
    assert "evolution_feasible" in payload
    assert "transform_feasible" in payload
    assert "mechanics_coverage" in payload
    assert "unknowns" in payload
    assert "ok" in payload
    assert "TEAM EVALUATION" not in result.stdout


def test_recognition_evaluate_still_prints_json(store, fixtures, monkeypatch):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    result = _invoke(store, "evaluate", str(fixtures["labels"]))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "NOT real PAD" in payload["dataset"]
    assert payload["known_examples"] == 45
    assert payload["top1_accuracy"] == 1
    assert "TEAM EVALUATION" not in result.stdout


def test_team_help_lists_the_three_commands(tmp_path, monkeypatch):
    monkeypatch.setattr("padbox.cli.logging.basicConfig", lambda **kwargs: None)
    home = ["--home", str(tmp_path / "home")]
    result = CliRunner().invoke(app, [*home, "team", "--help"])
    assert result.exit_code == 0, result.output
    for name in ("evaluate", "optimize", "compare"):
        assert name in result.stdout
    help_text = _command_help(home)
    assert "--json" in help_text
    assert "--seed" in help_text
    assert "--simulations" in help_text
    assert "--turns" in help_text


def _command_help(home: list[str]) -> str:
    pages = [
        CliRunner().invoke(app, [*home, "team", name, "--help"])
        for name in ("evaluate", "optimize", "compare")
    ]
    assert all(page.exit_code == 0 for page in pages)
    return "".join(page.stdout for page in pages)
