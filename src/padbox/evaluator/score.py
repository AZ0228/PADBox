"""Hierarchical scores for one evaluated team.

The formulas are the ones in ``docs/scoring-model.md``. A component is
unresolved when the simulator did not measure it. An unresolved component
stays out of the scalar and is not stored as zero.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from padbox.mechanics.result import CheckResult

ObjectiveName = Literal[
    "general",
    "farming",
    "high-difficulty",
    "boss-damage",
    "consistent",
    "fast",
]
ComponentName = Literal[
    "activation_consistency",
    "survivability",
    "mechanic_coverage",
    "damage",
    "flexibility",
]
QualityStatus = Literal["ranked", "excluded", "unresolved"]
TraceRole = Literal["value", "beside", "context"]

# Earlier names sort first. Every scale is 1. The named scale is recorded
# so the formula stays visible; it does not change the order.
OBJECTIVE_ORDER: dict[str, tuple[str, ...]] = {
    "general": ("activation_consistency", "survivability", "damage", "flexibility"),
    "farming": ("activation_consistency", "flexibility", "survivability", "damage"),
    "high-difficulty": ("survivability", "activation_consistency", "damage", "flexibility"),
    "boss-damage": ("damage", "activation_consistency", "survivability", "flexibility"),
    "consistent": ("activation_consistency", "survivability", "flexibility", "damage"),
    "fast": ("damage", "flexibility", "activation_consistency", "survivability"),
}
OBJECTIVE_SCALE: dict[str, dict[str, int]] = {
    "general": {},
    "farming": {"flexibility": 1},
    "high-difficulty": {"survivability": 1},
    "boss-damage": {"damage": 1},
    "consistent": {"activation_consistency": 1},
    "fast": {"damage": 1},
}
_PLACE = 1001

# Short names used by a scenario, resolved before the utility and timing keys.
_ALIASES = {
    "damage absorption": "damage absorb void",
    "damage absorb": "damage absorb void",
    "attribute absorption": "attribute absorb void",
    "attribute absorb": "attribute absorb void",
    "damage void": "damage void pierce",
    "void pierce": "damage void pierce",
    "awoken bind": "awoken bind clear",
    "unmatchable": "unmatchable clear",
    "bind clear": "bind clear",
    "combo absorb": "combo absorb void",
    "combo absorption": "combo absorb void",
}
# Scenario labels that name one utility key. Absorb subtypes stay split.
REQUIREMENT_KEYS: dict[str, tuple[str, ...]] = {
    "bind clear": ("bind_clear",),
    "awoken bind clear": ("awoken_bind_clear",),
    "unmatchable clear": ("unmatchable_clear",),
    "damage absorb void": ("absorb_void:damage",),
    "attribute absorb void": ("absorb_void:attribute",),
    "combo absorb void": ("absorb_void:combo",),
    "damage void pierce": ("void_pierce",),
}
# Kinds the turn simulator records on a cast or a timed effect.
POLICY_KINDS: dict[str, str] = {
    "bind clear": "bind_clear",
    "awoken bind clear": "awoken_bind_clear",
    "unmatchable clear": "unmatchable_clear",
    "damage absorb void": "absorb_void",
    "attribute absorb void": "absorb_void",
    "combo absorb void": "absorb_void",
    "damage void pierce": "void_pierce",
}

_ACTIVATION = (
    "activation_consistency = leader_activation. "
    "The value is that metric's rate: the mean, across simulations, of the share of "
    "resolved turns on which every leader clause is met. offensive_activation, "
    "defensive_activation, and orb_generation_uptime are recorded beside it. "
    "longest_activation_gap is recorded beside it and is left out of the scalar."
)
_SURVIVAL = (
    "survivability = (runs with HP above 0) / runs, using the effective HP, shield "
    "uptime, and recovery the simulator recorded. A scenario with no enemy damage "
    "leaves this unresolved."
)
_DAMAGE = (
    "damage = (turns that meet the damage target) / (turns with a damage target). "
    "A turn with no damage target is left out of the average. The target is the "
    "attack multiplier, the attribute match, and the damage-cap state together."
)
_FLEXIBILITY = (
    "flexibility = 1 - max(0, unused_utility - 1) / slot_count when slot_count > 0, "
    "and 1 when slot_count is 0. The result is clamped to [0, 1]."
)
_COVERAGE = (
    "mechanic_coverage is mechanic_response_availability when the simulator reported "
    "that metric. Otherwise it is the share of decoder requirements judged pass among "
    "those judged pass or fail. An unknown requirement leaves the value unresolved."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricTrace(_Strict):
    """One input of a component. ``beside`` and ``context`` stay out of the scalar."""

    source: str
    value: float | None
    role: TraceRole
    note: str = ""


class ComponentScore(_Strict):
    """One quality dimension. ``value`` is null while the dimension is unresolved."""

    name: ComponentName
    value: float | None
    status: Literal["resolved", "unresolved"]
    formula: str
    detail: str
    traces: list[MetricTrace] = Field(default_factory=list)
    gap: float | None = None


class RankTerm(_Strict):
    """One position in the objective tuple, and the integer that enters the scalar."""

    component: str
    value: float | None
    scale: int
    rounded: int | None
    place: int


class RankResult(_Strict):
    """The objective's sort key. ``score`` is absent unless every term resolved."""

    objective: str
    order: list[str]
    terms: list[RankTerm]
    rank_key: list[float | None]
    score: int | None


