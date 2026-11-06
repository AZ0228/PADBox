"""Typed battle state for one supplied team.

The state records the turn, the form each monster is in, skill counters,
assist overcharge, effect durations, the board and its locks, HP, RCV,
shields, and awakenings. It does not choose a different team.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from padbox.mechanics.clauses import ClauseVerdict, LeaderClause
from padbox.mechanics.skill_state import ActiveSlot

BOARD_COLUMNS = 6
BOARD_ROWS = 5

ASSUMPTIONS: tuple[str, ...] = (
    "Cooldowns are max-skill minimums.",
    "Skill boost is applied once before turn 1 from normal Skill Boost awakenings "
    "on the entry forms and their assists.",
    "Super awakenings, latents, and the team badge are not selected.",
    "An assist replaces the host active. Charge fills the host cooldown before the assist counter.",
    "Each turn, including turn 1, ticks one charge before actives are used.",
    "Haste charges the other members after the caster's new cooldown is set.",
    "HP and RCV start as the sum of max-level card stats. A transform moves current HP "
    "by that member's change in max HP.",
    "The team shield is the highest active damage shield. RCV multipliers multiply.",
    "The board is 6×5. Any other size is unsupported.",
    "Skill board changes use that skill's quantity and source colors. Locked orbs stay put, "
    "except a full board pattern or a board refresh.",
    "Matching follows the greedy policy. A result is a policy approximation.",
    "A plus of five is a cross when a cross clause is unmet, "
    "and those orbs are not a linked match.",
    "A line of five is a linked match.",
    "Heart orbs stay unless recovery is needed or they are the chosen cross or linked group.",
    "Skyfall fills empty unlocked cells from the battle seed and is not matched on that turn.",
    "Actives follow the fixed skill-use policy.",
)

CastReason = Literal["ready", "not_ready", "unknown", "evolution", "bound", "already_used"]
DelayReason = Literal["applied", "unknown_resistance", "unknown_cooldown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrbCell(_Strict):
    """One board cell. Empty cells are filled by skyfall. Locked cells stay in place."""

    color: str | None = None
    locked: bool = False


class BoardState(_Strict):
    """A rectangular board. Only 6×5 is a supported battle board."""

    columns: int
    rows: int
    cells: list[OrbCell]
    unmatchable_turns: int = 0

    @model_validator(mode="after")
    def _cells_match(self) -> "BoardState":
        if len(self.cells) != self.columns * self.rows:
            raise ValueError("board cells do not match the declared size")
        return self

    def is_standard(self) -> bool:
        return self.columns == BOARD_COLUMNS and self.rows == BOARD_ROWS


class BoardChange(_Strict):
    """A skill's board edit, stored with the decoded quantity and colors."""

    turn: int
    member_index: int
    kind: str
    summary: str
    count: int | None = None
    colors: list[str] = Field(default_factory=list)
    source_colors: list[str] = Field(default_factory=list)


class TimedEffect(_Strict):
    """One effect that is still in force.

    `expires_on` is the first turn on which the effect is already gone.
    `unknown_duration` means the decoder did not give a single turn count,
    so the effect is listed and is not treated as active.
    """

    effect_id: int
    kind: str
    summary: str
    source_index: int
    source_skill_id: int | None
    started_on: int
    expires_on: int | None = None
    full: bool = False
    unknown_duration: bool = False
    percent: int | None = None
    multiplier_hundredths: int | None = None
    attribute: str | None = None
    absorb_kinds: list[str] = Field(default_factory=list)
    columns: int | None = None
    rows: int | None = None
    orbs: list[str] = Field(default_factory=list)
    rate: int | None = None
    mode: str | None = None


class SkillDelayEvent(_Strict):
    """One enemy skill delay applied to a member's counter."""

    turn: int
    member_index: int
    turns: int
    applied: bool
    reason: DelayReason


class CastRecord(_Strict):
    """One attempt to use the member's current active."""

    turn: int
    member_index: int
    monster_id: int
    state_id: str
    skill_id: int | None
    state_kind: str
    fired: bool
    reason: CastReason
    remaining_before: int | None
    overcharge_before: int | None
    used_before: bool
    evolution_met: bool
    effect_kinds: list[str]
    transform_to: int | None = None


class MemberState(_Strict):
    """One monster on the team.

    `monster_id` is the form in play. `entered_monster_id` is the form the
    battle started on. `active` is the only skill the monster can cast.
    `overcharge_remaining` is the host cooldown an assist still absorbs
    before its own counter moves. Zero means the member has no assist gate.
    """

    index: int
    role: Literal["leader", "sub", "helper"]
    monster_id: int
    entered_monster_id: int
    name: str
    hp_max: int | None
    atk: int | None
    rcv: int | None
    attributes: list[str]
    card_types: list[str]
    awakenings: list[int]
    assist_monster_id: int | None = None
    assist_awakenings: list[int] = Field(default_factory=list)
    active: ActiveSlot
    overcharge_remaining: int | None = 0
    evolution_met: bool = True
    bound_turns: int = 0
    awoken_bound_turns: int = 0
    cooldown_exact: bool = True

    def charges_remaining(self) -> int | None:
        """Charges until the current active can be used, before the next tick."""
        if self.overcharge_remaining is None or self.active.remaining is None:
            return None
        return self.overcharge_remaining + max(self.active.remaining, 0)

    def stage_index(self) -> int | None:
        """Evolving stage of the current active, when it has one."""
        return self.active.state().stage_index


