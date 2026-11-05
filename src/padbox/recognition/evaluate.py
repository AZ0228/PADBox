"""Evaluate labeled crops; dataset provenance is part of every result."""

import json
from pathlib import Path

import cv2

from padbox.recognition.matcher import Matcher


def evaluate(matcher: Matcher, manifest: Path) -> dict:
    data = json.loads(manifest.read_text())
    examples = data["examples"]
    if not examples:
        raise ValueError("Evaluation set is empty")
    top1 = top3 = high = false_high = unknowns = rejected_unknowns = known = 0
    errors = []
    for example in examples:
        path = (manifest.parent / example["image"]).resolve()
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Unreadable labeled image {path}")
        result = matcher.match(image)
        ids = [c.monster_id for c in result.candidates]
        label = example.get("monster_id")
        correct = bool(ids and ids[0] == label)
        if label is not None:
            known += 1
            top1 += correct
            top3 += label in ids
        else:
            unknowns += 1
            rejected_unknowns += not result.high_confidence
        high += result.high_confidence
        false_high += result.high_confidence and not correct
        if not correct and label is not None or result.high_confidence and label is None:
            errors.append(
                {
                    "image": str(path),
                    "label": label,
                    "predicted": ids,
                    "high_confidence": result.high_confidence,
                }
            )
    return {
        "dataset": data.get("dataset", "unspecified"),
        "examples": len(examples),
        "known_examples": known,
        "unknown_examples": unknowns,
        "top1_accuracy": top1 / known if known else None,
        "top3_accuracy": top3 / known if known else None,
        "high_confidence_predictions": high,
        "false_high_confidence_count": false_high,
        "high_confidence_coverage": high / len(examples),
        "false_high_confidence_rate": false_high / high if high else None,
        "false_high_per_example": false_high / len(examples),
        "unknown_rejection_rate": rejected_unknowns / unknowns if unknowns else None,
        "errors": errors,
        "limitation": "Similarity thresholds are uncalibrated until evaluated on real PAD crops.",
    }