class Quality(_Strict):
    """The five dimensions and the four-term rank for one objective."""

    strategic_quality: QualityStatus
    components: list[ComponentScore]
    rank: RankResult


def policy_mechanics(requirements: list[str]) -> list[str]:
    """Effect kinds the simulator can time for these scenario requirement names."""
    found: list[str] = []
    for name in requirements:
        kind = POLICY_KINDS.get(_canonical(name))
        if kind and kind not in found:
            found.append(kind)
    return found


def score_team(
    objective: str,
    feasibility: CheckResult,
    constraints: list[Any],
    metrics: list[Any] | None,
    slot_kinds: list[list[str]],
    prior_kinds: list[str],
    requirements: list[str],
    *,
    simulation_note: str | None = None,
) -> Quality:
    """Score one team. Invalid feasibility excludes it. Unknown stays unresolved."""
    by_metric = {str(metric.name): metric for metric in metrics or []}
    components = [
        _activation(by_metric, simulation_note),
        _survivability(by_metric),
        _coverage(by_metric, constraints),
        _damage(by_metric),
        _flexibility(slot_kinds, prior_kinds, requirements),
    ]
    return _assemble(objective, feasibility, components)


def _activation(metrics: dict[str, Any], simulation_note: str | None) -> ComponentScore:
    leader = metrics.get("leader_activation")
    traces = [
        _from_metric(leader, "value"),
        _from_metric(metrics.get("offensive_activation"), "beside"),
        _from_metric(metrics.get("defensive_activation"), "beside"),
        _from_metric(metrics.get("orb_generation_uptime"), "beside"),
        _from_metric(metrics.get("longest_activation_gap"), "beside"),
    ]
    gap_metric = metrics.get("longest_activation_gap")
    gap = _number(gap_metric) if gap_metric is not None else None
    value = _number(leader) if leader is not None else None
    if simulation_note:
        detail = simulation_note
    elif leader is None:
        detail = "The simulator omitted leader_activation."
    elif value is None:
        detail = "leader_activation has no resolved turns."
    else:
        detail = "The value is the leader_activation rate."
    return _component(
        "activation_consistency",
        value,
        _ACTIVATION,
        detail,
        [trace for trace in traces if trace is not None],
        gap=gap,
    )


def _survivability(metrics: dict[str, Any]) -> ComponentScore:
    traces = [
        _from_metric(metrics.get("healing_orb_availability"), "context"),
        _from_metric(metrics.get("active_effect_uptime"), "context"),
    ]
    return _component(
        "survivability",
        None,
        _SURVIVAL,
        "Enemy damage is not simulated, so the survival rate is unresolved.",
        [trace for trace in traces if trace is not None],
    )


def _damage(metrics: dict[str, Any]) -> ComponentScore:
    offense = _from_metric(metrics.get("offensive_activation"), "context")
    traces = [offense] if offense is not None else []
    return _component(
        "damage",
        None,
        _DAMAGE,
        "The scenario has no damage target, and the simulator does not record "
        "attack-multiplier, attribute-match, and damage-cap success. "
        "offensive_activation is not this share.",
        traces,
    )


def _coverage(metrics: dict[str, Any], constraints: list[Any]) -> ComponentScore:
    decoder = [item for item in constraints if str(item.name).startswith("requirement:")]
    scenario = [item for item in constraints if str(item.name).startswith("scenario:")]
    unknown = [item for item in decoder + scenario if item.result is CheckResult.UNKNOWN]
    passes = sum(item.result is CheckResult.PASS for item in decoder)
    fails = sum(item.result is CheckResult.FAIL for item in decoder)
    judged = passes + fails
    decoder_rate = passes / judged if judged else None
    timed = metrics.get("mechanic_response_availability")
    timed_value = _number(timed) if timed is not None else None
    use_timing = timed_value is not None and not unknown
    use_decoder = timed is None and decoder_rate is not None and not unknown
    traces: list[MetricTrace] = []
    if timed is not None:
        trace = _from_metric(timed, "value" if use_timing else "context")
        if trace is not None:
            traces.append(trace)
    if decoder_rate is not None or decoder:
        traces.append(
            MetricTrace(
                source="decoder_requirements",
                value=decoder_rate,
                role="value" if use_decoder else "context",
                note=f"{passes} pass, {fails} fail, {len(unknown)} unknown",
            )
        )
    if unknown:
        names = ", ".join(str(item.name) for item in unknown)
        return _component(
            "mechanic_coverage",
            None,
            _COVERAGE,
            f"Unresolved requirements stay unresolved: {names}.",
            traces,
        )
    if timed is not None:
        number = _number(timed)
        if number is None:
            return _component(
                "mechanic_coverage",
                None,
                _COVERAGE,
                "mechanic_response_availability has no resolved samples.",
                traces,
            )
        return _component(
            "mechanic_coverage",
            number,
            _COVERAGE,
            "The value is mechanic_response_availability.",
            traces,
        )
    if decoder_rate is not None:
        return _component(
            "mechanic_coverage",
            decoder_rate,
            _COVERAGE,
            "The simulator omitted mechanic_response_availability. "
            "The value is the share of judged decoder requirements that pass.",
            traces,
        )
    return _component(
        "mechanic_coverage",
        None,
        _COVERAGE,
        "The simulator omitted mechanic_response_availability and the check "
        "listed no judged decoder requirement.",
        traces,
    )


