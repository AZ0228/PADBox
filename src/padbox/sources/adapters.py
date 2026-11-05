"""Public snapshot and curated-file adapters. No game endpoints are contacted."""

import hashlib
import json
import logging
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from pydantic import TypeAdapter

from padbox.database.store import now
from padbox.models import Monster, Provenance, Relationship, Skill

LOG = logging.getLogger(__name__)
PINNED_COMMIT = "6d8b0698208aebef17e4f4fefe6b3c616f8d0ed8"
REPO = "https://raw.githubusercontent.com/Mapaler/PADDashFormation"


class SourceAdapter(Protocol):
    def load(self) -> list[Monster]: ...


class CuratedSource:
    def __init__(self, manifest: Path):
        self.manifest = manifest.resolve()

    def load(self) -> list[Monster]:
        records = TypeAdapter(list[Monster]).validate_json(self.manifest.read_text())
        if len({m.monster_id for m in records}) != len(records):
            raise ValueError("Duplicate monster IDs in reference manifest")
        for m in records:
            if m.icon:
                m.icon = (self.manifest.parent / m.icon).resolve()
                if cv2.imread(str(m.icon)) is None:
                    raise ValueError(f"Reference icon missing or unreadable: {m.icon}")
        return records


class SnapshotCache:
    def __init__(self, directory: Path):
        self.directory = directory

    def fetch(self, url: str) -> Path:
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.directory / key
        metadata = path.with_name(path.name + ".json")
        if path.exists() and metadata.exists():
            expected = json.loads(metadata.read_text())["sha256"]
            if hashlib.sha256(path.read_bytes()).hexdigest() == expected:
                return path
            raise ValueError(f"Cache integrity mismatch: {path}; remove this entry and retry")
        self.directory.mkdir(parents=True, exist_ok=True)
        LOG.info("Caching public reference: %s", url)
        request = urllib.request.Request(url, headers={"User-Agent": "PADBox/0.1 local-cache"})
        # Download to memory, validate HTTP status/size, then atomically publish.
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = response.read(50_000_001)
        if not payload or len(payload) > 50_000_000:
            raise ValueError(f"Empty or oversized reference download: {url}")
        temp = path.with_suffix(".part")
        temp.write_bytes(payload)
        temp.replace(path)
        metadata.write_text(
            json.dumps(
                {"url": url, "fetched_at": now(), "sha256": hashlib.sha256(payload).hexdigest()},
                indent=2,
            )
        )
        return path


