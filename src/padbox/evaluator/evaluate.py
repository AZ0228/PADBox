"""Evaluation of one supplied team.

`evaluate_team` reuses `check_team`, then runs the recorded simulation and
scores the five quality dimensions. It explains each fielded card by
evaluating the team again without that card. It does not search for a team
or turn an unresolved requirement into a pass or a fail. The report records the ruleset,
scenario, seed, and data provenance of the run. The scalar is the objective
tuple and is absent while any rank component is unresolved.
"""

import json
from collections.abc import Mapping
from contextlib import closing
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from padbox.database.store import Store
from padbox.evaluator.explain import CardExplanation
from padbox.evaluator.score import (
    ComponentScore,
    RankResult,
    _canonical,
    policy_mechanics,
    score_team,
)
from padbox.evaluator.sim import (
    BOARD_COLUMNS,
    BOARD_ROWS,
    DEFAULT_SEED,
    DEFAULT_SIMULATIONS,
    DEFAULT_TURNS,
    MemberInput,
    PolicyTarget,
    SimulationReport,
    run_monte_carlo,
)
from padbox.inventory.snapshots import current_box
from padbox.mechanics.clauses import leader_clauses
from padbox.mechanics.decode import DECODER_VERSION, Effect, decode_skill
from padbox.mechanics.explain import _index
from padbox.mechanics.result import CheckResult, combine_checks
from padbox.mechanics.team import check_team

ObjectiveName = Literal[
    "general",
    "farming",
    "high-difficulty",
    "boss-damage",
    "consistent",
    "fast",
]
_LEADER_HOLE = "No owned copy can become this leader."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssistSpec(_Strict):
    """One equipped copy. ``owned_id`` is the box card and ``form_id`` is the equip."""

    owned_id: int
    form_id: int


class SubSpec(_Strict):
    """One sub slot. The assist, when named, spends its own copy."""

    owned_id: int
    form_id: int
    assist: AssistSpec | None = None


class TeamSpec(_Strict):
    """The supplied lineup. ``leader_id`` is the card the team is built around."""

    leader_id: int
    helper_id: int | None = None
    leader_assist: AssistSpec | None = None
    helper_assist: AssistSpec | None = None
    subs: list[SubSpec] = Field(default_factory=list)


class Scenario(_Strict):
    """The stated objective and the simulation settings recorded with the run.

    Naming a requirement does not make an unmodeled mechanic pass or fail.
    """

    name: ObjectiveName = "general"
    seed: int = DEFAULT_SEED
    turns: int = DEFAULT_TURNS
    simulations: int = DEFAULT_SIMULATIONS
    requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _positive_counts(self) -> "Scenario":
        if self.turns < 1 or self.simulations < 1:
            raise ValueError("turns and simulations must be positive")
        return self


class Ruleset(_Strict):
    """Rules recorded for the run. The only supported board is 6 columns by 5 rows."""

    board_columns: int = BOARD_COLUMNS
    board_rows: int = BOARD_ROWS
    matching_policy: Literal["greedy"] = "greedy"
    activation_basis: Literal["policy_approximation"] = "policy_approximation"


class DataProvenance(_Strict):
    """Where the cards and the box for this run came from."""

    reference_source: str | None
    reference_version: str | None
    reference_updated_at: str | None = None
    box_revision: int | None
    box_source_scan: str | None = None
    skill_index: Literal["supplied", "cached", "missing"]
    decoder_version: str


class ConstraintCheck(_Strict):
    """One hard check. ``unknown`` is left unresolved."""

    name: str
    result: CheckResult
    detail: str = ""


class EvaluationReport(_Strict):
    """Feasibility and quality of one supplied team under one scenario and ruleset.

    ``rank.score`` is absent unless every hard check passed and every component
    in the objective tuple resolved. ``mechanic_coverage`` is reported with the
    other dimensions and is not one of the four rank terms.
    """

    ruleset: Ruleset
    scenario: Scenario
    seed: int
    provenance: DataProvenance
    feasibility: CheckResult
    strategic_quality: Literal["ranked", "excluded", "unresolved"]
    team_structure_valid: bool
    team_complete: bool
    ownership_valid: bool
    evolution_feasible: CheckResult
    leader_legality: CheckResult
    helper_legality: CheckResult
    transform_feasible: CheckResult
    mechanics_coverage: CheckResult
    board_supported: bool
    constraints: list[ConstraintCheck]
    unknowns: list[str]
    validation: dict[str, Any]
    components: list[ComponentScore]
    rank: RankResult
    simulation: SimulationReport | None = None
    cards: list[CardExplanation] = Field(default_factory=list)


