from pathlib import Path

from streamlit.testing.v1 import AppTest

from padbox.export.collection import collection
from padbox.inventory.pipeline import import_media
from padbox.inventory.service import entries, statistics

APP_PATH = Path(__file__).parents[1] / "src/padbox/review/app.py"


def test_review_ui_accept_persists(store, fixtures, monkeypatch):
    import_media(store, fixtures["image"])
    monkeypatch.setenv("PADBOX_HOME", str(store.home))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    assert not app.exception
    assert any("PADBox" in title.value for title in app.title)
    next(c for c in app.checkbox if c.label == "Include reviewed entries").check().run(timeout=20)
    original = entries(store)[0]["id"]
    next(
        t for t in app.text_input if t.label == "Search reference monsters by ID or name"
    ).set_value("900015").run(timeout=20)
    next(s for s in app.selectbox if s.label == "Correct identity").set_value(900015).run(
        timeout=20
    )
    accept_button = next(b for b in app.button if b.label == "Accept selected identity")
    accept_button.click().run(timeout=20)
    assert not app.exception
    assert statistics(store)["confirmed_entries"] == 0
    assert next(r for r in entries(store) if r["id"] == original)["monster_id"] == 900015
    app.sidebar.radio[0].set_value("Collection").run(timeout=20)
    assert not app.exception
    assert app.dataframe


def test_empty_review_and_calibration(tmp_path, monkeypatch):
    monkeypatch.setenv("PADBOX_HOME", str(tmp_path / "empty"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    assert not app.exception
    app.sidebar.radio[0].set_value("Calibration").run(timeout=20)
    assert not app.exception
    assert app.header[0].value == "Grid calibration"


def test_streamlit_assumes_pending_stack_quantities_without_approving_ids(
    store, reviewable_draft, monkeypatch
):
    import json
    from contextlib import closing

    with closing(store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=?", (json.dumps({"stackable": True}),))
    before = entries(store, reviewable_draft)
    monkeypatch.setenv("PADBOX_HOME", str(store.home))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    next(b for b in app.button if b.label == "Use quantity 1 for all pending stacks").click().run(
        timeout=20
    )
    assert not app.exception
    after = entries(store, reviewable_draft)
    assert all(r["quantity"] == 1 and r["quantity_verified"] for r in after)
    assert [r["status"] for r in after] == [r["status"] for r in before]
    assert next(
        b for b in app.button if b.label == "Use quantity 1 for all pending stacks"
    ).disabled
    assert collection(store)["monsters"] == []


def test_streamlit_batch_accepts_by_selected_thresholds_without_applying(
    store, reviewable_draft, monkeypatch
):
    from padbox.inventory.batch import BatchPolicy, preview_batch

    sid = reviewable_draft
    preview = preview_batch(store, sid, BatchPolicy(min_score=0, min_margin=0))
    assert preview["eligible_count"] > 0
    before = sum(r["status"] == "confirmed" for r in entries(store, sid))
    monkeypatch.setenv("PADBOX_HOME", str(store.home))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    next(n for n in app.number_input if n.label == "Minimum similarity").set_value(0.0).run()
    next(n for n in app.number_input if n.label == "Minimum lead over alternatives").set_value(
        0.0
    ).run()
    next(
        b
        for b in app.button
        if b.label == f"Accept {preview['eligible_count']} qualifying identities"
    ).click().run(timeout=20)
    assert not app.exception
    assert (
        sum(r["status"] == "confirmed" for r in entries(store, sid))
        == before + preview["eligible_count"]
    )
    assert collection(store)["monsters"] == []


def test_review_ui_snapshot_diff_and_apply(store, fixtures, monkeypatch):
    import_media(store, fixtures["image"])
    monkeypatch.setenv("PADBOX_HOME", str(store.home))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    next(b for b in app.button if b.label == "Preview changes").click().run(timeout=20)
    assert not app.exception
    next(
        c
        for c in app.checkbox
        if c.label == "I captured the complete unfiltered box and verified counts"
    ).check().run(timeout=20)
    next(b for b in app.button if b.label == "Apply scan — replace entire box").click().run(
        timeout=20
    )
    assert not app.exception
    assert collection(store)["revision"] == 1
    assert sum(m["quantity"] for m in collection(store)["monsters"]) == 15
    assert any("read-only" in i.value for i in app.info)


def test_streamlit_batch_rejects_evidence_changed_since_display(
    store, reviewable_draft, monkeypatch
):
    import json
    from contextlib import closing

    sid = reviewable_draft
    monkeypatch.setenv("PADBOX_HOME", str(store.home))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    button = next(b for b in app.button if b.label == "Accept 1 qualifying identities")
    references = {r["id"]: r for r in store.references()}
    with closing(store.connect()) as db, db:
        row = db.execute(
            "SELECT id,proposed_id FROM owned WHERE recording_id=? AND status='uncertain'", (sid,)
        ).fetchone()
        # Same count, same score and policy, but a different proposed exact form.
        candidates = [{"monster_id": 900015, "score": 0.99}, {"monster_id": 900014, "score": 0.70}]
        for candidate in candidates:
            reference = references[candidate["monster_id"]]
            candidate.update(name=reference["name"], reference_icon=reference["icon"])
        db.execute("UPDATE owned SET proposed_id=900015,confidence=0.99 WHERE id=?", (row["id"],))
        db.execute(
            "UPDATE observation SET candidates=? WHERE matched_inventory_id=?",
            (json.dumps(candidates), row["id"]),
        )
    button.click().run(timeout=20)
    assert not app.exception
    assert any("changed" in e.value for e in app.error)
    assert next(r for r in entries(store, sid) if r["id"] == row["id"])["status"] == "uncertain"
