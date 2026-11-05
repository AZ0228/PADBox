"""Browser-to-HTTP-to-SQLite tests using an original synthetic screen-share feed.

Install the optional test browser with `uv run playwright install chromium`.
No OS permission prompt or physical iPhone is simulated as having been verified.
"""

import base64
import json
import threading
from contextlib import closing

import pytest
from playwright.sync_api import Error, expect, sync_playwright

from padbox.live.server import LiveServer


@pytest.fixture
def browser_page(store, fixtures):
    server = LiveServer(store, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as exc:
            server.shutdown()
            server.server_close()
            pytest.skip(f"Test browser unavailable: uv run playwright install chromium. {exc}")
        page = browser.new_page(viewport={"width": 1360, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        first = base64.b64encode(fixtures["image"].read_bytes()).decode()
        shifted = base64.b64encode(
            fixtures["image"].with_name("box-shifted.png").read_bytes()
        ).decode()
        page.add_init_script(
            script="""
          window.fakeFrame=0;
          navigator.mediaDevices.getDisplayMedia=async()=>{
            const images=await Promise.all(SOURCES.map(src=>new Promise(resolve=>{
              const image=new Image();image.onload=()=>resolve(image);image.src=src;
            })));
            const canvas=document.createElement('canvas');canvas.width=500;canvas.height=430;
            window.fakeCanvas=canvas;
            const draw=()=>canvas.getContext('2d').drawImage(images[window.fakeFrame],0,0);
            window.fakeDraw=draw;draw();window.fakeTimer=setInterval(draw,50);
            window.fakeStream=canvas.captureStream(10);
            return window.fakeStream;
          };
        """.replace(
                "SOURCES",
                str([f"data:image/png;base64,{first}", f"data:image/png;base64,{shifted}"]),
            )
        )
        page.goto(server.origin)
        expect(page.get_by_text("Local server connected", exact=True)).to_be_visible()
        yield page, server, errors
        browser.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not errors, errors


def metric(page, label):
    return page.locator(".metric").filter(has=page.get_by_text(label, exact=True)).locator("b")


def test_live_capture_pause_correct_scroll_and_reload(browser_page):
    page, server, _ = browser_page
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    expect(page.get_by_role("button", name="Start new scan", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Start new scan", exact=True).click()
    expect(metric(page, "Detected slots")).to_have_text("15", timeout=15000)
    expect(page.locator(".box")).to_have_count(15)
    page.locator(".box").first.click()
    expect(page.locator("#scan-state")).to_have_text("Paused")
    page.get_by_label("Chosen monster ID").fill("900015")
    page.get_by_role("button", name="Accept identity", exact=True).click()
    expect(metric(page, "Confirmed")).to_have_text("8")
    page.get_by_role("button", name="Resume scanning", exact=True).click()
    page.evaluate("window.fakeFrame=1")
    expect(metric(page, "Detected slots")).to_have_text("20", timeout=15000)
    page.get_by_role("button", name="Finish capture", exact=True).click()
    expect(page.locator("#scan-state")).to_have_text("Draft ready")
    session = server.sessions.list()[0]
    assert session["active_entries"] == 20
    assert session["confirmed_entries"] == 9
    assert session["status"] == "complete"
    page.reload()
    page.get_by_label("Saved sessions").select_option(session["id"])
    page.get_by_role("button", name="Load session", exact=True).click()
    expect(metric(page, "Detected slots")).to_have_text("20")
    expect(metric(page, "Confirmed")).to_have_text("9")
    expect(page.get_by_role("button", name="Resume scanning", exact=True)).to_be_disabled()


def test_capture_permission_denial_and_track_end_are_clear(browser_page):
    page, server, _ = browser_page
    page.evaluate("""() => {
      window.originalCapture=navigator.mediaDevices.getDisplayMedia;
      navigator.mediaDevices.getDisplayMedia=async()=>{
        throw new Error('Permission denied by user');
      };
    }""")
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    expect(page.locator("#notice")).to_contain_text("Permission denied")
    assert server.sessions.list() == []
    page.evaluate("() => {navigator.mediaDevices.getDisplayMedia=window.originalCapture;}")
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    page.get_by_role("button", name="Start new scan", exact=True).click()
    expect(metric(page, "Detected slots")).to_have_text("15", timeout=15000)
    page.evaluate("window.fakeStream.getVideoTracks()[0].dispatchEvent(new Event('ended'))")
    expect(page.locator("#notice")).to_contain_text("disconnected", timeout=10000)
    expect(page.locator("#scan-state")).to_have_text("Paused")
    assert server.sessions.list()[0]["active_entries"] == 15


def test_live_crop_is_interactive_and_saved(browser_page):
    page, server, _ = browser_page
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    page.get_by_role("button", name="Crop to phone screen", exact=True).click()
    area = page.locator("#crop-overlay").bounding_box()
    page.mouse.move(area["x"] + 1, area["y"] + 1)
    page.mouse.down()
    page.mouse.move(area["x"] + area["width"] - 1, area["y"] + area["height"] - 1)
    page.mouse.up()
    page.get_by_role("button", name="Start new scan", exact=True).click()
    expect(metric(page, "Detected slots")).to_have_text("15", timeout=15000)
    session = server.sessions.list()[0]
    crop = session["config"]["capture_crop"]
    assert 0 < crop[0] < 0.01 and 0.99 < crop[2] < 1
    page.get_by_role("button", name="Pause to review", exact=True).click()
    expect(page.locator("#scan-state")).to_have_text("Paused")
    page.reload()
    page.get_by_label("Saved sessions").select_option(session["id"])
    page.get_by_role("button", name="Load session", exact=True).click()
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    expect(page.get_by_role("button", name="Resume scanning", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Resume scanning", exact=True).click()
    # Reselecting a feed breaks continuity, but the saved map uniquely locates these rows.
    expect(page.locator("#map-position")).to_contain_text("Known section found", timeout=15000)
    expect(metric(page, "Detected slots")).to_have_text("15")
    expect(metric(page, "Slot ambiguities")).to_have_text("0", timeout=15000)
    expect(page.locator("#scan-state")).to_have_text("Scanning", timeout=15000)


def test_live_automatically_pauses_after_grid_disappears(browser_page):
    page, server, _ = browser_page
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    page.locator("#auto-pause").check()
    page.get_by_role("button", name="Start new scan", exact=True).click()
    expect(metric(page, "Detected slots")).to_have_text("15", timeout=15000)
    show_transition(page)
    expect(page.locator("#notice")).to_contain_text("No box grid", timeout=15000)
    expect(page.locator("#scan-state")).to_have_text("Paused", timeout=15000)
    assert server.sessions.list()[0]["active_entries"] == 15


def test_offline_start_shows_restart_instructions_without_creating_session(browser_page):
    page, server, _ = browser_page
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    page.route("**/health", lambda route: route.abort("connectionrefused"))
    page.get_by_role("button", name="Start new scan", exact=True).click()
    expect(page.locator("#notice")).to_contain_text("Cannot reach the PADBox live server")
    expect(page.locator("#notice")).to_contain_text(
        "Run 'padbox live' in a terminal and keep it open"
    )
    expect(page.locator("#connection")).to_contain_text("unreachable")
    assert server.sessions.list() == []


def test_start_after_server_restart_explains_stale_page(browser_page):
    page, server, _ = browser_page
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    server.token = "replacement-token-after-restart"
    page.get_by_role("button", name="Start new scan", exact=True).click()
    expect(page.locator("#notice")).to_contain_text("Refresh the local PADBox page")
    assert server.sessions.list() == []


def test_snapshot_diff_apply_and_identical_rescan_in_browser(browser_page):
    from padbox.export.collection import collection

    page, server, _ = browser_page
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    for revision in [1, 2]:
        previous = page.locator("#scan-state").get_attribute("data-session-id") or ""
        page.get_by_role("button", name="Start new scan", exact=True).click()
        expect(page.locator("#scan-state")).not_to_have_attribute("data-session-id", previous)
        expect(metric(page, "Detected slots")).to_have_text("15", timeout=15000)
        page.get_by_role("button", name="Finish capture", exact=True).click()
        sid = server.sessions.list()[0]["id"]
        page.get_by_role("button", name="Preview changes", exact=True).click()
        expect(page.locator("#diff-summary")).to_contain_text("Resolve")
        apply_button = page.locator("#apply-scan")
        expect(apply_button).to_be_disabled()
        assert collection(server.sessions.store)["revision"] == revision - 1
        # The individual review UI is exercised in the test above. Resolve the
        # remaining synthetic labels here, then exercise actual diff/apply UI.
        for row in server.sessions.snapshot(sid)["entries"]:
            if row["status"] != "confirmed":
                server.sessions.review(sid, row["id"], "accept", row["proposed_id"])
        page.get_by_role("button", name="Preview changes", exact=True).click()
        expect(page.locator("#diff-summary")).to_contain_text("Ready for full-box")
        if revision == 2:
            expect(page.locator("#diff-table")).to_contain_text("No quantity changes")
        expect(apply_button).to_be_disabled()
        page.locator("#full-box").check()
        expect(apply_button).to_be_enabled()
        page.locator("#expected-slots").fill("15")
        apply_button.click()
        expect(page.locator("#applied-state")).to_contain_text(f"revision {revision}")
        expect(page.locator("#scan-state")).to_have_text("Applied")
        assert sum(r["quantity"] for r in collection(server.sessions.store)["monsters"]) == 15
        expect(apply_button).to_be_disabled()
    page.get_by_text("Applied scan history", exact=True).click()
    page.get_by_role("button", name="Refresh history", exact=True).click()
    expect(page.locator("#history-list > details")).to_have_count(2)


def start_capture(page):
    page.get_by_role("button", name="Choose preview window", exact=True).click()
    page.get_by_role("button", name="Start new scan", exact=True).click()
    expect(metric(page, "Detected slots")).to_have_text("15", timeout=15000)
    expect(page.locator(".reconstructed-card")).to_have_count(15)


def test_batch_preview_threshold_changes_acceptance_and_reload(browser_page):
    from padbox.export.collection import collection
    from padbox.inventory.batch import BatchPolicy, preview_batch

    page, server, _ = browser_page
    start_capture(page)
    expect(page.locator("#batch-preview")).to_be_disabled()
    page.get_by_role("button", name="Finish capture", exact=True).click()
    expect(page.locator("#scan-state")).to_have_text("Draft ready")
    sid = server.sessions.list()[0]["id"]
    policy = BatchPolicy(min_score=0, min_margin=0)
    preview = preview_batch(server.sessions.store, sid, policy)
    assert preview["eligible_count"] > 0
    count = server.sessions.snapshot(sid)["confirmed_entries"]
    page.get_by_label("Minimum similarity", exact=True).fill("0")
    page.get_by_label("Minimum lead over alternatives", exact=True).fill("0")
    page.get_by_role("button", name="Preview qualifying matches", exact=True).click()
    expect(page.locator("#batch-summary")).to_contain_text(
        f"{preview['eligible_count']} identities qualify"
    )
    expect(page.locator(".batch-sample")).to_have_count(preview["eligible_count"])
    expect(page.locator(".batch-sample").first.locator("img")).to_have_count(2)
    page.get_by_label("Minimum similarity", exact=True).fill("0.95")
    expect(page.locator("#batch-accept")).to_be_disabled()
    expect(page.locator(".batch-sample")).to_have_count(0)
    # A threshold-band button previews the new threshold automatically.
    page.get_by_role("button", name="Preview qualifying matches", exact=True).click()
    page.locator("#batch-bands button").last.click()
    expect(page.get_by_label("Minimum similarity", exact=True)).to_have_value("0.60")
    expect(page.locator("#batch-accept")).to_be_enabled()
    page.locator("#batch-accept").click()
    expect(page.locator("#batch-summary")).to_contain_text("identities accepted in this draft")
    expect(metric(page, "Confirmed")).to_have_text(str(count + preview["eligible_count"]))
    assert collection(server.sessions.store)["monsters"] == []
    page.reload()
    page.get_by_label("Saved sessions").select_option(sid)
    page.get_by_role("button", name="Load session", exact=True).click()
    expect(metric(page, "Confirmed")).to_have_text(str(count + preview["eligible_count"]))
    expect(page.locator("#batch-preview")).to_be_enabled()  # No source/re-scan needed.


def test_batch_stale_preview_requires_refresh_before_any_acceptance(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    page.get_by_role("button", name="Finish capture", exact=True).click()
    expect(page.locator("#scan-state")).to_have_text("Draft ready")
    sid = server.sessions.list()[0]["id"]
    page.get_by_label("Minimum similarity", exact=True).fill("0.60")
    page.get_by_label("Minimum lead over alternatives", exact=True).fill("0")
    page.locator("#batch-preview").click()
    expect(page.locator("#batch-accept")).to_be_enabled()
    row = next(r for r in server.sessions.snapshot(sid)["entries"] if r["status"] == "uncertain")
    server.sessions.review(sid, row["id"], "accept", 900015)
    count = server.sessions.snapshot(sid)["confirmed_entries"]
    page.locator("#batch-accept").click()
    expect(page.locator("#batch-summary")).to_contain_text("changed")
    expect(page.locator("#batch-accept")).to_be_disabled()
    assert server.sessions.snapshot(sid)["confirmed_entries"] == count


def test_reconstructed_box_is_default_aligned_view_with_separate_copies(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    expect(page.locator("#reconstruction")).to_be_visible()
    expect(page.locator("#review")).to_be_hidden()
    expect(page.locator("#review-panel-title")).to_have_text("Reconstructed monster box")
    left = page.locator(".scan-panel").bounding_box()
    right = page.locator(".review-panel").bounding_box()
    assert left["x"] + left["width"] <= right["x"]
    assert abs(left["y"] - right["y"]) < 2
    session = server.sessions.list()[0]
    rows = {r["id"]: r for r in server.sessions.snapshot(session["id"])["entries"]}
    for cell in session["result"]["cells"]:
        tile = page.locator(f'.reconstructed-card[data-inventory-id="{cell["inventory_id"]}"]')
        row = rows[cell["inventory_id"]]
        assert int(tile.get_attribute("data-monster-id")) == (
            row["monster_id"] or row["proposed_id"]
        )
        assert "Reference #" in tile.locator("img").get_attribute("alt")
        geometry = tile.evaluate(
            "n => ({left:parseFloat(n.style.left), top:parseFloat(n.style.top)})"
        )
        assert abs(geometry["left"] - 100 * cell["rect"][0] / 500) < 0.01
        assert abs(geometry["top"] - 100 * cell["rect"][1] / 430) < 0.01
    expect(page.locator('.reconstructed-card[data-monster-id="900001"]')).to_have_count(2)
    assert page.locator(".reconstructed-card.accepted").count() > 0
    assert page.locator(".reconstructed-card.proposed").count() > 0


def prepare_manual_queue(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    page.get_by_role("button", name="Finish capture", exact=True).click()
    expect(page.locator("#scan-state")).to_have_text("Draft ready")
    sid = server.sessions.list()[0]["id"]
    with closing(server.sessions.store.connect()) as db, db:
        db.execute(
            "UPDATE owned SET status='uncertain',monster_id=NULL,high_confidence=0 "
            "WHERE recording_id=?",
            (sid,),
        )
    page.get_by_label("Saved sessions").select_option(sid)
    page.get_by_role("button", name="Load session", exact=True).click()
    snapshot = server.sessions.snapshot(sid)
    ids = [
        s["inventory_id"]
        for s in sorted(
            snapshot["box_map"]["slots"], key=lambda s: (s["segment"], s["world_y"], s["column"])
        )
    ]
    expect(page.locator("#queue-summary")).to_contain_text("15 entries in this review queue")
    return page, server, sid, ids


def test_keyboard_review_accepts_alternative_and_advances_without_returning_to_box(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    page.locator("#start-review").click()
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[0])
    expect(page.locator("#review-progress")).to_have_text("1 of 15 in queue")
    first = next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[0])
    alternative = first["candidates"][1]["monster_id"]
    page.keyboard.press("2")
    expect(page.get_by_label("Chosen monster ID")).to_have_value(str(alternative))
    expect(page.locator("#alternatives button").nth(1)).to_have_attribute("aria-pressed", "true")
    assert server.sessions.snapshot(sid)["confirmed_entries"] == 0  # Choosing doesn't save.
    page.keyboard.press("a")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    expect(page.locator("#review")).to_be_visible()
    expect(page.locator("#review-progress")).to_have_text("1 of 14 in queue")
    assert (
        next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[0])["monster_id"]
        == alternative
    )
    # Next and previous only navigate. Arrow keys wrap within unresolved entries.
    page.keyboard.press("j")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[2])
    page.keyboard.press("ArrowLeft")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    page.keyboard.press("k")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[-1])
    assert server.sessions.snapshot(sid)["confirmed_entries"] == 1
    page.keyboard.press("Escape")
    expect(page.locator("#reconstruction")).to_be_visible()
    page.keyboard.press("r")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    page.reload()
    page.get_by_label("Saved sessions").select_option(sid)
    page.get_by_role("button", name="Load session", exact=True).click()
    expect(page.locator("#start-review")).to_be_enabled()
    page.keyboard.press("r")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    assert server.sessions.snapshot(sid)["confirmed_entries"] == 1


def test_keyboard_review_ignores_typing_modifiers_and_held_keys(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    page.locator("#start-review").click()
    page.keyboard.press("/")
    expect(page.locator("#search")).to_be_focused()
    page.keyboard.type("axjkd123")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[0])
    assert server.sessions.snapshot(sid)["confirmed_entries"] == 0
    page.keyboard.press("Escape")
    expect(page.locator("#review")).to_be_focused()
    page.keyboard.press("i")
    expect(page.locator("#monster-id")).to_be_focused()
    page.locator("#monster-id").fill("900015")
    page.keyboard.press("Escape")
    page.keyboard.press("Control+a")
    page.keyboard.press("Meta+x")
    page.evaluate("window.dispatchEvent(new KeyboardEvent('keydown',{key:'a',repeat:true}))")
    assert server.sessions.snapshot(sid)["confirmed_entries"] == 0
    page.keyboard.press("a")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    assert (
        next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[0])["monster_id"]
        == 900015
    )


def test_review_keeps_stack_open_until_identity_and_quantity_are_resolved(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    row = next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[0])
    mid = row["proposed_id"]
    with closing(server.sessions.store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=? WHERE id=?", (json.dumps({"stackable": True}), mid))
    server.sessions.stackable.add(mid)
    page.get_by_role("button", name="Load session", exact=True).click()
    page.locator("#start-review").click()
    page.keyboard.press("a")
    expect(page.locator("#review-remaining")).to_contain_text("still needs stack quantity")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[0])
    page.keyboard.press("q")
    expect(page.locator("#entry-quantity")).to_be_focused()
    page.locator("#entry-quantity").fill("12")
    page.keyboard.press("Escape")
    page.keyboard.press("v")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    saved = next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[0])
    assert saved["quantity"] == 12 and saved["quantity_verified"]
    assert saved["status"] == "confirmed"


def test_bulk_stack_quantity_one_clears_queue_preserves_ids_and_survives_reload(browser_page):
    from padbox.export.collection import collection
    from padbox.inventory.service import set_entry_quantity

    page, server, sid, ids = prepare_manual_queue(browser_page)
    store = server.sessions.store
    with closing(store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=?", (json.dumps({"stackable": True}),))
    server.sessions.stackable.update(server.sessions.reference_names)
    set_entry_quantity(store, ids[0], 17)
    before = server.sessions.snapshot(sid)["entries"]
    page.get_by_role("button", name="Load session", exact=True).click()
    page.get_by_label("Review queue", exact=True).select_option("quantity")
    expect(page.locator("#queue-summary")).to_contain_text("14 entries in this review queue")
    page.locator("#start-review").click()
    page.locator("#stack-quantity-one").click()
    expect(page.locator("#stack-quantity-summary")).to_have_text("0 pending stack quantities")
    expect(page.locator("#queue-summary")).to_contain_text("0 entries in this review queue")
    expect(page.locator("#queue-summary")).to_contain_text("15 entries need review overall")
    expect(page.locator("#review")).to_be_hidden()
    expect(page.locator("#stack-quantity-one")).to_be_disabled()
    after = server.sessions.snapshot(sid)["entries"]
    assert [r["status"] for r in after] == [r["status"] for r in before]
    assert all(r["quantity_verified"] for r in after)
    assert next(r for r in after if r["id"] == ids[0])["quantity"] == 17
    assert all(r["quantity"] == 1 for r in after if r["id"] != ids[0])
    assert collection(store)["monsters"] == []
    page.get_by_label("Review queue", exact=True).select_option("identity")
    page.locator("#start-review").click()
    expect(page.locator("#entry-warning")).not_to_contain_text("Stackable material")
    page.reload()
    page.get_by_label("Saved sessions").select_option(sid)
    page.get_by_role("button", name="Load session", exact=True).click()
    expect(page.locator("#stack-quantity-summary")).to_have_text("0 pending stack quantities")
    expect(page.locator("#stack-quantity-one")).to_be_disabled()


def test_bulk_stack_quantity_failed_request_keeps_pending_work(browser_page):
    page, server, _, _ = prepare_manual_queue(browser_page)
    with closing(server.sessions.store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=?", (json.dumps({"stackable": True}),))
    server.sessions.stackable.update(server.sessions.reference_names)
    page.get_by_role("button", name="Load session", exact=True).click()
    expect(page.locator("#stack-quantity-one")).to_be_enabled()
    page.route("**/stack-quantity-one", lambda route: route.abort())
    page.locator("#stack-quantity-one").click()
    expect(page.locator("#notice")).to_contain_text("server")
    expect(page.locator("#stack-quantity-one")).to_be_enabled()
    expect(page.locator("#stack-quantity-summary")).to_have_text("15 pending stack quantities")


def test_review_filter_sort_and_explicit_copy_confirmation(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    with closing(server.sessions.store.connect()) as db, db:
        db.execute("UPDATE owned SET confidence=0.99 WHERE id=?", (ids[2],))
        db.execute("UPDATE owned SET confidence=0.10 WHERE id=?", (ids[3],))
        db.execute("UPDATE owned SET tracking='ambiguous' WHERE id=?", (ids[4],))
        db.execute("UPDATE owned SET proposed_id=NULL,status='unmatched' WHERE id=?", (ids[5],))
    page.get_by_role("button", name="Load session", exact=True).click()
    page.get_by_label("Review order", exact=True).select_option("highest")
    page.locator("#start-review").click()
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[2])
    page.keyboard.press("Escape")
    page.get_by_label("Review order", exact=True).select_option("lowest")
    page.locator("#start-review").click()
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[3])
    page.get_by_label("Review queue", exact=True).select_option("unmatched")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[5])
    expect(page.locator("#queue-summary")).to_contain_text("1 entries in this review queue")
    page.get_by_label("Review queue", exact=True).select_option("tracking")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[4])
    page.locator("#review").focus()
    page.keyboard.press("a")
    expect(page.locator("#notice")).to_contain_text("Resolve slot ambiguity")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[4])
    page.keyboard.press("d")
    expect(page.locator("#distinct")).to_be_checked()
    page.keyboard.press("a")
    expect(page.locator("#review")).to_be_hidden()
    assert (
        next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[4])["tracking"]
        == "clear"
    )


def test_review_reject_advances_and_completion_needs_no_back_click(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    # Keep just two entries unresolved; all other identities are already reviewed.
    for row in server.sessions.snapshot(sid)["entries"]:
        if row["id"] not in ids[:2]:
            server.sessions.review(sid, row["id"], "accept", row["proposed_id"])
    page.get_by_role("button", name="Load session", exact=True).click()
    page.locator("#start-review").click()
    page.keyboard.press("x")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    page.keyboard.press("a")
    expect(page.locator("#review")).to_be_hidden()
    expect(page.locator("#start-review")).to_be_disabled()
    expect(page.locator("#notice")).to_contain_text("No more entries in this queue")
    assert (
        next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[0])["status"]
        == "rejected"
    )


def test_review_disabling_advance_keeps_the_corrected_entry_open(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    page.get_by_label("Advance after resolving an entry").uncheck()
    page.locator("#start-review").click()
    page.locator("#accept").click()
    expect(metric(page, "Confirmed")).to_have_text("1")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[0])
    page.locator("#review-next").click()
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])


