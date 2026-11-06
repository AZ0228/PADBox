"""Counterfactual explanations for one evaluated team.

Each fielded card gets a role, the verified effects only that card brings,
and a second evaluation with that card removed. The same scenario, ruleset,
and seed are used. No replacement card is evaluated, so the explanation does
not say one was compared or that this card ranks higher than another.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from padbox.database.store import Store
from padbox.mechanics.clauses import leader_clauses
from padbox.mechanics.decode import decode_skill
from padbox.mechanics.result import CheckResult
from padbox.mechanics.skill_state import cooldown_spec

Change = Literal["fell", "rose", "unchanged", "became_unresolved", "became_resolved"]
_ROLE_ORDER = (
    "absorb_void:attribute",
    "absorb_void:damage",
    "absorb_void:combo",
    "absorb_void",
    "void_pierce",
    "bind_clear",
    "awoken_bind_clear",
    "unmatchable_clear",
    "orb_convert",
    "board_change",
    "row_change",
    "column_change",
    "create_orbs",
    "skyfall",
    "damage_shield",
    "damage_cap",
    "heal_percent",
    "rcv_buff",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComponentChange(_Strict):
    """One quality component before and after the card is removed."""

    component: str
    before: float | None
    after: float | None
    change: Change


class RemovalResult(_Strict):
    """The team evaluated again without this card. ``after`` boosts stay null
    when the removed card is the leader, because no other leader was supplied
    to ``check_team``.
    """

    feasibility: CheckResult
    team_complete: bool
    changes: list[ComponentChange]
    lost_kinds: list[str]
    gained_kinds: list[str]
    skill_boost_before: int | None
    skill_boost_after: int | None
    skill_boost_required_before: int | None
    skill_boost_required_after: int | None
    transform_before: str | None
    transform_after: str | None
    detail: str


class CardExplanation(_Strict):
    """One fielded card. Alternatives stay empty unless one was evaluated."""

    slot: str
    monster_id: int
    owned_id: int | None
    name: str
    role: str
    unique_contribution: list[str]
    unresolved_effects: list[str]
    active_cooldown: int | None
    replaced_host_cooldown: int | None = None
    removal: RemovalResult
    alternatives_considered: list[int] = Field(default_factory=list)
    ranks_higher_because: str | None = None

    @model_validator(mode="after")
    def _ranking_needs_a_comparison(self) -> "CardExplanation":
        if self.ranks_higher_because and not self.alternatives_considered:
            raise ValueError("A ranking reason requires an alternative that was evaluated.")
        return self


class MeasuredDifference(_Strict):
    """One measured field of two supplied teams."""

    name: str
    left: float | int | str | None
    right: float | int | str | None


class TeamComparison(_Strict):
    """Differences between two evaluations. ``selected_team`` stays null."""

    left_label: str
    right_label: str
    objective: str
    seed: int
    turns: int
    simulations: int
    differences: list[MeasuredDifference]
    left_cards: list[CardExplanation]
    right_cards: list[CardExplanation]
    left_score: int | None
    right_score: int | None
    selected_team: None = None
    note: str


def explain_cards(
    spec: Any,
    scenario: Any,
    ruleset: Any,
    report: Any,
    *,
    store: Store,
    skill_index: dict[int, dict[str, Any]] | None,
) -> list[CardExplanation]:
    """Explain every fielded card by evaluating the team again without it."""
    from padbox.evaluator.evaluate import _roster, _utility_keys, evaluate_team

    roster = _roster(store, spec, report.validation, skill_index)
    cards: dict[int, dict[str, Any]] = {}
    skills: dict[int, dict[str, Any]] = {}
    if roster is not None:
        _members, skills, cards, _slot_rows, _prior = roster
    kinds = _usable(spec, cards, skills, _utility_keys)
    explanations = []
    for slot in _slots(spec, cards):
        reduced, omit_leader = _without(spec, slot)
        if omit_leader:
            removed = evaluate_team(
                spec,
                scenario,
                ruleset,
                store=store,
                skill_index=skill_index,
                explain=False,
                _omit_leader=True,
            )
            after_kinds = _usable(spec, cards, skills, _utility_keys, omit_leader=True)
            boost_after = (None, None, None)
        else:
            removed = evaluate_team(
                reduced,
                scenario,
                ruleset,
                store=store,
                skill_index=skill_index,
                explain=False,
            )
            after_kinds = _usable(reduced, cards, skills, _utility_keys)
            boost_after = _boost(removed.validation)
        explanations.append(
            _explain_slot(
                slot,
                report,
                removed,
                kinds,
                after_kinds,
                cards,
                skills,
                _utility_keys,
                boost_after,
                leader_omitted=omit_leader,
            )
        )
    return explanations


def compare_teams(
    left: Any,
    right: Any,
    scenario: Any = None,
    ruleset: Any = None,
    *,
    store: Store,
    skill_index: dict[int, dict[str, Any]] | None = None,
    left_label: str = "left",
    right_label: str = "right",
) -> TeamComparison:
    """Evaluate two supplied teams and list the measured differences.

    Neither team is selected. Card explanations still record no alternative,
    because this comparison does not replace one card with another.
    """
    from padbox.evaluator.evaluate import evaluate_team

    first = evaluate_team(left, scenario, ruleset, store=store, skill_index=skill_index)
    second = evaluate_team(right, scenario, ruleset, store=store, skill_index=skill_index)
    differences = _differences(first, second)
    note = (
        f"{left_label} and {right_label} were both evaluated under {first.scenario.name} "
        f"with seed {first.seed}, {first.scenario.turns} turns, and "
        f"{first.scenario.simulations} simulations. No card was replaced by an "
        "alternative, and neither team is selected."
    )
    return TeamComparison(
        left_label=left_label,
        right_label=right_label,
        objective=first.scenario.name,
        seed=first.seed,
        turns=first.scenario.turns,
        simulations=first.scenario.simulations,
        differences=differences,
        left_cards=list(first.cards),
        right_cards=list(second.cards),
        left_score=first.rank.score,
        right_score=second.rank.score,
        note=note,
    )


def _explain_slot(
    slot: dict[str, Any],
    report: Any,
    removed: Any,
    before_kinds: dict[str, set[str]],
    after_kinds: dict[str, set[str]],
    cards: dict[int, dict[str, Any]],
    skills: dict[int, dict[str, Any]],
    utility_keys: Any,
    boost_after: tuple[int | None, int | None, str | None],
    *,
    leader_omitted: bool,
) -> CardExplanation:
    card = cards.get(slot["monster_id"])
    keys = utility_keys(card, skills) if card is not None else []
    unresolved = _unresolved(card, skills) if card is not None else []
    present = before_kinds.get(slot["label"], set())
    others: set[str] = set()
    for label, kinds in before_kinds.items():
        if label != slot["label"]:
            others.update(kinds)
    unique = [kind for kind in keys if kind in present and kind not in others]
    if slot["kind"] == "leader" and card is not None:
        unique = [f"leader clause: {line}" for line in _clause_text(card, skills)] + unique
    before_set = set().union(*before_kinds.values()) if before_kinds else set()
    after_set = set().union(*after_kinds.values()) if after_kinds else set()
    lost = sorted(before_set - after_set)
    gained = sorted(after_set - before_set)
    boost_before = _boost(report.validation)
    if leader_omitted:
        boost_after = (None, None, None)
    changes = _changes(report, removed)
    active_cooldown = _minimum(card, skills) if card is not None else None
    host_cooldown = _host_cooldown(slot, cards, skills)
    role = _role(slot, card, skills, keys, unresolved)
    detail = _detail(
        changes,
        lost,
        gained,
        boost_before,
        boost_after,
        leader_omitted=leader_omitted,
        host_cooldown=host_cooldown if isinstance(host_cooldown, int) else None,
        active_cooldown=active_cooldown,
    )
    return CardExplanation(
        slot=slot["label"],
        monster_id=slot["monster_id"],
        owned_id=slot["owned_id"],
        name=slot["name"],
        role=role,
        unique_contribution=unique,
        unresolved_effects=unresolved,
        active_cooldown=active_cooldown,
        replaced_host_cooldown=host_cooldown if isinstance(host_cooldown, int) else None,
        removal=RemovalResult(
            feasibility=removed.feasibility,
            team_complete=removed.team_complete,
            changes=changes,
            lost_kinds=lost,
            gained_kinds=gained,
            skill_boost_before=boost_before[0],
            skill_boost_after=boost_after[0],
            skill_boost_required_before=boost_before[1],
            skill_boost_required_after=boost_after[1],
            transform_before=boost_before[2],
            transform_after=boost_after[2],
            detail=detail,
        ),
    )


def _slots(spec: Any, cards: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        _slot(
            "leader",
            spec.leader_id,
            None,
            cards,
            kind="leader",
        )
    ]
    if spec.leader_assist is not None:
        rows.append(
            _assist_slot("leader assist", spec.leader_assist, spec.leader_id, cards, index=None)
        )
    for index, sub in enumerate(spec.subs):
        rows.append(
            _slot(
                f"sub {index + 1}",
                sub.form_id,
                sub.owned_id,
                cards,
                kind="sub",
                index=index,
                host_id=sub.form_id if sub.assist is not None else None,
            )
        )
        if sub.assist is not None:
            rows.append(
                _assist_slot(
                    f"sub {index + 1} assist",
                    sub.assist,
                    sub.form_id,
                    cards,
                    index=index,
                )
            )
    if spec.helper_id is not None:
        rows.append(_slot("helper", spec.helper_id, None, cards, kind="helper"))
        if spec.helper_assist is not None:
            rows.append(
                _assist_slot(
                    "helper assist",
                    spec.helper_assist,
                    spec.helper_id,
                    cards,
                    index=None,
                )
            )
    return rows


def _slot(
    label: str,
    monster_id: int,
    owned_id: int | None,
    cards: dict[int, dict[str, Any]],
    *,
    kind: str,
    index: int | None = None,
    host_id: int | None = None,
) -> dict[str, Any]:
    card = cards.get(monster_id)
    return {
        "label": label,
        "kind": kind,
        "index": index,
        "monster_id": monster_id,
        "owned_id": owned_id,
        "name": str(card["name"]) if card is not None else f"#{monster_id}",
        "host_id": host_id,
        "host_cooldown": None,
    }


def _assist_slot(
    label: str,
    assist: Any,
    host_id: int,
    cards: dict[int, dict[str, Any]],
    *,
    index: int | None,
) -> dict[str, Any]:
    row = _slot(label, assist.form_id, assist.owned_id, cards, kind="assist", index=index)
    row["host_id"] = host_id
    return row


def _without(spec: Any, slot: dict[str, Any]) -> tuple[Any, bool]:
    kind = slot["kind"]
    if kind == "leader":
        return spec, True
    if kind == "helper":
        return spec.model_copy(update={"helper_id": None, "helper_assist": None}), False
    if kind == "sub":
        subs = [sub for index, sub in enumerate(spec.subs) if index != slot["index"]]
        return spec.model_copy(update={"subs": subs}), False
    if kind == "assist" and slot["label"] == "leader assist":
        return spec.model_copy(update={"leader_assist": None}), False
    if kind == "assist" and slot["label"] == "helper assist":
        return spec.model_copy(update={"helper_assist": None}), False
    subs = []
    for index, sub in enumerate(spec.subs):
        if index == slot["index"]:
            subs.append(sub.model_copy(update={"assist": None}))
        else:
            subs.append(sub)
    return spec.model_copy(update={"subs": subs}), False


def _usable(
    spec: Any,
    cards: dict[int, dict[str, Any]],
    skills: dict[int, dict[str, Any]],
    utility_keys: Any,
    *,
    omit_leader: bool = False,
) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    if not omit_leader and spec.leader_id in cards:
        label = "leader assist" if spec.leader_assist is not None else "leader"
        card_id = spec.leader_assist.form_id if spec.leader_assist is not None else spec.leader_id
        found[label] = set(utility_keys(cards[card_id], skills))
    for index, sub in enumerate(spec.subs, start=1):
        if sub.assist is not None and sub.assist.form_id in cards:
            found[f"sub {index} assist"] = set(utility_keys(cards[sub.assist.form_id], skills))
        elif sub.form_id in cards:
            found[f"sub {index}"] = set(utility_keys(cards[sub.form_id], skills))
    if spec.helper_id is not None and spec.helper_id in cards:
        label = "helper assist" if spec.helper_assist is not None else "helper"
        card_id = spec.helper_assist.form_id if spec.helper_assist is not None else spec.helper_id
        if card_id in cards:
            found[label] = set(utility_keys(cards[card_id], skills))
    return found


def _role(
    slot: dict[str, Any],
    card: dict[str, Any] | None,
    skills: dict[int, dict[str, Any]],
    keys: list[str],
    unresolved: list[str],
) -> str:
    if card is None:
        return "This card is not in the local reference set."
    parts: list[str] = []
    if slot["kind"] == "leader":
        clauses = _clause_text(card, skills)
        if clauses:
            parts.append("Leader clauses: " + "; ".join(clauses) + ".")
    primary = _primary(keys)
    if primary is not None:
        rest = [kind for kind in keys if kind != primary]
        sentence = f"Primary active role: {_label(primary)}."
        if rest:
            sentence += " Also " + ", ".join(_label(kind) for kind in rest) + "."
        parts.append(sentence)
    elif not unresolved:
        parts.append("No verified active effect.")
    if unresolved:
        parts.append("Unresolved active effects: " + ", ".join(unresolved) + ".")
    if slot["kind"] == "assist":
        parts.append(
            "This assist replaces the host active. Charge fills the host cooldown "
            "before the assist counter."
        )
    if slot.get("host_id") is not None and slot["kind"] == "sub":
        parts.append("An assist replaces this card's active while it is equipped.")
    return " ".join(parts)


def _clause_text(card: dict[str, Any], skills: dict[int, dict[str, Any]]) -> list[str]:
    skill_id = int(card.get("leaderSkillId") or 0)
    skill = skills.get(skill_id)
    if skill is None:
        return []
    lines = []
    for clause in leader_clauses(decode_skill(skill, skills)):
        if clause.evidence == "verified" and clause.summary:
            lines.append(clause.summary)
        elif clause.evidence != "verified":
            lines.append(f"unresolved clause {clause.clause_id}")
    return lines


def _primary(keys: list[str]) -> str | None:
    for name in _ROLE_ORDER:
        if name in keys:
            return name
    return keys[0] if keys else None


def _label(kind: str) -> str:
    return kind.replace("_", " ").replace(":", " ")


def _unresolved(card: dict[str, Any], skills: dict[int, dict[str, Any]]) -> list[str]:
    skill_id = int(card.get("activeSkillId") or 0)
    skill = skills.get(skill_id)
    if skill is None:
        return []
    names = []
    for effect in decode_skill(skill, skills):
        if effect.evidence == "verified" or effect.kind == "transform":
            continue
        if effect.kind not in names:
            names.append(effect.kind)
    return names


def _minimum(card: dict[str, Any], skills: dict[int, dict[str, Any]]) -> int | None:
    skill_id = int(card.get("activeSkillId") or 0)
    skill = skills.get(skill_id)
    if skill is None:
        return None
    minimum = cooldown_spec(skill).minimum
    return minimum if isinstance(minimum, int) else None


def _host_cooldown(
    slot: dict[str, Any],
    cards: dict[int, dict[str, Any]],
    skills: dict[int, dict[str, Any]],
) -> int | None:
    if slot["kind"] != "assist":
        return None
    host = cards.get(slot.get("host_id"))
    if host is None:
        return None
    return _minimum(host, skills)


def _changes(before: Any, after: Any) -> list[ComponentChange]:
    later = {component.name: component for component in after.components}
    rows = []
    for component in before.components:
        other = later[component.name]
        rows.append(
            ComponentChange(
                component=component.name,
                before=component.value,
                after=other.value,
                change=_direction(component.value, other.value),
            )
        )
    return rows


def _direction(before: float | None, after: float | None) -> Change:
    if before is None and after is None:
        return "unchanged"
    if before is not None and after is None:
        return "became_unresolved"
    if before is None and after is not None:
        return "became_resolved"
    if after < before:
        return "fell"
    if after > before:
        return "rose"
    return "unchanged"


def _boost(validation: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    block = validation.get("skill_boost") or {}
    total = block.get("total")
    required = block.get("required")
    result = block.get("result")
    return (
        total if isinstance(total, int) else None,
        required if isinstance(required, int) else None,
        str(result) if result is not None else None,
    )


def _detail(
    changes: list[ComponentChange],
    lost: list[str],
    gained: list[str],
    before: tuple[int | None, int | None, str | None],
    after: tuple[int | None, int | None, str | None],
    *,
    leader_omitted: bool,
    host_cooldown: int | None,
    active_cooldown: int | None,
) -> str:
    if leader_omitted:
        sentences = ["Re-evaluated the battle without this leader. No other leader was evaluated."]
    else:
        sentences = [
            "Re-evaluated without this card under the same scenario, seed, turns, "
            "and simulation count."
        ]
    for change in changes:
        if change.change == "unchanged":
            continue
        sentences.append(
            f"{change.component} {change.change} from {change.before} to {change.after}."
        )
    if lost:
        sentences.append("Lost kinds: " + ", ".join(lost) + ".")
    if gained:
        sentences.append("Gained kinds: " + ", ".join(gained) + ".")
    if before[0] is not None or before[1] is not None:
        sentences.append(
            f"Skill boost {before[0]} against required {before[1]} ({before[2]})."
        )
    if after[0] is not None or after[1] is not None or after[2] is not None:
        sentences.append(
            f"Without this card, skill boost {after[0]} against required {after[1]} "
            f"({after[2]})."
        )
    if host_cooldown is not None and active_cooldown is not None:
        sentences.append(
            f"Host minimum cooldown is {host_cooldown}. "
            f"Assist minimum cooldown is {active_cooldown}."
        )
        if active_cooldown > host_cooldown:
            sentences.append(
                "The assist cooldown is longer than the host cooldown, so the host "
                "active stays unavailable while the assist is equipped."
            )
    return " ".join(sentences)


def _differences(left: Any, right: Any) -> list[MeasuredDifference]:
    rows = [
        MeasuredDifference(
            name="feasibility",
            left=left.feasibility.value,
            right=right.feasibility.value,
        ),
        MeasuredDifference(
            name="transform",
            left=left.transform_feasible.value,
            right=right.transform_feasible.value,
        ),
        MeasuredDifference(
            name="mechanics_coverage",
            left=left.mechanics_coverage.value,
            right=right.mechanics_coverage.value,
        ),
    ]
    left_boost = _boost(left.validation)
    right_boost = _boost(right.validation)
    rows.append(
        MeasuredDifference(name="skill_boost_total", left=left_boost[0], right=right_boost[0])
    )
    rows.append(
        MeasuredDifference(
            name="skill_boost_required", left=left_boost[1], right=right_boost[1]
        )
    )
    later = {component.name: component for component in right.components}
    for component in left.components:
        other = later[component.name]
        rows.append(
            MeasuredDifference(name=component.name, left=component.value, right=other.value)
        )
    rows.append(
        MeasuredDifference(
            name="unresolved_cards",
            left=_unresolved_names(left),
            right=_unresolved_names(right),
        )
    )
    return rows


def _unresolved_names(report: Any) -> str:
    names = []
    for card in report.cards:
        if card.unresolved_effects and card.name not in names:
            names.append(card.name)
    return ", ".join(names)
