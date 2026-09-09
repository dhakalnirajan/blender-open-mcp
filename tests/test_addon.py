"""
Tests for the Blender MCP add-on.
=================================
Note: The addon.py architecture changed in v4.0.0 to support provider-agnostic
LLM backends and runtime provider switching. These tests reflect that.
"""

import sys
import os
import threading
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


class TestMainThreadDispatch(unittest.TestCase):
    """bpy work must be marshalled to Blender's main thread (see _dispatch).

    Regression cover for the add-on running every handler on the per-connection
    worker thread, which gave bpy.ops a restricted context
    ("'Context' object has no attribute 'active_object'") and destabilised
    renders.
    """

    def setUp(self):
        addon._pump_registered = False
        addon._pump_verified = False
        while not addon._main_thread_jobs.empty():
            addon._main_thread_jobs.get_nowait()

    tearDown = setUp

    def _run_pump_until(self, stop_event):
        """Stand in for bpy.app.timers draining the queue on the main thread."""
        while not stop_event.is_set():
            addon._main_thread_pump()
            time.sleep(0.005)

    def test_runs_inline_when_no_pump(self):
        """Headless/mocked bpy has no timer pump: never block, run inline."""
        caller = threading.current_thread().ident
        seen = {}

        def job():
            seen["thread"] = threading.current_thread().ident
            return "done"

        result = []
        t = threading.Thread(target=lambda: result.append(addon._run_on_main_thread(job)))
        t.start()
        t.join(timeout=5.0)

        self.assertEqual(result, ["done"])
        self.assertNotEqual(seen["thread"], caller)  # ran on the worker itself

    def test_job_runs_on_pump_thread_not_caller(self):
        """With a live pump, the handler executes on the pump thread."""
        addon._pump_registered = True
        stop = threading.Event()
        pump = threading.Thread(target=self._run_pump_until, args=(stop,), daemon=True)
        pump.start()

        seen = {}

        def job():
            seen["thread"] = threading.current_thread().ident
            return 42

        out = []
        worker = threading.Thread(target=lambda: out.append(addon._run_on_main_thread(job)))
        worker.start()
        worker.join(timeout=5.0)
        stop.set()
        pump.join(timeout=2.0)

        self.assertEqual(out, [42])
        self.assertEqual(seen["thread"], pump.ident)

    def test_exception_propagates_to_caller(self):
        """Errors raised on the main thread surface in the requesting thread."""
        addon._pump_registered = True
        stop = threading.Event()
        pump = threading.Thread(target=self._run_pump_until, args=(stop,), daemon=True)
        pump.start()

        def boom():
            raise ValueError("kaboom")

        captured = []

        def worker():
            try:
                addon._run_on_main_thread(boom)
            except Exception as exc:  # noqa: BLE001
                captured.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5.0)
        stop.set()
        pump.join(timeout=2.0)

        self.assertEqual(len(captured), 1)
        self.assertIsInstance(captured[0], ValueError)
        self.assertIn("kaboom", str(captured[0]))

    def test_bpy_commands_are_marshalled_worker_safe_are_not(self):
        """create_object goes to the main thread; PolyHaven lookups do not."""
        addon._pump_registered = True
        stop = threading.Event()
        pump = threading.Thread(target=self._run_pump_until, args=(stop,), daemon=True)
        pump.start()

        threads = {}

        def spy(name):
            def handler(_params):
                threads[name] = threading.current_thread().ident
                return {"ok": name}
            return handler

        originals = {k: addon.HANDLERS[k] for k in ("create_object", "get_polyhaven_categories")}
        addon.HANDLERS["create_object"] = spy("create_object")
        addon.HANDLERS["get_polyhaven_categories"] = spy("polyhaven")
        try:
            def worker():
                addon._dispatch("create_object", {})
                addon._dispatch("get_polyhaven_categories", {})

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=5.0)
            worker_id = t.ident
        finally:
            addon.HANDLERS.update(originals)
            stop.set()
            pump.join(timeout=2.0)

        self.assertEqual(threads["create_object"], pump.ident)
        self.assertEqual(threads["polyhaven"], worker_id)

    def test_stop_pump_releases_pending_jobs(self):
        """Stopping the server must not leave worker threads blocked forever."""
        addon._pump_registered = True
        addon._pump_verified = True

        captured = []

        def worker():
            try:
                addon._run_on_main_thread(lambda: "never")
            except Exception as exc:  # noqa: BLE001
                captured.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.1)  # let it enqueue and block
        addon._stop_main_thread_pump()
        t.join(timeout=5.0)

        self.assertFalse(t.is_alive())
        self.assertEqual(len(captured), 1)
        self.assertIn("stopped", str(captured[0]).lower())
