#!/usr/bin/env python3
"""Send-to-MASSO Manager v2: Qt desktop interface over the shared UDP client."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import queue
import sys
import time

try:
    from PySide6.QtCore import Qt, QTimer, Signal, QItemSelectionModel
    from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence
    from PySide6.QtWidgets import (
        QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
        QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
        QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
        QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError:
    raise SystemExit("Install the desktop dependencies first:\n  python -m pip install -r requirements.txt")

from masso_core import (
    APP_DIR, RESOURCE_DIR, MassoClient, MassoStatus, build_masso_qr_payload, default_qr_filename,
    format_elapsed_seconds, load_config, normalize_masso_folder, save_config,
    validate_masso_name_parts,
)
from masso_workflow import QueueItem, UploadQueue

STYLE = """
QWidget { color: #202b38; font-size: 13px; }
QMainWindow, QWidget#workspace { background: #f2f4f5; }
QWidget#sidebar { background: #17212b; }
QWidget#sidebar QLabel { color: #b0bdc8; }
QWidget#sidebar QLabel#brand { color: #ffffff; font-size: 23px; font-weight: 700; }
QWidget#sidebar QPushButton { text-align: left; border: none; background: transparent; color: #bdc8d2; padding: 13px 15px; }
QWidget#sidebar QPushButton:checked { color: #ffffff; background: #2a3a47; border-left: 3px solid #64c8a9; }
QWidget#sidebar QPushButton:hover { background: #24323e; }
QFrame#card { background: #ffffff; border: 1px solid #dfe5e8; border-radius: 12px; }
QLabel#title { font-size: 29px; font-weight: 700; color: #17212b; }
QLabel#section { font-size: 17px; font-weight: 600; }
QLabel#eyebrow { color: #7a8691; font-size: 11px; font-weight: 600; }
QLabel#muted { color: #77838f; }
QLabel#metric { font-size: 24px; font-weight: 600; }
QLabel#state { font-size: 25px; font-weight: 600; }
QLabel#badge { background: #edf1f4; color: #667582; border-radius: 12px; padding: 6px 12px; font-size: 12px; }
QPushButton { background: #ffffff; border: 1px solid #d4dde3; border-radius: 7px; padding: 9px 14px; font-weight: 500; }
QPushButton:hover { background: #edf4f2; border-color: #90b7aa; }
QPushButton:pressed { background: #dcece6; }
QPushButton#primary { background: #177c60; border-color: #177c60; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #12664f; }
QPushButton#primary:disabled { background: #e1e8e5; border-color: #e1e8e5; color: #92a19b; }
QPushButton:disabled { background: #edf0f2; color: #99a3ac; border-color: #e1e6e9; }
QLineEdit, QComboBox { background: #ffffff; border: 1px solid #d4dde3; border-radius: 6px; padding: 8px 10px; selection-background-color: #177c60; }
QLineEdit:focus, QComboBox:focus { border-color: #177c60; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: white; color: #202b38; selection-background-color: #e5f2ec; selection-color: #17212b; }
QTableWidget { background: #ffffff; border: none; gridline-color: #eef1f3; selection-background-color: #e5f2ec; selection-color: #17212b; }
QHeaderView::section { background: #f7f9fa; color: #75828e; border: none; border-bottom: 1px solid #e6ebee; padding: 12px 10px; font-size: 11px; font-weight: 600; }
QTableWidget::item { padding-left: 10px; border-bottom: 1px solid #eef1f3; }
QProgressBar { background: #e9eef0; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #28a17d; border-radius: 3px; }
QCheckBox { color: #687583; spacing: 8px; }
QPlainTextEdit { background: #17212b; color: #c5d3dd; border: none; border-radius: 8px; padding: 10px; font-family: Menlo, Consolas, monospace; font-size: 11px; }
QScrollBar:vertical { width: 9px; background: #f3f5f6; }
QScrollBar::handle:vertical { background: #c9d2d8; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QToolTip { background: #17212b; color: white; border: none; padding: 8px; }
"""


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


def card():
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(12)
    return widget, layout


class FileTable(QTableWidget):
    filesDropped = Signal(list)

    def __init__(self):
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["FILE", "SIZE", "MASSO FOLDER", "STATUS"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(48)
        self.setShowGrid(False)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in [(1, 92), (2, 160), (3, 110)]:
            self.setColumnWidth(col, width)
        self.setMinimumHeight(220)
        self.placeholder = label("Drop G-code files here\n\nor choose Add files to build your queue.", "muted")
        self.placeholder.setParent(self.viewport())
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setAlternatingRowColors(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.placeholder.setGeometry(self.viewport().rect())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        self.filesDropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()


class DemoClient:
    """A preview never opens sockets or sends files."""
    def __init__(self):
        self.status = MassoStatus()
        self.connected = False

    def stop(self):
        pass


class MainWindow(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.setWindowTitle("Send-to-MASSO Manager · v2 Preview" if demo else "Send-to-MASSO Manager · v2")
        self.resize(1240, 960)
        self.setMinimumSize(1060, 740)
        icon = RESOURCE_DIR / "S2M.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.events = queue.Queue()
        self.client = DemoClient() if demo else MassoClient(self.events)
        self.workflow = UploadQueue(self.client)
        self.config = load_config() if not demo else {
            "profiles": [{"name": "Workshop MASSO", "ip": "192.168.1.120"}],
            "last_folder": "\\Jobs\\", "last_profile": "Workshop MASSO",
        }
        self.workflow.auto_clear = bool(self.config.get("auto_clear_queue", False))
        self.tools_data = {}
        self.tools_busy = False
        self.controller_serial = None
        self.build_ui()
        self.load_profiles()
        if demo:
            self.workflow.items = [QueueItem(1, "/preview/Bracket_Left.nc", "\\Jobs\\"),
                                   QueueItem(2, "/preview/Bracket_Right.nc", "\\Jobs\\"),
                                   QueueItem(3, "/preview/Mounting_Plate.tap", "\\Jobs\\", "Done")]
            self.workflow.next_id = 4
            self.workflow.message = "Preview mode · controller connections and uploads are disabled."
        self.refresh_table()
        self.refresh_status()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        add_action = QAction("Add files", self)
        add_action.setShortcut(QKeySequence("Ctrl+O"))
        add_action.triggered.connect(self.choose_files)
        self.addAction(add_action)

    def build_ui(self):
        shell = QWidget()
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.setCentralWidget(shell)
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(188)
        nav = QVBoxLayout(side)
        nav.setContentsMargins(16, 30, 16, 24)
        nav.setSpacing(6)
        nav.addWidget(label("SEND TO\nMASSO", "brand"))
        nav.addWidget(label("SHOP WORKSPACE", "eyebrow"))
        nav.addSpacing(30)
        self.pages = QStackedWidget()
        self.nav_buttons = []
        for index, text in enumerate(["File queue", "Tools & export", "Profiles"]):
            b = button(text.replace("&", "&&"), lambda checked=False, i=index: self.set_page(i))
            b.setCheckable(True)
            nav.addWidget(b)
            self.nav_buttons.append(b)
        self.nav_buttons[0].setChecked(True)
        nav.addStretch()
        nav.addWidget(label("LOCAL NETWORK\nDesktop edition · v2", "muted"))
        row.addWidget(side)
        work = QWidget()
        work.setObjectName("workspace")
        layout = QVBoxLayout(work)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(18)
        top = QHBoxLayout()
        self.page_title = label("File queue", "title")
        top.addWidget(self.page_title)
        top.addStretch()
        self.connection_badge = label("Offline", "badge")
        top.addWidget(self.connection_badge)
        layout.addLayout(top)
        connection, conn = card()
        conn.setContentsMargins(16, 12, 16, 12)
        line = QHBoxLayout()
        line.addWidget(label("CONTROLLER", "eyebrow"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(165)
        self.profile_combo.currentIndexChanged.connect(self.profile_selected)
        line.addWidget(self.profile_combo, 1)
        self.host = QLineEdit()
        self.host.setPlaceholderText("IP address or hostname")
        self.host.setMinimumWidth(150)
        line.addWidget(self.host, 1)
        self.connect_btn = button("Connect", self.toggle_connection)
        line.addWidget(self.connect_btn)
        conn.addLayout(line)
        layout.addWidget(connection)
        self.build_queue_page()
        self.build_tools_page()
        self.build_profiles_page()
        page_scroll = QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        page_scroll.setWidget(self.pages)
        page_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        layout.addWidget(page_scroll, 1)
        log_line = QHBoxLayout()
        self.log_toggle = button("Activity log ▸", self.toggle_log)
        self.log_toggle.setCheckable(True)
        log_line.addWidget(self.log_toggle)
        log_line.addStretch()
        log_line.addWidget(label("Independent utility for MASSO controllers", "muted"))
        layout.addLayout(log_line)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFixedHeight(145)
        self.log_view.hide()
        layout.addWidget(self.log_view)
        row.addWidget(work, 1)
        self.log("Desktop ready. Connect to a controller to receive live status.")

    def build_queue_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        status, body = card()
        top = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(label("MACHINE STATUS", "eyebrow"))
        self.state = label("Not connected", "state")
        left.addWidget(self.state)
        self.safety = label("Connect to receive live machine status.", "muted")
        left.addWidget(self.safety)
        top.addLayout(left, 2)
        self.metrics = {}
        for key, heading, value in [("progress", "JOB PROGRESS", "—"), ("elapsed", "ELAPSED", "—"), ("jobs", "JOB COUNT", "—")]:
            column = QVBoxLayout()
            column.addWidget(label(heading, "eyebrow"))
            self.metrics[key] = label(value, "metric")
            column.addWidget(self.metrics[key])
            top.addLayout(column, 1)
        body.addLayout(top)
        self.machine_file = label("Current file   —", "muted")
        body.addWidget(self.machine_file)
        self.machine_progress = QProgressBar()
        self.machine_progress.setTextVisible(False)
        body.addWidget(self.machine_progress)
        layout.addWidget(status)
        queue_card, body = card()
        heading = QHBoxLayout()
        heading.addWidget(label("Upload queue", "section"))
        self.queue_count = label("0 files", "muted")
        heading.addWidget(self.queue_count)
        heading.addStretch()
        self.add_btn = button("+ Add files", self.choose_files)
        heading.addWidget(self.add_btn)
        body.addLayout(heading)
        folder = QHBoxLayout()
        folder.addWidget(label("Target folder", "muted"))
        self.folder = QLineEdit(self.config.get("last_folder", "\\"))
        self.folder.setPlaceholderText("\\Jobs\\")
        self.folder.textChanged.connect(self.folder_changed)
        folder.addWidget(self.folder, 1)
        body.addLayout(folder)
        self.table = FileTable()
        self.table.filesDropped.connect(self.add_files)
        self.table.itemSelectionChanged.connect(self.refresh_target)
        body.addWidget(self.table, 1)
        tools = QHBoxLayout()
        self.queue_buttons = []
        for text, action in [("Remove", self.remove_selected), ("Clear", self.clear_queue),
                             ("Move up", lambda: self.move_selected(-1)), ("Move down", lambda: self.move_selected(1))]:
            b = button(text, action)
            tools.addWidget(b)
            self.queue_buttons.append(b)
        tools.addStretch()
        self.qr_btn = button("QR codes…", self.generate_qr)
        tools.addWidget(self.qr_btn)
        body.addLayout(tools)
        self.target_preview = label("Target   \\", "muted")
        self.target_preview.setWordWrap(True)
        body.addWidget(self.target_preview)
        footer = QHBoxLayout()
        self.auto_clear = QCheckBox("Clear queue after success")
        self.auto_clear.setChecked(self.workflow.auto_clear)
        self.auto_clear.toggled.connect(self.auto_clear_changed)
        footer.addWidget(self.auto_clear)
        footer.addStretch()
        self.send_btn = button("Send queue →", self.send_queue, True)
        self.send_btn.setMinimumWidth(160)
        footer.addWidget(self.send_btn)
        body.addLayout(footer)
        self.transfer_progress = QProgressBar()
        self.transfer_progress.setTextVisible(False)
        body.addWidget(self.transfer_progress)
        self.transfer_message = label("Add files to start a queue.", "muted")
        self.transfer_message.setWordWrap(True)
        body.addWidget(self.transfer_message)
        layout.addWidget(queue_card, 1)
        self.pages.addWidget(page)

    def build_tools_page(self):
        page, layout = card()
        layout.addWidget(label("Controller tool library", "section"))
        description = label("Read tool numbers and names from the controller, then export a text file.", "muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        actions = QHBoxLayout()
        self.scan_btn = button("Download tools", self.scan_tools, True)
        self.export_btn = button("Export text…", self.export_tools)
        actions.addWidget(self.scan_btn)
        actions.addWidget(self.export_btn)
        actions.addStretch()
        layout.addLayout(actions)
        self.tools_status = label("No tool data downloaded yet.", "muted")
        layout.addWidget(self.tools_status)
        self.tools_table = QTableWidget(0, 2)
        self.tools_table.setHorizontalHeaderLabels(["TOOL NUMBER", "TOOL NAME"])
        self.tools_table.verticalHeader().hide()
        self.tools_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tools_table.setColumnWidth(0, 150)
        self.tools_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.tools_table, 1)
        self.pages.addWidget(page)

    def build_profiles_page(self):
        page, layout = card()
        layout.addWidget(label("Connection profiles", "section"))
        layout.addWidget(label("Save a controller address for each machine or workshop.", "muted"))
        form = QFormLayout()
        form.setVerticalSpacing(18)
        self.profile_name = QLineEdit()
        self.profile_ip = QLineEdit()
        self.profile_name.setPlaceholderText("Workshop MASSO")
        self.profile_ip.setPlaceholderText("192.168.1.120")
        form.addRow("Profile name", self.profile_name)
        form.addRow("Controller address", self.profile_ip)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.profile_buttons = [button("Save profile", self.save_profile, True),
                                button("New profile", self.new_profile),
                                button("Delete profile", self.delete_profile)]
        for b in self.profile_buttons:
            actions.addWidget(b)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        layout.addWidget(label("Profiles are shared with the original desktop app.", "muted"))
        self.pages.addWidget(page)

    def set_page(self, index):
        self.pages.setCurrentIndex(index)
        self.page_title.setText(["File queue", "Tools & export", "Profiles"][index])
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(index == i)

    def persist(self):
        if not self.demo:
            save_config(self.config)

    def load_profiles(self):
        if self.client.connected:
            return
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self.config.get("profiles", []):
            self.profile_combo.addItem(profile["name"], profile["ip"])
        index = self.profile_combo.findText(self.config.get("last_profile", ""))
        if index >= 0:
            self.profile_combo.setCurrentIndex(index)
        self.profile_combo.blockSignals(False)
        self.profile_selected()

    def profile_selected(self, *_):
        if self.client.connected:
            return
        self.host.setText(self.profile_combo.currentData() or "")
        self.profile_name.setText(self.profile_combo.currentText())
        self.profile_ip.setText(self.host.text())

    def save_profile(self):
        if self.client.connected:
            return self.warn("Disconnect before changing connection profiles.")
        name, ip = self.profile_name.text().strip(), self.profile_ip.text().strip()
        if not name or not ip:
            return self.warn("Enter both a profile name and a controller address.")
        profiles = self.config.setdefault("profiles", [])
        existing = next((p for p in profiles if p["name"] == name), None)
        if existing:
            existing["ip"] = ip
        else:
            profiles.append({"name": name, "ip": ip})
        self.config["last_profile"] = name
        self.persist()
        self.load_profiles()
        self.log(f"Saved profile: {name}")

    def new_profile(self):
        if self.client.connected:
            return self.warn("Disconnect before changing connection profiles.")
        self.profile_name.clear()
        self.profile_ip.clear()
        self.profile_name.setFocus()

    def delete_profile(self):
        if self.client.connected:
            return self.warn("Disconnect before changing connection profiles.")
        name = self.profile_name.text().strip()
        if not any(p["name"] == name for p in self.config.get("profiles", [])):
            return self.warn("Select a saved profile first.")
        if QMessageBox.question(self, "Delete profile", f"Delete {name}?") != QMessageBox.StandardButton.Yes:
            return
        self.config["profiles"] = [p for p in self.config["profiles"] if p["name"] != name]
        self.config["last_profile"] = ""
        self.persist()
        self.load_profiles()

    def toggle_connection(self):
        if self.demo:
            return
        if self.workflow.busy or self.workflow.running or self.tools_busy:
            return self.warn("Wait for the active operation to finish.")
        if self.client.connected:
            self.client.stop()
            self.tools_data.clear()
            self.controller_serial = None
            self.refresh_tools()
        else:
            host = self.host.text().strip()
            if not host:
                return self.warn("Enter a controller address first.")
            self.config["last_profile"] = self.profile_combo.currentText()
            self.persist()
            self.tools_data.clear()
            self.controller_serial = None
            self.refresh_tools()
            self.client.start(host)
        self.refresh_status()

    def choose_files(self):
        if self.workflow.running or self.workflow.busy:
            return self.warn("Wait for the active queue to finish before adding files.")
        paths, _ = QFileDialog.getOpenFileNames(self, "Add G-code files", self.config.get("last_local_dir", str(Path.home())),
                                               "G-code (*.nc *.cnc *.tap *.eia *.txt);;All files (*)")
        if paths:
            self.add_files(paths)

    def add_files(self, paths):
        try:
            self.workflow.add(paths, self.folder.text() or "\\")
        except ValueError as exc:
            return self.warn(str(exc))
        if paths:
            self.config["last_local_dir"] = str(Path(paths[-1]).parent)
            self.persist()
        self.refresh_table()

    def folder_changed(self):
        self.workflow.set_folder(self.folder.text() or "\\")
        self.refresh_table()

    def selected_ids(self):
        return {self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in self.table.selectionModel().selectedRows()}

    def remove_selected(self):
        try:
            self.workflow.remove(self.selected_ids())
        except ValueError as exc:
            self.warn(str(exc))
        self.refresh_table()

    def clear_queue(self):
        try:
            self.workflow.clear()
        except ValueError as exc:
            self.warn(str(exc))
        self.refresh_table()

    def move_selected(self, direction):
        ids = self.selected_ids()
        if len(ids) == 1:
            self.workflow.move(next(iter(ids)), direction)
            self.refresh_table()

    def refresh_table(self):
        selected = self.selected_ids()
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(self.workflow.items))
        colors = {"Pending": "#72808d", "Sending": "#16735b", "Done": "#16735b", "Failed": "#b34343"}
        for row, item in enumerate(self.workflow.items):
            path = Path(item.path)
            try:
                size = path.stat().st_size
                size_text = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
            except OSError:
                size_text = "Preview" if self.demo else "Missing"
            for col, value in enumerate([path.name, size_text, item.folder, item.status]):
                cell = QTableWidgetItem(value)
                cell.setToolTip(item.path if col == 0 else value)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.id)
                if col == 3:
                    cell.setForeground(QColor(colors[item.status]))
                self.table.setItem(row, col, cell)
            if item.id in selected:
                self.table.selectionModel().select(
                    self.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
        self.table.blockSignals(False)
        self.queue_count.setText(f"{len(self.workflow.items)} file{'s' if len(self.workflow.items) != 1 else ''}")
        self.table.placeholder.setVisible(not self.workflow.items)
        # Keep the table visible when empty so it remains a drop target.
        self.refresh_target()
        self.refresh_status()

    def refresh_target(self):
        ids = self.selected_ids()
        item = next((i for i in self.workflow.items if i.id in ids), None)
        if item is None:
            item = next(iter(self.workflow.pending()), None)
        if item:
            _, errors, warnings, preview = validate_masso_name_parts(item.path, item.folder)
            text = "Target   " + preview
            if errors or warnings:
                text += "\n" + " ".join(errors or warnings)
        else:
            text = "Target   " + normalize_masso_folder(self.folder.text() or "\\")
        self.target_preview.setText(text)

    def auto_clear_changed(self, value):
        self.workflow.auto_clear = value
        self.config["auto_clear_queue"] = value
        self.persist()

    def send_queue(self):
        if self.demo:
            return
        try:
            warnings = self.workflow.validate()
            if warnings and QMessageBox.question(self, "Target warning", "\n".join(warnings[:8]) + "\n\nSend anyway?") != QMessageBox.StandardButton.Yes:
                return
            self.config["last_folder"] = normalize_masso_folder(self.folder.text() or "\\")
            self.persist()
            self.workflow.start()
        except (ValueError, OSError) as exc:
            self.warn(str(exc))
        self.refresh_table()

    def refresh_status(self):
        st = self.client.status
        allowed, reason = self.workflow.allowed()
        fresh = bool(st.last_packet_time and time.monotonic() - st.last_packet_time <= 5)
        self.connection_badge.setText("Preview" if self.demo else ("Connected" if st.connected and fresh else "Connecting…" if st.connected else "Offline"))
        state_text = st.state_text() if st.connected and fresh else "Waiting for controller" if st.connected else "Not connected"
        self.state.setText(state_text)
        color = "#b34343" if st.connected and st.faulted else "#b78022" if st.connected and (st.running or st.prompt_waiting) else "#177c60" if allowed else "#596977"
        self.state.setStyleSheet(f"color: {color};")
        self.safety.setText("Uploads enabled · machine is stopped" if allowed else "Uploads locked · " + reason)
        self.metrics["progress"].setText(f"{st.progress}%" if fresh else "—")
        self.metrics["elapsed"].setText(format_elapsed_seconds(st.elapsed_seconds) if fresh else "—")
        self.metrics["jobs"].setText(str(st.job_count) if fresh else "—")
        self.machine_file.setText("Current file   " + (st.filename or "—"))
        self.machine_progress.setValue(st.progress if fresh else 0)
        busy = self.workflow.busy or self.workflow.running
        self.connect_btn.setText("Disconnect" if self.client.connected else "Connect")
        self.connect_btn.setEnabled(not self.demo and not busy and not self.tools_busy)
        self.profile_combo.setEnabled(not self.client.connected and not busy)
        self.host.setEnabled(not self.client.connected and not busy)
        self.profile_name.setEnabled(not self.client.connected)
        self.profile_ip.setEnabled(not self.client.connected)
        for b in self.profile_buttons:
            b.setEnabled(not self.client.connected)
        self.add_btn.setEnabled(not busy)
        valid = bool(self.workflow.pending())
        try:
            self.workflow.validate()
        except (ValueError, OSError):
            valid = False
        self.send_btn.setEnabled(not self.demo and allowed and valid and not busy and not self.tools_busy)
        self.send_btn.setToolTip("Send all pending files" if allowed else reason)
        for b in self.queue_buttons:
            b.setEnabled(not busy)
        self.qr_btn.setEnabled(bool(self.workflow.items))
        self.scan_btn.setEnabled(not self.demo and st.connected and fresh and not busy and not self.tools_busy)
        self.export_btn.setEnabled(bool(self.tools_data) and not self.tools_busy)
        self.transfer_progress.setValue(self.workflow.progress)
        self.transfer_message.setText(self.workflow.message)

    def poll(self):
        changed = False
        for _ in range(200):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(str(payload))
            elif kind == "controller_serial":
                self.controller_serial = payload
            elif kind == "tool_data":
                if payload.get("tool_name"):
                    self.tools_data[int(payload["tool_index"])] = payload["tool_name"]
                    self.refresh_tools()
            elif kind == "tools_scan_complete":
                self.tools_busy = False
                self.tools_status.setText(f"{len(self.tools_data)} populated tools downloaded. Export when ready." if payload.get("ok") else "Download failed: " + payload.get("reason", "Unknown error"))
                self.refresh_tools()
            self.workflow.handle(kind, payload)
            if kind.startswith("upload_"):
                changed = True
        # Wait until the previous worker posts its final upload_state=False.
        if self.workflow.advance_pending and not self.workflow.busy:
            self.workflow.advance()
            changed = True
        if changed:
            self.refresh_table()
        self.refresh_status()

    def scan_tools(self):
        if self.demo or not self.client.connected or self.workflow.busy or self.workflow.running or self.tools_busy:
            return
        self.tools_data.clear()
        self.tools_busy = True
        self.tools_status.setText("Requesting tool slots 1–118…")
        self.refresh_tools()
        self.client.get_tools_data_async()

    def refresh_tools(self):
        self.tools_table.setRowCount(len(self.tools_data))
        for row, (number, name) in enumerate(sorted(self.tools_data.items())):
            self.tools_table.setItem(row, 0, QTableWidgetItem(str(number)))
            self.tools_table.setItem(row, 1, QTableWidgetItem(name))

    def export_tools(self):
        if not self.tools_data or self.tools_busy:
            return
        export_dir = Path.home() if getattr(sys, "frozen", False) else APP_DIR
        output, _ = QFileDialog.getSaveFileName(self, "Export tool data", str(export_dir / "MASSO Tools.txt"), "Text file (*.txt)")
        if not output:
            return
        serial = f"G3-{self.controller_serial}" if self.controller_serial else "Unknown"
        lines = ["-" * 76, f"Tools Data for Machine: {self.profile_combo.currentText() or 'MASSO'}",
                 f"MASSO Serial No: {serial}", f"Created on: {datetime.now():%d %b %Y, %I:%M:%S %p}",
                 "-" * 76, "", "TOOL NO.     TOOL NAME", "========     =============================="]
        lines.extend(f"{number:>6}       {name}" for number, name in sorted(self.tools_data.items()))
        lines.extend(["", "-- MAKE WITH MASSO --", ""])
        try:
            Path(output).write_text("\n".join(lines), encoding="utf-8")
            self.log(f"Tools exported to {output}")
            self.tools_status.setText(f"Exported {len(self.tools_data)} tools to {Path(output).name}")
        except OSError as exc:
            self.warn(str(exc))

    def generate_qr(self):
        ids = self.selected_ids()
        items = [item for item in self.workflow.items if item.id in ids] if ids else self.workflow.items
        if not items:
            return
        for item in items:
            ok, errors, _, _ = validate_masso_name_parts(item.path, item.folder)
            if not ok:
                return self.warn(" ".join(errors))
        output = QFileDialog.getExistingDirectory(self, "Save QR codes for selected files" if ids else "Save QR codes for queue")
        if not output:
            return
        try:
            import qrcode
            names = [default_qr_filename(item.path) for item in items]
            if len(set(names)) != len(names):
                return self.warn("Files with the same name would overwrite QR images. Select them separately.")
            destinations = [Path(output) / name for name in names]
            if any(path.exists() for path in destinations):
                if QMessageBox.question(self, "Replace QR codes", "Some QR images already exist. Replace them?") != QMessageBox.StandardButton.Yes:
                    return
            for item, destination in zip(items, destinations):
                qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
                qr.add_data(build_masso_qr_payload(item.path, item.folder))
                qr.make(fit=True)
                qr.make_image(fill_color="black", back_color="white").save(destination)
            self.log(f"Saved {len(items)} QR code(s) to {output}")
            QMessageBox.information(self, "QR codes saved", f"Saved {len(items)} QR code(s) to:\n{output}")
        except Exception as exc:
            self.warn(f"Could not generate QR codes: {exc}")

    def toggle_log(self, checked):
        self.log_view.setVisible(checked)
        self.log_toggle.setText("Activity log ▾" if checked else "Activity log ▸")

    def log(self, message):
        self.log_view.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")

    def warn(self, message):
        QMessageBox.warning(self, "Send-to-MASSO", message)

    def closeEvent(self, event):
        if self.workflow.busy or self.workflow.running or self.tools_busy:
            self.warn("Wait for the active upload or tool download before closing.")
            event.ignore()
            return
        self.timer.stop()
        self.client.stop()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Preview with sample queue, no network connections or settings writes")
    parser.add_argument("--screenshot", type=Path, help="Save a demo screenshot and exit")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(QFont("Helvetica Neue" if sys.platform == "darwin" else "Segoe UI", 10))
    app.setStyleSheet(STYLE)
    window = MainWindow(demo=args.demo or bool(args.screenshot))
    window.show()
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(args.screenshot)):
                print("Could not save screenshot", file=sys.stderr)
                app.exit(1)
            else:
                app.quit()
        QTimer.singleShot(300, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
