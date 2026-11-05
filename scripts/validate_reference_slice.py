"""Construct a local icon montage and measure detection/matching against cached references.

This is a reference-derived smoke test, not real screenshot validation. Game art stays ignored.
Run: uv run python scripts/validate_reference_slice.py
"""

import json
from pathlib import Path
from time import perf_counter

import cv2

from padbox.database.store import Store
from padbox.demo import render_grid
from padbox.detection.grid import annotate, detect
from padbox.recognition.evaluate import evaluate
from padbox.recognition.matcher import Matcher


def main() -> None:
    store = Store(Path(".padbox"))
    refs = store.references()
    by_id = {r["id"]: r for r in refs}
    selected = list(range(1, 13)) + [13966, 13967, 13968]
    if any(not by_id.get(mid, {}).get("icon") for mid in selected):
        raise SystemExit("Cache icons 1 through 12 and 13966,13967,13968 first")
    directory = store.home / "reference-validation"
    directory.mkdir(exist_ok=True)
    icons = [cv2.imread(by_id[mid]["icon"]) for mid in selected]
    grid = render_grid(icons)
    cv2.imwrite(str(directory / "constructed-grid.png"), grid)
    cells = detect(grid)
    cv2.imwrite(str(directory / "detected-grid.png"), annotate(grid, cells))
    examples = []
    for i, cell in enumerate(cells):
        row = round((cell.y - 80) / 94)
        column = round((cell.x - 20) / 94)
        expected = selected[row * 5 + column]
        path = directory / f"crop-{i:02}.png"
        cv2.imwrite(str(path), cell.image)
        examples.append({"image": path.name, "monster_id": expected})
    manifest = directory / "labels.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset": "Reference-derived constructed grid; NOT real PAD capture",
                "examples": examples,
            },
            indent=2,
        )
    )
    start = perf_counter()
    matcher = Matcher(refs)
    load_seconds = perf_counter() - start
    start = perf_counter()
    report = evaluate(matcher, manifest)
    report.update(
        {
            "detected_cells": len(cells),
            "expected_cells": 15,
            "cached_references": len(matcher.references),
            "matcher_load_seconds": load_seconds,
            "evaluation_seconds": perf_counter() - start,
        }
    )
    (directory / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
