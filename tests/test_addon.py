"""
Tests for the Blender MCP add-on.
=================================
Note: The addon.py architecture changed in v4.0.0 to support provider-agnostic
LLM backends and runtime provider switching. These tests reflect that.
"""

import sys
import os
import json
import select
import time
import unittest
from unittest.mock import patch, MagicMock

# Mock bpy and its submodules before importing the addon module
bpy_mock = MagicMock()
bpy_mock.props = MagicMock()
sys.modules['bpy'] = bpy_mock
sys.modules['bpy.props'] = bpy_mock.props

# Add the root directory to the path to allow imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Now we can import the addon
import addon


class TestAddonHandlers(unittest.TestCase):
    """Test the command handler functions."""

    def setUp(self):
        """Set up mock bpy context for tests."""
        # Mock scene
        self.mock_scene = MagicMock()
        self.mock_scene.name = "Scene"
        self.mock_scene.frame_current = 1
        self.mock_scene.frame_start = 1
        self.mock_scene.frame_end = 250
        self.mock_scene.render.engine = "CYCLES"
        self.mock_scene.render.resolution_x = 1920
        self.mock_scene.render.resolution_y = 1080
        self.mock_scene.camera = MagicMock()
        self.mock_scene.camera.name = "Camera"
        self.mock_scene.objects = []

        addon.bpy.context.scene = self.mock_scene
        mock_objects = MagicMock()
        mock_objects.get.return_value = None  # default: object not found
        addon.bpy.data.objects = mock_objects

    def test_handle_get_scene_info_empty_scene(self):
        """Test scene info handler with empty scene."""
        result = addon.handle_get_scene_info({})
        self.assertEqual(result["scene_name"], "Scene")
        self.assertEqual(result["object_count"], 0)
        self.assertEqual(result["objects"], [])

    def test_handle_get_object_info_not_found(self):
        """Test object info handler with non-existent object."""
        with self.assertRaises(ValueError) as context:
            addon.handle_get_object_info({"object_name": "NonExistent"})
        self.assertIn("not found", str(context.exception))

    def test_handle_create_object_invalid_type(self):
        """Test create object with invalid primitive type."""
        with self.assertRaises(ValueError) as context:
            addon.handle_create_object({"type": "INVALID"})
        self.assertIn("Unknown primitive type", str(context.exception))

    def test_handle_delete_object_not_found(self):
        """Test delete object with non-existent object."""
        with self.assertRaises(ValueError) as context:
            addon.handle_delete_object({"name": "NonExistent"})
        self.assertIn("not found", str(context.exception))

    def test_handle_modify_object_not_found(self):
        """Test modify object with non-existent object."""
        with self.assertRaises(ValueError) as context:
            addon.handle_modify_object({"name": "NonExistent"})
        self.assertIn("not found", str(context.exception))

    def test_handle_set_material_object_not_found(self):
        """Test set material with non-existent object."""
        with self.assertRaises(ValueError) as context:
            addon.handle_set_material({
                "object_name": "NonExistent",
                "material_name": "TestMat"
            })
        self.assertIn("not found", str(context.exception))

    def test_handle_set_material_unsupported_type(self):
        """Test set material on unsupported object type."""
        mock_obj = MagicMock()
        mock_obj.type = "EMPTY"
        addon.bpy.data.objects.get = MagicMock(return_value=mock_obj)

        with self.assertRaises(ValueError) as context:
            addon.handle_set_material({
                "object_name": "Empty",
                "material_name": "TestMat"
            })
        self.assertIn("does not support materials", str(context.exception))

    def test_vec3_from_list_helper(self):
        """Test the vec3 from list helper function."""
        self.assertEqual(addon._vec3_from_list([1, 2, 3]), (1.0, 2.0, 3.0))
        self.assertEqual(addon._vec3_from_list([1, 2]), (0.0, 0.0, 0.0))
        self.assertEqual(addon._vec3_from_list(None), (0.0, 0.0, 0.0))
        self.assertEqual(addon._vec3_from_list([1, 2, 3, 4, 5]), (1.0, 2.0, 3.0))

    def test_ok_response_helper(self):
        """Test the ok response formatter."""
        result = addon._ok({"test": "data"})
        self.assertEqual(
            result,
            b'{"status": "ok", "result": {"test": "data"}}\n'
        )

    def test_err_response_helper(self):
        """Test the error response formatter."""
        result = addon._err("Something went wrong")
        self.assertEqual(
            result,
            b'{"status": "error", "message": "Something went wrong"}\n'
        )

    def test_handlers_dict_contains_all_commands(self):
        """Test that all expected handlers are registered."""
        expected_handlers = [
            "get_scene_info",
            "get_object_info",
            "create_object",
            "modify_object",
            "delete_object",
            "set_material",
            "render_image",
            "execute_blender_code",
            "get_polyhaven_categories",
            "search_polyhaven_assets",
            "download_polyhaven_asset",
            "set_texture",
            "set_llm_provider",
            "get_llm_provider",
            "get_ollama_models",
        ]
        for handler in expected_handlers:
            self.assertIn(handler, addon.HANDLERS)
            self.assertTrue(callable(addon.HANDLERS[handler]))


