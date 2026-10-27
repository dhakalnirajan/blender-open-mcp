"""
End-to-end integration tests for blender-open-mcp.

These prove the four layers work together without a real Blender or LLM:

1. MCP  - a real fastmcp Client session (prompts/list, prompts/get,
          tools/list, tools/call) against the server's FastMCP instance.
2. Addon - the timer-driven addon.py TCP server loop is pumped on a local
          port with bpy mocked, and driven through the MCP server's Blender
          bridge (_send_blender_command) over a real socket.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# --- Path setup ------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# --- Mock bpy before importing addon.py ------------------------------------
bpy_mock = MagicMock()
bpy_mock.props = MagicMock()
sys.modules["bpy"] = bpy_mock
sys.modules["bpy.props"] = bpy_mock.props

import addon  # noqa: E402
from blender_open_mcp import server  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _llm_snapshot():
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in server._llm_state.items()}


def _restore_llm(snapshot):
    server._llm_state.clear()
    server._llm_state.update(snapshot)


# ---------------------------------------------------------------------------
# MCP end-to-end (in-process fastmcp Client <-> server FastMCP instance)
# ---------------------------------------------------------------------------

class TestMCPEndToEnd:
    @pytest.mark.asyncio
    async def test_tools_and_prompts_are_listable(self):
        from fastmcp import Client

        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            for expected in [
                "blender_get_scene_info",
                "blender_create_object",
                "blender_execute_code",
                "blender_ai_prompt",
                "blender_set_llm_provider",
                "blender_get_llm_provider",
                "blender_list_llm_models",
            ]:
                assert expected in tool_names

            prompts = await client.list_prompts()
            prompt_names = {p.name for p in prompts}
            for expected in [
                "blender_build_scene",
                "blender_review_scene",
                "blender_configure_llm",
            ]:
                assert expected in prompt_names

    @pytest.mark.asyncio
    async def test_prompts_get_returns_messages(self):
        from fastmcp import Client

        async with Client(server.mcp) as client:
            result = await client.get_prompt(
                "blender_build_scene",
                arguments={"description": "a red cube on a plane"},
            )
            text = "\n".join(
                m.content.text for m in result.messages if hasattr(m.content, "text")
            )
            assert "a red cube on a plane" in text
            assert "blender_get_scene_info" in text

            review = await client.get_prompt("blender_review_scene")
            review_text = "\n".join(
                m.content.text
                for m in review.messages
                if hasattr(m.content, "text")
            )
            assert "blender_get_scene_info" in review_text

            cfg = await client.get_prompt(
                "blender_configure_llm",
                arguments={"provider": "lmstudio"},
            )
            cfg_text = "\n".join(
                m.content.text for m in cfg.messages if hasattr(m.content, "text")
            )
            assert "blender_set_llm_provider" in cfg_text
            assert "lmstudio" in cfg_text

    @pytest.mark.asyncio
    async def test_call_provider_tools_through_mcp(self):
        from fastmcp import Client

        snapshot = _llm_snapshot()
        try:
            async with Client(server.mcp) as client:
                state = await client.call_tool("blender_get_llm_provider")
                raw = state.content[0].text
                parsed = json.loads(raw)
                assert parsed["provider"] in {
                    "openai",
                    "ollama",
                    "lmstudio",
                    "llamacpp",
                    "azure",
                    "openai_compat",
                }

                switched = await client.call_tool(
                    "blender_set_llm_provider",
                    arguments={
                        "provider": "lmstudio",
                        "base_url": "http://localhost:1234/v1",
                        "model": "local-model",
                    },
                )
                parsed2 = json.loads(switched.content[0].text)
                assert parsed2["provider"] == "lmstudio"
                assert parsed2["model"] == "local-model"
        finally:
            _restore_llm(snapshot)

    @pytest.mark.asyncio
    async def test_ai_prompt_returns_graceful_error_when_provider_down(self):
        from fastmcp import Client

        snapshot = _llm_snapshot()
        try:
            server._llm_state.update(
                {
                    "provider": "openai",
                    "base_url": "http://localhost:59997/v1",
                    "model": "gpt-4o-mini",
                    "api_key": None,
                    "extra": {},
                }
            )
            async with Client(server.mcp) as client:
                result = await client.call_tool(
                    "blender_ai_prompt", arguments={"prompt": "hello"}
                )
                text = result.content[0].text
                assert text.startswith("Error:")
                assert "provider" in text.lower() or "openai" in text.lower()
        finally:
            _restore_llm(snapshot)


# ---------------------------------------------------------------------------
# Addon TCP bridge end-to-end (server tools <-> real addon server socket)
# ---------------------------------------------------------------------------

@pytest.fixture()
def addon_server():
    """Pump addon.py's timer server with bpy mocked; yield and stop."""
    host, port = "127.0.0.1", _free_port()

    # Mock the Blender scene context used by handlers.
    scene = MagicMock()
    scene.name = "Scene"
    scene.frame_current = 1
    scene.frame_start = 1
    scene.frame_end = 250
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.camera = MagicMock()
    scene.camera.name = "Camera"
    scene.objects = []

    mock_objects = MagicMock()
    mock_objects.get.return_value = None
    addon.bpy.context.scene = scene
    addon.bpy.data.objects = mock_objects

    addon.bpy.app.timers.is_registered.return_value = False
    addon._start_server(host, port)

    def pump_timer():
        while addon._server_running:
            if addon._process_server_requests() is None:
                return
            time.sleep(0.01)

    thread = threading.Thread(target=pump_timer, daemon=True)
    thread.start()
    deadline = time.time() + 5.0
    while time.time() < deadline and addon._server_socket is None:
        time.sleep(0.01)

    yield host, port

    addon._stop_server()
    thread.join(timeout=3.0)