class MatchedGroup(_Strict):
    """One group the greedy policy matched. `cells` are row-major indexes."""

    color: str
    size: int
    shape: Literal["cross", "l_shape", "row", "group"]
    cells: list[int]


class ClauseTag(_Strict):
    """Whether one clause counts as offense, defense, or neither."""

    clause_id: str
    offensive: bool
    defensive: bool


class TurnReport(_Strict):
    """The match and clause verdicts for one turn.

    `basis` is `policy_approximation` on every report. `leader_verdicts` follows
    the leader clauses in order. A board that was not matched leaves the board
    clauses unknown. Readiness is the counter after the turn's tick and before
    actives. `applied_kinds` are the verified effects that fired this turn.
    """

    turn: int
    basis: Literal["policy_approximation"]
    matches: list[MatchedGroup]
    heal_orbs_matched: int
    heal_orbs_kept: int
    skyfall_count: int
    board_observed: bool
    leader_verdicts: list[ClauseVerdict]
    helper_verdicts: list[ClauseVerdict]
    leader_tags: list[ClauseTag] = Field(default_factory=list)
    helper_tags: list[ClauseTag] = Field(default_factory=list)
    ready_members: list[int] = Field(default_factory=list)
    unknown_cooldown_members: list[int] = Field(default_factory=list)
    bound_members: list[int] = Field(default_factory=list)
    applied_kinds: list[str] = Field(default_factory=list)
    active_effect_kinds: list[str] = Field(default_factory=list)


class GameState(_Strict):
    """One battle at one moment.

    `turn` is 0 after skill boosts and before the first tick. Leader and
    helper clauses are the activation conditions. `leader_verdicts` stays
    null until the first turn report. `turn_reports` keeps every turn.
    """

    turn: int = 0
    seed: int = 0
    members: list[MemberState]
    board: BoardState
    effects: list[TimedEffect] = Field(default_factory=list)
    board_changes: list[BoardChange] = Field(default_factory=list)
    casts: list[CastRecord] = Field(default_factory=list)
    skill_delays: list[SkillDelayEvent] = Field(default_factory=list)
    leader_clauses: list[LeaderClause] = Field(default_factory=list)
    helper_clauses: list[LeaderClause] = Field(default_factory=list)
    leader_clauses_known: bool = True
    helper_clauses_known: bool = True
    leader_verdicts: list[ClauseVerdict] | None = None
    helper_verdicts: list[ClauseVerdict] | None = None
    turn_reports: list[TurnReport] = Field(default_factory=list)
    hp: int | None = None
    enemy_delayed_through: int = 0
    unknowns: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    opening_skill_boost: int | None = None
    skill_boost_known: bool = True
    team_complete: bool = False
    sub_count: int = 0
    helper_count: int = 0
    next_effect_id: int = 1

    _cards: dict[int, dict] = PrivateAttr(default_factory=dict)
    _skills: dict[int, dict] = PrivateAttr(default_factory=dict)
    _rng: Any = PrivateAttr(default=None)
    _applied: list[str] = PrivateAttr(default_factory=list)


def empty_board(columns: int = BOARD_COLUMNS, rows: int = BOARD_ROWS) -> BoardState:
    """An unlocked board of the given size. Cells start empty."""
    if columns < 1 or rows < 1:
        raise ValueError("board dimensions must be positive")
    return BoardState(
        columns=columns,
        rows=rows,
        cells=[OrbCell() for _ in range(columns * rows)],
    )


def team_hp_max(state: GameState) -> int | None:
    """Sum of member max HP. Missing member HP makes the team total unknown."""
    values = [member.hp_max for member in state.members]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def shield_percent(state: GameState) -> int | None:
    """Highest known damage shield. No shield is 0. An unknown shield is unknown."""
    found: list[int] = []
    for effect in state.effects:
        if effect.kind != "damage_shield" or effect.unknown_duration:
            if effect.kind == "damage_shield" and effect.unknown_duration:
                return None
            continue
        if effect.percent is None:
            return None
        found.append(effect.percent)
    return max(found, default=0)


def effective_rcv(state: GameState) -> int | None:
    """Team RCV after known multipliers, multiplied in application order."""
    values = [member.rcv for member in state.members]
    if any(value is None for value in values):
        return None
    base = sum(value for value in values if value is not None)
    factor = 100
    for effect in state.effects:
        if effect.kind != "rcv_buff":
            continue
        if effect.unknown_duration or effect.multiplier_hundredths is None:
            return None
        factor = factor * effect.multiplier_hundredths // 100
    return base * factor // 100


def board_is_supported(state: GameState) -> bool:
    """True when the board is 6×5 and no active effect asks for another size."""
    if not state.board.is_standard():
        return False
    for effect in state.effects:
        if effect.kind != "board_size":
            continue
        if (
            effect.unknown_duration
            or effect.columns != BOARD_COLUMNS
            or effect.rows != BOARD_ROWS
        ):
            return False
    return True


def enemy_is_delayed(state: GameState) -> bool:
    """True on a turn the recorded enemy-move delay still covers."""
    return state.turn > 0 and state.enemy_delayed_through >= state.turn