def evaluate_team(
    team: TeamSpec | Mapping[str, Any],
    scenario: Scenario | Mapping[str, Any] | None = None,
    ruleset: Ruleset | Mapping[str, Any] | None = None,
    *,
    store: Store,
    skill_index: dict[int, dict[str, Any]] | None = None,
    explain: bool = True,
    _omit_leader: bool = False,
) -> EvaluationReport:
    """Judge one supplied team, simulate it, and score the recorded metrics.

    ``store`` is keyword-only: the team spec does not contain the box or the
    reference snapshot. Unknown requirements stay ``unknown``. When
    ``explain`` is true, each fielded card is described and then removed and
    evaluated again under this same scenario. ``_omit_leader`` is the leader
    removal: the battle drops that card and no other leader is substituted.
    """
    spec = team if isinstance(team, TeamSpec) else TeamSpec.model_validate(team)
    stated = _scenario(scenario)
    rules = _ruleset(ruleset)
    validation = check_team(store, spec.leader_id, _team_payload(spec), skill_index)
    provenance = _provenance(store, spec.leader_id, skill_index)
    constraints = _constraints(spec, rules, stated, validation)
    if _omit_leader:
        constraints.append(
            ConstraintCheck(
                name="leader_present",
                result=CheckResult.FAIL,
                detail="The leader was removed. No other leader was evaluated.",
            )
        )
    by_name = {item.name: item for item in constraints}
    feasibility = combine_checks(item.result for item in constraints)
    board_supported = by_name["board"].result is CheckResult.PASS
    simulation, slots, prior, note = _simulate(
        store,
        spec,
        stated,
        validation,
        skill_index,
        board_supported,
        omit_leader=_omit_leader,
    )
    quality = score_team(
        stated.name,
        feasibility,
        constraints,
        simulation.metrics if simulation is not None else None,
        slots,
        prior,
        stated.requirements,
        simulation_note=note,
    )
    report = EvaluationReport(
        ruleset=rules,
        scenario=stated,
        seed=stated.seed,
        provenance=provenance,
        feasibility=feasibility,
        strategic_quality=quality.strategic_quality,
        team_structure_valid=validation["team_structure_valid"],
        team_complete=False if _omit_leader else validation["team_complete"],
        ownership_valid=validation["ownership_valid"],
        evolution_feasible=CheckResult(validation["evolution_feasible"]),
        leader_legality=by_name["leader_legality"].result,
        helper_legality=by_name["helper_legality"].result,
        transform_feasible=CheckResult(validation["transform_feasible"]),
        mechanics_coverage=CheckResult(validation["mechanics_coverage"]),
        board_supported=board_supported,
        constraints=constraints,
        unknowns=_unknown_lines(constraints, validation),
        validation=validation,
        components=quality.components,
        rank=quality.rank,
        simulation=simulation,
    )
    if not explain:
        return report
    from padbox.evaluator.explain import explain_cards

    return report.model_copy(
        update={
            "cards": explain_cards(
                spec, stated, rules, report, store=store, skill_index=skill_index
            )
        }
    )


def _scenario(value: Scenario | Mapping[str, Any] | None) -> Scenario:
    if value is None:
        return Scenario()
    if isinstance(value, Scenario):
        return value
    return Scenario.model_validate(value)


def _ruleset(value: Ruleset | Mapping[str, Any] | None) -> Ruleset:
    if value is None:
        return Ruleset()
    if isinstance(value, Ruleset):
        return value
    return Ruleset.model_validate(value)


def _team_payload(spec: TeamSpec) -> dict[str, Any]:
    return {
        "helper_id": spec.helper_id,
        "leader_assist": _pair(spec.leader_assist),
        "helper_assist": _pair(spec.helper_assist),
        "subs": [
            {
                "owned_id": sub.owned_id,
                "form_id": sub.form_id,
                "assist": _pair(sub.assist),
            }
            for sub in spec.subs
        ],
    }


