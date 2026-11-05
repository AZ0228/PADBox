"""Coverage of one leader against the applied box.

This reports which verified roles owned copies can reach. It does not pick a team,
score cards, or spend the badge. Each copy can become one form. An assist
evolution spends that copy. A borrowed helper does not have to be owned.
"""

import json
from collections import Counter, deque
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from typing import Any

from padbox.database.store import Store
from padbox.inventory.snapshots import current_box
from padbox.mechanics.awakenings import (
    NAMES,
    capped,
    describe,
    has_value,
    skill_boost,
    total,
)
from padbox.mechanics.decode import DECODER_VERSION, Effect
from padbox.mechanics.explain import _index, _skill_report, roles_from

_ENTRY_KINDS = {"evolution", "assist_evolution"}
_MAX_DEPTH = 8
_PROVIDER_LIMIT = 30
_ROLES = (
    ("bind_clear", "bind clear", lambda effect: effect.kind == "bind_clear"),
    ("awoken_bind_clear", "awoken bind clear", lambda effect: effect.kind == "awoken_bind_clear"),
    ("unmatchable_clear", "unmatchable clear", lambda effect: effect.kind == "unmatchable_clear"),
    (
        "attribute_absorb",
        "attribute absorb void",
        lambda effect: (
            effect.kind == "absorb_void" and "attribute" in effect.details.get("kinds", [])
        ),
    ),
    (
        "damage_absorb",
        "damage absorb void",
        lambda effect: effect.kind == "absorb_void" and "damage" in effect.details.get("kinds", []),
    ),
    (
        "combo_absorb",
        "combo absorb void",
        lambda effect: effect.kind == "absorb_void" and "combo" in effect.details.get("kinds", []),
    ),
    ("void_pierce", "damage void pierce", lambda effect: effect.kind == "void_pierce"),
)


@dataclass
class Card:
    monster_id: int
    name: str
    raw: dict[str, Any]
    awakenings: list[int]
    super_awakenings: list[int]
    active: dict[str, Any] | None
    leader: dict[str, Any] | None


@dataclass
class BoxSession:
    box: dict[str, Any]
    owned: dict[int, int]
    unconfirmed: int
    known_owned: dict[int, int]
    missing_owned: list[int]
    forms_by_owned: dict[int, list[dict[str, Any]]]
    load: Any
    facts_for: Any
    index_status: str
    leader_id: int
    helper_id: int | None
    combats: list[int]
    transforms: bool
    available: int
    entry: Card
    entry_facts: dict[str, Any]
    reserved_from: int | None
    reserved_path: list[str]
    spends: list[int]
    helper: dict[str, Any] | None
    buckets: dict[str, list[dict[str, Any]]]
    haste: list[dict[str, Any]]
    unnamed: set[int]
    unknown_forms: set[tuple[int, int]]
    remaining: dict[int, int]
    pinned: set[tuple[int, int]]


def cover_box(
    store: Store,
    leader_id: int,
    helper_id: int | None = None,
    skill_index: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    with leader_context(store, leader_id, helper_id, skill_index) as session:
        return _cover_report(session)


@contextmanager
def leader_context(
    store: Store,
    leader_id: int,
    helper_id: int | None = None,
    skill_index: dict[int, dict[str, Any]] | None = None,
) -> Iterator[BoxSession]:
    box = current_box(store)
    if box is None:
        raise ValueError("No applied box. Review a full-box scan and apply it before cover.")
    owned, unconfirmed = _owned(box)
    with closing(store.connect("reference")) as db:
        outgoing, incoming = _graph(db)
        cache: dict[int, Card | None] = {}

        def load(monster_id: int) -> Card | None:
            return _load(db, cache, monster_id)

        leader = load(leader_id)
        if leader is None:
            raise ValueError(f"Monster #{leader_id} is not in the local reference set")
        if helper_id is not None and load(helper_id) is None:
            raise ValueError(f"Monster #{helper_id} is not in the local reference set")
        version = _version(db, leader_id)
        index = skill_index if skill_index is not None else _index(store.home, version, None)
        facts_cache: dict[int, dict[str, Any]] = {}

        def facts_for(card: Card) -> dict[str, Any]:
            return _facts(card, index, facts_cache)

        entries, combats, transforms = _identity(leader_id, incoming, outgoing)
        known_owned = {monster_id: qty for monster_id, qty in owned.items() if load(monster_id)}
        missing_owned = sorted(set(owned) - set(known_owned))
        forms_by_owned = {
            monster_id: _reachable(monster_id, outgoing, load, known_owned)
            for monster_id in known_owned
        }
        reservation = _reserve(entries, forms_by_owned, known_owned)
        available = _count_available(entries, forms_by_owned, known_owned)
        remaining = dict(known_owned)
        entry_id = entries[0]
        reserved_path: list[str] = []
        spends: list[int] = []
        if reservation:
            reserved_from, entry_id, spends, reserved_path = reservation
            remaining[reserved_from] = remaining.get(reserved_from, 0) - 1
            for material_id in spends:
                remaining[material_id] = remaining.get(material_id, 0) - 1
        else:
            reserved_from = None
        pinned: set[tuple[int, int]] = set()
        if reserved_from is not None:
            for form in forms_by_owned.get(reserved_from, []):
                pinned.add((reserved_from, int(form["form_id"])))
        entry = load(entry_id)
        if entry is None:
            raise ValueError(f"Monster #{entry_id} is not in the local reference set")
        entry_facts = facts_for(entry)
        helper = _helper(helper_id, incoming, outgoing, load) if helper_id else None
        if helper:
            for form in helper["forms"]:
                pinned.add((helper["card"].monster_id, int(form["form_id"])))
        buckets: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in _ROLES}
        haste: list[dict[str, Any]] = []
        unnamed: set[int] = set()
        unknown_forms: set[tuple[int, int]] = set()
        for owned_id, forms in forms_by_owned.items():
            source = load(owned_id)
            if source is None:
                continue
            _collect(
                buckets,
                haste,
                unnamed,
                unknown_forms,
                source,
                forms,
                load,
                facts_for,
                known_owned[owned_id],
                False,
                pinned,
            )
        if helper:
            _collect(
                buckets,
                haste,
                unnamed,
                unknown_forms,
                helper["card"],
                helper["forms"],
                load,
                facts_for,
                1,
                True,
                pinned,
            )
        index_status = "supplied" if skill_index is not None else ("cached" if index else "missing")
        yield BoxSession(
            box=box,
            owned=owned,
            unconfirmed=unconfirmed,
            known_owned=known_owned,
            missing_owned=missing_owned,
            forms_by_owned=forms_by_owned,
            load=load,
            facts_for=facts_for,
            index_status=index_status,
            leader_id=leader_id,
            helper_id=helper_id,
            combats=combats,
            transforms=transforms,
            available=available,
            entry=entry,
            entry_facts=entry_facts,
            reserved_from=reserved_from,
            reserved_path=reserved_path,
            spends=spends,
            helper=helper,
            buckets=buckets,
            haste=haste,
            unnamed=unnamed,
            unknown_forms=unknown_forms,
            remaining=remaining,
            pinned=pinned,
        )


