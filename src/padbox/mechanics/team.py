"""Candidate pool for one leader, and a check of a team chosen from it.

The pool lists owned forms beside the evolutions and assists those copies can
become. It does not assign slots or pick a best team. `check_team` reports
ownership, evolution, team shape, transform readiness, mechanic coverage, and
unknowns separately. Transform readiness, evolution feasibility, and other
decoder-backed requirements use `pass`, `fail`, or `unknown`. An unresolved
cooldown is unknown, and one member's unknown result does not erase another
member's known cooldown. A special evolution requirement the reference cannot
interpret is unknown. Exact copy limits and non-stackable spends stay separate
from assumptions about untracked stackable materials, max skill, and awakenings.
`strategic_quality` stays ``not_evaluated`` until a later evaluator scores it.
`ok` is deprecated: it is true only when every hard check passes on a complete
team of one leader, four subs, and one helper.
`candidate_copies` and `omitted_copies` are deprecated aliases of the monster-ID
group counts `candidate_groups` and `omitted_groups`.
"""

from collections import Counter
from typing import Any

from padbox.database.store import Store
from padbox.mechanics.awakenings import describe, skill_boost
from padbox.mechanics.coverage import (
    _ROLES,
    STANDARD_SUB_SLOTS,
    BoxSession,
    Card,
    _cover_report,
    _force_summary,
    _produce_summary,
    leader_context,
    uptime_covers,
)
from padbox.mechanics.decode import ATTRIBUTES, DECODER_VERSION
from padbox.mechanics.result import CheckResult, combine_checks

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
    groups = sum(1 for row in candidates if not row["borrowed"])
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
        "candidate_groups": groups,
        "candidate_copies": groups,
        "candidate_forms": sum(len(row["forms"]) for row in candidates),
        "omitted_groups": omitted,
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
            if not form["borrowed"]
            and form.get("sub_slot") != "blocked"
            and form["can_damage"]
            and _matches_bucket(form, answers)
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
    subs, slot_problems = _subs(team.get("subs", []))
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
            slot_problems,
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
        _mark_sub_slots(
            built,
            session.known_owned[owned_id],
            owned_id == session.reserved_from,
            session.leader_id,
        )
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
        for form in built:
            form["sub_slot"] = "borrowed"
        rows.append(_group(card, 0, False, True, built))
    rows.sort(key=lambda row: (not row["leader_copies"], not row["borrowed"], row["owned_id"]))
    return rows


