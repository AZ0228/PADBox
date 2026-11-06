"""Fixed skill-use policy.

The order is leader, subs in listed order, then helper. Transforms go first.
Any other ready active is used only when it answers the scenario's remaining
needs. The policy does not search cast orders.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from padbox.evaluator.sim.state import MemberState
from padbox.mechanics.decode import Effect

Phase = Literal["transform", "utility"]

_HEAL_KINDS = {"heal_rcv", "heal_flat", "heal_percent", "heal_over_time"}
_COLOR_KEYS = ("attributes", "to", "orbs", "colors", "destination", "destinations")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyTarget(_Strict):
    """What the scenario still needs. An empty target uses transforms only."""

    colors: list[str] = Field(default_factory=list)
    mechanics: list[str] = Field(default_factory=list)
    recovery: bool = False


def transform_monster(effects: list[Effect]) -> int | None:
    """Monster id of a verified transform on this cast, if it has one."""
    for effect in effects:
        if effect.kind != "transform" or effect.evidence != "verified":
            continue
        monster_id = effect.details.get("monster_id")
        if isinstance(monster_id, int) and monster_id > 0:
            return monster_id
    return None


def choose(member: MemberState, target: PolicyTarget, phase: Phase) -> bool:
    """Whether this pass tries the member's current active.

    Readiness is the scheduler's decision. This function only applies the
    fixed order's selection rule.
    """
    formed = transform_monster(member.active.state().effects)
    if phase == "transform":
        return formed is not None
    if formed is not None:
        return False
    return _answers(member.active.state().effects, target)


def _answers(effects: list[Effect], target: PolicyTarget) -> bool:
    needed = set(target.mechanics)
    colors = set(target.colors)
    for effect in effects:
        if effect.evidence != "verified":
            continue
        if effect.kind in needed:
            return True
        if target.recovery and (effect.kind in _HEAL_KINDS or "heal" in _colors(effect)):
            return True
        if colors and colors.intersection(_colors(effect)):
            return True
    return False


def _colors(effect: Effect) -> set[str]:
    found: set[str] = set()
    for key in _COLOR_KEYS:
        value = effect.details.get(key)
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, list):
            found.update(str(item) for item in value)
    return found