class TestAddonDispatch(unittest.TestCase):
    """Test the command dispatch function."""

    def test_dispatch_unknown_command(self):
        """Test dispatch with unknown command returns error."""
        result = addon._dispatch("unknown_command", {})
        self.assertIn(b"Unknown command", result)
        self.assertIn(b"error", result)

    def test_dispatch_valid_command(self):
        """Test dispatch with valid command returns ok."""
        # Mock a simple handler
        original_handler = addon.HANDLERS.get("get_scene_info")
        mock_handler = MagicMock(return_value={"test": "result"})
        addon.HANDLERS["get_scene_info"] = mock_handler

        try:
            result = addon._dispatch("get_scene_info", {})
            self.assertIn(b"ok", result)
            mock_handler.assert_called_once_with({})
        finally:
            if original_handler:
                addon.HANDLERS["get_scene_info"] = original_handler

    def test_dispatch_handler_raises_exception(self):
        """Test dispatch when handler raises an exception."""
        original_handler = addon.HANDLERS.get("get_scene_info")

        def failing_handler(params):
            raise ValueError("Test error")

        addon.HANDLERS["get_scene_info"] = failing_handler

        try:
            result = addon._dispatch("get_scene_info", {})
            self.assertIn(b"error", result)
            self.assertIn(b"ValueError", result)
            self.assertIn(b"Test error", result)
        finally:
            if original_handler:
                addon.HANDLERS["get_scene_info"] = original_handler