def _pair(item: AssistSpec | None) -> dict[str, int] | None:
    if item is None:
        return None
    return {"owned_id": item.owned_id, "form_id": item.form_id}


def _constraints(
    spec: TeamSpec,
    rules: Ruleset,
    scenario: Scenario,
    validation: dict[str, Any],
) -> list[ConstraintCheck]:
    problems = [str(item) for item in validation["problems"]]
    structure = _flag(
        "team_structure",
        validation["team_structure_valid"],
        "The team shape is one leader, four subs, and a separate helper.",
        _joined(problems, ("sub slots", "not an assist", "without a helper"))
        or "The team shape is not a standard team.",
    )
    complete = _flag(
        "team_complete",
        validation["team_complete"],
        "The team is one leader, four subs, and one helper.",
        "The team is partial. A missing helper does not open a fifth sub slot.",
    )
    ownership = _flag(
        "ownership",
        validation["ownership_valid"],
        "Copy limits hold for the leader, the subs, and the assists.",
        _joined(problems, ("not in the applied box", "No owned copy", "is used", "the box has"))
        or "Ownership or copy limits do not hold.",
    )
    evolution = CheckResult(validation["evolution_feasible"])
    evolution_detail = _evolution_detail(evolution, problems)
    leader = _leader_check(problems)
    helper = _helper_check(spec, problems)
    transform = CheckResult(validation["transform_feasible"])
    coverage = CheckResult(validation["mechanics_coverage"])
    checks = [
        _board_check(rules),
        structure,
        complete,
        ownership,
        ConstraintCheck(name="evolution", result=evolution, detail=evolution_detail),
        leader,
        helper,
        ConstraintCheck(name="transform", result=transform, detail=_transform_detail(transform)),
        ConstraintCheck(
            name="mechanics_coverage",
            result=coverage,
            detail=_coverage_detail(coverage),
        ),
    ]
    checks.extend(_transform_members(validation))
    decoder = _decoder_results(validation)
    checks.extend(
        ConstraintCheck(name=f"requirement:{name}", result=result)
        for name, result in decoder.items()
    )
    checks.extend(_scenario_checks(scenario, decoder))
    return checks


def _flag(name: str, passed: bool, ok: str, failed: str) -> ConstraintCheck:
    if passed:
        return ConstraintCheck(name=name, result=CheckResult.PASS, detail=ok)
    return ConstraintCheck(name=name, result=CheckResult.FAIL, detail=failed)


def _joined(problems: list[str], needles: tuple[str, ...]) -> str:
    matched = [problem for problem in problems if any(needle in problem for needle in needles)]
    return " ".join(matched)


def _evolution_detail(result: CheckResult, problems: list[str]) -> str:
    if result is CheckResult.PASS:
        return "Evolution spends that this check can see are feasible."
    if result is CheckResult.UNKNOWN:
        return "A special evolution requirement is unresolved."
    joined = _joined(problems, ("not a reachable", "evolution material"))
    return joined or "Evolution spends are not feasible."


def _leader_check(problems: list[str]) -> ConstraintCheck:
    if _LEADER_HOLE in problems:
        return ConstraintCheck(
            name="leader_legality",
            result=CheckResult.FAIL,
            detail=_LEADER_HOLE,
        )
    return ConstraintCheck(
        name="leader_legality",
        result=CheckResult.PASS,
        detail="An owned copy can become this leader.",
    )


def _helper_check(spec: TeamSpec, problems: list[str]) -> ConstraintCheck:
    missing = next((problem for problem in problems if "without a helper" in problem), None)
    if spec.helper_id is None:
        detail = "No helper was supplied. A friend's helper is not assumed."
        if missing is not None:
            detail = f"{detail} {missing}"
        return ConstraintCheck(name="helper_legality", result=CheckResult.FAIL, detail=detail)
    if missing is not None:
        return ConstraintCheck(name="helper_legality", result=CheckResult.FAIL, detail=missing)
    return ConstraintCheck(
        name="helper_legality",
        result=CheckResult.PASS,
        detail=f"Helper #{spec.helper_id} was supplied explicitly.",
    )


