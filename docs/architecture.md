# PADBox architecture

## Scope

A local, review-first collection importer. Python 3.12, Typer CLI, OpenCV/FFprobe, Pydantic contracts, SQLite, Streamlit, uv and pytest. No optimizer, paid API, game credentials, or cloud processing. The primary source decision and limitations are in [data-sources.md](data-sources.md).

## Data flow

`source adapter → reference.sqlite + icon cache`

`image/video/live feed → sampled grid crops → within-scan tracking → ranked matches → isolated draft → review → diff → atomic full-box replacement → JSON/CSV`

Modules separate data sources, video decoding, grid detection, matching, inventory identity/review, persistence, UI, and export. Image input is a first-class path for calibration and debugging. Content hashes make identical-file reimports idempotent; failed imports resume without deleting reviewed decisions. Every observation stores its frame/time/rectangle, candidates, extracted crop and inventory-copy association. Debug images and logs are local artifacts.

## Persistence

Reference metadata and player inventory live in **separate SQLite files**, each with schema versioning. Logical monster IDs cross the boundary; reference refresh cannot cascade-delete inventory. Normalized tables cover monsters, server availability, skills, monster-skill roles, awakenings/ordered associations, typed evolution/transform edges, recordings, owned copies, observations, and review events. Raw source mechanics and provenance remain JSON alongside normalized searchable fields. Unknown fields remain null. Foreign keys within each database are enforced.

Each owned row represents one inventory slot within one capture; separate copies retain separate UUIDs. `quantity` is one for ordinary cards and the explicitly verified xN count for a stackable slot. Identity confirmation and copy-tracking certainty remain separate. Tracking gaps within a scan require distinct-copy confirmation or a same-scan merge. Cross-scan merging is forbidden for snapshots.

Inventory schema v2 adds `box_scan`, `box_state`, and `box_history` alongside quantity/verification columns on `owned`. Recording rows remain the common file/live evidence boundary. New recordings register an isolated snapshot draft before ingesting observations. Old recordings/reviews are retained, while the first snapshot initializes the baseline from confirmed legacy ownership only. Existing unconfirmed proposals are not treated as owned cards.

`inventory.snapshots` owns preview and apply. Preview aggregates exact monster-ID quantities, reports current/new counts, deltas, unresolved entries and readiness blockers. A SHA-256 token binds approval to the entire draft (including rejected/merged entries and quantities), capture state, and current box revision/document. Apply revalidates inside `BEGIN IMMEDIATE`, requires a finished nonempty draft, resolved identities/copy tracking/stack counts, full-box attestation, and an optional expected-slot check. It atomically replaces the current box and records immutable before/after documents and the diff. Concurrent applications from the same baseline cannot both succeed. Repeated application of an applied scan is rejected.

The current box is an immutable JSON document separate from draft evidence. Collection exports/statistics use it once snapshot mode begins; draft review uses an explicit recording scope. Strong auto-acceptance affects only draft entries and has an audit event. All changes to applied evidence are rejected; updates use another full scan. Prior scan evidence and revision history remain available, but never contribute to later scan counts. Identical rescans produce zero quantity changes. Coverage cannot be inferred from unseen game content: user attestation and optional slot-count validation remain necessary.

## Vision and uncertainty

Start with edge/contour square proposals and repeated row/column geometry (including a lower-contrast, gap-closing fallback), plus configurable normalized ROI and calibrated cell dimensions. Detection does not infer that every screen is the box. Fully visible cells only; inspect annotated debug frames. Calibration without `follow_scroll` requires aligned rows. With that option, repeated top/bottom border edges estimate the row phase in every frame; blank or weak-periodicity screens are rejected. Sampling avoids matching every decoded video frame; near-static sampled frames are logged/skipped. Sudden/no-grid frames break tracking continuity.

