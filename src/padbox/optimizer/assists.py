"""Assists for one shortlisted team, chosen with that team's skill timing.

The lineup is already fixed. This step equips owned assists onto hosts whose
base active the timing plan does not cast. Charge follows the simulator: skill
boost applies once, and an assist spends the host cooldown before its own.
``evaluate_team`` then scores the assisted team. This search does not roll
the board and does not try every equip permutation.
"""

import json
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass

from padbox.database.store import Store
from padbox.mechanics.awakenings import NAMES, capped
from padbox.mechanics.decode import ATTRIBUTES
from padbox.optimizer.candidates import Candidate, CandidatePool
from padbox.optimizer.search import SearchedTeam, TeamMember

_ACTIVE_SKIP = {"skill_boost", "damage"}
_HAZARDS = (
    ("bind_resist_percent", 100),
    ("skill_bind_resist_percent", 100),
    ("blind_resist_percent", 100),
    ("jammer_resist_percent", 100),
    ("poison_resist_percent", 100),
    ("cloud_resist", 1),
    ("tape_resist", 1),
)
_DELAY_RESIST = next(
    awakening_id for awakening_id, name in NAMES.items() if name == "Skill Delay Resistance"
)
_SUB_CHANGE = {
    awakening_id: color
    for awakening_id, name in NAMES.items()
    if name.startswith("Change Sub Attribute: ")
    for color in [name.removeprefix("Change Sub Attribute: ").lower()]
}


@dataclass
class _Host:
    role: str
    order: int
    candidate: Candidate
    awakenings: list[int]
    sub_attribute: str | None
    sub_known: bool


@dataclass
class _Facts:
    awakenings: list[int]
    sub_attribute: str | None
    sub_known: bool


def attach_assists(
    store: Store,
    pool: CandidatePool,
    teams: list[SearchedTeam],
    *,
    turns: int,
) -> None:
    """Equip assists on each shortlisted team. The caller evaluates afterwards."""
    if not teams:
        return
    facts = _facts(store, {row.form_id for row in pool.candidates})
    assists = [
        row
        for row in pool.candidates
        if row.kind == "assist" and row.equip and not row.borrowed
    ]
    assists.sort(key=_copy_key)
    box = _box_counts(store)
    colors = _leader_colors(pool.essential_roles)
    for team in teams:
        _attach_one(pool, team, assists, facts, box, colors, turns)


def _attach_one(
    pool: CandidatePool,
    team: SearchedTeam,
    assists: list[Candidate],
    facts: dict[int, _Facts],
    box: Counter[int],
    colors: set[str],
    turns: int,
) -> None:
    hosts, used, remaining = _hosts(pool, team, facts, box)
    if hosts is None or remaining is None:
        team.assist_notes.append("Assist search skipped this team: a fielded copy is missing.")
        return
    active = _active_roles(pool.essential_roles)
    base_boost = _boost(hosts, {})
    protected = _protected(hosts, active, turns, base_boost)
    for index in sorted(protected):
        host = hosts[index]
        if _transforms(host.candidate):
            reason = "its transform is the base active"
        else:
            reason = "the rotation casts this base active"
        team.assist_notes.append(f"Kept {host.role} #{host.candidate.form_id}: {reason}.")
    chosen: dict[int, Candidate] = {}
    current = _score(hosts, chosen, active, colors, facts, turns, pool.transform_turns)
    while True:
        pick: tuple[tuple[float, ...], int, Candidate] | None = None
        for index in range(len(hosts)):
            if index in protected or index in chosen:
                continue
            for assist in assists:
                trial = dict(chosen)
                trial[index] = assist
                if not _legal(hosts, trial, remaining, used, colors, facts):
                    continue
                score = _score(hosts, trial, active, colors, facts, turns, pool.transform_turns)
                if score <= current:
                    continue
                if pick is None or score > pick[0]:
                    pick = (score, index, assist)
        if pick is None:
            break
        chosen[pick[1]] = pick[2]
        current = pick[0]
    _write(team, hosts, chosen, facts, colors, turns, _boost(hosts, chosen))


