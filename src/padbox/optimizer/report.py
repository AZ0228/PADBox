"""Why a tested team places where it does.

The text is read from the ``evaluate_team`` reports already run for this beam.
It does not simulate again, and it does not treat a team outside the beam as
having been compared.
"""

from pydantic import BaseModel, ConfigDict, Field

from padbox.evaluator.score import OBJECTIVE_ORDER, ObjectiveName
from padbox.mechanics.result import CheckResult
from padbox.optimizer.search import SearchedTeam

_ACTIVATION = (
    "leader_activation",
    "offensive_activation",
    "defensive_activation",
    "orb_generation_uptime",
    "longest_activation_gap",
)
_ROTATION = ("skill_availability", "turns_to_transform")
_FEASIBILITY = {CheckResult.PASS: 0, CheckResult.UNKNOWN: 1, CheckResult.FAIL: 2}
_FEASIBILITY_NAME = {0: "pass", 1: "unknown", 2: "fail", 3: "not evaluated"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineupCard(_Strict):
    """One card in the ranked lineup."""

    role: str
    form_id: int
    form_name: str
    owned_id: int | None = None
    copy_index: int | None = None
    borrowed: bool = False
    supply: str | None = None


class CoverageRow(_Strict):
    """One decoder requirement on the evaluated team."""

    name: str
    result: str


class MetricLine(_Strict):
    """One recorded simulator metric. ``value`` is null when it did not resolve."""

    name: str
    value: float | None
    detail: str = ""


class Tradeoff(_Strict):
    """This team against one other team that was evaluated in the same search."""

    other_rank: int
    ahead: bool
    tied: bool
    reason: str


class TeamReport(_Strict):
    """The ranked explanation for one tested team."""

    rank: int
    lineup: list[LineupCard]
    helper: LineupCard
    assists: list[LineupCard]
    role_coverage: list[CoverageRow]
    transform_plan: list[str]
    skill_rotation: list[str]
    activation: list[MetricLine]
    survival: str
    damage: str
    weaknesses: list[str]
    unknowns: list[str]
    selected_because: str
    tradeoffs: list[Tradeoff] = Field(default_factory=list)
    scope: str


def order_key(team: SearchedTeam, objective: ObjectiveName) -> tuple:
    """Sort key for one objective. A smaller tuple sorts first."""
    report = team.evaluation
    if report is None:
        return (3,)
    feasibility = _FEASIBILITY[report.feasibility]
    by_name = {component.name: component for component in report.components}
    terms: list[tuple[int, float]] = []
    for name in OBJECTIVE_ORDER[objective]:
        component = by_name.get(name)
        if component is None or component.value is None:
            terms.append((1, 0.0))
        else:
            terms.append((0, -float(component.value)))
    return (feasibility, *terms, team.uncertainty, tuple(team.cheap_score))


def explain_ranked(
    teams: list[SearchedTeam],
    *,
    leader_id: int,
    objective: ObjectiveName,
    seed: int,
    turns: int,
    simulations: int,
    beam_width: int,
    complete_teams_scored: int,
    guarantee: str,
) -> None:
    """Fill ``team.report`` for each evaluated team. The list is already in rank order."""
    if not teams:
        return
    scope = (
        f"Objective {objective}, seed {seed}, {_count(turns, 'turn')}, "
        f"{_count(simulations, 'simulation')}, beam width {beam_width}. "
        f"{complete_teams_scored} complete lineups were scored before the beam cut. {guarantee}"
    )
    keys = [order_key(team, objective) for team in teams]
    for index, team in enumerate(teams):
        tradeoffs = [
            _tradeoff(other, keys[index], keys[other], teams[index], teams[other], objective)
            for other in range(len(teams))
            if other != index
        ]
        team.report = _report(
            team,
            rank=index + 1,
            leader_id=leader_id,
            objective=objective,
            tradeoffs=tradeoffs,
            scope=scope,
            tested=len(teams),
        )


def _tradeoff(
    other: int,
    left_key: tuple,
    right_key: tuple,
    left: SearchedTeam,
    right: SearchedTeam,
    objective: ObjectiveName,
) -> Tradeoff:
    ahead = left_key < right_key
    tied = left_key == right_key
    return Tradeoff(
        other_rank=other + 1,
        ahead=ahead,
        tied=tied,
        reason=_reason(ahead, tied, left_key, right_key, left, right, objective),
    )


def _reason(
    ahead: bool,
    tied: bool,
    left_key: tuple,
    right_key: tuple,
    left: SearchedTeam,
    right: SearchedTeam,
    objective: ObjectiveName,
) -> str:
    if tied:
        return (
            "The two teams tie on feasibility, the objective terms, uncertainty, "
            "and the cheap screen."
        )
    if left_key[0] != right_key[0]:
        return (
            f"Feasibility is {_FEASIBILITY_NAME[left_key[0]]}, and the other team is "
            f"{_FEASIBILITY_NAME[right_key[0]]}."
        )
    order = OBJECTIVE_ORDER[objective]
    for offset, name in enumerate(order, start=1):
        if left_key[offset] == right_key[offset]:
            continue
        return _component_reason(name, left, right)
    uncertainty_at = 1 + len(order)
    if left_key[uncertainty_at] != right_key[uncertainty_at]:
        return (
            "The objective terms tie. "
            f"Uncertainty is {left_key[uncertainty_at]}, and the other team has "
            f"{right_key[uncertainty_at]}."
        )
    if ahead:
        placed = "this team first"
    else:
        placed = "the other team first"
    return (
        "The objective terms and the uncertainty tie. "
        f"The cheap screen then places {placed}. That screen is not evaluate_team."
    )


def _component_reason(name: str, left: SearchedTeam, right: SearchedTeam) -> str:
    left_value = _component_value(left, name)
    right_value = _component_value(right, name)
    if left_value is None and right_value is not None:
        return f"{name} is unresolved, and the other team has {_number(right_value)}."
    if left_value is not None and right_value is None:
        return f"{name} is {_number(left_value)}, and the other team's {name} is unresolved."
    return f"{name} is {_number(left_value)}, and the other team has {_number(right_value)}."


def _report(
    team: SearchedTeam,
    *,
    rank: int,
    leader_id: int,
    objective: ObjectiveName,
    tradeoffs: list[Tradeoff],
    scope: str,
    tested: int,
) -> TeamReport:
    evaluation = team.evaluation
    validation = evaluation.validation if evaluation is not None else {}
    lineup, helper, assists = _lineup(team, leader_id, validation)
    coverage = _coverage(validation)
    place = f"Rank {rank} of {tested} tested teams. {scope}"
    comparisons = " ".join(f"Against rank {row.other_rank}: {row.reason}" for row in tradeoffs)
    return TeamReport(
        rank=rank,
        lineup=lineup,
        helper=helper,
        assists=assists,
        role_coverage=coverage,
        transform_plan=_transform(validation),
        skill_rotation=_rotation(evaluation),
        activation=_activation(evaluation),
        survival=_estimate(evaluation, objective, "survivability"),
        damage=_estimate(evaluation, objective, "damage"),
        weaknesses=_weaknesses(evaluation, objective, validation),
        unknowns=_unknowns(evaluation),
        selected_because=f"{place} {comparisons}".strip(),
        tradeoffs=tradeoffs,
        scope=scope,
    )


def _lineup(
    team: SearchedTeam, leader_id: int, validation: dict
) -> tuple[list[LineupCard], LineupCard, list[LineupCard]]:
    leader_name, leader_owned = _leader_identity(leader_id, validation)
    leader = LineupCard(
        role="leader",
        form_id=leader_id,
        form_name=leader_name,
        owned_id=leader_owned,
    )
    helper = LineupCard(
        role="helper",
        form_id=team.helper.form_id,
        form_name=team.helper.form_name,
        owned_id=team.helper.owned_id,
        copy_index=team.helper.copy_index,
        borrowed=team.helper.borrowed,
        supply=team.helper.supply,
    )
    subs = [
        LineupCard(
            role=f"sub {index + 1}",
            form_id=sub.form_id,
            form_name=sub.form_name,
            owned_id=sub.owned_id,
            copy_index=sub.copy_index,
            borrowed=sub.borrowed,
        )
        for index, sub in enumerate(team.subs)
    ]
    assists: list[LineupCard] = []
    if team.leader_assist is not None:
        assists.append(_assist_card("leader assist", team.leader_assist))
    for index, sub in enumerate(team.subs):
        if sub.assist is not None:
            assists.append(_assist_card(f"sub {index + 1} assist", sub.assist))
    if team.helper_assist is not None:
        assists.append(_assist_card("helper assist", team.helper_assist))
    return [leader, *subs, helper], helper, assists


def _assist_card(role: str, member: object) -> LineupCard:
    return LineupCard(
        role=role,
        form_id=member.form_id,  # type: ignore[attr-defined]
        form_name=member.form_name,  # type: ignore[attr-defined]
        owned_id=member.owned_id,  # type: ignore[attr-defined]
        copy_index=member.copy_index,  # type: ignore[attr-defined]
        borrowed=member.borrowed,  # type: ignore[attr-defined]
    )


def _leader_identity(leader_id: int, validation: dict) -> tuple[str, int | None]:
    owned: int | None = None
    name: str | None = None
    for row in validation.get("investments") or []:
        if row.get("where") != "Leader":
            continue
        if isinstance(row.get("owned_id"), int):
            owned = row["owned_id"]
        if row.get("form_name"):
            name = str(row["form_name"])
    for row in (validation.get("skill_boost") or {}).get("members") or []:
        if row.get("where") == "Leader" and row.get("form_name") and name is None:
            name = str(row["form_name"])
    return name or f"#{leader_id}", owned


def _coverage(validation: dict) -> list[CoverageRow]:
    rows = []
    for row in validation.get("decoder_requirements") or []:
        name = str(row.get("name") or "")
        if not name:
            continue
        rows.append(CoverageRow(name=name, result=str(row.get("result") or "unknown")))
    return rows


def _transform(validation: dict) -> list[str]:
    boost = validation.get("skill_boost") or {}
    if not boost:
        return ["No transform check was recorded for this team."]
    required = boost.get("required")
    total = boost.get("total")
    result = boost.get("result")
    if required is None:
        lines = ["This lineup has no known transform cooldown."]
    else:
        lines = [
            f"Skill boost is {total}. The known transform needs {required} turns. "
            f"The check is {result}."
        ]
    for member in boost.get("members") or []:
        lines.append(
            f"{member.get('where')} #{member.get('form_id')} {member.get('form_name')} "
            f"needs {member.get('required_turns')} turns ({member.get('result')})."
        )
    return lines


def _rotation(evaluation: object) -> list[str]:
    lines = [
        "Actives resolve in one order: leader, subs in lineup order, then helper.",
        "A transform is used on the first turn that skill is ready.",
        "An assist replaces the host active. Charge spends the host cooldown first.",
        "The simulation report records rates, not each cast.",
    ]
    metrics = _metrics(evaluation)
    for name in _ROTATION:
        metric = metrics.get(name)
        if metric is None:
            lines.append(f"{name} was not recorded.")
        elif metric.value is None:
            lines.append(f"{name} has no resolved value.")
        else:
            lines.append(f"{name} is {_number(metric.value)}.")
    return lines


def _activation(evaluation: object) -> list[MetricLine]:
    metrics = _metrics(evaluation)
    lines = []
    for name in _ACTIVATION:
        metric = metrics.get(name)
        if metric is None:
            lines.append(
                MetricLine(name=name, value=None, detail="The simulator omitted this metric.")
            )
            continue
        detail = ""
        if metric.value is None:
            detail = "The metric has no resolved value."
        lines.append(MetricLine(name=name, value=metric.value, detail=detail))
    return lines


def _estimate(evaluation: object, objective: ObjectiveName, name: str) -> str:
    component = _component(evaluation, name)
    if component is None:
        return f"{name} was not scored."
    if name not in OBJECTIVE_ORDER[objective]:
        rank = "It is not one of this objective's rank terms."
    else:
        rank = "It is one of this objective's rank terms."
    if component.value is None:
        text = f"{name} is unresolved. {component.detail} {rank}"
    else:
        text = f"{name} is {_number(component.value)}. {component.detail} {rank}"
    for trace in component.traces:
        if trace.role == "value":
            continue
        if trace.value is None:
            text += f" {trace.source} was recorded without a value."
        else:
            text += f" {trace.source} is {_number(trace.value)} and does not replace this estimate."
    return text


def _weaknesses(evaluation: object, objective: ObjectiveName, validation: dict) -> list[str]:
    lines: list[str] = []
    if evaluation is None:
        return ["This team was not evaluated."]
    for check in evaluation.constraints:  # type: ignore[attr-defined]
        result = check.result.value if hasattr(check.result, "value") else str(check.result)
        if result == "pass":
            continue
        detail = check.detail or check.name
        lines.append(f"{check.name} is {result}. {detail}")
    for hole in validation.get("open_holes") or []:
        lines.append(str(hole))
    for name in OBJECTIVE_ORDER[objective]:
        component = _component(evaluation, name)
        if component is None or component.value is None:
            detail = "" if component is None else component.detail
            lines.append(f"{name} is unresolved. {detail}".strip())
    return lines


def _unknowns(evaluation: object) -> list[str]:
    if evaluation is None:
        return []
    lines = [str(line) for line in evaluation.unknowns]  # type: ignore[attr-defined]
    validation = evaluation.validation  # type: ignore[attr-defined]
    for row in validation.get("unknown_effects") or []:
        where = row.get("where", "card")
        lines.append(f"{where} has unresolved effects: {row.get('effects')}")
    return lines


def _metrics(evaluation: object) -> dict:
    if evaluation is None or evaluation.simulation is None:  # type: ignore[attr-defined]
        return {}
    return {metric.name: metric for metric in evaluation.simulation.metrics}  # type: ignore[attr-defined]


def _component(evaluation: object, name: str) -> object:
    if evaluation is None:
        return None
    for component in evaluation.components:  # type: ignore[attr-defined]
        if component.name == name:
            return component
    return None


def _component_value(team: SearchedTeam, name: str) -> float | None:
    component = _component(team.evaluation, name)
    if component is None or component.value is None:  # type: ignore[attr-defined]
        return None
    return float(component.value)  # type: ignore[attr-defined]


def _count(value: int, word: str) -> str:
    suffix = "" if value == 1 else "s"
    return f"{value} {word}{suffix}"


def _number(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "unresolved"
    return format(value, ".6g")
