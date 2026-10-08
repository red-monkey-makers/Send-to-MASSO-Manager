"""Toolkit-independent queue orchestration for the desktop app."""
from dataclasses import dataclass
from pathlib import Path
import time

from masso_core import normalize_masso_folder, validate_masso_name_parts


@dataclass
class QueueItem:
    id: int
    path: str
    folder: str
    status: str = "Pending"


class UploadQueue:
    def __init__(self, client):
        self.client = client
        self.items: list[QueueItem] = []
        self.next_id = 1
        self.running = False
        self.active_id = None
        self.busy = False
        self.advance_pending = False
        self.auto_clear = False
        self.progress = 0
        self.last_upload = None
        self.message = "Add files to start a queue."

    def add(self, paths, folder):
        if self.running or self.busy:
            raise ValueError("Wait for the active queue to finish before adding files.")
        for raw_path in paths:
            path = Path(raw_path)
            if path.is_file():
                self.items.append(QueueItem(self.next_id, str(path.resolve()), normalize_masso_folder(folder)))
                self.next_id += 1

    def set_folder(self, folder):
        for item in self.items:
            if item.status in ("Pending", "Failed"):
                item.folder = normalize_masso_folder(folder)

    def pending(self):
        return [item for item in self.items if item.status in ("Pending", "Failed")]

    def allowed(self):
        # A socket connection alone does not mean the controller is responding.
        st = self.client.status
        ok, reason = st.upload_allowed()
        if ok and (not st.last_packet_time or time.monotonic() - st.last_packet_time > 5):
            return False, "Waiting for fresh machine status"
        return ok, reason

    def validate(self):
        warnings = []
        for item in self.pending():
            path = Path(item.path)
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"File missing or empty: {path.name}")
            ok, errors, notes, _ = validate_masso_name_parts(item.path, item.folder)
            if not ok:
                raise ValueError(f"{path.name}: " + " ".join(errors))
            warnings.extend(f"{path.name}: {note}" for note in notes)
        return warnings

    def start(self):
        if self.running or self.busy:
            return
        ok, reason = self.allowed()
        if not ok:
            raise ValueError(reason)
        if not self.pending():
            raise ValueError("Add pending files first.")
        self.validate()
        self.running = True
        self.advance()

    def advance(self):
        if not self.running or self.busy or self.active_id is not None:
            return
        self.advance_pending = False
        if not self.pending():
            self.running = False
            self.message = "Queue complete. All files sent."
            if self.auto_clear:
                self.items.clear()
            return
        ok, reason = self.allowed()
        if not ok:
            self.running = False
            self.message = f"Queue stopped: {reason}"
            return
        item = self.pending()[0]
        try:
            self.validate()
        except (ValueError, OSError) as exc:
            self.running = False
            self.message = str(exc)
            return
        item.status = "Sending"
        self.active_id = item.id
        self.busy = True
        self.progress = 0
        self.message = f"Sending {Path(item.path).name}"
        self.client.upload_file_async(item.path, item.folder, upload_id=item.id)

    def handle(self, kind, payload):
        if kind == "upload_state":
            self.busy = bool(payload)
        elif kind == "upload_progress":
            self.progress = int(payload)
        elif kind in ("upload_complete", "upload_failed"):
            upload_id = payload.get("upload_id")
            if upload_id != self.active_id or self.active_id is None:
                return
            item = next((item for item in self.items if item.id == upload_id), None)
            if item:
                item.status = "Done" if kind == "upload_complete" else "Failed"
            self.active_id = None
            if kind == "upload_complete":
                self.last_upload = payload
                self.advance_pending = self.running
                self.message = f"Sent {payload.get('filename', 'file')}"
            else:
                self.running = False
                self.message = "Queue stopped: " + payload.get("reason", "Upload failed")

    def remove(self, ids):
        if self.active_id in ids:
            raise ValueError("The file currently uploading cannot be removed.")
        self.items = [item for item in self.items if item.id not in ids]

    def clear(self):
        if self.busy or self.running:
            raise ValueError("Wait for the active upload before clearing the queue.")
        self.items.clear()
        self.message = "Queue cleared."

    def move(self, item_id, direction):
        if self.running or self.busy:
            return
        index = next((i for i, item in enumerate(self.items) if item.id == item_id), -1)
        target = index + direction
        if index >= 0 and 0 <= target < len(self.items):
            self.items[index], self.items[target] = self.items[target], self.items[index]
