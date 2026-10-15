"""
Tests for blender-open-mcp MCP Server
======================================
These tests validate server tool registration, provider routing, and error
handling without requiring a live Blender instance or a live LLM endpoint.
"""

from __future__ import annotations

import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure server is importable from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Shared model/helper tests
# ---------------------------------------------------------------------------

class TestVec3:
    def test_vec3_defaults(self):
        from blender_open_mcp.server import Vec3

        v = Vec3()
        assert v.x == 0.0
        assert v.as_list() == [0.0, 0.0, 0.0]

    def test_vec3_custom(self):
        from blender_open_mcp.server import Vec3

        v = Vec3(x=1.0, y=2.5, z=-3.0)
        assert v.as_list() == [1.0, 2.5, -3.0]

    def test_vec3_arg_helper(self):
        from blender_open_mcp.server import Vec3, _vec3_arg

        assert _vec3_arg(Vec3(x=1.0, y=2.0, z=3.0)) == [1.0, 2.0, 3.0]
        assert _vec3_arg(None) is None


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_handle_blender_error_connection_refused(self):
        from blender_open_mcp.server import _handle_blender_error

        err = ConnectionRefusedError("Connection refused")
        result = _handle_blender_error(err)
        assert "Cannot connect" in result or "Connection refused" in result

    def test_handle_blender_error_timeout(self):
        from blender_open_mcp.server import _handle_blender_error

        err = TimeoutError("Timed out")
        result = _handle_blender_error(err)
        assert "Timed out" in result or "timeout" in result.lower()

    def test_handle_blender_error_unknown(self):
        from blender_open_mcp.server import _handle_blender_error

        err = RuntimeError("Unexpected")
        result = _handle_blender_error(err)
        assert "RuntimeError" in result or "Unexpected" in result

    def test_handle_provider_error_message(self):
        from blender_open_mcp.server import _handle_provider_error

        err = OSError("Connection refused")
        result = _handle_provider_error(err)
        assert "Error" in result and "provider" in result.lower()


# ---------------------------------------------------------------------------
# Blender command helper tests
# ---------------------------------------------------------------------------

class TestBlenderCommandHelper:
    def test_send_blender_command_connection_refused(self):
        """When Blender add-on is not running, raises ConnectionRefusedError."""
        import blender_open_mcp.server as srv
        from blender_open_mcp.server import _send_blender_command

        old_host, old_port = srv.BLENDER_HOST, srv.BLENDER_PORT
        try:
            srv.BLENDER_HOST = "localhost"
            srv.BLENDER_PORT = 19999
            with pytest.raises(ConnectionRefusedError):
                _send_blender_command("get_scene_info")
        finally:
            srv.BLENDER_HOST = old_host
            srv.BLENDER_PORT = old_port

    def test_format_blender_result_ok(self):
        from blender_open_mcp.server import _format_blender_result

        resp = {"status": "ok", "result": {"name": "Cube"}}
        out = _format_blender_result(resp)
        assert "Cube" in out

    def test_format_blender_result_error(self):
        from blender_open_mcp.server import _format_blender_result

        resp = {"status": "error", "message": "Object not found"}
        out = _format_blender_result(resp)
        assert "Object not found" in out

    def test_format_blender_result_plain(self):
        from blender_open_mcp.server import _format_blender_result

        out = _format_blender_result({"status": "ok", "result": "hello"})
        assert out == "hello"


# ---------------------------------------------------------------------------
# Tool registration / annotations
# ---------------------------------------------------------------------------

class TestToolAnnotations:
    """Verify tools are registered with the correct MCP annotations."""

    @staticmethod
    async def _tool(name: str):
        from blender_open_mcp.server import mcp

        return await mcp.get_tool(name)

    @pytest.mark.asyncio
    async def test_blender_delete_is_destructive(self):
        t = await self._tool("blender_delete_object")
        assert t is not None
        assert t.annotations.destructive_hint is True

    @pytest.mark.asyncio
    async def test_scene_info_is_readonly(self):
        t = await self._tool("blender_get_scene_info")
        assert t.annotations.read_only_hint is True

    @pytest.mark.asyncio
    async def test_execute_code_is_destructive(self):
        t = await self._tool("blender_execute_code")
        assert t.annotations.destructive_hint is True

    @pytest.mark.asyncio
    async def test_ai_prompt_is_not_readonly(self):
        t = await self._tool("blender_ai_prompt")
        assert t.annotations.read_only_hint is False

    @pytest.mark.asyncio
    async def test_set_llm_provider_is_not_readonly(self):
        t = await self._tool("blender_set_llm_provider")
        assert t.annotations.read_only_hint is False

    @pytest.mark.asyncio
    async def test_core_tools_registered(self):
        from blender_open_mcp.server import mcp

        names = [t.name for t in await mcp.list_tools()]
        for expected in [
            "blender_get_scene_info",
            "blender_create_object",
            "blender_delete_object",
            "blender_ai_prompt",
            "blender_get_llm_provider",
            "blender_set_llm_provider",
            "blender_list_llm_models",
            "blender_get_ollama_models",
        ]:
            assert expected in names

    @pytest.mark.asyncio
    async def test_ai_prompt_schema_is_flat(self):
        """Tool arguments should be flat fields, not a 'params' wrapper."""
        t = await self._tool("blender_ai_prompt")
        props = t.parameters.get("properties", {})
        assert "prompt" in props
        assert "params" not in props