Overlap tracking compares candidate vertical translations and the appearance of corresponding same-column crops. A cumulative coordinate map retains historical slots during continuous overlap, so scrolling backward can reuse entries that left the viewport. A match needs multiple distinct strongly agreeing icons, strong median agreement across geometrically corresponding cells, and a unique displacement. The consensus can tolerate animated overlays in some cells. A fallback uses forward/backward-checked optical flow with distributed anchors, a dominant vertical translation, bounded displacement and residual crop agreement. Repeated patterns, jumps, transitions, and inadequate overlap start ambiguous tracks. A tracking failure must create review work, not silently remove owned copies. Indistinguishable duplicate rows and movement missed between identical sampled frames are fundamentally ambiguous; slow scrolling with pauses helps but does not prove completeness.

`inventory.box_map` persists spatial slots separately from identities. Inventory schema v3 adds `scan_map_slot` (local copy ID, scan ID, map section, column, global y, most recent rectangle and observation evidence) and `scan_map_state` (section pitch/uncertainty, offset, viewport, direction and position status). Positions/checkpoints commit in the same transaction as observations; an ingestion failure reloads durable map state. Rejected/merged entries are excluded from active map payloads. Previously recorded observations are retained during migration; historical positions are not inferred for pre-map scans.

Continuous overlap updates a global vertical offset, retaining map positions and local copy IDs when rows leave and re-enter the view. Content moving up means progressing down through the box; reversing that displacement means revisiting earlier rows. After failed overlap, a missing grid, restart or explicit continuity break, the map proposes offsets with a hash shortlist and verifies overlapping same-column positions using crop appearance and geometry. Shared `inventory.consensus` permits a single distinctive ordered row: at least four corresponding positions, three ≥0.90 appearance anchors with distinct artwork across three columns, median similarity ≥0.88, at least half the positions ≥0.90, and at most 20% (minimum allowance one) below 0.72. Location ranking combines median/mean evidence and still requires ≥0.025 separation from competing placements. This replaces the old six-anchor/two-row requirement. Whole-overlap consensus tolerates overlays without requiring every icon to match nearly exactly. Once a unique translation is established, a returning cell at one known position reuses that slot even when its overlay changed. Monster recognition thresholds remain separate.

The liberal default assumes ordered rows are distinctive within the observed box, as requested. Repeated row signatures with competing offsets remain ambiguous. Direction orders hypotheses but cannot break an appearance tie, and matching never deduplicates by monster ID. A visually identical unseen row cannot be distinguished from a revisited row using images alone. Completely unseen gaps cannot be filled from scrolling direction; the map does not certify completeness.

The API returns a `box_map` for each live session, including sections, slots and the last captured viewport. Reloading a session reads it without constructing the matcher or loading all templates. Relocalization loads the first and latest 64px crop templates for active mapped slots only when a processor is needed. Both observed appearances can propose/verify an offset, retaining earlier artwork when later overlays change. They remain independent of monster recognition IDs. The minimap renders the full scanned extent as a canvas with a cyan captured-window overlay and a white independent-browsing overlay; unknown joins are separated and labeled. Drag, wheel and keyboard navigation display archived slots in the reconstructed pane and do not control the phone. Selecting a slot invokes the same durable per-copy review service. Follow live restores sampled frame alignment. Cached reference images decorate the map; identity/tracking status colors remain visible even when no cached thumbnail is available.

While adjacent overlap continues inside an uncertain section, relocation also searches other sections, excluding the active section's self-match. A unique ordered-row bridge proposes the coordinate translation. Resume and newly added positions additionally reconcile **all saved sections**, using first/latest source and target appearances. Pairwise placement prefers the anchored map, then older fragments, and requires a unique location within that target. Overlapping duplicate fragments no longer veto each other merely by appearing as separate destinations. Every overlapping position participates in robust context validation; row pitch, confirmed IDs, human source reviews and recursive same-frame co-occurrence remain safeguards. Where legacy map positions have several candidates, a unique appearance match can select the target; ties block that join.

