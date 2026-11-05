from pathlib import Path

import cv2
import numpy as np
import pytest

from padbox.demo import render_grid
from padbox.detection.grid import detect
from padbox.inventory.tracking import align
from padbox.models import Layout


def icons(fixtures: dict[str, Path]) -> list[np.ndarray]:
    return [
        cv2.imread(str(fixtures["references"].parent / "icons" / f"{i}.png")) for i in range(1, 16)
    ]


@pytest.mark.parametrize(
    "x,y,size,step,width,height",
    [
        (20, 80, 80, 94, 500, 430),
        (35, 115, 64, 78, 460, 440),
        (10, 24, 100, 112, 600, 450),
    ],
)
def test_grid_at_different_positions(fixtures, x, y, size, step, width, height):
    image = render_grid(icons(fixtures), x, y, size, step, width, height)
    cells = detect(image)
    assert len(cells) == 15
    assert {c.column for c in cells} == set(range(5))
    assert abs(cells[0].x - x) <= 2
    assert abs(cells[0].y - y) <= 2


def test_no_grid_on_transition():
    assert detect(np.zeros((640, 360, 3), dtype=np.uint8)) == []


def test_partial_row_is_not_invented(fixtures):
    image = render_grid(icons(fixtures), y=240, height=430)
    assert len(detect(image)) == 10


def test_calibration_scales_with_screen(fixtures):
    image = cv2.imread(str(fixtures["image"]))
    layout = Layout(
        columns=5,
        x=20 / 500,
        y=80 / 430,
        size=80 / 500,
        step_x=94 / 500,
        step_y=94 / 430,
        roi=(0, 0, 1, 0.9),
    )
    assert len(detect(image, layout)) == 15
    doubled = cv2.resize(image, None, fx=2, fy=2)
    cells = detect(doubled, layout)
    assert len(cells) == 15
    assert cells[0].rect == [40, 160, 160, 160]


def test_scroll_overlap_preserves_physical_copies(fixtures):
    image = cv2.imread(str(fixtures["image"]))
    shifted = cv2.imread(str(fixtures["image"].with_name("box-shifted.png")))
    before, after = detect(image), detect(shifted)
    result = align(before, after)
    assert result.certain
    assert len(result.links) == 10
    assert result.links[0] == 5
    assert result.links[9] == 14
    # Two identical monsters occupy different cells in one frame.
    assert before[0].rect != before[2].rect


def test_repeated_patterns_preserve_ambiguity(fixtures):
    image = render_grid([icons(fixtures)[0]] * 15)
    result = align(detect(image), detect(image))
    assert not result.certain
    assert not result.links


def test_insufficient_overlap_is_ambiguous(fixtures):
    before = detect(cv2.imread(str(fixtures["image"])))
    after = detect(render_grid(icons(fixtures)[::-1]))
    assert not align(before, after).certain


def test_low_contrast_grid_fallback():
    # Original procedural flat backgrounds: insufficient contrast for strong Canny pass.
    image = render_grid([np.full((80, 80, 3), 8, dtype=np.uint8)] * 15)
    assert len(detect(image)) == 15


@pytest.mark.parametrize("y", [40, 80, 101, 127])
def test_calibrated_scroll_phase_tracks_rows(fixtures, y):
    image = render_grid(icons(fixtures), y=y)
    layout = Layout(
        columns=5,
        roi=(0, 0.05, 1, 0.95),
        x=20 / 500,
        y=80 / 430,
        size=80 / 500,
        step_x=94 / 500,
        step_y=94 / 430,
        follow_scroll=True,
    )
    cells = detect(image, layout)
    assert cells
    assert min(abs(cells[0].y - (y + row * 94)) for row in range(3)) <= 3
    assert all(c.y >= round(0.05 * 430) and c.y + c.height <= round(0.95 * 430) for c in cells)


def test_scroll_calibration_rejects_blank_screen():
    layout = Layout(
        columns=5, x=0.04, y=0.2, size=0.16, step_x=0.188, step_y=0.22, follow_scroll=True
    )
    assert detect(np.zeros((430, 500, 3), dtype=np.uint8), layout) == []


def test_optical_flow_checks_forward_backward_motion(fixtures):
    from padbox.inventory.tracking import _flow_alignment

    before = render_grid(icons(fixtures), y=80)
    shifted = render_grid(icons(fixtures), y=60)
    after = (shifted.astype(np.float32) * 0.85).astype(np.uint8)
    previous, current = detect(before), detect(after)
    result = _flow_alignment(previous, current, before, after)
    assert result is not None
    assert result.certain
    assert len(result.links) == 15
    assert "optical flow" in result.reason


def test_optical_flow_does_not_resolve_identical_rows(fixtures):
    from padbox.inventory.tracking import _flow_alignment

    before = render_grid([icons(fixtures)[0]] * 15)
    assert _flow_alignment(detect(before), detect(before), before, before) is None
