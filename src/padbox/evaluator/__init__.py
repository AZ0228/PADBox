"""Team evaluator.

Scores one supplied team under a stated scenario. The turn simulator is
`padbox.evaluator.sim`. `evaluate_team` judges hard constraints, runs that
simulation, and ranks the team with the formulas in `docs/scoring-model.md`.
It explains each card by evaluating the team again without that card.
`compare_teams` reports differences between two supplied teams. It does not
search for a team or select a winner.

`DungeonProfile` is a versioned list of encounter floors. On a scenario, each
listed mechanic is an essential requirement. A floor is handled only when
every one of those requirements is modeled and met.
"""

from padbox.evaluator.dungeon import (
    DUNGEON_PROFILE_VERSION,
    ENCOUNTER_MECHANICS,
    DungeonFloor,
    DungeonProfile,
    DungeonVerdict,
    EncounterMechanic,
    FloorRequirement,
    FloorVerdict,
    load_dungeon_profile,
)
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
    "DUNGEON_PROFILE_VERSION",
    "DataProvenance",
    "DungeonFloor",
    "DungeonProfile",
    "DungeonVerdict",
    "ENCOUNTER_MECHANICS",
    "EncounterMechanic",
    "EvaluationReport",
    "FloorRequirement",
    "FloorVerdict",
    "Ruleset",
    "Scenario",
    "SubSpec",
    "TeamComparison",
    "TeamSpec",
    "compare_teams",
    "evaluate_team",
    "load_dungeon_profile",
]
