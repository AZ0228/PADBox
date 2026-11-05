"""Decode skill type/params into verified effects.

Compound actives (type 116) and leader skills (type 138) are lists of component
skill IDs. Layouts below were checked against repeated NA description pairings in
the pinned snapshot. A layout that does not match stays an unknown effect.
English description text is not parsed.
"""

from typing import Any

from pydantic import BaseModel, Field

FULL_CLEAR = 9999
DECODER_VERSION = "5"

ATTRIBUTES = {0: "fire", 1: "water", 2: "wood", 3: "light", 4: "dark"}
ATTRIBUTE_BITS = {1: "fire", 2: "water", 4: "wood", 8: "light", 16: "dark"}
# Same low bits as attributes, then heal and the hazard orbs used by conversions.
ORB_BITS = {
    1: "fire",
    2: "water",
    4: "wood",
    8: "light",
    16: "dark",
    32: "heal",
    64: "jammer",
    128: "poison",
    256: "lethal poison",
    512: "bomb",
}
# Target codes whose English descriptions agreed on every checked skill.
# Damage-cap targets whose descriptions agreed. Target 1 is this ally for a cap.
CAP_TARGETS = {
    1: "this ally",
    2: "the leader",
    3: "this ally and the leader",
    5: "this ally and the helper",
    6: "the leader and the helper",
    8: "all subs",
    9: "this ally and all subs",
    15: "all allies",
}
CARD_TYPES = {
    1: "Balanced",
    2: "Physical",
    3: "Healer",
    4: "Dragon",
    5: "God",
    6: "Attacker",
    7: "Devil",
    8: "Machine",
}
TEAM_ATK = {
    2: "the leader",
    3: "this ally and the leader",
    4: "the helper",
    5: "this ally and the helper",
    6: "the leader and the helper",
    8: "all subs",
    9: "this ally and all subs",
}
ORBS = {0: "fire", 1: "water", 2: "wood", 3: "light", 4: "dark", 5: "heal"}
# Scalar orb codes for a direct change. These are not the bit masks used above.
CHANGE_ORBS = {
    **ORBS,
    6: "jammer",
    7: "poison",
    8: "lethal poison",
}
BOARDS = {1: (7, 6), 2: (5, 4), 3: (6, 5)}
# One description token was present on every leader skill that set only this bit.
TRIGGER_BITS = {
    0: "two_pronged_attack",
    1: "damage_void_piercer",
    2: "l_attack",
    3: "cross_attack",
    4: "t_attack",
    5: "enhanced_fire_rows",
    6: "enhanced_water_rows",
    8: "enhanced_light_rows",
    9: "enhanced_dark_rows",
    10: "three_attribute_attack",
    11: "four_attribute_attack",
    12: "five_attribute_attack",
    15: "enhanced_fire_combos",
    16: "enhanced_water_combos",
    17: "enhanced_wood_combos",
    18: "enhanced_light_combos",
    19: "enhanced_dark_combos",
    21: "super_enhanced_matching",
    22: "fire_and_water_attack",
    23: "water_and_wood_attack",
    24: "wood_and_fire_attack",
}
ABSORB_KINDS = ("attribute", "combo", "damage")


class Effect(BaseModel):
    kind: str
    evidence: str
    source_type: int
    skill_id: int | None = None
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


def decode_skill(
    skill: dict[str, Any], index: dict[int, dict[str, Any]], seen: set[int] | None = None
) -> list[Effect]:
    """Flatten one skill. `index` maps every component skill ID to its raw record."""
    visiting = set() if seen is None else seen
    skill_id = int(skill.get("id") or 0)
    if skill_id and skill_id in visiting:
        return [_unknown(skill, "skill reference cycle")]
    if skill_id:
        visiting.add(skill_id)
    try:
        kind = int(skill.get("type") if skill.get("type") is not None else -1)
        params = [int(value) for value in skill.get("params") or []]
        if kind in {116, 138}:
            effects: list[Effect] = []
            for child_id in params:
                child = index.get(child_id)
                if child is None:
                    effects.append(
                        Effect(
                            kind="unknown",
                            evidence="unknown",
                            source_type=kind,
                            skill_id=skill_id or None,
                            summary=f"Component skill {child_id} is not in the local skill index",
                            details={"component_id": child_id},
                        )
                    )
                    continue
                effects.extend(decode_skill(child, index, visiting))
            return effects
        return _decode_leaf(skill_id, kind, params)
    finally:
        if skill_id:
            visiting.discard(skill_id)


def _decode_leaf(skill_id: int, kind: int, params: list[int]) -> list[Effect]:
    decoded = _LEAVES.get(kind, _unsupported)(kind, params)
    for effect in decoded:
        effect.skill_id = skill_id or None
    return decoded


def _unsupported(kind: int, params: list[int]) -> list[Effect]:
    return [_unknown_leaf(kind, params, f"Unrecognized skill type {kind}")]


def _unknown_leaf(kind: int, params: list[int], reason: str) -> Effect:
    return Effect(
        kind="unknown",
        evidence="unknown",
        source_type=kind,
        summary=reason,
        details={"params": params},
    )


def _unknown(skill: dict[str, Any], reason: str) -> Effect:
    kind = int(skill.get("type") or -1)
    params = [int(value) for value in skill.get("params") or []]
    effect = _unknown_leaf(kind, params, reason)
    effect.skill_id = int(skill.get("id") or 0) or None
    return effect