def _flexibility(
    slot_kinds: list[list[str]], prior_kinds: list[str], requirements: list[str]
) -> ComponentScore:
    needed = _needed(requirements)
    covered = set(prior_kinds)
    unused = 0
    for kinds in slot_kinds:
        relevant = _relevant(kinds, needed)
        if not relevant:
            continue
        if any(kind not in covered for kind in relevant):
            covered.update(relevant)
            continue
        unused += 1
    slot_count = len(slot_kinds)
    if slot_count == 0:
        value = 1.0
    else:
        value = 1 - max(0, unused - 1) / slot_count
        value = min(1.0, max(0.0, value))
    note = f"unused_utility={unused}, slot_count={slot_count}"
    return _component(
        "flexibility",
        value,
        _FLEXIBILITY,
        note,
        [MetricTrace(source="utility_slots", value=value, role="value", note=note)],
    )


def _needed(requirements: list[str]) -> set[str] | None:
    """Utility keys the scenario named. None means every verified kind counts."""
    found: set[str] = set()
    for name in requirements:
        found.update(REQUIREMENT_KEYS.get(_canonical(name), ()))
    if not found:
        return None
    return found


def _relevant(kinds: list[str], needed: set[str] | None) -> list[str]:
    unique = list(dict.fromkeys(kinds))
    if needed is None:
        return unique
    return [kind for kind in unique if kind in needed]


def _assemble(
    objective: str, feasibility: CheckResult, components: list[ComponentScore]
) -> Quality:
    """Attach the rank. A scalar exists only for a feasible team with every term."""
    rank = _rank(objective, components)
    if feasibility is not CheckResult.PASS:
        rank = rank.model_copy(update={"score": None})
    return Quality(
        strategic_quality=_status(feasibility, rank),
        components=components,
        rank=rank,
    )


def _rank(objective: str, components: list[ComponentScore]) -> RankResult:
    by_name = {component.name: component for component in components}
    order = OBJECTIVE_ORDER[objective]
    scales = OBJECTIVE_SCALE[objective]
    terms: list[RankTerm] = []
    for index, name in enumerate(order):
        component = by_name[name]
        scale = scales.get(name, 1)
        rounded = None if component.value is None else _thousandths(component.value, scale)
        terms.append(
            RankTerm(
                component=name,
                value=component.value,
                scale=scale,
                rounded=rounded,
                place=_PLACE ** (len(order) - 1 - index),
            )
        )
    score = None
    if all(term.rounded is not None for term in terms):
        score = sum(term.rounded * term.place for term in terms if term.rounded is not None)
    return RankResult(
        objective=objective,
        order=list(order),
        terms=terms,
        rank_key=[term.value for term in terms],
        score=score,
    )


def _status(feasibility: CheckResult, rank: RankResult) -> QualityStatus:
    if feasibility is CheckResult.FAIL:
        return "excluded"
    if feasibility is CheckResult.UNKNOWN or rank.score is None:
        return "unresolved"
    return "ranked"


def _component(
    name: ComponentName,
    value: float | None,
    formula: str,
    detail: str,
    traces: list[MetricTrace],
    gap: float | None = None,
) -> ComponentScore:
    return ComponentScore(
        name=name,
        value=value,
        status="resolved" if value is not None else "unresolved",
        formula=formula,
        detail=detail,
        traces=traces,
        gap=gap,
    )


def _from_metric(metric: Any, role: TraceRole) -> MetricTrace | None:
    if metric is None:
        return None
    return MetricTrace(source=str(metric.name), value=_number(metric), role=role)


def _number(metric: Any) -> float | None:
    value = getattr(metric, "value", None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _thousandths(value: float, scale: int) -> int:
    """Round ``value * scale`` to the nearest thousandth, half up, then clamp."""
    scaled = Decimal(str(value)) * Decimal(scale)
    if scaled < 0:
        scaled = Decimal(0)
    if scaled > 1:
        scaled = Decimal(1)
    rounded = int((scaled * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return min(1000, max(0, rounded))


def _canonical(name: str) -> str:
    key = _normalize(name)
    return _ALIASES.get(key, key)


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().replace("_", " ").split())