def _cover_report(session: BoxSession) -> dict[str, Any]:
    sub_slots = 4 if session.helper else 5
    transform = _transform(
        session.entry,
        session.entry_facts,
        session.combats,
        session.transforms,
        session.reserved_from is not None,
        session.helper,
        session.remaining,
        session.forms_by_owned,
        session.load,
        session.facts_for,
        sub_slots,
    )
    colors = _colors(
        session.combats,
        session.helper,
        session.remaining,
        session.forms_by_owned,
        session.load,
        session.facts_for,
        sub_slots,
        session.known_owned,
        session.pinned,
    )
    awakening_checks = _awakening_checks(
        [session.load(monster_id) for monster_id in session.known_owned],
        _requirement_attributes(colors),
    )
    session.unnamed.update(awakening_checks.pop("unnamed_ids"))
    holes = _holes(
        session.entry,
        session.available,
        transform,
        session.buckets,
        colors,
        sorted(session.unnamed),
        len(session.unknown_forms),
        session.missing_owned,
        session.unconfirmed,
        session.box.get("complete"),
    )
    return {
        "decoder_version": DECODER_VERSION,
        "skill_index": session.index_status,
        "box": {
            "revision": session.box.get("revision"),
            "complete": bool(session.box.get("complete")),
            "owned_monsters": len(session.owned),
            "owned_copies": sum(session.owned.values()),
            "unconfirmed_copies": session.unconfirmed,
        },
        "leader_id": session.leader_id,
        "helper_id": session.helper_id,
        "leader": _leader_report(
            session.leader_id,
            session.entry,
            session.entry_facts,
            session.combats,
            session.load,
            session.facts_for,
            session.available,
            session.reserved_from,
            session.reserved_path,
            session.spends,
            session.transforms,
        ),
        "helper": _helper_report(session.helper, session.load, session.facts_for)
        if session.helper
        else None,
        "transform": transform,
        "roles": {key: _finish(providers) for key, providers in session.buckets.items()},
        "color_force": colors,
        "awakening_checks": awakening_checks,
        "turn1_haste": _finish(session.haste)["providers"],
        "turn1_haste_total": len(session.haste),
        "holes": holes,
        "notes": _notes(
            session.transforms or bool(session.helper and session.helper["transforms"]),
            any(color["match"] == "cross" for color in colors),
        ),
        "loadout": _loadout(session.entry, session.combats, session.helper, session.load),
    }


def _owned(box: dict[str, Any]) -> tuple[dict[int, int], int]:
    owned: dict[int, int] = {}
    unconfirmed = 0
    for row in box.get("monsters") or []:
        monster_id = int(row["monster_id"])
        quantity = int(row.get("quantity") or 0)
        if quantity < 1:
            continue
        owned[monster_id] = owned.get(monster_id, 0) + quantity
        if row.get("confirmed") is False:
            unconfirmed += quantity
    return owned, unconfirmed


def _graph(db: Any) -> tuple[dict[int, list[tuple[int, str]]], dict[int, list[tuple[int, str]]]]:
    outgoing: dict[int, list[tuple[int, str]]] = {}
    incoming: dict[int, list[tuple[int, str]]] = {}
    for row in db.execute("SELECT source_id, target_id, kind FROM relationship"):
        source, target, kind = int(row["source_id"]), int(row["target_id"]), str(row["kind"])
        outgoing.setdefault(source, []).append((target, kind))
        incoming.setdefault(target, []).append((source, kind))
    return outgoing, incoming


