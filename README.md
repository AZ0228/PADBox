# PADBox

A local Puzzle & Dragons **North America** full-box snapshot tool for macOS. Capture your entire box, review uncertain results and a quantity diff, then apply the scan as a complete replacement. Repeat full scans to track changes over time. Live iPhone previews, recordings and still images are supported. The team optimizer is intentionally deferred.

**Status:** working vertical slice and review/export MVP, validated with procedural fixtures. A first real iPhone recording has been processed with a calibrated layout, but independently labeled recognition/count accuracy is still **unmeasured**. Do not treat recognition scores, automatic grid detection, slot tracking, or recording completeness as verified on real gameplay yet. Stacked material quantities are not read; one visible slot initially means one copy.

## Setup

Python 3.12+, [uv](https://docs.astral.sh/uv/) and FFmpeg/FFprobe are recommended. FFprobe metadata has an OpenCV fallback. This Mac already has FFmpeg; a project Python 3.12 environment and lockfile have been created.

```sh
cd /Users/james/Documents/Code/FunCoding/PADBox
uv sync --python 3.12
uv run padbox --help
```

If `uv` is not on your PATH, `python3 -m uv` works with the installation made during project setup. For example, `python3 -m uv run padbox --help`.