def _board_check(rules: Ruleset) -> ConstraintCheck:
    supported = rules.board_columns == BOARD_COLUMNS and rules.board_rows == BOARD_ROWS
    if supported:
        return ConstraintCheck(
            name="board",
            result=CheckResult.PASS,
            detail="The board is 6×5, 6 columns by 5 rows.",
        )
    return ConstraintCheck(
        name="board",
        result=CheckResult.FAIL,
        detail=(
            f"A {rules.board_columns}×{rules.board_rows} board is unsupported. "
            f"The supported board is {BOARD_COLUMNS}×{BOARD_ROWS}, "
            "6 columns by 5 rows."
        ),
    )


def _transform_detail(result: CheckResult) -> str:
    if result is CheckResult.PASS:
        return "Every known transform requirement is met."
    if result is CheckResult.FAIL:
        return "A known transform requirement is missed."
    return "A transform requirement is unresolved."


def _coverage_detail(result: CheckResult) -> str:
    if result is CheckResult.PASS:
        return "Every judged decoder requirement passes."
    if result is CheckResult.FAIL:
        return "A judged decoder requirement fails."
    return "A decoder requirement is unresolved."


def _transform_members(validation: dict[str, Any]) -> list[ConstraintCheck]:
    members = validation.get("skill_boost", {}).get("members") or []
    checks = []
    for member in members:
        where = str(member.get("where") or "member")
        result = CheckResult(str(member.get("result") or CheckResult.UNKNOWN.value))
        turns = member.get("required_turns")
        if not isinstance(turns, int):
            detail = f"{where} transform cooldown is unresolved."
        elif result is CheckResult.PASS:
            detail = f"{where} transform needs {turns} turns and the counted boost meets it."
        else:
            detail = f"{where} transform needs {turns} turns."
        checks.append(ConstraintCheck(name=f"transform:{where}", result=result, detail=detail))
    return checks


def _decoder_results(validation: dict[str, Any]) -> dict[str, CheckResult]:
    found: dict[str, CheckResult] = {}
    for row in validation.get("decoder_requirements") or []:
        name = str(row.get("name") or "")
        if not name:
            continue
        found[name] = CheckResult(str(row.get("result") or CheckResult.UNKNOWN.value))
    return found


def _scenario_checks(
    scenario: Scenario, decoder: dict[str, CheckResult]
) -> list[ConstraintCheck]:
    checks = []
    normalized = {_canonical(name): result for name, result in decoder.items()}
    for name in scenario.requirements:
        label = _canonical(name)
        result = normalized.get(label)
        if result is None:
            checks.append(
                ConstraintCheck(
                    name=f"scenario:{name}",
                    result=CheckResult.UNKNOWN,
                    detail="This requirement is not a modeled check, so it stays unresolved.",
                )
            )
            continue
        checks.append(
            ConstraintCheck(
                name=f"scenario:{name}",
                result=result,
                detail=f"Decoder requirement '{label}' is {result.value}.",
            )
        )
    return checks


