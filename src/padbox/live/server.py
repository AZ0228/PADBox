"""Small same-origin, loopback-only HTTP service for browser window capture."""

import base64
import binascii
import json
import logging
import os
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from padbox.database.store import Store
from padbox.export.collection import collection, csv_export
from padbox.inventory.batch import BatchPolicy, accept_batch, preview_batch
from padbox.inventory.service import add, assume_pending_stack_quantities, entries
from padbox.inventory.snapshots import apply_scan, history, preview_scan, scan_list
from padbox.live.lifecycle import home_id
from padbox.live.session import MAX_JPEG_BYTES, LiveSessions
from padbox.models import Layout

LOG = logging.getLogger(__name__)


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layout: Layout
    capture_crop: tuple[float, float, float, float] = (0, 0, 1, 1)
    name: str = Field(default="Live box scan", max_length=100)


class CapturedFrame(BaseModel):
    sequence: int = Field(ge=0, strict=True)
    timestamp: float = Field(ge=0, allow_inf_nan=False)
    jpeg: str = Field(max_length=MAX_JPEG_BYTES * 4 // 3 + 8)


class Control(BaseModel):
    action: str
    discontinuity: bool = False


class Decision(BaseModel):
    inventory_id: str
    action: str
    monster_id: int | None = None
    distinct: bool = False
    target_id: str = ""
    quantity: int | None = Field(default=None, ge=1, le=10000, strict=True)


class ApplyScan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_token: str = Field(min_length=64, max_length=64)
    full_box_verified: bool = Field(strict=True)
    expected_slots: int | None = Field(default=None, ge=1, strict=True)


class AddEntry(BaseModel):
    monster_id: int = Field(ge=1, strict=True)
    quantity: int = Field(default=1, ge=1, le=10000, strict=True)


class AcceptBatchRequest(BatchPolicy):
    preview_token: str = Field(min_length=64, max_length=64)


class LiveServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, store: Store, port: int = 8502, layout: Layout | None = None):
        self.sessions = LiveSessions(store, layout)
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), LiveHandler)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def server_close(self) -> None:
        self.sessions.shutdown()
        super().server_close()