def _hosts(
    pool: CandidatePool,
    team: SearchedTeam,
    facts: dict[int, _Facts],
    box: Counter[int],
) -> tuple[list[_Host] | None, set[tuple[int, int]], Counter[int] | None]:
    leader = next((row for row in pool.candidates if row.kind == "leader"), None)
    helper = _find(
        pool,
        owned_id=team.helper.owned_id,
        copy_index=team.helper.copy_index,
        form_id=team.helper.form_id,
        kind="helper",
    )
    subs = [
        _find(
            pool,
            owned_id=sub.owned_id,
            copy_index=sub.copy_index,
            form_id=sub.form_id,
            kind="sub",
        )
        for sub in team.subs
    ]
    if leader is None or helper is None or any(sub is None for sub in subs):
        return None, set(), None
    found_subs = [sub for sub in subs if sub is not None]
    remaining = box.copy()
    used: set[tuple[int, int]] = set()
    for row, borrowed in (
        (leader, False),
        (helper, helper.borrowed),
        *[(sub, False) for sub in found_subs],
    ):
        updated = _consume(remaining, row, borrowed=borrowed)
        if updated is None:
            return None, set(), None
        remaining = updated
        if not borrowed:
            used.add((row.owned_id, row.copy_index))
    hosts = [_host("leader", 0, leader, facts)]
    for index, sub in enumerate(found_subs):
        hosts.append(_host(f"sub {index + 1}", index + 1, sub, facts))
    hosts.append(_host("helper", len(found_subs) + 1, helper, facts))
    return hosts, used, remaining


def _host(role: str, order: int, candidate: Candidate, facts: dict[int, _Facts]) -> _Host:
    card = facts.get(candidate.form_id)
    return _Host(
        role=role,
        order=order,
        candidate=candidate,
        awakenings=[] if card is None else list(card.awakenings),
        sub_attribute=None if card is None else card.sub_attribute,
        sub_known=False if card is None else card.sub_known,
    )


def _protected(
    hosts: list[_Host],
    active: set[str],
    turns: int,
    boost: int,
) -> set[int]:
    """Hosts whose base active the unassisted plan casts, plus every transform."""
    protected = {index for index, host in enumerate(hosts) if _transforms(host.candidate)}
    unmet = set(active)
    events: list[tuple[int, int, int]] = []
    for index, host in enumerate(hosts):
        if index in protected:
            continue
        ready = _ready_turn(host.candidate.minimum_cooldown, boost)
        if ready is None or ready > turns:
            continue
        events.append((ready, host.order, index))
    for _, _, index in sorted(events):
        covered = set(hosts[index].candidate.answers) & unmet
        if not covered:
            continue
        protected.add(index)
        unmet -= covered
    return protected


def _legal(
    hosts: list[_Host],
    assignment: dict[int, Candidate],
    remaining: Counter[int],
    used: set[tuple[int, int]],
    colors: set[str],
    facts: dict[int, _Facts],
) -> bool:
    seen = set(used)
    left = remaining.copy()
    for index, assist in assignment.items():
        pair = (assist.owned_id, assist.copy_index)
        host = hosts[index]
        if pair in seen or pair == (host.candidate.owned_id, host.candidate.copy_index):
            return False
        if _attribute_gain(host, assist, colors, facts) is None:
            return False
        updated = _consume(left, assist, borrowed=False)
        if updated is None:
            return False
        left = updated
        seen.add(pair)
    return True


