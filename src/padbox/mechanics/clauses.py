"""Leader skills as independent activation clauses.

Each clause has one condition and the effects that fire when that condition
is met. A linked-orb reduction and a cross-attack bonus stay two clauses.
Evaluation reads only the observation field for that condition: five linked
orbs do not count as a cross, and a cross does not count as a linked match.

An effect this module cannot place on a condition becomes an unknown clause.
Its verdict is unknown. Description text is not read.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from padbox.mechanics.decode import Effect

ConditionKind = Literal[
    "unconditional",
    "attribute",
    "card_type",
    "linked",
    "cross",
    "l_shape",
    "row",
    "combo",
    "hp",
    "multi_color",
    "awakening",
    "unknown",
]
EffectKind = Literal[
    "multiplier",
    "damage_reduction",
    "added_combos",
    "fixed_damage",
    "move_time",
    "shield",
    "unknown",
]
VerdictStatus = Literal["met", "unmet", "unknown"]

_ROW_COLOR = {
    "enhanced_fire_rows": "fire",
    "enhanced_water_rows": "water",
    "enhanced_light_rows": "light",
    "enhanced_dark_rows": "dark",
}
_COLOR_COUNT = {
    "three_attribute_attack": 3,
    "four_attribute_attack": 4,
    "five_attribute_attack": 5,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClauseCondition(_Strict):
    """The fact that has to be true before this clause's effects apply.

    `minimum` is the numeric threshold: linked orbs, rows, combos, HP
    percent, or matched colors. `combination` is `single` when one attribute
    carries that threshold. Several attributes on one linked clause stay
    `unknown`, because the skill record does not say whether every color is
    required.
    """

    kind: ConditionKind
    attributes: list[str] = Field(default_factory=list)
    card_types: list[str] = Field(default_factory=list)
    minimum: int | None = None
    comparison: Literal["at_least", "at_most"] | None = None
    combination: Literal["single", "unknown"] | None = None
    trigger: str | None = None


class ClauseEffect(_Strict):
    """One bonus granted by a clause. A cross component can carry several."""

    kind: EffectKind
    stat: Literal["atk", "rcv", "hp"] | None = None
    multiplier_hundredths: int | None = None
    percent: int | None = None
    combos: int | None = None
    damage: int | None = None
    seconds: int | None = None


class LeaderClause(_Strict):
    clause_id: str
    condition: ClauseCondition
    effects: list[ClauseEffect]
    skill_id: int | None
    source_type: int
    evidence: Literal["verified", "unknown"]
    summary: str
    raw_parameters: list[int] = Field(default_factory=list)


class MatchObservation(_Strict):
    """Facts supplied for one evaluation. One field does not fill another."""

    linked: dict[str, int] = Field(default_factory=dict)
    cross: bool = False
    l_shape: bool = False
    rows: dict[str, int] = Field(default_factory=dict)
    combos: int | None = None
    hp_percent: int | None = None
    attributes: list[str] = Field(default_factory=list)
    card_types: list[str] = Field(default_factory=list)
    color_count: int | None = None
    awakenings: list[str] = Field(default_factory=list)


class ClauseVerdict(_Strict):
    clause_id: str
    status: VerdictStatus


def leader_clauses(effects: list[Effect]) -> list[LeaderClause]:
    """Activation clauses for one leader skill, in component order.

    Rank EXP is a leader effect and is not an activation clause, so it is
    omitted. Every other effect that has no clause mapping becomes an
    unknown clause, including an unrecognized skill type.
    """
    clauses: list[LeaderClause] = []
    for effect in effects:
        clauses.extend(_clauses_from(effect))
    return clauses


def evaluate_clauses(
    clauses: list[LeaderClause], observation: MatchObservation
) -> list[ClauseVerdict]:
    """One verdict per clause, in the same order. Verdicts are not combined."""
    return [
        ClauseVerdict(clause_id=clause.clause_id, status=_status(clause, observation))
        for clause in clauses
    ]


def _clauses_from(effect: Effect) -> list[LeaderClause]:
    if effect.kind == "rank_exp":
        return []
    if effect.evidence != "verified":
        return [_unknown_clause(effect)]
    if effect.kind == "linked_orb_leader":
        return _linked_clauses(effect)
    if effect.kind == "awakening_trigger":
        return _trigger_clause(effect)
    if effect.kind in {"move_time", "move_time_bonus"}:
        return _move_clause(effect)
    return [_unknown_clause(effect)]


def _linked_clauses(effect: Effect) -> list[LeaderClause]:
    attributes = [str(name) for name in effect.details["attributes"]]
    minimum = int(effect.details["minimum_linked"])
    condition = ClauseCondition(
        kind="linked",
        attributes=attributes,
        minimum=minimum,
        combination="single" if len(attributes) == 1 else "unknown",
    )
    clauses: list[LeaderClause] = []
    atk = int(effect.details["atk_multiplier_hundredths"])
    shield = int(effect.details["shield_percent"])
    if atk not in {0, 100}:
        clauses.append(
            _clause(
                effect,
                condition,
                [ClauseEffect(kind="multiplier", stat="atk", multiplier_hundredths=atk)],
                "multiplier",
            )
        )
    if shield:
        clauses.append(
            _clause(
                effect,
                condition,
                [ClauseEffect(kind="damage_reduction", percent=shield)],
                "damage_reduction",
            )
        )
    return clauses or [_unknown_clause(effect)]


def _trigger_clause(effect: Effect) -> list[LeaderClause]:
    parts: list[ClauseEffect] = []
    atk = int(effect.details["atk_multiplier_hundredths"])
    rcv = int(effect.details["rcv_multiplier_hundredths"])
    shield = int(effect.details["shield_percent"])
    combos = int(effect.details["added_combos"])
    fixed = int(effect.details["fixed_damage"])
    if atk:
        parts.append(ClauseEffect(kind="multiplier", stat="atk", multiplier_hundredths=atk))
    if rcv:
        parts.append(ClauseEffect(kind="multiplier", stat="rcv", multiplier_hundredths=rcv))
    if shield:
        parts.append(ClauseEffect(kind="damage_reduction", percent=shield))
    if combos:
        parts.append(ClauseEffect(kind="added_combos", combos=combos))
    if fixed:
        parts.append(ClauseEffect(kind="fixed_damage", damage=fixed))
    if not parts:
        return [_unknown_clause(effect)]
    condition = _trigger_condition(str(effect.details["trigger"]))
    return [_clause(effect, condition, parts, "trigger")]


def _trigger_condition(trigger: str) -> ClauseCondition:
    if trigger == "cross_attack":
        return ClauseCondition(kind="cross", trigger=trigger)
    if trigger == "l_attack":
        return ClauseCondition(kind="l_shape", trigger=trigger)
    row = _ROW_COLOR.get(trigger)
    if row is not None:
        return ClauseCondition(kind="row", attributes=[row], minimum=1, trigger=trigger)
    colors = _COLOR_COUNT.get(trigger)
    if colors is not None:
        return ClauseCondition(kind="multi_color", minimum=colors, trigger=trigger)
    return ClauseCondition(kind="awakening", trigger=trigger)


def _move_clause(effect: Effect) -> list[LeaderClause]:
    seconds = effect.details.get("seconds")
    if not isinstance(seconds, int):
        return [_unknown_clause(effect)]
    return [
        _clause(
            effect,
            ClauseCondition(kind="unconditional"),
            [ClauseEffect(kind="move_time", seconds=seconds)],
            "move_time",
        )
    ]


def _clause(
    effect: Effect,
    condition: ClauseCondition,
    effects: list[ClauseEffect],
    suffix: str,
) -> LeaderClause:
    return LeaderClause(
        clause_id=f"skill:{effect.skill_id}/{suffix}",
        condition=condition.model_copy(deep=True),
        effects=effects,
        skill_id=effect.skill_id,
        source_type=effect.source_type,
        evidence="verified",
        summary=_summary(condition, effects),
    )


def _unknown_clause(effect: Effect) -> LeaderClause:
    raw = effect.details.get("params")
    return LeaderClause(
        clause_id=f"skill:{effect.skill_id}/type:{effect.source_type}",
        condition=ClauseCondition(kind="unknown"),
        effects=[ClauseEffect(kind="unknown")],
        skill_id=effect.skill_id,
        source_type=effect.source_type,
        evidence="unknown",
        summary=effect.summary,
        raw_parameters=[int(value) for value in raw] if isinstance(raw, list) else [],
    )


def _summary(condition: ClauseCondition, effects: list[ClauseEffect]) -> str:
    bonus = ", ".join(_effect_phrase(effect) for effect in effects)
    tail = _condition_phrase(condition)
    if tail:
        return f"{bonus} {tail}"
    return bonus


def _effect_phrase(effect: ClauseEffect) -> str:
    if effect.kind == "multiplier" and effect.multiplier_hundredths is not None:
        stat = (effect.stat or "atk").upper()
        return f"{_times(effect.multiplier_hundredths)}x {stat}"
    if effect.kind == "damage_reduction" and effect.percent is not None:
        return f"{effect.percent}% damage reduction"
    if effect.kind == "added_combos" and effect.combos is not None:
        return f"+{effect.combos} combos"
    if effect.kind == "fixed_damage" and effect.damage is not None:
        return f"{effect.damage:,} fixed damage"
    if effect.kind == "move_time" and effect.seconds is not None:
        return f"Move time is {effect.seconds} seconds"
    if effect.kind == "shield" and effect.percent is not None:
        return f"{effect.percent}% shield"
    return "unknown effect"


def _condition_phrase(condition: ClauseCondition) -> str:
    if condition.kind == "linked":
        colors = "/".join(condition.attributes)
        return f"with {condition.minimum}+ linked {colors}"
    if condition.kind == "cross":
        return "when a cross is used"
    if condition.kind == "l_shape":
        return "when an L match is used"
    if condition.kind == "row":
        color = "/".join(condition.attributes)
        return f"with {condition.minimum}+ {color} rows"
    if condition.kind == "combo" and condition.minimum is not None:
        return f"with {condition.minimum}+ combos"
    if condition.kind == "hp" and condition.minimum is not None:
        relation = "at least" if condition.comparison == "at_least" else "at most"
        return f"while HP is {relation} {condition.minimum}%"
    if condition.kind == "multi_color" and condition.minimum is not None:
        return f"with {condition.minimum}+ colors"
    if condition.kind == "attribute":
        return f"for {'/'.join(condition.attributes)}"
    if condition.kind == "card_type":
        return f"for {'/'.join(condition.card_types)}"
    if condition.kind == "awakening" and condition.trigger:
        return f"when {condition.trigger.replace('_', ' ')} is used"
    return ""


def _times(hundredths: int) -> str:
    if hundredths % 100 == 0:
        return str(hundredths // 100)
    return f"{hundredths / 100:.2f}".rstrip("0").rstrip(".")


def _status(clause: LeaderClause, observation: MatchObservation) -> VerdictStatus:
    if clause.evidence != "verified" or clause.condition.kind == "unknown":
        return "unknown"
    kind = clause.condition.kind
    if kind == "unconditional":
        return "met"
    if kind == "linked":
        return _linked_status(clause.condition, observation)
    if kind == "cross":
        return "met" if observation.cross else "unmet"
    if kind == "l_shape":
        return "met" if observation.l_shape else "unmet"
    if kind == "row":
        return _at_least(
            clause.condition,
            observation.rows.get(clause.condition.attributes[0], 0)
            if len(clause.condition.attributes) == 1
            else None,
        )
    if kind == "combo":
        return _at_least(clause.condition, observation.combos)
    if kind == "hp":
        return _hp_status(clause.condition, observation.hp_percent)
    if kind == "multi_color":
        return _at_least(clause.condition, observation.color_count)
    if kind == "attribute":
        return _member(clause.condition.attributes, observation.attributes)
    if kind == "card_type":
        return _member(clause.condition.card_types, observation.card_types)
    if kind == "awakening":
        if not clause.condition.trigger:
            return "unknown"
        return "met" if clause.condition.trigger in observation.awakenings else "unmet"
    return "unknown"


def _linked_status(condition: ClauseCondition, observation: MatchObservation) -> VerdictStatus:
    if condition.combination != "single" or len(condition.attributes) != 1:
        return "unknown"
    return _at_least(condition, observation.linked.get(condition.attributes[0], 0))


def _at_least(condition: ClauseCondition, value: int | None) -> VerdictStatus:
    if condition.minimum is None or value is None:
        return "unknown"
    if value >= condition.minimum:
        return "met"
    return "unmet"


def _hp_status(condition: ClauseCondition, hp_percent: int | None) -> VerdictStatus:
    if condition.minimum is None or condition.comparison is None or hp_percent is None:
        return "unknown"
    if condition.comparison == "at_least":
        return "met" if hp_percent >= condition.minimum else "unmet"
    if condition.comparison == "at_most":
        return "met" if hp_percent <= condition.minimum else "unmet"
    return "unknown"


def _member(required: list[str], present: list[str]) -> VerdictStatus:
    if len(required) != 1:
        return "unknown"
    if required[0] in present:
        return "met"
    return "unmet"
