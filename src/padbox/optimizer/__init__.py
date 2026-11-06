"""Team search over the owned collection.

This layer is the only one that searches. It asks `padbox.mechanics` what an
owned copy can become and `padbox.evaluator` how a supplied team scores.
Mechanics does not select a team. The evaluator does not search.
"""

from padbox.optimizer.candidates import (
    Candidate,
    CandidatePool,
    PrunedCandidate,
    generate_candidates,
)
from padbox.optimizer.report import TeamReport
from padbox.optimizer.search import (
    DEFAULT_BEAM_WIDTH,
    ChosenHelper,
    FieldedSub,
    SearchedTeam,
    SearchResult,
    TeamMember,
    search_teams,
)

__all__ = [
    "DEFAULT_BEAM_WIDTH",
    "Candidate",
    "CandidatePool",
    "ChosenHelper",
    "FieldedSub",
    "PrunedCandidate",
    "SearchResult",
    "SearchedTeam",
    "TeamMember",
    "TeamReport",
    "generate_candidates",
    "search_teams",
]
