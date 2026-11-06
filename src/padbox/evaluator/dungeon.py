"""Versioned dungeon profiles.

Schema version 1. A profile names a dungeon and lists its floors. Each floor
has ``enemy_hp``, ``preemptive_damage``, and ``mechanics``. A mechanic is one
of these names:

- ``damage_absorb``: damage absorption
- ``attribute_absorb``: attribute absorption
- ``damage_void``: damage void
- ``awoken_bind``: awoken bind
- ``unmatchable``: unmatchable
- ``skill_delay``: skill delay
- ``resolve``: resolve
- ``enemy_shield``: enemy shields
- ``hp_threshold``: HP thresholds

Any other name is rejected. This version records that a mechanic is present.
It does not store a threshold, an attribute, a duration, or a shield amount.
A missing quantity is absent, not zero. Loading a profile does not say that a
team handles a floor. ``judge_dungeon`` does that from response timing: a floor
is handled only when every listed mechanic is modeled and met. Anything
unmodeled stays an uncertainty, and the verdict does not say the floor is cleared.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from padbox.mechanics.result import CheckResult

DUNGEON_PROFILE_VERSION = 1

ENCOUNTER_MECHANICS = (
    "damage_absorb",
    "attribute_absorb",
    "damage_void",
    "awoken_bind",
    "unmatchable",
    "skill_delay",
    "resolve",
    "enemy_shield",
    "hp_threshold",
)
EncounterMechanic = Literal[
    "damage_absorb",
    "attribute_absorb",
    "damage_void",
    "awoken_bind",
    "unmatchable",
    "skill_delay",
    "resolve",
    "enemy_shield",
    "hp_threshold",
]
_KNOWN_MECHANICS = frozenset(ENCOUNTER_MECHANICS)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DungeonFloor(_Strict):
    """One floor. ``mechanics`` uses only ``ENCOUNTER_MECHANICS``."""

    enemy_hp: int = Field(gt=0)
    preemptive_damage: int = Field(ge=0)
    mechanics: list[EncounterMechanic]

    @field_validator("mechanics", mode="before")
    @classmethod
    def _reject_unknown_mechanics(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        unknown = [
            name for name in value if isinstance(name, str) and name not in _KNOWN_MECHANICS
        ]
        if unknown:
            raise ValueError("Unknown mechanic names: " + ", ".join(unknown))
        return value


class DungeonProfile(_Strict):
    """A versioned dungeon. Omitted ``schema_version`` means version 1.

    The brief's example has no version field. Loading it still records version 1.
    """

    schema_version: Literal[1] = DUNGEON_PROFILE_VERSION
    name: str = Field(min_length=1)
    floors: list[DungeonFloor] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _supported_version(cls, value: object) -> object:
        supported = (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value == DUNGEON_PROFILE_VERSION
        )
        if not supported:
            raise ValueError(
                f"Unsupported dungeon profile version {value!r}. "
                f"This build reads version {DUNGEON_PROFILE_VERSION}."
            )
        return value


def load_dungeon_profile(source: Path | str) -> DungeonProfile:
    """Read one JSON profile. Unknown mechanic names and versions do not load."""
    path = Path(source)
    return DungeonProfile.model_validate_json(path.read_text(encoding="utf-8"))


class _Modeled(NamedTuple):
    mechanic: str
    response: str
    answer: str
    label: str


# Simulator keys for responses whose timing the battle records.
# ``answer`` is the candidate role. ``label`` is the score-model name.
_MODELED = (
    _Modeled("damage_absorb", "absorb_void:damage", "damage_absorb", "damage absorb void"),
    _Modeled(
        "attribute_absorb", "absorb_void:attribute", "attribute_absorb", "attribute absorb void"
    ),
    _Modeled("damage_void", "void_pierce", "void_pierce", "damage void pierce"),
    _Modeled("awoken_bind", "awoken_bind_clear", "awoken_bind_clear", "awoken bind clear"),
    _Modeled("unmatchable", "unmatchable_clear", "unmatchable_clear", "unmatchable clear"),
)
_BY_MECHANIC = {row.mechanic: row for row in _MODELED}
_UNMODELED = {
    "skill_delay": (
        "Skill delay is not applied from a dungeon profile. "
        "The profile has no delay length, so this requirement is unmodeled."
    ),
    "resolve": "Resolve is not a modeled encounter, so this requirement is unmodeled.",
    "enemy_shield": (
        "Enemy shields are not modeled. A team shield is not an enemy shield, "
        "so this requirement is unmodeled."
    ),
    "hp_threshold": "Enemy HP thresholds are not modeled, so this requirement is unmodeled.",
}
_SCHEDULE_NOTE = (
    "The profile does not say which turn a mechanic occurs. "
    "Timing is the team's response during the simulated battle, not an enemy schedule. "
    "This is not a clear."
)


class FloorRequirement(_Strict):
    """One listed mechanic on one floor. ``modeled`` is false when no response is timed."""

    floor: int
    mechanic: str
    modeled: bool
    result: CheckResult
    detail: str


class FloorVerdict(_Strict):
    """Whether the team handles one floor. ``handled`` is not a clear."""

    floor: int
    enemy_hp: int
    preemptive_damage: int
    handled: bool
    requirements: list[FloorRequirement]
    uncertainties: list[str]


class DungeonVerdict(_Strict):
    """Floor verdicts for one profile. Unmodeled mechanics stay in ``uncertainties``."""

    name: str
    schema_version: int
    floors: list[FloorVerdict]
    uncertainties: list[str]
    note: str


def response_keys(profile: DungeonProfile | None) -> list[str]:
    """Simulator response keys for the profile's modeled mechanics."""
    return _collect(profile, lambda row: row.response)


