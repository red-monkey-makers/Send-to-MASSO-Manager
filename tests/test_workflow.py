import tempfile
import time
import unittest
from pathlib import Path

from masso_core import MassoStatus, validate_masso_name_parts
from masso_workflow import UploadQueue


class FakeClient:
    def __init__(self):
        self.calls = []
        self.status = MassoStatus(connected=True, stopped_since=time.monotonic() - 3,
                                  last_packet_time=time.monotonic())

    def upload_file_async(self, path, folder, upload_id):
        self.calls.append((path, folder, upload_id))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = [Path(self.temp.name) / name for name in ("one.nc", "two.tap")]
        for path in self.paths:
            path.write_text("G0 X0\n")
        self.client = FakeClient()
        self.queue = UploadQueue(self.client)
        self.queue.add(self.paths, "/Jobs/")

    def finish(self):
        upload_id = self.queue.active_id
        self.queue.handle("upload_complete", {"upload_id": upload_id, "filename": "one.nc"})
        self.queue.handle("upload_state", False)
        self.queue.advance()

    def test_uploads_sequentially_after_worker_release(self):
        self.queue.start()
        self.assertEqual(len(self.client.calls), 1)
        self.queue.handle("upload_complete", {"upload_id": self.queue.active_id})
        self.queue.advance()
        self.assertEqual(len(self.client.calls), 1)
        self.queue.handle("upload_state", False)
        self.queue.advance()
        self.assertEqual(len(self.client.calls), 2)
        self.finish()
        self.assertFalse(self.queue.running)
        self.assertEqual([i.status for i in self.queue.items], ["Done", "Done"])

    def test_machine_safety_gates(self):
        for changes in ({"connected": False}, {"run_flag": 2}, {"fault_code": 0x15},
                        {"prompt_state": 0}, {"stopped_since": None},
                        {"stopped_since": time.monotonic()}, {"last_packet_time": time.monotonic() - 6}):
            with self.subTest(changes=changes):
                self.client.status = MassoStatus(connected=True, stopped_since=time.monotonic() - 3,
                                                last_packet_time=time.monotonic())
                for key, value in changes.items():
                    setattr(self.client.status, key, value)
                with self.assertRaises(ValueError):
                    self.queue.start()
                self.assertFalse(self.client.calls)

    def test_rechecks_status_between_files(self):
        self.queue.start()
        self.client.status.run_flag = 2
        self.finish()
        self.assertEqual(len(self.client.calls), 1)
        self.assertFalse(self.queue.running)
        self.assertEqual(self.queue.items[1].status, "Pending")

    def test_failed_upload_stops_queue_and_can_retry(self):
        self.queue.start()
        self.queue.handle("upload_failed", {"upload_id": self.queue.active_id, "reason": "No ACK"})
        self.queue.handle("upload_state", False)
        self.assertFalse(self.queue.running)
        self.assertEqual(self.queue.items[0].status, "Failed")
        self.queue.start()
        self.assertEqual(len(self.client.calls), 2)

    def test_folder_edits_preserve_sent_and_active_targets(self):
        self.queue.start()
        self.queue.set_folder("/Changed/")
        self.assertEqual(self.queue.items[0].folder, "\\Jobs\\")
        self.assertEqual(self.queue.items[1].folder, "\\Changed\\")
        self.finish()
        self.queue.set_folder("/Other/")
        self.assertEqual(self.queue.items[0].folder, "\\Jobs\\")
        self.assertEqual(self.queue.items[1].folder, "\\Changed\\")

    def test_active_upload_cannot_be_removed_or_cleared(self):
        self.queue.start()
        with self.assertRaises(ValueError):
            self.queue.remove({self.queue.active_id})
        with self.assertRaises(ValueError):
            self.queue.clear()

    def test_invalid_names_missing_and_empty_files_block_start(self):
        # Validate forbidden filename characters without creating a file that
        # Windows itself rejects. Remote folders never touch the local filesystem.
        ok, errors, _, _ = validate_masso_name_parts("bad?.nc", "\\")
        self.assertFalse(ok)
        self.assertIn("Invalid MASSO character(s): ?", errors)
        for filename, contents, folder in [("invalid.nc", "G0", "\\bad?\\"),
                                           ("café.nc", "G0", "\\"),
                                           ("empty.nc", "", "\\")]:
            with self.subTest(filename=filename, folder=folder):
                path = Path(self.temp.name) / filename
                path.write_text(contents, encoding="utf-8")
                self.queue.items.clear()
                self.queue.add([path], folder)
                with self.assertRaises(ValueError):
                    self.queue.start()
        self.paths[0].unlink()
        self.queue.items.clear()
        self.queue.add([self.paths[1]], "\\")
        self.paths[1].unlink()
        with self.assertRaises(ValueError):
            self.queue.start()
        self.assertFalse(self.client.calls)

    def test_auto_clear_after_success(self):
        self.queue.auto_clear = True
        self.queue.start()
        self.finish()
        self.finish()
        self.assertEqual(self.queue.items, [])

    def test_stale_completion_does_not_advance_active_upload(self):
        self.queue.start()
        active = self.queue.active_id
        self.queue.handle("upload_complete", {"upload_id": 999})
        self.assertEqual(self.queue.active_id, active)
        self.assertEqual(self.queue.items[0].status, "Sending")

    def test_running_queue_rejects_unconfirmed_additions(self):
        path = Path(self.temp.name) / "unexpected.bin"
        path.write_bytes(b"data")
        self.queue.start()
        original_ids = [item.id for item in self.queue.items]
        with self.assertRaises(ValueError):
            self.queue.add([path], "\\")
        self.assertEqual([item.id for item in self.queue.items], original_ids)
        self.finish()
        self.finish()
        self.queue.add([path], "\\")
        self.assertTrue(self.queue.validate())
