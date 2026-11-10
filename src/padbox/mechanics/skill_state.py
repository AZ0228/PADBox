"""Active-skill state machines.

A skill is a set of states. The monster's slot has one current state, and
that state is the only active it can use on a turn. Base, transformed,
evolving stages, and an inherited assist active are alternatives.

Cooldown numbers come from the skill record. The counter starts at that
record's minimum under the max-skill assumption used by the rest of the
mechanics layer. Entering a state sets the counter to the new state's
minimum (`entry_charge` is `state_minimum`). The snapshot has no separate
post-transform cooldown, so that entry charge is a model rule.

An evolving sequence loops, ends, or stays unknown. The choice comes from
the decoded `loops` flag or from an explicit `sequence` argument. Description
text is not read.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from padbox.mechanics.decode import Effect, EvolvingParameters, TransformParameters, decode_skill

EVOLVING_SKILL = 232

StateKind = Literal["base", "transformed", "evolving", "inherited"]
SequenceKind = Literal["loops", "ends", "unknown"]
Continuation = Literal["not_ready", "already_used", "cycle", "transition", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CooldownSpec(_Strict):
    """Cooldown copied from one skill record.

    `base` is `initialCooldown` when that value is a positive integer.
    `minimum` is `base - (maxLevel - 1)`, the max-skill assumption. `known`
    is false when the skill record itself is missing.
    """

    skill_id: int | None
    base: int | None
    minimum: int | None
    known: bool = True


class ActiveState(_Strict):
    state_id: str
    kind: StateKind
    monster_id: int
    skill_id: int | None
    name: str
    cooldown: CooldownSpec
    effects: list[Effect]
    stage_index: int | None = None


class Transition(_Strict):
    source: str
    target: str | None
    trigger: Literal["use"]
    known: bool


class SkillMachine(_Strict):
    """States and transitions for one monster's active slot.

    `sequence` is set for an evolving skill. It is null for a base active
    and for a transform. `order` lists the skill's own states. An inherited
    active is added to `states` without being inserted into `order`.
    """

    monster_id: int
    skill_id: int
    initial: str
    order: list[str]
    states: dict[str, ActiveState]
    transitions: list[Transition]
    sequence: SequenceKind | None = None


class Cast(_Strict):
    """One attempt to use the current active.

    `effects` are the current state's effects. `state_id` is the state that
    was cast. The slot may already be on the next state.
    """

    used: bool
    continuation: Continuation
    state_id: str
    effects: list[Effect]


class ActiveSlot(_Strict):
    """The monster's current active and its cooldown counter.

    `assumption` records that minimums are max-skill values. `entry_charge`
    records that a transition loads the destination state's minimum.
    """

    machine: SkillMachine
    state_id: str
    remaining: int | None
    used_this_turn: bool = False
    assumption: Literal["max_skill"] = "max_skill"
    entry_charge: Literal["state_minimum"] = "state_minimum"

    @classmethod
    def start(cls, machine: SkillMachine) -> "ActiveSlot":
        state = machine.states[machine.initial]
        return cls(machine=machine, state_id=machine.initial, remaining=_counter(state))

    def state(self) -> ActiveState:
        return self.machine.states[self.state_id]

    def status(self) -> Literal["ready", "charging", "unknown"]:
        if self.remaining is None:
            return "unknown"
        if self.remaining <= 0:
            return "ready"
        return "charging"

    def usable(self) -> ActiveState | None:
        """The one active this monster can use on this turn, if any."""
        if self.used_this_turn or self.status() != "ready":
            return None
        return self.state()

    def tick(self) -> None:
        """One start-of-turn cooldown step. This does not apply haste."""
        self.used_this_turn = False
        if self.remaining is not None and self.remaining > 0:
            self.remaining -= 1

    def use(self) -> Cast:
        """Cast the current active, or refuse while it is not ready.

        A known transition moves the slot to the next state and loads that
        state's minimum. A cycle reloads the same state's minimum. An unknown
        continuation casts this state's effects once and leaves the counter
        unknown, so the slot does not invent a loop or a final cooldown.
        """
        current_id = self.state_id
        if self.used_this_turn:
            return Cast(
                used=False, continuation="already_used", state_id=current_id, effects=[]
            )
        if self.status() != "ready":
            continuation: Continuation = "unknown" if self.status() == "unknown" else "not_ready"
            return Cast(used=False, continuation=continuation, state_id=current_id, effects=[])
        current = self.state()
        effects = list(current.effects)
        self.used_this_turn = True
        transition = _outgoing(self.machine, current_id)
        if transition is None:
            self.remaining = _counter(current)
            return Cast(used=True, continuation="cycle", state_id=current_id, effects=effects)
        if not transition.known or transition.target is None:
            self.remaining = None
            return Cast(used=True, continuation="unknown", state_id=current_id, effects=effects)
        self.state_id = transition.target
        self.remaining = _counter(self.state())
        return Cast(used=True, continuation="transition", state_id=current_id, effects=effects)


def cooldown_spec(skill: dict[str, Any]) -> CooldownSpec:
    """Build a cooldown from a raw skill record."""
    initial = skill.get("initialCooldown")
    levels = int(skill.get("maxLevel") or 0)
    base = initial if isinstance(initial, int) and initial > 0 else None
    minimum = None if base is None else max(0, base - max(0, levels - 1))
    return CooldownSpec(skill_id=int(skill.get("id") or 0) or None, base=base, minimum=minimum)


def machine_for(
    card: dict[str, Any],
    skills: dict[int, dict[str, Any]],
    cards: dict[int, dict[str, Any]] | None = None,
    *,
    sequence: SequenceKind | None = None,
) -> SkillMachine:
    """Build the active machine for one card.

    An evolving active (type 232) becomes one state per stage id in its
    parameter list. Any other active is a base state, plus a transformed
    state for each transform effect in that chain. `sequence` overrides the
    decoded loop flag and is only valid for an evolving skill. Pass it when
    the loop behavior is known from outside this snapshot. Description text
    is not an input.
    """
    monster_id = int(card["id"])
    active_id = int(card["activeSkillId"])
    active = _skill(skills, active_id, f"monster {monster_id} active {active_id}")
    effects = decode_skill(active, skills)
    if int(active.get("type") or -1) == EVOLVING_SKILL:
        evolving = next((effect for effect in effects if effect.kind == "evolving_skill"), None)
        if evolving is None:
            raise ValueError(f"monster {monster_id} active {active_id} did not decode as evolving")
        return _evolving_machine(monster_id, active, evolving, skills, sequence)
    if sequence is not None:
        raise ValueError("sequence applies only to an evolving skill")
    return _form_machine(monster_id, active, effects, skills, cards or {})


def with_inherited(
    slot: ActiveSlot,
    skill: dict[str, Any],
    skills: dict[int, dict[str, Any]],
    monster_id: int,
) -> ActiveSlot:
    """Return a slot whose current active is the assist skill.

    The host states stay on the machine so the replacement is visible. The
    slot's current state is only the inherited active, and no transition
    returns the host active beside it. The counter is the assist skill's own
    minimum. This does not combine cooldowns and does not model overcharge.
    """
    state = _state_from_skill(
        f"inherited:{int(skill['id'])}",
        "inherited",
        monster_id,
        skill,
        decode_skill(skill, skills),
    )
    machine = slot.machine.model_copy(deep=True)
    machine.states[state.state_id] = state
    return ActiveSlot(machine=machine, state_id=state.state_id, remaining=_counter(state))


def actives_this_turn(slot: ActiveSlot) -> list[ActiveState]:
    """The usable active for this turn: either empty or the current state."""
    active = slot.usable()
    if active is None:
        return []
    return [active]


def _counter(state: ActiveState) -> int | None:
    if not state.cooldown.known:
        return None
    if state.cooldown.minimum is None:
        return 0
    return state.cooldown.minimum


def _outgoing(machine: SkillMachine, state_id: str) -> Transition | None:
    matches = [
        item for item in machine.transitions if item.source == state_id and item.trigger == "use"
    ]
    if not matches:
        return None
    return matches[0]


def _skill(skills: dict[int, dict[str, Any]], skill_id: int, label: str) -> dict[str, Any]:
    skill = skills.get(skill_id)
    if skill is None:
        raise ValueError(f"{label} is not in the skill index")
    return skill


def _state_from_skill(
    state_id: str,
    kind: StateKind,
    monster_id: int,
    skill: dict[str, Any],
    effects: list[Effect],
    stage_index: int | None = None,
) -> ActiveState:
    return ActiveState(
        state_id=state_id,
        kind=kind,
        monster_id=monster_id,
        skill_id=int(skill["id"]),
        name=str(skill.get("name") or ""),
        cooldown=cooldown_spec(skill),
        effects=list(effects),
        stage_index=stage_index,
    )


def _transform_target(effects: list[Effect]) -> int | None:
    for effect in effects:
        if effect.kind != "transform":
            continue
        parameters = effect.parameters
        if isinstance(parameters, TransformParameters):
            return parameters.monster_id
    return None


def _form_machine(
    monster_id: int,
    active: dict[str, Any],
    effects: list[Effect],
    skills: dict[int, dict[str, Any]],
    cards: dict[int, dict[str, Any]],
) -> SkillMachine:
    states: dict[str, ActiveState] = {}
    transitions: list[Transition] = []
    order: list[str] = []
    pending = [("base", "base", monster_id, active, effects)]
    seen: set[int] = set()
    while pending:
        state_id, kind, form_id, skill, form_effects = pending.pop(0)
        if form_id in seen:
            continue
        seen.add(form_id)
        states[state_id] = _state_from_skill(state_id, kind, form_id, skill, form_effects)
        order.append(state_id)
        target = _transform_target(form_effects)
        if target is None or target in seen:
            continue
        next_id = f"transformed:{target}"
        transitions.append(Transition(source=state_id, target=next_id, trigger="use", known=True))
        other = cards.get(target)
        if other is None:
            states[next_id] = ActiveState(
                state_id=next_id,
                kind="transformed",
                monster_id=target,
                skill_id=None,
                name="",
                cooldown=CooldownSpec(skill_id=None, base=None, minimum=None, known=False),
                effects=[],
            )
            order.append(next_id)
            continue
        next_skill_id = int(other["activeSkillId"])
        next_skill = _skill(skills, next_skill_id, f"monster {target} active {next_skill_id}")
        pending.append(
            (
                next_id,
                "transformed",
                target,
                next_skill,
                decode_skill(next_skill, skills),
            )
        )
    return SkillMachine(
        monster_id=monster_id,
        skill_id=int(active["id"]),
        initial="base",
        order=order,
        states=states,
        transitions=transitions,
        sequence=None,
    )


def _resolved_sequence(effect: Effect, override: SequenceKind | None) -> SequenceKind:
    if override is not None:
        return override
    parameters = effect.parameters
    loops = parameters.loops if isinstance(parameters, EvolvingParameters) else None
    if loops is True:
        return "loops"
    if loops is False:
        return "ends"
    return "unknown"


def _evolving_machine(
    monster_id: int,
    active: dict[str, Any],
    effect: Effect,
    skills: dict[int, dict[str, Any]],
    sequence: SequenceKind | None,
) -> SkillMachine:
    chosen = _resolved_sequence(effect, sequence)
    parameters = effect.parameters
    if not isinstance(parameters, EvolvingParameters):
        raise ValueError(f"monster {monster_id} evolving skill has no stage list")
    stage_ids = [int(value) for value in effect.details.get("stage_ids") or []]
    if not stage_ids:
        stage_ids = [stage.skill_id for stage in parameters.stages if stage.skill_id is not None]
    by_id = {stage.skill_id: stage for stage in parameters.stages}
    states: dict[str, ActiveState] = {}
    order: list[str] = []
    for index, stage_skill_id in enumerate(stage_ids):
        state_id = f"stage:{index}"
        stage = by_id.get(stage_skill_id)
        skill = skills.get(stage_skill_id)
        if stage is None or skill is None:
            states[state_id] = ActiveState(
                state_id=state_id,
                kind="evolving",
                monster_id=monster_id,
                skill_id=stage_skill_id,
                name="",
                cooldown=CooldownSpec(
                    skill_id=stage_skill_id, base=None, minimum=None, known=False
                ),
                effects=[] if stage is None else list(stage.effects),
                stage_index=index,
            )
        else:
            states[state_id] = _state_from_skill(
                state_id, "evolving", monster_id, skill, stage.effects, index
            )
        order.append(state_id)
    if not order:
        raise ValueError(f"monster {monster_id} evolving skill has no stages")
    transitions: list[Transition] = [
        Transition(source=order[index], target=order[index + 1], trigger="use", known=True)
        for index in range(len(order) - 1)
    ]
    last = order[-1]
    if chosen == "loops":
        transitions.append(Transition(source=last, target=order[0], trigger="use", known=True))
    elif chosen == "unknown":
        transitions.append(Transition(source=last, target=None, trigger="use", known=False))
    return SkillMachine(
        monster_id=monster_id,
        skill_id=int(active["id"]),
        initial=order[0],
        order=order,
        states=states,
        transitions=transitions,
        sequence=chosen,
    )