def _mark_sub_slots(
    forms: list[dict[str, Any]], copies: int, reserved: bool, leader_id: int
) -> None:
    spare = copies - (1 if reserved else 0)
    for form in forms:
        if not reserved:
            form["sub_slot"] = "available"
        elif form["form_id"] == leader_id:
            form["sub_slot"] = "leader"
        elif spare >= 1:
            form["sub_slot"] = "spare"
        else:
            form["sub_slot"] = "blocked"


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
    spends = [_material_view(session, material_id) for material_id in form["spends"]]
    assumed = [
        _material_view(session, material_id) for material_id in form.get("assumed_materials") or []
    ]
    row = {
        "form_id": card.monster_id,
        "form_name": card.name,
        "availability": "reachable" if any(step in _INVESTMENT for step in path) else "in_box",
        "stage": form["stage"],
        "path": path,
        "spends": spends,
        "assumed_materials": assumed,
        "special_requirements": list(form.get("special_requirements") or []),
        "sub_slot": "available",
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
        "sub_slots": STANDARD_SUB_SLOTS,
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
        (
            "Farmable materials are not spent. A monster listed under spends is consumed. "
            "Untracked stackable materials are assumptions, reported apart from exact spends."
        ),
        (
            "A standard team is one leader, four subs, and one helper. "
            "A missing helper does not add a fifth sub."
        ),
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
    slot_problems: list[str],
) -> dict[str, Any]:
    problems: list[str] = []
    structure: list[str] = []
    ownership: list[str] = []
    evolution: list[str] = []
    coverage_problems: list[str] = []
    for message in slot_problems:
        _note(problems, structure, message)
    if session.reserved_from is None:
        _note(problems, ownership, "No owned copy can become this leader.")
    selections = []
    for index, sub in enumerate(subs, start=1):
        selections.append(
            _resolve(
                session,
                sub["owned_id"],
                sub["form_id"],
                f"Sub {index}",
                problems,
                ownership,
                evolution,
                structure,
            )
        )
        if sub["assist"] is not None:
            owned_id, form_id = sub["assist"]
            selections.append(
                _resolve(
                    session,
                    owned_id,
                    form_id,
                    f"Sub {index} assist",
                    problems,
                    ownership,
                    evolution,
                    structure,
                    equip=True,
                )
            )
    if leader_assist is not None:
        selections.append(
            _resolve(
                session,
                leader_assist[0],
                leader_assist[1],
                "Leader assist",
                problems,
                ownership,
                evolution,
                structure,
                equip=True,
            )
        )
    if helper_assist is not None:
        if session.helper is None:
            _note(problems, structure, "A helper assist was named without a helper.")
        selections.append(
            _resolve(
                session,
                helper_assist[0],
                helper_assist[1],
                "Helper assist",
                problems,
                ownership,
                evolution,
                structure,
                equip=True,
            )
        )
    chosen = _choose_routes(session, selections)
    copies, materials = _demands(session, selections, chosen)
    for message in _demand_messages(session, copies, materials, materials_only=False):
        _note(problems, ownership, message)
    for message in _demand_messages(session, copies, materials, materials_only=True):
        _note(problems, evolution, message)
    for message in _damage_problems(session, cover, selections):
        _note(problems, coverage_problems, message)
    actives = _chosen_actives(session, selections)
    coverage_holes, coverage_result, decoder_requirements = _coverage_report(cover, actives)
    if coverage_problems:
        coverage_result = CheckResult.FAIL
    boost = _boost_report(session, cover, selections)
    unknowns = _chosen_unknowns(actives)
    specials = _specials(chosen)
    ownership_valid = not ownership
    if evolution:
        evolution_result = CheckResult.FAIL
    elif specials:
        evolution_result = CheckResult.UNKNOWN
    else:
        evolution_result = CheckResult.PASS
    team_structure_valid = not structure
    team_complete = len(subs) == STANDARD_SUB_SLOTS and session.helper is not None
    transform_feasible = boost["result"]
    mechanics_coverage = coverage_result.value
    passed = CheckResult.PASS.value
    return {
        "decoder_version": DECODER_VERSION,
        "leader_id": session.leader_id,
        "helper_id": session.helper_id,
        "ownership_valid": ownership_valid,
        "evolution_feasible": evolution_result.value,
        "team_structure_valid": team_structure_valid,
        "team_complete": team_complete,
        "transform_feasible": transform_feasible,
        "mechanics_coverage": mechanics_coverage,
        "decoder_requirements": decoder_requirements,
        "strategic_quality": "not_evaluated",
        "unknowns": unknowns,
        "ok": (
            ownership_valid
            and evolution_result is CheckResult.PASS
            and team_structure_valid
            and team_complete
            and transform_feasible == passed
            and mechanics_coverage == passed
        ),
        "problems": problems,
        "open_holes": coverage_holes + _transform_holes(boost),
        "unknown_effects": unknowns,
        "copy_uses": _copy_uses(session, selections),
        "special_requirements": specials,
        "assumptions": _assumptions(session, chosen),
        "investments": _investments(session, selections, chosen),
        "skill_boost": boost,
    }


def _note(problems: list[str], bucket: list[str], message: str) -> None:
    bucket.append(message)
    problems.append(message)


def _resolve(
    session: BoxSession,
    owned_id: int,
    form_id: int,
    label: str,
    problems: list[str],
    ownership: list[str],
    evolution: list[str],
    structure: list[str],
    equip: bool = False,
) -> dict[str, Any] | None:
    if owned_id not in session.forms_by_owned:
        name = _name(session, owned_id)
        _note(problems, ownership, f"{label}: #{owned_id} {name} is not in the applied box.")
        return None
    form = next(
        (item for item in session.forms_by_owned[owned_id] if item["form_id"] == form_id),
        None,
    )
    if form is None:
        _note(
            problems,
            evolution,
            f"{label}: #{form_id} is not a reachable form of owned #{owned_id}.",
        )
        return None
    card = session.load(form_id)
    if card is None:
        _note(problems, evolution, f"{label}: #{form_id} is not in the local reference set.")
        return None
    if equip and not _is_equip(card):
        _note(problems, structure, f"{label}: #{form_id} {card.name} is not an assist equip.")
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


def _material_view(session: BoxSession, material_id: int) -> dict[str, Any]:
    material = session.load(material_id)
    return {"monster_id": material_id, "name": material.name if material else None}


