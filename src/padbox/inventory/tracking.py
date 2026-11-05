"""Conservative adjacent-frame translation, independent of monster identity."""

from dataclasses import dataclass

import cv2
import numpy as np

from padbox.detection.grid import Cell
from padbox.inventory.consensus import LOCATION_MARGIN, row_consensus
from padbox.recognition.matcher import appearance_similarity


@dataclass
class Alignment:
    links: dict[int, int]  # Current cell index -> previous cell index.
    certain: bool
    reason: str


def align(
    previous: list[Cell],
    current: list[Cell],
    previous_image: np.ndarray | None = None,
    current_image: np.ndarray | None = None,
) -> Alignment:
    if not previous:
        return Alignment({}, True, "first visible grid")
    if not current:
        return Alignment({}, False, "no current grid")
    size = float(np.median([c.width for c in current]))
    shifts = sorted(
        {
            round((c.y - p.y) / 4) * 4
            for c in current
            for p in previous
            if c.column == p.column and abs(c.x - p.x) < size * 0.15
        }
    )
    possibilities: list[tuple[float, float, dict[int, int]]] = []
    similarity_cache: dict[tuple[int, int], float] = {}
    for shift in shifts:
        geometric_links: dict[int, int] = {}
        for i, cell in enumerate(current):
            candidates = [
                (j, p)
                for j, p in enumerate(previous)
                if p.column == cell.column
                and abs(p.x - cell.x) < size * 0.15
                and abs(p.y + shift - cell.y) < size * 0.13
            ]
            if len(candidates) != 1:
                continue
            j, other = candidates[0]
            key = (i, j)
            if key not in similarity_cache:
                similarity_cache[key] = appearance_similarity(cell.image, other.image)
            geometric_links[i] = j
        evidence = row_consensus(
            [(current[i], similarity_cache[i, j]) for i, j in geometric_links.items()]
        )
        if evidence:
            possibilities.append((evidence.score, shift, geometric_links))
    possibilities.sort(key=lambda p: (-p[0], -len(p[2]), abs(p[1])))
    if not possibilities:
        if previous_image is not None and current_image is not None:
            optical = _flow_alignment(previous, current, previous_image, current_image)
            if optical is not None:
                return optical
        return Alignment({}, False, "insufficient distinct overlapping icons; review slot identity")
    best = possibilities[0]
    # Nearby shift bins may describe the same correspondence; compare distinct mappings only.
    rivals = [p for p in possibilities[1:] if p[2] != best[2]]
    if rivals and best[0] - rivals[0][0] < LOCATION_MARGIN:
        return Alignment({}, False, "multiple plausible scroll offsets; review slot identity")
    return Alignment(best[2], True, f"unique vertical offset {best[1]:.0f}px")


def _flow_alignment(
    previous: list[Cell], current: list[Cell], before: np.ndarray, after: np.ndarray
) -> Alignment | None:
    """Independent, spatially distributed forward/backward flow evidence for overlay changes."""
    if before.shape != after.shape or len(previous) < 10 or len(current) < 10:
        return None
    distinct: list[np.ndarray] = []
    for cell in previous:
        if all(appearance_similarity(cell.image, im) < 0.90 for im in distinct):
            distinct.append(cell.image)
        if len(distinct) >= 3:
            break
    if len(distinct) < 3:
        return None
    scale = min(1.0, 640 / before.shape[1])
    gray_before = cv2.resize(cv2.cvtColor(before, cv2.COLOR_BGR2GRAY), None, fx=scale, fy=scale)
    gray_after = cv2.resize(cv2.cvtColor(after, cv2.COLOR_BGR2GRAY), None, fx=scale, fy=scale)
    mask = np.zeros_like(gray_before)
    for cell in previous:
        x1, y1 = (
            round((cell.x + cell.width * 0.15) * scale),
            round((cell.y + cell.height * 0.15) * scale),
        )
        x2, y2 = (
            round((cell.x + cell.width * 0.85) * scale),
            round((cell.y + cell.height * 0.85) * scale),
        )
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    points = cv2.goodFeaturesToTrack(
        gray_before, maxCorners=500, qualityLevel=0.02, minDistance=7, mask=mask
    )
    if points is None or len(points) < 40:
        return None
    moved, valid, _ = cv2.calcOpticalFlowPyrLK(gray_before, gray_after, points, None)
    if moved is None:
        return None
    returned, reverse_valid, _ = cv2.calcOpticalFlowPyrLK(gray_after, gray_before, moved, None)
    if returned is None:
        return None
    good = (
        valid.ravel().astype(bool)
        & reverse_valid.ravel().astype(bool)
        & (np.linalg.norm(returned - points, axis=2).ravel() < 1.0)
    )
    source = points.reshape(-1, 2)[good] / scale
    delta = (moved - points).reshape(-1, 2)[good] / scale
    if len(delta) < 40:
        return None
    median = np.median(delta, axis=0)
    inliers = (np.abs(delta[:, 0] - median[0]) < 2.5) & (np.abs(delta[:, 1] - median[1]) < 2.5)
    size = float(np.median([cell.width for cell in current]))
    if (
        inliers.sum() < 40
        or inliers.mean() < 0.65
        or abs(median[0]) > size * 0.04
        or abs(median[1]) > size * 0.75
    ):
        return None
    support = source[inliers]
    if np.ptp(support[:, 0]) < size * 1.5 or np.ptp(support[:, 1]) < size * 2:
        return None
    links = {}
    for i, cell in enumerate(current):
        candidates = [
            j
            for j, p in enumerate(previous)
            if p.column == cell.column
            and abs(p.x + median[0] - cell.x) < size * 0.10
            and abs(p.y + median[1] - cell.y) < size * 0.10
        ]
        if len(candidates) == 1:
            links[i] = candidates[0]
    if len(links) < 5:
        return None
    agreement = [
        appearance_similarity(current[i].image, previous[j].image) for i, j in links.items()
    ]
    if float(np.median(agreement)) < 0.75:
        return None
    return Alignment(
        links, True, f"forward/backward optical flow: {inliers.sum()} anchors, dy={median[1]:.1f}px"
    )
