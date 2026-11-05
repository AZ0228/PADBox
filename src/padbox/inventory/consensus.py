"""Ordered grid overlap: tolerate overlays without requiring two complete rows."""

from dataclasses import dataclass

import numpy as np

from padbox.detection.grid import Cell
from padbox.recognition.matcher import appearance_similarity

ANCHOR_SCORE = 0.90
LOCATION_MARGIN = 0.025


def context_agrees(scores: list[float]) -> bool:
    """Tolerate sparse obscured cards while requiring the overlap to agree overall."""
    if not scores:
        return True
    if len(scores) < 4:
        return min(scores) >= 0.85
    return (
        float(np.median(scores)) >= 0.88
        and sum(s >= ANCHOR_SCORE for s in scores) >= len(scores) * 0.5
        and sum(s < 0.72 for s in scores) <= max(1, int(len(scores) * 0.20))
    )


@dataclass
class Overlap:
    score: float
    anchors: int


def row_consensus(pairs: list[tuple[Cell, float]]) -> Overlap | None:
    """Correspondences already enforce column order and one vertical translation.

    Four positions with three distinct strong artworks in three columns suffice;
    they may all belong to a single row. Repeated rows still need a unique offset.
    """
    if len(pairs) < 4 or not context_agrees([score for _, score in pairs]):
        return None
    anchors = [(cell, score) for cell, score in pairs if score >= ANCHOR_SCORE]
    if len(anchors) < 3 or len({cell.column for cell, _ in anchors}) < 3:
        return None
    distinct: list[np.ndarray] = []
    for cell, _ in anchors:
        if all(appearance_similarity(cell.image, image) < 0.90 for image in distinct):
            distinct.append(cell.image)
        if len(distinct) >= 3:
            break
    if len(distinct) < 3:
        return None
    scores = [score for _, score in pairs]
    return Overlap(float(0.6 * np.median(scores) + 0.4 * np.mean(scores)), len(anchors))
