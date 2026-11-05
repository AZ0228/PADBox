import json
import threading
import urllib.error
import urllib.request
from contextlib import closing

import cv2
import numpy as np
import pytest

from padbox.export.collection import collection
from padbox.inventory.service import entries
from padbox.live.server import LiveServer
from padbox.live.session import LiveSessions, decode_frame
from padbox.models import Layout


def jpeg(path) -> bytes:
    image = cv2.imread(str(path))
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return encoded.tobytes()


@pytest.fixture
def live(store):
    return LiveSessions(store)


def running(live, additions=False):
    session = live.create(Layout(), additions)
    live.control(session["id"], "resume")
    return session["id"]


def test_live_overlap_review_and_exports(live, store, fixtures):
    session_id = running(live)
    first = live.submit(session_id, 0, 0, jpeg(fixtures["image"]))
    assert first["active_entries"] == 15
    assert first["confirmed_entries"] == 10
    assert len(first["result"]["cells"]) == 15
    first_id = first["entries"][0]["id"]
    corrected = live.review(session_id, first_id, "accept", 900015)
    assert corrected["confirmed_entries"] == 10
    again = live.submit(session_id, 1, 0.5, jpeg(fixtures["image"]))
    assert again["active_entries"] == 15
    # JPEG retry is idempotent, even with a malformed payload after commit.
    assert live.submit(session_id, 1, 0.5, b"not a jpeg")["active_entries"] == 15
    moved = live.submit(session_id, 2, 1, jpeg(fixtures["image"].with_name("box-shifted.png")))
    assert moved["active_entries"] == 20
    row = next(r for r in moved["entries"] if r["id"] == first_id)
    assert row["monster_id"] == 900015
    assert row["status"] == "confirmed"
    assert collection(store)["monsters"] == []
    assert collection(store)["unresolved"] == []
    assert len({r["id"] for r in moved["entries"] if r["proposed_id"] == 900001}) == 2
    live.control(session_id, "finish")
    assert live.snapshot(session_id)["status"] == "complete"
    with pytest.raises(ValueError, match="read-only"):
        live.control(session_id, "resume")


def test_pause_resume_and_restart_preserve_reviews(live, store, fixtures):
    session_id = running(live)
    result = live.submit(session_id, 0, 0, jpeg(fixtures["image"]))
    owned_id = result["entries"][0]["id"]
    live.review(session_id, owned_id, "accept", 900001)
    live.control(session_id, "pause")
    with pytest.raises(ValueError, match="paused"):
        live.submit(session_id, 1, 0.2, jpeg(fixtures["image"]))
    live.control(session_id, "resume")
    live.submit(session_id, 1, 0.2, jpeg(fixtures["image"]))
    restarted = LiveSessions(store)
    assert restarted.list()[0]["status"] == "paused"
    assert restarted.snapshot(session_id)["confirmed_entries"] == 10
    restarted.control(session_id, "resume")
    result = restarted.submit(session_id, 2, 0.4, jpeg(fixtures["image"]))
    # Persisted multi-row artwork uniquely relocalizes the viewport after restart.
    assert result["ambiguous_entries"] == 0
    assert result["active_entries"] == 15
    assert result["box_map"]["position"] == "relocalized"
    assert next(r for r in result["entries"] if r["id"] == owned_id)["status"] == "confirmed"
    assert result["result"]["status"] == "ok"


def test_rescan_does_not_create_cross_scan_copy_ambiguity(live, store, fixtures):
    first = running(live)
    live.submit(first, 0, 0, jpeg(fixtures["image"]))
    second = running(live)
    result = live.submit(second, 0, 0, jpeg(fixtures["image"]))
    assert result["ambiguous_entries"] == 0
    assert result["active_entries"] == 15
    assert collection(store)["monsters"] == []