def answer_keys(profile: DungeonProfile | None) -> list[str]:
    """Candidate answers that correspond to modeled dungeon mechanics."""
    return _collect(profile, lambda row: row.answer)


def score_labels(profile: DungeonProfile | None) -> list[str]:
    """Score-model names for modeled dungeon mechanics."""
    return _collect(profile, lambda row: row.label)


def unmodeled_mechanics(profile: DungeonProfile | None) -> list[str]:
    """Listed mechanics the battle cannot time, in floor order."""
    if profile is None:
        return []
    found: list[str] = []
    for floor in profile.floors:
        for name in floor.mechanics:
            if name in _UNMODELED and name not in found:
                found.append(name)
    return found


def judge_dungeon(
    profile: DungeonProfile,
    timing: Mapping[str, tuple[int, int, int]] | None,
) -> DungeonVerdict:
    """Judge each listed mechanic from response timing.

    ``timing`` maps a response key to ``(simulations, simulations_met,
    simulations_unknown)``. ``None`` means the battle was not simulated.
    A floor is handled only when every listed mechanic is modeled and met.
    """
    floors: list[FloorVerdict] = []
    collected: list[str] = [_SCHEDULE_NOTE]
    for index, floor in enumerate(profile.floors, start=1):
        requirements = [
            _requirement(index, name, timing) for name in floor.mechanics
        ]
        uncertainties = _floor_uncertainties(index, floor, requirements)
        handled = bool(requirements) and all(
            item.modeled and item.result is CheckResult.PASS for item in requirements
        )
        floors.append(
            FloorVerdict(
                floor=index,
                enemy_hp=floor.enemy_hp,
                preemptive_damage=floor.preemptive_damage,
                handled=handled,
                requirements=requirements,
                uncertainties=uncertainties,
            )
        )
        for line in uncertainties:
            if line not in collected:
                collected.append(line)
    return DungeonVerdict(
        name=profile.name,
        schema_version=profile.schema_version,
        floors=floors,
        uncertainties=collected,
        note=(
            "A floor is handled only when every listed mechanic is modeled and met. "
            "Unmodeled mechanics stay uncertainties. This verdict does not say a floor is cleared."
        ),
    )


def _collect(profile: DungeonProfile | None, pick) -> list[str]:
    if profile is None:
        return []
    found: list[str] = []
    for floor in profile.floors:
        for name in floor.mechanics:
            row = _BY_MECHANIC.get(name)
            if row is None:
                continue
            value = pick(row)
            if value not in found:
                found.append(value)
    return found


def _requirement(
    floor: int,
    mechanic: str,
    timing: Mapping[str, tuple[int, int, int]] | None,
) -> FloorRequirement:
    modeled = _BY_MECHANIC.get(mechanic)
    if modeled is None:
        text = _UNMODELED.get(mechanic, "No response model is recorded for this mechanic.")
        detail = f"Floor {floor} {mechanic} is unmodeled. {text}"
        return FloorRequirement(
            floor=floor,
            mechanic=mechanic,
            modeled=False,
            result=CheckResult.UNKNOWN,
            detail=detail,
        )
    result, detail = _timed(floor, mechanic, modeled.label, modeled.response, timing)
    return FloorRequirement(
        floor=floor,
        mechanic=mechanic,
        modeled=True,
        result=result,
        detail=detail,
    )


def _timed(
    floor: int,
    mechanic: str,
    label: str,
    response: str,
    timing: Mapping[str, tuple[int, int, int]] | None,
) -> tuple[CheckResult, str]:
    if timing is None:
        return (
            CheckResult.UNKNOWN,
            (
                f"Floor {floor} {mechanic} is modeled as {label}, and the battle was not "
                "simulated, so its timing is unresolved."
            ),
        )
    row = timing.get(response)
    if row is None:
        return (
            CheckResult.UNKNOWN,
            f"Floor {floor} {mechanic} is modeled as {label}, and its timing was not recorded.",
        )
    simulations, met, unknown = row
    if simulations < 1:
        return (
            CheckResult.UNKNOWN,
            f"Floor {floor} {mechanic} is modeled as {label}, and no simulation recorded it.",
        )
    if met == simulations:
        return (
            CheckResult.PASS,
            (
                f"Floor {floor} {mechanic} is met. {label} was active with known timing "
                f"in {met} of {simulations} simulations."
            ),
        )
    if unknown:
        return (
            CheckResult.UNKNOWN,
            (
                f"Floor {floor} {mechanic} is modeled as {label}, and its timing is unknown "
                f"in {unknown} of {simulations} simulations."
            ),
        )
    return (
        CheckResult.FAIL,
        (
            f"Floor {floor} {mechanic} is not met. {label} was active with known timing "
            f"in {met} of {simulations} simulations."
        ),
    )


def _floor_uncertainties(
    floor: int,
    encounter: DungeonFloor,
    requirements: list[FloorRequirement],
) -> list[str]:
    lines = [item.detail for item in requirements if item.result is CheckResult.UNKNOWN]
    lines.append(
        f"Floor {floor} enemy HP is {encounter.enemy_hp}. "
        "Enemy HP is not a damage target, so this floor is not cleared."
    )
    if encounter.preemptive_damage > 0:
        lines.append(
            f"Floor {floor} preemptive damage is {encounter.preemptive_damage}. "
            "It is not applied, so this floor is not cleared."
        )
    if not requirements:
        lines.append(
            f"Floor {floor} lists no mechanic. "
            "Enemy HP alone does not show that the team handles this floor."
        )
    return lines
