"""Original procedural art, never game artwork. Isolated demo home only."""

import json
from pathlib import Path

import cv2
import numpy as np

from padbox.models import Layout


class Demo:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.icons = self.root / "icons"
        self.icons.mkdir(exist_ok=True)

    def generate(self) -> dict[str, Path]:
        images = []
        monsters = []
        for i in range(15):
            rng = np.random.default_rng(100 + i)
            image = rng.integers(25, 190, (80, 80, 3), dtype=np.uint8)
            image = cv2.GaussianBlur(image, (9, 9), 0)
            for _ in range(8):
                color = tuple(int(v) for v in rng.integers(10, 255, 3))
                center = tuple(int(v) for v in rng.integers(8, 70, 2))
                cv2.circle(image, center, int(rng.integers(4, 18)), color, -1)
            cv2.rectangle(image, (1, 1), (78, 78), (245, 245, 245), 2)
            path = self.icons / f"{i + 1}.png"
            cv2.imwrite(str(path), image)
            images.append(image)
            monsters.append(
                {
                    "monster_id": 900001 + i,
                    "name": f"Synthetic fixture {i + 1}",
                    "servers": ["NA"],
                    "icon": str(path.resolve()),
                    "provenance": {"source": "PADBox synthetic, NOT PAD cards", "version": "1"},
                }
            )
        manifest = self.root / "references.json"
        manifest.write_text(json.dumps(monsters, indent=2))
        # Two separate copies of fixture 1 in the first row; never collapsed by ID.
        sequence = [0, 1, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 2, 5, 8, 11]
        first = render_grid([images[i] for i in sequence[:15]])
        second = render_grid([images[i] for i in sequence[5:20]])
        image_path = self.root / "box.png"
        cv2.imwrite(str(image_path), first)
        shifted_path = self.root / "box-shifted.png"
        cv2.imwrite(str(shifted_path), second)
        video_path = self.root / "recording.avi"
        writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (500, 430))
        if not writer.isOpened():
            raise RuntimeError("OpenCV MJPG video encoder unavailable")
        try:
            for image in [first] * 10 + [second] * 10:
                writer.write(image)
        finally:
            writer.release()
        labels = []
        evaluation_dir = self.root / "evaluation"
        evaluation_dir.mkdir(exist_ok=True)
        rng = np.random.default_rng(42)
        for i, image in enumerate(images):
            for variant in ["plain", "overlay", "compressed"]:
                crop = image.copy()
                if variant == "overlay":
                    cv2.putText(
                        crop, "+297", (3, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1
                    )
                    cv2.circle(crop, (5, 5), 5, (255, 0, 255), -1)
                elif variant == "compressed":
                    noise = rng.normal(0, 2, crop.shape)
                    crop = np.clip(crop.astype(float) + noise, 0, 255).astype(np.uint8)
                    _, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    crop = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                name = f"{i}-{variant}.png"
                cv2.imwrite(str(evaluation_dir / name), crop)
                labels.append({"image": f"evaluation/{name}", "monster_id": 900001 + i})
        unknown = rng.integers(0, 255, (80, 80, 3), dtype=np.uint8)
        cv2.imwrite(str(evaluation_dir / "unknown.png"), unknown)
        labels.append({"image": "evaluation/unknown.png", "monster_id": None})
        labeled_path = self.root / "labels.json"
        labeled_path.write_text(
            json.dumps(
                {"dataset": "synthetic procedural icons v1; NOT real PAD", "examples": labels},
                indent=2,
            )
        )
        layout = self.root / "layout.json"
        layout.write_text(Layout(columns=5, roi=(0, 0.1, 1, 0.95)).model_dump_json(indent=2))
        return {
            "references": manifest,
            "image": image_path,
            "video": video_path,
            "labels": labeled_path,
            "layout": layout,
        }


def render_grid(
    images: list[np.ndarray],
    x: int = 20,
    y: int = 80,
    size: int = 80,
    step: int = 94,
    width: int = 500,
    height: int = 430,
) -> np.ndarray:
    frame = np.full((height, width, 3), 22, dtype=np.uint8)
    for i, image in enumerate(images):
        col, row = i % 5, i // 5
        px, py = x + col * step, y + row * step
        if 0 <= px and 0 <= py and px + size <= width and py + size <= height:
            frame[py : py + size, px : px + size] = cv2.resize(image, (size, size))
    return frame