def _form_routes(form: dict[str, Any]) -> list[dict[str, Any]]:
    routes = form.get("routes")
    if routes:
        return routes
    return [
        {
            "path": list(form.get("path") or []),
            "spends": list(form.get("spends") or []),
            "assumed_materials": list(form.get("assumed_materials") or []),
            "special_requirements": list(form.get("special_requirements") or []),
        }
    ]


def _route_rank(route: dict[str, Any]) -> tuple[int, int, tuple[int, ...]]:
    spends = tuple(route.get("spends") or [])
    return (1 if route.get("special_requirements") else 0, len(spends), spends)


def _leader_routes(session: BoxSession) -> list[dict[str, Any]]:
    if session.reserved_from is None:
        return []
    for form in session.forms_by_owned.get(session.reserved_from, []):
        if form["form_id"] == session.entry.monster_id and form["stage"] == "entry":
            return _form_routes(form)
    return [
        {
            "path": list(session.reserved_path),
            "spends": list(session.spends),
            "assumed_materials": list(session.assumed_materials),
            "special_requirements": list(session.special_requirements),
        }
    ]


def _fits(pool: Counter[int], spends: list[int]) -> bool:
    need: Counter[int] = Counter(spends)
    return all(pool[monster_id] >= count for monster_id, count in need.items())


def _choose_routes(
    session: BoxSession, selections: list[dict[str, Any] | None]
) -> dict[str, dict[str, Any]]:
    """Pick one payable route per evolution, keeping equal-cost alternatives."""
    pool: Counter[int] = Counter(session.known_owned)
    if session.reserved_from is not None:
        pool[session.reserved_from] -= 1
    for selection in selections:
        if selection is not None:
            pool[selection["owned_id"]] -= 1
    investors: list[tuple[str, list[dict[str, Any]]]] = []
    leader_routes = _leader_routes(session)
    if leader_routes:
        investors.append(("leader", leader_routes))
    for selection in selections:
        if selection is None:
            continue
        investors.append((selection["label"], _form_routes(selection["form"])))
    investors.sort(key=lambda item: len(item[1]))
    chosen: dict[str, dict[str, Any]] = {}
    for key, routes in investors:
        ordered = sorted(routes, key=_route_rank)
        pick = next((route for route in ordered if _fits(pool, route["spends"])), None)
        if pick is None:
            pick = ordered[0]
        else:
            for monster_id in pick["spends"]:
                pool[monster_id] -= 1
        chosen[key] = pick
    return chosen


def _demands(
    session: BoxSession,
    selections: list[dict[str, Any] | None],
    chosen: dict[str, dict[str, Any]],
) -> tuple[Counter[int], Counter[int]]:
    copies: Counter[int] = Counter()
    materials: Counter[int] = Counter()
    if session.reserved_from is not None:
        copies[session.reserved_from] += 1
    for selection in selections:
        if selection is not None:
            copies[selection["owned_id"]] += 1
    for route in chosen.values():
        materials.update(route["spends"])
    return copies, materials


def _demand_messages(
    session: BoxSession,
    copies: Counter[int],
    materials: Counter[int],
    materials_only: bool,
) -> list[str]:
    messages = []
    monster_ids = sorted(set(copies) | set(materials))
    for monster_id in monster_ids:
        used = copies[monster_id]
        spent = materials[monster_id]
        have = session.known_owned.get(monster_id, 0)
        if used + spent <= have:
            continue
        if materials_only:
            if spent <= 0:
                continue
            messages.append(_material_message(session, monster_id, used, spent, have))
            continue
        if used > have:
            messages.append(_copy_message(session, monster_id, used, have))
    return messages


def _copy_message(session: BoxSession, monster_id: int, used: int, have: int) -> str:
    name = _name(session, monster_id)
    text = f"#{monster_id} {name} is used {used} times and the box has {have}."
    if used > 1:
        text += " Those forms are mutually exclusive on one copy."
    return text


def _material_message(
    session: BoxSession, monster_id: int, used: int, spent: int, have: int
) -> str:
    name = _name(session, monster_id)
    if used:
        return (
            f"#{monster_id} {name} is used {used} times and required {spent} times "
            f"as evolution material, and the box has {have}."
        )
    return (
        f"#{monster_id} {name} is required {spent} times as evolution material "
        f"and the box has {have}."
    )