class TestAddonTimerServer(unittest.TestCase):
    """Exercise the nonblocking socket state machine without Blender threads."""

    def setUp(self):
        self.timer_api = addon.bpy.app.timers
        self.registered = patch.object(
            self.timer_api, "is_registered", return_value=False
        )
        self.registered.start()
        probe = addon.socket.socket(addon.socket.AF_INET, addon.socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        self.host, self.port = probe.getsockname()
        probe.close()
        addon._start_server(self.host, self.port)

    def tearDown(self):
        addon._stop_server()
        self.registered.stop()

    def _connect(self):
        client = addon.socket.create_connection((self.host, self.port), timeout=1)
        client.settimeout(1)
        return client

    def _pump_and_read_response(self, client):
        deadline = time.monotonic() + 1
        chunks = []
        while time.monotonic() < deadline:
            addon._process_server_requests()
            ready, _, _ = select.select([client], [], [], 0.01)
            if ready:
                chunk = client.recv(4096)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        self.fail("timer server did not return a response")

    def test_timer_handles_fragmented_request_and_dispatches_once(self):
        handler = MagicMock(return_value={"ok": True})
        original = addon.HANDLERS.get("timer_test")
        addon.HANDLERS["timer_test"] = handler
        client = self._connect()
        try:
            client.sendall(b'{"type":"timer_test",')
            addon._process_server_requests()
            client.sendall(b'"params":{"value":1}}\n')
            response = json.loads(self._pump_and_read_response(client))
            self.assertEqual(response, {"status": "ok", "result": {"ok": True}})
            handler.assert_called_once_with({"value": 1})
        finally:
            client.close()
            if original is None:
                addon.HANDLERS.pop("timer_test", None)
            else:
                addon.HANDLERS["timer_test"] = original

    def test_timer_returns_invalid_json_error(self):
        client = self._connect()
        try:
            client.sendall(b"{invalid}\n")
            response = json.loads(self._pump_and_read_response(client))
            self.assertEqual(response["status"], "error")
            self.assertIn("Invalid JSON", response["message"])
        finally:
            client.close()

    def test_request_at_size_limit_is_accepted(self):
        line = b'{"type":"size_test"}'
        handler = MagicMock(return_value={"ok": True})
        original = addon.HANDLERS.get("size_test")
        addon.HANDLERS["size_test"] = handler
        client = self._connect()
        try:
            with patch.object(addon, "MAX_REQUEST_BYTES", len(line)):
                client.sendall(line + b"\n")
                response = json.loads(self._pump_and_read_response(client))
            self.assertEqual(response, {"status": "ok", "result": {"ok": True}})
            handler.assert_called_once_with({})
        finally:
            client.close()
            if original is None:
                addon.HANDLERS.pop("size_test", None)
            else:
                addon.HANDLERS["size_test"] = original

    def test_request_over_size_limit_returns_error(self):
        client = self._connect()
        try:
            with patch.object(addon, "MAX_REQUEST_BYTES", 8):
                client.sendall(b"123456789\n")
                response = json.loads(self._pump_and_read_response(client))
            self.assertEqual(response["status"], "error")
            self.assertIn("maximum size", response["message"])
        finally:
            client.close()

    def test_accepted_socket_setup_error_does_not_stop_timer(self):
        class AcceptedSocket:
            closed = False

            def setblocking(self, _enabled):
                raise OSError("socket closed during accept")

            def close(self):
                self.closed = True

        class Listener:
            def __init__(self, accepted):
                self.accepted = accepted

            def accept(self):
                if self.accepted is not None:
                    accepted, self.accepted = self.accepted, None
                    return accepted, ("127.0.0.1", 12345)
                raise BlockingIOError

        old_socket = addon._server_socket
        old_running = addon._server_running
        old_clients = addon._server_clients
        accepted = AcceptedSocket()
        try:
            addon._server_socket = Listener(accepted)
            addon._server_running = True
            addon._server_clients = {}
            self.assertEqual(
                addon._process_server_requests(), addon.SERVER_POLL_INTERVAL
            )
            self.assertTrue(accepted.closed)
            self.assertEqual(addon._server_clients, {})
        finally:
            addon._server_socket = old_socket
            addon._server_running = old_running
            addon._server_clients = old_clients

    def test_stop_closes_listener_and_connected_clients(self):
        client = self._connect()
        addon._process_server_requests()
        self.assertTrue(addon._server_clients)
        addon._stop_server()
        self.assertFalse(addon._server_running)
        self.assertFalse(addon._server_clients)
        self.assertIsNone(addon._server_socket)
        self.assertEqual(client.recv(1), b"")
        client.close()

    def test_timer_resumes_partial_response_send(self):
        class PartialSocket:
            def __init__(self):
                self.output = bytearray()
                self.closed = False

            def send(self, data):
                chunk = bytes(data[:3])
                self.output.extend(chunk)
                return len(chunk)

            def close(self):
                self.closed = True

        conn = PartialSocket()
        addon._server_clients[conn] = {
            "buffer": bytearray(),
            "response": b"abcdefgh\n",
            "sent": 0,
            "deadline": time.monotonic() + 1,
        }
        addon._process_server_requests()
        self.assertEqual(bytes(conn.output), b"abc")
        self.assertEqual(addon._server_clients[conn]["sent"], 3)
        addon._process_server_requests()
        addon._process_server_requests()
        self.assertEqual(bytes(conn.output), b"abcdefgh\n")
        self.assertTrue(conn.closed)
        self.assertNotIn(conn, addon._server_clients)

    def test_timer_closes_expired_partial_request(self):
        client = self._connect()
        client.sendall(b'{"type":')
        addon._process_server_requests()
        conn = next(iter(addon._server_clients))
        addon._server_clients[conn]["deadline"] = 0
        addon._process_server_requests()
        self.assertNotIn(conn, addon._server_clients)
        client.close()


class TestAddonPolyHaven(unittest.TestCase):
    """Test PolyHaven integration handlers."""

    @patch('addon.urllib.request.urlopen')
    def test_handle_get_polyhaven_categories(self, mock_urlopen):
        """Test fetching PolyHaven categories."""
        mock_response = MagicMock()
        mock_response.read.return_value = b'["wood", "metal", "fabric"]'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = addon.handle_get_polyhaven_categories({"asset_type": "textures"})
        self.assertEqual(result, ["wood", "metal", "fabric"])

    @patch('addon.urllib.request.urlopen')
    def test_handle_search_polyhaven_assets(self, mock_urlopen):
        """Test searching PolyHaven assets."""
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"asset1": {}, "asset2": {}}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = addon.handle_search_polyhaven_assets({"asset_type": "textures"})
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["assets"]), 2)
