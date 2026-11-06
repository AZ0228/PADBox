"""Team evaluator.

Scores one supplied team under a stated scenario. The turn simulator is
`padbox.evaluator.sim`. `evaluate_team` judges hard constraints, runs that
simulation, and ranks the team with the formulas in `docs/scoring-model.md`.
It explains each card by evaluating the team again without that card.
`compare_teams` reports differences between two supplied teams. It does not
search for a team or select a winner.
"""

from padbox.evaluator.evaluate import (
    AssistSpec,
    ConstraintCheck,
    DataProvenance,
    EvaluationReport,
    Ruleset,
    Scenario,
    SubSpec,
    TeamSpec,
    evaluate_team,
)
from padbox.evaluator.explain import (
    CardExplanation,
    TeamComparison,
    compare_teams,
)

__all__ = [
    "AssistSpec",
    "CardExplanation",
    "ConstraintCheck",
    "DataProvenance",
    "EvaluationReport",
    "Ruleset",
    "Scenario",
    "SubSpec",
    "TeamComparison",
    "TeamSpec",
    "compare_teams",
    "evaluate_team",
]