def _score(
    hosts: list[_Host],
    assignment: dict[int, Candidate],
    active: set[str],
    colors: set[str],
    facts: dict[int, _Facts],
    turns: int,
    transform_turns: int | None,
) -> tuple[float, ...]:
    boost = _boost(hosts, assignment)
    held: set[str] = set()
    in_time: set[str] = set()
    backups: dict[str, float] = defaultdict(float)
    attribute_gains = 0
    hazard_hits: set[tuple[int, str]] = set()
    for index, host in enumerate(hosts):
        assist = assignment.get(index)
        awakenings = list(host.awakenings)
        if assist is not None:
            card = facts.get(assist.form_id)
            if card is not None:
                awakenings.extend(card.awakenings)
            gain = _attribute_gain(host, assist, colors, facts)
            if gain:
                attribute_gains += gain
        for hazard in _hazards(awakenings):
            hazard_hits.add((index, hazard))
        if assist is None:
            if _transforms(host.candidate):
                continue
            answers = set(host.candidate.answers) & active
            held |= answers
            ready = _ready_turn(host.candidate.minimum_cooldown, boost)
        else:
            answers = set(assist.answers) & active
            ready = _inherited_ready(
                host.candidate.minimum_cooldown,
                assist.minimum_cooldown,
                boost,
            )
            if ready is None:
                continue
            held |= answers
        if ready is None:
            continue
        if ready <= turns:
            in_time |= answers
            continue
        for role in answers:
            backups[role] = max(backups[role], 1.0 / ready)
    pressure = sum(value for role, value in backups.items() if role not in in_time)
    progress = _progress(boost, transform_turns)
    return (
        float(len(held)),
        float(len(in_time)),
        progress,
        pressure,
        float(len(hazard_hits)),
        float(attribute_gains),
    )


def _write(
    team: SearchedTeam,
    hosts: list[_Host],
    chosen: dict[int, Candidate],
    facts: dict[int, _Facts],
    colors: set[str],
    turns: int,
    boost: int,
) -> None:
    for index, assist in chosen.items():
        host = hosts[index]
        member = TeamMember(
            owned_id=assist.owned_id,
            copy_index=assist.copy_index,
            form_id=assist.form_id,
            form_name=assist.form_name,
        )
        if host.role == "leader":
            team.leader_assist = member
        elif host.role == "helper":
            team.helper_assist = member
        else:
            slot = host.order - 1
            team.subs[slot] = team.subs[slot].model_copy(update={"assist": member})
        ready = _inherited_ready(
            host.candidate.minimum_cooldown,
            assist.minimum_cooldown,
            boost,
        )
        if ready is None:
            clock = f"its cooldown is unknown, so it is not timed within {turns} turns"
        elif ready <= turns:
            clock = f"the inherited active is ready on turn {ready}"
        else:
            clock = f"the inherited active is ready on turn {ready}, after this window"
        gain = _attribute_gain(host, assist, colors, facts) or 0
        team.assist_notes.append(
            f"Equipped #{assist.form_id} on {host.role}: "
            f"skill boost {assist.skill_boost}, attribute change {gain}, {clock}."
        )


def _boost(hosts: list[_Host], assignment: dict[int, Candidate]) -> int:
    total = sum(host.candidate.skill_boost for host in hosts)
    return total + sum(assist.skill_boost for assist in assignment.values())


def _progress(boost: int, transform_turns: int | None) -> float:
    if transform_turns is None or transform_turns <= 0:
        return 1.0
    return min(boost, transform_turns) / transform_turns


def _ready_turn(cooldown: int | None, boost: int) -> int | None:
    if cooldown is None:
        return None
    charges = max(0, cooldown - boost)
    return 1 if charges == 0 else charges


def _inherited_ready(host_cd: int | None, assist_cd: int | None, boost: int) -> int | None:
    if host_cd is None or assist_cd is None:
        return None
    charges = max(0, host_cd + assist_cd - boost)
    return 1 if charges == 0 else charges


def _transforms(candidate: Candidate) -> bool:
    return "transform" in candidate.path


def _active_roles(essential: list[str]) -> set[str]:
    return {role for role in essential if role not in _ACTIVE_SKIP}


def _leader_colors(essential: list[str]) -> set[str]:
    colors = set()
    for role in essential:
        if role.endswith("_orbs"):
            colors.add(role[: -len("_orbs")])
        elif role.endswith("_board"):
            colors.add(role[: -len("_board")])
    return colors


