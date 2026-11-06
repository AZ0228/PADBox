"""Decode skill type/params into typed effects.

`read_skill` is syntactic: it keeps the skill type and raw parameter list.
It does not read description text and does not assign a game-rule meaning.
Interpretation then accepts only layouts checked against repeated NA
description pairings. Those effects are verified. Any other layout is an
explicit unsupported effect.

Compound actives (type 116) and leader skills (type 138) list independent
components, so those components stay separate effects. A component whose
parameters belong together stays one effect: an absorb void lists every kind
it voids, and a linked-orb shield keeps its link condition. An evolving skill
(type 232) stays one grouped effect whose stages are nested, not simultaneous.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FULL_CLEAR = 9999
DECODER_VERSION = "6"

ATTRIBUTES = {0: "fire", 1: "water", 2: "wood", 3: "light", 4: "dark"}
ATTRIBUTE_BITS = {1: "fire", 2: "water", 4: "wood", 8: "light", 16: "dark"}
# Same low bits as attributes, then heal and the hazard orbs used by conversions.
ORB_BITS = {
    1: "fire",
    2: "water",
    4: "wood",
    8: "light",
    16: "dark",
    32: "heal",
    64: "jammer",
    128: "poison",
    256: "lethal poison",
    512: "bomb",
}
# Target codes whose English descriptions agreed on every checked skill.
# Damage-cap targets whose descriptions agreed. Target 1 is this ally for a cap.
CAP_TARGETS = {
    1: "this ally",
    2: "the leader",
    3: "this ally and the leader",
    5: "this ally and the helper",
    6: "the leader and the helper",
    8: "all subs",
    9: "this ally and all subs",
    15: "all allies",
}
CARD_TYPES = {
    1: "Balanced",
    2: "Physical",
    3: "Healer",
    4: "Dragon",
    5: "God",
    6: "Attacker",
    7: "Devil",
    8: "Machine",
}
TEAM_ATK = {
    2: "the leader",
    3: "this ally and the leader",
    4: "the helper",
    5: "this ally and the helper",
    6: "the leader and the helper",
    8: "all subs",
    9: "this ally and all subs",
}
ORBS = {0: "fire", 1: "water", 2: "wood", 3: "light", 4: "dark", 5: "heal"}
# Scalar orb codes for a direct change. These are not the bit masks used above.
CHANGE_ORBS = {
    **ORBS,
    6: "jammer",
    7: "poison",
    8: "lethal poison",
}
BOARDS = {1: (7, 6), 2: (5, 4), 3: (6, 5)}
# One description token was present on every leader skill that set only this bit.
TRIGGER_BITS = {
    0: "two_pronged_attack",
    1: "damage_void_piercer",
    2: "l_attack",
    3: "cross_attack",
    4: "t_attack",
    5: "enhanced_fire_rows",
    6: "enhanced_water_rows",
    8: "enhanced_light_rows",
    9: "enhanced_dark_rows",
    10: "three_attribute_attack",
    11: "four_attribute_attack",
    12: "five_attribute_attack",
    15: "enhanced_fire_combos",
    16: "enhanced_water_combos",
    17: "enhanced_wood_combos",
    18: "enhanced_light_combos",
    19: "enhanced_dark_combos",
    21: "super_enhanced_matching",
    22: "fire_and_water_attack",
    23: "water_and_wood_attack",
    24: "wood_and_fire_attack",
}
ABSORB_KINDS = ("attribute", "combo", "damage")


class EffectParameters(BaseModel):
    """Typed parameters. This is the interpreted shape, not the raw integer list."""

    model_config = ConfigDict(extra="forbid")


class Duration(BaseModel):
    turns: int | None = None
    full: bool = False
    start_turn: int | None = None
    end_turn: int | None = None


class SkillSyntax(BaseModel):
    """What the raw skill record says, before a game-rule meaning is attached."""

    skill_id: int | None
    source_type: int
    raw_parameters: list[int]


class EffectProvenance(BaseModel):
    source: str
    decoder_version: str
    evidence_level: Literal["syntactic", "verified", "unsupported", "source_encoded"]
    supported_parameters: list[str]
    unknown_parameters: list[str]
    verification_status: Literal["verified", "unverified", "unsupported"]


class UnsupportedParameters(EffectParameters):
    raw: list[int] = Field(default_factory=list)
    component_id: int | None = None


class CreateOrbsParameters(EffectParameters):
    count: int
    colors: list[str]


class OrbReplaceParameters(EffectParameters):
    count: int
    excluded_colors: list[str]
    destination: list[str]


class OrbConvertParameters(EffectParameters):
    source_colors: list[str]
    destination: list[str]


class OrbSwap(BaseModel):
    source: str
    destination: str


class OrbChangeParameters(EffectParameters):
    changes: list[OrbSwap]
    destinations: list[str]


class BoardChangeParameters(EffectParameters):
    orbs: list[str]


class BoardSizeParameters(EffectParameters):
    duration: Duration
    columns: int
    rows: int


class LinePlacement(BaseModel):
    where: str
    orbs: list[str]


class LineChangeParameters(EffectParameters):
    lines: list[LinePlacement]
    destinations: list[str]


class LockClearParameters(EffectParameters):
    full: bool


class SkyfallParameters(EffectParameters):
    duration: Duration
    orbs: list[str]
    rate: int
    mode: str


class RouletteParameters(EffectParameters):
    duration: Duration
    count: int
    speed: str


class DamageShieldParameters(EffectParameters):
    duration: Duration
    percent: int


class AttributeGuardParameters(EffectParameters):
    duration: Duration
    attribute: str
    percent: int


class StatModifierParameters(EffectParameters):
    duration: Duration | None = None
    multiplier_hundredths: int | None = None
    attribute: str | None = None
    card_type: str | None = None
    target: str | None = None


class HpCostParameters(EffectParameters):
    to_one: bool = False
    remaining_percent: int | None = None


class HealParameters(EffectParameters):
    duration: Duration | None = None
    multiplier_hundredths: int | None = None
    hp: int | None = None
    percent: int | None = None


class AbsorbVoidParameters(EffectParameters):
    duration: Duration
    kinds: list[str]


class TimedEffectParameters(EffectParameters):
    duration: Duration


class BindClearParameters(TimedEffectParameters):
    pass


class AwokenBindClearParameters(TimedEffectParameters):
    pass


class UnmatchableClearParameters(TimedEffectParameters):
    pass


class DelayParameters(TimedEffectParameters):
    pass


class HasteParameters(TimedEffectParameters):
    pass


class VoidPierceParameters(TimedEffectParameters):
    pass


class NoSkyfallParameters(TimedEffectParameters):
    pass


class DefenseBreakParameters(EffectParameters):
    duration: Duration
    percent: int


class SingleAttackParameters(EffectParameters):
    attribute: str
    multiplier_hundredths: int


class TransformParameters(EffectParameters):
    monster_id: int


class DamageCapParameters(EffectParameters):
    duration: Duration
    billion_tenths: int
    target: str


class AttributeChangeParameters(EffectParameters):
    duration: Duration
    attribute: str


class MoveTimeParameters(EffectParameters):
    seconds: int | float | None = None
    multiplier_hundredths: int | None = None
    duration: Duration | None = None


class LinkedOrbParameters(EffectParameters):
    attributes: list[str]
    minimum_linked: int
    atk_multiplier_hundredths: int
    shield_percent: int


class AwakeningTriggerParameters(EffectParameters):
    trigger: str
    atk_multiplier_hundredths: int
    rcv_multiplier_hundredths: int
    shield_percent: int
    added_combos: int
    fixed_damage: int


class OrbEnhanceParameters(EffectParameters):
    orbs: list[str]


class AddedCombosParameters(EffectParameters):
    duration: Duration
    combos: int


class PercentParameters(EffectParameters):
    percent: int


class ShieldBreakParameters(EffectParameters):
    shields: int | None = None
    percent: int | None = None


class ComboOrbParameters(EffectParameters):
    count: int | None = None
    source_color: str | None = None


class RankExpParameters(EffectParameters):
    multiplier_hundredths: int


class MassAttackParameters(EffectParameters):
    attribute: str
    multiplier_hundredths: int


class EmptyParameters(EffectParameters):
    pass


class RecordedParameters(EffectParameters):
    """Verified effect whose details have not been given a named model yet."""

    values: dict[str, Any]


class AwakeningParameters(EffectParameters):
    awakening_id: int
    name: str | None
    effects: dict[str, Any]


class Effect(BaseModel):
    kind: str
    evidence: str
    source_type: int
    skill_id: int | None = None
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    parameters: Any = None
    provenance: EffectProvenance | None = None

    @model_validator(mode="before")
    @classmethod
    def _attach_structure(cls, data: Any) -> Any:
        if not isinstance(data, dict) or data.get("provenance") is not None:
            return data
        details = dict(data.get("details") or {})
        kind = str(data.get("kind") or "unsupported")
        evidence = str(data.get("evidence") or "unknown")
        source_type = data.get("source_type")
        source_type = int(source_type) if source_type is not None else -1
        parameters, supported, unknown = _parameters_from(kind, details)
        if kind == "unsupported" or evidence == "unknown":
            level = "unsupported"
            status = "unsupported"
        elif evidence == "verified":
            level = "verified"
            status = "verified"
        elif evidence == "source_encoded":
            level = "source_encoded"
            status = "unverified"
        else:
            level = "syntactic"
            status = "unverified"
        data["parameters"] = parameters
        data["provenance"] = EffectProvenance(
            source=_source(data.get("skill_id"), source_type),
            decoder_version=DECODER_VERSION,
            evidence_level=level,
            supported_parameters=supported,
            unknown_parameters=unknown,
            verification_status=status,
        )
        return data


class SkillStage(BaseModel):
    skill_id: int | None
    source_type: int
    effects: list[Effect]


class EvolvingParameters(EffectParameters):
    stages: list[SkillStage]
    loops: bool | None = None


def read_skill(skill: dict[str, Any]) -> SkillSyntax:
    """Copy type and parameters. Description text is ignored on purpose."""
    raw_type = skill.get("type")
    return SkillSyntax(
        skill_id=int(skill.get("id") or 0) or None,
        source_type=int(raw_type) if raw_type is not None else -1,
        raw_parameters=[int(value) for value in skill.get("params") or []],
    )


def _source(skill_id: int | None, source_type: int | None) -> str:
    if skill_id:
        return f"skill:{int(skill_id)}/type:{int(source_type)}"
    return f"type:{int(source_type)}"


def _bind_source(effect: Effect, skill_id: int | None) -> Effect:
    effect.skill_id = skill_id or None
    if effect.provenance is not None:
        effect.provenance.source = _source(effect.skill_id, effect.source_type)
    return effect


def decode_skill(
    skill: dict[str, Any], index: dict[int, dict[str, Any]], seen: set[int] | None = None
) -> list[Effect]:
    """Interpret one skill. `index` maps every component skill ID to its raw record."""
    visiting = set() if seen is None else seen
    syntax = read_skill(skill)
    if syntax.skill_id and syntax.skill_id in visiting:
        return [_bind_source(_unsupported_syntax(syntax, "skill reference cycle"), syntax.skill_id)]
    if syntax.skill_id:
        visiting.add(syntax.skill_id)
    try:
        return _interpret(syntax, index, visiting)
    finally:
        if syntax.skill_id:
            visiting.discard(syntax.skill_id)


def _interpret(
    syntax: SkillSyntax, index: dict[int, dict[str, Any]], visiting: set[int]
) -> list[Effect]:
    if syntax.source_type in {116, 138}:
        return _independent_components(syntax, index, visiting)
    if syntax.source_type == 232:
        return [_evolving(syntax, index, visiting)]
    return _decode_leaf(syntax.skill_id, syntax.source_type, syntax.raw_parameters)


def _independent_components(
    syntax: SkillSyntax, index: dict[int, dict[str, Any]], visiting: set[int]
) -> list[Effect]:
    """Each component fires with the skill. None of them is a later stage."""
    effects: list[Effect] = []
    for child_id in syntax.raw_parameters:
        child = index.get(child_id)
        if child is None:
            effects.append(
                _bind_source(
                    Effect(
                        kind="unsupported",
                        evidence="unknown",
                        source_type=syntax.source_type,
                        summary=f"Component skill {child_id} is not in the local skill index",
                        details={"component_id": child_id},
                    ),
                    syntax.skill_id,
                )
            )
            continue
        effects.extend(decode_skill(child, index, visiting))
    return effects


def _evolving(
    syntax: SkillSyntax, index: dict[int, dict[str, Any]], visiting: set[int]
) -> Effect:
    """Stages are alternatives over time. They are not one flat active."""
    stages: list[SkillStage] = []
    missing: list[int] = []
    for child_id in syntax.raw_parameters:
        child = index.get(child_id)
        if child is None:
            missing.append(child_id)
            continue
        child_syntax = read_skill(child)
        stages.append(
            SkillStage(
                skill_id=child_syntax.skill_id,
                source_type=child_syntax.source_type,
                effects=decode_skill(child, index, visiting),
            )
        )
    return Effect(
        kind="evolving_skill",
        evidence="syntactic",
        source_type=syntax.source_type,
        skill_id=syntax.skill_id,
        summary=f"Evolving skill with {len(stages)} stages",
        details={"stage_ids": list(syntax.raw_parameters)},
        parameters=EvolvingParameters(stages=stages, loops=None),
        provenance=EffectProvenance(
            source=_source(syntax.skill_id, syntax.source_type),
            decoder_version=DECODER_VERSION,
            evidence_level="syntactic",
            supported_parameters=["stages"],
            unknown_parameters=["loops", *[f"stage:{child_id}" for child_id in missing]],
            verification_status="unverified",
        ),
    )


def _decode_leaf(skill_id: int | None, kind: int, params: list[int]) -> list[Effect]:
    decoded = _LEAVES.get(kind, _unsupported)(kind, params)
    return [_bind_source(effect, skill_id) for effect in decoded]


def _unsupported(kind: int, params: list[int]) -> list[Effect]:
    return [_unknown_leaf(kind, params, f"Unrecognized skill type {kind}")]


def _unknown_leaf(kind: int, params: list[int], reason: str) -> Effect:
    return Effect(
        kind="unsupported",
        evidence="unknown",
        source_type=kind,
        summary=reason,
        details={"params": params},
    )


def _unsupported_syntax(syntax: SkillSyntax, reason: str) -> Effect:
    return _unknown_leaf(syntax.source_type, syntax.raw_parameters, reason)


def decode_awakening(awakening_id: int) -> Effect:
    """One awakening is one grouped effect. Magnitudes are checklist values, not battle proof."""
    from padbox.mechanics.awakenings import CHECKLIST, NAMES

    name = NAMES.get(awakening_id)
    checklist = dict(CHECKLIST.get(awakening_id) or {})
    source = f"awakening:{awakening_id}"
    if name is None:
        return Effect(
            kind="unsupported",
            evidence="unknown",
            source_type=-1,
            summary=f"Unrecognized awakening {awakening_id}",
            details={"params": [awakening_id]},
            parameters=UnsupportedParameters(raw=[awakening_id]),
            provenance=EffectProvenance(
                source=source,
                decoder_version=DECODER_VERSION,
                evidence_level="unsupported",
                supported_parameters=[],
                unknown_parameters=[f"[0]={awakening_id}"],
                verification_status="unsupported",
            ),
        )
    supported = ["name", *checklist.keys()]
    unknown = [] if checklist else ["magnitude"]
    return Effect(
        kind="awakening",
        evidence="source_encoded",
        source_type=-1,
        summary=name,
        details={"awakening_id": awakening_id, "name": name, "effects": checklist},
        parameters=AwakeningParameters(
            awakening_id=awakening_id, name=name, effects=checklist
        ),
        provenance=EffectProvenance(
            source=source,
            decoder_version=DECODER_VERSION,
            evidence_level="source_encoded",
            supported_parameters=supported,
            unknown_parameters=unknown,
            verification_status="unverified",
        ),
    )


def _turns(value: int) -> dict[str, Any]:
    if value == FULL_CLEAR:
        return {"full": True}
    return {"full": False, "turns": value}


def _clear_summary(name: str, value: int) -> str:
    if value == FULL_CLEAR:
        return f"Fully clears {name}"
    return f"Clears {value} turns of {name}"


def _multiplier(hundredths: int) -> str:
    if hundredths % 100 == 0:
        return str(hundredths // 100)
    text = f"{hundredths / 100:.2f}".rstrip("0").rstrip(".")
    return text


def _positive(value: int) -> bool:
    return value > 0


def _recovery(kind: int, params: list[int]) -> list[Effect]:
    if not params or len(params) > 5:
        return [_unknown_leaf(kind, params, "Unrecognized recovery parameters")]
    bind = params[0]
    rcv = params[1] if len(params) > 1 else 0
    flat_hp = params[2] if len(params) > 2 else 0
    percent_hp = params[3] if len(params) > 3 else 0
    awoken = params[4] if len(params) > 4 else 0
    effects: list[Effect] = []
    if bind:
        effects.append(
            Effect(
                kind="bind_clear",
                evidence="verified",
                source_type=kind,
                summary=_clear_summary("bind", bind),
                details=_turns(bind),
            )
        )
    if rcv:
        effects.append(
            Effect(
                kind="heal_rcv",
                evidence="verified",
                source_type=kind,
                summary=f"Recovers {_multiplier(rcv)}x RCV as HP",
                details={"multiplier_hundredths": rcv},
            )
        )
    if flat_hp:
        effects.append(
            Effect(
                kind="heal_flat",
                evidence="verified",
                source_type=kind,
                summary=f"Recovers {flat_hp:,} HP",
                details={"hp": flat_hp},
            )
        )
    if percent_hp:
        effects.append(
            Effect(
                kind="heal_percent",
                evidence="verified",
                source_type=kind,
                summary=f"Recovers {percent_hp}% HP",
                details={"percent": percent_hp},
            )
        )
    if awoken:
        effects.append(
            Effect(
                kind="awoken_bind_clear",
                evidence="verified",
                source_type=kind,
                summary=_clear_summary("awoken bind", awoken),
                details=_turns(awoken),
            )
        )
    return effects or [_unknown_leaf(kind, params, "Recovery parameters have no effect")]


def _unmatchable(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized unmatchable-clear parameters")]
    return [
        Effect(
            kind="unmatchable_clear",
            evidence="verified",
            source_type=kind,
            summary=_clear_summary("unmatchable orbs", params[0]),
            details=_turns(params[0]),
        )
    ]


def _haste(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or params[0] != params[1] or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized skill-charge parameters")]
    return [
        Effect(
            kind="charge_other_skills",
            evidence="verified",
            source_type=kind,
            summary=f"Charges other skills by {params[0]} turns",
            details={"turns": params[0]},
        )
    ]


def _transform(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized transform parameters")]
    return [
        Effect(
            kind="transform",
            evidence="verified",
            source_type=kind,
            summary=f"Transforms into #{params[0]}",
            details={"monster_id": params[0]},
        )
    ]


def _void_pierce(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized damage-void parameters")]
    return [
        Effect(
            kind="void_pierce",
            evidence="verified",
            source_type=kind,
            summary=f"Pierces damage void for {params[0]} turns",
            details={"turns": params[0]},
        )
    ]


def _board(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _positive(params[0]) or params[1] not in BOARDS:
        return [_unknown_leaf(kind, params, "Unrecognized board-size parameters")]
    columns, rows = BOARDS[params[1]]
    return [
        Effect(
            kind="board_size",
            evidence="verified",
            source_type=kind,
            summary=f"Board becomes {columns}x{rows} for {params[0]} turns",
            details={"turns": params[0], "columns": columns, "rows": rows},
        )
    ]


def _absorb(kind: int, params: list[int]) -> list[Effect]:
    if not params or not _positive(params[0]) or len(params) > 4:
        return [_unknown_leaf(kind, params, "Unrecognized absorption parameters")]
    flags = params[1:]
    if not flags or any(flag not in {0, 1} for flag in flags):
        return [_unknown_leaf(kind, params, "Unrecognized absorption parameters")]
    if len(flags) == 1 and flags[0] != 1:
        return [_unknown_leaf(kind, params, "Unrecognized absorption parameters")]
    selected = [name for name, flag in zip(ABSORB_KINDS, flags, strict=False) if flag]
    if not selected:
        return [_unknown_leaf(kind, params, "Unrecognized absorption parameters")]
    label = " and ".join(name.replace("_", " ") for name in selected)
    return [
        Effect(
            kind="absorb_void",
            evidence="verified",
            source_type=kind,
            summary=f"Voids {label} absorption for {params[0]} turns",
            details={"turns": params[0], "kinds": selected},
        )
    ]


def _allies_atk(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or not _positive(params[0]) or not _positive(params[2]):
        return [_unknown_leaf(kind, params, "Unrecognized attack-buff target")]
    turns, target, multiplier = params
    if target == 15:
        return [
            Effect(
                kind="all_allies_atk",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(multiplier)}x ATK for all allies for {turns} turns",
                details={"turns": turns, "multiplier_hundredths": multiplier},
            )
        ]
    label = TEAM_ATK.get(target)
    if label is None:
        return [_unknown_leaf(kind, params, "Unrecognized attack-buff target")]
    return [
        Effect(
            kind="team_atk",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(multiplier)}x ATK for {label} for {turns} turns",
            details={"turns": turns, "target": label, "multiplier_hundredths": multiplier},
        )
    ]


def _attribute_atk(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or not _positive(params[0]) or not _positive(params[2]):
        return [_unknown_leaf(kind, params, "Unrecognized attribute attack buff")]
    if params[1] == 5:
        return [
            Effect(
                kind="rcv_buff",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(params[2])}x RCV for {params[0]} turns",
                details={"turns": params[0], "multiplier_hundredths": params[2]},
            )
        ]
    if params[1] not in ATTRIBUTES:
        return [_unknown_leaf(kind, params, "Unrecognized attribute attack buff")]
    attribute = ATTRIBUTES[params[1]]
    return [
        Effect(
            kind="attribute_atk",
            evidence="verified",
            source_type=kind,
            summary=(f"{_multiplier(params[2])}x ATK for {attribute} for {params[0]} turns"),
            details={
                "turns": params[0],
                "attribute": attribute,
                "multiplier_hundredths": params[2],
            },
        )
    ]


def _mass_attack(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or params[0] not in ATTRIBUTES or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized mass-attack parameters")]
    attribute = ATTRIBUTES[params[0]]
    return [
        Effect(
            kind="mass_attack",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[1])}x ATK {attribute} mass attack",
            details={"attribute": attribute, "multiplier_hundredths": params[1]},
        )
    ]


def _orb_bit_names(mask: int) -> list[str] | None:
    if mask <= 0 or mask & ~1023:
        return None
    return [name for bit, name in ORB_BITS.items() if mask & bit]


def _create_orbs(kind: int, params: list[int]) -> list[Effect]:
    # Two equal color fields, or one field, create that many of each listed orb.
    # A larger second field changes that many orbs which are not the extra colors.
    if len(params) == 2 and _positive(params[0]):
        return _spawn_orbs(kind, params[0], params[1])
    if len(params) != 3 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized orb creation")]
    if params[1] == params[2]:
        return _spawn_orbs(kind, params[0], params[1])
    if params[1] and params[1] & ~params[2] == 0:
        destination = _orb_bit_names(params[1])
        excluded = _orb_bit_names(params[2] & ~params[1])
        if destination and excluded:
            return [
                Effect(
                    kind="orb_replace",
                    evidence="verified",
                    source_type=kind,
                    summary=(
                        f"Changes {params[0]} orbs that are not {' or '.join(excluded)} "
                        f"to {', '.join(destination)}"
                    ),
                    details={"count": params[0], "excluded": excluded, "to": destination},
                )
            ]
    return [_unknown_leaf(kind, params, "Unrecognized orb creation")]


def _spawn_orbs(kind: int, count: int, mask: int) -> list[Effect]:
    orbs = _orb_bit_names(mask)
    if not orbs:
        return [_unknown_leaf(kind, [count, mask], "Unrecognized orb color in orb creation")]
    if len(orbs) == 1:
        summary = f"Creates {count} {orbs[0]} at random"
    else:
        summary = f"Creates {count} of each of {', '.join(orbs)} at random"
    return [
        Effect(
            kind="create_orbs",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"count": count, "attributes": orbs},
        )
    ]


def _orb_convert(kind: int, params: list[int]) -> list[Effect]:
    # One destination color. Several destinations are not one repeated meaning.
    if len(params) != 2:
        return [_unknown_leaf(kind, params, "Unrecognized orb conversion")]
    source = _orb_bit_names(params[0])
    destination = _orb_bit_names(params[1])
    if not source or not destination or len(destination) != 1:
        return [_unknown_leaf(kind, params, "Unrecognized orb conversion")]
    return [
        Effect(
            kind="orb_convert",
            evidence="verified",
            source_type=kind,
            summary=f"Changes {', '.join(source)} to {destination[0]}",
            details={"from": source, "to": destination},
        )
    ]


def _board_change(kind: int, params: list[int]) -> list[Effect]:
    colors: list[str] = []
    for code in params:
        if code == -1:
            break
        name = ORBS.get(code)
        if name is None:
            return [_unknown_leaf(kind, params, "Unrecognized orb color in a board change")]
        colors.append(name)
    if not colors:
        return [_unknown_leaf(kind, params, "Board change lists no orbs")]
    return [
        Effect(
            kind="board_change",
            evidence="verified",
            source_type=kind,
            summary="Changes the board to " + ", ".join(colors),
            details={"orbs": colors},
        )
    ]


def _move_time(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not 1 <= params[0] <= 30:
        return [_unknown_leaf(kind, params, "Unrecognized fixed move time")]
    return [
        Effect(
            kind="move_time",
            evidence="verified",
            source_type=kind,
            summary=f"Move time is {params[0]} seconds",
            details={"seconds": params[0]},
        )
    ]


def _move_time_bonus(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]) or params[0] % 100:
        return [_unknown_leaf(kind, params, "Unrecognized move-time bonus")]
    seconds = params[0] // 100
    return [
        Effect(
            kind="move_time_bonus",
            evidence="verified",
            source_type=kind,
            summary=f"Orb move time +{seconds} seconds",
            details={"seconds": seconds},
        )
    ]


def _rank_exp(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized rank-experience parameters")]
    return [
        Effect(
            kind="rank_exp",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[0])}x Rank EXP when entering as leader",
            details={"multiplier_hundredths": params[0]},
        )
    ]


def _attribute_names(mask: int) -> list[str] | None:
    if mask <= 0 or mask & ~0b11111:
        return None
    return [name for bit, name in ATTRIBUTE_BITS.items() if mask & bit]


def _linked_shield(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 4 or not _positive(params[1]) or not 0 <= params[3] <= 100:
        return [_unknown_leaf(kind, params, "Unrecognized linked-orb leader parameters")]
    attributes = _attribute_names(params[0])
    if attributes is None or params[2] < 0:
        return [_unknown_leaf(kind, params, "Unrecognized linked-orb leader parameters")]
    parts = [f"{params[3]}% damage reduction"]
    if params[2] not in {0, 100}:
        parts.insert(0, f"{_multiplier(params[2])}x ATK")
    summary = f"{' and '.join(parts)} with {params[1]}+ linked {'/'.join(attributes)} orbs"
    return [
        Effect(
            kind="linked_orb_leader",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={
                "attributes": attributes,
                "minimum_linked": params[1],
                "atk_multiplier_hundredths": params[2],
                "shield_percent": params[3],
            },
        )
    ]


def _trigger_bonus(kind: int, params: list[int]) -> list[Effect]:
    if not params or len(params) > 6:
        return [_unknown_leaf(kind, params, "Unrecognized awakening-trigger parameters")]
    bits = params[0]
    known = [TRIGGER_BITS[bit] for bit in range(32) if bits & (1 << bit) and bit in TRIGGER_BITS]
    unknown_bits = [bit for bit in range(32) if bits & (1 << bit) and bit not in TRIGGER_BITS]
    if len(known) != 1 or unknown_bits:
        return [_unknown_leaf(kind, params, "Awakening trigger is not a single verified bit")]
    atk = params[1] if len(params) > 1 else 0
    shield = params[2] if len(params) > 2 else 0
    combos = params[3] if len(params) > 3 else 0
    fixed = params[4] if len(params) > 4 else 0
    rcv = params[5] if len(params) > 5 else 0
    if atk < 0 or shield < 0 or combos < 0 or fixed < 0 or rcv < 0 or shield > 100:
        return [_unknown_leaf(kind, params, "Unrecognized awakening-trigger parameters")]
    clauses: list[str] = []
    if atk:
        clauses.append(f"{_multiplier(atk)}x ATK")
    if rcv:
        clauses.append(f"{_multiplier(rcv)}x RCV")
    if shield:
        clauses.append(f"{shield}% damage reduction")
    if combos:
        clauses.append(f"+{combos} combos")
    if fixed:
        clauses.append(f"{fixed:,} fixed damage")
    if not clauses:
        return [_unknown_leaf(kind, params, "Awakening trigger has no verified bonus")]
    trigger = known[0].replace("_", " ")
    return [
        Effect(
            kind="awakening_trigger",
            evidence="verified",
            source_type=kind,
            summary=f"When {trigger} is used: {', '.join(clauses)}",
            details={
                "trigger": known[0],
                "atk_multiplier_hundredths": atk,
                "rcv_multiplier_hundredths": rcv,
                "shield_percent": shield,
                "added_combos": combos,
                "fixed_damage": fixed,
            },
        )
    ]


def _for_turns(turns: int) -> str:
    unit = "turn" if turns == 1 else "turns"
    return f"for {turns} {unit}"


def _percent_guard(turns: int, percent: int) -> bool:
    return _positive(turns) and 1 <= percent <= 100


def _damage_shield(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _percent_guard(params[0], params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized damage-shield parameters")]
    turns, percent = params
    if percent == 100:
        summary = f"Voids damage {_for_turns(turns)}"
    else:
        summary = f"Reduces damage by {percent}% {_for_turns(turns)}"
    return [
        Effect(
            kind="damage_shield",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"turns": turns, "percent": percent},
        )
    ]


def _gravity(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not 1 <= params[0] <= 100:
        return [_unknown_leaf(kind, params, "Unrecognized gravity parameters")]
    return [
        Effect(
            kind="gravity",
            evidence="verified",
            source_type=kind,
            summary=f"Reduces enemies' remaining HP by {params[0]}%",
            details={"percent": params[0]},
        )
    ]


def _delay(kind: int, params: list[int]) -> list[Effect]:
    turns = None
    if len(params) == 1 and _positive(params[0]):
        turns = params[0]
    elif len(params) == 2 and params[0] == params[1] and _positive(params[0]):
        turns = params[0]
    if turns is None:
        return [_unknown_leaf(kind, params, "Unrecognized delay parameters")]
    return [
        Effect(
            kind="delay",
            evidence="verified",
            source_type=kind,
            summary=f"Delays enemies' next move {_for_turns(turns)}",
            details={"turns": turns},
        )
    ]


def _defense_break(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _percent_guard(params[0], params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized defense-break parameters")]
    turns, percent = params
    if percent == 100:
        summary = f"Voids enemies' defense {_for_turns(turns)}"
    else:
        summary = f"Reduces enemies' defense by {percent}% {_for_turns(turns)}"
    return [
        Effect(
            kind="defense_break",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"turns": turns, "percent": percent},
        )
    ]


def _added_combos(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _positive(params[0]) or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized combo-bonus parameters")]
    turns, combos = params
    noun = "combo" if combos == 1 else "combos"
    return [
        Effect(
            kind="added_combos",
            evidence="verified",
            source_type=kind,
            summary=f"Adds {combos} {noun} {_for_turns(turns)}",
            details={"turns": turns, "combos": combos},
        )
    ]


def _timed_move(kind: int, params: list[int]) -> list[Effect]:
    # Two parameters are added seconds in tenths. Three are a multiplier in hundredths.
    if len(params) == 2 and _positive(params[0]) and params[1] != 0:
        tenths = params[1]
        amount = tenths // 10 if tenths % 10 == 0 else round(tenths / 10, 1)
        number = str(amount)
        unit = "second" if abs(tenths) == 10 else "seconds"
        sign = "+" if tenths > 0 else ""
        return [
            Effect(
                kind="move_time_buff",
                evidence="verified",
                source_type=kind,
                summary=f"Orb move time {sign}{number} {unit} {_for_turns(params[0])}",
                details={"turns": params[0], "seconds": amount},
            )
        ]
    if len(params) == 3 and params[1] == 0 and _positive(params[0]) and _positive(params[2]):
        return [
            Effect(
                kind="move_time_buff",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(params[2])}x orb move time {_for_turns(params[0])}",
                details={"turns": params[0], "multiplier_hundredths": params[2]},
            )
        ]
    return [_unknown_leaf(kind, params, "Unrecognized timed move-time parameters")]


def _paired_atk(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 4 or not _positive(params[0]) or not _positive(params[3]):
        return [_unknown_leaf(kind, params, "Unrecognized paired attack parameters")]
    turns, first, second, multiplier = params
    if first not in range(6) or second not in range(6) or (first == 5 and second == 5):
        return [_unknown_leaf(kind, params, "Unrecognized paired attack parameters")]
    effects: list[Effect] = []
    seen: list[str] = []
    for slot in (first, second):
        if slot == 5 or ATTRIBUTES[slot] in seen:
            continue
        attribute = ATTRIBUTES[slot]
        seen.append(attribute)
        effects.append(
            Effect(
                kind="attribute_atk",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(multiplier)}x ATK for {attribute} {_for_turns(turns)}",
                details={
                    "turns": turns,
                    "attribute": ATTRIBUTES[slot],
                    "multiplier_hundredths": multiplier,
                },
            )
        )
    if 5 in {first, second}:
        effects.append(
            Effect(
                kind="rcv_buff",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(multiplier)}x RCV {_for_turns(turns)}",
                details={"turns": turns, "multiplier_hundredths": multiplier},
            )
        )
    return effects


def _orb_change(kind: int, params: list[int]) -> list[Effect]:
    # Three parameters omit the second destination, which is fire on every checked skill.
    if len(params) == 4:
        raw_pairs = [(params[0], params[1]), (params[2], params[3])]
    elif len(params) == 3:
        raw_pairs = [(params[0], params[1]), (params[2], 0)]
    else:
        return [_unknown_leaf(kind, params, "Unrecognized orb change")]
    pairs: list[tuple[str, str]] = []
    for source_code, destination_code in raw_pairs:
        source = CHANGE_ORBS.get(source_code)
        destination = CHANGE_ORBS.get(destination_code)
        if source is None or destination is None:
            return [_unknown_leaf(kind, params, "Unrecognized orb change")]
        pairs.append((source, destination))
    if len({destination for _, destination in pairs}) == 1:
        sources = ", ".join(source for source, _ in pairs)
        summary = f"Changes {sources} to {pairs[0][1]}"
    else:
        changed = " and ".join(f"{source} to {destination}" for source, destination in pairs)
        summary = f"Changes {changed}"
    destinations: list[str] = []
    for _, destination in pairs:
        if destination not in destinations:
            destinations.append(destination)
    return [
        Effect(
            kind="orb_change",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={
                "changes": [{"from": source, "to": destination} for source, destination in pairs],
                "to": destinations,
            },
        )
    ]


def _billion_amount(value: int) -> str:
    if value % 10:
        return f"{value / 10:.1f}"
    return str(value // 10)


def _damage_cap(kind: int, params: list[int]) -> list[Effect]:
    if len(params) == 2:
        turns, amount, target = params[0], params[1], 1
    elif len(params) == 3:
        turns, amount, target = params
    else:
        return [_unknown_leaf(kind, params, "Unrecognized damage-cap parameters")]
    label = CAP_TARGETS.get(target)
    if label is None or not _positive(turns) or not _positive(amount):
        return [_unknown_leaf(kind, params, "Unrecognized damage-cap parameters")]
    return [
        Effect(
            kind="damage_cap",
            evidence="verified",
            source_type=kind,
            summary=(
                f"Sets damage cap to {_billion_amount(amount)} billion for {label} "
                f"{_for_turns(turns)}"
            ),
            details={"turns": turns, "billion_tenths": amount, "target": label},
        )
    ]


def _self_damage_cap(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2:
        return [_unknown_leaf(kind, params, "Unrecognized damage-cap parameters")]
    return _damage_cap(kind, [params[0], params[1], 1])


def _orb_enhance(kind: int, params: list[int]) -> list[Effect]:
    # The second value is 6 on every checked enhance.
    if len(params) != 2 or params[1] != 6:
        return [_unknown_leaf(kind, params, "Unrecognized orb enhance")]
    orbs = _orb_bit_names(params[0])
    if not orbs or params[0] & ~63:
        return [_unknown_leaf(kind, params, "Unrecognized orb enhance")]
    if params[0] == 63:
        summary = "Enhances all orbs"
    elif len(orbs) == 1:
        summary = f"Enhances {orbs[0]} orbs"
    else:
        summary = f"Enhances {', '.join(orbs[:-1])} and {orbs[-1]} orbs"
    return [
        Effect(
            kind="orb_enhance",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"orbs": orbs},
        )
    ]


def _type_atk(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or not _positive(params[0]) or not _positive(params[2]):
        return [_unknown_leaf(kind, params, "Unrecognized type-attack parameters")]
    typing = CARD_TYPES.get(params[1])
    if typing is None:
        return [_unknown_leaf(kind, params, "Unrecognized type-attack parameters")]
    return [
        Effect(
            kind="type_atk",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[2])}x ATK for {typing} {_for_turns(params[0])}",
            details={
                "turns": params[0],
                "card_type": typing,
                "multiplier_hundredths": params[2],
            },
        )
    ]


def _heal_over_time(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or params[1] != 0 or not _percent_guard(params[0], params[2]):
        return [_unknown_leaf(kind, params, "Unrecognized regeneration parameters")]
    turns, percent = params[0], params[2]
    if percent == 100:
        summary = f"Fully recovers HP {_for_turns(turns)}"
    else:
        summary = f"Recovers {percent}% HP {_for_turns(turns)}"
    return [
        Effect(
            kind="heal_over_time",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"turns": turns, "percent": percent},
        )
    ]


def _board_refresh(kind: int, params: list[int]) -> list[Effect]:
    if params:
        return [_unknown_leaf(kind, params, "Unrecognized board refresh")]
    return [
        Effect(
            kind="board_refresh",
            evidence="verified",
            source_type=kind,
            summary="Replaces all orbs",
            details={},
        )
    ]


def _hp_damage(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not 1 <= params[0] <= 100:
        return [_unknown_leaf(kind, params, "Unrecognized HP-damage parameters")]
    return [
        Effect(
            kind="hp_damage",
            evidence="verified",
            source_type=kind,
            summary=f"Inflicts damage equal to {params[0]}% of enemies' HP",
            details={"percent": params[0]},
        )
    ]


_COLUMN_BITS = (
    (1, "the leftmost column"),
    (2, "the 2nd column from the left"),
    (4, "the 3rd column from the left"),
    (8, "the 3rd column from the right"),
    (16, "the 2nd column from the right"),
    (32, "the rightmost column"),
)
_COLUMN_GROUPS = {
    3: "the 2 leftmost columns",
    7: "the 3 leftmost columns",
    12: "the 3rd column from the left and the 3rd from the right",
    18: "the 2nd columns from the left and the right",
    33: "the outermost columns",
    35: "the rightmost and the 2 leftmost columns",
    37: "the outermost columns and the 3rd from the left",
    48: "the 2 rightmost columns",
    49: "the leftmost and the 2 rightmost columns",
    51: "the 2 leftmost and the 2 rightmost columns",
    56: "the 3 rightmost columns",
}
_ROW_BITS = (
    (1, "the top row"),
    (2, "the 2nd row from the top"),
    (4, "the 3rd row from the top"),
    (8, "the 2nd row from the bottom"),
    (16, "the bottom row"),
)
_ROW_GROUPS = {
    3: "the 2 top rows",
    10: "the 2nd rows from the top and bottom",
    17: "the top and bottom rows",
    19: "the 2 top rows and the bottom row",
    24: "the 2 bottom rows",
    27: "the 2 top rows and the 2 bottom rows",
}


def _place_label(
    mask: int, bits: tuple[tuple[int, str], ...], groups: dict[int, str]
) -> str | None:
    allowed = sum(bit for bit, _ in bits)
    if mask <= 0 or mask & ~allowed:
        return None
    grouped = groups.get(mask)
    if grouped:
        return grouped
    names = [label for bit, label in bits if mask & bit]
    if len(names) == 1:
        return names[0]
    return " and ".join(names)


def _orb_destination(mask: int) -> tuple[str, list[str]] | None:
    orbs = _orb_bit_names(mask)
    if not orbs:
        return None
    if len(orbs) == 1:
        return orbs[0], orbs
    listed = ", ".join(orbs[:-1]) + " and " + orbs[-1]
    return f"{listed} at random", orbs


def _line_change(
    kind: int,
    params: list[int],
    bits: tuple[tuple[int, str], ...],
    groups: dict[int, str],
    effect_kind: str,
) -> list[Effect]:
    if len(params) not in {2, 4, 6} or len(params) % 2:
        return [_unknown_leaf(kind, params, "Unrecognized line change")]
    clauses: list[str] = []
    lines: list[dict[str, Any]] = []
    destinations: list[str] = []
    for index in range(0, len(params), 2):
        place = _place_label(params[index], bits, groups)
        destination = _orb_destination(params[index + 1])
        if place is None or destination is None:
            return [_unknown_leaf(kind, params, "Unrecognized line change")]
        phrase, orbs = destination
        clauses.append(f"{place} to {phrase}")
        lines.append({"where": place, "orbs": orbs})
        for orb in orbs:
            if orb not in destinations:
                destinations.append(orb)
    if len(clauses) == 1:
        summary = f"Changes {clauses[0]}"
    else:
        summary = "Changes " + " and ".join(clauses)
    return [
        Effect(
            kind=effect_kind,
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"lines": lines, "to": destinations},
        )
    ]


def _lock_clear(kind: int, params: list[int]) -> list[Effect]:
    # Empty parameters, and a single 1, both say the locks are removed.
    if params not in ([], [1]):
        return [_unknown_leaf(kind, params, "Unrecognized lock-clear parameters")]
    return [
        Effect(
            kind="lock_clear",
            evidence="verified",
            source_type=kind,
            summary="Removes locks",
            details={"full": True},
        )
    ]


def _no_skyfall(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized no-skyfall parameters")]
    return [
        Effect(
            kind="no_skyfall",
            evidence="verified",
            source_type=kind,
            summary=f"No skyfall combos {_for_turns(params[0])}",
            details={"turns": params[0]},
        )
    ]


def _max_hp(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _positive(params[0]) or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized maximum-HP parameters")]
    return [
        Effect(
            kind="max_hp",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[1])}x maximum HP {_for_turns(params[0])}",
            details={"turns": params[0], "multiplier_hundredths": params[1]},
        )
    ]


def _passive_attribute(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or params[0] not in ATTRIBUTES or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized attribute multiplier")]
    attribute = ATTRIBUTES[params[0]]
    return [
        Effect(
            kind="attribute_multiplier",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[1])}x ATK for {attribute}",
            details={"attribute": attribute, "multiplier_hundredths": params[1]},
        )
    ]


def _attribute_guard(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or not _percent_guard(params[0], params[2]) or params[1] not in ATTRIBUTES:
        return [_unknown_leaf(kind, params, "Unrecognized attribute-guard parameters")]
    turns, attribute, percent = params[0], ATTRIBUTES[params[1]], params[2]
    if percent == 100:
        summary = f"Voids {attribute} damage {_for_turns(turns)}"
    else:
        summary = f"Reduces {attribute} damage by {percent}% {_for_turns(turns)}"
    return [
        Effect(
            kind="attribute_guard",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"turns": turns, "attribute": attribute, "percent": percent},
        )
    ]


def _direct_change(kind: int, params: list[int]) -> list[Effect]:
    if len(params) == 1:
        coded = [(params[0], 0)]
    elif len(params) == 2:
        coded = [(params[0], params[1])]
    else:
        return [_unknown_leaf(kind, params, "Unrecognized orb change")]
    pairs: list[tuple[str, str]] = []
    for source_code, destination_code in coded:
        source = CHANGE_ORBS.get(source_code)
        destination = CHANGE_ORBS.get(destination_code)
        if source is None or destination is None:
            return [_unknown_leaf(kind, params, "Unrecognized orb change")]
        pairs.append((source, destination))
    summary = f"Changes {pairs[0][0]} to {pairs[0][1]}"
    return [
        Effect(
            kind="orb_change",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"changes": [{"from": pairs[0][0], "to": pairs[0][1]}], "to": [pairs[0][1]]},
        )
    ]


def _leader_swap(kind: int, params: list[int]) -> list[Effect]:
    if params:
        return [_unknown_leaf(kind, params, "Unrecognized leader-swap parameters")]
    return [
        Effect(
            kind="leader_swap",
            evidence="verified",
            source_type=kind,
            summary="Switches places with the leader. Use again to switch back",
            details={},
        )
    ]


def _shield_break(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not 1 <= params[0] <= 100:
        return [_unknown_leaf(kind, params, "Unrecognized shield parameters")]
    if params[0] == 100:
        summary = "Destroys 1 shield"
        details: dict[str, Any] = {"shields": 1}
    else:
        summary = f"Reduces enemy shield by {params[0]}%"
        details = {"percent": params[0]}
    return [
        Effect(
            kind="shield_break",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details=details,
        )
    ]


_ROULETTE = {
    100: "roulette",
    50: "rapid roulette",
    30: "ultra rapid roulette",
    150: "slow roulette",
}


def _roulette(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 8 or any(params[index] != 0 for index in range(2, 7)):
        return [_unknown_leaf(kind, params, "Unrecognized roulette parameters")]
    turns, speed, count = params[0], params[1], params[7]
    name = _ROULETTE.get(speed)
    if name is None or not _positive(turns) or not _positive(count):
        return [_unknown_leaf(kind, params, "Unrecognized roulette parameters")]
    noun = "orb" if count == 1 else "orbs"
    return [
        Effect(
            kind="roulette",
            evidence="verified",
            source_type=kind,
            summary=f"Creates {count} {name} {noun} {_for_turns(turns)}",
            details={"turns": turns, "count": count, "speed": name},
        )
    ]


def _self_attribute(kind: int, params: list[int]) -> list[Effect]:
    if len(params) == 1 and _positive(params[0]):
        turns, attribute = params[0], "fire"
    elif len(params) == 2 and _positive(params[0]) and params[1] in ATTRIBUTES:
        turns, attribute = params[0], ATTRIBUTES[params[1]]
    else:
        return [_unknown_leaf(kind, params, "Unrecognized attribute-change parameters")]
    return [
        Effect(
            kind="self_attribute",
            evidence="verified",
            source_type=kind,
            summary=f"This ally's attribute changes to {attribute} {_for_turns(turns)}",
            details={"turns": turns, "attribute": attribute},
        )
    ]


def _single_attack(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or params[0] not in ATTRIBUTES or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized single-attack parameters")]
    attribute = ATTRIBUTES[params[0]]
    return [
        Effect(
            kind="single_attack",
            evidence="verified",
            source_type=kind,
            summary=f"Inflicts {_multiplier(params[1])}x {attribute} ATK on 1 enemy",
            details={"attribute": attribute, "multiplier_hundredths": params[1]},
        )
    ]


def _hp_cost(kind: int, params: list[int]) -> list[Effect]:
    if not params:
        return [
            Effect(
                kind="hp_cost",
                evidence="verified",
                source_type=kind,
                summary="Reduces HP to 1",
                details={"to_one": True},
            )
        ]
    if len(params) != 1 or not 1 <= params[0] <= 99:
        return [_unknown_leaf(kind, params, "Unrecognized HP-cost parameters")]
    if params[0] == 50:
        summary = "Halves HP"
    else:
        summary = f"Reduces HP by {100 - params[0]}%"
    return [
        Effect(
            kind="hp_cost",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"remaining_percent": params[0]},
        )
    ]


def _combo_orbs(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2:
        return [_unknown_leaf(kind, params, "Unrecognized combo-orb parameters")]
    if params[0] == 63 and _positive(params[1]):
        noun = "orb" if params[1] == 1 else "orbs"
        return [
            Effect(
                kind="create_combo_orbs",
                evidence="verified",
                source_type=kind,
                summary=f"Creates {params[1]} combo {noun}",
                details={"count": params[1]},
            )
        ]
    attribute = ATTRIBUTE_BITS.get(params[0])
    if attribute and params[1] == 42:
        return [
            Effect(
                kind="combo_convert",
                evidence="verified",
                source_type=kind,
                summary=f"{attribute.capitalize()} orbs become combo orbs",
                details={"from": attribute},
            )
        ]
    return [_unknown_leaf(kind, params, "Unrecognized combo-orb parameters")]


def _turn_span(start: int, end: int) -> str:
    if start == end:
        return _for_turns(start)
    return f"for {start} to {end} turns"


def _skyfall(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 4 or not _positive(params[3]):
        return [_unknown_leaf(kind, params, "Unrecognized skyfall parameters")]
    orbs = _orb_bit_names(params[0])
    start, end, rate = params[1], params[2], params[3]
    if not orbs or not _positive(start) or not _positive(end) or start > end:
        return [_unknown_leaf(kind, params, "Unrecognized skyfall parameters")]
    listed = orbs[0] if len(orbs) == 1 else ", ".join(orbs[:-1]) + " and " + orbs[-1]
    span = _turn_span(start, end)
    if rate == 100:
        summary = f"Only {listed} orbs appear {span}"
        mode = "only"
    else:
        summary = f"{listed.capitalize()} orbs appear more often {span}"
        mode = "increased"
    return [
        Effect(
            kind="skyfall",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={
                "orbs": orbs,
                "to": orbs,
                "start_turn": start,
                "end_turn": end,
                "rate": rate,
                "mode": mode,
            },
        )
    ]


def _column_change(kind: int, params: list[int]) -> list[Effect]:
    return _line_change(kind, params, _COLUMN_BITS, _COLUMN_GROUPS, "column_change")


def _row_change(kind: int, params: list[int]) -> list[Effect]:
    return _line_change(kind, params, _ROW_BITS, _ROW_GROUPS, "row_change")


def _duration(details: dict[str, Any]) -> Duration:
    return Duration(
        turns=details.get("turns"),
        full=bool(details.get("full")),
        start_turn=details.get("start_turn"),
        end_turn=details.get("end_turn"),
    )


def _timed(model: type[EffectParameters], details: dict[str, Any]) -> tuple:
    return model(duration=_duration(details)), ["duration"], []


def _unknown_parameter_names(details: dict[str, Any]) -> list[str]:
    names = [f"[{index}]={value}" for index, value in enumerate(details.get("params") or [])]
    if "component_id" in details:
        names.append(f"component_id={details['component_id']}")
    return names


def _parameters_from(
    kind: str, details: dict[str, Any]
) -> tuple[EffectParameters, list[str], list[str]]:
    """Turn a verified details mapping into the typed parameter model for that effect."""
    if kind == "unsupported":
        raw = [int(value) for value in details.get("params") or []]
        return (
            UnsupportedParameters(raw=raw, component_id=details.get("component_id")),
            [],
            _unknown_parameter_names(details),
        )
    if kind == "create_orbs":
        return (
            CreateOrbsParameters(count=details["count"], colors=list(details["attributes"])),
            ["count", "colors"],
            [],
        )
    if kind == "orb_replace":
        return (
            OrbReplaceParameters(
                count=details["count"],
                excluded_colors=list(details["excluded"]),
                destination=list(details["to"]),
            ),
            ["count", "excluded_colors", "destination"],
            [],
        )
    if kind == "orb_convert":
        return (
            OrbConvertParameters(
                source_colors=list(details["from"]), destination=list(details["to"])
            ),
            ["source_colors", "destination"],
            [],
        )
    if kind == "orb_change":
        changes = [
            OrbSwap(source=item["from"], destination=item["to"]) for item in details["changes"]
        ]
        return (
            OrbChangeParameters(changes=changes, destinations=list(details["to"])),
            ["changes", "destinations"],
            [],
        )
    if kind == "board_change":
        return BoardChangeParameters(orbs=list(details["orbs"])), ["orbs"], []
    if kind == "board_size":
        return (
            BoardSizeParameters(
                duration=_duration(details), columns=details["columns"], rows=details["rows"]
            ),
            ["duration", "columns", "rows"],
            [],
        )
    if kind in {"column_change", "row_change"}:
        lines = [
            LinePlacement(where=item["where"], orbs=list(item["orbs"])) for item in details["lines"]
        ]
        return (
            LineChangeParameters(lines=lines, destinations=list(details["to"])),
            ["lines", "destinations"],
            [],
        )
    if kind == "lock_clear":
        return LockClearParameters(full=bool(details.get("full"))), ["full"], []
    if kind == "skyfall":
        return (
            SkyfallParameters(
                duration=_duration(details),
                orbs=list(details["orbs"]),
                rate=details["rate"],
                mode=details["mode"],
            ),
            ["duration", "orbs", "rate", "mode"],
            [],
        )
    if kind == "roulette":
        return (
            RouletteParameters(
                duration=_duration(details), count=details["count"], speed=details["speed"]
            ),
            ["duration", "count", "speed"],
            [],
        )
    if kind == "damage_shield":
        return (
            DamageShieldParameters(duration=_duration(details), percent=details["percent"]),
            ["duration", "percent"],
            [],
        )
    if kind == "attribute_guard":
        return (
            AttributeGuardParameters(
                duration=_duration(details),
                attribute=details["attribute"],
                percent=details["percent"],
            ),
            ["duration", "attribute", "percent"],
            [],
        )
    if kind in {"attribute_atk", "rcv_buff", "team_atk", "all_allies_atk", "type_atk", "max_hp"}:
        return (
            StatModifierParameters(
                duration=_duration(details) if "turns" in details else None,
                multiplier_hundredths=details.get("multiplier_hundredths"),
                attribute=details.get("attribute"),
                card_type=details.get("card_type"),
                target=details.get("target"),
            ),
            [
                name
                for name, present in (
                    ("duration", "turns" in details),
                    ("multiplier_hundredths", "multiplier_hundredths" in details),
                    ("attribute", "attribute" in details),
                    ("card_type", "card_type" in details),
                    ("target", "target" in details),
                )
                if present
            ],
            [],
        )
    if kind == "hp_cost":
        return (
            HpCostParameters(
                to_one=bool(details.get("to_one")),
                remaining_percent=details.get("remaining_percent"),
            ),
            [name for name in ("to_one", "remaining_percent") if details.get(name) is not None],
            [],
        )
    if kind in {"heal_rcv", "heal_flat", "heal_percent", "heal_over_time"}:
        return (
            HealParameters(
                duration=_duration(details) if "turns" in details else None,
                multiplier_hundredths=details.get("multiplier_hundredths"),
                hp=details.get("hp"),
                percent=details.get("percent"),
            ),
            [
                name
                for name, present in (
                    ("duration", "turns" in details),
                    ("multiplier_hundredths", "multiplier_hundredths" in details),
                    ("hp", "hp" in details),
                    ("percent", "percent" in details),
                )
                if present
            ],
            [],
        )
    if kind == "absorb_void":
        return (
            AbsorbVoidParameters(duration=_duration(details), kinds=list(details["kinds"])),
            ["duration", "kinds"],
            [],
        )
    if kind == "move_time":
        return MoveTimeParameters(seconds=details["seconds"]), ["seconds"], []
    if kind == "void_pierce":
        return _timed(VoidPierceParameters, details)
    if kind == "charge_other_skills":
        return _timed(HasteParameters, details)
    if kind == "delay":
        return _timed(DelayParameters, details)
    if kind == "no_skyfall":
        return _timed(NoSkyfallParameters, details)
    if kind == "transform":
        return TransformParameters(monster_id=details["monster_id"]), ["monster_id"], []
    if kind == "damage_cap":
        return (
            DamageCapParameters(
                duration=_duration(details),
                billion_tenths=details["billion_tenths"],
                target=details["target"],
            ),
            ["duration", "billion_tenths", "target"],
            [],
        )
    if kind == "self_attribute":
        return (
            AttributeChangeParameters(
                duration=_duration(details), attribute=details["attribute"]
            ),
            ["duration", "attribute"],
            [],
        )
    if kind in {"move_time_bonus", "move_time_buff"}:
        supported = []
        if "seconds" in details:
            supported.append("seconds")
        if "multiplier_hundredths" in details:
            supported.append("multiplier_hundredths")
        if "turns" in details:
            supported.append("duration")
        return (
            MoveTimeParameters(
                seconds=details.get("seconds"),
                multiplier_hundredths=details.get("multiplier_hundredths"),
                duration=_duration(details) if "turns" in details else None,
            ),
            supported,
            [],
        )
    if kind == "linked_orb_leader":
        return (
            LinkedOrbParameters(
                attributes=list(details["attributes"]),
                minimum_linked=details["minimum_linked"],
                atk_multiplier_hundredths=details["atk_multiplier_hundredths"],
                shield_percent=details["shield_percent"],
            ),
            [
                "attributes",
                "minimum_linked",
                "atk_multiplier_hundredths",
                "shield_percent",
            ],
            [],
        )
    if kind == "awakening_trigger":
        return (
            AwakeningTriggerParameters(
                trigger=details["trigger"],
                atk_multiplier_hundredths=details["atk_multiplier_hundredths"],
                rcv_multiplier_hundredths=details["rcv_multiplier_hundredths"],
                shield_percent=details["shield_percent"],
                added_combos=details["added_combos"],
                fixed_damage=details["fixed_damage"],
            ),
            [
                "trigger",
                "atk_multiplier_hundredths",
                "rcv_multiplier_hundredths",
                "shield_percent",
                "added_combos",
                "fixed_damage",
            ],
            [],
        )
    if kind == "orb_enhance":
        return OrbEnhanceParameters(orbs=list(details["orbs"])), ["orbs"], []
    if kind == "added_combos":
        return (
            AddedCombosParameters(duration=_duration(details), combos=details["combos"]),
            ["duration", "combos"],
            [],
        )
    if kind == "defense_break":
        return (
            DefenseBreakParameters(duration=_duration(details), percent=details["percent"]),
            ["duration", "percent"],
            [],
        )
    if kind in {"gravity", "hp_damage"}:
        return PercentParameters(percent=details["percent"]), ["percent"], []
    if kind == "shield_break":
        supported = [name for name in ("shields", "percent") if name in details]
        return (
            ShieldBreakParameters(shields=details.get("shields"), percent=details.get("percent")),
            supported,
            [],
        )
    if kind == "create_combo_orbs":
        return ComboOrbParameters(count=details["count"]), ["count"], []
    if kind == "combo_convert":
        return ComboOrbParameters(source_color=details["from"]), ["source_color"], []
    if kind == "rank_exp":
        return (
            RankExpParameters(multiplier_hundredths=details["multiplier_hundredths"]),
            ["multiplier_hundredths"],
            [],
        )
    if kind == "mass_attack":
        return (
            MassAttackParameters(
                attribute=details["attribute"],
                multiplier_hundredths=details["multiplier_hundredths"],
            ),
            ["attribute", "multiplier_hundredths"],
            [],
        )
    if kind == "single_attack":
        return (
            SingleAttackParameters(
                attribute=details["attribute"],
                multiplier_hundredths=details["multiplier_hundredths"],
            ),
            ["attribute", "multiplier_hundredths"],
            [],
        )
    if kind == "attribute_multiplier":
        return (
            StatModifierParameters(
                attribute=details["attribute"],
                multiplier_hundredths=details["multiplier_hundredths"],
            ),
            ["attribute", "multiplier_hundredths"],
            [],
        )
    if kind in {"board_refresh", "leader_swap"}:
        return EmptyParameters(), [], []
    if kind == "bind_clear":
        return _timed(BindClearParameters, details)
    if kind == "awoken_bind_clear":
        return _timed(AwokenBindClearParameters, details)
    if kind == "unmatchable_clear":
        return _timed(UnmatchableClearParameters, details)
    return RecordedParameters(values=dict(details)), list(details), []


_LEAVES = {
    0: _mass_attack,
    3: _damage_shield,
    6: _gravity,
    9: _direct_change,
    10: _board_refresh,
    11: _passive_attribute,
    15: _move_time_bonus,
    18: _delay,
    19: _defense_break,
    20: _orb_change,
    21: _attribute_guard,
    37: _single_attack,
    50: _attribute_atk,
    71: _board_change,
    88: _type_atk,
    90: _paired_atk,
    93: _leader_swap,
    126: _skyfall,
    127: _column_change,
    128: _row_change,
    132: _timed_move,
    140: _orb_enhance,
    142: _self_attribute,
    141: _create_orbs,
    154: _orb_convert,
    117: _recovery,
    146: _haste,
    148: _rank_exp,
    160: _added_combos,
    172: _lock_clear,
    161: _hp_damage,
    173: _absorb,
    178: _move_time,
    179: _heal_over_time,
    184: _no_skyfall,
    182: _linked_shield,
    190: _combo_orbs,
    191: _void_pierce,
    195: _hp_cost,
    196: _unmatchable,
    202: _transform,
    207: _roulette,
    230: _allies_atk,
    237: _max_hp,
    241: _self_damage_cap,
    244: _board,
    258: _damage_cap,
    259: _shield_break,
    271: _trigger_bonus,
}
