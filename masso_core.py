"""Shared MASSO protocol and configuration; independent of the UI toolkit."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import socket
import subprocess
import struct
import threading
import webbrowser
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Callable

APP_NAME = "Send-to-MASSO Manager"
# Keep the shop utility self-contained: profiles/config live beside the program.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
# Bundled assets are separate from writable settings in a frozen application.
RESOURCE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False) and sys.platform == "darwin":
    CONFIG_FILE = Path.home() / "Library" / "Application Support" / "Send-to-MASSO Manager" / "send_to_masso.json"
else:
    CONFIG_FILE = APP_DIR / "send_to_masso.json"
CONTROLLER_PORT = 65535
LOCAL_PORT_START = 11000
LOCAL_PORT_END = 11050
CHUNK_SIZE = 1422
STOPPED_STABLE_SECONDS = 1.5
SUPPORTED_EXTENSIONS = {".nc", ".cnc", ".tap", ".eia", ".txt"}
INVALID_TARGET_CHARS = set(':*?"<>|')
TOOL_INDEX_MIN = 1
TOOL_INDEX_MAX = 118


ALARM_CODE_NAMES = {
    0x00: "X Motor Alarm",
    0x01: "Y Motor Alarm",
    0x02: "Z Motor Alarm",
    0x03: "A Motor Alarm",
    0x04: "B Motor Alarm",
    0x05: "Spindle Alarm",
    0x06: "Air Pressure Low Alarm",
    0x14: "Lubricant Low Alarm",
    0x15: "Torch Breakaway",
}


def alarm_code_text(code: int) -> str:
    """Return a human-readable MASSO status byte-7 alarm name."""
    if code == 0xFF:
        return "No Fault"
    return ALARM_CODE_NAMES.get(code, f"Fault / Alarm 0x{code:02X}")




# -----------------------------
# Protocol helpers
# -----------------------------

def crc16_ccitt_le(data: bytes) -> bytes:
    """Calculate CRC16-CCITT, returned as little-endian bytes."""
    crc = 0x0000
    poly = 0x1021
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ poly
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc.to_bytes(2, "little")


def with_crc(payload: bytes) -> bytes:
    return crc16_ccitt_le(payload) + payload


def final_chunk_trailer_len(data_length: int) -> int:
    """Return MASSO compact-final trailer length (1-4 bytes).

    The payload after the 2-byte CRC has an 11-byte protocol header and must
    land on a 4-byte boundary. MASSO Link still emits 4 bytes when the payload
    would otherwise already be aligned.
    """
    trailer = (-(11 + int(data_length))) % 4
    return trailer or 4


def decode_data_ack_next(ack: bytes) -> int:
    """Decode type-0x0B ACK next-expected index from bytes 6-7, little-endian."""
    if len(ack) < 8 or ack[4] != 0x0B:
        raise ValueError("Not a valid MASSO data ACK")
    return int.from_bytes(ack[6:8], "little")


def normalize_masso_folder(folder: str) -> str:
    r"""MASSO Link captures used backslash-delimited folders like \Folder\."""
    folder = (folder or "\\").strip().replace("/", "\\")
    if not folder:
        folder = "\\"
    if not folder.startswith("\\"):
        folder = "\\" + folder
    if not folder.endswith("\\"):
        folder += "\\"
    return folder


def build_remote_target_preview(local_path: str, remote_folder: str) -> str:
    """Return the exact MASSO-style target string shown to the operator."""
    filename = Path(local_path).name if local_path else ""
    folder = normalize_masso_folder(remote_folder)
    return folder + filename if filename else folder


def build_masso_qr_payload(local_path: str, remote_folder: str) -> str:
    """Build MASSO QR payload for loading a G-code file.

    MASSO documentation describes payloads in the form:
        ^CSLG<path-to-gcode-file>^CE

    The examples do not show a leading root backslash, so strip leading root backslashes from
    the displayed MASSO target for QR generation. Internal folder separators are
    left as MASSO-style backslashes.
    """
    target = build_remote_target_preview(local_path, remote_folder)
    qr_target = target.lstrip("\\")
    return f"^CSLG{qr_target}^CE"


def default_qr_filename(local_path: str) -> str:
    path = Path(local_path)
    return f"{path.stem}_MASSO_QR.png"


def validate_masso_name_parts(local_path: str, remote_folder: str) -> tuple[bool, list[str], list[str], str]:
    """Validate the target path/name using the shop-tested practical rule set.

    Forward slashes in folder input are normalized to backslashes before
    validation. Backslashes are allowed as folder separators.
    Returns: ok, errors, warnings, preview
    """
    errors: list[str] = []
    warnings: list[str] = []
    folder = normalize_masso_folder(remote_folder)
    filename = Path(local_path).name if local_path else ""
    preview = folder + filename if filename else folder

    names_only = preview.replace("\\", "")
    for ch in names_only:
        if ord(ch) < 32:
            errors.append("Control characters are not allowed in MASSO file/folder names.")
            break
    bad = sorted({ch for ch in names_only if ch in INVALID_TARGET_CHARS})
    if bad:
        errors.append("Invalid MASSO character(s): " + " ".join(bad))
    if any(ord(ch) > 127 for ch in names_only):
        errors.append("MASSO target contains non-ASCII characters. Use plain ASCII names, for example cafe.tap instead of café.tap.")

    if filename:
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            warnings.append(f"MASSO normally expects one of: {allowed}")

    return not errors, errors, warnings, preview


@dataclass
class MassoStatus:
    connected: bool = False
    progress: int = 0
    run_flag: int = 0
    fault_code: int = 0xFF
    job_count: int = 0
    prompt_state: int = 0x01
    elapsed_seconds: int = 0
    filename: str = ""
    raw_len: int = 0
    last_packet_time: float = 0.0
    stopped_since: Optional[float] = None
    feed_hold_active: bool = False

    @property
    def running(self) -> bool:
        return self.run_flag == 0x02

    @property
    def breakaway(self) -> bool:
        return self.fault_code == 0x15

    @property
    def faulted(self) -> bool:
        return self.fault_code != 0xFF

    @property
    def alarm_text(self) -> str:
        return alarm_code_text(self.fault_code)

    @property
    def prompt_waiting(self) -> bool:
        return self.prompt_state == 0x00

    def state_text(self) -> str:
        if self.faulted:
            return self.alarm_text
        if self.prompt_waiting:
            return "Waiting for User / Tool Change"
        if self.feed_hold_active:
            return "Feed Hold"
        if self.running:
            return f"Machine Running - {self.progress}%"
        return "Machine Stopped"

    def upload_allowed(self) -> tuple[bool, str]:
        if not self.connected:
            return False, "Not connected"
        if self.running:
            return False, "Machine running"
        if self.faulted:
            return False, self.alarm_text
        if self.prompt_waiting:
            return False, "Waiting for user/tool change"
        if self.stopped_since is None:
            return False, "Waiting for stopped status"
        stable_for = time.monotonic() - self.stopped_since
        if stable_for < STOPPED_STABLE_SECONDS:
            return False, "Stopped debounce"
        return True, "Ready"


class MassoClient:
    """Small UDP protocol client. All GUI updates are sent through event_queue."""

    def __init__(self, event_queue: queue.Queue):
        self.event_queue = event_queue
        self.host: Optional[str] = None
        self.controller_address: Optional[str] = None
        # MASSO Link appears to use two UDP sockets:
        #   - receive/status socket bound to UDP 11000
        #   - send socket using an ephemeral source port
        # V1.1 used one bound socket for both directions. That worked for status,
        # but file chunks timed out on the Touch. V1.2 separates RX and TX.
        self.socket: Optional[socket.socket] = None       # RX socket, bound to 11000-11050
        self.tx_socket: Optional[socket.socket] = None    # TX socket, ephemeral source port
        self.local_port: Optional[int] = None
        self.tx_local_port: Optional[int] = None
        self.connected = False
        self.listening = False
        self.upload_in_progress = False

        self.listen_thread: Optional[threading.Thread] = None
        self.keepalive_thread: Optional[threading.Thread] = None
        self.tx_listen_thread: Optional[threading.Thread] = None
        self._seen_first_status = False
        self.status = MassoStatus()
        self.last_status_raw: Optional[bytes] = None
        self.controller_serial: Optional[int] = None

        self._ack_event = threading.Event()
        self._last_ack: Optional[bytes] = None

        self._lock = threading.Lock()
        self._tx_lock = threading.Lock()
        self._tx_generation = 0

        # MASSO Link snapshots these fields at connection time. The config
        # packet uses all six values; keepalive and tool requests reuse parts
        # of the same snapshot rather than reading a live clock each time.
        self._connect_time_fields: Optional[tuple[int, int, int, int, int, int]] = None

    def post(self, event_type: str, payload: Any = None) -> None:
        self.event_queue.put((event_type, payload))

    def log(self, msg: str) -> None:
        self.post("log", msg)

    def start(self, host: str) -> bool:
        self.stop()
        self.host = host.strip()
        if not self.host:
            self.log("No MASSO IP specified")
            return False

        self._connect_time_fields = self._time_fields()

        for port in range(LOCAL_PORT_START, LOCAL_PORT_END + 1):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("", port))
                s.settimeout(0.25)
                self.socket = s
                self.local_port = port
                break
            except OSError:
                continue

        if not self.socket:
            self.log(f"Could not bind UDP port {LOCAL_PORT_START}-{LOCAL_PORT_END}")
            return False

        try:
            self.tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.tx_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Do not bind TX. Let the OS choose an ephemeral source port, matching MASSO Link behavior.
            self.tx_socket.connect((self.host, CONTROLLER_PORT))
            self.controller_address = self.tx_socket.getpeername()[0]
            self.tx_socket.settimeout(0.25)
            self.tx_local_port = self.tx_socket.getsockname()[1]
        except Exception as exc:
            self.log(f"Could not create TX socket: {exc}")
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None
            return False

        self.listening = True
        self.connected = True
        self._seen_first_status = False
        self.status = MassoStatus(connected=True)
        self.post("status", self.status)

        self._tx_generation += 1
        tx_generation = self._tx_generation

        self.listen_thread = threading.Thread(
            target=self._listen_loop, args=(self.socket, self.controller_address), daemon=True
        )
        self.tx_listen_thread = threading.Thread(
            target=self._tx_listen_loop,
            args=(self.tx_socket, tx_generation),
            daemon=True,
        )
        self.keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self.listen_thread.start()
        self.tx_listen_thread.start()
        self.keepalive_thread.start()

        self.log(
            f"RX UDP {self.local_port}; TX UDP {self.tx_local_port}; "
            f"connecting to MASSO {self.host}:{CONTROLLER_PORT}"
        )
        self._send_handshake()
        return True

    def stop(self) -> None:
        self.connected = False
        self.listening = False
        self.controller_address = None
        self._tx_generation += 1
        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
        if self.tx_socket:
            try:
                self.tx_socket.close()
            except OSError:
                pass
        self.socket = None
        self.tx_socket = None
        self.tx_local_port = None
        self.status.connected = False
        self.post("status", self.status)

    def _send(self, packet: bytes) -> None:
        with self._tx_lock:
            if not self.tx_socket or not self.host:
                raise RuntimeError("TX socket not started")
            # TX socket is connected to the MASSO controller and uses an ephemeral source port.
            self.tx_socket.send(packet)

    def _time_fields(self) -> tuple[int, int, int, int, int, int]:
        """Return MASSO Link-style local time fields.

        Captures show packet bytes ordered as:
          hour, minute, second, day, month, year_offset

        Example from Touch capture on 2026-06-04 around 11:01:21:
          keepalive payload: 03 00 01 0b 01 15 04 06
          config payload:    03 00 03 0b 01 15 04 06 1a 00 00 00
        """
        now = datetime.now()
        return now.hour, now.minute, now.second, now.day, now.month, now.year - 2000

    def _connection_time_fields(self) -> tuple[int, int, int, int, int, int]:
        """Return the time snapshot captured when this connection started."""
        if self._connect_time_fields is None:
            self._connect_time_fields = self._time_fields()
        return self._connect_time_fields

    def _build_discovery_payload(self) -> bytes:
        # MASSO Link captures have shown several values in the final byte.
        # Using the connection-time month remains compatible with our tested
        # Touch/G3 controllers, but it should not be treated as a strict field.
        _hour, _minute, _second, _day, month, _year = self._connection_time_fields()
        return bytes([0x03, 0x00, 0x02, 0xF8, 0x2A, 0x00, 0x00, month & 0xFF])

    def _build_config_payload(self) -> bytes:
        hour, minute, second, day, month, year = self._connection_time_fields()
        payload = bytearray([0x03, 0x00, 0x03, hour, minute, second, day, month])
        payload.extend(year.to_bytes(4, "little", signed=False))
        return bytes(payload)

    def _build_keepalive_payload(self) -> bytes:
        # MASSO Link reuses the connection-time snapshot here; these bytes are
        # not a live clock.
        hour, minute, second, day, month, _year = self._connection_time_fields()
        return bytes([0x03, 0x00, 0x01, hour, minute, second, day, month])

    def _send_handshake(self) -> None:
        try:
            self._send(with_crc(self._build_discovery_payload()))
            time.sleep(0.15)
            self._send(with_crc(self._build_config_payload()))
            self.log("Handshake sent with local clock time")
        except Exception as exc:
            self.log(f"Handshake error: {exc}")

    def _keepalive_loop(self) -> None:
        while self.connected:
            try:
                self._send(with_crc(self._build_keepalive_payload()))
            except Exception as exc:
                self.log(f"Keepalive error: {exc}")
                break
            time.sleep(1.0)

    def _listen_loop(self, sock: Optional[socket.socket] = None, controller_address: Optional[str] = None) -> None:
        # Capture this connection's socket and resolved IPv4 peer. An old listener
        # must never read the replacement socket after disconnect/reconnect.
        sock = sock if sock is not None else self.socket
        controller_address = controller_address or self.controller_address
        while self.listening and sock is not None and sock is self.socket:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                self.log(f"Receive error: {exc}")
                continue

            if not self.listening or sock is not self.socket:
                break
            if not controller_address or addr[0] != controller_address:
                continue

            if len(data) == 270:
                self._handle_status(data)
            elif len(data) == 10:
                self._handle_small_packet(data)
            elif len(data) == 38 and data[4] == 0x08:
                self._handle_tool_packet(data)


    def _tx_listen_loop(self, sock: Optional[socket.socket] = None, generation: Optional[int] = None) -> None:
        """Listen for replies sent back to the TX ephemeral port.

        MASSO Link captures show some upload ACKs can be sent to the ephemeral
        sender port as well as UDP 11000. Listening here makes us tolerant of
        either behavior.

        A generation value is used because V1.7.10 can refresh the TX socket
        before each upload. This prevents old listener threads from racing the
        new listener on the replacement socket.
        """
        sock = sock or self.tx_socket
        generation = self._tx_generation if generation is None else generation

        while self.listening and sock and generation == self._tx_generation:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                self.log(f"TX receive error: {exc}")
                continue

            if not self.listening or generation != self._tx_generation:
                break
            if len(data) == 10:
                self._handle_small_packet(data, source="TX")
            elif len(data) == 270:
                self._handle_status(data)
            elif len(data) == 38 and data[4] == 0x08:
                self._handle_tool_packet(data)

    def _handle_small_packet(self, data: bytes, source: str = "RX") -> None:
        pkt_type = data[4]
        if pkt_type in (0x0A, 0x0B):
            self._last_ack = data
            self._ack_event.set()
            return
        if pkt_type == 0x03:
            serial = int.from_bytes(data[5:7], "little")
            self.controller_serial = serial
            self.post("controller_serial", serial)
            self.log(f"Configuration response ({source}): controller serial {serial}")

    def _build_tool_request_payload(self, tool_index: int) -> bytes:
        """Build MASSO Link-style tool data request for one tool slot.

        Captures show the one-byte tool index followed by the connection-time
        fields minute, second, day, month. MASSO Link requests slots 1-118.
        """
        _hour, minute, second, day, month, _year = self._connection_time_fields()
        return bytes([
            0x03, 0x00, 0x08, tool_index & 0xFF,
            minute & 0xFF, second & 0xFF, day & 0xFF, month & 0xFF,
        ])

    def _send_tool_request(self, tool_index: int) -> None:
        if not self.socket or not self.host:
            raise RuntimeError("RX/status socket not started")
        packet = with_crc(self._build_tool_request_payload(tool_index))
        # Tool requests are sent from the bound RX/status socket so responses
        # come back to UDP 11000-11050, matching MASSO Link captures.
        self.socket.sendto(packet, (self.host, CONTROLLER_PORT))

    def get_tools_data_async(self) -> None:
        t = threading.Thread(target=self._get_tools_data_worker, daemon=True)
        t.start()

    def _get_tools_data_worker(self) -> None:
        if not self.connected or not self.socket or not self.host:
            self.log("Get Tools Data blocked: not connected")
            self.post("tools_scan_complete", {"ok": False, "reason": "Not connected"})
            return
        self.log(f"Get Tools Data: requesting tool slots {TOOL_INDEX_MIN}-{TOOL_INDEX_MAX}")
        try:
            for tool_index in range(TOOL_INDEX_MIN, TOOL_INDEX_MAX + 1):
                self._send_tool_request(tool_index)
                time.sleep(0.015)
            # Give the listener thread time to receive the final responses before
            # enabling Generate Text File.
            time.sleep(1.0)
            self.post("tools_scan_complete", {"ok": True})
        except Exception as exc:
            self.log(f"Get Tools Data error: {exc}")
            self.post("tools_scan_complete", {"ok": False, "reason": str(exc)})

    def _handle_tool_packet(self, data: bytes) -> None:
        tool_index = data[5]
        raw_name = data[6:].split(b"\x00", 1)[0]
        name = raw_name.decode("ascii", errors="ignore").strip()
        self.post("tool_data", {"tool_index": tool_index, "tool_name": name})

    def _handle_status(self, data: bytes) -> None:
        now = time.monotonic()
        old = self.status
        st = MassoStatus(connected=True)
        st.raw_len = len(data)
        st.last_packet_time = now
        st.progress = data[5]
        st.run_flag = data[6]
        st.fault_code = data[7]
        st.job_count = int.from_bytes(data[8:12], "little")
        st.prompt_state = data[12]
        st.elapsed_seconds = int.from_bytes(data[13:17], "little")
        filename_bytes = data[17:80]
        st.filename = filename_bytes.split(b"\x00", 1)[0].decode("ascii", errors="ignore")

        if not self._seen_first_status:
            self._seen_first_status = True
            self.log(
                f"Status received: {st.state_text()} "
                f"progress={st.progress}% elapsed={format_elapsed_seconds(st.elapsed_seconds)} job={st.job_count}"
            )

        # Stopped debounce tracking
        if st.run_flag == 0x00 and st.fault_code == 0xFF:
            if old.stopped_since is not None and old.run_flag == 0x00 and old.fault_code == 0xFF:
                st.stopped_since = old.stopped_since
            else:
                st.stopped_since = now
        else:
            st.stopped_since = None

        # Feed hold is not currently decoded.
        # Byte 13 was previously treated as a line number, but production testing
        # showed bytes 13-16 are elapsed run time in seconds. Do not infer feed hold
        # from this field.
        st.feed_hold_active = False

        self.status = st
        self.last_status_raw = data
        self.post("status", st)

    def _reset_tx_socket_for_upload(self) -> bool:
        """Refresh the TX/upload socket before a file send.

        Home testing with V1.7.9 showed long-name uploads could succeed on the
        first send after connect, then fail on the second send until the app was
        disconnected/reconnected. Recreating only the TX socket gives the upload
        path a fresh ephemeral UDP source port/session while keeping the status
        RX socket alive.
        """
        if not self.controller_address:
            self.log("Cannot reset TX socket: no connected MASSO address set")
            return False

        with self._tx_lock:
            old_sock = self.tx_socket
            self._tx_generation += 1
            generation = self._tx_generation

            try:
                new_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                new_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # Keep uploads on the same controller whose status we trust,
                # even if the configured hostname resolves differently later.
                new_sock.connect((self.controller_address, CONTROLLER_PORT))
                new_sock.settimeout(0.25)
                self.tx_socket = new_sock
                self.tx_local_port = new_sock.getsockname()[1]
            except Exception as exc:
                self.log(f"Could not reset TX socket for upload: {exc}")
                self.tx_socket = old_sock
                return False

            if old_sock:
                try:
                    old_sock.close()
                except OSError:
                    pass

        self.tx_listen_thread = threading.Thread(
            target=self._tx_listen_loop,
            args=(self.tx_socket, generation),
            daemon=True,
        )
        self.tx_listen_thread.start()
        self.log(f"TX socket refreshed for upload: UDP {self.tx_local_port}")

        # Re-send the short connection sequence from the fresh TX port. This
        # mimics the manual disconnect/reconnect workaround without dropping the
        # UI/status RX socket.
        try:
            self._send_handshake()
            time.sleep(0.15)
        except Exception as exc:
            self.log(f"TX refresh handshake warning: {exc}")

        return True


    # -----------------------------
    # Upload
    # -----------------------------

    def upload_file_async(self, local_path: str, remote_folder: str, upload_id: Optional[int] = None) -> None:
        if self.upload_in_progress:
            self.log("Upload already in progress")
            return
        t = threading.Thread(target=self._upload_file_worker, args=(local_path, remote_folder, upload_id), daemon=True)
        t.start()

    def _build_start_upload_packets(self, filesize: int, remote_folder: str, filename: str):
        """Build MASSO Link-style start-upload packet candidates.

        V1.7.9 finding: MASSO Link start-upload packets appear to pad the
        payload AFTER the 2-byte CRC to a 4-byte boundary. The bytes after the
        filename NUL in captures look like garbage/text residue, but the packet
        payload length is consistently divisible by 4.

        Keep the proven fixed-50 packet for names that fit. For longer names,
        build a compact variable-length packet and zero-pad only to the next
        4-byte payload boundary.
        """
        folder = normalize_masso_folder(remote_folder)
        folder_b = folder.encode("ascii", errors="replace")
        name_b = filename.encode("ascii", errors="replace")

        if len(folder_b) > 255:
            raise ValueError(f"Remote folder is too long for 1-byte folder length field ({len(folder_b)} bytes).")

        base = bytearray()
        base.extend(b"\x03\x00")
        base.append(0x0A)
        base.extend(filesize.to_bytes(4, "little"))
        base.extend(b"\x00\x00")
        base.append(len(folder_b))  # folder length, not including NUL
        base.extend(folder_b)
        base.append(0x00)
        base.extend(name_b)
        base.append(0x00)

        candidates = []

        # Candidate 1: proven fixed-50 packet for normal names.
        # Payload length after CRC is 48 bytes, which is also 4-byte aligned.
        if len(base) <= 48:
            p = bytearray(base)
            if len(p) <= 45:
                p.extend(b"826"[: 48 - len(p)])
            while len(p) < 48:
                p.append(0x00)
            candidates.append(("fixed-50", with_crc(bytes(p))))
            return candidates

        # Long-name experimental path. Do not try multiple bad variants first;
        # the controller may enter/hold a failed receive state after a bad start.
        if len(base) > 180:
            raise ValueError(
                f"Remote folder/name too long for experimental packet ({len(base)} payload bytes, max 180). "
                "Use a shorter target folder and filename for now."
            )

        p = bytearray(base)
        pad_len = (-len(p)) % 4
        if pad_len:
            p.extend(b"\x00" * pad_len)
        candidates.append((f"compact-4byte-align-pad{pad_len}", with_crc(bytes(p))))

        return candidates

    def _build_data_packet(self, chunk_index: int, chunk: bytes, *, pad_to_full_chunk: bool = False, final_chunk: bool = False) -> bytes:
        """Build a file data packet.

        Full packets carry 1422 data bytes and a 3-byte trailer. Compact final
        packets keep the real data length and pad so the payload after the
        two-byte CRC is a multiple of four bytes.
        """
        wire_chunk = chunk
        length_field = len(chunk)
        if pad_to_full_chunk and len(chunk) < CHUNK_SIZE:
            wire_chunk = chunk + (b"\x00" * (CHUNK_SIZE - len(chunk)))
            length_field = CHUNK_SIZE

        payload = bytearray()
        payload.extend(b"\x03\x00")
        payload.append(0x0B)
        payload.extend(chunk_index.to_bytes(4, "little"))
        payload.extend(length_field.to_bytes(4, "little"))
        payload.extend(wire_chunk)
        if final_chunk and len(chunk) < CHUNK_SIZE and not pad_to_full_chunk:
            # The payload after the 2-byte CRC has an 11-byte protocol header
            # (03 00 + type + index + length). MASSO Link aligns that payload
            # to a 4-byte boundary. If already aligned, it still appends 4 pad
            # bytes rather than zero.
            final_pad_len = final_chunk_trailer_len(len(chunk))
            payload.extend(b"\x00" * final_pad_len)
        else:
            payload.extend(b"\x00\x00\x00")
        return with_crc(bytes(payload))


    def _build_data_packet_full_wire_actual_length(self, chunk_index: int, chunk: bytes) -> bytes:
        """Build a compatibility data packet for small/single-chunk uploads.

        Some captures from the work MASSO show start-upload accepted, but the
        controller ignores a short first/final packet. This variant keeps the
        length field equal to the real file bytes, but pads the wire data area
        out to CHUNK_SIZE so the UDP packet length matches a normal full chunk.
        If MASSO honors the length field, the saved file should remain the
        correct size; the extra bytes are just transport padding.
        """
        wire_chunk = chunk
        if len(wire_chunk) < CHUNK_SIZE:
            wire_chunk = wire_chunk + (b"\x00" * (CHUNK_SIZE - len(wire_chunk)))

        payload = bytearray()
        payload.extend(b"\x03\x00")
        payload.append(0x0B)
        payload.extend(chunk_index.to_bytes(4, "little"))
        payload.extend(len(chunk).to_bytes(4, "little"))
        payload.extend(wire_chunk)
        payload.extend(b"\x00\x00\x00")
        return with_crc(bytes(payload))

    def _wait_for_ack(self, expected_type: int, timeout: float = 2.0) -> Optional[bytes]:
        self._ack_event.clear()
        self._last_ack = None
        if self._ack_event.wait(timeout=timeout):
            ack = self._last_ack
            if ack and len(ack) == 10 and ack[4] == expected_type:
                return ack
        return None

    def _send_with_ack(self, packet: bytes, expected_type: int, timeout: float = 2.0) -> Optional[bytes]:
        self._ack_event.clear()
        self._last_ack = None
        self._send(packet)
        if self._ack_event.wait(timeout=timeout):
            ack = self._last_ack
            if ack and len(ack) == 10 and ack[4] == expected_type:
                return ack
        return None

    def _upload_file_worker(self, local_path: str, remote_folder: str, upload_id: Optional[int] = None) -> None:
        self.upload_in_progress = True
        self.post("upload_state", True)

        def fail(reason: str) -> None:
            self.log(reason)
            self.post("upload_failed", {
                "upload_id": upload_id,
                "path": local_path,
                "folder": remote_folder,
                "reason": reason,
                "timestamp": time.time(),
            })

        try:
            allowed, reason = self.status.upload_allowed()
            if not allowed:
                fail(f"Upload blocked: {reason}")
                return

            path = Path(local_path)
            if not path.exists() or not path.is_file():
                fail(f"File not found: {local_path}")
                return
            if path.stat().st_size <= 0:
                fail("Upload blocked: file is empty")
                return

            filename = path.name
            filesize = path.stat().st_size
            remote_folder = normalize_masso_folder(remote_folder)

            self.log(f"Starting upload: {filename} ({filesize} bytes) -> {remote_folder}")

            # V1.7.10: refresh TX/upload socket before every file. This matches
            # the observed workaround where reconnecting made the first upload
            # reliable again.
            if not self._reset_tx_socket_for_upload():
                fail("Upload failed: could not refresh TX socket")
                return

            start_packets = self._build_start_upload_packets(filesize, remote_folder, filename)

            start_ack = None
            start_variant = None
            for variant_name, start_packet in start_packets:
                self.log(f"Trying start-upload packet: {variant_name} len={len(start_packet)}")
                for attempt in range(1, 4):
                    start_ack = self._send_with_ack(start_packet, 0x0A, timeout=2.0)
                    if start_ack is None:
                        self.log(f"Start upload {variant_name} attempt {attempt}: no ACK")
                        continue
                    # Start-upload ACKs vary by controller/firmware. Byte 5 is
                    # the stable accepted/rejected discriminator:
                    #   00 = start accepted
                    #   F7 = start rejected/failure
                    # Later captures showed bytes 6 onward can carry the previous
                    # upload's final data counter, so they must not be required to zero.
                    code = start_ack[5:7]
                    if start_ack[5] == 0x00:
                        start_variant = variant_name
                        self.log(f"Start upload accepted via {variant_name}; code={code.hex(' ')} ACK={start_ack.hex(' ')}")
                        break
                    self.log(f"Start upload {variant_name} rejected: code {code.hex(' ')} ACK={start_ack.hex(' ')}")
                    start_ack = None
                    time.sleep(0.4)
                if start_ack is not None:
                    break

            if start_ack is None:
                fail("Upload failed: MASSO did not accept start upload")
                return

            total_chunks = (filesize + CHUNK_SIZE - 1) // CHUNK_SIZE
            with path.open("rb") as f:
                for chunk_index in range(total_chunks):
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    # V1.6/V1.7.4: keep actual final chunk length, but mark final
                    # short chunks so they get the MASSO Link-style trailer.
                    pad_this_chunk = False
                    is_final_chunk = (chunk_index == total_chunks - 1)
                    expected_next = chunk_index + 1

                    packet_variants = [(
                        "short-final" if is_final_chunk and len(chunk) < CHUNK_SIZE else "normal",
                        self._build_data_packet(
                            chunk_index,
                            chunk,
                            pad_to_full_chunk=pad_this_chunk,
                            final_chunk=is_final_chunk,
                        ),
                    )]

                    # Work MASSO compatibility test: a 411-byte single-chunk file
                    # accepted start-upload but ignored the short final packet. Try a
                    # full-size wire packet with the real length field if the proven
                    # short packet gets no ACK.
                    if is_final_chunk and len(chunk) < CHUNK_SIZE:
                        packet_variants.append((
                            "full-wire-real-length",
                            self._build_data_packet_full_wire_actual_length(chunk_index, chunk),
                        ))

                    ok = False
                    for packet_variant_name, packet in packet_variants:
                        if chunk_index == 0:
                            self.log(
                                f"Sending first data packet variant {packet_variant_name} from TX UDP {self.tx_local_port}: "
                                f"len={len(packet)} real_chunk_len={len(chunk)} "
                                f"final={is_final_chunk} head={packet[:16].hex(' ')}"
                            )

                        # MASSO Link resends chunks quickly until the ACK advances.
                        max_attempts = 10 if packet_variant_name != "full-wire-real-length" else 12
                        if is_final_chunk and packet_variant_name == "short-final":
                            max_attempts = 6
                        ack_timeout = 0.20 if is_final_chunk else 0.30
                        for attempt in range(1, max_attempts + 1):
                            ack = self._send_with_ack(packet, 0x0B, timeout=ack_timeout)
                            if ack is None:
                                if attempt in (1, max_attempts) or attempt % 5 == 0:
                                    self.log(f"Chunk {expected_next}/{total_chunks} {packet_variant_name}: no ACK, retry {attempt}")
                                continue

                            # Data ACK bytes 6:8 contain the next expected chunk
                            # index as a little-endian 16-bit value. Earlier code read
                            # bytes 5:7 as big-endian, which accidentally looked like an
                            # 8-bit rollover at chunk 256.
                            ack_next = decode_data_ack_next(ack)
                            if ack_next == expected_next:
                                if expected_next == 1:
                                    self.log(f"First chunk ACK received via {packet_variant_name}: {ack.hex(' ')}")
                                ok = True
                                break

                            # MASSO can return a stale/previous ACK first. Keep resending
                            # the same chunk until the ACK advances to the expected value.
                            if attempt in (1, max_attempts) or attempt % 5 == 0:
                                self.log(
                                    f"Chunk {expected_next}/{total_chunks} {packet_variant_name}: "
                                    f"stale/unexpected ACK next={ack_next} expected={expected_next}, retry {attempt}"
                                )
                        if ok:
                            break

                    if not ok:
                        fail(f"Upload failed at chunk {expected_next}/{total_chunks}")
                        return

                    progress = int(expected_next * 100 / total_chunks)
                    self.post("upload_progress", progress)
                    if expected_next == 1 or expected_next == total_chunks or expected_next % 5 == 0:
                        self.log(f"Sent chunk {expected_next}/{total_chunks} ({progress}%)")

            self.log(f"Upload complete: {filename}")
            self.post("upload_progress", 100)
            self.post("upload_complete", {
                "upload_id": upload_id,
                "filename": filename,
                "folder": remote_folder,
                "path": str(path),
                "size": filesize,
                "timestamp": time.time(),
            })
        except Exception as exc:
            fail(f"Upload error: {exc}")
        finally:
            self.upload_in_progress = False
            self.post("upload_state", False)


def format_elapsed_seconds(seconds: int) -> str:
    """Format elapsed seconds from MASSO status bytes 13-16."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def open_file_with_default_app(path: Path) -> tuple[bool, str]:
    """Open a file using the OS default application with detailed diagnostics."""
    path = Path(path).resolve()
    attempts = []

    if not path.exists():
        return False, f"file does not exist: {path}"

    if sys.platform.startswith("win"):
        path_s = str(path)
        parent_s = str(path.parent)

        # 1) Native Windows default association, with usable return code.
        try:
            rc = ctypes.windll.shell32.ShellExecuteW(
                None,
                "open",
                path_s,
                None,
                parent_s,
                1,  # SW_SHOWNORMAL
            )
            attempts.append(f"ShellExecuteW={int(rc)}")
            if int(rc) > 32:
                return True, "; ".join(attempts)
        except Exception as exc:
            attempts.append(f"ShellExecuteW exception={exc}")

        # 2) Python's normal wrapper.
        try:
            os.startfile(path_s)  # type: ignore[attr-defined]
            attempts.append("os.startfile=called")
            return True, "; ".join(attempts)
        except Exception as exc:
            attempts.append(f"os.startfile exception={exc}")

        # 3) cmd START with shell.
        try:
            proc = subprocess.Popen(f'start "" "{path_s}"', shell=True, cwd=parent_s)
            attempts.append(f"cmd start pid={proc.pid}")
            return True, "; ".join(attempts)
        except Exception as exc:
            attempts.append(f"cmd start exception={exc}")

        # 4) Force Notepad as a last resort.
        try:
            proc = subprocess.Popen(["notepad.exe", path_s], cwd=parent_s)
            attempts.append(f"notepad.exe pid={proc.pid}")
            return True, "; ".join(attempts)
        except Exception as exc:
            attempts.append(f"notepad.exe exception={exc}")

        return False, "; ".join(attempts)

    if sys.platform == "darwin":
        proc = subprocess.Popen(["open", str(path)])
        return True, f"open pid={proc.pid}"

    try:
        proc = subprocess.Popen(["xdg-open", str(path)])
        return True, f"xdg-open pid={proc.pid}"
    except Exception as exc:
        # webbrowser is a harmless final fallback on non-Windows systems.
        try:
            ok = webbrowser.open(path.as_uri())
            return ok, f"xdg-open exception={exc}; webbrowser.open={ok}"
        except Exception as exc2:
            return False, f"xdg-open exception={exc}; webbrowser exception={exc2}"


# -----------------------------
# Config
# -----------------------------

def load_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {
        "profiles": [
            {"name": "MASSO", "ip": "192.168.137.245"},
        ],
        "last_profile": "MASSO",
        "last_folder": "\\",
        "last_local_dir": str(Path.home()),
        "auto_clear_queue": False,
    }


def save_config(cfg: Dict[str, Any]) -> None:
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