def _attribute_gain(
    host: _Host,
    assist: Candidate,
    colors: set[str],
    facts: dict[int, _Facts],
) -> int | None:
    """1 when the equip adds a leader color, 0 when it does not, None to reject."""
    card = facts.get(assist.form_id)
    change = _sub_change([] if card is None else card.awakenings)
    if change is None:
        return 0
    if not host.sub_known:
        return 0
    if isinstance(change, set):
        if host.sub_attribute in colors and not change & colors:
            return None
        return 0
    if host.sub_attribute in colors and change not in colors:
        return None
    if change in colors and host.sub_attribute != change:
        return 1
    return 0


def _sub_change(awakenings: list[int]) -> str | set[str] | None:
    colors: list[str] = []
    for awakening_id in awakenings:
        color = _SUB_CHANGE.get(awakening_id)
        if color is not None and color not in colors:
            colors.append(color)
    if not colors:
        return None
    if len(colors) == 1:
        return colors[0]
    return set(colors)


def _hazards(awakenings: list[int]) -> set[str]:
    found = {key for key, threshold in _HAZARDS if capped(awakenings, key) >= threshold}
    if _DELAY_RESIST in awakenings:
        found.add("skill_delay_resist")
    return found


def _find(
    pool: CandidatePool,
    *,
    owned_id: int,
    copy_index: int,
    form_id: int,
    kind: str,
) -> Candidate | None:
    for row in pool.candidates:
        if (
            row.kind == kind
            and row.owned_id == owned_id
            and row.copy_index == copy_index
            and row.form_id == form_id
        ):
            return row
    return None


def _consume(
    remaining: Counter[int], candidate: Candidate, *, borrowed: bool
) -> Counter[int] | None:
    need: Counter[int] = Counter(candidate.spends)
    if not borrowed:
        need[candidate.owned_id] += 1
    if any(remaining[monster_id] < count for monster_id, count in need.items()):
        return None
    updated = remaining.copy()
    updated.subtract(need)
    return updated


def _box_counts(store: Store) -> Counter[int]:
    from padbox.inventory.snapshots import current_box

    box = current_box(store) or {}
    counts: Counter[int] = Counter()
    for row in box.get("monsters") or []:
        quantity = int(row.get("quantity") or 0)
        if quantity < 1:
            continue
        counts[int(row["monster_id"])] += quantity
    return counts


def _facts(store: Store, form_ids: set[int]) -> dict[int, _Facts]:
    found: dict[int, _Facts] = {}
    with closing(store.connect("reference")) as db:
        for form_id in form_ids:
            row = db.execute("SELECT raw FROM monster WHERE id=?", (form_id,)).fetchone()
            if row is None:
                continue
            raw = json.loads(row["raw"]) if row["raw"] else {}
            awakenings = [
                int(item["awakening_id"])
                for item in db.execute(
                    "SELECT awakening_id FROM monster_awakening "
                    "WHERE monster_id=? AND kind='normal' ORDER BY position",
                    (form_id,),
                )
            ]
            sub_attribute, sub_known = _sub_attribute(raw if isinstance(raw, dict) else {})
            found[form_id] = _Facts(
                awakenings=awakenings,
                sub_attribute=sub_attribute,
                sub_known=sub_known,
            )
    return found


def _sub_attribute(raw: dict) -> tuple[str | None, bool]:
    attrs = raw.get("attrs")
    if not isinstance(attrs, list):
        return None, False
    if len(attrs) < 2:
        return None, True
    if isinstance(attrs[1], int) and not isinstance(attrs[1], bool) and attrs[1] in ATTRIBUTES:
        return ATTRIBUTES[attrs[1]], True
    return None, False


def _copy_key(candidate: Candidate) -> tuple[int, int, int]:
    return (candidate.owned_id, candidate.copy_index, candidate.form_id)
