"""Text reports for ``padbox team``.

The section names are the brief's report. Each value is copied from the
evaluation, the search, or the comparison that just ran. A missing metric
stays "not computed". An unresolved value stays unresolved. Activation lines
are approximations of the greedy policy.
"""

from typing import Any

from padbox.evaluator.evaluate import EvaluationReport
from padbox.evaluator.explain import TeamComparison
from padbox.optimizer.report import TeamReport
from padbox.optimizer.search import SearchResult

_LAYOUT: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Legality",
        (
            "Structure",
            "Ownership",
            "Evolution",
            "Transformation",
            "Leader",
            "Helper",
            "Board",
        ),
    ),
    (
        "Activation",
        (
            "Offensive activation",
            "Defensive activation",
            "Orb supply",
            "Longest activation gap",
        ),
    ),
    (
        "Utility",
        (
            "Damage absorption",
            "Attribute absorption",
            "Damage void",
            "Awoken bind",
            "Unmatchable",
        ),
    ),
    ("Survivability", ("Effective HP", "Healing", "Shield uptime")),
    ("Damage", ("Estimated output", "Damage cap limitations")),
    ("Uncertainty", ("Unsupported mechanics", "Unverified assumptions")),
    ("Recommendations", ("Ranked alternatives", "Suggested assists", "Skill rotation")),
)
_UTILITY = (
    ("Damage absorption", "damage absorb void", "damage_absorb"),
    ("Attribute absorption", "attribute absorb void", "attribute_absorb"),
    ("Damage void", "damage void pierce", "damage_void"),
    ("Awoken bind", "awoken bind clear", "awoken_bind"),
    ("Unmatchable", "unmatchable clear", "unmatchable"),
)
_NO_ALTERNATIVE = (
    "No alternative team was evaluated. This command scores the supplied team and does not search."
)
_RECOVERY = "Recovery sufficiency was not computed because enemy damage is not simulated."
_SHIELD = (
    "Shield uptime is not a separate metric. The battle uses the highest active damage shield."
)


def format_evaluation(
    report: EvaluationReport,
    *,
    ranked_alternatives: str | None = None,
    suggested_assists: str | None = None,
    skill_rotation: str | None = None,
) -> str:
    """One team's text report. Omitted recommendation text is filled from this report."""
    rows = _rows(report)
    rows["Recommendations"]["Ranked alternatives"] = ranked_alternatives or _NO_ALTERNATIVE
    rows["Recommendations"]["Suggested assists"] = suggested_assists or _assists(report)
    rows["Recommendations"]["Skill rotation"] = skill_rotation or _rotation(report)
    lines = _preamble(report)
    lines.extend(_render(rows))
    return "\n".join(lines) + "\n"


def format_search(result: SearchResult) -> str:
    """Ranked teams, then every pruned candidate and the reason it was dropped."""
    lines = [
        "TEAM OPTIMIZATION",
        f"Leader: {result.leader_id}",
        f"Objective: {result.objective}",
        f"Seed: {result.seed}",
        f"Turns: {result.turns}",
        f"Simulations: {result.simulations}",
        f"Beam width: {result.beam_width}",
        f"Complete teams scored: {result.complete_teams_scored}",
        f"Guarantee: {result.guarantee}",
    ]
    if result.dungeon is not None:
        lines.append(f"Dungeon: {result.dungeon.name}")
    lines.extend(f"Note: {note}" for note in result.notes)
    if not result.ranked:
        lines.append("No team was evaluated.")
        lines.extend(_render(_blank("No team was evaluated.")))
    for team in result.ranked:
        lines.append("")
        rank = team.report.rank if team.report is not None else "?"
        lines.append(f"Rank {rank}")
        if team.report is not None:
            lines.extend(_lineup(team.report))
        if team.evaluation is None:
            lines.append("This lineup was not evaluated.")
            lines.extend(_render(_blank("This lineup was not evaluated.")))
            continue
        lines.append(
            format_evaluation(
                team.evaluation,
                ranked_alternatives=_alternatives(team.report, result),
                suggested_assists=_chosen_assists(team.report),
                skill_rotation=_chosen_rotation(team.report),
            ).rstrip("\n")
        )
    lines.append("")
    lines.append("Rejected candidates")
    if not result.pruned:
        lines.append("  None.")
    else:
        lines.extend(f"  {_prune(row)}" for row in result.pruned)
    return "\n".join(lines) + "\n"