def _copy_uses(
    session: BoxSession, selections: list[dict[str, Any] | None]
) -> list[dict[str, Any]]:
    uses = []
    if session.reserved_from is not None:
        use: dict[str, Any] = {
            "where": "Leader",
            "owned_id": session.reserved_from,
            "form_id": session.entry.monster_id,
            "kind": "reserved_leader",
        }
        if session.transforms and session.leader_id != session.entry.monster_id:
            use["battle_form_id"] = session.leader_id
        uses.append(use)
    for selection in selections:
        if selection is None:
            continue
        form = selection["form"]
        kind = "assist" if selection["equip"] else "sub"
        if (
            not selection["equip"]
            and form["stage"] == "battle"
            and "transform" in form["path"]
        ):
            kind = "transform_base"
        uses.append(
            {
                "where": selection["label"],
                "owned_id": selection["owned_id"],
                "form_id": form["form_id"],
                "kind": kind,
            }
        )
    return uses


def _specials(chosen: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for key, route in chosen.items():
        where = "Leader" if key == "leader" else key
        for item in route.get("special_requirements") or []:
            rows.append({"where": where, **item})
    return rows


def _assumptions(
    session: BoxSession, chosen: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = [
        {
            "kind": "max_skill",
            "exact": False,
            "summary": (
                "Cooldowns use max skill level. The box does not record per-copy skill level."
            ),
        },
        {
            "kind": "normal_awakenings",
            "exact": False,
            "summary": (
                "Skill boost counts every normal awakening on the reference card. "
                "Unlock state is untracked. Super awakenings, sync awakenings, and latents "
                "stay unselected."
            ),
        },
    ]
    for key, route in chosen.items():
        where = "Leader" if key == "leader" else key
        for material_id in route.get("assumed_materials") or []:
            name = _name(session, material_id)
            rows.append(
                {
                    "kind": "stackable_material",
                    "exact": False,
                    "where": where,
                    "monster_id": material_id,
                    "name": name,
                    "summary": (
                        f"#{material_id} {name} is an untracked stackable material. "
                        "It is assumed available and is not an exact spend."
                    ),
                }
            )
    return rows


def _chosen_actives(
    session: BoxSession, selections: list[dict[str, Any] | None]
) -> list[dict[str, Any]]:
    """One active per monster: entry, transformed, or inherited, never all three."""
    resolved = [selection for selection in selections if selection is not None]
    inherited = {
        _host_label(selection["label"]): selection
        for selection in resolved
        if selection["equip"]
    }
    members: list[tuple[str, Card, str]] = []
    leader_card = _fielded_card(session, session.leader_id, session.entry, session.transforms)
    members.append(("Leader", leader_card, _active_state(leader_card, session.entry)))
    if session.helper:
        helper_entry = session.helper["card"]
        helper_card = _fielded_card(
            session,
            session.helper_id or helper_entry.monster_id,
            helper_entry,
            session.helper["transforms"],
        )
        members.append(("Helper", helper_card, _active_state(helper_card, helper_entry)))
    for selection in resolved:
        if selection["equip"]:
            continue
        members.append(
            (selection["label"], selection["card"], _form_state(selection["form"]))
        )
    actives = []
    for label, card, state in members:
        equip = inherited.get(label)
        if equip is not None:
            actives.append(
                _active_source(label, equip["card"], session.facts_for(equip["card"]), "inherited")
            )
            continue
        actives.append(_active_source(label, card, session.facts_for(card), state))
    return actives


def _fielded_card(session: BoxSession, named_id: int, entry: Card, transforms: bool) -> Card:
    if transforms and named_id != entry.monster_id:
        card = session.load(named_id)
        if card is not None:
            return card
    return entry


def _active_state(card: Card, entry: Card) -> str:
    if card.monster_id == entry.monster_id:
        return "entry"
    return "transformed"


def _form_state(form: dict[str, Any]) -> str:
    if form["stage"] == "battle":
        return "transformed"
    return "entry"


def _host_label(label: str) -> str:
    suffix = " assist"
    if label.endswith(suffix):
        return label[: -len(suffix)]
    return label


def _active_source(label: str, card: Card, facts: dict[str, Any], state: str) -> dict[str, Any]:
    return {
        "label": label,
        "form_id": card.monster_id,
        "form_name": card.name,
        "state": state,
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


def _coverage_report(
    cover: dict[str, Any], actives: list[dict[str, Any]]
) -> tuple[list[str], CheckResult, list[dict[str, Any]]]:
    holes: list[str] = []
    requirements: list[dict[str, Any]] = []
    results: list[CheckResult] = []
    for _key, label, predicate in _ROLES:
        result = _effect_result(
            actives, lambda facts, predicate=predicate: _matches(facts, predicate)
        )
        _record(requirements, results, label, result)
        if result is CheckResult.PASS:
            continue
        if result is CheckResult.UNKNOWN and _role_any(actives, predicate):
            holes.append(f"{label.title()} is only on chosen cards that also have unknown effects.")
        elif result is CheckResult.UNKNOWN:
            holes.append(
                f"{label.title()} is unresolved because a chosen active is not fully decoded."
            )
        else:
            holes.append(f"No chosen card has a verified {label}.")
    for color in cover["color_force"]:
        attributes = set(color["attributes"])
        if not attributes:
            if color.get("leader_unknown"):
                result = CheckResult.UNKNOWN
                name = f"#{color['form_id']} leader skill"
                _record(requirements, results, name, result)
                holes.append(
                    f"#{color['form_id']} has unknown leader-skill effects and no decoded "
                    "linked-orb condition."
                )
            continue
        attrs = "/".join(color["attributes"])
        if color.get("match") == "cross":
            name = f"{attrs} cross"
            if _color_clean(actives, attributes, True):
                _record(requirements, results, name, CheckResult.PASS)
                continue
            if _has_unknown_active(actives):
                result = CheckResult.UNKNOWN
                if _color_any(actives, attributes, True):
                    holes.append(
                        f"{attrs.capitalize()} orbs for a cross on #{color['form_id']} are only "
                        "on chosen cards that also have unknown effects."
                    )
                else:
                    holes.append(
                        f"{attrs.capitalize()} orbs for a cross on #{color['form_id']} are "
                        "unresolved because a chosen active is not fully decoded."
                    )
            else:
                result = CheckResult.FAIL
                holes.append(
                    f"No chosen card makes {attrs} orbs that can stay a cross for "
                    f"#{color['form_id']} ({color['minimum_linked']}+ linked). "
                    "A full board of that color clears as one match and cannot be a cross."
                )
            _record(requirements, results, name, result)
            continue
        name = f"full {attrs} board"
        if uptime_covers(_link_uptime(actives, attributes)):
            _record(requirements, results, name, CheckResult.PASS)
            continue
        if _has_unknown_active(actives):
            result = CheckResult.UNKNOWN
            if _color_any(actives, attributes, False):
                holes.append(
                    f"A full board of {attrs} for #{color['form_id']} is only on chosen cards "
                    "that also have unknown effects."
                )
            else:
                holes.append(
                    f"A full board of {attrs} for #{color['form_id']} is unresolved because "
                    "a chosen active is not fully decoded."
                )
        elif _color_clean(actives, attributes, False):
            result = CheckResult.FAIL
            holes.append(
                f"Chosen cards do not keep a full board of {attrs} up for "
                f"#{color['form_id']} ({color['minimum_linked']}+ linked)."
            )
        else:
            result = CheckResult.FAIL
            holes.append(
                f"No chosen card keeps a full board of {attrs} up for "
                f"#{color['form_id']} ({color['minimum_linked']}+ linked)."
            )
        _record(requirements, results, name, result)
    return holes, combine_checks(results), requirements


def _record(
    requirements: list[dict[str, Any]],
    results: list[CheckResult],
    name: str,
    result: CheckResult,
) -> None:
    requirements.append({"name": name, "result": result.value})
    results.append(result)


def _effect_result(actives: list[dict[str, Any]], satisfied: Any) -> CheckResult:
    if any(not item["unknown"] and satisfied(item["facts"]) for item in actives):
        return CheckResult.PASS
    if _has_unknown_active(actives):
        return CheckResult.UNKNOWN
    return CheckResult.FAIL


def _has_unknown_active(actives: list[dict[str, Any]]) -> bool:
    return any(item["unknown"] for item in actives)


def _link_uptime(actives: list[dict[str, Any]], attributes: set[str]) -> float:
    """Reciprocal cooldown of clean full-board actives, one contribution per slot."""
    best: dict[str, int] = {}
    for item in actives:
        if item["unknown"] or not _force_summary(item["facts"], attributes):
            continue
        cooldown = item["facts"]["active"].get("minimum_cooldown")
        if not isinstance(cooldown, int) or cooldown <= 0:
            continue
        current = best.get(item["label"])
        if current is None or cooldown < current:
            best[item["label"]] = cooldown
    return sum(1 / cooldown for cooldown in best.values())


def _transform_holes(boost: dict[str, Any]) -> list[str]:
    holes = []
    required = boost["required"]
    if (
        boost["result"] == CheckResult.FAIL.value
        and isinstance(required, int)
        and boost["total"] < required
    ):
        holes.append(
            f"Chosen skill boost is {boost['total']}, short of the {required}-turn transform."
        )
    unresolved = [member["where"] for member in boost["members"] if member["result"] == "unknown"]
    if unresolved:
        names = ", ".join(unresolved)
        if isinstance(required, int):
            holes.append(
                f"{names} transform cooldown is unresolved. "
                f"The known transform still requires {required} turns."
            )
        else:
            holes.append(f"{names} transform cooldown is unresolved.")
    return holes


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
    members = [_leader_transform_member(card) for card in cover["transform"]["cards"]]
    for selection in selections:
        if selection is None or selection["equip"]:
            continue
        member = _sub_transform_member(session, selection)
        if member is not None:
            members.append(member)
    known = [
        member["required_turns"]
        for member in members
        if isinstance(member["required_turns"], int)
    ]
    required = max(known) if known else None
    bases = [
        member["base_required_turns"]
        for member in members
        if isinstance(member.get("base_required_turns"), int)
    ]
    base_required = max(bases) if bases else None
    for member in members:
        turns = member["required_turns"]
        if not isinstance(turns, int):
            member["result"] = CheckResult.UNKNOWN.value
        elif total >= turns:
            member["result"] = CheckResult.PASS.value
        else:
            member["result"] = CheckResult.FAIL.value
    if not members:
        result = CheckResult.PASS
    else:
        result = combine_checks(CheckResult(member["result"]) for member in members)
    return {
        "total": total,
        "required": required,
        "required_at_base_skill": base_required,
        "basis": {
            "cooldown": "max_skill",
            "awakenings": "all_reference_normal_awakenings",
            "exact": False,
        },
        "result": result.value,
        "meets": result is CheckResult.PASS,
        "members": members,
        "badge": "excluded",
    }


def _leader_transform_member(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "where": str(card["role"]).title(),
        "form_id": card["entry_id"],
        "form_name": card["entry_name"],
        "required_turns": card["required_turns"],
        "base_required_turns": card.get("base_required_turns"),
        "result": CheckResult.UNKNOWN.value,
    }


def _sub_transform_member(
    session: BoxSession, selection: dict[str, Any]
) -> dict[str, Any] | None:
    form = selection["form"]
    if form["stage"] != "battle" or "transform" not in form["path"]:
        return None
    entry = _dungeon_entry(session, selection)
    turns = None
    base = None
    form_id = selection["card"].monster_id
    form_name = selection["card"].name
    if entry is not None:
        facts = session.facts_for(entry)
        targets = facts["active_roles"]["transform_into"]
        cooldown = facts["active"].get("minimum_cooldown")
        if form_id in targets and isinstance(cooldown, int):
            turns = cooldown
            base = facts["active"].get("base_cooldown")
        form_id = entry.monster_id
        form_name = entry.name
    return {
        "where": selection["label"],
        "form_id": form_id,
        "form_name": form_name,
        "required_turns": turns,
        "base_required_turns": base if isinstance(base, int) else None,
        "result": CheckResult.UNKNOWN.value,
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


def _investments(
    session: BoxSession,
    selections: list[dict[str, Any] | None],
    chosen: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    leader = chosen.get("leader")
    if leader is not None and any(step in _INVESTMENT for step in leader["path"]):
        rows.append(
            {
                "where": "Leader",
                "owned_id": session.reserved_from,
                "form_id": session.entry.monster_id,
                "form_name": session.entry.name,
                "path": list(leader["path"]),
                "spends": list(leader["spends"]),
            }
        )
    for selection in selections:
        if selection is None:
            continue
        route = chosen.get(selection["label"], selection["form"])
        path = list(route.get("path") or [])
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
                "spends": list(route.get("spends") or []),
            }
        )
    return rows


def _subs(value: object) -> tuple[list[dict[str, Any]], list[str]]:
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
    problems = []
    if len(rows) > STANDARD_SUB_SLOTS:
        problems.append(
            f"A standard team has {STANDARD_SUB_SLOTS} sub slots and the team lists "
            f"{len(rows)}. The helper slot is separate and does not become a fifth sub."
        )
    return rows, problems


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
    pinned = [form for form in ordered if form.get("sub_slot") == "leader"]
    others = [form for form in ordered if form.get("sub_slot") != "leader"]
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
        (
            "Pick at most four subs. A missing helper does not add a fifth sub. "
            "The reserved leader copy is not a sub. An alternate form of that copy "
            "needs a spare copy."
        ),
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