def _load(db: Any, cache: dict[int, Card | None], monster_id: int) -> Card | None:
    if monster_id in cache:
        return cache[monster_id]
    row = db.execute("SELECT id, name, raw FROM monster WHERE id=?", (monster_id,)).fetchone()
    if row is None:
        cache[monster_id] = None
        return None
    skills: dict[str, dict[str, Any]] = {}
    query = """SELECT ms.role AS role, skill.raw AS raw FROM monster_skill ms
        JOIN skill ON skill.id = ms.skill_id WHERE ms.monster_id=?"""
    for item in db.execute(query, (monster_id,)):
        skills[str(item["role"])] = json.loads(item["raw"])
    awakenings: list[int] = []
    supers: list[int] = []
    awakening_query = """SELECT awakening_id, kind FROM monster_awakening
        WHERE monster_id=? ORDER BY position"""
    for item in db.execute(awakening_query, (monster_id,)):
        if item["kind"] == "super":
            supers.append(int(item["awakening_id"]))
        else:
            awakenings.append(int(item["awakening_id"]))
    card = Card(
        monster_id=int(row["id"]),
        name=str(row["name"]),
        raw=json.loads(row["raw"]),
        awakenings=awakenings,
        super_awakenings=supers,
        active=skills.get("active"),
        leader=skills.get("leader"),
    )
    cache[monster_id] = card
    return card


def _version(db: Any, monster_id: int) -> str:
    row = db.execute("SELECT provenance FROM monster WHERE id=?", (monster_id,)).fetchone()
    provenance = json.loads(row["provenance"]) if row else {}
    return str(provenance.get("version") or "")