def sheet_location(monster_id: int) -> tuple[int, int, int]:
    """Normalized public IDs, one-based 10x10 sheets, 100px art with 2px gutters."""
    if monster_id < 1:
        raise ValueError("Monster ID must be positive")
    page = (monster_id - 1) // 100 + 1
    index = (monster_id - 1) % 100
    return page, (index % 10) * 102, (index // 10) * 102


class FormationSource:
    def __init__(
        self,
        cache: Path,
        icons: Path,
        ids: set[int] | None = None,
        commit: str = PINNED_COMMIT,
        all_icons: bool = False,
    ):
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Snapshot version must be a full commit SHA")
        self.cache = SnapshotCache(cache)
        self.icons = icons / commit
        self.ids = ids or set()
        self.all_icons = all_icons
        self.report: dict = {}
        self.commit = commit
        self.base = f"{REPO}/{commit}"

    def _json(self, path: str) -> tuple[object, Path]:
        local = self.cache.fetch(f"{self.base}/{path}")
        return json.loads(local.read_text(encoding="utf-8-sig")), local

    def load(self) -> list[Monster]:
        cards, cards_path = self._json("monsters-info/mon_en.json")
        skills_data, _ = self._json("monsters-info/skill_en.json")
        keys, _ = self._json("monsters-info/ckey.json")
        if not isinstance(cards, list) or not isinstance(skills_data, list):
            raise ValueError("Unexpected upstream snapshot structure")
        skills = {s["id"]: s for s in skills_data}
        en_key = next(k for k in keys if k["code"] == "en")
        updated = datetime.fromtimestamp(en_key["updateTime"] / 1000, UTC).isoformat()
        provenance = Provenance(
            source="PADDashFormation/NA",
            version=self.commit,
            url=f"{self.base}/monsters-info/mon_en.json",
            updated_at=updated,
            fetched_at=json.loads(cards_path.with_name(cards_path.name + ".json").read_text())[
                "fetched_at"
            ]
            if cards_path.with_name(cards_path.name + ".json").exists()
            else now(),
            sha256=hashlib.sha256(cards_path.read_bytes()).hexdigest(),
            usage="Upstream code GPL-3.0; game art/metadata third-party; local cache only",
        )
        enabled = {c["id"] for c in cards if c.get("enabled") and not c.get("isEmpty")}
        selected = enabled if self.all_icons else self.ids
        missing = selected - enabled
        if missing:
            raise ValueError(f"Requested IDs missing/unavailable in NA snapshot: {sorted(missing)}")
        result: list[Monster] = []
        self.icons.mkdir(parents=True, exist_ok=True)
        sheet_images: dict[int, np.ndarray | None] = {}
        for card in cards:
            mid = card["id"]
            if mid not in enabled:
                continue
            # All metadata is loaded; only selected icons are downloaded (no asset dump).
            icon = None
            if mid in selected:
                page, x, y = sheet_location(mid)
                if page not in sheet_images:
                    try:
                        path = self.cache.fetch(f"{self.base}/images/cards_en/CARDS_{page:03}.PNG")
                        sheet_images[page] = cv2.imread(str(path), cv2.IMREAD_COLOR)
                        if sheet_images[page] is None:
                            LOG.warning("Could not decode icon sheet %s", page)
                    except (OSError, urllib.error.URLError, ValueError) as exc:
                        LOG.warning("Icon sheet %s unavailable: %s", page, exc)
                        sheet_images[page] = None
                while len(sheet_images) > 4:
                    del sheet_images[next(iter(sheet_images))]
                sheet = sheet_images[page]
                if sheet is not None and y + 100 <= sheet.shape[0] and x + 100 <= sheet.shape[1]:
                    crop = sheet[y : y + 100, x : x + 100]
                    if crop.std() > 2:
                        icon = self.icons / f"{mid}.png"
                        if not cv2.imwrite(str(icon), crop):
                            raise OSError(f"Failed writing {icon}")
                    else:
                        LOG.debug("Empty-looking icon for #%s", mid)
                else:
                    LOG.warning("Missing/out-of-bounds icon for #%s", mid)
            edges = [
                Relationship(target_id=t, kind="transform")
                for t in card.get("henshinTo", [])
                if t > 0
            ]
            # Reverse parent direction: each card's outgoing evolution edges.
            # Built in a second pass below in O(n).
            result.append(
                Monster(
                    monster_id=mid,
                    name=card["name"],
                    servers=["NA"],
                    attributes=card.get("attrs", []),
                    types=card.get("types", []),
                    rarity=card.get("rarity"),
                    icon=icon,
                    provenance=provenance,
                    active_skill=self._skill(skills.get(card.get("activeSkillId"))),
                    leader_skill=self._skill(skills.get(card.get("leaderSkillId"))),
                    awakenings=card.get("awakenings", []),
                    super_awakenings=card.get("superAwakenings", []),
                    relationships=edges,
                    raw=card,
                )
            )
        missing_icons = sorted(
            m.monster_id for m in result if m.monster_id in selected and m.icon is None
        )
        self.report = {
            "source": "PADDashFormation/NA",
            "version": self.commit,
            "metadata_records": len(result),
            "requested_icons": len(selected),
            "usable_icons": len(selected) - len(missing_icons),
            "missing_or_blank_icon_ids": missing_icons,
        }
        if missing_icons:
            LOG.warning(
                "%s requested icons unavailable/blank; metadata remains searchable",
                len(missing_icons),
            )
        by_id = {m.monster_id: m for m in result}
        for m in result:
            parent = m.raw.get("evoBaseId")
            if parent in by_id and parent != m.monster_id:
                # Awakening 49 is the source's equip flag; raw remains available for auditing.
                kind = "assist_evolution" if 49 in m.awakenings else "evolution"
                by_id[parent].relationships.append(Relationship(target_id=m.monster_id, kind=kind))
        return result

    @staticmethod
    def _skill(raw: dict | None) -> Skill | None:
        if not raw or not raw.get("id"):
            return None
        cd = raw.get("initialCooldown")
        levels = raw.get("maxLevel", 0)
        minimum = max(0, cd - max(0, levels - 1)) if cd is not None else None
        return Skill(
            skill_id=raw["id"],
            name=raw["name"],
            description=raw.get("description", ""),
            base_cooldown=cd,
            minimum_cooldown=minimum,
            raw=raw,
            evidence="source_encoded",
        )