Reconciliation first repairs legacy duplicate positions using ordered row consensus. Partial duplicate rows require stable neighboring copy IDs observed in the same screen row alongside both appearances, traversing prior merged descendants. This uses recorded copy identities, geometry and artwork rather than repeated monster IDs. Canonical entries retain the earliest evidence; reviewed source decisions, confirmed-ID conflicts and simultaneous copies cannot be merged. Duplicate source slots become audited `merged` rows; observations are never rewritten/deleted. Nonoverlapping source slots retain their UUIDs and move by the established translation. Only section-placement uncertainty clears when joined to an anchored map. Aliases compose across joins, updating visible IDs and the active viewport/scroll offset even when that section was off screen. Joins and map checkpoints share one transaction; failed resume repair or ingestion reloads durable state. `map_join` audit events permit later proposal improvements/rematching. Applied history is immutable, and current ownership still changes only through explicit diff Apply.

Matching combines normalized central artwork, DCT perceptual hash, HSV histogram, and normalized image correlation/pixel distance. With more than 96 reference icons, vectorized hash/color retrieval shortlists 96 for detailed reranking; real-data retrieval recall remains unqualified. Overlays/borders are masked by an interior crop. Score and runner-up margin gate a **strong draft match** (≥0.94 score, ≥0.055 margin, at least three references). Strong matches with clear tracking are accepted automatically in snapshot drafts, with an `auto_accept` audit event; user corrections take precedence. Lower-confidence results remain proposals. Scores are similarity heuristics, not calibrated probabilities. Reference coverage affects confidence; even a perfect match against a tiny set does not establish closed-world correctness. Stackable references are excluded from auto-acceptance because visible slot identity does not establish xN quantity. Applying a whole draft always requires an explicit diff approval.

A sequence-level acceptance path additionally requires the three most recent saved observations to agree on the proposed ID, with score ≥0.85, margin ≥0.055, three returned candidates and three distinct frame indices. It still requires clear copy tracking, no manual review and a non-stackable reference. Exactly unchanged skipped samples supply no new observations. Successful acceptance sets the entry's strong-evidence flag and records `repeated_separated` plus supporting frame indices in the audit; the existing ≥0.94 path records `single_strong`. Single-crop evaluation still measures the unchanged single-frame matcher gate, so its metrics must not be represented as sequence-policy accuracy. Correlated observations can repeatedly agree on the wrong variant; real labeled sequence evaluation remains necessary.

An evaluation harness reports top-1/top-3, high-score coverage, false-high-score rate (both per high-score prediction and per labeled example), unknown rejection, and dataset size. Embeddings are deferred until a real labeled baseline demonstrates benefit; synthetic tests alone cannot justify model complexity or confidence calibration.

`inventory.batch` provides explicit bulk identity review independently of automatic acceptance.
`BatchPolicy` validates similarity (0–1.00, default 0.80) and runner-up separation
(0–1.00, default 0.04). Preview operates on a consistent inventory read transaction and uses
the same representative evidence as entry review: the saved top candidate must match both
the proposed ID and stored score, with at least one ranked alternative. Only uncertain,
clear-tracking entries with a local NA reference qualify. Exact ties, missing evidence,
unmatched/inactive copies and previous identity decisions stay excluded. Quantity-only review
does not block identity acceptance. Stack identity acceptance leaves quantity verification intact.
The explicit batch threshold has no 0.60 floor. The matcher's separate proposal cutoff still
leaves sub-0.60 detections unmatched with no proposed ID; lowering the review threshold does
not assign those IDs or include unmatched entries automatically.

Preview returns exact per-copy candidates, weakest-first order, excluded reason counts,
cumulative threshold counts at the selected separation, remaining identity/quantity work and a
SHA-256 token over scan state, policy, copy rows, evidence, review events and relevant references.
Acceptance recomputes eligibility under `BEGIN IMMEDIATE` and rejects stale tokens before any
write. All qualifying identities and their `batch_accept` audit events commit atomically; a
failure rolls back the entire batch. Events retain the batch UUID, thresholds, score, margin,
supporting observation and preview token. Copy IDs, quantities, tracking, confidence flags and
current/applied collection documents remain unchanged. Batch review requires paused or completed
capture and an editable draft; applied history and running captures are rejected.