def _unknown_lines(constraints: list[ConstraintCheck], validation: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for row in validation.get("unknowns") or []:
        where = row.get("where", "card")
        lines.append(f"{where} has unresolved effects: {row.get('effects')}")
    for check in constraints:
        if check.result is not CheckResult.UNKNOWN:
            continue
        if check.detail:
            lines.append(f"{check.name}: {check.detail}")
        else:
            lines.append(f"{check.name} is unresolved.")
    return lines


def _provenance(
    store: Store,
    leader_id: int,
    skill_index: dict[int, dict[str, Any]] | None,
) -> DataProvenance:
    box = current_box(store) or {}
    with closing(store.connect("reference")) as db:
        row = db.execute("SELECT provenance FROM monster WHERE id=?", (leader_id,)).fetchone()
    raw = json.loads(row["provenance"]) if row else {}
    version = _text(raw.get("version"))
    if skill_index is not None:
        index_status = "supplied"
    else:
        cached = _index(store.home, version or "", None)
        index_status = "cached" if cached else "missing"
    revision = box.get("revision")
    return DataProvenance(
        reference_source=_text(raw.get("source")),
        reference_version=version,
        reference_updated_at=_text(raw.get("updated_at")),
        box_revision=revision if isinstance(revision, int) else None,
        box_source_scan=_text(box.get("source_scan")),
        skill_index=index_status,
        decoder_version=DECODER_VERSION,
    )


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _simulate(
    store: Store,
    spec: TeamSpec,
    scenario: Scenario,
    validation: dict[str, Any],
    skill_index: dict[int, dict[str, Any]] | None,
    board_supported: bool,
    *,
    omit_leader: bool = False,
) -> tuple[SimulationReport | None, list[list[str]], list[str], str | None]:
    """Run the scenario's Monte Carlo count when the board is 6×5."""
    if not board_supported:
        return None, [], [], "The board is unsupported, so the battle was not simulated."
    try:
        roster = _roster(store, spec, validation, skill_index, omit_leader=omit_leader)
    except ValueError as exc:
        return None, [], [], str(exc)
    if roster is None:
        return None, [], [], "A fielded card is not in the local reference set."
    members, skills, cards, slots, prior = roster
    colors = [] if omit_leader else _leader_colors(cards[spec.leader_id], skills)
    target = PolicyTarget(
        colors=colors,
        mechanics=policy_mechanics(scenario.requirements),
    )
    report = run_monte_carlo(
        members,
        skills,
        cards,
        target=target,
        turns=scenario.turns,
        simulations=scenario.simulations,
        seed=scenario.seed,
    )
    return report, slots, prior, None


def _roster(
    store: Store,
    spec: TeamSpec,
    validation: dict[str, Any],
    skill_index: dict[int, dict[str, Any]] | None,
    *,
    omit_leader: bool = False,
) -> tuple[
    list[MemberInput],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    list[list[str]],
    list[str],
] | None:
    """Cards, skills, utility slots, and kinds the leader and helper already bring."""
    wanted = _fielded_ids(spec)
    with closing(store.connect("reference")) as db:
        wanted = _with_transforms(db, wanted)
        cards: dict[int, dict[str, Any]] = {}
        skills: dict[int, dict[str, Any]] = {}
        for monster_id in sorted(wanted):
            card, card_skills = _load_card(db, monster_id)
            if card is None:
                if monster_id in _fielded_ids(spec):
                    return None
                continue
            cards[monster_id] = card
            skills.update(card_skills)
    if skill_index:
        skills.update(skill_index)
    if 0 not in skills:
        skills[0] = {"id": 0, "name": "", "description": "", "type": 0, "params": []}
    members = _members(spec, cards, validation, omit_leader=omit_leader)
    prior = [] if omit_leader else _utility_keys(cards[spec.leader_id], skills)
    if spec.helper_id is not None:
        for key in _utility_keys(cards[spec.helper_id], skills):
            if key not in prior:
                prior.append(key)
    slots: list[list[str]] = []
    for sub in spec.subs:
        slots.append(_utility_keys(cards[sub.form_id], skills))
        if sub.assist is not None:
            slots.append(_utility_keys(cards[sub.assist.form_id], skills))
    if spec.leader_assist is not None:
        slots.append(_utility_keys(cards[spec.leader_assist.form_id], skills))
    if spec.helper_assist is not None:
        slots.append(_utility_keys(cards[spec.helper_assist.form_id], skills))
    return members, skills, cards, slots, prior


def _fielded_ids(spec: TeamSpec) -> set[int]:
    ids = {spec.leader_id}
    if spec.helper_id is not None:
        ids.add(spec.helper_id)
    for sub in spec.subs:
        ids.add(sub.form_id)
        if sub.assist is not None:
            ids.add(sub.assist.form_id)
    if spec.leader_assist is not None:
        ids.add(spec.leader_assist.form_id)
    if spec.helper_assist is not None:
        ids.add(spec.helper_assist.form_id)
    return ids


def _with_transforms(db: Any, ids: set[int]) -> set[int]:
    pending = list(ids)
    seen: set[int] = set()
    while pending and len(seen) < 32:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        rows = db.execute(
            "SELECT target_id FROM relationship WHERE source_id=? AND kind='transform'",
            (current,),
        )
        pending.extend(int(row["target_id"]) for row in rows)
    return seen


def _load_card(db: Any, monster_id: int) -> tuple[dict[str, Any], dict[int, dict[str, Any]]] | None:
    row = db.execute("SELECT id, name, raw FROM monster WHERE id=?", (monster_id,)).fetchone()
    if row is None:
        return None
    raw = json.loads(row["raw"]) if row["raw"] else {}
    card = dict(raw) if isinstance(raw, dict) else {}
    card["id"] = int(row["id"])
    card["name"] = str(row["name"])
    awakenings = [
        int(item["awakening_id"])
        for item in db.execute(
            "SELECT awakening_id FROM monster_awakening WHERE monster_id=? AND kind='normal' "
            "ORDER BY position",
            (monster_id,),
        )
    ]
    card["awakenings"] = awakenings
    skills: dict[int, dict[str, Any]] = {}
    active_id = 0
    leader_id = 0
    joined = db.execute(
        """SELECT ms.role AS role, skill.id AS id, skill.raw AS raw
        FROM monster_skill ms JOIN skill ON skill.id = ms.skill_id
        WHERE ms.monster_id=?""",
        (monster_id,),
    )
    for item in joined:
        skill = json.loads(item["raw"]) if item["raw"] else {}
        if not isinstance(skill, dict):
            skill = {}
        skill_id = int(item["id"])
        skill["id"] = skill_id
        skills[skill_id] = skill
        if item["role"] == "active":
            active_id = skill_id
        elif item["role"] == "leader":
            leader_id = skill_id
    card["activeSkillId"] = active_id
    card["leaderSkillId"] = leader_id
    return card, skills


def _members(
    spec: TeamSpec,
    cards: dict[int, dict[str, Any]],
    validation: dict[str, Any],
    *,
    omit_leader: bool = False,
) -> list[MemberInput]:
    members = []
    if not omit_leader:
        members.append(
            MemberInput(
                role="leader",
                card=cards[spec.leader_id],
                assist=_assist_card(spec.leader_assist, cards),
            )
        )
    failed = validation["evolution_feasible"] == CheckResult.FAIL.value
    for sub in spec.subs:
        members.append(
            MemberInput(
                role="sub",
                card=cards[sub.form_id],
                assist=_assist_card(sub.assist, cards),
                evolution_met=not (failed and sub.form_id != sub.owned_id),
            )
        )
    if spec.helper_id is not None:
        members.append(
            MemberInput(
                role="helper",
                card=cards[spec.helper_id],
                assist=_assist_card(spec.helper_assist, cards),
            )
        )
    return members


def _assist_card(
    assist: AssistSpec | None, cards: dict[int, dict[str, Any]]
) -> dict[str, Any] | None:
    if assist is None:
        return None
    return cards[assist.form_id]


def _leader_colors(card: dict[str, Any], skills: dict[int, dict[str, Any]]) -> list[str]:
    skill_id = int(card.get("leaderSkillId") or 0)
    skill = skills.get(skill_id)
    if skill is None:
        return []
    colors: list[str] = []
    for clause in leader_clauses(decode_skill(skill, skills)):
        if clause.evidence != "verified":
            continue
        for name in clause.condition.attributes:
            if name not in colors:
                colors.append(name)
    return colors


def _utility_keys(card: dict[str, Any], skills: dict[int, dict[str, Any]]) -> list[str]:
    skill_id = int(card.get("activeSkillId") or 0)
    skill = skills.get(skill_id)
    if skill is None:
        return []
    keys: list[str] = []
    for effect in decode_skill(skill, skills):
        if effect.evidence != "verified":
            continue
        if effect.kind == "evolving_skill":
            keys.extend(_stage_keys(effect))
            continue
        if effect.kind == "transform":
            continue
        keys.extend(_effect_keys(effect))
    return list(dict.fromkeys(keys))


def _stage_keys(effect: Effect) -> list[str]:
    parameters = effect.parameters
    stages = getattr(parameters, "stages", None)
    if not stages:
        return []
    keys: list[str] = []
    for stage_effect in stages[0].effects:
        if stage_effect.evidence != "verified" or stage_effect.kind == "transform":
            continue
        keys.extend(_effect_keys(stage_effect))
    return keys


def _effect_keys(effect: Effect) -> list[str]:
    if effect.kind != "absorb_void":
        return [effect.kind]
    names = effect.details.get("kinds") or []
    if isinstance(names, list) and names:
        return [f"absorb_void:{name}" for name in names]
    return ["absorb_void"]
