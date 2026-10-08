"""Connection identity tests use fake sockets; no network access is needed."""
import queue
import unittest
from unittest.mock import MagicMock, patch

from masso_core import MassoClient
from masso_workflow import UploadQueue


class ReceiveSocket:
    def __init__(self, packets):
        self.packets = iter(packets)

    def recvfrom(self, size):
        try:
            return next(self.packets)
        except StopIteration:
            raise OSError("End of test packets")


class ConnectionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.client = MassoClient(self.events)
        self.client.host = "workshop.local"
        self.client.controller_address = "192.168.1.120"
        self.client.listening = True

    def status_packet(self):
        data = bytearray(270)
        data[7] = 0xff
        data[12] = 1
        return bytes(data)

    def test_foreign_status_ack_serial_and_tools_are_ignored(self):
        ack = bytearray(10)
        ack[4] = 0x0a
        serial = bytearray(10)
        serial[4] = 3
        serial[5] = 42
        tool = bytearray(38)
        tool[4] = 8
        tool[5] = 1
        tool[6:10] = b"Mill"
        packets = [self.status_packet(), bytes(ack), bytes(serial), bytes(tool)]
        self.client.socket = ReceiveSocket([(data, ("192.168.1.121", 65535)) for data in packets])
        self.client._listen_loop()
        self.assertFalse(self.client.status.connected)
        self.assertIsNone(self.client._last_ack)
        self.assertIsNone(self.client.controller_serial)
        self.assertTrue(self.events.empty())
        self.assertFalse(UploadQueue(self.client).allowed()[0])

    def test_matching_resolved_address_can_enable_readiness(self):
        # The configured hostname is not compared directly to the numeric sender.
        self.client.socket = ReceiveSocket([(self.status_packet(), ("192.168.1.120", 65535))] * 2)
        with patch("masso_core.time.monotonic", side_effect=[100.0, 102.0, 102.0, 102.0]):
            self.client._listen_loop()
            self.assertEqual(UploadQueue(self.client).allowed(), (True, "Ready"))
        self.assertEqual(self.client.status.last_packet_time, 102.0)

    def test_unknown_peer_does_not_accept_status(self):
        self.client.controller_address = None
        self.client.socket = ReceiveSocket([(self.status_packet(), ("192.168.1.120", 65535))])
        self.client._listen_loop()
        self.assertFalse(self.client.status.connected)

    def test_old_rx_listener_cannot_apply_packet_after_reconnect(self):
        old_socket = MagicMock()
        replacement = MagicMock()
        def receive(size):
            self.client.socket = replacement
            return self.status_packet(), ("192.168.1.120", 65535)
        old_socket.recvfrom.side_effect = receive
        self.client.socket = old_socket
        self.client._listen_loop(old_socket, "192.168.1.120")
        self.assertFalse(self.client.status.connected)
        replacement.recvfrom.assert_not_called()

    def test_old_tx_listener_cannot_apply_packet_after_reconnect(self):
        old_socket = MagicMock()
        generation = self.client._tx_generation
        def receive(size):
            self.client._tx_generation += 1
            return self.status_packet()
        old_socket.recv.side_effect = receive
        self.client._tx_listen_loop(old_socket, generation)
        self.assertFalse(self.client.status.connected)

    def test_start_captures_resolved_peer_from_connected_socket(self):
        rx, tx = MagicMock(), MagicMock()
        tx.getpeername.return_value = ("192.168.1.120", 65535)
        tx.getsockname.return_value = ("192.168.1.10", 23456)
        self.client.listening = False
        with patch("masso_core.socket.socket", side_effect=[rx, tx]), \
             patch("masso_core.threading.Thread"), patch.object(self.client, "_send_handshake"):
            self.assertTrue(self.client.start("workshop.local"))
        self.assertEqual(self.client.controller_address, "192.168.1.120")
        tx.connect.assert_called_once_with(("workshop.local", 65535))
        self.client.stop()
        self.assertIsNone(self.client.controller_address)

    def test_upload_socket_refresh_keeps_the_original_resolved_address(self):
        old_socket, new_socket = MagicMock(), MagicMock()
        self.client.tx_socket = old_socket
        new_socket.getsockname.return_value = ("192.168.1.10", 23457)
        with patch("masso_core.socket.socket", return_value=new_socket), \
             patch("masso_core.threading.Thread"), patch.object(self.client, "_send_handshake"), \
             patch("masso_core.time.sleep"):
            self.assertTrue(self.client._reset_tx_socket_for_upload())
        new_socket.connect.assert_called_once_with(("192.168.1.120", 65535))
        old_socket.close.assert_called_once()