The local HTTP API exposes authenticated, same-origin POST `/api/scans/{id}/batch-preview` and
`batch-accept`, sharing the live session lock. Browser preview counts and a paginated 24-pair
gallery let users inspect weakest qualifying results first. Changing thresholds or any rendered
session state clears approval; server token validation also handles other tabs. The Streamlit
workspace calls the same service. User-approved batch decisions do not become calibrated
high-confidence matcher predictions; automatic gates and evaluation metrics are unchanged.

## Live capture and shared frame processing

`inventory.service.assume_pending_stack_quantities` provides an explicit bulk count-as-one
decision for a paused/completed draft. It resolves all active, unverified stackable slots
using their confirmed or proposed reference ID, without changing identity/tracking status
or already verified counts. One immediate transaction updates quantities and records per-copy
`quantity` events with `method=assume_one`, previous values and a shared batch UUID. Failures
roll back the whole action. The authenticated `POST /api/scans/{id}/stack-quantity-one`
endpoint takes an empty object and shares the live session lock; Streamlit calls the same
service. Resolved quantity warnings disappear, and saved batch/diff previews become stale.
Applied history remains immutable; replacing saved ownership still requires a fresh diff Apply.

The bundled `review-queue.js` computes review membership and ordering from saved per-copy
entries and map coordinates. Filters separate identity, unmatched, tracking and quantity work;
ordering supports box positions and either confidence direction. Review navigation wraps
without saving. Successful accept/reject/merge/quantity actions advance when the current
entry is resolved or leaves the selected queue. The default pending queue retains incomplete
stack entries; an identity-only pass advances while leaving quantity verification pending.
Removal preserves the previous ordering so rejection/merging selects the next copy correctly.

Keyboard and pointer actions share the existing review API. Selection and saves hold a UI
lock to prevent repeated input from affecting the next entry; failed requests preserve the
selection and unsaved identity. Accept waits for comparison image loading to settle, and stale
images/search responses cannot replace the newly selected entry. Editable-field input,
composition, modifiers and held-key repeats are excluded from global shortcuts. Applied
snapshots remain navigable but cannot be edited. Queue controls require no source connection
and add no schema or ownership mutation path; saved review events survive reload as before.

`padbox live` serves a small browser UI and HTTP API on **127.0.0.1:8502**, while the existing Streamlit collection review remains on port 8501. JavaScript uses `getDisplayMedia` from an explicit user click; the browser owns the source picker and screen-capture permission. A crop overlay removes window chrome/letterboxing before encoding a JPEG locally. Only the selected crop reaches Python. No audio, cloud service, phone credential or authenticated game endpoint is involved.

Both file import and live capture call `inventory.processor.FrameProcessor`. It holds incremental tracking/appearance state and writes observations plus per-copy proposals transactionally per frame. The importer remains a file-reader wrapper, retaining content-hash idempotency and existing decisions. Live sessions use UUID recording IDs, immutable layout/crop configuration, per-frame sequence IDs and increasing timestamps. Checkpoints in recording metadata store committed sequence/timestamp, image dimensions and counters; snapshot isolation uses the version 2 tables described above.

The live browser allows only one upload at a time, drops the notion of a queued video backlog, and waits 300ms after each processed response before sampling again. Processing latency is displayed and slow captures advise slower scrolling. This is sampled feedback, not a guarantee of video-rate recognition. The browser keeps a full-motion source preview and overlays results on the exact sampled image. Clicking a detection pauses and opens crop/reference alternatives; identity acceptance, rejection, search, same-scan merge and current-box export use shared services. Only strong matches with clear tracking are accepted automatically within the draft. Lower-confidence identities and stack quantities require review. The current box remains untouched until Preview changes and Apply.

