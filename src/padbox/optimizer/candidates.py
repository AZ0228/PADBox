"""Owned-copy candidates for one leader.

The pool lists directly owned forms, reachable evolutions, transform forms,
assists, duplicate copies, and helper leaders. A single copy is one
``(owned_id, copy_index)``; its forms are alternatives, so that copy cannot
fill two roles. Pruning is limited to a hard incompatibility, an unreachable
form, an ownership conflict, a missing essential role, or clear dominance of
another form of the same owned card. Every pruned row records that reason.

An unverified effect is an uncertainty score. It is not a prune reason, and
it does not let a fully decoded card displace this one.
"""

from collections import Counter, deque
from contextlib import closing
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from padbox.database.store import Store
from padbox.mechanics.awakenings import NAMES, capped
from padbox.mechanics.coverage import (
    _MAX_DEPTH,
    _ROLES,
    STANDARD_SUB_SLOTS,
    BoxSession,
    Card,
    _cover_report,
    leader_context,
)
from padbox.mechanics.team import (
    _color_attributes,
    _cross_attributes,
    _damage_attributes,
    _form_row,
    _needs_boost,
)

CandidateKind = Literal["leader", "sub", "assist", "helper"]
PruneReason = Literal[
    "hard_incompatibility",
    "unreachable",
    "ownership_conflict",
    "missing_essential_role",
    "dominated",
]
HelperOrigin = Literal["owned", "explicit"]
_ENTRY_KINDS = {"evolution", "assist_evolution"}
_EQUIP_RESISTS = (
    ("bind_resist_percent", 100),
    ("skill_bind_resist_percent", 100),
    ("blind_resist_percent", 100),
    ("jammer_resist_percent", 100),
    ("poison_resist_percent", 100),
    ("cloud_resist", 1),
    ("tape_resist", 1),
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UnknownEffect(_Strict):
    """One active effect the decoder did not verify."""

    source_type: int | None = None
    summary: str


class Candidate(_Strict):
    """One way to field one copy. Rows that share a copy are alternatives."""

    owned_id: int
    copy_index: int
    form_id: int
    form_name: str
    kind: CandidateKind
    availability: Literal["in_box", "reachable"]
    stage: str
    path: list[str]
    spends: list[int] = Field(default_factory=list)
    assumed_materials: list[int] = Field(default_factory=list)
    special_requirements: list[dict[str, Any]] = Field(default_factory=list)
    equip: bool
    main_attribute: str | None = None
    answers: list[str] = Field(default_factory=list)
    skill_boost: int = 0
    minimum_cooldown: int | None = None
    uncertainty: float = 0
    unknown_effects: list[UnknownEffect] = Field(default_factory=list)
    borrowed: bool = False
    helper_origin: HelperOrigin | None = None
    can_damage: bool = False


class PrunedCandidate(_Strict):
    """A form that was considered and removed, with the reason it was removed."""

    owned_id: int | None = None
    copy_index: int | None = None
    form_id: int
    form_name: str
    kind: CandidateKind
    reason: PruneReason
    detail: str
    dominated_by: int | None = None


class CandidatePool(_Strict):
    """The broad pool for one leader. This is not a ranked team."""

    leader_id: int
    helper_id: int | None
    reserved_owned_id: int | None
    reserved_copy_index: int | None
    sub_slots: int
    transform_turns: int | None
    essential_roles: list[str]
    candidates: list[Candidate]
    pruned: list[PrunedCandidate]
    notes: list[str]


def generate_candidates(
    store: Store,
    leader_id: int,
    helper_id: int | None = None,
    skill_index: dict[int, dict[str, Any]] | None = None,
) -> CandidatePool:
    """List owned forms for ``leader_id`` and record every prune.

    ``helper_id`` is an explicit helper. It is borrowed only when no owned
    copy can become that card. Other helper rows are the user's own copies.
    A monster that was neither owned nor passed as ``helper_id`` is not a
    candidate.
    """
    with leader_context(store, leader_id, helper_id, skill_index) as session:
        cover = _cover_report(session)
        attributes = _color_attributes(cover)
        cross = _cross_attributes(cover)
        essential_roles = _essential_roles(cover, session)
        essential = set(essential_roles)
        transform_turns = cover["transform"]["required_turns"]
        if not isinstance(transform_turns, int):
            transform_turns = None
        kept: list[Candidate] = []
        pruned: list[PrunedCandidate] = []
        for owned_id, forms in session.forms_by_owned.items():
            source = session.load(owned_id)
            if source is None:
                continue
            copies = session.known_owned[owned_id]
            for copy_index in range(copies):
                _consider_copy(
                    session,
                    source,
                    forms,
                    copy_index,
                    attributes,
                    cross,
                    essential,
                    kept,
                    pruned,
                )
        if session.helper is not None and not _box_can_field(session, session.helper_id):
            _add_explicit_helper(session, attributes, cross, kept)
        pruned.extend(_unreachable(store, session))
        kept, dominated = _drop_dominated(kept)
        pruned.extend(dominated)
        reserved = session.reserved_from
    kept.sort(key=_candidate_order)
    pruned.sort(key=_prune_order)
    return CandidatePool(
        leader_id=leader_id,
        helper_id=helper_id,
        reserved_owned_id=reserved,
        reserved_copy_index=0 if reserved is not None else None,
        sub_slots=STANDARD_SUB_SLOTS,
        transform_turns=transform_turns,
        essential_roles=essential_roles,
        candidates=kept,
        pruned=pruned,
        notes=_notes(reserved),
    )


def _consider_copy(
    session: BoxSession,
    source: Card,
    forms: list[dict[str, Any]],
    copy_index: int,
    attributes: set[str],
    cross: set[str],
    essential: set[str],
    kept: list[Candidate],
    pruned: list[PrunedCandidate],
) -> None:
    reserved = session.reserved_from == source.monster_id and copy_index == 0
    for form in forms:
        card = session.load(int(form["form_id"]))
        if card is None:
            continue
        row = _form_row(session, source, form, attributes, cross, False)
        leader_slot = reserved and _is_leader_form(session, form)
        kinds = _kinds(row, card, leader_slot=leader_slot)
        for kind in kinds:
            candidate = _build(session, source.monster_id, copy_index, form, row, card, kind)
            if reserved and kind != "leader":
                pruned.append(
                    _rejected(
                        candidate,
                        "ownership_conflict",
                        (
                            f"Copy {copy_index} of #{source.monster_id} {source.name} "
                            f"is reserved for the leader and cannot also be a {kind}."
                        ),
                    )
                )
                continue
            if not reserved:
                short = _shortfall(session.remaining, source.monster_id, candidate.spends)
                if short:
                    listed = ", ".join(f"#{monster_id}" for monster_id in short)
                    pruned.append(
                        _rejected(
                            candidate,
                            "ownership_conflict",
                            (
                                f"#{candidate.form_id} {candidate.form_name} needs {listed}, "
                                "and that copy is already reserved."
                            ),
                        )
                    )
                    continue
            if kind == "helper" and attributes and not candidate.can_damage:
                main = candidate.main_attribute or "untyped"
                listed = "/".join(sorted(attributes))
                pruned.append(
                    _rejected(
                        candidate,
                        "hard_incompatibility",
                        (
                            f"#{candidate.form_id} {candidate.form_name} is {main}, "
                            f"and a helper has to be {listed} to deal damage on that match."
                        ),
                    )
                )
                continue
            if (
                kind in {"sub", "assist"}
                and not _contributes(candidate, essential)
                and not (kind == "assist" and _equip_utility(card.awakenings))
            ):
                pruned.append(
                    _rejected(
                        candidate,
                        "missing_essential_role",
                        (
                            f"#{candidate.form_id} {candidate.form_name} has no verified "
                            f"{kind} answer for this leader."
                        ),
                    )
                )
                continue
            if kind == "helper":
                origin: HelperOrigin = (
                    "explicit"
                    if session.helper_id is not None and candidate.form_id == session.helper_id
                    else "owned"
                )
                candidate = candidate.model_copy(update={"helper_origin": origin})
            kept.append(candidate)


def _add_explicit_helper(
    session: BoxSession,
    attributes: set[str],
    cross: set[str],
    kept: list[Candidate],
) -> None:
    """The named helper is not in the box. It is not a friend's assumed card."""
    helper = session.helper
    if helper is None:
        return
    entry: Card = helper["card"]
    for form in helper["forms"]:
        card = session.load(int(form["form_id"]))
        if card is None:
            continue
        row = _form_row(session, entry, form, attributes, cross, True)
        kept.append(
            _build(
                session,
                entry.monster_id,
                0,
                form,
                row,
                card,
                "helper",
                borrowed=True,
                helper_origin="explicit",
            )
        )


def _unreachable(store: Store, session: BoxSession) -> list[PrunedCandidate]:
    outgoing = _outgoing(store)
    pruned: list[PrunedCandidate] = []
    seen: set[tuple[int, int]] = set()
    for owned_id, forms in session.forms_by_owned.items():
        reached = {int(form["form_id"]) for form in forms}
        for target_id, kind in _unreached_targets(owned_id, reached, outgoing):
            key = (owned_id, target_id)
            if key in seen:
                continue
            seen.add(key)
            card = session.load(target_id)
            name = card.name if card else ""
            role: CandidateKind = "assist" if kind == "assist_evolution" else "sub"
            pruned.append(
                PrunedCandidate(
                    owned_id=owned_id,
                    form_id=target_id,
                    form_name=name,
                    kind=role,
                    reason="unreachable",
                    detail=_unreachable_detail(session, owned_id, target_id, name),
                )
            )
    return pruned


def _unreached_targets(
    owned_id: int,
    reached: set[int],
    outgoing: dict[int, list[tuple[int, str]]],
) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    depth = {owned_id: 0}
    queue: deque[int] = deque([owned_id])
    while queue:
        current = queue.popleft()
        here = depth[current]
        for target, kind in outgoing.get(current, []):
            if kind not in _ENTRY_KINDS:
                continue
            if here >= _MAX_DEPTH:
                if target not in reached:
                    found.append((target, kind))
                continue
            if target not in depth:
                depth[target] = here + 1
                queue.append(target)
            if target not in reached:
                found.append((target, kind))
    sources = reached | {owned_id}
    for source in sources:
        for target, kind in outgoing.get(source, []):
            if kind == "transform" and target not in reached:
                found.append((target, kind))
    return found


def _unreachable_detail(session: BoxSession, owned_id: int, target_id: int, name: str) -> str:
    label = f"#{target_id} {name}".rstrip()
    card = session.load(target_id)
    if card is None:
        return f"{label} is not in the local reference set, so owned #{owned_id} cannot become it."
    short = _missing_materials(session, card)
    if short:
        listed = ", ".join(f"#{monster_id}" for monster_id in short)
        return f"{label} is unreachable from owned #{owned_id}: the box does not have {listed}."
    return (
        f"{label} is unreachable from owned #{owned_id}: "
        "the evolution is deeper than the reachable path."
    )


def _missing_materials(session: BoxSession, card: Card) -> list[int]:
    needed: Counter[int] = Counter()
    for value in card.raw.get("evoMaterials") or []:
        if not value:
            continue
        material_id = int(value)
        material = session.load(material_id)
        if material is None or material.raw.get("stackable"):
            continue
        needed[material_id] += 1
    return sorted(
        material_id
        for material_id, count in needed.items()
        if session.known_owned.get(material_id, 0) < count
    )


def _drop_dominated(
    kept: list[Candidate],
) -> tuple[list[Candidate], list[PrunedCandidate]]:
    """Drop a form only when another surviving form of that card strictly covers it.

    A different card never dominates this one. An extra unverified effect
    blocks dominance, so a cleaner active cannot bury a useful cooldown.
    """
    eligible = {
        index for index, candidate in enumerate(kept) if candidate.kind in {"sub", "assist"}
    }
    dropped: set[int] = set()
    while eligible:
        maximal = [
            index
            for index in eligible
            if not any(
                other != index and _strictly_better(kept[other], kept[index]) for other in eligible
            )
        ]
        if not maximal:
            break
        eligible -= set(maximal)
        for index in list(eligible):
            if any(_strictly_better(kept[other], kept[index]) for other in maximal):
                dropped.add(index)
                eligible.remove(index)
    pruned: list[PrunedCandidate] = []
    rest: list[Candidate] = []
    survivors = [kept[index] for index in range(len(kept)) if index not in dropped]
    for index, candidate in enumerate(kept):
        if index not in dropped:
            rest.append(candidate)
            continue
        dominator = _dominator(survivors, candidate)
        form_id = dominator.form_id if dominator is not None else None
        name = dominator.form_name if dominator is not None else "another form"
        pruned.append(
            _rejected(
                candidate,
                "dominated",
                _dominance_detail(candidate, form_id, name, dominator),
                dominated_by=form_id,
            )
        )
    return rest, pruned


def _dominator(survivors: list[Candidate], subject: Candidate) -> Candidate | None:
    found: Candidate | None = None
    for other in survivors:
        if not _strictly_better(other, subject):
            continue
        if found is None or _prefer_dominator(other, found):
            found = other
    return found


def _prefer_dominator(left: Candidate, right: Candidate) -> bool:
    left_cd = left.minimum_cooldown if isinstance(left.minimum_cooldown, int) else 10**9
    right_cd = right.minimum_cooldown if isinstance(right.minimum_cooldown, int) else 10**9
    return (left_cd, -left.skill_boost, left.form_id) < (
        right_cd,
        -right.skill_boost,
        right.form_id,
    )


def _strictly_better(better: Candidate, worse: Candidate) -> bool:
    if better.kind != worse.kind or better.owned_id != worse.owned_id:
        return False
    if better.form_id == worse.form_id:
        return False
    if better.uncertainty != worse.uncertainty:
        return False
    if not set(worse.answers) <= set(better.answers):
        return False
    if worse.can_damage and not better.can_damage:
        return False
    if not _cooldown_no_worse(better, worse):
        return False
    if better.skill_boost < worse.skill_boost or len(better.spends) > len(worse.spends):
        return False
    return _strict_gain(better, worse)


def _cooldown_no_worse(better: Candidate, worse: Candidate) -> bool:
    left = better.minimum_cooldown
    right = worse.minimum_cooldown
    if isinstance(left, int) and isinstance(right, int):
        return left <= right
    return left == right


def _strict_gain(better: Candidate, worse: Candidate) -> bool:
    left = better.minimum_cooldown
    right = worse.minimum_cooldown
    shorter = isinstance(left, int) and isinstance(right, int) and left < right
    return bool(
        shorter
        or better.skill_boost > worse.skill_boost
        or set(better.answers) > set(worse.answers)
        or len(better.spends) < len(worse.spends)
        or (better.can_damage and not worse.can_damage)
    )


def _dominance_detail(
    worse: Candidate,
    form_id: int | None,
    name: str,
    better: Candidate | None,
) -> str:
    gains: list[str] = []
    if better is not None:
        left = better.minimum_cooldown
        right = worse.minimum_cooldown
        if isinstance(left, int) and isinstance(right, int) and left < right:
            gains.append("a shorter cooldown")
        if better.skill_boost > worse.skill_boost:
            gains.append("more skill boost")
        if set(better.answers) > set(worse.answers):
            gains.append("a broader verified active")
        if len(better.spends) < len(worse.spends):
            gains.append("a cheaper evolution")
        if better.can_damage and not worse.can_damage:
            gains.append("damage on the leader color")
    listed = ", ".join(gains) if gains else "a strictly better verified profile"
    other = f"#{form_id} {name}".rstrip() if form_id is not None else name
    return (
        f"#{worse.form_id} {worse.form_name} is dominated by {other} "
        f"of owned #{worse.owned_id}: {listed}."
    )


def _build(
    session: BoxSession,
    owned_id: int,
    copy_index: int,
    form: dict[str, Any],
    row: dict[str, Any],
    card: Card,
    kind: CandidateKind,
    *,
    borrowed: bool = False,
    helper_origin: HelperOrigin | None = None,
) -> Candidate:
    unknowns = [
        UnknownEffect(
            source_type=(
                int(effect["source_type"]) if effect.get("source_type") is not None else None
            ),
            summary=str(effect.get("summary") or ""),
        )
        for effect in row["active"]["unknown_effects"]
    ]
    uncertainty = float(len(unknowns))
    if kind in {"leader", "helper"}:
        facts = session.facts_for(card)
        uncertainty += float(
            sum(effect.evidence != "verified" for effect in facts["leader_effects"])
        )
    cooldown = row["active"]["minimum_cooldown"]
    return Candidate(
        owned_id=owned_id,
        copy_index=copy_index,
        form_id=int(row["form_id"]),
        form_name=str(row["form_name"]),
        kind=kind,
        availability=row["availability"],
        stage=str(form["stage"]),
        path=[str(step) for step in form.get("path") or []],
        spends=[int(material_id) for material_id in form.get("spends") or []],
        assumed_materials=[int(material_id) for material_id in form.get("assumed_materials") or []],
        special_requirements=list(form.get("special_requirements") or []),
        equip=bool(row["equip"]),
        main_attribute=row["main_attribute"],
        answers=list(row["answers"]),
        skill_boost=int(row["skill_boost"]),
        minimum_cooldown=cooldown if isinstance(cooldown, int) else None,
        uncertainty=uncertainty,
        unknown_effects=unknowns,
        borrowed=borrowed,
        helper_origin=helper_origin,
        can_damage=bool(row["can_damage"]),
    )


def _kinds(row: dict[str, Any], card: Card, *, leader_slot: bool) -> list[CandidateKind]:
    if leader_slot:
        return ["leader"]
    kinds: list[CandidateKind] = ["sub"]
    if row["equip"]:
        kinds.append("assist")
    if card.leader:
        kinds.append("helper")
    return kinds


def _is_leader_form(session: BoxSession, form: dict[str, Any]) -> bool:
    if int(form["form_id"]) != session.leader_id:
        return False
    if session.transforms and session.leader_id in session.combats:
        return form["stage"] == "battle"
    return form["stage"] == "entry"


def _equip_utility(awakenings: list[int]) -> bool:
    """A resist or a sub-attribute change is enough to keep an equip in the pool."""
    for awakening_id in awakenings:
        name = NAMES.get(awakening_id, "")
        if name.startswith("Change Sub Attribute:") or name == "Skill Delay Resistance":
            return True
    return any(capped(awakenings, key) >= threshold for key, threshold in _EQUIP_RESISTS)


def _contributes(candidate: Candidate, essential: set[str]) -> bool:
    """True when the card is kept. An unverified effect is not treated as absence."""
    if candidate.uncertainty > 0:
        return True
    if set(candidate.answers) & essential:
        return True
    return "damage" in essential and candidate.can_damage


def _essential_roles(cover: dict[str, Any], session: BoxSession) -> list[str]:
    roles = [key for key, _, _ in _ROLES]
    roles.append("haste")
    if _needs_boost(session):
        roles.append("skill_boost")
    if _damage_attributes(cover):
        roles.append("damage")
    for color in cover["color_force"]:
        for name in color["attributes"]:
            if not name:
                continue
            if color.get("match") == "cross":
                roles.append(f"{name}_orbs")
            else:
                roles.append(f"{name}_board")
                roles.append(f"{name}_orbs")
    return sorted(set(roles))


def _box_can_field(session: BoxSession, monster_id: int | None) -> bool:
    if monster_id is None:
        return False
    return any(
        int(form["form_id"]) == monster_id
        for forms in session.forms_by_owned.values()
        for form in forms
    )


def _shortfall(remaining: dict[int, int], owned_id: int, spends: list[int]) -> list[int]:
    need = Counter(spends)
    need[owned_id] += 1
    return sorted(
        monster_id for monster_id, count in need.items() if remaining.get(monster_id, 0) < count
    )


def _rejected(
    candidate: Candidate,
    reason: PruneReason,
    detail: str,
    *,
    dominated_by: int | None = None,
) -> PrunedCandidate:
    return PrunedCandidate(
        owned_id=candidate.owned_id,
        copy_index=candidate.copy_index,
        form_id=candidate.form_id,
        form_name=candidate.form_name,
        kind=candidate.kind,
        reason=reason,
        detail=detail,
        dominated_by=dominated_by,
    )


def _outgoing(store: Store) -> dict[int, list[tuple[int, str]]]:
    outgoing: dict[int, list[tuple[int, str]]] = {}
    with closing(store.connect("reference")) as db:
        for row in db.execute("SELECT source_id, target_id, kind FROM relationship"):
            outgoing.setdefault(int(row["source_id"]), []).append(
                (int(row["target_id"]), str(row["kind"]))
            )
    return outgoing


def _notes(reserved: int | None) -> list[str]:
    notes = [
        "Each owned copy is one (owned_id, copy_index). Forms of that copy are alternatives.",
        "A form's spends are checked on their own, after the leader copy is reserved.",
        "Unverified effects are an uncertainty score. They are not a prune reason.",
        "A helper is the explicit helper, or an owned copy with a leader skill.",
        (
            "An equip with a hazard resist or a sub-attribute change stays "
            "even with no essential active."
        ),
        "This pool does not rank a team.",
        f"A standard team has {STANDARD_SUB_SLOTS} sub slots and one helper.",
    ]
    if reserved is None:
        notes.append("No owned copy can become this leader.")
    else:
        notes.append(f"Copy 0 of #{reserved} is reserved for the leader.")
    return notes


def _candidate_order(candidate: Candidate) -> tuple:
    return (
        candidate.kind,
        candidate.owned_id,
        candidate.copy_index,
        candidate.form_id,
        candidate.borrowed,
    )


def _prune_order(row: PrunedCandidate) -> tuple:
    return (row.reason, row.owned_id or -1, row.form_id, row.kind, row.detail)