def test_keyboard_burst_saves_only_one_entry_and_readonly_history_cannot_mutate(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    page.locator("#start-review").click()
    page.evaluate(
        "for(let i=0;i<8;i++) window.dispatchEvent(new KeyboardEvent('keydown',{key:'a'}))"
    )
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    assert server.sessions.snapshot(sid)["confirmed_entries"] == 1

    with closing(server.sessions.store.connect()) as db, db:
        count = db.execute("SELECT COUNT(*) FROM review_event WHERE action='accept'").fetchone()[0]
        assert count == 1
        db.execute("UPDATE box_scan SET status='applied' WHERE recording_id=?", (sid,))
    page.get_by_role("button", name="Load session", exact=True).click()
    page.locator("#start-review").click()
    page.keyboard.press("a")
    page.keyboard.press("x")
    expect(page.locator("#notice")).to_contain_text("read-only")
    assert server.sessions.snapshot(sid)["confirmed_entries"] == 1


def test_review_failed_request_keeps_choice_and_does_not_advance(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    page.locator("#start-review").click()
    page.keyboard.press("2")
    chosen = page.locator("#monster-id").input_value()
    endpoint = f"**/api/sessions/{sid}/review"
    page.route(
        endpoint,
        lambda route: route.fulfill(
            status=400, content_type="application/json", body=json.dumps({"error": "Save failed"})
        ),
    )
    page.keyboard.press("a")
    expect(page.locator("#notice")).to_have_text("Save failed")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[0])
    expect(page.locator("#monster-id")).to_have_value(chosen)
    expect(page.locator("#accept")).to_be_enabled()
    assert server.sessions.snapshot(sid)["confirmed_entries"] == 0
    page.unroute(endpoint)
    page.keyboard.press("a")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])


