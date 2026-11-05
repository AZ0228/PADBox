"""Candidate pool for one leader, and a check of a team chosen from it.

The pool lists owned forms beside the evolutions and assists those copies can
become. It does not assign slots or pick a best team. `check_team` reports
copy conflicts, unreachable forms, open holes, and unknown effects.
"""

from typing import Any

from padbox.database.store import Store
from padbox.mechanics.awakenings import describe, skill_boost
from padbox.mechanics.coverage import (
    _ROLES,
    BoxSession,
    Card,
    _cover_report,
    _force_summary,
    _produce_summary,
    leader_context,
)
from padbox.mechanics.decode import ATTRIBUTES, DECODER_VERSION

_INVESTMENT = {"evolution", "assist_evolution"}
_AWOKEN_ASSIST = 49
_BUCKET_LIMIT = 8
_ASSIST_LIMIT = 4


def pool_box(
    store: Store,
    leader_id: int,
    helper_id: int | None = None,
    skill_index: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    with leader_context(store, leader_id, helper_id, skill_index) as session:
        cover = _cover_report(session)
        requirements = _requirements(cover)
        candidates = _candidates(session, cover)
        omitted = _omitted(session, candidates)
        unknown_actives = len(session.unknown_forms)
    return {
        "decoder_version": DECODER_VERSION,
        "skill_index": cover["skill_index"],
        "box": cover["box"],
        "leader_id": leader_id,
        "helper_id": helper_id,
        "leader": cover["leader"],
        "helper": cover["helper"],
        "requirements": requirements,
        "holes": cover["holes"],
        "contract": _contract(requirements),
        "team_file": _team_file_shape(),
        "candidates": candidates,
        "candidate_copies": sum(1 for row in candidates if not row["borrowed"]),
        "candidate_forms": sum(len(row["forms"]) for row in candidates),
        "omitted_copies": omitted,
        "unknown_active_count": unknown_actives,
    }


def shortlist_box(
    store: Store,
    leader_id: int,
    helper_id: int | None = None,
    skill_index: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Best forms for each requirement, with a few Awoken Assist options on each."""
    pool = pool_box(store, leader_id, helper_id, skill_index)
    flat = _flat_forms(pool)
    specs = _bucket_specs(pool)
    relevant = {key for key, _, _, _ in specs}
    buckets = []
    for key, label, mode, answers in specs:
        rows = [
            form
            for form in flat
            if not form["borrowed"] and form["can_damage"] and _matches_bucket(form, answers)
        ]
        chosen = _take(rows, mode)
        buckets.append(
            {
                "key": key,
                "label": label,
                "candidates": [
                    _short_row(form, flat, specs, relevant, key) for form in chosen
                ],
            }
        )
    return {
        "decoder_version": pool["decoder_version"],
        "skill_index": pool["skill_index"],
        "box": pool["box"],
        "leader_id": pool["leader_id"],
        "helper_id": pool["helper_id"],
        "leader": pool["leader"],
        "requirements": pool["requirements"],
        "holes": pool["holes"],
        "unknown_active_count": pool["unknown_active_count"],
        "bucket_limit": _BUCKET_LIMIT,
        "assist_limit": _ASSIST_LIMIT,
        "buckets": buckets,
        "contract": _shortlist_contract(pool["requirements"]),
        "team_file": pool["team_file"],
    }


def check_team(
    store: Store,
    leader_id: int,
    team: dict[str, Any],
    skill_index: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    helper_id = _optional_int(team.get("helper_id"), "helper_id")
    subs = _subs(team.get("subs", []))
    leader_assist = _pair(team.get("leader_assist"), "leader_assist")
    helper_assist = _pair(team.get("helper_assist"), "helper_assist")
    with leader_context(store, leader_id, helper_id, skill_index) as session:
        cover = _cover_report(session)
        return _check(
            session,
            cover,
            subs,
            leader_assist,
            helper_assist,
        )


def _candidates(session: BoxSession, cover: dict[str, Any]) -> list[dict[str, Any]]:
    attributes = _color_attributes(cover)
    cross_attributes = _cross_attributes(cover)
    needs_boost = _needs_boost(session)
    rows = []
    for owned_id, forms in session.forms_by_owned.items():
        source = session.load(owned_id)
        if source is None:
            continue
        built = [
            _form_row(session, source, form, attributes, cross_attributes, False) for form in forms
        ]
        relevant = owned_id == session.reserved_from or any(
            _relevant(form, needs_boost) for form in built
        )
        if not relevant:
            continue
        rows.append(
            _group(
                source,
                session.known_owned[owned_id],
                owned_id == session.reserved_from,
                False,
                built,
            )
        )
    if session.helper:
        card = session.helper["card"]
        built = [
            _form_row(session, card, form, attributes, cross_attributes, True)
            for form in session.helper["forms"]
        ]
        rows.append(_group(card, 0, False, True, built))
    rows.sort(key=lambda row: (not row["leader_copies"], not row["borrowed"], row["owned_id"]))
    return rows


def _group(
    source: Card, copies: int, leader: bool, borrowed: bool, forms: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "owned_id": source.monster_id,
        "owned_name": source.name,
        "copies": copies,
        "leader_copies": 1 if leader else 0,
        "borrowed": borrowed,
        "forms": sorted(forms, key=_form_order),
    }


def _form_row(
    session: BoxSession,
    source: Card,
    form: dict[str, Any],
    attributes: set[str],
    cross_attributes: set[str],
    borrowed: bool,
) -> dict[str, Any]:
    card = session.load(form["form_id"])
    if card is None:
        raise ValueError(f"Monster #{form['form_id']} is not in the local reference set")
    facts = session.facts_for(card)
    path = list(form["path"])
    boost = skill_boost(card.awakenings)
    answers = _answers(facts, attributes, cross_attributes, boost)
    spends = [
        {
            "monster_id": material_id,
            "name": material.name if (material := session.load(material_id)) else None,
        }
        for material_id in form["spends"]
    ]
    row = {
        "form_id": card.monster_id,
        "form_name": card.name,
        "availability": "reachable" if any(step in _INVESTMENT for step in path) else "in_box",
        "stage": form["stage"],
        "path": path,
        "spends": spends,
        "equip": _is_equip(card),
        "main_attribute": _main_attribute(card),
        "can_damage": _can_damage(card, attributes),
        "skill_boost": boost,
        "latent_slots": 8 if card.raw.get("is8Latent") else 6,
        "awakenings": [describe(awakening_id) for awakening_id in card.awakenings],
        "super_awakenings": [describe(awakening_id) for awakening_id in card.super_awakenings],
        "active": _active_view(facts),
        "answers": answers,
        "owned_id": source.monster_id,
        "borrowed": borrowed,
    }
    if borrowed or source.monster_id == session.reserved_from:
        row["leader_effects"] = _verified_view(facts["leader_effects"])
    return row


def _answers(
    facts: dict[str, Any], attributes: set[str], cross_attributes: set[str], boost: int
) -> list[str]:
    effects = facts["active_effects"]
    answers = [
        key
        for key, _, predicate in _ROLES
        if any(effect.evidence == "verified" and predicate(effect) for effect in effects)
    ]
    for attribute in sorted(attributes):
        if attribute in cross_attributes:
            if _produce_summary(facts, {attribute}, for_cross=True):
                answers.append(f"{attribute}_orbs")
            continue
        if _force_summary(facts, {attribute}):
            answers.append(f"{attribute}_board")
        elif _produce_summary(facts, {attribute}):
            answers.append(f"{attribute}_orbs")
    if any(
        effect.evidence == "verified" and effect.kind == "charge_other_skills" for effect in effects
    ):
        answers.append("haste")
    if boost > 0:
        answers.append("skill_boost")
    return answers


def _relevant(form: dict[str, Any], needs_boost: bool) -> bool:
    answers = [answer for answer in form["answers"] if answer != "skill_boost"]
    if needs_boost:
        return bool(answers) or form["skill_boost"] > 0
    return bool(answers)


def _active_view(facts: dict[str, Any]) -> dict[str, Any]:
    report = facts["active"]
    return {
        "name": report.get("name") or "",
        "base_cooldown": report.get("base_cooldown"),
        "minimum_cooldown": report.get("minimum_cooldown"),
        "effects": _verified_view(facts["active_effects"]),
        "unknown_effects": _unknown_view(facts["active_effects"]),
    }


def _verified_view(effects: list[Any]) -> list[dict[str, Any]]:
    return [
        {"kind": effect.kind, "summary": effect.summary, "details": effect.details}
        for effect in effects
        if effect.evidence == "verified"
    ]


def _unknown_view(effects: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "source_type": effect.source_type,
            "summary": effect.summary,
            "params": list(effect.details.get("params") or []),
        }
        for effect in effects
        if effect.evidence != "verified"
    ]


def _form_order(form: dict[str, Any]) -> tuple[bool, bool, int]:
    return (form["availability"] != "in_box", form["stage"] != "entry", form["form_id"])


def _requirements(cover: dict[str, Any]) -> dict[str, Any]:
    transform = cover["transform"]
    return {
        "sub_slots": 4 if cover["helper"] else 5,
        "transform_turns": transform["required_turns"],
        "roles": {key: role["status"] for key, role in cover["roles"].items()},
        "colors": [
            {
                "form_id": color["form_id"],
                "form_name": color["form_name"],
                "attributes": color["attributes"],
                "minimum_linked": color["minimum_linked"],
                "match": color["match"],
                "status": color["status"],
                "summary": color["summary"],
            }
            for color in cover["color_force"]
        ],
    }


def _needs_boost(session: BoxSession) -> bool:
    if session.transforms:
        return True
    return bool(session.helper and session.helper["transforms"])


def _omitted(session: BoxSession, candidates: list[dict[str, Any]]) -> int:
    included = {row["owned_id"] for row in candidates if not row["borrowed"]}
    return len(set(session.known_owned) - included)


def _contract(requirements: dict[str, Any]) -> list[str]:
    return [
        "Choose only forms listed here. A form this pool does not list is not a choice.",
        "Forms under one owned_id are alternatives for that copy. One copy becomes one form.",
        "leader_copies are reserved for the leader. Remaining copies of that card can be used.",
        (
            "Only a form with Awoken Assist can be equipped. That equip spends its own copy. "
            "Pair it with another card. This pool does not pair them."
        ),
        (
            "A reachable form can be made from the owned card. "
            "Keep the in_box form when the upgrade is small."
        ),
        "Farmable materials are not spent. A monster listed under spends is consumed.",
        _color_contract(requirements, shortlist=False),
        _damage_contract(requirements),
        "Unknown effects stay unresolved. Do not treat them as empty.",
        "Super awakenings are a menu. Latents are not selected. Neither is checked.",
        (
            "Name a battle form to use its active. "
            "That choice also counts the transform active that reaches it."
        ),
        (
            "Haste on a transform skill does not reduce that skill's cooldown. "
            "The team badge is excluded."
        ),
        "This pool does not assign slots or name a best team.",
    ]


def _team_file_shape() -> dict[str, Any]:
    return {
        "helper_id": None,
        "leader_assist": None,
        "helper_assist": None,
        "subs": [{"owned_id": 0, "form_id": 0, "assist": None}],
    }


def _check(
    session: BoxSession,
    cover: dict[str, Any],
    subs: list[dict[str, Any]],
    leader_assist: tuple[int, int] | None,
    helper_assist: tuple[int, int] | None,
) -> dict[str, Any]:
    problems: list[str] = []
    sub_slots = 4 if session.helper else 5
    if len(subs) > sub_slots:
        problems.append(f"This leader has {sub_slots} sub slots and the team lists {len(subs)}.")
    if session.reserved_from is None:
        problems.append("No owned copy can become this leader.")
    selections = []
    for index, sub in enumerate(subs, start=1):
        selections.append(
            _resolve(session, sub["owned_id"], sub["form_id"], f"Sub {index}", problems)
        )
        if sub["assist"] is not None:
            owned_id, form_id = sub["assist"]
            selections.append(
                _resolve(session, owned_id, form_id, f"Sub {index} assist", problems, equip=True)
            )
    if leader_assist is not None:
        selections.append(
            _resolve(
                session, leader_assist[0], leader_assist[1], "Leader assist", problems, equip=True
            )
        )
    if helper_assist is not None:
        if session.helper is None:
            problems.append("A helper assist was named without a helper.")
        selections.append(
            _resolve(
                session, helper_assist[0], helper_assist[1], "Helper assist", problems, equip=True
            )
        )
    problems.extend(_copy_problems(session, selections))
    problems.extend(_damage_problems(session, cover, selections))
    actives = _chosen_actives(session, selections)
    open_holes = _open_holes(session, cover, selections, actives)
    return {
        "decoder_version": DECODER_VERSION,
        "leader_id": session.leader_id,
        "helper_id": session.helper_id,
        "ok": not problems and not open_holes,
        "problems": problems,
        "open_holes": open_holes,
        "unknown_effects": _chosen_unknowns(actives),
        "investments": _investments(selections),
        "skill_boost": _boost_report(session, cover, selections),
    }


def _resolve(
    session: BoxSession,
    owned_id: int,
    form_id: int,
    label: str,
    problems: list[str],
    equip: bool = False,
) -> dict[str, Any] | None:
    if owned_id not in session.forms_by_owned:
        name = _name(session, owned_id)
        problems.append(f"{label}: #{owned_id} {name} is not in the applied box.")
        return None
    form = next(
        (item for item in session.forms_by_owned[owned_id] if item["form_id"] == form_id),
        None,
    )
    if form is None:
        problems.append(f"{label}: #{form_id} is not a reachable form of owned #{owned_id}.")
        return None
    card = session.load(form_id)
    if card is None:
        problems.append(f"{label}: #{form_id} is not in the local reference set.")
        return None
    if equip and not _is_equip(card):
        problems.append(f"{label}: #{form_id} {card.name} is not an assist equip.")
    return {"label": label, "owned_id": owned_id, "form": form, "card": card, "equip": equip}


def _damage_problems(
    session: BoxSession, cover: dict[str, Any], selections: list[dict[str, Any] | None]
) -> list[str]:
    required = _damage_attributes(cover)
    problems = []
    if session.helper is not None:
        problems.extend(_damage_problem("Helper", session.helper["card"], required))
    for selection in selections:
        if selection is None or selection["equip"]:
            continue
        problems.extend(_damage_problem(selection["label"], selection["card"], required))
    return problems


def _damage_problem(label: str, card: Card, required: set[str]) -> list[str]:
    if _can_damage(card, required):
        return []
    attrs = "/".join(sorted(required))
    main = _main_attribute(card) or "untyped"
    if main not in required:
        reason = f"is {main}, and a sub has to be {attrs} to deal damage on that match"
    else:
        reason = f"is {attrs} and has no awakenings, so it does not deal damage on that match"
    return [f"{label}: #{card.monster_id} {card.name} {reason}."]


def _copy_problems(session: BoxSession, selections: list[dict[str, Any] | None]) -> list[str]:
    demand: dict[int, int] = {}
    if session.reserved_from is not None:
        _add(demand, session.reserved_from, 1)
        for material_id in session.spends:
            _add(demand, material_id, 1)
    for selection in selections:
        if selection is None:
            continue
        _add(demand, selection["owned_id"], 1)
        for material_id in selection["form"]["spends"]:
            _add(demand, material_id, 1)
    problems = []
    for monster_id, count in sorted(demand.items()):
        have = session.known_owned.get(monster_id, 0)
        if count > have:
            problems.append(
                f"#{monster_id} {_name(session, monster_id)} is used {count} times "
                f"and the box has {have}."
            )
    return problems


def _chosen_actives(
    session: BoxSession, selections: list[dict[str, Any] | None]
) -> list[dict[str, Any]]:
    actives = [_active_source("Leader", session.entry, session.entry_facts)]
    if session.transforms:
        for combat_id in session.combats:
            card = session.load(combat_id)
            if card is not None:
                actives.append(_active_source("Leader", card, session.facts_for(card)))
    if session.helper:
        card = session.helper["card"]
        actives.append(_active_source("Helper", card, session.facts_for(card)))
        if session.helper["transforms"]:
            for combat_id in session.helper["combats"]:
                combat = session.load(combat_id)
                if combat is not None:
                    actives.append(_active_source("Helper", combat, session.facts_for(combat)))
    for selection in selections:
        if selection is None:
            continue
        card = selection["card"]
        actives.append(_active_source(selection["label"], card, session.facts_for(card)))
        entry = _dungeon_entry(session, selection)
        if entry is not None and entry.monster_id != card.monster_id:
            actives.append(_active_source(selection["label"], entry, session.facts_for(entry)))
    return actives


def _active_source(label: str, card: Card, facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": label,
        "form_id": card.monster_id,
        "form_name": card.name,
        "facts": facts,
        "unknown": _unknown_view(facts["active_effects"]),
    }


def _dungeon_entry(session: BoxSession, selection: dict[str, Any]) -> Card | None:
    form = selection["form"]
    if form["stage"] != "battle":
        return selection["card"]
    prefix = list(form["path"][:-1])
    for other in session.forms_by_owned.get(selection["owned_id"], []):
        if other["stage"] == "entry" and other["path"] == prefix:
            return session.load(other["form_id"])
    return selection["card"]


def _open_holes(
    session: BoxSession,
    cover: dict[str, Any],
    selections: list[dict[str, Any] | None],
    actives: list[dict[str, Any]],
) -> list[str]:
    holes = []
    for _key, label, predicate in _ROLES:
        clean = _role_clean(actives, predicate)
        if clean:
            continue
        if _role_any(actives, predicate):
            holes.append(f"{label.title()} is only on chosen cards that also have unknown effects.")
        else:
            holes.append(f"No chosen card has a verified {label}.")
    for color in cover["color_force"]:
        attributes = set(color["attributes"])
        if not attributes:
            continue
        cross = color.get("match") == "cross"
        if _color_clean(actives, attributes, cross):
            continue
        attrs = "/".join(color["attributes"])
        if cross:
            if _color_any(actives, attributes, True):
                holes.append(
                    f"{attrs.capitalize()} orbs for a cross on #{color['form_id']} are only "
                    "on chosen cards that also have unknown effects."
                )
            else:
                holes.append(
                    f"No chosen card makes {attrs} orbs that can stay a cross for "
                    f"#{color['form_id']} ({color['minimum_linked']}+ linked). "
                    "A full board of that color clears as one match and cannot be a cross."
                )
            continue
        if _color_any(actives, attributes, False):
            holes.append(
                f"A full board of {attrs} for #{color['form_id']} is only on chosen cards "
                "that also have unknown effects."
            )
        else:
            holes.append(
                f"No chosen card keeps a full board of {attrs} up for "
                f"#{color['form_id']} ({color['minimum_linked']}+ linked)."
            )
    boost = _boost_report(session, cover, selections)
    if boost["required"] is not None and not boost["meets"]:
        holes.append(
            f"Chosen skill boost is {boost['total']}, short of the "
            f"{boost['required']}-turn transform."
        )
    return holes


def _role_clean(actives: list[dict[str, Any]], predicate: Any) -> bool:
    return any(not item["unknown"] and _matches(item["facts"], predicate) for item in actives)


def _role_any(actives: list[dict[str, Any]], predicate: Any) -> bool:
    return any(_matches(item["facts"], predicate) for item in actives)


def _matches(facts: dict[str, Any], predicate: Any) -> bool:
    return any(
        effect.evidence == "verified" and predicate(effect) for effect in facts["active_effects"]
    )


def _color_clean(actives: list[dict[str, Any]], attributes: set[str], cross: bool) -> bool:
    return any(
        not item["unknown"] and _color_summary(item["facts"], attributes, cross) for item in actives
    )


def _color_any(actives: list[dict[str, Any]], attributes: set[str], cross: bool) -> bool:
    return any(_color_summary(item["facts"], attributes, cross) for item in actives)


def _color_summary(facts: dict[str, Any], attributes: set[str], cross: bool) -> str:
    if cross:
        return _produce_summary(facts, attributes, for_cross=True)
    return _force_summary(facts, attributes)


def _boost_report(
    session: BoxSession, cover: dict[str, Any], selections: list[dict[str, Any] | None]
) -> dict[str, Any]:
    required = cover["transform"]["required_turns"]
    total = skill_boost(session.entry.awakenings) if session.reserved_from is not None else 0
    if session.helper:
        total += skill_boost(session.helper["card"].awakenings)
    for selection in selections:
        if selection is None:
            continue
        entry = _dungeon_entry(session, selection)
        card = entry if entry is not None else selection["card"]
        if selection["equip"]:
            card = selection["card"]
        total += skill_boost(card.awakenings)
    return {
        "total": total,
        "required": required,
        "meets": required is None or (session.reserved_from is not None and total >= required),
        "badge": "excluded",
    }


def _chosen_unknowns(actives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for item in actives:
        if not item["unknown"]:
            continue
        key = (item["label"], item["form_id"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "where": item["label"],
                "form_id": item["form_id"],
                "form_name": item["form_name"],
                "effects": item["unknown"],
            }
        )
    return rows


def _investments(selections: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    rows = []
    for selection in selections:
        if selection is None:
            continue
        path = list(selection["form"]["path"])
        if not any(step in _INVESTMENT for step in path):
            continue
        card = selection["card"]
        rows.append(
            {
                "where": selection["label"],
                "owned_id": selection["owned_id"],
                "form_id": card.monster_id,
                "form_name": card.name,
                "path": path,
                "spends": list(selection["form"]["spends"]),
            }
        )
    return rows


def _subs(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("subs must be a list")
    rows = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Sub {index} must be an object")
        owned_id = _required_int(item.get("owned_id"), f"Sub {index} owned_id")
        form_id = _required_int(item.get("form_id"), f"Sub {index} form_id")
        rows.append(
            {
                "owned_id": owned_id,
                "form_id": form_id,
                "assist": _pair(item.get("assist"), f"Sub {index} assist"),
            }
        )
    return rows


def _pair(value: object, label: str) -> tuple[int, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object with owned_id and form_id")
    return (
        _required_int(value.get("owned_id"), f"{label} owned_id"),
        _required_int(value.get("form_id"), f"{label} form_id"),
    )


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, label)


def _required_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _add(demand: dict[int, int], monster_id: int, count: int) -> None:
    demand[monster_id] = demand.get(monster_id, 0) + count


def _name(session: BoxSession, monster_id: int) -> str:
    card = session.load(monster_id)
    return card.name if card else ""


def _flat_forms(pool: dict[str, Any]) -> list[dict[str, Any]]:
    flat = []
    for group in pool["candidates"]:
        for form in group["forms"]:
            flat.append(
                {
                    **form,
                    "owned_name": group["owned_name"],
                    "copies": group["copies"],
                    "leader_copies": group["leader_copies"],
                }
            )
    return flat


def _bucket_specs(pool: dict[str, Any]) -> list[tuple[str, str, str, list[str]]]:
    specs = [(key, label, "cooldown", [key]) for key, label, _ in _ROLES]
    if pool["requirements"]["transform_turns"] is not None:
        specs.append(("skill_boost", "skill boost", "boost", ["skill_boost"]))
    for color in pool["requirements"]["colors"]:
        attributes = [name for name in color["attributes"] if name]
        if not attributes:
            continue
        if color.get("match") == "cross":
            specs.append(
                (
                    "_".join(attributes) + "_orbs",
                    "/".join(attributes) + " orbs for a cross",
                    "cooldown",
                    [f"{name}_orbs" for name in attributes],
                )
            )
            continue
        specs.append(
            (
                "full_" + "_".join(attributes) + "_board",
                "full " + "/".join(attributes) + " board",
                "cooldown",
                [f"{name}_board" for name in attributes],
            )
        )
    return specs


def _matches_bucket(form: dict[str, Any], answers: list[str]) -> bool:
    return any(answer in form["answers"] for answer in answers)


def _take(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda form: _bucket_order(form, mode))
    pinned = [form for form in ordered if form["leader_copies"]]
    others = [form for form in ordered if not form["leader_copies"]]
    return pinned + others[: max(0, _BUCKET_LIMIT - len(pinned))]


def _bucket_order(form: dict[str, Any], mode: str) -> tuple:
    cooldown = form["active"]["minimum_cooldown"]
    missing = not isinstance(cooldown, int)
    head = (
        form["leader_copies"] <= 0,
        bool(form["active"]["unknown_effects"]),
        form["availability"] != "in_box",
        len(form["spends"]),
    )
    tail = (form["form_id"], form["owned_id"])
    if mode == "boost":
        return (*head, -form["skill_boost"], missing, cooldown or 0, *tail)
    return (*head, missing, cooldown or 0, -form["skill_boost"], *tail)


def _short_row(
    form: dict[str, Any],
    flat: list[dict[str, Any]],
    specs: list[tuple[str, str, str, list[str]]],
    relevant: set[str],
    bucket: str,
) -> dict[str, Any]:
    return {
        **_brief_form(form),
        "also_fights_for": [
            key
            for key, _, _, answers in specs
            if key != bucket and _matches_bucket(form, answers)
        ],
        "assists": _assists(form, flat, relevant),
    }


def _assists(
    sub: dict[str, Any], flat: list[dict[str, Any]], relevant: set[str]
) -> list[dict[str, Any]]:
    ranked = []
    for equip in flat:
        if equip["borrowed"] or not equip["equip"] or not _spare_copy(sub, equip):
            continue
        added = [
            answer
            for answer in equip["answers"]
            if answer in relevant and answer not in sub["answers"]
        ]
        if not added and equip["skill_boost"] <= 0:
            continue
        ranked.append((equip, added))
    ranked.sort(key=lambda item: _assist_order(*item))
    return [_assist_row(equip, added) for equip, added in ranked[:_ASSIST_LIMIT]]


def _spare_copy(sub: dict[str, Any], equip: dict[str, Any]) -> bool:
    spare = equip["copies"] - equip["leader_copies"]
    if equip["owned_id"] == sub["owned_id"] and not sub["leader_copies"]:
        spare -= 1
    return spare >= 1


def _assist_order(equip: dict[str, Any], added: list[str]) -> tuple:
    cooldown = equip["active"]["minimum_cooldown"]
    return (
        bool(equip["active"]["unknown_effects"]),
        -len(added),
        -equip["skill_boost"],
        equip["availability"] != "in_box",
        len(equip["spends"]),
        not isinstance(cooldown, int),
        cooldown or 0,
        equip["form_id"],
        equip["owned_id"],
    )


def _assist_row(equip: dict[str, Any], added: list[str]) -> dict[str, Any]:
    return {**_brief_form(equip), "adds": added}


def _brief_form(form: dict[str, Any]) -> dict[str, Any]:
    active = form["active"]
    return {
        "owned_id": form["owned_id"],
        "owned_name": form["owned_name"],
        "copies": form["copies"],
        "leader_copies": form["leader_copies"],
        "form_id": form["form_id"],
        "form_name": form["form_name"],
        "availability": form["availability"],
        "stage": form["stage"],
        "path": form["path"],
        "spends": form["spends"],
        "equip": form["equip"],
        "main_attribute": form["main_attribute"],
        "skill_boost": form["skill_boost"],
        "minimum_cooldown": active["minimum_cooldown"],
        "unknown_effects": [
            {"source_type": effect["source_type"], "summary": effect["summary"]}
            for effect in active["unknown_effects"]
        ],
        "summary": "; ".join(effect["summary"] for effect in active["effects"]),
    }


def _shortlist_contract(requirements: dict[str, Any]) -> list[str]:
    return [
        "Each bucket is one spot a sub can fight for. A copy can win only one spot.",
        "Pick at most the number of sub slots. The reserved leader copy is not a sub.",
        (
            "An assist must come from that sub's list, have Awoken Assist, "
            "and spend a different copy."
        ),
        "A reachable form is optional. Keep the owned form when the upgrade is small.",
        _color_contract(requirements, shortlist=True),
        _damage_contract(requirements),
        "Unknown effects stay unresolved. This shortlist does not name a team.",
    ]


def _color_contract(requirements: dict[str, Any], shortlist: bool) -> str:
    if any(color.get("match") == "cross" for color in requirements["colors"]):
        return (
            "A cross is exactly five orbs in a plus. Make some of the linked color and "
            "leave other colors on the board. A full board of that color clears as one "
            "match and cannot be a cross."
        )
    if shortlist:
        return "A full board is the only color entry that keeps the link. Orb makers are omitted."
    return (
        "A full board of one required color can keep that link up. "
        "Creating or changing some orbs does not."
    )


def _is_equip(card: Card) -> bool:
    return _AWOKEN_ASSIST in card.awakenings


def _damage_contract(requirements: dict[str, Any]) -> str:
    colors = sorted({name for color in requirements["colors"] for name in color["attributes"]})
    if not colors:
        return "This leader has no linked color, so a sub's attribute is not limited."
    listed = "/".join(colors)
    return (
        f"A sub's main attribute has to be {listed}, and it has to have awakenings. "
        f"That is what deals damage on the {listed} match. An assist can be another color."
    )


def _damage_attributes(cover: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for color in cover["color_force"]:
        names.update(color["attributes"])
    return names


def _main_attribute(card: Card) -> str | None:
    attrs = card.raw.get("attrs")
    if not isinstance(attrs, list) or not attrs or not isinstance(attrs[0], int):
        return None
    return ATTRIBUTES.get(attrs[0])


def _can_damage(card: Card, required: set[str]) -> bool:
    attrs = card.raw.get("attrs")
    if not required or not isinstance(attrs, list) or not attrs or not isinstance(attrs[0], int):
        return True
    if ATTRIBUTES.get(attrs[0]) not in required:
        return False
    return bool(card.awakenings)


def _color_attributes(cover: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for color in cover["color_force"]:
        names.update(color["attributes"])
    return names


def _cross_attributes(cover: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for color in cover["color_force"]:
        if color.get("match") == "cross":
            names.update(color["attributes"])
    return names
