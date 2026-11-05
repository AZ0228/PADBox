"""Local Streamlit review, calibration and explicit per-copy reconciliation."""

import json
import os
from pathlib import Path

import cv2
import streamlit as st

from padbox.database.store import Store
from padbox.detection.grid import annotate, detect
from padbox.export.collection import collection, csv_export
from padbox.inventory.batch import BatchPolicy, accept_batch, preview_batch
from padbox.inventory.service import (
    accept,
    add,
    assume_pending_stack_quantities,
    entries,
    merge,
    reject,
    set_entry_quantity,
    set_quantity,
    statistics,
)
from padbox.inventory.snapshots import apply_scan, history, preview_scan, scan_list
from padbox.models import Layout
from padbox.video.reader import frames


def run() -> None:
    st.set_page_config(page_title="PADBox review", page_icon="🐉", layout="wide")
    store = Store(Path(os.environ.get("PADBOX_HOME", ".padbox")))
    st.title("PADBox collection review")
    st.caption("North America · Full-box replacement drafts · Review changes before Apply")
    page = st.sidebar.radio("Workspace", ["Review", "Collection", "Calibration"])
    if page == "Calibration":
        calibration()
        return
    scans = scan_list(store)
    scan_id = None
    if page == "Review" and scans:
        by_scan = {r["recording_id"]: r for r in scans}
        scan_id = st.selectbox(
            "Scan draft / history",
            list(by_scan),
            format_func=lambda key: (
                f"{key[:12]} · {by_scan[key]['captured_at']} · {by_scan[key]['status']}"
            ),
        )
        replacement_controls(store, scan_id)
    stats = statistics(store, scan_id)
    columns = st.columns(4)
    for i, key in enumerate(
        [
            "detected_inventory_entries",
            "confirmed_entries",
            "uncertain_entries",
            "unmatched_entries",
            "unique_monster_ids",
            "total_owned_copies",
            "unresolved_tracking",
        ]
    ):
        columns[i % 4].metric(key.replace("_", " ").title(), stats[key])
    st.caption(
        "Owned copies counts confirmed entries. Ambiguous tracks may represent repeated views."
    )
    refs = {r["id"]: r for r in store.references()}
    rows = entries(store, scan_id)
    if page == "Collection":
        st.dataframe(collection(store)["monsters"], width="stretch")
        if not scans:
            manual_controls(store)
        else:
            st.caption("This is the currently applied box. Review a new full scan to replace it.")
            st.json(history(store))
        st.download_button(
            "Download JSON",
            json.dumps(collection(store), indent=2),
            "padbox-collection.json",
            "application/json",
        )
        st.download_button("Download CSV", csv_export(store), "padbox-collection.csv", "text/csv")
        return
    if scan_id and by_scan[scan_id]["status"] == "applied":
        st.info("Applied snapshot: read-only history. Start a new full scan to update your box.")
        st.dataframe(rows, width="stretch")
        return
    if scan_id:
        manual_controls(store, scan_id)
    batch = [
        r
        for r in rows
        if r["high_confidence"] and r["tracking"] == "clear" and r["status"] == "uncertain"
    ]
    if scan_id:
        batch_controls(store, scan_id)
        pending_stacks = [
            r
            for r in rows
            if r["status"] not in {"merged", "rejected"}
            and not r["quantity_verified"]
            and bool(
                json.loads(refs.get(r["monster_id"] or r["proposed_id"], {}).get("raw", "{}")).get(
                    "stackable"
                )
            )
        ]
        st.caption(
            f"{len(pending_stacks)} pending stack quantities. Skip exact material counts by "
            "counting each pending stack as 1. Already verified quantities are kept."
        )
        if st.button("Use quantity 1 for all pending stacks", disabled=not pending_stacks):
            try:
                assume_pending_stack_quantities(store, scan_id)
                for pending in pending_stacks:
                    st.session_state.pop(f"quantity-{pending['id']}", None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if not scan_id:
        with st.expander(f"Legacy strong-match review · {len(batch)} proposals", expanded=False):
            st.write(
                "Scores are heuristic similarities. Inspect crops and reference coverage "
                "before accepting. Ambiguous slots are excluded."
            )
            st.dataframe(
                [
                    {
                        "entry": r["id"],
                        "id": r["proposed_id"],
                        "name": refs.get(r["proposed_id"], {}).get("name", "Unknown"),
                        "score": r["confidence"],
                    }
                    for r in batch
                ],
                width="stretch",
            )
            if st.button(f"Accept these {len(batch)} proposals", disabled=not batch):
                for row in batch:
                    accept(store, row["id"], row["proposed_id"])
                st.rerun()
    include_reviewed = st.checkbox("Include reviewed entries", value=False)
    visible = [
        r
        for r in rows
        if r["status"] != "merged"
        and (
            include_reviewed
            or r["status"] in {"uncertain", "unmatched"}
            or (
                not r["quantity_verified"]
                and json.loads(
                    refs.get(r["monster_id"] or r["proposed_id"], {}).get("raw", "{}")
                ).get("stackable")
            )
        )
    ]
    if not visible:
        st.info("No entries to review. Import a recording or add copies on the Collection page.")
        return
    by_id = {r["id"]: r for r in visible}

    def label(inventory_id: str) -> str:
        row = by_id[inventory_id]
        mid = row["monster_id"] or row["proposed_id"]
        return (
            f"{inventory_id[:8]} · #{mid or '?'} "
            f"{refs.get(mid, {}).get('name', 'Unmatched')} · {row['status']} · {row['tracking']}"
        )

    selected = st.selectbox("Inventory entry", list(by_id), format_func=label)
    row = by_id[selected]
    st.code(selected, language=None)
    left, right = st.columns(2)
    with left:
        st.subheader("Recording crop")
        if row["crop"] and Path(row["crop"]).exists():
            st.image(row["crop"], width=240)
            if row.get("source_timestamp") is not None:
                st.caption(f"Recording position: {row['source_timestamp']:.2f}s")
        else:
            st.info("Manual entry or crop unavailable")
    with right:
        proposed = refs.get(row["monster_id"] or row["proposed_id"])
        st.subheader(
            f"#{proposed['id']} {proposed['name']}" if proposed else "No proposed identity"
        )
        if proposed and proposed.get("icon") and Path(proposed["icon"]).exists():
            st.image(proposed["icon"], width=240)
        st.write(f"Similarity: {row['confidence']:.3f} · {row['status']}")
        if proposed:
            st.caption(f"Reference: {json.loads(proposed['provenance'])['source']}")
    alternatives = json.loads(row["candidates"] or "[]")
    st.subheader("Alternatives")
    for column, candidate in zip(st.columns(max(1, len(alternatives))), alternatives, strict=False):
        with column:
            st.write(f"#{candidate['monster_id']} {candidate['name']}")
            if Path(candidate["reference_icon"]).exists():
                st.image(candidate["reference_icon"], width=100)
            st.caption(f"Similarity {candidate['score']:.3f}")
    alternative_id = None
    if alternatives:
        alternatives_by_id = {candidate["monster_id"]: candidate for candidate in alternatives}
        alternative_id = st.selectbox(
            "Choose an alternative identity",
            list(alternatives_by_id),
            index=None,
            format_func=lambda mid: f"#{mid} {alternatives_by_id[mid]['name']}",
            key=f"alternative-{selected}",
        )
    distinct = False
    if row["tracking"] == "ambiguous":
        st.warning(
            f"Slot identity needs review: {row['tracking_reason'] or 'possible repeated entry'}"
        )
        distinct = st.checkbox(
            "I verified this is a separate owned copy", key=f"distinct-{selected}"
        )
        targets = [
            r for r in rows if r["id"] != selected and r["status"] not in {"merged", "rejected"}
        ]
        target_map = {r["id"]: r for r in targets}
        with st.expander("This is another view of an existing copy"):
            if target_map:
                target = st.selectbox(
                    "Existing inventory copy",
                    list(target_map),
                    format_func=lambda key: (
                        f"{key[:8]} · "
                        f"#{target_map[key]['monster_id'] or target_map[key]['proposed_id']}"
                    ),
                )
                target_crop = target_map[target].get("crop")
                if target_crop and Path(target_crop).exists():
                    st.image(target_crop, width=180, caption="Existing copy crop")
                if st.button("Merge into this copy"):
                    try:
                        merge(store, selected, target)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
    query = st.text_input("Search reference monsters by ID or name", key=f"search-{selected}")
    choices = store.references(query)[:100] if query else []
    manual_id = None
    if choices:
        choice_map = {r["id"]: r["name"] for r in choices}
        manual_id = st.selectbox(
            "Correct identity",
            list(choice_map),
            index=None,
            format_func=lambda mid: f"#{mid} {choice_map[mid]}",
        )
    chosen_id = manual_id or alternative_id or row["proposed_id"] or row["monster_id"]
    selected_reference = refs.get(
        chosen_id or (alternatives[0]["monster_id"] if alternatives else None)
    )
    if (
        selected_reference
        and json.loads(selected_reference.get("raw", "{}")).get("stackable")
        and (not row["quantity_verified"] or chosen_id != (row["monster_id"] or row["proposed_id"]))
    ):
        st.warning("This item can stack. Verify the displayed xN quantity below before applying.")
    if scan_id:
        quantity = st.number_input(
            "Copies in this slot",
            min_value=1,
            max_value=10000,
            value=row["quantity"],
            key=f"quantity-{selected}",
        )
        if st.button("Verify slot quantity"):
            set_entry_quantity(store, selected, quantity)
            st.rerun()
    first, second = st.columns(2)
    with first:
        if st.button("Accept selected identity", disabled=chosen_id is None):
            try:
                accept(store, selected, chosen_id, distinct)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with second:
        if st.button("Reject entry / false detection"):
            reject(store, selected)
            st.rerun()


def batch_controls(store: Store, scan_id: str) -> None:
    with st.expander("Batch accept identities", expanded=True):
        st.caption(
            "Similarity is not probability. Copy ambiguities stay in review; "
            "stack quantities still require verification. Saved ownership changes only on Apply."
        )
        score = st.number_input(
            "Minimum similarity",
            min_value=0.0,
            max_value=1.0,
            value=0.80,
            step=0.01,
            key=f"batch_score_{scan_id}",
        )
        margin = st.number_input(
            "Minimum lead over alternatives",
            min_value=0.0,
            max_value=1.0,
            value=0.04,
            step=0.01,
            key=f"batch_margin_{scan_id}",
        )
        policy = BatchPolicy(min_score=score, min_margin=margin)
        preview_key = f"batch_preview_{scan_id}"
        shown_preview = st.session_state.get(preview_key)
        try:
            preview = preview_batch(store, scan_id, policy)
        except ValueError as exc:
            st.info(str(exc))
            return
        st.write(
            f"{preview['eligible_count']} identities qualify · "
            f"{preview['remaining_identity_count']} remain after acceptance"
        )
        st.caption(
            "Excluded: "
            + ", ".join(
                f"{count} {reason.replace('_', ' ')}"
                for reason, count in preview["skipped"].items()
            )
        )
        st.dataframe(preview["score_bands"], width="stretch")
        st.caption("Weakest qualifying identities first; open an entry below to compare artwork.")
        st.dataframe(preview["entries"][:50], width="stretch")
        if st.button(
            f"Accept {preview['eligible_count']} qualifying identities",
            disabled=not preview["eligible_count"],
        ):
            try:
                if not shown_preview or shown_preview["policy"] != policy.model_dump():
                    raise ValueError("Thresholds changed; review the updated preview first")
                result = accept_batch(store, scan_id, policy, shown_preview["preview_token"])
                st.session_state[f"batch_result_{scan_id}"] = result["accepted_count"]
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        st.session_state[preview_key] = {
            "policy": preview["policy"],
            "preview_token": preview["preview_token"],
        }
        if f"batch_result_{scan_id}" in st.session_state:
            st.success(
                f"{st.session_state[f'batch_result_{scan_id}']} identities accepted in the draft."
            )


def manual_controls(store: Store, scan_id: str | None = None) -> None:
    with st.form("manual"):
        st.subheader("Add monsters or adjust confirmed quantity")
        monster_id = st.number_input("Monster ID", min_value=1, step=1)
        quantity = st.number_input("Quantity", min_value=0, max_value=10000, value=1, step=1)
        mode = st.radio(
            "Action", ["Add new copies"] if scan_id else ["Add new copies", "Set confirmed total"]
        )
        if st.form_submit_button("Save quantity"):
            try:
                if mode == "Add new copies":
                    add(store, int(monster_id), int(quantity), scan_id)
                else:
                    set_quantity(store, int(monster_id), int(quantity))
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def replacement_controls(store: Store, scan_id: str) -> None:
    st.subheader("Review changes and replace the box")
    key = f"diff-{scan_id}"
    if st.button("Preview changes"):
        st.session_state[key] = preview_scan(store, scan_id)
        st.session_state[f"full-{scan_id}"] = False
    preview = st.session_state.get(key)
    if not preview:
        st.caption("Finish the capture and review entries, then preview the replacement diff.")
        return
    st.write(
        f"{preview['before_total']} → {preview['after_total']} copies · "
        f"+{preview['added_copies']} added · {preview['removed_copies']} removed"
    )
    st.dataframe(preview["changes"], width="stretch")
    for blocker in preview["blockers"]:
        st.warning(blocker)
    full_box = st.checkbox(
        "I captured the complete unfiltered box and verified counts", key=f"full-{scan_id}"
    )
    if st.button(
        "Apply scan — replace entire box", disabled=not full_box or not preview["can_apply"]
    ):
        try:
            apply_scan(store, scan_id, preview["preview_token"], full_box)
            del st.session_state[key]
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def calibration() -> None:
    st.header("Grid calibration")
    st.write(
        "Preview a local image or the first frame of a recording. Coordinates are normalized "
        "to the screen. Start with an ROI around the box; use a forced grid for aligned stills."
    )
    path = st.text_input("Local image or recording path")
    columns = st.number_input("Expected columns", min_value=3, max_value=12, value=5)
    left = st.number_input("ROI left", min_value=0.0, max_value=1.0, value=0.0)
    top = st.number_input("ROI top", min_value=0.0, max_value=1.0, value=0.1)
    right = st.number_input("ROI right", min_value=0.0, max_value=1.0, value=1.0)
    bottom = st.number_input("ROI bottom", min_value=0.0, max_value=1.0, value=0.95)
    forced = st.checkbox("Use calibrated grid geometry")
    follow = st.checkbox(
        "Follow scrolling rows using border evidence", value=False, disabled=not forced
    )
    kwargs = {"roi": (left, top, right, bottom), "columns": int(columns)}
    if forced:
        kwargs.update(
            {
                "x": st.number_input("First icon x / screen width", value=0.04),
                "y": st.number_input("First icon y / screen height", value=0.2),
                "size": st.number_input("Icon size / screen width", value=0.16),
                "step_x": st.number_input("Column step / screen width", value=0.188),
                "step_y": st.number_input("Row step / screen height", value=0.22),
            }
        )
    try:
        kwargs["follow_scroll"] = follow and forced
        settings = Layout(**kwargs)
        st.download_button(
            "Save layout JSON",
            settings.model_dump_json(indent=2),
            "layout.json",
            "application/json",
        )
        if path:
            frame = next(frames(Path(path).expanduser()))
            cells = detect(frame.image, settings)
            st.image(cv2.cvtColor(annotate(frame.image, cells), cv2.COLOR_BGR2RGB))
            st.write(f"{len(cells)} complete cells detected")
    except (ValueError, OSError) as exc:
        st.error(str(exc))


if __name__ == "__main__":
    run()