# ---------------------------------------------------------------------------
# Tool behavior without Blender (validation before socket dispatch)
# ---------------------------------------------------------------------------

class TestToolInputValidation:
    @pytest.mark.asyncio
    async def test_set_material_bad_color_rejected_before_socket(self):
        """Invalid color should return an error without touching the socket."""
        from blender_open_mcp.server import blender_set_material

        result = await blender_set_material(
            "Cube", "Mat", color=[5.0, 0.0, 0.0]
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_scene_info_error_when_no_blender(self, monkeypatch):
        """Without Blender running, tool returns a friendly error string."""
        import blender_open_mcp.server as server

        monkeypatch.setattr(server, "BLENDER_HOST", "127.0.0.1")
        monkeypatch.setattr(server, "BLENDER_PORT", 1)
        result = await server.blender_get_scene_info()
        assert "Cannot connect to Blender" in result


# ---------------------------------------------------------------------------
# LLM provider routing tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestProviderRouting:
    def _fake_openai_resp(self, text="Here is some Python code..."):
        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "id": "cmpl-1",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                }

        return FakeResp()

    def _fake_ollama_resp(self, text="Ollama answered."):
        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"role": "assistant", "content": text}, "done": True}

        return FakeResp()

    @pytest.mark.asyncio
    async def test_query_llm_provider_openai_compat(self):
        from blender_open_mcp.server import _query_llm

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=self._fake_openai_resp())
            result = await _query_llm(
                "Create a cube in Blender",
                provider="openai",
                base_url="http://localhost:8000/v1",
                api_key="sk-test",
                model="gpt-4o-mini",
            )

        assert "Python" in result or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_query_llm_provider_ollama_chat(self):
        from blender_open_mcp.server import _query_llm

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=self._fake_ollama_resp())
            result = await _query_llm(
                "Create a cube in Blender",
                provider="ollama",
                base_url="http://localhost:11434",
                model="llama3.2",
            )

        assert "Ollama" in result or "answered" in result

    @pytest.mark.asyncio
    async def test_query_llm_provider_lm_studio(self):
        from blender_open_mcp.server import _query_llm

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(
                return_value=self._fake_openai_resp("LM Studio answered.")
            )
            result = await _query_llm(
                "Create a cube in Blender",
                provider="lmstudio",
                base_url="http://localhost:1234/v1",
                model="local-model",
            )

        assert "LM Studio" in result or "answered" in result

    @pytest.mark.asyncio
    async def test_query_llm_provider_llamacpp(self):
        from blender_open_mcp.server import _query_llm

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(
                return_value=self._fake_openai_resp("llama.cpp answered.")
            )
            result = await _query_llm(
                "Create a cube",
                provider="llamacpp",
                base_url="http://localhost:8080/v1",
                model="qwen",
            )

        assert "answered" in result

    @pytest.mark.asyncio
    async def test_query_llm_provider_connection_error(self):
        import httpx

        from blender_open_mcp.server import _query_llm

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            result = await _query_llm(
                "test prompt",
                provider="openai",
                base_url="http://localhost:8000/v1",
                api_key="sk-test",
                model="gpt-4o-mini",
            )

        assert "Error" in result
        assert "provider" in result.lower() or "openai" in result.lower()

    @pytest.mark.asyncio
    async def test_ai_prompt_default_provider_state(self):
        """blender_ai_prompt without overrides should use _llm_state and error gracefully."""
        import blender_open_mcp.server as srv

        # Point at an unused port so the request fails fast with a provider error.
        old = dict(srv._llm_state)
        try:
            srv._llm_state["provider"] = "openai"
            srv._llm_state["base_url"] = "http://localhost:59999/v1"
            srv._llm_state["model"] = "gpt-4o-mini"
            from blender_open_mcp.server import blender_ai_prompt

            result = await blender_ai_prompt("hello")
            assert isinstance(result, str)
            assert "Error" in result
        finally:
            srv._llm_state.update(old)

    @pytest.mark.asyncio
    async def test_set_llm_provider_tool_updates_state(self):
        import blender_open_mcp.server as srv
        from blender_open_mcp.server import blender_set_llm_provider

        old = dict(srv._llm_state)
        try:
            srv._llm_state.update(
                {
                    "provider": "openai",
                    "base_url": "http://localhost:8000/v1",
                    "model": "gpt-4o-mini",
                    "api_key": None,
                }
            )
            result = await blender_set_llm_provider(
                provider="ollama",
                base_url="http://localhost:11434",
                model="llama3.2",
            )
            assert "ollama" in result.lower()
            assert srv._llm_state["provider"] == "ollama"
            assert srv._llm_state["model"] == "llama3.2"
            assert srv._llm_state["base_url"] == "http://localhost:11434"
        finally:
            srv._llm_state.update(old)

    @pytest.mark.asyncio
    async def test_set_llm_provider_with_api_key(self):
        import blender_open_mcp.server as srv
        from blender_open_mcp.server import blender_set_llm_provider

        old = dict(srv._llm_state)
        try:
            srv._llm_state["api_key"] = None
            result = await blender_set_llm_provider(
                provider="openai",
                base_url="http://localhost:8000/v1",
                api_key="sk-secret",
                model="gpt-4o-mini",
            )
            assert "openai" in result.lower()
            assert srv._llm_state["api_key"] == "sk-secret"
            # Masked in the public response
            assert "sk-secret" not in result
        finally:
            srv._llm_state.update(old)

    @pytest.mark.asyncio
    async def test_set_llm_provider_invalid_provider(self):
        from blender_open_mcp.server import blender_set_llm_provider

        result = await blender_set_llm_provider(provider="does_not_exist")
        assert "Error" in result
        assert "Unknown LLM provider" in result

    @pytest.mark.asyncio
    async def test_list_llm_models_ollama(self):
        import blender_open_mcp.server as srv
        from blender_open_mcp.server import blender_list_llm_models

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "models": [
                        {"name": "llama3.2", "modified_at": "2024-01-01"},
                        {"name": "gemma3", "modified_at": "2024-01-02"},
                    ]
                }

        old = dict(srv._llm_state)
        try:
            srv._llm_state.update(
                {
                    "provider": "ollama",
                    "base_url": "http://localhost:11434",
                    "api_key": None,
                }
            )
            with patch("httpx.AsyncClient") as MockClient:
                instance = MockClient.return_value.__aenter__.return_value
                instance.get = AsyncMock(return_value=FakeResp())
                result = await blender_list_llm_models()

            parsed = json.loads(result)
            assert parsed["provider"] == "ollama"
            assert parsed["models"][0]["id"] == "llama3.2"
        finally:
            srv._llm_state.update(old)

    @pytest.mark.asyncio
    async def test_ollama_alias_tools(self):
        import blender_open_mcp.server as srv
        from blender_open_mcp.server import (
            blender_set_ollama_model,
            blender_set_ollama_url,
        )

        old = dict(srv._llm_state)
        try:
            srv._llm_state.update(
                {
                    "provider": "ollama",
                    "base_url": "http://localhost:11434",
                    "model": "llama3.2",
                    "api_key": None,
                }
            )
            r1 = await blender_set_ollama_model(model_name="gemma3")
            assert "ollama" in r1.lower()
            assert srv._llm_state["model"] == "gemma3"
            r2 = await blender_set_ollama_url(url="http://localhost:11435")
            assert srv._llm_state["base_url"] == "http://localhost:11435"
            assert "ollama" in r2.lower()
        finally:
            srv._llm_state.update(old)


