from pathlib import Path

import pytest

from padbox.demo import Demo


@pytest.fixture
def fixtures(tmp_path: Path) -> dict[str, Path]:
    return Demo(tmp_path / "fixtures").generate()
