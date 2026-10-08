import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from masso_core import MassoStatus
from send_to_masso_v2 import MainWindow, STYLE


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.window = MainWindow(demo=True)
        self.window.timer.stop()
        self.window.workflow.items.clear()
        self.window.refresh_table()
        self.addCleanup(self.window.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "part.nc"
        self.path.write_text("G0 X0\n")

    def test_new_desktop_runs_without_tkinter(self):
        import sys
        self.assertNotIn("tkinter", sys.modules)
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window.send_btn.isEnabled())
        self.assertTrue(self.window.table.placeholder.isVisible())

    def test_add_move_remove_and_folder_preview(self):
        other = Path(self.temp.name) / "second.tap"
        other.write_text("G0 Y0\n")
        self.window.add_files([str(self.path), str(other)])
        self.assertEqual(self.window.table.rowCount(), 2)
        self.window.table.selectRow(0)
        self.window.move_selected(1)
        self.assertEqual(Path(self.window.workflow.items[1].path).name, "part.nc")
        self.window.folder.setText("/Test/")
        self.assertIn("\\Test\\part.nc", self.window.target_preview.text())
        self.window.remove_selected()
        self.assertEqual(self.window.table.rowCount(), 1)

    def test_render_all_pages_and_log_at_minimum_size(self):
        self.window.resize(1060, 740)
        self.window.show()
        for page in range(3):
            self.window.set_page(page)
            self.window.toggle_log(True)
            self.app.processEvents()
            self.assertFalse(self.window.grab().isNull())

    def test_demo_cannot_enable_sending_even_with_ready_status(self):
        self.window.add_files([str(self.path)])
        self.window.client.status = MassoStatus(connected=True, stopped_since=time.monotonic() - 3,
                                               last_packet_time=time.monotonic())
        self.window.refresh_status()
        self.assertFalse(self.window.send_btn.isEnabled())
        self.assertFalse(self.window.connect_btn.isEnabled())

    def test_live_send_and_worker_events(self):
        self.window.demo = False
        self.window.client.status = MassoStatus(connected=True, stopped_since=time.monotonic() - 3,
                                               last_packet_time=time.monotonic())
        self.window.client.connected = True
        calls = []
        self.window.client.upload_file_async = lambda path, folder, upload_id: calls.append(upload_id)
        with patch("send_to_masso_v2.save_config"):
            self.window.add_files([str(self.path)])
            self.assertTrue(self.window.send_btn.isEnabled())
            self.window.send_queue()
        self.assertEqual(len(calls), 1)
        self.window.events.put(("upload_complete", {"upload_id": calls[0], "filename": "part.nc"}))
        self.window.events.put(("upload_state", False))
        self.window.poll()
        self.assertFalse(self.window.workflow.running)
        self.assertEqual(self.window.workflow.items[0].status, "Done")

    def test_tool_events_export_and_qr_generation(self):
        self.window.events.put(("tool_data", {"tool_index": 3, "tool_name": "End mill"}))
        self.window.events.put(("tools_scan_complete", {"ok": True}))
        self.window.poll()
        self.assertEqual(self.window.tools_table.item(0, 1).text(), "End mill")
        output = Path(self.temp.name) / "tools.txt"
        with patch("send_to_masso_v2.QFileDialog.getSaveFileName", return_value=(str(output), "")):
            self.window.export_tools()
        self.assertIn("End mill", output.read_text())
        self.window.add_files([str(self.path)])
        with patch("send_to_masso_v2.QFileDialog.getExistingDirectory", return_value=self.temp.name), \
             patch("send_to_masso_v2.QMessageBox.information"):
            self.window.generate_qr()
        from PIL import Image
        image_path = Path(self.temp.name) / "part_MASSO_QR.png"
        with Image.open(image_path) as image:
            self.assertEqual(image.format, "PNG")

    def test_connected_profile_changes_preserve_active_identity(self):
        from copy import deepcopy
        self.window.client.connected = True
        self.window.client.host = self.window.host.text()
        active_address = self.window.host.text()
        active_name = self.window.profile_combo.currentText()
        saved = deepcopy(self.window.config)
        self.window.refresh_status()
        self.assertFalse(self.window.profile_name.isEnabled())
        self.assertFalse(self.window.profile_ip.isEnabled())
        self.assertTrue(all(not b.isEnabled() for b in self.window.profile_buttons))
        # Guard callbacks too, even if invoked directly rather than by a button.
        self.window.profile_name.setText("Other machine")
        self.window.profile_ip.setText("192.168.1.121")
        with patch.object(self.window, "warn") as warning:
            self.window.save_profile()
            self.window.new_profile()
            self.window.delete_profile()
        self.assertEqual(warning.call_count, 3)
        self.assertEqual(self.window.config, saved)
        self.assertEqual(self.window.host.text(), active_address)
        self.assertEqual(self.window.profile_combo.currentText(), active_name)
        self.assertEqual(self.window.client.host, active_address)
        self.window.client.connected = False
        self.window.refresh_status()
        self.window.save_profile()
        self.assertEqual(self.window.host.text(), "192.168.1.121")
        self.assertTrue(self.window.profile_name.isEnabled())

    def test_refresh_preserves_all_selected_rows_for_batch_removal(self):
        from PySide6.QtCore import QItemSelectionModel
        paths = [self.path, Path(self.temp.name) / "second.tap", Path(self.temp.name) / "third.nc"]
        for path in paths:
            path.write_text("G0 X0\n")
        self.window.add_files([str(path) for path in paths])
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for row in (0, 2):
            self.window.table.selectionModel().select(self.window.table.model().index(row, 0), flags)
        selected = self.window.selected_ids()
        self.assertEqual(len(selected), 2)
        self.window.refresh_table()
        self.assertEqual(self.window.selected_ids(), selected)
        self.window.folder.setText("/Changed/")
        self.assertEqual(self.window.selected_ids(), selected)
        self.window.remove_selected()
        self.assertEqual(len(self.window.workflow.items), 1)
        self.assertEqual(Path(self.window.workflow.items[0].path).name, "second.tap")

    def test_busy_queue_blocks_picker_and_dropped_files(self):
        self.window.workflow.running = True
        self.window.refresh_status()
        self.assertFalse(self.window.add_btn.isEnabled())
        with patch.object(self.window, "warn") as warning, \
             patch("send_to_masso_v2.QFileDialog.getOpenFileNames") as picker:
            self.window.choose_files()
            self.window.table.filesDropped.emit([str(self.path)])
        picker.assert_not_called()
        self.assertEqual(warning.call_count, 2)
        self.assertFalse(self.window.workflow.items)
        self.window.workflow.running = False
        self.window.refresh_status()
        self.assertTrue(self.window.add_btn.isEnabled())
        self.window.table.filesDropped.emit([str(self.path)])
        self.assertEqual(len(self.window.workflow.items), 1)
