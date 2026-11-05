"""Sequential video sampling: grab intervening frames, decode only selected samples."""

import hashlib
import json
import logging
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

LOG = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class Frame:
    index: int
    timestamp: float
    image: np.ndarray


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metadata(path: Path) -> dict:
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Cannot decode image {path}")
        return {"kind": "image", "width": image.shape[1], "height": image.shape[0]}
    if shutil.which("ffprobe"):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode:
            raise ValueError(f"FFprobe cannot read video: {result.stderr[:500]}")
        return {"kind": "video", "ffprobe": json.loads(result.stdout)}
    LOG.warning("FFprobe is unavailable; using OpenCV metadata fallback")
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError(f"Cannot open video {path}")
        return {
            "kind": "video",
            "fps": capture.get(cv2.CAP_PROP_FPS),
            "frames": capture.get(cv2.CAP_PROP_FRAME_COUNT),
            "width": capture.get(cv2.CAP_PROP_FRAME_WIDTH),
            "height": capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
        }
    finally:
        capture.release()


def frames(path: Path, interval: float = 0.3) -> Iterator[Frame]:
    if interval <= 0:
        raise ValueError("Sampling interval must be positive")
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Cannot decode image {path}")
        yield Frame(0, 0.0, image)
        return
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError(f"Cannot open video {path}")
        capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not fps or not np.isfinite(fps):
            raise ValueError("Video has no usable frame rate; transcode with FFmpeg first")
        index, next_time = 0, 0.0
        while capture.grab():
            timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if timestamp <= 0 and index > 0:
                timestamp = index / fps
            if timestamp + 1e-6 >= next_time:
                ok, image = capture.retrieve()
                if not ok or image is None:
                    raise ValueError(f"Failed decoding frame {index}")
                yield Frame(index, timestamp, image)
                next_time = timestamp + interval
            index += 1
        if index == 0:
            raise ValueError("No video frames decoded")
    finally:
        capture.release()