def test_no_grid_resize_and_sequence_gap(live, store, fixtures):
    session_id = running(live)
    image = jpeg(fixtures["image"])
    first = live.submit(session_id, 0, 0, image)
    # Sequence gaps invalidate continuity, then the map can prove a known location.
    with pytest.raises(ValueError, match="timestamps"):
        live.submit(session_id, 1, 0, image)
    result = live.submit(session_id, 2, 1, image)
    assert result["ambiguous_entries"] == 0
    assert result["box_map"]["position"] == "relocalized"
    assert "Dropped frame" in result["result"]["warnings"][0]
    _, blank = cv2.imencode(".jpg", np.zeros((430, 500, 3), dtype=np.uint8))
    result = live.submit(session_id, 3, 2, blank.tobytes())
    assert result["result"]["status"] == "no_grid"
    assert result["active_entries"] == 15
    result = live.submit(session_id, 4, 2.5, blank.tobytes())
    assert result["result"]["status"] == "no_grid"
    assert "No box grid" in result["result"]["message"]
    assert result["active_entries"] == 15
    _, resized = cv2.imencode(".jpg", np.zeros((860, 1000, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="size changed"):
        live.submit(session_id, 5, 3, resized.tobytes())
    assert live.snapshot(session_id)["status"] == "paused"
    assert first["active_entries"] == 15


def test_slow_capture_keeps_visual_overlap_instead_of_resetting_tracking(
    live, fixtures, monkeypatch
):
    sid = running(live)
    live.submit(sid, 0, 0, jpeg(fixtures["image"]))
    processor = live.processors[sid]

    def unexpected_reset():
        pytest.fail("Wall-clock latency must not discard visual continuity")

    monkeypatch.setattr(processor, "break_continuity", unexpected_reset)
    live.received_at[sid] -= 10
    moved = live.submit(sid, 1, 12, jpeg(fixtures["image"].with_name("box-shifted.png")))
    assert moved["active_entries"] == 20
    assert moved["ambiguous_entries"] == 0
    assert len(moved["box_map"]["segments"]) == 1
    assert "unique vertical offset" in moved["result"]["message"]
    assert "Capture delayed" in moved["result"]["warnings"][0]


def test_crop_lookup_returns_only_the_requested_copies_evidence(live, store, fixtures):
    sid = running(live)
    live.submit(sid, 0, 0, jpeg(fixtures["image"]))
    row = entries(store, sid)[0]
    another = running(live)
    live.submit(another, 0, 0, jpeg(fixtures["image"]))
    assert entries(store, inventory_id=row["id"]) == [row]
    assert entries(store, another, inventory_id=row["id"]) == []
    assert entries(store, inventory_id="not-a-copy") == []
    assert str(live.image_path("crops", row["id"])) == row["crop"]
    with pytest.raises(ValueError, match="not available"):
        live.image_path("crops", "not-a-copy")


def test_input_validation_and_saved_capture_crop(live, fixtures):
    for bad in [b"", b"bad", b"\xff\xd8\x00\x00"]:
        with pytest.raises(ValueError):
            decode_frame(bad)
    image = jpeg(fixtures["image"])
    assert decode_frame(image).shape == (430, 500, 3)
    # Fabricated JPEG header with a huge bitmap is rejected before decoder allocation.
    oversized = b"\xff\xd8\xff\xc0\x00\x08\x08\xff\xff\xff\xff\x01"
    with pytest.raises(ValueError, match="megapixels"):
        decode_frame(oversized)
    with pytest.raises(ValueError, match="crop"):
        live.create(Layout(), capture_crop=(0, 0, 0, 0))
    session = live.create(Layout(), capture_crop=(0.1, 0.05, 0.9, 0.95))
    assert session["config"]["capture_crop"] == [0.1, 0.05, 0.9, 0.95]


@pytest.fixture
def live_server(store):
    server = LiveServer(store, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def request(server, path, body=None, token=True, origin=None, host=None):
    headers = {"X-PADBox-Token": server.token} if token else {}
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(server.origin + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def test_http_batch_preview_acceptance_validation_and_auth(live_server, reviewable_draft):
    server = live_server
    sid = reviewable_draft
    policy = {"min_score": 0.50, "min_margin": 0}
    endpoint = f"/api/scans/{sid}/batch-preview"
    assert request(server, endpoint, policy, token=False)[0] == 403
    assert request(server, endpoint, policy, origin="https://example.com")[0] == 403
    assert request(server, endpoint, {"min_score": 1.1})[0] == 400
    assert request(server, endpoint, {**policy, "include_ambiguous": True})[0] == 400
    status, body, _ = request(server, endpoint, policy)
    assert status == 200, body
    preview = json.loads(body)
    assert preview["eligible_count"] > 0
    assert (
        request(server, f"/api/scans/{sid}/batch-accept", {**policy, "preview_token": "0" * 64})[0]
        == 400
    )
    status, body, _ = request(
        server,
        f"/api/scans/{sid}/batch-accept",
        {**policy, "preview_token": preview["preview_token"]},
    )
    assert status == 200, body
    assert json.loads(body)["accepted_count"] == preview["eligible_count"]
    assert collection(server.sessions.store)["monsters"] == []
    assert (
        request(
            server,
            f"/api/scans/{sid}/batch-accept",
            {**policy, "preview_token": preview["preview_token"]},
        )[0]
        == 400
    )


def test_http_assume_stack_quantities_auth_and_draft_scope(live_server):
    from padbox.inventory.service import add, entries

    server = live_server
    store = server.sessions.store
    with closing(store.connect("reference")) as db, db:
        db.execute("UPDATE monster SET raw=? WHERE id=900001", (json.dumps({"stackable": True}),))
    sid = server.sessions.create(Layout())["id"]
    item = add(store, 900001, 1, sid)[0]
    endpoint = f"/api/scans/{sid}/stack-quantity-one"
    assert request(server, endpoint, {}, token=False)[0] == 403
    assert request(server, endpoint, {}, origin="https://example.com")[0] == 403
    assert request(server, endpoint, {"quantity": 10})[0] == 400
    assert not entries(store, sid)[0]["quantity_verified"]
    status, body, _ = request(server, endpoint, {})
    assert status == 200 and json.loads(body)["updated_count"] == 1
    row = next(r for r in entries(store, sid) if r["id"] == item)
    assert row["quantity"] == 1 and row["quantity_verified"]
    assert collection(store)["monsters"] == []
    assert json.loads(request(server, endpoint, {})[1])["updated_count"] == 0
    with closing(store.connect()) as db, db:
        db.execute("UPDATE box_scan SET status='applied' WHERE recording_id=?", (sid,))
    assert request(server, endpoint, {})[0] == 400


def test_http_rejects_untrusted_requests_and_malformed_frames(live_server):
    server = live_server
    assert request(server, "/api/bootstrap", token=False)[0] == 403
    assert request(server, "/api/sessions", {"layout": {}}, origin="https://example.com")[0] == 403
    assert request(server, "/", host="evil.example")[0] == 403
    status, body, _ = request(server, "/api/sessions", {"layout": {}})
    sid = json.loads(body)["id"]
    assert (
        request(
            server, f"/api/sessions/{sid}/frame", {"sequence": 0, "timestamp": 0, "jpeg": "%%%bad"}
        )[0]
        == 400
    )
    assert request(server, "/api/icons/../../etc/passwd")[0] == 400
    assert request(server, "/api/sessions", {"layout": {"roi": [0, 0, 0, 1]}})[0] == 400
    assert request(server, "/api/sessions", {"layout": {}, "capture_crop": [0, 0, 0, 0]})[0] == 400
    # Private file paths are never a supported HTTP resource.
    assert request(server, "/.padbox/inventory.sqlite")[0] == 404


def test_live_review_entry_scope_and_indirect_copy_safeguards(live, fixtures, store):
    first = running(live)
    result = live.submit(first, 0, 0, jpeg(fixtures["image"]))
    ids = [r["id"] for r in result["entries"]]
    second = live.create(Layout())["id"]
    with pytest.raises(ValueError, match="not in this session"):
        live.review(second, ids[0], "reject")
    with pytest.raises(ValueError, match="same frame"):
        live.review(first, ids[0], "merge", target_id=ids[2])
    with closing(store.connect()) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM review_event WHERE action != 'auto_accept'"
            ).fetchone()[0]
            == 0
        )
    assert len(entries(store)) == 15


def test_processing_failure_pauses_and_retry_keeps_existing_decisions(live, fixtures, monkeypatch):
    session_id = running(live)
    first = live.submit(session_id, 0, 0, jpeg(fixtures["image"]))
    owned_id = first["entries"][0]["id"]
    live.review(session_id, owned_id, "accept", 900001)
    processor = live.processors[session_id]
    original = processor.process

    def fail(frame):
        raise OSError("Simulated disk failure")

    monkeypatch.setattr(processor, "process", fail)
    with pytest.raises(OSError, match="disk failure"):
        live.submit(session_id, 1, 0.5, jpeg(fixtures["image"]))
    assert live.snapshot(session_id)["status"] == "paused"
    assert live.snapshot(session_id)["last_sequence"] == 0
    monkeypatch.setattr(processor, "process", original)
    live.control(session_id, "resume")
    retried = live.submit(session_id, 1, 0.5, jpeg(fixtures["image"]))
    assert retried["confirmed_entries"] == 10
    assert retried["ambiguous_entries"] == 0
    assert retried["box_map"]["position"] == "relocalized"