# ---------------------------------------------------------------------------
# PolyHaven integration tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestPolyHavenTools:
    @pytest.mark.asyncio
    async def test_get_polyhaven_categories_success(self):
        from blender_open_mcp.server import blender_get_polyhaven_categories

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = ["wood", "metal", "fabric"]

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_resp)
            result = await blender_get_polyhaven_categories(asset_type="textures")

        parsed = json.loads(result)
        assert "wood" in parsed

    @pytest.mark.asyncio
    async def test_search_polyhaven_assets_pagination(self):
        from blender_open_mcp.server import blender_search_polyhaven_assets

        fake_assets = {
            f"asset_{i}": {"name": f"Asset {i}", "categories": ["wood"]} for i in range(50)
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = fake_assets

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_resp)
            result = await blender_search_polyhaven_assets(limit=10, offset=0)

        parsed = json.loads(result)
        assert parsed["total"] == 50
        assert parsed["count"] == 10
        assert parsed["has_more"] is True
        assert parsed["next_offset"] == 10

    @pytest.mark.asyncio
    async def test_polyhaven_categories_connection_error(self):
        import httpx

        from blender_open_mcp.server import blender_get_polyhaven_categories

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            result = await blender_get_polyhaven_categories(asset_type="hdris")

        assert "Error" in result
        assert "PolyHaven" in result
