"""Contour grid baseline with normalized calibration fallback and debug overlays."""

from dataclasses import dataclass

import cv2
import numpy as np

from padbox.models import Layout


@dataclass
class Cell:
    x: int
    y: int
    width: int
    height: int
    column: int
    image: np.ndarray

    @property
    def rect(self) -> list[int]:
        return [self.x, self.y, self.width, self.height]


def _iou(a: tuple, b: tuple) -> float:
    x = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    intersect = x * y
    return intersect / max(1, a[2] * a[3] + b[2] * b[3] - intersect)


def detect(image: np.ndarray, layout: Layout | None = None) -> list[Cell]:
    baseline = _detect(image, layout, low_contrast=False)
    if len(baseline) >= 6:
        return baseline
    fallback = _detect(image, layout, low_contrast=True)
    return fallback if len(fallback) > len(baseline) else baseline


def _detect(image: np.ndarray, layout: Layout | None, low_contrast: bool) -> list[Cell]:
    layout = layout or Layout()
    height, width = image.shape[:2]
    rx1, ry1, rx2, ry2 = layout.roi
    left, top, right, bottom = (
        round(rx1 * width),
        round(ry1 * height),
        round(rx2 * width),
        round(ry2 * height),
    )
    if layout.size is not None:
        size = round(layout.size * width)
        if size < 8:
            raise ValueError("Calibrated icon size is too small")
        result = []
        y = layout.y * height
        if layout.follow_scroll:
            phase = _scroll_phase(image, layout)
            if phase is None:
                return []
            y = phase
        while round(y) + size <= bottom:
            for col in range(layout.columns):
                x = round((layout.x + col * layout.step_x) * width)
                iy = round(y)
                if (
                    layout.follow_scroll
                    and x >= 0
                    and x + size <= width
                    and image[iy : iy + size, x : x + size].std() < 3
                ):
                    continue
                if x >= left and iy >= top and x + size <= right:
                    result.append(Cell(x, iy, size, size, col, image[iy : iy + size, x : x + size]))
            y += layout.step_y * height
        return result
    region = image[top:bottom, left:right]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    contours = []
    # Flat dark icon backgrounds may have much lower contrast than their artwork.
    for low, high in [(12, 40)] if low_contrast else [(45, 130)]:
        edges = cv2.Canny(gray, low, high)
        found, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(found)
        if low_contrast:
            closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
            found, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            contours.extend(found)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if (
            0.055 * width <= w <= 0.32 * width
            and 0.87 <= w / max(1, h) <= 1.15
            and x > 0
            and y > 0
            and x + w < region.shape[1]
            and y + h < region.shape[0]
        ):
            # Prefer rectangular border contours over square-ish artwork features.
            if cv2.contourArea(contour) / (w * h) >= 0.65:
                boxes.append((x + left, y + top, w, h))
    kept = []
    for box in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
        if all(_iou(box, other) < 0.65 for other in kept):
            kept.append(box)
    if not kept:
        return []
    # Find the size cluster with the strongest repeated horizontal rows.
    best: list[tuple] = []
    best_score = 0
    for seed in kept:
        cluster = [b for b in kept if abs(b[2] - seed[2]) < seed[2] * 0.13]
        rows: list[list[tuple]] = []
        for box in sorted(cluster, key=lambda b: (b[1], b[0])):
            row = next((r for r in rows if abs(r[0][1] - box[1]) < seed[2] * 0.2), None)
            if row is None:
                rows.append([box])
            else:
                row.append(box)
        regular_rows = []
        for row in rows:
            row.sort(key=lambda b: b[0])
            if len(row) < 3 or (layout.columns and len(row) > layout.columns):
                continue
            dx = np.diff([b[0] for b in row])
            if dx.min() >= seed[2] * 0.85 and dx.max() - dx.min() < seed[2] * 0.20:
                regular_rows.append(row)
        score = sum(len(r) for r in regular_rows)
        if score > best_score:
            best_score = score
            best = [b for r in regular_rows for b in r]
    if not best:
        return []
    # A common x lattice gives columns stable identities across vertical scroll.
    size = float(np.median([b[2] for b in best]))
    xs: list[float] = []
    for box in sorted(best, key=lambda b: b[0]):
        if not xs or abs(box[0] - xs[-1]) > size * 0.25:
            xs.append(float(box[0]))
    if len(xs) < 3 or len(xs) > 12:
        return []
    result = []
    # Include short final rows only when their cells align to an established lattice.
    for box in kept:
        x, y, w, h = box
        col = int(np.argmin([abs(x - px) for px in xs]))
        if abs(w - size) < size * 0.13 and abs(x - xs[col]) < size * 0.12:
            result.append(Cell(x, y, w, h, col, image[y : y + h, x : x + w]))
    return sorted(result, key=lambda c: (round(c.y / (size * 0.3)), c.x))


def _scroll_phase(image: np.ndarray, layout: Layout) -> int | None:
    """Fit a vertical lattice to horizontal top/bottom border edges across columns.

    Calibrated geometry supplies scale and columns; every frame supplies its row phase.
    Reject screens lacking sufficiently strong periodic border evidence.
    """
    height, width = image.shape[:2]
    size = round(layout.size * width)
    step = round(layout.step_y * height)
    top, bottom = round(layout.roi[1] * height), round(layout.roi[3] * height)
    bottom = min(bottom, height - 1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    edges = np.abs(np.diff(gray, axis=0))
    lanes = []
    for column in range(layout.columns):
        x = round((layout.x + column * layout.step_x) * width)
        left, right = x + round(size * 0.28), min(width, x + round(size * 0.90))
        if left < 0 or right <= left:
            return None
        lanes.append(np.mean(edges[:, left:right], axis=1))
    projection = np.mean(lanes, axis=0)
    projection = cv2.dilate(projection.reshape(-1, 1), np.ones((5, 1), np.uint8)).ravel()
    scores = []
    for phase in range(step):
        rows = list(range(top + phase, bottom - size + 1, step))
        scores.append(
            float(np.mean([projection[y] + 0.7 * projection[y + size - 1] for y in rows]))
            if len(rows) >= 2
            else 0
        )
    best = int(np.argmax(scores))
    if scores[best] < 12 or scores[best] < float(np.median(scores)) * 1.5:
        return None
    return top + best


def annotate(image: np.ndarray, cells: list[Cell]) -> np.ndarray:
    debug = image.copy()
    for i, cell in enumerate(cells):
        cv2.rectangle(
            debug, (cell.x, cell.y), (cell.x + cell.width, cell.y + cell.height), (0, 255, 0), 2
        )
        cv2.putText(
            debug,
            f"{i}:c{cell.column}",
            (cell.x, max(12, cell.y - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (0, 255, 0),
            1,
        )
    return debug