def format_comparison(comparison: TeamComparison) -> str:
    """Both supplied teams. ``selected_team`` stays none."""
    lines = [
        "TEAM COMPARISON",
        comparison.note,
        "Selected team: none",
        f"Objective: {comparison.objective}",
        f"Seed: {comparison.seed}",
        f"Turns: {comparison.turns}",
        f"Simulations: {comparison.simulations}",
        "",
        f"Left: {comparison.left_label}",
        _side(comparison.left_report, comparison.note),
        "",
        f"Right: {comparison.right_label}",
        _side(comparison.right_report, comparison.note),
        "",
        "Measured differences",
    ]
    for row in comparison.differences:
        lines.append(f"  {row.name}: left {_show(row.left)}, right {_show(row.right)}")
    lines.append("Selected team: none")
    return "\n".join(lines) + "\n"


def _side(report: Any, note: str) -> str:
    if report is None:
        return "\n".join(["The team report was not attached.", *_render(_blank("not attached"))])
    return format_evaluation(report, ranked_alternatives=note).rstrip("\n")


def _preamble(report: EvaluationReport) -> list[str]:
    provenance = report.provenance
    source = provenance.reference_source or "unrecorded"
    version = provenance.reference_version or "unrecorded"
    lines = [
        "TEAM EVALUATION",
        f"Objective: {report.scenario.name}",
        f"Seed: {report.seed}",
        f"Turns: {report.scenario.turns}",
        f"Simulations: {report.scenario.simulations}",
        f"Board: {report.ruleset.board_columns}×{report.ruleset.board_rows}",
        f"Matching: {report.ruleset.matching_policy}",
        f"Activation basis: {report.ruleset.activation_basis}",
        f"Reference: {source}",
        f"Reference version: {version}",
        f"Box revision: {provenance.box_revision}",
        f"Skill index: {provenance.skill_index}",
        f"Decoder: {provenance.decoder_version}",
        f"Feasibility: {report.feasibility.value}",
        f"Strategic quality: {report.strategic_quality}",
    ]
    if report.dungeon is not None:
        lines.append(f"Dungeon: {report.dungeon.name}")
        lines.append(report.dungeon.note)
    return lines


def _rows(report: EvaluationReport) -> dict[str, dict[str, str]]:
    metrics = _metrics(report)
    rows = {section: {} for section, _labels in _LAYOUT}
    legality = rows["Legality"]
    legality["Structure"] = (
        f"{_check(report, 'team_structure')} Complete: {_check(report, 'team_complete')}"
    )
    legality["Ownership"] = _check(report, "ownership")
    legality["Evolution"] = _check(report, "evolution")
    legality["Transformation"] = _transformation(report)
    legality["Leader"] = _check(report, "leader_legality")
    legality["Helper"] = _check(report, "helper_legality")
    legality["Board"] = _check(report, "board")
    activation = rows["Activation"]
    activation["Offensive activation"] = _metric(metrics, "offensive_activation")
    activation["Defensive activation"] = _metric(metrics, "defensive_activation")
    activation["Orb supply"] = _metric(metrics, "orb_generation_uptime")
    activation["Longest activation gap"] = _metric(metrics, "longest_activation_gap")
    for label, requirement, mechanic in _UTILITY:
        rows["Utility"][label] = _utility(report, requirement, mechanic)
    survival = rows["Survivability"]
    survival["Effective HP"] = _component_line(report, "survivability")
    survival["Healing"] = f"{_named(metrics, 'healing_orb_availability')} {_RECOVERY}"
    survival["Shield uptime"] = f"{_SHIELD} {_named(metrics, 'active_effect_uptime')}"
    damage = rows["Damage"]
    damage["Estimated output"] = _component_line(report, "damage")
    damage["Damage cap limitations"] = _damage_cap(report)
    uncertainty = rows["Uncertainty"]
    uncertainty["Unsupported mechanics"] = _unsupported(report)
    uncertainty["Unverified assumptions"] = _assumptions(report)
    return rows