def test_identity_only_queue_advances_while_preserving_stack_quantity_work(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    mid = next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[0])[
        "proposed_id"
    ]
    with closing(server.sessions.store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=? WHERE id=?", (json.dumps({"stackable": True}), mid))
    server.sessions.stackable.add(mid)
    page.get_by_role("button", name="Load session", exact=True).click()
    page.get_by_label("Review queue", exact=True).select_option("identity")
    page.locator("#start-review").click()
    page.keyboard.press("a")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[1])
    saved = next(r for r in server.sessions.snapshot(sid)["entries"] if r["id"] == ids[0])
    assert saved["status"] == "confirmed" and not saved["quantity_verified"]
    page.get_by_label("Review queue", exact=True).select_option("quantity")
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[0])


def test_manual_merge_advances_to_next_queued_copy_without_changing_counts_by_id(browser_page):
    page, server, sid, ids = prepare_manual_queue(browser_page)
    with closing(server.sessions.store.connect()) as db, db:
        db.execute(
            "INSERT INTO owned(id,proposed_id,status,tracking,confidence,recording_id,created_at) "
            "SELECT 'extra-view',proposed_id,'uncertain','ambiguous',confidence,"
            "recording_id,created_at "
            "FROM owned WHERE id=?",
            (ids[0],),
        )
    page.get_by_role("button", name="Load session", exact=True).click()
    page.locator('#entry-list button[data-inventory-id="extra-view"]').click()
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", "extra-view")
    page.get_by_text("Repeated view of a copy in this scan?", exact=True).click()
    page.locator("#merge-search").fill(ids[0])
    expect(page.locator(f'#merge-target option[value="{ids[0]}"]')).to_have_count(1)
    page.locator("#merge-target").select_option(ids[0])
    page.locator("#merge").click()
    expect(page.locator("#review")).to_have_attribute("data-inventory-id", ids[0])
    snapshot = server.sessions.snapshot(sid)
    assert snapshot["active_entries"] == 15
    assert next(r for r in snapshot["entries"] if r["id"] == "extra-view")["status"] == "merged"