Capture continues through `no_grid` and `tracking_warning` results by default. An unchecked, visible toolbar control enables optional automatic pausing. Warnings, ambiguous tracks and full-box Apply blockers remain intact; continuous ingestion does not certify copy counts or conceal tracking gaps. Clicking a detection still pauses for review, while transport/decoder/size failures and source disconnection still stop capture. The reconstructed “Track?” badge distinguishes copy-tracking uncertainty from an identity proposal. Toolbar height is measured with ResizeObserver so wrapped controls do not cover the workspace.

Pauses retain in-memory overlap state only while the source remains consistent. A newly selected feed, restart, sequence gap or missing grid breaks continuity. A wall-clock delay over five seconds only logs a warning: browser rendering, thumbnail requests and uploads can delay frames without moving the box. Visual overlap determines whether continuity holds; failed overlap still invokes map relocation or starts an uncertain section. Resume reconciles saved fragments transactionally before capture; after an explicit gap, ordered-row relocalization must establish a unique position or new observations remain ambiguous. A dimension change pauses before importing that frame. Source crops persist with the session. Completed draft captures cannot ingest more frames but review remains editable until Apply. Applied scans are immutable history. A server restart marks previously running sessions paused, and restarting capture does not discard accepted/rejected decisions.

Crop HTTP requests query only the requested inventory ID and its indexed observations instead of rereading all recordings for every thumbnail. Reference images remain keyed by monster ID when a score improves, avoiding image removal/reload on unchanged identity. Reconstructed artwork fills its tile absolutely; container-relative compact labels leave the center visible at narrow pane sizes. Counts remain explicitly provisional while copy tracking is unresolved.

The default desktop workspace places capture and reconstruction in equal columns. Both use the captured frame aspect ratio and identical normalized cell coordinates from `result.cells`, so the reference tiles align with the sampled phone image. Reconstruction nodes are keyed by local inventory UUID, preserving separate copies and moving surviving nodes as overlap tracking shifts their coordinates. No monster-ID deduplication or speculative rows are introduced. Accepted/proposed/unmatched/copy/quantity states remain visible; unknown slots and missing reference images use capture evidence. Position transitions are disabled on tracking warnings and when reduced motion is requested. A missing grid removes stale tiles.

Clicking a tile in either pane pauses ingestion and opens the existing comparison/review controls on the right. Closing review or resuming returns to the reconstruction; accepted corrections replace the displayed reference image. The shared sampled-frame boundary is retained: this is not additional per-video-frame recognition. Browser tests cover spatial alignment, tracked tile reuse on scroll, duplicate copies, review transitions, corrected icons, unknown/missing assets, and responsive stacking.

The service binds only to loopback, enforces its displayed Host, and requires a per-process token plus same-origin checks on API requests. JPEG size/dimensions and sequence/timestamp contracts are bounded before processing; arbitrary filesystem paths are not HTTP endpoints. Local HTML/CSS/JS are bundled package resources, with no remote frontend libraries. Reference/crop images and exports require the same token. These controls keep this a local tool; there is no remote hosting or sharing feature.

Automated validation covers the HTTP and persistence boundaries and a real headless Chromium UI driven by an original synthetic `canvas.captureStream` feed. Browser tests exercise the actual frontend, HTTP, matcher, SQLite and review flow but replace the OS source picker. Physical QuickTime/iPhone capture and macOS permission prompts must be validated separately and are not claimed by those tests.

## Highest-risk assumptions

1. Current NA icons and metadata remain accessible and consistently numbered; mitigate with pinned snapshots, hashes, cached/local adapters and integrity checks.
2. Small icon crops distinguish near-identical variants despite overlays/compression; measure on labeled real PAD data before claiming reliability.
3. Scroll overlap identifies unique inventory entries; repeated rows and sampling gaps can make this impossible. Preserve ambiguity and require human reconciliation.
4. Generic contour detection transfers to the current iPhone box UI; retain ROI/layout calibration and annotated frames. A first recording required a calibrated border-phase detector; generalization to other devices/layouts is unqualified.
5. A recording reveals every owned item and quantity. Filters, stackable materials, assists, partial rows and hidden content can violate this; MVP counts visible slots only and does not OCR stacked quantities.