def _render(rows: dict[str, dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for section, labels in _LAYOUT:
        lines.append(section)
        for label in labels:
            lines.append(f"  {label}: {rows.get(section, {}).get(label, 'not recorded')}")
    return lines


def _blank(message: str) -> dict[str, dict[str, str]]:
    return {section: dict.fromkeys(labels, message) for section, labels in _LAYOUT}


def _check(report: EvaluationReport, name: str) -> str:
    item = next((check for check in report.constraints if check.name == name), None)
    if item is None:
        return "not recorded"
    if item.detail:
        return f"{item.result.value}. {item.detail}"
    return item.result.value


def _transformation(report: EvaluationReport) -> str:
    text = _check(report, "transform")
    boost = report.validation.get("skill_boost") or {}
    if not boost:
        return text
    required = boost.get("required")
    total = boost.get("total")
    result = boost.get("result")
    if required is None:
        sentence = (
            f"Skill boost total is {total}. No known transform cooldown is recorded ({result})."
        )
    else:
        sentence = (
            f"Skill boost total is {total}. The known transform needs {required} ({result})."
        )
    members = []
    for member in boost.get("members") or []:
        members.append(
            f"{member.get('where')} #{member.get('form_id')} needs "
            f"{member.get('required_turns')} ({member.get('result')})."
        )
    return " ".join([text, sentence, *members])


def _metrics(report: EvaluationReport) -> dict[str, Any]:
    if report.simulation is None:
        return {}
    return {metric.name: metric for metric in report.simulation.metrics}


def _metric(metrics: dict[str, Any], name: str) -> str:
    metric = metrics.get(name)
    if metric is None:
        return "not computed. The simulator did not report this metric."
    if metric.value is None:
        body = "unresolved"
    else:
        body = _num(metric.value)
        if metric.interval is not None:
            low = _num(metric.interval.low)
            high = _num(metric.interval.high)
            body += f", 95% interval {low}–{high}"
    notes = ["policy approximation"]
    if metric.failures:
        failures = ", ".join(f"{item.code} {item.count}" for item in metric.failures)
        notes.append(f"failures {failures}")
    if metric.unresolved:
        notes.append(f"unresolved samples {metric.unresolved}")
    return f"{body} ({'; '.join(notes)})"


def _named(metrics: dict[str, Any], name: str) -> str:
    if name not in metrics:
        return f"{name} was not computed."
    return f"{name} is {_metric(metrics, name)}."


def _utility(report: EvaluationReport, requirement: str, mechanic: str) -> str:
    rows = report.validation.get("decoder_requirements") or []
    match = next((row for row in rows if row.get("name") == requirement), None)
    if match is None:
        text = "not recorded on this team's decoder requirements"
    else:
        text = f"{match.get('result')}. Decoder requirement '{requirement}'."
    holes = [
        str(hole)
        for hole in report.validation.get("open_holes") or []
        if requirement in str(hole).lower()
    ]
    floors = []
    if report.dungeon is not None:
        for floor in report.dungeon.floors:
            for item in floor.requirements:
                if item.mechanic == mechanic:
                    floors.append(item.detail)
    extra = holes + floors
    if not extra:
        return text
    return f"{text} {' '.join(extra)}"


def _component_line(report: EvaluationReport, name: str) -> str:
    component = next((item for item in report.components if item.name == name), None)
    if component is None:
        return "not scored"
    if component.value is None:
        return f"unresolved. {component.detail}"
    return f"{_num(component.value)}. {component.detail}"


def _damage_cap(report: EvaluationReport) -> str:
    limit = (
        "The simulator does not record whether a damage cap limited damage. "
        "A missing damage target leaves estimated output unresolved."
    )
    if not report.cards:
        return (
            f"{limit} This evaluation did not explain cards, so no card-level cap was listed."
        )
    holders = []
    for card in report.cards:
        blob = " ".join(card.unique_contribution) + " " + card.role
        if "damage_cap" in blob:
            holders.append(f"{card.slot} #{card.monster_id} {card.name}")
    if not holders:
        return f"{limit} No fielded card explanation lists a verified damage cap."
    return f"{limit} Verified damage cap on {', '.join(holders)}."


def _unsupported(report: EvaluationReport) -> str:
    lines = [_effect_line(row) for row in report.validation.get("unknowns") or []]
    for line in report.unknowns:
        text = str(line)
        if "has unresolved effects:" in text:
            continue
        if text not in lines:
            lines.append(text)
    if report.dungeon is not None:
        for line in report.dungeon.uncertainties:
            if line not in lines:
                lines.append(line)
    if not lines:
        return "None recorded."
    return " ".join(lines)


def _effect_line(row: dict) -> str:
    summaries = []
    for effect in row.get("effects") or []:
        if isinstance(effect, dict) and effect.get("summary"):
            summaries.append(str(effect["summary"]))
        else:
            summaries.append(str(effect))
    listed = ", ".join(summaries) if summaries else "unresolved effects"
    return f"{row.get('where')} #{row.get('form_id')} {row.get('form_name')}: {listed}."


def _assumptions(report: EvaluationReport) -> str:
    lines = []
    for row in report.validation.get("assumptions") or []:
        if row.get("exact") is True:
            continue
        summary = row.get("summary")
        if summary:
            lines.append(f"unverified: {summary}")
    if report.simulation is not None:
        lines.extend(report.simulation.assumptions)
    if not lines:
        return "None recorded."
    return " ".join(lines)


def _assists(report: EvaluationReport) -> str:
    if not report.cards:
        return "This evaluation did not explain cards, so equipped assists are not listed here."
    rows = [card for card in report.cards if "assist" in card.slot]
    if not rows:
        return "No assist is equipped on this team."
    return "; ".join(f"{card.slot} #{card.monster_id} {card.name}. {card.role}" for card in rows)


def _rotation(report: EvaluationReport) -> str:
    metrics = _metrics(report)
    parts = [
        "Actives resolve leader, then subs in listed order, then helper.",
        "A transform is used on the first turn that skill is ready.",
        "An assist replaces the host active. Charge spends the host cooldown first.",
        "The rates are policy approximations. The report does not list each cast.",
        f"skill_availability: {_metric(metrics, 'skill_availability')}",
        f"turns_to_transform: {_metric(metrics, 'turns_to_transform')}",
    ]
    return " ".join(parts)


def _lineup(report: TeamReport) -> list[str]:
    lines = ["Lineup"]
    for card in report.lineup:
        supply = f" ({card.supply})" if card.supply else ""
        lines.append(f"  {card.role}: #{card.form_id} {card.form_name}{supply}")
    return lines


def _alternatives(report: TeamReport | None, result: SearchResult) -> str:
    if report is None:
        text = "No ranking explanation was recorded."
    else:
        text = report.selected_because
    if result.pruned:
        count = len(result.pruned)
        noun = "candidate was" if count == 1 else "candidates were"
        text += f" {count} {noun} pruned. Their reasons follow the ranked teams."
    else:
        text += " No candidate was pruned."
    return text


def _chosen_assists(report: TeamReport | None) -> str:
    if report is None or not report.assists:
        return "No assist was chosen for this team."
    return "; ".join(f"{card.role} #{card.form_id} {card.form_name}" for card in report.assists)


def _chosen_rotation(report: TeamReport | None) -> str:
    if report is None or not report.skill_rotation:
        return "No skill rotation was recorded."
    return " ".join(report.skill_rotation)


def _prune(row: Any) -> str:
    dominated = ""
    if row.dominated_by is not None:
        dominated = f" Dominated by #{row.dominated_by}."
    return (
        f"#{row.form_id} {row.form_name} ({row.kind}): {row.reason}. {row.detail}{dominated}"
    )


def _num(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "unresolved"
    return format(value, ".6g")


def _show(value: object) -> str:
    if value is None:
        return "unresolved"
    return str(value)