class LiveHandler(BaseHTTPRequestHandler):
    server: LiveServer

    def log_message(self, format: str, *args: object) -> None:
        LOG.info("Live HTTP: " + format, *args)

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: object, status: int = 200) -> None:
        self._send(json.dumps(data).encode(), "application/json", status)

    def _allowed_host(self) -> bool:
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self._json({"error": "Use the displayed 127.0.0.1 URL"}, 403)
            return False
        return True

    def _authorized(self) -> bool:
        token = self.headers.get("X-PADBox-Token", "")
        origin = self.headers.get("Origin")
        if not secrets.compare_digest(token, self.server.token) or (
            origin is not None and origin != self.server.origin
        ):
            self._json({"error": "Refresh the local PADBox page before continuing"}, 403)
            return False
        return True

    def do_GET(self) -> None:
        if not self._allowed_host():
            return
        url = urlsplit(self.path)
        path = url.path
        try:
            if path in {"/", "/app.js", "/style.css", "/minimap.js", "/review-queue.js"}:
                name = "index.html" if path == "/" else path[1:]
                body = files("padbox.live").joinpath("web", name).read_bytes()
                if path == "/":
                    body = body.replace(b"__PADBOX_TOKEN__", self.server.token.encode())
                content_type = {
                    "/": "text/html; charset=utf-8",
                    "/app.js": "text/javascript",
                    "/minimap.js": "text/javascript",
                    "/review-queue.js": "text/javascript",
                    "/style.css": "text/css",
                }[path]
                self._send(body, content_type)
                return
            if path == "/health":
                self._json(
                    {
                        "status": "ok",
                        "service": "padbox-live",
                        "pid": os.getpid(),
                        "home_id": home_id(self.server.sessions.store.home),
                    }
                )
                return
            if not self._authorized():
                return
            sessions = self.server.sessions
            if path == "/api/bootstrap":
                self._json(
                    {
                        "layout": sessions.default_layout.model_dump(mode="json"),
                        "sessions": sessions.list(),
                        "reference_count": len(sessions.reference_names),
                    }
                )
            elif path == "/api/sessions":
                self._json(sessions.list())
            elif path == "/api/scans":
                self._json(scan_list(sessions.store))
            elif path == "/api/history":
                self._json(history(sessions.store))
            elif path.startswith("/api/scans/") and path.endswith("/diff"):
                self._json(preview_scan(sessions.store, path.split("/")[3]))
            elif path.startswith("/api/sessions/"):
                self._json(sessions.snapshot(path.split("/")[3]))
            elif path == "/api/search":
                query = parse_qs(url.query).get("q", [""])[0][:100]
                self._json(
                    [
                        {"id": r["id"], "name": r["name"], "icon_url": f"/api/icons/{r['id']}"}
                        for r in sessions.store.references(query)[:50]
                    ]
                )
            elif path == "/api/inventory":
                query = parse_qs(url.query).get("q", [""])[0].lower()[:100]
                scan_id = parse_qs(url.query).get("scan", [""])[0]
                matches = [
                    sessions._entry(r)
                    for r in entries(sessions.store, scan_id)
                    if r["status"] not in {"merged", "rejected"} and r["recording_id"] == scan_id
                ]
                self._json(
                    [
                        r
                        for r in matches
                        if query
                        in f"{r['id']} {r['name']} {r['monster_id']} {r['proposed_id']}".lower()
                    ][:100]
                )
            elif path in {"/api/export/json", "/api/export/csv"}:
                if path.endswith("json"):
                    self._json(collection(sessions.store))
                else:
                    self._send(csv_export(sessions.store).encode(), "text/csv; charset=utf-8")
            elif path.startswith(("/api/icons/", "/api/crops/")):
                _, _, kind, value = path.split("/")
                local = sessions.image_path(kind, value)
                content_type = (
                    "image/jpeg" if local.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
                )
                self._send(local.read_bytes(), content_type)
            else:
                self._json({"error": "Not found"}, 404)
        except (ValueError, OSError, KeyError, IndexError) as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self) -> None:
        if not self._allowed_host() or not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 5_500_000:
                self.close_connection = True
                self._json({"error": "Request body must be 1 byte to 5.5 MB"}, 413)
                return
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Expected application/json")
            data = json.loads(self.rfile.read(length))
            path = urlsplit(self.path).path
            sessions = self.server.sessions
            if path == "/api/sessions":
                request = CreateSession.model_validate(data)
                self._json(
                    sessions.create(
                        request.layout, name=request.name, capture_crop=request.capture_crop
                    ),
                    201,
                )
                return
            parts = path.split("/")
            if (
                len(parts) == 5
                and parts[1:3] == ["api", "scans"]
                and parts[4] == "stack-quantity-one"
            ):
                if data != {}:
                    raise ValueError("This action only sets pending stack quantities to one")
                with sessions.lock:
                    self._json(
                        {"updated_count": assume_pending_stack_quantities(sessions.store, parts[3])}
                    )
                return
            if (
                len(parts) == 5
                and parts[1:3] == ["api", "scans"]
                and parts[4] in {"batch-preview", "batch-accept"}
            ):
                with sessions.lock:
                    if parts[4] == "batch-preview":
                        policy = BatchPolicy.model_validate(data)
                        self._json(preview_batch(sessions.store, parts[3], policy))
                    else:
                        request = AcceptBatchRequest.model_validate(data)
                        policy = BatchPolicy.model_validate(
                            request.model_dump(exclude={"preview_token"})
                        )
                        self._json(
                            accept_batch(sessions.store, parts[3], policy, request.preview_token)
                        )
                return
            if len(parts) == 5 and parts[1:3] == ["api", "scans"] and parts[4] == "apply":
                apply = ApplyScan.model_validate(data)
                self._json(apply_scan(sessions.store, parts[3], **apply.model_dump()))
                return
            if len(parts) == 5 and parts[1:3] == ["api", "scans"] and parts[4] == "add":
                entry = AddEntry.model_validate(data)
                self._json(
                    {
                        "inventory_ids": add(
                            sessions.store, entry.monster_id, entry.quantity, parts[3]
                        )
                    }
                )
                return
            if len(parts) != 5 or parts[1:3] != ["api", "sessions"]:
                self._json({"error": "Not found"}, 404)
                return
            session_id, action = parts[3:5]
            if action == "frame":
                frame = CapturedFrame.model_validate(data)
                try:
                    jpeg = base64.b64decode(frame.jpeg, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError("Malformed base64 frame") from exc
                self._json(sessions.submit(session_id, frame.sequence, frame.timestamp, jpeg))
            elif action == "control":
                control = Control.model_validate(data)
                self._json(sessions.control(session_id, control.action, control.discontinuity))
            elif action == "review":
                decision = Decision.model_validate(data)
                self._json(sessions.review(session_id, **decision.model_dump()))
            else:
                self._json({"error": "Not found"}, 404)
        except (ValueError, ValidationError, OSError, KeyError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception:
            LOG.exception("Live request failed")
            self._json({"error": "Frame processing failed; session paused. Check padbox.log"}, 500)
