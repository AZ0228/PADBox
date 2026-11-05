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

## Import a real box

Reference metadata and icons must be cached first. The primary source is the public PADDashFormation NA snapshot pinned to **2026-10-30**. No game account, paid API or network access is needed during matching/review once cached.

```sh
# Full reference coverage; first download is substantially larger than selected icons.
uv run padbox sources sync --all-icons

# Transfer your iPhone recording to your Mac, then:
uv run padbox import /path/to/recording.mov
uv run padbox review
# In Review: select the draft, resolve entries, Preview changes, then Apply.
# Or use the CLI after review:
uv run padbox scans list
uv run padbox scans diff SCAN_ID --output data/replacement-diff.json
# Inspect the diff file, then replace the saved box:
uv run padbox scans apply SCAN_ID --preview data/replacement-diff.json --full-box
uv run padbox export --format json --output collection.json
uv run padbox export --format csv --output collection.csv
```

`review` starts Streamlit at http://127.0.0.1:8501. Metadata, databases, icons, crops, annotated frames, summaries, and logs remain under `.padbox/` (ignored by Git). Use `padbox --home /another/path COMMAND` or `PADBOX_HOME` to choose another collection.

For a small source smoke test instead of the full icon cache:

```sh
uv run padbox sources sync --ids 13966,13967,13968
```

All enabled metadata is imported, but only requested icons participate in visual matching. Previously cached icons from the same snapshot are retained. A small icon set has poor recognition coverage; high similarity against it does not establish that an unknown card belongs to that set. Missing/failed icon downloads are logged; metadata remains searchable. To retry missing images, repeat sync. A failed metadata download leaves the current reference database intact. To adopt a newer snapshot intentionally, pass `--commit FULL_GITHUB_COMMIT_SHA`; see the source verification notes first.

## Recording and calibration

Use a consistent box sort/filter and portrait orientation. One distinctive complete overlapping row between sampled views can establish tracking; more overlap helps when the capture is blurred. Avoid opening cards or changing sort mid-recording. Check that the recording covers the box you intend to index, including the first and last rows. The tool cannot infer entries never shown, hidden assists, or quantities obscured by stack overlays.

```sh
uv run padbox inspect /path/to/recording.mov --output data/first-frame.png
uv run padbox import /path/to/recording.mov --interval 0.2 --layout layout.json
```

Use **Calibration** in the review UI to preview an image/first frame and save `layout.json`. Start with an ROI around the box and expected column count. Example:

```json
{"roi": [0.02, 0.20, 0.98, 0.88], "columns": 5}
```

Coordinates are normalized, not tied to an iPhone pixel resolution. Automatic detection uses square contours and repeated geometry inside the ROI. For aligned stills, calibration can force cell positions using normalized `x`, `y`, `size`, `step_x`, `step_y`, and `columns`. `size` is a fraction of screen **width**; `y`/`step_y` are fractions of **height**. For scrolling recordings, set `follow_scroll: true` with calibrated geometry. The detector fits row phase to repeated border edges on each sampled frame and rejects screens without that evidence. Forced grids without this option require aligned, fully visible rows. Always inspect annotated previews; current iPhone layout calibration is not a universal layout guarantee.

Every processed frame gets an annotated preview under `.padbox/recordings/<sha256>/`. Failed empty imports can retry with a corrected layout. Once observations exist, changing sampling/layout requires a separate `--home` so existing reviewed evidence cannot be overwritten. Identical-file imports with the same settings are idempotent; failed runs resume existing observations. Re-encoded files have different hashes and are treated as new recordings.

## Review and corrections

The live page has a keyboard review queue under **Session entries**. Load a saved scan,
then press **R** or click **Review next unresolved entry**. Accepting or rejecting advances
automatically, keeping the comparison pane open. Choose box order, lowest similarity first,
or highest similarity first, and filter for unresolved identities, unmatched entries, copy
tracking, stack quantities, or all entries. Separate copies remain separate queue items.

