"""Cheap ensemble baseline with explicit rejection and a runner-up margin."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from padbox.models import Candidate, Match

LOG = logging.getLogger(__name__)
MIN_CANDIDATE_SCORE = 0.60
HIGH_SCORE = 0.94
MIN_MARGIN = 0.055


def normalize(image: np.ndarray) -> np.ndarray:
    if image is None or min(image.shape[:2]) < 8:
        raise ValueError("Icon must be a nonempty image at least 8px square")
    # Excludes corners (attributes/favorite) and bottom (level/enhancement) consistently.
    resized = cv2.resize(image, (64, 64), interpolation=cv2.INTER_AREA)
    return resized[10:48, 10:54].astype(np.float32) / 255.0


@dataclass
class Features:
    pixels: np.ndarray
    hash: np.ndarray
    histogram: np.ndarray
    gray: np.ndarray


def features(image: np.ndarray) -> Features:
    pixels = normalize(image)
    gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
    dct = cv2.dct(cv2.resize(gray, (32, 32)))[:8, :8].flatten()[1:]
    phash = dct > np.median(dct)
    hsv = cv2.cvtColor((pixels * 255).astype(np.uint8), cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
    hist = cv2.normalize(hist, None, norm_type=cv2.NORM_L1)
    return Features(pixels, phash, hist, gray)


def appearance_similarity(a: np.ndarray, b: np.ndarray) -> float:
    # Small contour jitter/compression must not turn an unchanged slot into a new copy.
    first = cv2.GaussianBlur(normalize(a), (3, 3), 0)
    second = cv2.GaussianBlur(normalize(b), (3, 3), 0)
    query = first[2:-2, 2:-2]
    errors = [
        float(np.mean(np.abs(query - second[y : y + query.shape[0], x : x + query.shape[1]])))
        for y in range(5)
        for x in range(5)
    ]
    return float(np.clip(1 - min(errors) * 2, 0, 1))


class Matcher:
    def __init__(self, references: list[dict]):
        self.references: list[tuple[dict, Features]] = []
        for ref in references:
            if not ref.get("icon"):
                continue
            image = cv2.imread(str(ref["icon"]))
            if image is None:
                LOG.warning("Missing/unreadable reference icon #%s: %s", ref["id"], ref["icon"])
                continue
            self.references.append((ref, features(image)))
        self.reference_by_id = {ref["id"]: ref for ref, _ in self.references}
        self.hashes = np.array([f.hash for _, f in self.references], dtype=bool)
        self.root_histograms = np.array(
            [np.sqrt(f.histogram.flatten()) for _, f in self.references], dtype=np.float32
        )

    def match(self, image: np.ndarray, top_k: int = 3) -> Match:
        query = features(image)
        ranked: list[Candidate] = []
        # Vectorized hash + color retrieval; expensive image correlation only on a shortlist.
        # Retrieval recall must be measured on real PAD labels before production claims.
        indices = np.arange(len(self.references))
        if len(self.references) > 96:
            hash_scores = np.mean(self.hashes == query.hash, axis=1)
            coefficients = self.root_histograms @ np.sqrt(query.histogram.flatten())
            color_scores = 1 - np.sqrt(np.clip(1 - coefficients, 0, 1))
            coarse_scores = 0.65 * hash_scores + 0.35 * color_scores
            indices = np.argsort(-coarse_scores, kind="stable")[:96]
        for index in indices:
            ref, target = self.references[index]
            phash = float(np.mean(query.hash == target.hash))
            histogram = 1 - float(
                cv2.compareHist(query.histogram, target.histogram, cv2.HISTCMP_BHATTACHARYYA)
            )
            mse = float(np.mean(np.abs(query.pixels - target.pixels)))
            pixel = max(0.0, 1 - 2 * mse)
            # Normalized template correlation, gated for constant/low-texture patches.
            if query.gray.std() < 0.005 or target.gray.std() < 0.005:
                corr = 0.0
            else:
                corr = float(cv2.matchTemplate(query.gray, target.gray, cv2.TM_CCOEFF_NORMED)[0, 0])
                corr = max(0.0, min(1.0, corr))
            score = float(
                np.clip(0.25 * phash + 0.15 * histogram + 0.35 * pixel + 0.25 * corr, 0, 1)
            )
            ranked.append(
                Candidate(
                    monster_id=ref["id"],
                    name=ref["name"],
                    score=score,
                    reference_icon=str(Path(ref["icon"]).resolve()),
                    components={
                        "phash": phash,
                        "histogram": histogram,
                        "pixels": pixel,
                        "template": corr,
                    },
                )
            )
        ranked.sort(key=lambda c: (-c.score, c.monster_id))
        margin = ranked[0].score - ranked[1].score if len(ranked) > 1 else 0
        high = len(ranked) >= 3 and ranked[0].score >= HIGH_SCORE and margin >= MIN_MARGIN
        reason = (
            "high similarity and separation; human acceptance required"
            if high
            else "no references"
            if not ranked
            else "low score, close alternatives, or fewer than three references"
        )
        if ranked:
            raw = self.reference_by_id[ranked[0].monster_id].get("raw", {})
            raw = json.loads(raw) if isinstance(raw, str) else raw
            if raw.get("stackable"):
                high = False
                reason = "Stackable item: verify displayed quantity manually before counting copies"
        return Match(candidates=ranked[:top_k], high_confidence=high, reason=reason)