def _turns(value: int) -> dict[str, Any]:
    if value == FULL_CLEAR:
        return {"full": True}
    return {"full": False, "turns": value}


def _clear_summary(name: str, value: int) -> str:
    if value == FULL_CLEAR:
        return f"Fully clears {name}"
    return f"Clears {value} turns of {name}"


def _multiplier(hundredths: int) -> str:
    if hundredths % 100 == 0:
        return str(hundredths // 100)
    text = f"{hundredths / 100:.2f}".rstrip("0").rstrip(".")
    return text


def _positive(value: int) -> bool:
    return value > 0


def _recovery(kind: int, params: list[int]) -> list[Effect]:
    if not params or len(params) > 5:
        return [_unknown_leaf(kind, params, "Unrecognized recovery parameters")]
    bind = params[0]
    rcv = params[1] if len(params) > 1 else 0
    flat_hp = params[2] if len(params) > 2 else 0
    percent_hp = params[3] if len(params) > 3 else 0
    awoken = params[4] if len(params) > 4 else 0
    effects: list[Effect] = []
    if bind:
        effects.append(
            Effect(
                kind="bind_clear",
                evidence="verified",
                source_type=kind,
                summary=_clear_summary("bind", bind),
                details=_turns(bind),
            )
        )
    if rcv:
        effects.append(
            Effect(
                kind="heal_rcv",
                evidence="verified",
                source_type=kind,
                summary=f"Recovers {_multiplier(rcv)}x RCV as HP",
                details={"multiplier_hundredths": rcv},
            )
        )
    if flat_hp:
        effects.append(
            Effect(
                kind="heal_flat",
                evidence="verified",
                source_type=kind,
                summary=f"Recovers {flat_hp:,} HP",
                details={"hp": flat_hp},
            )
        )
    if percent_hp:
        effects.append(
            Effect(
                kind="heal_percent",
                evidence="verified",
                source_type=kind,
                summary=f"Recovers {percent_hp}% HP",
                details={"percent": percent_hp},
            )
        )
    if awoken:
        effects.append(
            Effect(
                kind="awoken_bind_clear",
                evidence="verified",
                source_type=kind,
                summary=_clear_summary("awoken bind", awoken),
                details=_turns(awoken),
            )
        )
    return effects or [_unknown_leaf(kind, params, "Recovery parameters have no effect")]


def _unmatchable(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized unmatchable-clear parameters")]
    return [
        Effect(
            kind="unmatchable_clear",
            evidence="verified",
            source_type=kind,
            summary=_clear_summary("unmatchable orbs", params[0]),
            details=_turns(params[0]),
        )
    ]


def _haste(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or params[0] != params[1] or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized skill-charge parameters")]
    return [
        Effect(
            kind="charge_other_skills",
            evidence="verified",
            source_type=kind,
            summary=f"Charges other skills by {params[0]} turns",
            details={"turns": params[0]},
        )
    ]


def _transform(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized transform parameters")]
    return [
        Effect(
            kind="transform",
            evidence="verified",
            source_type=kind,
            summary=f"Transforms into #{params[0]}",
            details={"monster_id": params[0]},
        )
    ]


def _void_pierce(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized damage-void parameters")]
    return [
        Effect(
            kind="void_pierce",
            evidence="verified",
            source_type=kind,
            summary=f"Pierces damage void for {params[0]} turns",
            details={"turns": params[0]},
        )
    ]


def _board(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _positive(params[0]) or params[1] not in BOARDS:
        return [_unknown_leaf(kind, params, "Unrecognized board-size parameters")]
    columns, rows = BOARDS[params[1]]
    return [
        Effect(
            kind="board_size",
            evidence="verified",
            source_type=kind,
            summary=f"Board becomes {columns}x{rows} for {params[0]} turns",
            details={"turns": params[0], "columns": columns, "rows": rows},
        )
    ]


def _absorb(kind: int, params: list[int]) -> list[Effect]:
    if not params or not _positive(params[0]) or len(params) > 4:
        return [_unknown_leaf(kind, params, "Unrecognized absorption parameters")]
    flags = params[1:]
    if not flags or any(flag not in {0, 1} for flag in flags):
        return [_unknown_leaf(kind, params, "Unrecognized absorption parameters")]
    if len(flags) == 1 and flags[0] != 1:
        return [_unknown_leaf(kind, params, "Unrecognized absorption parameters")]
    selected = [name for name, flag in zip(ABSORB_KINDS, flags, strict=False) if flag]
    if not selected:
        return [_unknown_leaf(kind, params, "Unrecognized absorption parameters")]
    label = " and ".join(name.replace("_", " ") for name in selected)
    return [
        Effect(
            kind="absorb_void",
            evidence="verified",
            source_type=kind,
            summary=f"Voids {label} absorption for {params[0]} turns",
            details={"turns": params[0], "kinds": selected},
        )
    ]


def _allies_atk(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or not _positive(params[0]) or not _positive(params[2]):
        return [_unknown_leaf(kind, params, "Unrecognized attack-buff target")]
    turns, target, multiplier = params
    if target == 15:
        return [
            Effect(
                kind="all_allies_atk",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(multiplier)}x ATK for all allies for {turns} turns",
                details={"turns": turns, "multiplier_hundredths": multiplier},
            )
        ]
    label = TEAM_ATK.get(target)
    if label is None:
        return [_unknown_leaf(kind, params, "Unrecognized attack-buff target")]
    return [
        Effect(
            kind="team_atk",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(multiplier)}x ATK for {label} for {turns} turns",
            details={"turns": turns, "target": label, "multiplier_hundredths": multiplier},
        )
    ]


def _attribute_atk(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or not _positive(params[0]) or not _positive(params[2]):
        return [_unknown_leaf(kind, params, "Unrecognized attribute attack buff")]
    if params[1] == 5:
        return [
            Effect(
                kind="rcv_buff",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(params[2])}x RCV for {params[0]} turns",
                details={"turns": params[0], "multiplier_hundredths": params[2]},
            )
        ]
    if params[1] not in ATTRIBUTES:
        return [_unknown_leaf(kind, params, "Unrecognized attribute attack buff")]
    attribute = ATTRIBUTES[params[1]]
    return [
        Effect(
            kind="attribute_atk",
            evidence="verified",
            source_type=kind,
            summary=(f"{_multiplier(params[2])}x ATK for {attribute} for {params[0]} turns"),
            details={
                "turns": params[0],
                "attribute": attribute,
                "multiplier_hundredths": params[2],
            },
        )
    ]


def _mass_attack(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or params[0] not in ATTRIBUTES or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized mass-attack parameters")]
    attribute = ATTRIBUTES[params[0]]
    return [
        Effect(
            kind="mass_attack",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[1])}x ATK {attribute} mass attack",
            details={"attribute": attribute, "multiplier_hundredths": params[1]},
        )
    ]


def _orb_bit_names(mask: int) -> list[str] | None:
    if mask <= 0 or mask & ~1023:
        return None
    return [name for bit, name in ORB_BITS.items() if mask & bit]


def _create_orbs(kind: int, params: list[int]) -> list[Effect]:
    # Two equal color fields, or one field, create that many of each listed orb.
    # A larger second field changes that many orbs which are not the extra colors.
    if len(params) == 2 and _positive(params[0]):
        return _spawn_orbs(kind, params[0], params[1])
    if len(params) != 3 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized orb creation")]
    if params[1] == params[2]:
        return _spawn_orbs(kind, params[0], params[1])
    if params[1] and params[1] & ~params[2] == 0:
        destination = _orb_bit_names(params[1])
        excluded = _orb_bit_names(params[2] & ~params[1])
        if destination and excluded:
            return [
                Effect(
                    kind="orb_replace",
                    evidence="verified",
                    source_type=kind,
                    summary=(
                        f"Changes {params[0]} orbs that are not {' or '.join(excluded)} "
                        f"to {', '.join(destination)}"
                    ),
                    details={"count": params[0], "excluded": excluded, "to": destination},
                )
            ]
    return [_unknown_leaf(kind, params, "Unrecognized orb creation")]


def _spawn_orbs(kind: int, count: int, mask: int) -> list[Effect]:
    orbs = _orb_bit_names(mask)
    if not orbs:
        return [_unknown_leaf(kind, [count, mask], "Unrecognized orb color in orb creation")]
    if len(orbs) == 1:
        summary = f"Creates {count} {orbs[0]} at random"
    else:
        summary = f"Creates {count} of each of {', '.join(orbs)} at random"
    return [
        Effect(
            kind="create_orbs",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"count": count, "attributes": orbs},
        )
    ]


def _orb_convert(kind: int, params: list[int]) -> list[Effect]:
    # One destination color. Several destinations are not one repeated meaning.
    if len(params) != 2:
        return [_unknown_leaf(kind, params, "Unrecognized orb conversion")]
    source = _orb_bit_names(params[0])
    destination = _orb_bit_names(params[1])
    if not source or not destination or len(destination) != 1:
        return [_unknown_leaf(kind, params, "Unrecognized orb conversion")]
    return [
        Effect(
            kind="orb_convert",
            evidence="verified",
            source_type=kind,
            summary=f"Changes {', '.join(source)} to {destination[0]}",
            details={"from": source, "to": destination},
        )
    ]


def _board_change(kind: int, params: list[int]) -> list[Effect]:
    colors: list[str] = []
    for code in params:
        if code == -1:
            break
        name = ORBS.get(code)
        if name is None:
            return [_unknown_leaf(kind, params, "Unrecognized orb color in a board change")]
        colors.append(name)
    if not colors:
        return [_unknown_leaf(kind, params, "Board change lists no orbs")]
    return [
        Effect(
            kind="board_change",
            evidence="verified",
            source_type=kind,
            summary="Changes the board to " + ", ".join(colors),
            details={"orbs": colors},
        )
    ]


def _move_time(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not 1 <= params[0] <= 30:
        return [_unknown_leaf(kind, params, "Unrecognized fixed move time")]
    return [
        Effect(
            kind="move_time",
            evidence="verified",
            source_type=kind,
            summary=f"Move time is {params[0]} seconds",
            details={"seconds": params[0]},
        )
    ]


def _move_time_bonus(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]) or params[0] % 100:
        return [_unknown_leaf(kind, params, "Unrecognized move-time bonus")]
    seconds = params[0] // 100
    return [
        Effect(
            kind="move_time_bonus",
            evidence="verified",
            source_type=kind,
            summary=f"Orb move time +{seconds} seconds",
            details={"seconds": seconds},
        )
    ]


def _rank_exp(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized rank-experience parameters")]
    return [
        Effect(
            kind="rank_exp",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[0])}x Rank EXP when entering as leader",
            details={"multiplier_hundredths": params[0]},
        )
    ]


def _attribute_names(mask: int) -> list[str] | None:
    if mask <= 0 or mask & ~0b11111:
        return None
    return [name for bit, name in ATTRIBUTE_BITS.items() if mask & bit]


def _linked_shield(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 4 or not _positive(params[1]) or not 0 <= params[3] <= 100:
        return [_unknown_leaf(kind, params, "Unrecognized linked-orb leader parameters")]
    attributes = _attribute_names(params[0])
    if attributes is None or params[2] < 0:
        return [_unknown_leaf(kind, params, "Unrecognized linked-orb leader parameters")]
    parts = [f"{params[3]}% damage reduction"]
    if params[2] not in {0, 100}:
        parts.insert(0, f"{_multiplier(params[2])}x ATK")
    summary = f"{' and '.join(parts)} with {params[1]}+ linked {'/'.join(attributes)} orbs"
    return [
        Effect(
            kind="linked_orb_leader",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={
                "attributes": attributes,
                "minimum_linked": params[1],
                "atk_multiplier_hundredths": params[2],
                "shield_percent": params[3],
            },
        )
    ]


def _trigger_bonus(kind: int, params: list[int]) -> list[Effect]:
    if not params or len(params) > 6:
        return [_unknown_leaf(kind, params, "Unrecognized awakening-trigger parameters")]
    bits = params[0]
    known = [TRIGGER_BITS[bit] for bit in range(32) if bits & (1 << bit) and bit in TRIGGER_BITS]
    unknown_bits = [bit for bit in range(32) if bits & (1 << bit) and bit not in TRIGGER_BITS]
    if len(known) != 1 or unknown_bits:
        return [_unknown_leaf(kind, params, "Awakening trigger is not a single verified bit")]
    atk = params[1] if len(params) > 1 else 0
    shield = params[2] if len(params) > 2 else 0
    combos = params[3] if len(params) > 3 else 0
    fixed = params[4] if len(params) > 4 else 0
    rcv = params[5] if len(params) > 5 else 0
    if atk < 0 or shield < 0 or combos < 0 or fixed < 0 or rcv < 0 or shield > 100:
        return [_unknown_leaf(kind, params, "Unrecognized awakening-trigger parameters")]
    clauses: list[str] = []
    if atk:
        clauses.append(f"{_multiplier(atk)}x ATK")
    if rcv:
        clauses.append(f"{_multiplier(rcv)}x RCV")
    if shield:
        clauses.append(f"{shield}% damage reduction")
    if combos:
        clauses.append(f"+{combos} combos")
    if fixed:
        clauses.append(f"{fixed:,} fixed damage")
    if not clauses:
        return [_unknown_leaf(kind, params, "Awakening trigger has no verified bonus")]
    trigger = known[0].replace("_", " ")
    return [
        Effect(
            kind="awakening_trigger",
            evidence="verified",
            source_type=kind,
            summary=f"When {trigger} is used: {', '.join(clauses)}",
            details={
                "trigger": known[0],
                "atk_multiplier_hundredths": atk,
                "rcv_multiplier_hundredths": rcv,
                "shield_percent": shield,
                "added_combos": combos,
                "fixed_damage": fixed,
            },
        )
    ]


def _for_turns(turns: int) -> str:
    unit = "turn" if turns == 1 else "turns"
    return f"for {turns} {unit}"


def _percent_guard(turns: int, percent: int) -> bool:
    return _positive(turns) and 1 <= percent <= 100


def _damage_shield(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _percent_guard(params[0], params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized damage-shield parameters")]
    turns, percent = params
    if percent == 100:
        summary = f"Voids damage {_for_turns(turns)}"
    else:
        summary = f"Reduces damage by {percent}% {_for_turns(turns)}"
    return [
        Effect(
            kind="damage_shield",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"turns": turns, "percent": percent},
        )
    ]


def _gravity(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not 1 <= params[0] <= 100:
        return [_unknown_leaf(kind, params, "Unrecognized gravity parameters")]
    return [
        Effect(
            kind="gravity",
            evidence="verified",
            source_type=kind,
            summary=f"Reduces enemies' remaining HP by {params[0]}%",
            details={"percent": params[0]},
        )
    ]


def _delay(kind: int, params: list[int]) -> list[Effect]:
    turns = None
    if len(params) == 1 and _positive(params[0]):
        turns = params[0]
    elif len(params) == 2 and params[0] == params[1] and _positive(params[0]):
        turns = params[0]
    if turns is None:
        return [_unknown_leaf(kind, params, "Unrecognized delay parameters")]
    return [
        Effect(
            kind="delay",
            evidence="verified",
            source_type=kind,
            summary=f"Delays enemies' next move {_for_turns(turns)}",
            details={"turns": turns},
        )
    ]


def _defense_break(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _percent_guard(params[0], params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized defense-break parameters")]
    turns, percent = params
    if percent == 100:
        summary = f"Voids enemies' defense {_for_turns(turns)}"
    else:
        summary = f"Reduces enemies' defense by {percent}% {_for_turns(turns)}"
    return [
        Effect(
            kind="defense_break",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"turns": turns, "percent": percent},
        )
    ]


def _added_combos(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _positive(params[0]) or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized combo-bonus parameters")]
    turns, combos = params
    noun = "combo" if combos == 1 else "combos"
    return [
        Effect(
            kind="added_combos",
            evidence="verified",
            source_type=kind,
            summary=f"Adds {combos} {noun} {_for_turns(turns)}",
            details={"turns": turns, "combos": combos},
        )
    ]


def _timed_move(kind: int, params: list[int]) -> list[Effect]:
    # Two parameters are added seconds in tenths. Three are a multiplier in hundredths.
    if len(params) == 2 and _positive(params[0]) and params[1] != 0:
        tenths = params[1]
        amount = tenths // 10 if tenths % 10 == 0 else round(tenths / 10, 1)
        number = str(amount)
        unit = "second" if abs(tenths) == 10 else "seconds"
        sign = "+" if tenths > 0 else ""
        return [
            Effect(
                kind="move_time_buff",
                evidence="verified",
                source_type=kind,
                summary=f"Orb move time {sign}{number} {unit} {_for_turns(params[0])}",
                details={"turns": params[0], "seconds": amount},
            )
        ]
    if len(params) == 3 and params[1] == 0 and _positive(params[0]) and _positive(params[2]):
        return [
            Effect(
                kind="move_time_buff",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(params[2])}x orb move time {_for_turns(params[0])}",
                details={"turns": params[0], "multiplier_hundredths": params[2]},
            )
        ]
    return [_unknown_leaf(kind, params, "Unrecognized timed move-time parameters")]


def _paired_atk(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 4 or not _positive(params[0]) or not _positive(params[3]):
        return [_unknown_leaf(kind, params, "Unrecognized paired attack parameters")]
    turns, first, second, multiplier = params
    if first not in range(6) or second not in range(6) or (first == 5 and second == 5):
        return [_unknown_leaf(kind, params, "Unrecognized paired attack parameters")]
    effects: list[Effect] = []
    seen: list[str] = []
    for slot in (first, second):
        if slot == 5 or ATTRIBUTES[slot] in seen:
            continue
        attribute = ATTRIBUTES[slot]
        seen.append(attribute)
        effects.append(
            Effect(
                kind="attribute_atk",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(multiplier)}x ATK for {attribute} {_for_turns(turns)}",
                details={
                    "turns": turns,
                    "attribute": ATTRIBUTES[slot],
                    "multiplier_hundredths": multiplier,
                },
            )
        )
    if 5 in {first, second}:
        effects.append(
            Effect(
                kind="rcv_buff",
                evidence="verified",
                source_type=kind,
                summary=f"{_multiplier(multiplier)}x RCV {_for_turns(turns)}",
                details={"turns": turns, "multiplier_hundredths": multiplier},
            )
        )
    return effects


def _orb_change(kind: int, params: list[int]) -> list[Effect]:
    # Three parameters omit the second destination, which is fire on every checked skill.
    if len(params) == 4:
        raw_pairs = [(params[0], params[1]), (params[2], params[3])]
    elif len(params) == 3:
        raw_pairs = [(params[0], params[1]), (params[2], 0)]
    else:
        return [_unknown_leaf(kind, params, "Unrecognized orb change")]
    pairs: list[tuple[str, str]] = []
    for source_code, destination_code in raw_pairs:
        source = CHANGE_ORBS.get(source_code)
        destination = CHANGE_ORBS.get(destination_code)
        if source is None or destination is None:
            return [_unknown_leaf(kind, params, "Unrecognized orb change")]
        pairs.append((source, destination))
    if len({destination for _, destination in pairs}) == 1:
        sources = ", ".join(source for source, _ in pairs)
        summary = f"Changes {sources} to {pairs[0][1]}"
    else:
        changed = " and ".join(f"{source} to {destination}" for source, destination in pairs)
        summary = f"Changes {changed}"
    destinations: list[str] = []
    for _, destination in pairs:
        if destination not in destinations:
            destinations.append(destination)
    return [
        Effect(
            kind="orb_change",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={
                "changes": [{"from": source, "to": destination} for source, destination in pairs],
                "to": destinations,
            },
        )
    ]


def _billion_amount(value: int) -> str:
    if value % 10:
        return f"{value / 10:.1f}"
    return str(value // 10)


def _damage_cap(kind: int, params: list[int]) -> list[Effect]:
    if len(params) == 2:
        turns, amount, target = params[0], params[1], 1
    elif len(params) == 3:
        turns, amount, target = params
    else:
        return [_unknown_leaf(kind, params, "Unrecognized damage-cap parameters")]
    label = CAP_TARGETS.get(target)
    if label is None or not _positive(turns) or not _positive(amount):
        return [_unknown_leaf(kind, params, "Unrecognized damage-cap parameters")]
    return [
        Effect(
            kind="damage_cap",
            evidence="verified",
            source_type=kind,
            summary=(
                f"Sets damage cap to {_billion_amount(amount)} billion for {label} "
                f"{_for_turns(turns)}"
            ),
            details={"turns": turns, "billion_tenths": amount, "target": label},
        )
    ]


def _self_damage_cap(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2:
        return [_unknown_leaf(kind, params, "Unrecognized damage-cap parameters")]
    return _damage_cap(kind, [params[0], params[1], 1])


def _orb_enhance(kind: int, params: list[int]) -> list[Effect]:
    # The second value is 6 on every checked enhance.
    if len(params) != 2 or params[1] != 6:
        return [_unknown_leaf(kind, params, "Unrecognized orb enhance")]
    orbs = _orb_bit_names(params[0])
    if not orbs or params[0] & ~63:
        return [_unknown_leaf(kind, params, "Unrecognized orb enhance")]
    if params[0] == 63:
        summary = "Enhances all orbs"
    elif len(orbs) == 1:
        summary = f"Enhances {orbs[0]} orbs"
    else:
        summary = f"Enhances {', '.join(orbs[:-1])} and {orbs[-1]} orbs"
    return [
        Effect(
            kind="orb_enhance",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"orbs": orbs},
        )
    ]


def _type_atk(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or not _positive(params[0]) or not _positive(params[2]):
        return [_unknown_leaf(kind, params, "Unrecognized type-attack parameters")]
    typing = CARD_TYPES.get(params[1])
    if typing is None:
        return [_unknown_leaf(kind, params, "Unrecognized type-attack parameters")]
    return [
        Effect(
            kind="type_atk",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[2])}x ATK for {typing} {_for_turns(params[0])}",
            details={
                "turns": params[0],
                "card_type": typing,
                "multiplier_hundredths": params[2],
            },
        )
    ]


def _heal_over_time(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or params[1] != 0 or not _percent_guard(params[0], params[2]):
        return [_unknown_leaf(kind, params, "Unrecognized regeneration parameters")]
    turns, percent = params[0], params[2]
    if percent == 100:
        summary = f"Fully recovers HP {_for_turns(turns)}"
    else:
        summary = f"Recovers {percent}% HP {_for_turns(turns)}"
    return [
        Effect(
            kind="heal_over_time",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"turns": turns, "percent": percent},
        )
    ]


def _board_refresh(kind: int, params: list[int]) -> list[Effect]:
    if params:
        return [_unknown_leaf(kind, params, "Unrecognized board refresh")]
    return [
        Effect(
            kind="board_refresh",
            evidence="verified",
            source_type=kind,
            summary="Replaces all orbs",
            details={},
        )
    ]


def _hp_damage(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not 1 <= params[0] <= 100:
        return [_unknown_leaf(kind, params, "Unrecognized HP-damage parameters")]
    return [
        Effect(
            kind="hp_damage",
            evidence="verified",
            source_type=kind,
            summary=f"Inflicts damage equal to {params[0]}% of enemies' HP",
            details={"percent": params[0]},
        )
    ]


_COLUMN_BITS = (
    (1, "the leftmost column"),
    (2, "the 2nd column from the left"),
    (4, "the 3rd column from the left"),
    (8, "the 3rd column from the right"),
    (16, "the 2nd column from the right"),
    (32, "the rightmost column"),
)
_COLUMN_GROUPS = {
    3: "the 2 leftmost columns",
    7: "the 3 leftmost columns",
    12: "the 3rd column from the left and the 3rd from the right",
    18: "the 2nd columns from the left and the right",
    33: "the outermost columns",
    35: "the rightmost and the 2 leftmost columns",
    37: "the outermost columns and the 3rd from the left",
    48: "the 2 rightmost columns",
    49: "the leftmost and the 2 rightmost columns",
    51: "the 2 leftmost and the 2 rightmost columns",
    56: "the 3 rightmost columns",
}
_ROW_BITS = (
    (1, "the top row"),
    (2, "the 2nd row from the top"),
    (4, "the 3rd row from the top"),
    (8, "the 2nd row from the bottom"),
    (16, "the bottom row"),
)
_ROW_GROUPS = {
    3: "the 2 top rows",
    10: "the 2nd rows from the top and bottom",
    17: "the top and bottom rows",
    19: "the 2 top rows and the bottom row",
    24: "the 2 bottom rows",
    27: "the 2 top rows and the 2 bottom rows",
}


def _place_label(
    mask: int, bits: tuple[tuple[int, str], ...], groups: dict[int, str]
) -> str | None:
    allowed = sum(bit for bit, _ in bits)
    if mask <= 0 or mask & ~allowed:
        return None
    grouped = groups.get(mask)
    if grouped:
        return grouped
    names = [label for bit, label in bits if mask & bit]
    if len(names) == 1:
        return names[0]
    return " and ".join(names)


def _orb_destination(mask: int) -> tuple[str, list[str]] | None:
    orbs = _orb_bit_names(mask)
    if not orbs:
        return None
    if len(orbs) == 1:
        return orbs[0], orbs
    listed = ", ".join(orbs[:-1]) + " and " + orbs[-1]
    return f"{listed} at random", orbs


def _line_change(
    kind: int,
    params: list[int],
    bits: tuple[tuple[int, str], ...],
    groups: dict[int, str],
    effect_kind: str,
) -> list[Effect]:
    if len(params) not in {2, 4, 6} or len(params) % 2:
        return [_unknown_leaf(kind, params, "Unrecognized line change")]
    clauses: list[str] = []
    lines: list[dict[str, Any]] = []
    destinations: list[str] = []
    for index in range(0, len(params), 2):
        place = _place_label(params[index], bits, groups)
        destination = _orb_destination(params[index + 1])
        if place is None or destination is None:
            return [_unknown_leaf(kind, params, "Unrecognized line change")]
        phrase, orbs = destination
        clauses.append(f"{place} to {phrase}")
        lines.append({"where": place, "orbs": orbs})
        for orb in orbs:
            if orb not in destinations:
                destinations.append(orb)
    if len(clauses) == 1:
        summary = f"Changes {clauses[0]}"
    else:
        summary = "Changes " + " and ".join(clauses)
    return [
        Effect(
            kind=effect_kind,
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"lines": lines, "to": destinations},
        )
    ]


def _lock_clear(kind: int, params: list[int]) -> list[Effect]:
    # Empty parameters, and a single 1, both say the locks are removed.
    if params not in ([], [1]):
        return [_unknown_leaf(kind, params, "Unrecognized lock-clear parameters")]
    return [
        Effect(
            kind="lock_clear",
            evidence="verified",
            source_type=kind,
            summary="Removes locks",
            details={"full": True},
        )
    ]


def _no_skyfall(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not _positive(params[0]):
        return [_unknown_leaf(kind, params, "Unrecognized no-skyfall parameters")]
    return [
        Effect(
            kind="no_skyfall",
            evidence="verified",
            source_type=kind,
            summary=f"No skyfall combos {_for_turns(params[0])}",
            details={"turns": params[0]},
        )
    ]


def _max_hp(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or not _positive(params[0]) or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized maximum-HP parameters")]
    return [
        Effect(
            kind="max_hp",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[1])}x maximum HP {_for_turns(params[0])}",
            details={"turns": params[0], "multiplier_hundredths": params[1]},
        )
    ]


def _passive_attribute(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or params[0] not in ATTRIBUTES or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized attribute multiplier")]
    attribute = ATTRIBUTES[params[0]]
    return [
        Effect(
            kind="attribute_multiplier",
            evidence="verified",
            source_type=kind,
            summary=f"{_multiplier(params[1])}x ATK for {attribute}",
            details={"attribute": attribute, "multiplier_hundredths": params[1]},
        )
    ]


def _attribute_guard(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 3 or not _percent_guard(params[0], params[2]) or params[1] not in ATTRIBUTES:
        return [_unknown_leaf(kind, params, "Unrecognized attribute-guard parameters")]
    turns, attribute, percent = params[0], ATTRIBUTES[params[1]], params[2]
    if percent == 100:
        summary = f"Voids {attribute} damage {_for_turns(turns)}"
    else:
        summary = f"Reduces {attribute} damage by {percent}% {_for_turns(turns)}"
    return [
        Effect(
            kind="attribute_guard",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"turns": turns, "attribute": attribute, "percent": percent},
        )
    ]


def _direct_change(kind: int, params: list[int]) -> list[Effect]:
    if len(params) == 1:
        coded = [(params[0], 0)]
    elif len(params) == 2:
        coded = [(params[0], params[1])]
    else:
        return [_unknown_leaf(kind, params, "Unrecognized orb change")]
    pairs: list[tuple[str, str]] = []
    for source_code, destination_code in coded:
        source = CHANGE_ORBS.get(source_code)
        destination = CHANGE_ORBS.get(destination_code)
        if source is None or destination is None:
            return [_unknown_leaf(kind, params, "Unrecognized orb change")]
        pairs.append((source, destination))
    summary = f"Changes {pairs[0][0]} to {pairs[0][1]}"
    return [
        Effect(
            kind="orb_change",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"changes": [{"from": pairs[0][0], "to": pairs[0][1]}], "to": [pairs[0][1]]},
        )
    ]


def _leader_swap(kind: int, params: list[int]) -> list[Effect]:
    if params:
        return [_unknown_leaf(kind, params, "Unrecognized leader-swap parameters")]
    return [
        Effect(
            kind="leader_swap",
            evidence="verified",
            source_type=kind,
            summary="Switches places with the leader. Use again to switch back",
            details={},
        )
    ]


def _shield_break(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 1 or not 1 <= params[0] <= 100:
        return [_unknown_leaf(kind, params, "Unrecognized shield parameters")]
    if params[0] == 100:
        summary = "Destroys 1 shield"
        details: dict[str, Any] = {"shields": 1}
    else:
        summary = f"Reduces enemy shield by {params[0]}%"
        details = {"percent": params[0]}
    return [
        Effect(
            kind="shield_break",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details=details,
        )
    ]


_ROULETTE = {
    100: "roulette",
    50: "rapid roulette",
    30: "ultra rapid roulette",
    150: "slow roulette",
}


def _roulette(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 8 or any(params[index] != 0 for index in range(2, 7)):
        return [_unknown_leaf(kind, params, "Unrecognized roulette parameters")]
    turns, speed, count = params[0], params[1], params[7]
    name = _ROULETTE.get(speed)
    if name is None or not _positive(turns) or not _positive(count):
        return [_unknown_leaf(kind, params, "Unrecognized roulette parameters")]
    noun = "orb" if count == 1 else "orbs"
    return [
        Effect(
            kind="roulette",
            evidence="verified",
            source_type=kind,
            summary=f"Creates {count} {name} {noun} {_for_turns(turns)}",
            details={"turns": turns, "count": count, "speed": name},
        )
    ]


def _self_attribute(kind: int, params: list[int]) -> list[Effect]:
    if len(params) == 1 and _positive(params[0]):
        turns, attribute = params[0], "fire"
    elif len(params) == 2 and _positive(params[0]) and params[1] in ATTRIBUTES:
        turns, attribute = params[0], ATTRIBUTES[params[1]]
    else:
        return [_unknown_leaf(kind, params, "Unrecognized attribute-change parameters")]
    return [
        Effect(
            kind="self_attribute",
            evidence="verified",
            source_type=kind,
            summary=f"This ally's attribute changes to {attribute} {_for_turns(turns)}",
            details={"turns": turns, "attribute": attribute},
        )
    ]


def _single_attack(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2 or params[0] not in ATTRIBUTES or not _positive(params[1]):
        return [_unknown_leaf(kind, params, "Unrecognized single-attack parameters")]
    attribute = ATTRIBUTES[params[0]]
    return [
        Effect(
            kind="single_attack",
            evidence="verified",
            source_type=kind,
            summary=f"Inflicts {_multiplier(params[1])}x {attribute} ATK on 1 enemy",
            details={"attribute": attribute, "multiplier_hundredths": params[1]},
        )
    ]


def _hp_cost(kind: int, params: list[int]) -> list[Effect]:
    if not params:
        return [
            Effect(
                kind="hp_cost",
                evidence="verified",
                source_type=kind,
                summary="Reduces HP to 1",
                details={"to_one": True},
            )
        ]
    if len(params) != 1 or not 1 <= params[0] <= 99:
        return [_unknown_leaf(kind, params, "Unrecognized HP-cost parameters")]
    if params[0] == 50:
        summary = "Halves HP"
    else:
        summary = f"Reduces HP by {100 - params[0]}%"
    return [
        Effect(
            kind="hp_cost",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={"remaining_percent": params[0]},
        )
    ]


def _combo_orbs(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 2:
        return [_unknown_leaf(kind, params, "Unrecognized combo-orb parameters")]
    if params[0] == 63 and _positive(params[1]):
        noun = "orb" if params[1] == 1 else "orbs"
        return [
            Effect(
                kind="create_combo_orbs",
                evidence="verified",
                source_type=kind,
                summary=f"Creates {params[1]} combo {noun}",
                details={"count": params[1]},
            )
        ]
    attribute = ATTRIBUTE_BITS.get(params[0])
    if attribute and params[1] == 42:
        return [
            Effect(
                kind="combo_convert",
                evidence="verified",
                source_type=kind,
                summary=f"{attribute.capitalize()} orbs become combo orbs",
                details={"from": attribute},
            )
        ]
    return [_unknown_leaf(kind, params, "Unrecognized combo-orb parameters")]


def _turn_span(start: int, end: int) -> str:
    if start == end:
        return _for_turns(start)
    return f"for {start} to {end} turns"


def _skyfall(kind: int, params: list[int]) -> list[Effect]:
    if len(params) != 4 or not _positive(params[3]):
        return [_unknown_leaf(kind, params, "Unrecognized skyfall parameters")]
    orbs = _orb_bit_names(params[0])
    start, end, rate = params[1], params[2], params[3]
    if not orbs or not _positive(start) or not _positive(end) or start > end:
        return [_unknown_leaf(kind, params, "Unrecognized skyfall parameters")]
    listed = orbs[0] if len(orbs) == 1 else ", ".join(orbs[:-1]) + " and " + orbs[-1]
    span = _turn_span(start, end)
    if rate == 100:
        summary = f"Only {listed} orbs appear {span}"
        mode = "only"
    else:
        summary = f"{listed.capitalize()} orbs appear more often {span}"
        mode = "increased"
    return [
        Effect(
            kind="skyfall",
            evidence="verified",
            source_type=kind,
            summary=summary,
            details={
                "orbs": orbs,
                "to": orbs,
                "start_turn": start,
                "end_turn": end,
                "rate": rate,
                "mode": mode,
            },
        )
    ]


def _column_change(kind: int, params: list[int]) -> list[Effect]:
    return _line_change(kind, params, _COLUMN_BITS, _COLUMN_GROUPS, "column_change")


def _row_change(kind: int, params: list[int]) -> list[Effect]:
    return _line_change(kind, params, _ROW_BITS, _ROW_GROUPS, "row_change")


_LEAVES = {
    0: _mass_attack,
    3: _damage_shield,
    6: _gravity,
    9: _direct_change,
    10: _board_refresh,
    11: _passive_attribute,
    15: _move_time_bonus,
    18: _delay,
    19: _defense_break,
    20: _orb_change,
    21: _attribute_guard,
    37: _single_attack,
    50: _attribute_atk,
    71: _board_change,
    88: _type_atk,
    90: _paired_atk,
    93: _leader_swap,
    126: _skyfall,
    127: _column_change,
    128: _row_change,
    132: _timed_move,
    140: _orb_enhance,
    142: _self_attribute,
    141: _create_orbs,
    154: _orb_convert,
    117: _recovery,
    146: _haste,
    148: _rank_exp,
    160: _added_combos,
    172: _lock_clear,
    161: _hp_damage,
    173: _absorb,
    178: _move_time,
    179: _heal_over_time,
    184: _no_skyfall,
    182: _linked_shield,
    190: _combo_orbs,
    191: _void_pierce,
    195: _hp_cost,
    196: _unmatchable,
    202: _transform,
    207: _roulette,
    230: _allies_atk,
    237: _max_hp,
    241: _self_damage_cap,
    244: _board,
    258: _damage_cap,
    259: _shield_break,
    271: _trigger_bonus,
}
