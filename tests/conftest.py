import hashlib
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from padbox.database.store import Store
from padbox.demo import Demo
from padbox.inventory.pipeline import import_media
from padbox.models import Monster, Provenance, Relationship, Skill
from padbox.sources.adapters import PINNED_COMMIT, REPO, CuratedSource

# Compound actives, compound leader skills, and evolving actives reference other skills.
_SKILL_CONTAINERS = {116, 138, 232}
REGRESSION_CARDS = Path(__file__).resolve().parent / "fixtures" / "cards"


@pytest.fixture
def reviewable_draft(store, fixtures):
    """Exact file fixtures auto-accept; leave one valid identity for UI review."""
    sid = import_media(store, fixtures["image"])["recording_id"]
    with closing(store.connect()) as db, db:
        item = db.execute(
            "SELECT id FROM owned WHERE recording_id=? "
            "AND status='confirmed' AND high_confidence=1 ORDER BY id LIMIT 1",
            (sid,),
        ).fetchone()[0]
        db.execute(
            "UPDATE owned SET status='uncertain',monster_id=NULL,high_confidence=0 WHERE id=?",
            (item,),
        )
        db.execute("DELETE FROM review_event WHERE inventory_id=?", (item,))
    return sid


@pytest.fixture
def fixtures(tmp_path: Path) -> dict[str, Path]:
    return Demo(tmp_path / "fixtures").generate()


@pytest.fixture
def store(tmp_path: Path, fixtures: dict[str, Path]) -> Store:
    database = Store(tmp_path / "home")
    database.load_monsters(CuratedSource(fixtures["references"]).load())
    return database


