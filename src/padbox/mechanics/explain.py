"""Explain one monster's verified effects, including its transform and assist forms."""

import json
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from padbox.database.store import Store
from padbox.mechanics.awakenings import describe
from padbox.mechanics.decode import DECODER_VERSION, Effect, decode_skill

KIT_KINDS = {"transform", "assist_evolution"}


def explain_monster(
    store: Store, monster_id: int, skill_index: dict[int, dict[str, Any]] | None = None
) -> dict[str, Any]:
    with closing(store.connect("reference")) as db:
        row = db.execute(
            "SELECT id, name, raw, provenance FROM monster WHERE id=?", (monster_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Monster #{monster_id} is not in the local reference set")
        edges = [
            (int(item["source_id"]), int(item["target_id"]), str(item["kind"]))
            for item in db.execute("SELECT source_id, target_id, kind FROM relationship")
        ]
        members = kit_members(monster_id, edges)
        cards = []
        for member_id, reason in members:
            card = db.execute(
                "SELECT id, name, raw FROM monster WHERE id=?", (member_id,)
            ).fetchone()
            skills = {
                str(item["role"]): json.loads(item["raw"])
                for item in db.execute(
                    """SELECT ms.role AS role, skill.raw AS raw
                    FROM monster_skill ms JOIN skill ON skill.id = ms.skill_id
                    WHERE ms.monster_id=?""",
                    (member_id,),
                )
            }
            awakening_rows = db.execute(
                """SELECT awakening_id, kind FROM monster_awakening
                WHERE monster_id=? ORDER BY kind, position""",
                (member_id,),
            ).fetchall()
            outgoing = [
                {"target_id": target, "kind": kind}
                for source, target, kind in edges
                if source == member_id
            ]
            cards.append((reason, card, skills, awakening_rows, outgoing))
        provenance = json.loads(row["provenance"])
    index = _index(store.home, str(provenance.get("version") or ""), skill_index)
    forms = []
    for reason, card, skills, awakening_rows, outgoing in cards:
        if card is None:
            continue
        raw = json.loads(card["raw"])
        forms.append(
            _form(
                int(card["id"]),
                str(card["name"]),
                reason,
                raw,
                [int(item["awakening_id"]) for item in awakening_rows if item["kind"] == "normal"],
                [int(item["awakening_id"]) for item in awakening_rows if item["kind"] == "super"],
                skills,
                outgoing,
                index,
            )
        )
    return {
        "decoder_version": DECODER_VERSION,
        "monster_id": monster_id,
        "skill_index": "supplied" if skill_index is not None else _index_status(index),
        "forms": forms,
    }


def kit_members(monster_id: int, edges: list[tuple[int, int, str]]) -> list[tuple[int, str]]:
    """The card, its transform and assist forms, and the base those forms come from."""
    outgoing: dict[int, list[tuple[int, str]]] = defaultdict(list)
    incoming: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for source, target, kind in edges:
        outgoing[source].append((target, kind))
        incoming[target].append((source, kind))
    ordered = [(monster_id, "self")]
    seen = {monster_id}

    def add(member_id: int, reason: str) -> None:
        if member_id not in seen:
            seen.add(member_id)
            ordered.append((member_id, reason))

    for target, kind in outgoing[monster_id]:
        if kind in KIT_KINDS:
            add(target, kind)
    for base, kind in incoming[monster_id]:
        if kind != "transform":
            continue
        add(base, "transform_base")
        for target, child_kind in outgoing[base]:
            if child_kind in KIT_KINDS:
                add(target, child_kind)
    return ordered


def roles_from(effects: list[Effect]) -> dict[str, Any]:
    """Checklist facts present in verified effects. Unknown effects are counted only."""
    roles: dict[str, Any] = {
        "full_bind_clear": False,
        "bind_clear_turns": None,
        "full_awoken_bind_clear": False,
        "awoken_bind_clear_turns": None,
        "full_unmatchable_clear": False,
        "unmatchable_clear_turns": None,
        "attribute_absorb_turns": None,
        "damage_absorb_turns": None,
        "combo_absorb_turns": None,
        "void_pierce_turns": None,
        "charge_other_skills_turns": None,
        "transform_into": [],
        "boards": [],
        "board_changes": [],
        "created_orbs": [],
        "all_allies_atk": [],
        "move_time_seconds": None,
        "linked_orb_leaders": [],
        "awakening_triggers": [],
        "unknown_effects": 0,
    }
    for effect in effects:
        if effect.evidence != "verified":
            roles["unknown_effects"] += 1
            continue
        details = effect.details
        if effect.kind == "bind_clear":
            _clear_role(roles, "bind_clear", details)
        elif effect.kind == "awoken_bind_clear":
            _clear_role(roles, "awoken_bind_clear", details)
        elif effect.kind == "unmatchable_clear":
            _clear_role(roles, "unmatchable_clear", details)
        elif effect.kind == "absorb_void":
            for name in details.get("kinds", []):
                _max_turns(roles, f"{name}_absorb_turns", int(details["turns"]))
        elif effect.kind == "void_pierce":
            _max_turns(roles, "void_pierce_turns", int(details["turns"]))
        elif effect.kind == "charge_other_skills":
            _max_turns(roles, "charge_other_skills_turns", int(details["turns"]))
        elif effect.kind == "transform":
            roles["transform_into"].append(int(details["monster_id"]))
        elif effect.kind == "board_size":
            roles["boards"].append(
                {
                    "columns": details["columns"],
                    "rows": details["rows"],
                    "turns": details["turns"],
                }
            )
        elif effect.kind == "board_change":
            roles["board_changes"].append(list(details.get("orbs") or []))
        elif effect.kind == "create_orbs":
            roles["created_orbs"].append(
                {"count": details["count"], "attributes": list(details["attributes"])}
            )
        elif effect.kind == "all_allies_atk":
            roles["all_allies_atk"].append(
                {
                    "turns": details["turns"],
                    "multiplier_hundredths": details["multiplier_hundredths"],
                }
            )
        elif effect.kind == "move_time":
            roles["move_time_seconds"] = details["seconds"]
        elif effect.kind == "linked_orb_leader":
            roles["linked_orb_leaders"].append(details)
        elif effect.kind == "awakening_trigger":
            roles["awakening_triggers"].append(details)
    return roles


def _clear_role(roles: dict[str, Any], prefix: str, details: dict[str, Any]) -> None:
    if details.get("full"):
        roles[f"full_{prefix}"] = True
        return
    _max_turns(roles, f"{prefix}_turns", int(details["turns"]))


def _max_turns(roles: dict[str, Any], key: str, turns: int) -> None:
    current = roles[key]
    roles[key] = turns if current is None else max(int(current), turns)


def _form(
    monster_id: int,
    name: str,
    reason: str,
    raw: dict[str, Any],
    awakening_ids: list[int],
    super_awakenings: list[int],
    skills: dict[str, dict[str, Any]],
    outgoing: list[dict[str, Any]],
    index: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    active_effects, active = _skill_report(skills.get("active"), index)
    leader_effects, leader = _skill_report(skills.get("leader"), index)
    return {
        "monster_id": monster_id,
        "name": name,
        "reason": reason,
        "can_assist": bool(raw.get("canAssist")),
        "eight_latent_slots": bool(raw.get("is8Latent")),
        "sync_awakening": _sync(raw),
        "awakenings": [describe(awakening_id) for awakening_id in awakening_ids],
        "super_awakenings": [describe(awakening_id) for awakening_id in super_awakenings],
        "edges": outgoing,
        "active": active,
        "leader": leader,
        "roles": roles_from(active_effects + leader_effects),
    }


def _sync(raw: dict[str, Any]) -> dict[str, Any]:
    awakening_id = raw.get("syncAwakening")
    named = describe(int(awakening_id)) if isinstance(awakening_id, int) and awakening_id else None
    return {
        "id": awakening_id,
        "name": named["name"] if named else None,
        "conditions": raw.get("syncAwakeningConditions") or [],
        "evidence": "unknown",
    }


def _skill_report(
    skill: dict[str, Any] | None, index: dict[int, dict[str, Any]]
) -> tuple[list[Effect], dict[str, Any]]:
    if not skill:
        return [], {
            "skill_id": None,
            "base_cooldown": None,
            "minimum_cooldown": None,
            "effects": [],
        }
    merged = dict(index)
    skill_id = int(skill.get("id") or 0)
    if skill_id:
        merged[skill_id] = skill
    effects = decode_skill(skill, merged)
    initial = skill.get("initialCooldown")
    levels = int(skill.get("maxLevel") or 0)
    minimum = None
    if isinstance(initial, int) and initial > 0:
        minimum = max(0, initial - max(0, levels - 1))
    report = {
        "skill_id": skill_id or None,
        "name": skill.get("name") or "",
        "base_cooldown": initial if isinstance(initial, int) and initial > 0 else None,
        "minimum_cooldown": minimum,
        "effects": [effect.model_dump() for effect in effects],
    }
    return effects, report


def _index(
    home: Path, commit: str, supplied: dict[int, dict[str, Any]] | None
) -> dict[int, dict[str, Any]]:
    if supplied is not None:
        return supplied
    path = _cached_skill_file(home, commit)
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        return {}
    indexed: dict[int, dict[str, Any]] = {}
    for skill in payload:
        if isinstance(skill, dict) and skill.get("id"):
            indexed[int(skill["id"])] = skill
    return indexed


def _index_status(index: dict[int, dict[str, Any]]) -> str:
    return "cached" if index else "missing"


def _cached_skill_file(home: Path, commit: str) -> Path | None:
    cache = home / "cache"
    if not cache.is_dir():
        return None
    matches: list[tuple[str, Path]] = []
    for meta_path in cache.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        url = str(meta.get("url") or "")
        if not url.endswith("/monsters-info/skill_en.json"):
            continue
        if commit and commit not in url:
            continue
        data = cache / meta_path.name.removesuffix(".json")
        if data.is_file():
            matches.append((str(meta.get("fetched_at") or ""), data))
    if not matches:
        return None
    matches.sort()
    return matches[-1][1]