@pytest.mark.asyncio
async def test_addon_bridge_scene_info(addon_server, monkeypatch):
    host, port = addon_server
    monkeypatch.setattr(server, "BLENDER_HOST", host)
    monkeypatch.setattr(server, "BLENDER_PORT", port)

    result = await server.blender_get_scene_info()
    parsed = json.loads(result)
    assert parsed["scene_name"] == "Scene"
    assert parsed["object_count"] == 0
    assert parsed["render_engine"] == "CYCLES"
    assert parsed["active_camera"] == "Camera"


@pytest.mark.asyncio
async def test_addon_bridge_execute_code(addon_server, monkeypatch):
    host, port = addon_server
    monkeypatch.setattr(server, "BLENDER_HOST", host)
    monkeypatch.setattr(server, "BLENDER_PORT", port)

    result = await server.blender_execute_code(code="print('hello-from-blender')")
    parsed = json.loads(result)
    assert parsed["output"].strip() == "hello-from-blender"
    assert parsed["code_length"] == len("print('hello-from-blender')")


@pytest.mark.asyncio
async def test_addon_bridge_unknown_command_error(addon_server, monkeypatch):
    host, port = addon_server
    monkeypatch.setattr(server, "BLENDER_HOST", host)
    monkeypatch.setattr(server, "BLENDER_PORT", port)

    from blender_open_mcp.server import _send_blender_command

    # The addon answers unknown commands with a JSON error payload.
    response = _send_blender_command("no_such_command")
    assert response["status"] == "error"
    assert "Unknown command" in response["message"]


# ---------------------------------------------------------------------------
# Parity: every command the server sends must exist in addon.HANDLERS
# ---------------------------------------------------------------------------

class TestAddonServerParity:
    BRIDGE_COMMANDS = [
        "get_scene_info",
        "get_object_info",
        "create_object",
        "modify_object",
        "delete_object",
        "set_material",
        "render_image",
        "execute_blender_code",
        "download_polyhaven_asset",
        "set_texture",
    ]

    def test_all_bridge_commands_registered_in_addon(self):
        for command in self.BRIDGE_COMMANDS:
            assert command in addon.HANDLERS, f"addon missing handler '{command}'"
            assert callable(addon.HANDLERS[command])

    @pytest.mark.asyncio
    async def test_all_server_tools_registered_on_mcp(self):
        from fastmcp import Client

        async with Client(server.mcp) as client:
            tools = {t.name for t in await client.list_tools()}
        for tool in [
            "blender_get_scene_info",
            "blender_get_object_info",
            "blender_create_object",
            "blender_modify_object",
            "blender_delete_object",
            "blender_set_material",
            "blender_render_image",
            "blender_execute_code",
            "blender_get_polyhaven_categories",
            "blender_search_polyhaven_assets",
            "blender_download_polyhaven_asset",
            "blender_set_texture",
            "blender_ai_prompt",
            "blender_get_llm_provider",
            "blender_set_llm_provider",
            "blender_list_llm_models",
        ]:
            assert tool in tools