def _refuse_network(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("regression card loader must not use the network")


def load_regression_cards() -> list[dict[str, Any]]:
    """Pinned-snapshot extracts. Each file is one monster and the skills it references."""
    paths = sorted(REGRESSION_CARDS.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No regression card fixtures in {REGRESSION_CARDS}")
    records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    for path, record in zip(paths, records, strict=True):
        monster_id = int(record["monster_id"])
        card = record["card"]
        if path.stem != str(monster_id) or int(card["id"]) != monster_id:
            raise ValueError(f"{path.name} does not match monster #{monster_id}")
        if record["name"] != card["name"]:
            raise ValueError(f"#{monster_id} fixture name does not match the card")
        if record["snapshot_commit"] != PINNED_COMMIT:
            raise ValueError(f"#{monster_id} is not from the pinned snapshot {PINNED_COMMIT}")
        if record["provenance"]["version"] != PINNED_COMMIT:
            raise ValueError(f"#{monster_id} provenance is not the pinned snapshot")
    if len({record["monster_id"] for record in records}) != len(records):
        raise ValueError("Duplicate monster IDs in regression card fixtures")
    if len({record["skill_index"]["sha256"] for record in records}) != 1:
        raise ValueError("Regression cards cite different skill-index hashes")
    return records


def regression_skill_index(records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Merge per-card skill extracts. The same skill ID must carry the same raw record."""
    index: dict[int, dict[str, Any]] = {}
    for record in records:
        local = {int(skill["id"]): skill for skill in record["skills"]}
        for skill in local.values():
            if skill.get("type") not in _SKILL_CONTAINERS:
                continue
            missing = [
                param for param in skill.get("params") or [] if param not in local
            ]
            if missing:
                raise ValueError(
                    f"#{record['monster_id']} skill {skill['id']} is missing components {missing}"
                )
        for skill_id, skill in local.items():
            previous = index.get(skill_id)
            if previous is not None and previous != skill:
                raise ValueError(f"Skill {skill_id} differs between card extracts")
            index[skill_id] = skill
    return index


def _skill(raw: dict[str, Any] | None) -> Skill | None:
    if not raw or not raw.get("id"):
        return None
    cooldown = raw.get("initialCooldown")
    levels = raw.get("maxLevel", 0)
    minimum = max(0, cooldown - max(0, levels - 1)) if cooldown is not None else None
    return Skill(
        skill_id=raw["id"],
        name=raw.get("name") or "",
        description=raw.get("description") or "",
        base_cooldown=cooldown,
        minimum_cooldown=minimum,
        raw=raw,
        evidence="source_encoded",
    )


def _require_skill(
    skills: dict[int, dict[str, Any]], monster_id: int, skill_id: int, role: str
) -> Skill:
    skill = _skill(skills.get(skill_id))
    if skill is None:
        raise ValueError(f"#{monster_id} {role} skill {skill_id} is not in the extract")
    return skill


def _monsters(records: list[dict[str, Any]]) -> list[Monster]:
    monsters: list[Monster] = []
    for record in records:
        card = record["card"]
        skills = {int(skill["id"]): skill for skill in record["skills"]}
        monster_id = int(card["id"])
        monsters.append(
            Monster(
                monster_id=monster_id,
                name=card["name"],
                servers=["NA"],
                attributes=list(card.get("attrs") or []),
                types=list(card.get("types") or []),
                rarity=card.get("rarity"),
                provenance=Provenance.model_validate(record["provenance"]),
                active_skill=_require_skill(
                    skills, monster_id, int(card["activeSkillId"]), "active"
                ),
                leader_skill=_require_skill(
                    skills, monster_id, int(card["leaderSkillId"]), "leader"
                ),
                awakenings=list(card.get("awakenings") or []),
                super_awakenings=list(card.get("superAwakenings") or []),
                raw=card,
            )
        )
    by_id = {monster.monster_id: monster for monster in monsters}
    for monster in monsters:
        for target in monster.raw.get("henshinTo") or []:
            if int(target) > 0:
                monster.relationships.append(
                    Relationship(target_id=int(target), kind="transform")
                )
        parent = monster.raw.get("evoBaseId")
        if parent in by_id and parent != monster.monster_id:
            kind = "assist_evolution" if 49 in monster.awakenings else "evolution"
            by_id[int(parent)].relationships.append(
                Relationship(target_id=monster.monster_id, kind=kind)
            )
    return monsters


def _write_skill_index(
    store: Store, index: dict[int, dict[str, Any]], record: dict[str, Any]
) -> None:
    """Store every referenced skill, then publish the index the decoder already reads."""
    with closing(store.connect("reference")) as db, db:
        for raw in index.values():
            skill = _skill(raw)
            if skill is None:
                continue
            db.execute(
                """INSERT INTO skill VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                description=excluded.description,base_cooldown=excluded.base_cooldown,
                minimum_cooldown=excluded.minimum_cooldown,effects=excluded.effects,
                evidence=excluded.evidence,raw=excluded.raw""",
                (
                    skill.skill_id,
                    skill.name,
                    skill.description,
                    skill.base_cooldown,
                    skill.minimum_cooldown,
                    json.dumps(skill.effects),
                    skill.evidence,
                    json.dumps(raw),
                ),
            )
    payload = json.dumps(list(index.values()), ensure_ascii=False).encode()
    url = f"{REPO}/{record['snapshot_commit']}/monsters-info/skill_en.json"
    cache = store.home / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / hashlib.sha256(url.encode()).hexdigest()
    path.write_bytes(payload)
    path.with_name(path.name + ".json").write_text(
        json.dumps(
            {
                "url": url,
                "fetched_at": record["provenance"].get("fetched_at"),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            indent=2,
        )
    )


def build_regression_store(home: Path) -> Store:
    """Build a reference store from committed card extracts. Does not touch the network."""
    records = load_regression_cards()
    store = Store(home)
    store.load_monsters(_monsters(records))
    _write_skill_index(store, regression_skill_index(records), records[0])
    return store


@pytest.fixture
def regression_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Lineup cards from the pinned NA snapshot. Component skills are local."""
    monkeypatch.setattr("urllib.request.urlopen", _refuse_network)
    return build_regression_store(tmp_path / "regression")