def test_small_reconstructed_tiles_keep_artwork_visible_and_reference_image_stable(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    page.get_by_role("button", name="Pause to review", exact=True).click()
    # Mimic the small phone pane in the reported portrait screenshot.
    page.locator("#reconstructed-wrap").evaluate("n => n.style.width='190px'")
    tile = page.locator(".reconstructed-card").first
    geometry = tile.evaluate("""n => {
      const card=n.getBoundingClientRect(), image=n.querySelector('img').getBoundingClientRect();
      const id=n.querySelector('.card-id').getBoundingClientRect();
      const badge=n.querySelector('.card-status').getBoundingClientRect();
      return {card:card.height, image:image.height, id:id.height, badge:badge.height,
              badgeWidth:badge.width, width:card.width};
    }""")
    assert geometry["image"] >= geometry["card"] - 4
    assert geometry["id"] + geometry["badge"] < geometry["card"] * 0.55
    assert geometry["badgeWidth"] < geometry["width"] * 0.45
    # A better score for the same reference must not clear/reload its image.
    image = tile.locator("img")
    source = image.get_attribute("src")
    local_id = tile.get_attribute("data-inventory-id")
    image.evaluate("n => n.stableArtwork=true")
    session = server.sessions.list()[0]
    from contextlib import closing

    with closing(server.sessions.store.connect()) as db, db:
        db.execute("UPDATE owned SET confidence=confidence+0.0001 WHERE id=?", (local_id,))
    page.get_by_label("Saved sessions").select_option(session["id"])
    page.get_by_role("button", name="Load session", exact=True).click()
    image = page.locator(f'.reconstructed-card[data-inventory-id="{local_id}"] img')
    assert image.get_attribute("src") == source
    assert image.evaluate("n => n.stableArtwork")


def test_reconstructed_rows_follow_scroll_and_keep_tracked_tile_identity(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    session = server.sessions.list()[0]
    tracked = session["result"]["cells"][5]
    tile = page.locator(f'.reconstructed-card[data-inventory-id="{tracked["inventory_id"]}"]')
    tile.evaluate("n => n.testTrackedNode=true")
    first_top = tile.evaluate("n => parseFloat(n.style.top)")
    page.evaluate("window.fakeFrame=1")
    expect(metric(page, "Detected slots")).to_have_text("20", timeout=15000)
    moved = next(
        c
        for c in server.sessions.snapshot(session["id"])["result"]["cells"]
        if c["inventory_id"] == tracked["inventory_id"]
    )
    assert tile.evaluate("n => n.testTrackedNode")
    assert tile.evaluate("n => parseFloat(n.style.top)") < first_top
    assert abs(tile.evaluate("n => parseFloat(n.style.top)") - 100 * moved["rect"][1] / 430) < 0.01
    expect(page.locator(".reconstructed-card")).to_have_count(15)


def test_reconstructed_review_correction_back_and_resume(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    tile = page.locator(".reconstructed-card").first
    local_id = tile.get_attribute("data-inventory-id")
    tile.click()
    expect(page.locator("#scan-state")).to_have_text("Paused")
    expect(page.locator("#reconstruction")).to_be_hidden()
    expect(page.locator("#review")).to_be_visible()
    page.get_by_label("Chosen monster ID").fill("900015")
    page.get_by_role("button", name="Accept identity", exact=True).click()
    page.get_by_role("button", name="Back to box", exact=True).click()
    expect(page.locator("#reconstruction")).to_be_visible()
    tile = page.locator(f'.reconstructed-card[data-inventory-id="{local_id}"]')
    expect(tile).to_have_attribute("data-monster-id", "900015")
    expect(tile.locator("img")).to_have_attribute("alt", "Reference #900015 Synthetic fixture 15")
    tile.click()
    page.get_by_role("button", name="Resume scanning", exact=True).click()
    expect(page.locator("#reconstruction")).to_be_visible()
    expect(page.locator("#review")).to_be_hidden()


def test_reconstructed_unknown_and_missing_reference_are_explicit(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    page.get_by_role("button", name="Pause to review", exact=True).click()
    sid = server.sessions.list()[0]["id"]
    row = server.sessions.snapshot(sid)["entries"][0]
    # This reference has no reason to have loaded in the initial viewport.
    page.route("**/api/icons/900015", lambda route: route.fulfill(status=404))
    server.sessions.review(sid, row["id"], "accept", 900015)
    # Reload the session response by making a UI review decision on another tile.
    page.locator(".reconstructed-card").nth(1).click()
    page.get_by_role("button", name="Accept identity", exact=True).click()
    page.get_by_role("button", name="Back to box", exact=True).click()
    tile = page.locator(f'.reconstructed-card[data-inventory-id="{row["id"]}"]')
    expect(tile).to_have_class("reconstructed-card accepted missing-reference")
    expect(tile.locator("img")).to_have_attribute(
        "alt", "Captured slot; reference #900015 unavailable"
    )
    # Model an unmatched observation to verify it displays capture evidence, never a guessed icon.
    with server.sessions.store.connect() as db:
        other = server.sessions.snapshot(sid)["entries"][2]["id"]
        db.execute(
            "UPDATE owned SET monster_id=NULL,proposed_id=NULL,status='unmatched' WHERE id=?",
            (other,),
        )
    page.locator(".reconstructed-card").nth(1).click()
    page.get_by_role("button", name="Accept identity", exact=True).click()
    page.get_by_role("button", name="Back to box", exact=True).click()
    unknown = page.locator(f'.reconstructed-card[data-inventory-id="{other}"]')
    expect(unknown).to_have_attribute("data-monster-id", "")
    expect(unknown.locator(".card-id")).to_have_text("Unmatched")
    expect(unknown.locator("img")).to_have_attribute("alt", "Unmatched capture")


def test_side_by_side_view_stacks_on_small_screens(browser_page):
    page, server, _ = browser_page
    page.set_viewport_size({"width": 430, "height": 900})
    start_capture(page)
    left = page.locator(".scan-panel").bounding_box()
    right = page.locator(".review-panel").bounding_box()
    assert right["y"] >= left["y"] + left["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def show_transition(page):
    page.evaluate("""() => {
      clearInterval(window.fakeTimer);
      window.fakeTimer=setInterval(()=>{
        window.fakeCanvas.getContext('2d').fillStyle='black';
        window.fakeCanvas.getContext('2d').fillRect(0,0,500,430);
        window.fakeStream.getVideoTracks()[0].requestFrame?.();
      },50);
    }""")


def test_continuous_default_recovers_known_rows_through_the_map(browser_page):
    from padbox.inventory.snapshots import preview_scan

    page, server, _ = browser_page
    expect(page.locator("#auto-pause")).not_to_be_checked()
    start_capture(page)
    sid = server.sessions.list()[0]["id"]
    show_transition(page)
    expect(page.locator("#notice")).to_contain_text("No box grid", timeout=15000)
    expect(page.locator("#notice")).to_contain_text("Continuing capture")
    expect(page.locator("#scan-state")).to_have_text("Scanning")
    expect(page.locator(".reconstructed-card")).to_have_count(0)
    assert server.sessions.snapshot(sid)["status"] == "running"
    # Restore the feed without a single click of Resume.
    page.evaluate("""() => {
      clearInterval(window.fakeTimer);
      window.fakeDraw();window.fakeTimer=setInterval(window.fakeDraw,50);
    }""")
    expect(page.locator("#map-position")).to_contain_text("Known section found", timeout=15000)
    expect(metric(page, "Slot ambiguities")).to_have_text("0")
    expect(page.locator("#scan-state")).to_have_text("Scanning")
    expect(page.locator(".reconstructed-card.ambiguous")).to_have_count(0)
    # Further frames still arrive automatically after the tracking warning.
    last_sequence = server.sessions.snapshot(sid)["last_sequence"]
    page.evaluate("window.fakeFrame=1")
    expect(metric(page, "Detected slots")).to_have_text("20", timeout=15000)
    assert server.sessions.snapshot(sid)["last_sequence"] > last_sequence
    page.get_by_role("button", name="Finish capture", exact=True).click()
    expect(page.locator("#scan-state")).to_have_text("Draft ready")
    preview = preview_scan(server.sessions.store, sid)
    assert not preview["can_apply"]
    assert not any("copy tracking" in item["reasons"] for item in preview["unresolved"])


def test_continuous_scroll_forward_and_back_does_not_require_resume(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    sid = server.sessions.list()[0]["id"]
    first_id = server.sessions.snapshot(sid)["result"]["cells"][0]["inventory_id"]
    tile = page.locator(f'.reconstructed-card[data-inventory-id="{first_id}"]')
    page.evaluate("window.fakeFrame=1")
    expect(metric(page, "Detected slots")).to_have_text("20", timeout=15000)
    for target in [0, 1]:
        before = server.sessions.snapshot(sid)["last_sequence"]
        page.evaluate(f"window.fakeFrame={target}")
        expect(tile).to_have_count(1 if target == 0 else 0, timeout=15000)
        assert server.sessions.snapshot(sid)["last_sequence"] > before
        expect(page.locator("#scan-state")).to_have_text("Scanning")
        expect(metric(page, "Detected slots")).to_have_text("20")
        expect(metric(page, "Slot ambiguities")).to_have_text("0")


def test_minimap_sliding_window_tracks_direction_and_can_browse_while_scanning(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    sid = server.sessions.list()[0]["id"]
    minimap = page.get_by_role("slider", name="Browse mapped monster box rows")
    expect(minimap).to_be_visible()
    first_top = float(minimap.get_attribute("data-viewport-top"))
    page.evaluate("window.fakeFrame=1")
    expect(metric(page, "Detected slots")).to_have_text("20", timeout=15000)
    expect(page.locator("#map-position")).to_have_attribute("data-direction", "down")
    later_top = float(minimap.get_attribute("data-viewport-top"))
    assert later_top > first_top
    minimap.focus()
    minimap.press("ArrowUp")
    expect(page.locator("#follow-live")).to_be_visible()
    expect(page.locator("#map-position")).to_contain_text("Browsing saved rows")
    assert float(minimap.get_attribute("data-browse-top")) < later_top
    expect(page.locator("#scan-state")).to_have_text("Scanning")
    # Dragging is local map navigation; it never controls the phone or changes counts.
    area = minimap.bounding_box()
    page.mouse.move(area["x"] + area["width"] / 2, area["y"] + area["height"] * 0.8)
    page.mouse.down()
    page.mouse.move(area["x"] + area["width"] / 2, area["y"] + 2, steps=5)
    page.mouse.up()
    expect(page.locator("#reconstructed-timing")).to_contain_text("does not move the iPhone")
    assert server.sessions.snapshot(sid)["active_entries"] == 20
    page.get_by_role("button", name="Follow live", exact=True).click()
    expect(page.locator("#follow-live")).to_be_hidden()
    page.evaluate("window.fakeFrame=0")
    expect(page.locator("#map-position")).to_have_attribute("data-direction", "up", timeout=15000)
    expect(minimap).to_have_attribute("data-viewport-top", str(int(first_top)))
    assert server.sessions.snapshot(sid)["active_entries"] == 20


def test_saved_minimap_browsing_works_without_capture_and_preserves_copy_ids(browser_page):
    page, server, _ = browser_page
    start_capture(page)
    page.evaluate("window.fakeFrame=1")
    expect(metric(page, "Detected slots")).to_have_text("20", timeout=15000)
    page.get_by_role("button", name="Finish capture", exact=True).click()
    sid = server.sessions.list()[0]["id"]
    original_ids = {r["id"] for r in server.sessions.snapshot(sid)["entries"]}
    page.reload()
    page.get_by_label("Saved sessions").select_option(sid)
    page.get_by_role("button", name="Load session", exact=True).click()
    minimap = page.get_by_role("slider", name="Browse mapped monster box rows")
    expect(minimap).to_be_visible()
    expect(page.locator("#map-position")).to_contain_text("Browsing saved rows")
    expect(page.get_by_role("button", name="Follow live", exact=True)).to_be_disabled()
    minimap.focus()
    minimap.press("PageUp")
    tile = page.locator(".reconstructed-card").first
    item = tile.get_attribute("data-inventory-id")
    assert item in original_ids
    tile.click()
    expect(page.locator("#review")).to_be_visible()
    page.get_by_label("Chosen monster ID").fill("900015")
    page.get_by_role("button", name="Accept identity", exact=True).click()
    page.get_by_role("button", name="Back to box", exact=True).click()
    expect(page.locator(f'.reconstructed-card[data-inventory-id="{item}"]')).to_have_attribute(
        "data-monster-id", "900015"
    )
    assert {r["id"] for r in server.sessions.snapshot(sid)["entries"]} == original_ids


def test_minimap_unjoined_sections_are_visible_and_do_not_claim_a_single_position(browser_page):
    import cv2

    from padbox.demo import render_grid

    page, server, _ = browser_page
    start_capture(page)
    sid = server.sessions.list()[0]["id"]
    # Keep source size unchanged but replace its contents with a new arrangement.
    directory = server.sessions.store.home.parent / "fixtures" / "icons"
    art = [cv2.imread(str(directory / f"{i}.png")) for i in range(15, 0, -1)]
    ok, encoded = cv2.imencode(".png", render_grid(art))
    assert ok
    source = "data:image/png;base64," + base64.b64encode(encoded).decode()
    page.evaluate(
        """async source => {
      clearInterval(window.fakeTimer);
      const image=new Image();image.src=source;await image.decode();
      window.fakeTimer=setInterval(()=>{
        window.fakeCanvas.getContext('2d').drawImage(image,0,0);
        window.fakeStream.getVideoTracks()[0].requestFrame?.();
      },50);
    }""",
        source,
    )
    expect(metric(page, "Slot ambiguities")).to_have_text("15", timeout=15000)
    expect(page.locator("#map-position")).to_contain_text("Unjoined section")
    minimap = page.get_by_role("slider", name="Browse mapped monster box rows")
    expect(minimap).to_have_attribute("data-active-segment", "1")
    assert len(server.sessions.snapshot(sid)["box_map"]["segments"]) == 2
    expect(page.locator("#scan-state")).to_have_text("Scanning")
