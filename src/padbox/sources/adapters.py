"""Public snapshot and curated-file adapters. No game endpoints are contacted."""

from pathlib import Path
from typing import Protocol

import cv2
from pydantic import TypeAdapter

from padbox.models import Monster


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
