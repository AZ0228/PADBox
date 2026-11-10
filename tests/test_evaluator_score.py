"""Hierarchical scores trace to their components.

The scalar is the objective tuple as a base-1001 integer. An unresolved
component removes the scalar. Redundant utility past the first extra copy
lowers flexibility.
"""

from padbox.evaluator.score import (
    OBJECTIVE_ORDER,
    ComponentScore,
    score_team,
)
from padbox.mechanics.result import CheckResult


def _resolved(name: str, value: float) -> ComponentScore:
    quality = score_team(
        "general",
        CheckResult.PASS,
        [],
        [],
        [],
        [],
        [],
    )
    found = next(item for item in quality.components if item.name == name)
    return found.model_copy(update={"value": value, "status": "resolved"})


def _ranked(objective: str, values: dict[str, float]):
    components = [
        _resolved(name, values.get(name, 0.0))
        if name in values
        else next(
            item
            for item in score_team(
                "general", CheckResult.PASS, [], [], [], [], []
            ).components
            if item.name == name
        )
        for name in (
            "activation_consistency",
            "survivability",
            "mechanic_coverage",
            "damage",
            "flexibility",
        )
    ]
    # Rebuild through the public rank by feeding metrics and slots is awkward
    # for a fully resolved tuple, so call score_team's rank inputs directly.
    from padbox.evaluator.score import _rank

    return _rank(objective, components)


def test_scalar_is_the_objective_tuple():
    rank = _ranked(
        "general",
        {
            "activation_consistency": 0.5,
            "survivability": 1,
            "damage": 0,
            "flexibility": 0.25,
        },
    )
    assert [term.rounded for term in rank.terms] == [500, 1000, 0, 250]
    assert [term.place for term in rank.terms] == [1001**3, 1001**2, 1001, 1]
    assert rank.score == 500 * 1001**3 + 1000 * 1001**2 + 250
    assert rank.score == sum(term.rounded * term.place for term in rank.terms)
    assert all(term.scale == 1 for term in rank.terms)


def test_an_earlier_component_outranks_a_perfect_remainder():
    higher = _ranked(
        "general",
        {
            "activation_consistency": 0.501,
            "survivability": 0,
            "damage": 0,
            "flexibility": 0,
        },
    )
    lower = _ranked(
        "general",
        {
            "activation_consistency": 0.5,
            "survivability": 1,
            "damage": 1,
            "flexibility": 1,
        },
    )
    assert higher.score is not None and lower.score is not None
    assert higher.score > lower.score


def test_half_up_rounding_stays_inside_one_place():
    rank = _ranked(
        "general",
        {
            "activation_consistency": 0.5005,
            "survivability": 0,
            "damage": 0,
            "flexibility": 0,
        },
    )
    assert rank.terms[0].rounded == 501


def test_unresolved_component_has_no_scalar_and_is_not_zero():
    quality = score_team("general", CheckResult.PASS, [], [], [], [], [])
    assert quality.strategic_quality == "unresolved"
    assert quality.rank.score is None
    survival = next(item for item in quality.components if item.name == "survivability")
    damage = next(item for item in quality.components if item.name == "damage")
    assert survival.value is None
    assert damage.value is None
    assert quality.rank.rank_key[1] is None
    assert 0 not in quality.rank.rank_key[1:3]


def test_a_failed_team_is_excluded():
    quality = score_team("general", CheckResult.FAIL, [], [], [], [], [])
    assert quality.strategic_quality == "excluded"
    assert quality.rank.score is None


def test_a_scalar_requires_passing_feasibility():
    from padbox.evaluator.score import _assemble

    components = [
        _resolved(name, 1.0)
        for name in (
            "activation_consistency",
            "survivability",
            "mechanic_coverage",
            "damage",
            "flexibility",
        )
    ]
    passed = _assemble("general", CheckResult.PASS, components)
    unknown = _assemble("general", CheckResult.UNKNOWN, components)
    failed = _assemble("general", CheckResult.FAIL, components)
    assert passed.strategic_quality == "ranked"
    assert passed.rank.score == 1000 * (1001**3 + 1001**2 + 1001 + 1)
    assert unknown.strategic_quality == "unresolved"
    assert unknown.rank.score is None
    assert unknown.rank.rank_key == [1.0, 1.0, 1.0, 1.0]
    assert failed.strategic_quality == "excluded"
    assert failed.rank.score is None


def test_objectives_reorder_the_same_four_metrics():
    assert OBJECTIVE_ORDER["general"] == (
        "activation_consistency",
        "survivability",
        "damage",
        "flexibility",
    )
    assert OBJECTIVE_ORDER["farming"][1] == "flexibility"
    assert OBJECTIVE_ORDER["high-difficulty"][0] == "survivability"
    assert OBJECTIVE_ORDER["boss-damage"][0] == "damage"
    assert OBJECTIVE_ORDER["consistent"] == (
        "activation_consistency",
        "survivability",
        "flexibility",
        "damage",
    )
    assert OBJECTIVE_ORDER["fast"] == (
        "damage",
        "flexibility",
        "activation_consistency",
        "survivability",
    )
    for order in OBJECTIVE_ORDER.values():
        assert "mechanic_coverage" not in order
        assert len(order) == 4


def test_flexibility_forgives_one_redundant_copy():
    same = ["bind_clear", "void_pierce"]
    one = score_team("general", CheckResult.PASS, [], [], [same], [], [])
    two = score_team("general", CheckResult.PASS, [], [], [same, same], [], [])
    four = score_team("general", CheckResult.PASS, [], [], [same, same, same, same], [], [])
    mixed = score_team(
        "general",
        CheckResult.PASS,
        [],
        [],
        [["bind_clear"], ["bind_clear", "void_pierce"]],
        [],
        [],
    )
    assert _flex(one) == 1
    assert _flex(two) == 1
    assert _flex(four) == 0.5
    assert "unused_utility=3" in _flex_detail(four)
    assert _flex(mixed) == 1


def test_a_named_requirement_ignores_unrelated_duplicates():
    quality = score_team(
        "general",
        CheckResult.PASS,
        [],
        [],
        [["bind_clear"], ["bind_clear"]],
        [],
        ["damage void pierce"],
    )
    assert _flex(quality) == 1
    assert "unused_utility=0" in _flex_detail(quality)


def test_decoder_coverage_share_excludes_unknowns():
    from padbox.evaluator.evaluate import ConstraintCheck

    passing = ConstraintCheck(name="requirement:bind clear", result=CheckResult.PASS)
    failing = ConstraintCheck(name="requirement:full light board", result=CheckResult.FAIL)
    unknown = ConstraintCheck(name="scenario:resolve", result=CheckResult.UNKNOWN)
    judged = score_team("general", CheckResult.PASS, [passing, failing], [], [], [], [])
    coverage = next(item for item in judged.components if item.name == "mechanic_coverage")
    assert coverage.value == 0.5
    assert coverage.status == "resolved"
    open_requirement = score_team(
        "general", CheckResult.PASS, [passing, unknown], [], [], [], []
    )
    unresolved = next(
        item for item in open_requirement.components if item.name == "mechanic_coverage"
    )
    assert unresolved.value is None
    assert unresolved.status == "unresolved"


def _flex(quality) -> float | None:
    return next(item.value for item in quality.components if item.name == "flexibility")


def _flex_detail(quality) -> str:
    return next(item.detail for item in quality.components if item.name == "flexibility")