| Key | Action |
| --- | --- |
| **A** / **X** | Accept the chosen identity / reject the detection |
| **J** or **→** / **K** or **←** | Next / previous queued entry, without saving |
| **1–3** | Choose an alternative; press **A** to accept it |
| **/** / **I** / **Q** | Focus search / monster ID / quantity |
| **V** | Verify the entered slot quantity |
| **D** | Toggle the explicit separate-copy confirmation |
| **Escape** | Leave a text field; press again to return to the box |

Shortcuts do not act while typing, holding a key, using modifier keys, or saving a decision.
A failed save keeps the current entry and your choice open. The default unresolved queue
keeps stackable entries open until both identity and quantity are verified. Use **Identities
only** for an ID approval pass; quantities remain pending afterward. Uncheck **Advance after
resolving an entry** to stay on each resolved entry. Applied history remains read-only, and
approvals only update the draft; **Preview changes → Apply scan** still replaces ownership.

If exact material counts do not matter, click **Use quantity 1 for all pending stacks**
under **Session entries**. This sets every unresolved stack in the selected draft to 1
and resolves its quantity check, including entries outside the current queue/filter.
Already verified counts are kept. Separate slots stay separate; IDs and copy-tracking
issues still require approval. The same action is available in Streamlit. It works on
paused/completed drafts without a source connection. Counts exported after Apply use
these assumed values. Each decision is saved; you can still edit a slot quantity before Apply.

For a completed scan with many overlay-obscured proposals, use **Batch accept identities**
on the live page, immediately above Session entries. Load your existing saved scan; a source
connection or rescan is unnecessary. The Streamlit Review workspace has the same controls.

1. Set **Minimum similarity** (default **0.80**) and **Minimum lead over alternatives**
   (default **0.04**). Both accept any value from **0 to 1**. The lead is the best score
   minus the strongest alternative. The review threshold can go below 0.60; unmatched
   detections still stay excluded because they have no proposed ID.
2. Click **Preview qualifying matches**. The preview shows the qualifying count, excluded
   reasons, remaining identities and stack-quantity checks. Threshold-band buttons show how
   many qualify at other similarity levels while retaining your selected lead. Click a band
   to preview it. Lower thresholds accept more proposals and can include similar variants.
3. Compare the weakest qualifying captures and references first. The initial gallery shows
   24 entries; **Show more** loads another 24. Click any example for individual review.
4. Click **Accept N qualifying identities** to accept that exact preview in the draft.
   Changing thresholds or reviewing entries requires a fresh preview. Changes made from
   another tab invalidate the acceptance token as well. Each accepted copy has an audit event.
5. Resolve remaining identities and quantities, then use the existing **Preview changes →
   Apply scan** workflow to replace saved ownership.

Only uncertain identities with clear copy tracking, a local NA reference and saved ranked
evidence matching the proposal/score can qualify. Unmatched entries, exact ties, merged/rejected
copies and previous identity decisions are excluded. Stack identities can be accepted, but their
quantities remain unverified until explicitly checked. Batch acceptance preserves copy IDs,
duplicate counts, quantities, tracking and applied history. It does not change the automatic
matcher thresholds or turn a similarity score into a probability; this is your explicit bulk
review decision. **0.80 does not mean 80% accuracy.**

- Inspect the recording crop, proposed reference icon, similarity score, and alternatives. Search metadata by name or ID to correct a proposal.
- Strong, separated matches with clear tracking are accepted automatically inside new drafts. Inspect or correct them by disabling “Needs review only” / enabling “Include reviewed entries.” Lower-confidence results require acceptance. Similarity is not a probability.
- Resolve uncertain slot identity by checking **separate owned copy** or merging it into a known existing inventory entry. Entries visible in the same frame cannot be merged, including through prior merges.
- Reject false grid detections. Rejected/merged entries and review history remain stored as evidence.
- Add missed slots by ID to the selected draft. Verify stack quantities per slot. Unresolved observations remain separate when adjusting a count.

```sh
uv run padbox add 13966 --quantity 1 --scan SCAN_ID
uv run padbox entry-quantity INVENTORY_UUID 12  # Stackable items only
uv run padbox accept INVENTORY_UUID 13966
# Only after verifying that an ambiguous observation represents a separate slot:
uv run padbox accept INVENTORY_UUID 13966 --distinct
uv run padbox stats                 # Applied box
uv run padbox stats --scan SCAN_ID  # Draft
```

The matcher uses masked artwork, DCT hash, color histograms, pixel similarity and template correlation. Larger libraries use vectorized hash/color retrieval followed by reranking of 96 candidates. Full-library retrieval recall and thresholds still need real-data evaluation. To add more reference icons after an import and refresh unreviewed proposals:

```sh
uv run padbox sources sync --ids 1234,5678
uv run padbox rematch
```

Accepted identities, rejected entries and manually reviewed decisions are preserved. Later unobscured observations can improve an unreviewed proposal. Stackable references require identity and displayed xN quantity review before Apply. Single-frame automatic acceptance requires similarity ≥0.94 with a ≥0.055 lead over the next candidate, at least three references, and a non-stackable item. A second path accepts a non-stackable proposal after **three consecutive processed observations from distinct frames** agree on the ID, each with similarity ≥0.85 and a ≥0.055 lead, with at least three candidates. Both paths require clear copy tracking and preserve manual decisions. Exact unchanged frames skipped by the sampler do not count as additional evidence. The audit records the acceptance rule and supporting frame indices. Repetition measures consistency, not independent correctness; neither threshold has been calibrated against independently labeled real scans.

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