def _facts(
    card: Card, index: dict[int, dict[str, Any]], cache: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    cached = cache.get(card.monster_id)
    if cached is not None:
        return cached
    active_effects, active = _skill_report(card.active, index)
    leader_effects, leader = _skill_report(card.leader, index)
    cached = {
        "active_effects": active_effects,
        "leader_effects": leader_effects,
        "active": active,
        "leader": leader,
        "active_roles": roles_from(active_effects),
        "leader_roles": roles_from(leader_effects),
    }
    cache[card.monster_id] = cached
    return cached


def _identity(
    monster_id: int,
    incoming: dict[int, list[tuple[int, str]]],
    outgoing: dict[int, list[tuple[int, str]]],
) -> tuple[list[int], list[int], bool]:
    bases = sorted(source for source, kind in incoming.get(monster_id, []) if kind == "transform")
    if bases:
        return bases, [monster_id], True
    combats = sorted(target for target, kind in outgoing.get(monster_id, []) if kind == "transform")
    if combats:
        return [monster_id], combats, True
    return [monster_id], [monster_id], False


def _reachable(
    start_id: int,
    outgoing: dict[int, list[tuple[int, str]]],
    load: Any,
    owned: dict[int, int],
) -> list[dict[str, Any]]:
    best_spends: dict[int, tuple[int, ...]] = {start_id: ()}
    best_path: dict[int, list[str]] = {start_id: []}
    queue: deque[int] = deque([start_id])
    while queue:
        current = queue.popleft()
        path = best_path[current]
        if len(path) >= _MAX_DEPTH:
            continue
        for target, kind in outgoing.get(current, []):
            if kind not in _ENTRY_KINDS or load(target) is None:
                continue
            extra = _material_cost(load(target), load, owned)
            if extra is None:
                continue
            spends = tuple(sorted((*best_spends[current], *extra)))
            if target in best_spends and len(best_spends[target]) <= len(spends):
                continue
            best_spends[target] = spends
            best_path[target] = [*path, kind]
            queue.append(target)
    forms = []
    for form_id, path in best_path.items():
        forms.append(
            {
                "form_id": form_id,
                "path": list(path),
                "spends": list(best_spends[form_id]),
                "stage": "entry",
            }
        )
        for target, kind in outgoing.get(form_id, []):
            if kind == "transform" and load(target) is not None:
                forms.append(
                    {
                        "form_id": target,
                        "path": [*path, "transform"],
                        "spends": list(best_spends[form_id]),
                        "stage": "battle",
                    }
                )
    return forms


def _material_cost(child: Card, load: Any, owned: dict[int, int]) -> tuple[int, ...] | None:
    needed: Counter[int] = Counter()
    for value in child.raw.get("evoMaterials") or []:
        if not value:
            continue
        material_id = int(value)
        material = load(material_id)
        if material is None:
            return None
        if material.raw.get("stackable"):
            continue
        needed[material_id] += 1
    if any(owned.get(material_id, 0) < count for material_id, count in needed.items()):
        return None
    spent: list[int] = []
    for material_id, count in needed.items():
        spent.extend([material_id] * count)
    return tuple(spent)


def _reserve(
    entries: list[int],
    forms_by_owned: dict[int, list[dict[str, Any]]],
    owned: dict[int, int],
) -> tuple[int, int, list[int], list[str]] | None:
    options = []
    for entry_id in entries:
        for owned_id, forms in forms_by_owned.items():
            for form in forms:
                if form["form_id"] != entry_id or form["stage"] != "entry":
                    continue
                if not _payable(owned_id, form["spends"], owned):
                    continue
                options.append(
                    (len(form["spends"]), owned_id != entry_id, owned_id, entry_id, form)
                )
    if not options:
        return None
    _, _, owned_id, entry_id, form = min(options)
    return owned_id, entry_id, list(form["spends"]), list(form["path"])


def _payable(owned_id: int, spends: list[int], owned: dict[int, int]) -> bool:
    need = Counter(spends)
    need[owned_id] += 1
    return all(owned.get(monster_id, 0) >= count for monster_id, count in need.items())


def _count_available(
    entries: list[int],
    forms_by_owned: dict[int, list[dict[str, Any]]],
    owned: dict[int, int],
) -> int:
    options = []
    for entry_id in entries:
        for owned_id, forms in forms_by_owned.items():
            for form in forms:
                if form["form_id"] == entry_id and form["stage"] == "entry":
                    options.append((len(form["spends"]), owned_id, form["spends"]))
    pool = Counter(owned)
    available = 0
    while True:
        paid = False
        for _, owned_id, spends in sorted(options):
            need = Counter(spends)
            need[owned_id] += 1
            if any(pool[monster_id] < count for monster_id, count in need.items()):
                continue
            for monster_id, count in need.items():
                pool[monster_id] -= count
            available += 1
            paid = True
            break
        if not paid:
            return available


def _helper(
    helper_id: int,
    incoming: dict[int, list[tuple[int, str]]],
    outgoing: dict[int, list[tuple[int, str]]],
    load: Any,
) -> dict[str, Any]:
    entries, combats, transforms = _identity(helper_id, incoming, outgoing)
    entry = load(entries[0])
    if entry is None:
        raise ValueError(f"Monster #{entries[0]} is not in the local reference set")
    forms = [{"form_id": entry.monster_id, "path": [], "spends": [], "stage": "entry"}]
    if transforms:
        forms.extend(
            {
                "form_id": combat_id,
                "path": ["transform"],
                "spends": [],
                "stage": "battle",
            }
            for combat_id in combats
            if load(combat_id) is not None
        )
    return {"card": entry, "forms": forms, "combats": combats, "transforms": transforms}


def _collect(
    buckets: dict[str, list[dict[str, Any]]],
    haste: list[dict[str, Any]],
    unnamed: set[int],
    unknown_forms: set[tuple[int, int]],
    source: Card,
    forms: list[dict[str, Any]],
    load: Any,
    facts_for: Any,
    copies: int,
    borrowed: bool,
    pinned: set[tuple[int, int]],
) -> None:
    for form in forms:
        card = load(form["form_id"])
        if card is None:
            continue
        for awakening_id in card.awakenings:
            if awakening_id not in NAMES:
                unnamed.add(awakening_id)
        facts = facts_for(card)
        if not borrowed and facts["active_roles"]["unknown_effects"]:
            unknown_forms.add((source.monster_id, card.monster_id))
        for key, _, predicate in _ROLES:
            summary = _matching(facts["active_effects"], predicate)
            if summary:
                buckets[key].append(
                    _provider(source, form, card, facts, summary, borrowed, copies, pinned)
                )
        charge = _matching(
            facts["active_effects"], lambda effect: effect.kind == "charge_other_skills"
        )
        if charge:
            haste.append(_provider(source, form, card, facts, charge, borrowed, copies, pinned))


def _matching(effects: list[Effect], predicate: Any) -> str:
    parts = [
        effect.summary for effect in effects if effect.evidence == "verified" and predicate(effect)
    ]
    return "; ".join(parts)


def _provider(
    source: Card,
    form: dict[str, Any],
    card: Card,
    facts: dict[str, Any],
    summary: str,
    borrowed: bool,
    copies: int,
    pinned: set[tuple[int, int]],
) -> dict[str, Any]:
    return {
        "owned_id": source.monster_id,
        "owned_name": source.name,
        "copies": copies,
        "form_id": card.monster_id,
        "form_name": card.name,
        "stage": form["stage"],
        "path": form["path"],
        "spends": form["spends"],
        "borrowed": borrowed,
        "minimum_cooldown": facts["active"]["minimum_cooldown"],
        "summary": summary,
        "unknown_effects": facts["active_roles"]["unknown_effects"],
        "pinned": (source.monster_id, card.monster_id) in pinned,
    }


def _transform(
    entry: Card,
    entry_facts: dict[str, Any],
    combats: list[int],
    transforms: bool,
    reserved: bool,
    helper: dict[str, Any] | None,
    remaining: dict[int, int],
    forms_by_owned: dict[int, list[dict[str, Any]]],
    load: Any,
    facts_for: Any,
    sub_slots: int,
) -> dict[str, Any]:
    cards = []
    if transforms:
        cards.append(_transform_card("leader", entry, entry_facts, combats))
    if helper and helper["transforms"]:
        cards.append(
            _transform_card("helper", helper["card"], facts_for(helper["card"]), helper["combats"])
        )
    if not cards:
        return {
            "required_turns": None,
            "cards": [],
            "ceiling": None,
            "note": "This leader does not transform.",
        }
    known = [card["required_turns"] for card in cards]
    decoded = all(turn is not None for turn in known)
    required = max(known) if decoded else None
    helper_boost = skill_boost(helper["card"].awakenings) if helper else 0
    return {
        "required_turns": required,
        "cards": cards,
        "ceiling": _ceiling(
            skill_boost(entry.awakenings),
            skill_boost(entry.awakenings) if reserved else 0,
            helper_boost,
            remaining,
            forms_by_owned,
            load,
            sub_slots,
            required,
            reserved,
            decoded,
        ),
        "note": (
            "The transform skill's own haste charges other skills when it is used. "
            "It does not reduce this cooldown."
        ),
    }


def _transform_card(
    role: str, card: Card, facts: dict[str, Any], combats: list[int]
) -> dict[str, Any]:
    targets = facts["active_roles"]["transform_into"]
    matched = any(combat in targets for combat in combats)
    cooldown = facts["active"]["minimum_cooldown"] if matched else None
    return {
        "role": role,
        "entry_id": card.monster_id,
        "entry_name": card.name,
        "combat_ids": combats,
        "required_turns": cooldown,
        "skill_boost": skill_boost(card.awakenings),
        "haste_on_this_skill": facts["active_roles"]["charge_other_skills_turns"],
    }


def _ceiling(
    leader_boost: int,
    leader_counted: int,
    helper_boost: int,
    remaining: dict[int, int],
    forms_by_owned: dict[int, list[dict[str, Any]]],
    load: Any,
    sub_slots: int,
    required: int | None,
    reserved: bool,
    decoded: bool,
) -> dict[str, Any]:
    options = []
    for owned_id, quantity in remaining.items():
        if quantity < 1:
            continue
        best_boost: int | None = None
        best_card: Card | None = None
        for form in forms_by_owned.get(owned_id, []):
            if form["stage"] != "entry" or form["spends"]:
                continue
            card = load(form["form_id"])
            if card is None:
                continue
            boost = skill_boost(card.awakenings)
            if best_boost is None or boost > best_boost:
                best_boost = boost
                best_card = card
        if best_card is None or best_boost is None or best_boost <= 0:
            continue
        options.append((best_boost, owned_id, quantity, best_card))
    chosen = []
    sub_total = 0
    slots = sub_slots
    for boost, owned_id, quantity, card in sorted(options, key=lambda item: (-item[0], item[1])):
        if slots <= 0:
            break
        take = min(quantity, slots)
        source = load(owned_id)
        chosen.append(
            {
                "owned_id": owned_id,
                "owned_name": source.name if source else "",
                "form_id": card.monster_id,
                "form_name": card.name,
                "skill_boost": boost,
                "copies": take,
            }
        )
        sub_total += boost * take
        slots -= take
    total_boost = leader_counted + helper_boost + sub_total
    return {
        "sub_slots": sub_slots,
        "leader_skill_boost": leader_boost,
        "leader_counted": leader_counted,
        "helper_skill_boost": helper_boost,
        "chosen": chosen,
        "total": total_boost,
        "meets_requirement": bool(
            reserved and decoded and required is not None and total_boost >= required
        ),
        "badge": "excluded",
        "is_team": False,
    }


def _colors(
    combats: list[int],
    helper: dict[str, Any] | None,
    remaining: dict[int, int],
    forms_by_owned: dict[int, list[dict[str, Any]]],
    load: Any,
    facts_for: Any,
    sub_slots: int,
    owned: dict[int, int],
    pinned: set[tuple[int, int]],
) -> list[dict[str, Any]]:
    requirements = _linked(combats, "leader", load, facts_for)
    helper_ids = helper["combats"] if helper else []
    if helper:
        requirements.extend(_linked(helper["combats"], "helper", load, facts_for))
    results = []
    for requirement in requirements:
        attributes = set(requirement["attributes"])
        if not attributes:
            results.append(
                {
                    **requirement,
                    "status": "none",
                    "loop": None,
                    "providers": [],
                    "also_produces": [],
                    "mono_boards": [],
                    "orb_enhance_cards": 0,
                }
            )
            continue
        cross = requirement["match"] == "cross"
        force, produced = _color_rows(
            forms_by_owned, helper, load, facts_for, attributes, owned, pinned, cross
        )
        loop = None
        if not cross:
            loop = _loop(
                combats,
                helper_ids,
                remaining,
                forms_by_owned,
                load,
                facts_for,
                attributes,
                sub_slots,
            )
        force_done = _finish(force)
        produced_done = _finish(produced)
        chosen = produced_done if cross else force_done
        results.append(
            {
                **requirement,
                "status": chosen["status"] if cross else ("available" if loop["fits"] else "hole"),
                "loop": loop,
                "providers": chosen["providers"],
                "providers_total": chosen["total"],
                "providers_truncated": chosen["truncated"],
                "also_produces": [] if cross else produced_done["providers"],
                "also_produces_total": 0 if cross else produced_done["total"],
                "mono_boards": force_done["providers"] if cross else [],
                "orb_enhance_cards": _orb_cards(owned, load, attributes),
            }
        )
    return results


def _linked(combat_ids: list[int], side: str, load: Any, facts_for: Any) -> list[dict[str, Any]]:
    found = []
    for combat_id in combat_ids:
        card = load(combat_id)
        if card is None:
            continue
        facts = facts_for(card)
        linked = [
            effect
            for effect in facts["leader_effects"]
            if effect.evidence == "verified" and effect.kind == "linked_orb_leader"
        ]
        shape = _match_shape(facts)
        if not linked:
            found.append(
                _requirement(
                    side, card, [], None, None, facts["leader_roles"]["unknown_effects"], shape
                )
            )
            continue
        for effect in linked:
            found.append(
                _requirement(
                    side,
                    card,
                    list(effect.details["attributes"]),
                    effect.details["minimum_linked"],
                    effect.summary,
                    facts["leader_roles"]["unknown_effects"],
                    shape,
                )
            )
    return found


def _match_shape(facts: dict[str, Any]) -> str:
    for effect in facts["leader_effects"]:
        if (
            effect.evidence == "verified"
            and effect.kind == "awakening_trigger"
            and effect.details.get("trigger") == "cross_attack"
        ):
            return "cross"
    return "linked"


def _requirement(
    side: str,
    card: Card,
    attributes: list[str],
    minimum: int | None,
    summary: str | None,
    leader_unknown: int,
    match: str,
) -> dict[str, Any]:
    return {
        "side": side,
        "form_id": card.monster_id,
        "form_name": card.name,
        "attributes": attributes,
        "minimum_linked": minimum,
        "summary": summary,
        "leader_unknown": leader_unknown,
        "match": match,
    }


def _color_rows(
    forms_by_owned: dict[int, list[dict[str, Any]]],
    helper: dict[str, Any] | None,
    load: Any,
    facts_for: Any,
    attributes: set[str],
    owned: dict[int, int],
    pinned: set[tuple[int, int]],
    for_cross: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    force: list[dict[str, Any]] = []
    produced: list[dict[str, Any]] = []
    groups: list[tuple[Card, list[dict[str, Any]], int, bool]] = [
        (load(owned_id), forms, quantity, False)
        for owned_id, quantity in owned.items()
        if load(owned_id) is not None
        for forms in [forms_by_owned.get(owned_id, [])]
    ]
    if helper:
        groups.append((helper["card"], helper["forms"], 1, True))
    for source, forms, copies, borrowed in groups:
        if source is None:
            continue
        for form in forms:
            card = load(form["form_id"])
            if card is None:
                continue
            facts = facts_for(card)
            forced = _force_summary(facts, attributes)
            if forced:
                force.append(_provider(source, form, card, facts, forced, borrowed, copies, pinned))
            made = _produce_summary(facts, attributes, for_cross)
            if made:
                produced.append(
                    _provider(source, form, card, facts, made, borrowed, copies, pinned)
                )
    return force, produced


def _force_summary(facts: dict[str, Any], attributes: set[str]) -> str:
    parts = []
    for effect in facts["active_effects"]:
        if effect.evidence != "verified" or effect.kind != "board_change":
            continue
        orbs = list(effect.details.get("orbs") or [])
        if len(orbs) == 1 and orbs[0] in attributes:
            parts.append(effect.summary)
    return "; ".join(parts)


def _produce_summary(
    facts: dict[str, Any], attributes: set[str], for_cross: bool = False
) -> str:
    if for_cross and _force_summary(facts, attributes):
        return ""
    parts = []
    for effect in facts["active_effects"]:
        if effect.evidence != "verified":
            continue
        if effect.kind == "board_change":
            orbs = list(effect.details.get("orbs") or [])
            if len(orbs) == 1 and orbs[0] in attributes:
                continue
            if any(orb in attributes for orb in orbs):
                parts.append(effect.summary)
        elif effect.kind == "create_orbs" and any(
            name in attributes for name in effect.details.get("attributes") or []
        ):
            parts.append(effect.summary)
        elif effect.kind in {
            "orb_convert",
            "orb_replace",
            "orb_change",
            "column_change",
            "row_change",
            "skyfall",
        } and any(name in attributes for name in effect.details.get("to") or []):
            if _mono_skyfall(effect, attributes, for_cross):
                continue
            parts.append(effect.summary)
    return "; ".join(parts)


def _mono_skyfall(effect: Effect, attributes: set[str], for_cross: bool) -> bool:
    if not for_cross or effect.kind != "skyfall" or effect.details.get("mode") != "only":
        return False
    orbs = set(effect.details.get("to") or [])
    return bool(orbs) and orbs <= attributes


def _loop(
    leader_ids: list[int],
    helper_ids: list[int],
    remaining: dict[int, int],
    forms_by_owned: dict[int, list[dict[str, Any]]],
    load: Any,
    facts_for: Any,
    attributes: set[str],
    sub_slots: int,
) -> dict[str, Any]:
    uptime = 0.0
    used = []
    for slot, card_ids in (("leader", leader_ids), ("helper", helper_ids)):
        cooldown, card = _best_force(card_ids, load, facts_for, attributes)
        if cooldown is None or card is None:
            continue
        uptime += 1 / cooldown
        used.append(
            {
                "slot": slot,
                "owned_id": card.monster_id,
                "form_id": card.monster_id,
                "name": card.name,
                "copies": 1,
                "minimum_cooldown": cooldown,
            }
        )
    options = []
    for owned_id, quantity in remaining.items():
        if quantity < 1:
            continue
        best: int | None = None
        best_card: Card | None = None
        for form in forms_by_owned.get(owned_id, []):
            if form["stage"] != "entry" or form["spends"]:
                continue
            card = load(form["form_id"])
            if card is None or not _force_summary(facts_for(card), attributes):
                continue
            cooldown = facts_for(card)["active"]["minimum_cooldown"]
            if not isinstance(cooldown, int) or cooldown <= 0:
                continue
            if best is None or cooldown < best:
                best = cooldown
                best_card = card
        if best is not None and best_card is not None:
            options.append((best, owned_id, quantity, best_card))
    slots = sub_slots
    for cooldown, owned_id, quantity, card in sorted(options):
        if uptime >= 1 or slots <= 0:
            break
        take = min(quantity, slots)
        uptime += take / cooldown
        slots -= take
        source = load(owned_id)
        used.append(
            {
                "slot": "sub",
                "owned_id": owned_id,
                "form_id": card.monster_id,
                "name": source.name if source else card.name,
                "copies": take,
                "minimum_cooldown": cooldown,
            }
        )
    return {"fits": uptime >= 1 - 1e-9, "uptime": round(uptime, 3), "used": used}


def _best_force(
    card_ids: list[int], load: Any, facts_for: Any, attributes: set[str]
) -> tuple[int | None, Card | None]:
    best: int | None = None
    best_card: Card | None = None
    for card_id in card_ids:
        card = load(card_id)
        if card is None or not _force_summary(facts_for(card), attributes):
            continue
        cooldown = facts_for(card)["active"]["minimum_cooldown"]
        if not isinstance(cooldown, int) or cooldown <= 0:
            continue
        if best is None or cooldown < best:
            best = cooldown
            best_card = card
    return best, best_card


def _orb_cards(owned: dict[int, int], load: Any, attributes: set[str]) -> int:
    count = 0
    for owned_id in owned:
        card = load(owned_id)
        if card is None:
            continue
        if any(has_value(card.awakenings, "orb_enhance", attribute) for attribute in attributes):
            count += 1
    return count


def _awakening_checks(cards: list[Card | None], attributes: set[str]) -> dict[str, Any]:
    unnamed: set[int] = set()
    bind_100 = skill_bind_100 = tape = cloud = team_hp = team_rcv = 0
    l_unlock = void_awakenings = 0
    orb_enhance = 0
    for card in cards:
        if card is None:
            continue
        identifiers = card.awakenings
        unnamed.update(awakening_id for awakening_id in identifiers if awakening_id not in NAMES)
        if capped(identifiers, "bind_resist_percent") >= 100:
            bind_100 += 1
        if capped(identifiers, "skill_bind_resist_percent") >= 100:
            skill_bind_100 += 1
        tape += int(total(identifiers, "tape_resist") > 0)
        cloud += int(total(identifiers, "cloud_resist") > 0)
        team_hp += int(total(identifiers, "team_hp") > 0)
        team_rcv += int(total(identifiers, "team_rcv") > 0)
        l_unlock += int(total(identifiers, "l_unlock") >= 2)
        void_awakenings += int(total(identifiers, "void_pierce_awakening") > 0)
        if any(has_value(identifiers, "orb_enhance", attribute) for attribute in attributes):
            orb_enhance += 1
    return {
        "note": (
            "Counts use the owned card before evolution. Super awakenings are not included. "
            "Tape, cloud, team HP, and team RCV have no percent in this snapshot. "
            "Damage void piercer awakenings are match-based and do not fill the active "
            "void pierce hole."
        ),
        "bind_resist_100": bind_100,
        "skill_bind_resist_100": skill_bind_100,
        "tape": tape,
        "cloud": cloud,
        "team_hp": team_hp,
        "team_rcv": team_rcv,
        "l_unlock_at_least_2": l_unlock,
        "void_pierce_awakening": void_awakenings,
        "leader_color_orb_enhance": orb_enhance,
        "unnamed_ids": unnamed,
    }


def _requirement_attributes(colors: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for color in colors:
        names.update(color["attributes"])
    return names


def _leader_report(
    leader_id: int,
    entry: Card,
    entry_facts: dict[str, Any],
    combats: list[int],
    load: Any,
    facts_for: Any,
    available: int,
    reserved_from: int | None,
    reserved_path: list[str],
    spends: list[int],
    transforms: bool,
) -> dict[str, Any]:
    after = []
    if transforms:
        for combat_id in combats:
            card = load(combat_id)
            if card is None:
                continue
            facts = facts_for(card)
            after.append(
                {
                    "monster_id": card.monster_id,
                    "name": card.name,
                    "active": _brief(facts["active"], facts["active_effects"]),
                    "leader": _brief(facts["leader"], facts["leader_effects"]),
                }
            )
    return {
        "named_id": leader_id,
        "entry_id": entry.monster_id,
        "entry_name": entry.name,
        "combat_ids": combats,
        "transforms": transforms,
        "available_copies": available,
        "reserved_from": reserved_from,
        "reserved_path": reserved_path,
        "spends": spends,
        "skill_boost": skill_boost(entry.awakenings),
        "awakenings": [describe(awakening_id) for awakening_id in entry.awakenings],
        "before_transform": {
            "active": _brief(entry_facts["active"], entry_facts["active_effects"]),
            "leader": _brief(entry_facts["leader"], entry_facts["leader_effects"]),
        },
        "after_transform": after,
    }


def _helper_report(helper: dict[str, Any], load: Any, facts_for: Any) -> dict[str, Any]:
    card = helper["card"]
    facts = facts_for(card)
    return {
        "borrowed": True,
        "entry_id": card.monster_id,
        "entry_name": card.name,
        "combat_ids": helper["combats"],
        "transforms": helper["transforms"],
        "skill_boost": skill_boost(card.awakenings),
        "active": _brief(facts["active"], facts["active_effects"]),
        "leader": _brief(facts["leader"], facts["leader_effects"]),
        "after_transform": [
            {
                "monster_id": combat.monster_id,
                "name": combat.name,
                "leader": _brief(combat_facts["leader"], combat_facts["leader_effects"]),
            }
            for combat_id in helper["combats"]
            if helper["transforms"] and (combat := load(combat_id)) is not None
            for combat_facts in [facts_for(combat)]
        ],
    }


def _brief(report: dict[str, Any], effects: list[Effect]) -> dict[str, Any]:
    return {
        "name": report.get("name") or "",
        "minimum_cooldown": report["minimum_cooldown"],
        "unknown_effects": sum(effect.evidence != "verified" for effect in effects),
        "summaries": [effect.summary for effect in effects if effect.evidence == "verified"],
    }


def _finish(providers: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(providers, key=_sort_key)
    return {
        "status": _status(ordered),
        "total": len(ordered),
        "providers": ordered[:_PROVIDER_LIMIT],
        "truncated": max(0, len(ordered) - _PROVIDER_LIMIT),
    }


def _status(providers: list[dict[str, Any]]) -> str:
    if any(provider["unknown_effects"] == 0 for provider in providers):
        return "available"
    if providers:
        return "partial"
    return "hole"


def _sort_key(provider: dict[str, Any]) -> tuple[bool, bool, int, int, int]:
    cooldown = provider["minimum_cooldown"]
    return (
        not provider.get("pinned", False),
        cooldown is None,
        cooldown or 0,
        provider["form_id"],
        provider["owned_id"] or 0,
    )


def _holes(
    entry: Card,
    available: int,
    transform: dict[str, Any],
    buckets: dict[str, list[dict[str, Any]]],
    colors: list[dict[str, Any]],
    unnamed: list[int],
    unknown_forms: int,
    missing_owned: list[int],
    unconfirmed: int,
    complete: object,
) -> list[str]:
    holes = []
    if available < 1:
        holes.append(
            f"Entry form #{entry.monster_id} ({entry.name}) is not reachable from the applied box."
        )
    if complete is False:
        holes.append("The applied box is not marked complete.")
    if unconfirmed:
        holes.append(f"{unconfirmed} owned copies are unconfirmed.")
    if missing_owned:
        holes.append(f"{len(missing_owned)} owned monster IDs are not in the local reference set.")
    for card in transform["cards"]:
        if card["required_turns"] is None:
            targets = ", ".join(f"#{combat_id}" for combat_id in card["combat_ids"])
            holes.append(
                f"{card['role'].title()} entry #{card['entry_id']} has no verified "
                f"transform into {targets}."
            )
    ceiling = transform["ceiling"]
    if ceiling and transform["required_turns"] is not None and not ceiling["meets_requirement"]:
        holes.append(
            f"Skill boost on awakenings reaches {ceiling['total']}, short of the "
            f"{transform['required_turns']}-turn transform. This ceiling is not a team, "
            "and the badge is not included."
        )
    for key, label, _ in _ROLES:
        state = _status(buckets[key])
        if state == "hole":
            holes.append(f"No verified {label}.")
        elif state == "partial":
            holes.append(f"{label.title()} only appears on actives that also have unknown effects.")
    for color in colors:
        attrs = "/".join(color["attributes"])
        if color["status"] == "hole" and color["match"] == "cross":
            holes.append(
                f"No active makes {attrs} orbs that can stay a cross for "
                f"#{color['form_id']} ({color['minimum_linked']}+ linked). "
                "A full board of that color clears as one match and cannot be a cross."
            )
        elif color["status"] == "partial" and color["match"] == "cross":
            holes.append(
                f"{attrs.capitalize()} orbs for a cross on #{color['form_id']} only appear "
                "on actives that also have unknown effects."
            )
        elif color["status"] == "hole":
            holes.append(
                f"No active keeps a full board of {attrs} up often enough for "
                f"#{color['form_id']} ({color['minimum_linked']}+ linked). "
                "Creating or changing some orbs does not close this."
            )
        elif color["status"] == "none" and color["leader_unknown"]:
            holes.append(
                f"#{color['form_id']} has unknown leader-skill effects and no decoded "
                "linked-orb condition."
            )
    if unnamed:
        listed = ", ".join(str(awakening_id) for awakening_id in unnamed)
        holes.append(f"Awakening IDs on reachable forms have no name: {listed}.")
    if unknown_forms:
        holes.append(f"{unknown_forms} reachable actives still have unknown skill effects.")
    return holes


def _notes(has_transform: bool, cross: bool) -> list[str]:
    notes = [
        (
            "Each owned copy can become one form. An assist evolution spends that copy, "
            "so it cannot also be a sub."
        ),
        (
            "The skill-boost ceiling adds the leader, a borrowed helper when one was named, "
            "and the other owned cards with the most skill boost. It is not a team. "
            "The team badge is not included."
        ),
        (
            "Haste on a skill charges other skills when that skill is used. "
            "It does not reduce its own cooldown, and it is not added to the ceiling."
        ),
        (
            "A transformed sub's active is listed on roles and is not in the color-loop uptime. "
            "That transform has its own skill-boost cost."
        ),
        (
            "The leader and helper slots can use their transformed actives, because those "
            "are the forms whose match conditions are being checked."
        ),
        (
            (
                "A cross is exactly five orbs in a plus. A full board of the linked color "
                "clears as one match, so it cannot be that cross. Actives that make some of "
                "those orbs and leave other colors are the color entries."
            )
            if cross
            else (
                "A full board of one required color can be matched into the linked orbs. "
                "Creating orbs at random, changing some orbs to that color, or a board "
                "change that mixes colors, does not guarantee the link."
            )
        ),
        (
            "Farmable materials are the ones marked stackable. A non-stackable material "
            "is spent from the box when you own it. Forms that spend one are listed and "
            "are not added to the skill-boost ceiling."
        ),
        "Super awakenings are a menu. None is selected. Latents are not selected.",
        "Each match condition is checked on its own. One sub cannot fill two loops at once.",
    ]
    if has_transform:
        notes.append("The transform skill's own haste does not reduce that skill's cooldown.")
    return notes


def _loadout(
    entry: Card, combats: list[int], helper: dict[str, Any] | None, load: Any
) -> dict[str, Any]:
    sync_id = entry.raw.get("syncAwakening")
    named = describe(int(sync_id))["name"] if isinstance(sync_id, int) and sync_id else None
    combat_supers = {}
    for combat_id in combats:
        card = load(combat_id)
        if card is None or card.monster_id == entry.monster_id:
            continue
        combat_supers[str(combat_id)] = [
            describe(awakening_id) for awakening_id in card.super_awakenings
        ]
    return {
        "super_awakenings": {
            "leader_entry": [describe(awakening_id) for awakening_id in entry.super_awakenings],
            "combat": combat_supers,
            "helper_entry": (
                [describe(awakening_id) for awakening_id in helper["card"].super_awakenings]
                if helper
                else []
            ),
        },
        "latents": "not selected",
        "sync_awakening": {
            "id": sync_id,
            "name": named,
            "conditions": entry.raw.get("syncAwakeningConditions") or [],
            "note": "Not assumed to be met.",
        },
    }
