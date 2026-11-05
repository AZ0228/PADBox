from pathlib import Path

import pytest

from padbox.database.store import Store
from padbox.demo import Demo
from padbox.sources.adapters import CuratedSource


@pytest.fixture
def fixtures(tmp_path: Path) -> dict[str, Path]:
    return Demo(tmp_path / "fixtures").generate()


@pytest.fixture
def store(tmp_path: Path, fixtures: dict[str, Path]) -> Store:
    database = Store(tmp_path / "home")
    database.load_monsters(CuratedSource(fixtures["references"]).load())
    return database
