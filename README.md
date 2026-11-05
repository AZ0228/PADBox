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

## Full-box snapshots and duplicate copies

Each detected slot has its own local UUID. Within a scan, overlapping frames and scrolling backward reuse tracked slots; genuinely separate copies retain separate entries. A tracking gap remains a reviewable ambiguity. Do not deduplicate by monster ID.

Each new scan stands alone. It never merges with, appends to, or inherits acceptance from earlier scans. Only quantities by exact monster ID are compared between snapshots; the app does not claim to identify the same physical copy across scans. Base, evolved, transformed and assist IDs remain distinct. Capture the full box on every update.

Applying requires finished capture, at least one active slot, no unresolved identity/copy/quantity issues, and explicit full-box confirmation. The optional expected-slot count is an additional check. **The tool cannot prove that unshown rows, hidden assists, filters or obscured quantities were captured.** Confirm completeness against the game before Apply. Empty scans cannot clear the box accidentally. Diff totals while unresolved entries remain are provisional.

Inventory schema version 3 preserves existing observations and decisions, with per-scan map slots and checkpoints. The snapshot tables were introduced in version 2. The first new snapshot initializes the saved baseline from previously **confirmed** legacy ownership only. Old unconfirmed scan proposals remain evidence and are not counted as ownership; start a new full scan for the new workflow. No existing evidence is deleted. Applied snapshots and their before/after documents remain local history. If no entries were previously confirmed, the first diff starts from an empty saved box.

## Export semantics

Once snapshot mode is initialized, JSON/CSV exports contain only the **currently applied box**; drafts cannot leak into exports. JSON includes `revision` and `source_scan`. Duplicate counts and verified stack quantities are preserved. `complete: true` means the application checks passed and you attested full coverage; it is not an independently measured guarantee that every in-game slot was seen.

```json
{
  "schema_version": 1,
  "server": "NA",
  "revision": 1,
  "source_scan": "SCAN_ID",
  "monsters": [{"monster_id": 13966, "quantity": 2, "confirmed": true}],
  "unresolved": [],
  "complete": true
}
```

`padbox scans history` shows applied revisions and their additions/removals. Reapplying an already applied scan is rejected. If the draft or current box changes after a preview, preview again before applying. Previous unconverted collections retain legacy export semantics until the first new snapshot begins.
