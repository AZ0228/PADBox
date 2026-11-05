"""Two SQLite databases: replaceable references and durable user evidence."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from padbox.models import Monster

REFERENCE_SCHEMA = """
CREATE TABLE monster(id INTEGER PRIMARY KEY, name TEXT NOT NULL, rarity INTEGER,
 icon TEXT, provenance TEXT NOT NULL, raw TEXT NOT NULL);
CREATE TABLE availability(monster_id INTEGER REFERENCES monster(id), server TEXT NOT NULL,
 PRIMARY KEY(monster_id,server));
CREATE TABLE attribute(monster_id INTEGER REFERENCES monster(id), position INTEGER, value INTEGER,
 PRIMARY KEY(monster_id,position));
CREATE TABLE monster_type(monster_id INTEGER REFERENCES monster(id), value INTEGER,
 PRIMARY KEY(monster_id,value));
CREATE TABLE skill(id INTEGER PRIMARY KEY, name TEXT, description TEXT, base_cooldown INTEGER,
 minimum_cooldown INTEGER, effects TEXT, evidence TEXT, raw TEXT);
CREATE TABLE monster_skill(monster_id INTEGER REFERENCES monster(id), role TEXT,
 skill_id INTEGER REFERENCES skill(id), PRIMARY KEY(monster_id,role));
CREATE TABLE awakening(id INTEGER PRIMARY KEY, name TEXT, effect TEXT);
CREATE TABLE monster_awakening(monster_id INTEGER REFERENCES monster(id), position INTEGER,
 awakening_id INTEGER REFERENCES awakening(id), kind TEXT, PRIMARY KEY(monster_id,position,kind));
CREATE TABLE relationship(source_id INTEGER REFERENCES monster(id), target_id INTEGER NOT NULL,
 kind TEXT, evidence TEXT, PRIMARY KEY(source_id,target_id,kind));
CREATE INDEX monster_name ON monster(name);
"""
INVENTORY_SCHEMA = """
CREATE TABLE recording(id TEXT PRIMARY KEY, path TEXT, metadata TEXT, config TEXT,
 status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE owned(id TEXT PRIMARY KEY, monster_id INTEGER, proposed_id INTEGER,
 status TEXT NOT NULL DEFAULT 'unmatched' CHECK(status IN
 ('unmatched','uncertain','confirmed','rejected','merged')),
 tracking TEXT NOT NULL DEFAULT 'clear' CHECK(tracking IN ('clear','ambiguous')),
 confidence REAL NOT NULL DEFAULT 0, high_confidence INTEGER NOT NULL DEFAULT 0,
 recording_id TEXT REFERENCES recording(id), merged_into TEXT REFERENCES owned(id),
 level INTEGER, skill_level INTEGER, awakenings TEXT, assist_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE observation(id TEXT PRIMARY KEY, recording_id TEXT REFERENCES recording(id),
 timestamp REAL, frame_index INTEGER, rect TEXT, crop TEXT,
 candidates TEXT, matched_inventory_id TEXT NOT NULL REFERENCES owned(id),
 match_reason TEXT, tracking_reason TEXT);
CREATE TABLE review_event(id INTEGER PRIMARY KEY, inventory_id TEXT REFERENCES owned(id),
 action TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE INDEX observations_owned ON observation(matched_inventory_id);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, home: Path):
        self.home = home.expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        for name, schema in [("reference", REFERENCE_SCHEMA), ("inventory", INVENTORY_SCHEMA)]:
            with closing(self.connect(name)) as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    db.executescript("BEGIN;" + schema + "PRAGMA user_version=1; COMMIT;")
                    version = 1
                if version != 1:
                    raise ValueError(f"Unsupported {name} schema version {version}")

    def connect(self, name: str = "inventory") -> sqlite3.Connection:
        if name not in {"inventory", "reference"}:
            raise ValueError("Unknown database")
        db = sqlite3.connect(self.home / f"{name}.sqlite", timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def load_monsters(self, monsters: list[Monster]) -> int:
        # Entire manifest is validated by adapters before any mutation.
        with closing(self.connect("reference")) as db, db:
            for m in monsters:
                old = db.execute(
                    "SELECT icon,provenance FROM monster WHERE id=?", (m.monster_id,)
                ).fetchone()
                old_provenance = json.loads(old["provenance"]) if old else {}
                same_source = (
                    old_provenance.get("source") == m.provenance.source
                    and old_provenance.get("version") == m.provenance.version
                )
                icon = str(m.icon) if m.icon else (old["icon"] if old and same_source else None)
                db.execute(
                    """INSERT INTO monster VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,rarity=excluded.rarity,icon=excluded.icon,
                 provenance=excluded.provenance,raw=excluded.raw""",
                    (
                        m.monster_id,
                        m.name,
                        m.rarity,
                        icon,
                        m.provenance.model_dump_json(),
                        json.dumps(m.raw),
                    ),
                )
                for table in [
                    "availability",
                    "attribute",
                    "monster_type",
                    "monster_skill",
                    "monster_awakening",
                ]:
                    db.execute(f"DELETE FROM {table} WHERE monster_id=?", (m.monster_id,))
                db.execute("DELETE FROM relationship WHERE source_id=?", (m.monster_id,))
                db.executemany(
                    "INSERT INTO availability VALUES (?,?)", [(m.monster_id, s) for s in m.servers]
                )
                db.executemany(
                    "INSERT INTO attribute VALUES (?,?,?)",
                    [(m.monster_id, i, a) for i, a in enumerate(m.attributes)],
                )
                db.executemany(
                    "INSERT INTO monster_type VALUES (?,?)",
                    [(m.monster_id, t) for t in set(m.types)],
                )
                for role, skill in [("active", m.active_skill), ("leader", m.leader_skill)]:
                    if skill:
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
                                json.dumps(skill.raw),
                            ),
                        )
                        db.execute(
                            "INSERT INTO monster_skill VALUES (?,?,?)",
                            (m.monster_id, role, skill.skill_id),
                        )
                for kind, ids in [("normal", m.awakenings), ("super", m.super_awakenings)]:
                    for i, a in enumerate(ids):
                        db.execute("INSERT OR IGNORE INTO awakening(id) VALUES (?)", (a,))
                        db.execute(
                            "INSERT INTO monster_awakening VALUES (?,?,?,?)",
                            (m.monster_id, i, a, kind),
                        )
                for edge in m.relationships:
                    db.execute(
                        "INSERT INTO relationship VALUES (?,?,?,?)",
                        (m.monster_id, edge.target_id, edge.kind, edge.evidence),
                    )
        return len(monsters)

    def references(self, search: str = "", server: str = "NA") -> list[dict]:
        with closing(self.connect("reference")) as db:
            return [
                dict(r)
                for r in db.execute(
                    """SELECT m.* FROM monster m
             JOIN availability a ON a.monster_id=m.id AND a.server=?
             WHERE m.name LIKE ? OR CAST(m.id AS TEXT)=? ORDER BY m.id""",
                    (server, f"%{search}%", search),
                )
            ]

    def require_monster(self, monster_id: int, server: str = "NA") -> None:
        if not any(m["id"] == monster_id for m in self.references(str(monster_id), server)):
            raise ValueError(f"Monster #{monster_id} is not in the local {server} reference set")
