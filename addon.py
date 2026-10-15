"""
Blender Add-on: blender-open-mcp
=================================
Install this file in Blender:
  Edit → Preferences → Add-ons → Install → select addon.py → enable "Blender MCP"

After enabling, open the 3D Viewport, press N, find the "Blender MCP" panel,
and click "Start MCP Server". The add-on listens on TCP port 9876 by default.

Protocol (JSON over TCP, newline-terminated):
  Request : {"type": "<command>", "params": {...}}
  Response: {"status": "ok", "result": <any>}
           {"status": "error", "message": "<reason>"}
  Limit   : request JSON line must not exceed 8 MiB.
"""

bl_info = {
    "name": "Blender MCP",
    "author": "blender-open-mcp contributors",
    "version": (4, 0, 0),
    "blender": (3, 0, 0),
    "location": "3D Viewport > Sidebar > Blender MCP",
    "description": "MCP server add-on: control Blender via the Model Context Protocol",
    "category": "Interface",
}

import bpy
import json
import math
import os
import socket
import time
import traceback
import urllib.request
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876
SOCKET_TIMEOUT = 60.0
RECV_BUFFER = 8192
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CLIENTS = 16
SERVER_POLL_INTERVAL = 0.05


# ---------------------------------------------------------------------------
# Global server state
# ---------------------------------------------------------------------------
_server_socket: Optional[socket.socket] = None
_server_running = False
_server_clients: Dict[socket.socket, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(result: Any) -> bytes:
    return (json.dumps({"status": "ok", "result": result}) + "\n").encode("utf-8")


def _err(message: str) -> bytes:
    return (json.dumps({"status": "error", "message": message}) + "\n").encode("utf-8")


def _vec3_from_list(lst, default=(0.0, 0.0, 0.0)):
    if lst and len(lst) >= 3:
        return tuple(float(v) for v in lst[:3])
    return default


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_get_scene_info(_params: Dict) -> Any:
    scene = bpy.context.scene
    objects = []
    for obj in scene.objects:
        objects.append({
            "name": obj.name,
            "type": obj.type,
            "location": list(obj.location),
            "rotation_euler": list(obj.rotation_euler),
            "scale": list(obj.scale),
            "visible_viewport": obj.visible_get(),
            "material_slots": [ms.name for ms in obj.material_slots],
        })
    camera = scene.camera.name if scene.camera else None
    return {
        "scene_name": scene.name,
        "frame_current": scene.frame_current,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "render_engine": scene.render.engine,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "active_camera": camera,
        "object_count": len(scene.objects),
        "objects": objects,
    }


def handle_get_object_info(params: Dict) -> Any:
    name = params.get("object_name", "")
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object '{name}' not found in the scene.")
    info: Dict[str, Any] = {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation_euler": [math.degrees(r) for r in obj.rotation_euler],
        "rotation_euler_rad": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "visible_viewport": obj.visible_get(),
        "hide_render": obj.hide_render,
        "material_slots": [ms.name for ms in obj.material_slots],
        "parent": obj.parent.name if obj.parent else None,
        "children": [c.name for c in obj.children],
    }
    if obj.type == "MESH" and obj.data:
        info["vertex_count"] = len(obj.data.vertices)
        info["edge_count"] = len(obj.data.edges)
        info["polygon_count"] = len(obj.data.polygons)
    if obj.type == "LIGHT" and obj.data:
        info["light_type"] = obj.data.type
        info["light_energy"] = obj.data.energy
        info["light_color"] = list(obj.data.color)
    if obj.type == "CAMERA" and obj.data:
        info["camera_type"] = obj.data.type
        info["focal_length"] = obj.data.lens
    return info


def handle_create_object(params: Dict) -> Any:
    prim_type = params.get("type", "CUBE").upper()
    location = _vec3_from_list(params.get("location"))
    rotation = _vec3_from_list(params.get("rotation"))
    scale = _vec3_from_list(params.get("scale"), (1.0, 1.0, 1.0))

    # Deselect all
    bpy.ops.object.select_all(action="DESELECT")

    # Add primitive
    prim_dispatch = {
        "CUBE":       bpy.ops.mesh.primitive_cube_add,
        "SPHERE":     bpy.ops.mesh.primitive_uv_sphere_add,
        "CYLINDER":   bpy.ops.mesh.primitive_cylinder_add,
        "CONE":       bpy.ops.mesh.primitive_cone_add,
        "TORUS":      bpy.ops.mesh.primitive_torus_add,
        "PLANE":      bpy.ops.mesh.primitive_plane_add,
        "CIRCLE":     bpy.ops.mesh.primitive_circle_add,
        "ICO_SPHERE": bpy.ops.mesh.primitive_ico_sphere_add,
        "GRID":       bpy.ops.mesh.primitive_grid_add,
        "MONKEY":     bpy.ops.mesh.primitive_monkey_add,
    }
    op = prim_dispatch.get(prim_type)
    if op is None:
        raise ValueError(
            f"Unknown primitive type '{prim_type}'. Valid: {list(prim_dispatch)}"
        )

    op(location=location, rotation=rotation, scale=scale)

    obj = bpy.context.active_object
    if obj is None:
        raise RuntimeError(
            "Object was not created (no active object after operator)."
        )

    # Rename if requested
    desired_name = params.get("name")
    if desired_name:
        obj.name = desired_name
        if obj.data:
            obj.data.name = desired_name

    return {
        "created": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation_euler": list(obj.rotation_euler),
        "scale": list(obj.scale),
    }


def handle_modify_object(params: Dict) -> Any:
    name = params.get("name", "")
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object '{name}' not found.")

    changed: Dict[str, Any] = {}

    loc = params.get("location")
    if loc is not None:
        obj.location = _vec3_from_list(loc)
        changed["location"] = list(obj.location)

    rot = params.get("rotation")
    if rot is not None:
        obj.rotation_euler = _vec3_from_list(rot)
        changed["rotation_euler"] = list(obj.rotation_euler)

    scl = params.get("scale")
    if scl is not None:
        obj.scale = _vec3_from_list(scl, (1.0, 1.0, 1.0))
        changed["scale"] = list(obj.scale)

    visible = params.get("visible")
    if visible is not None:
        obj.hide_viewport = not bool(visible)
        changed["visible_viewport"] = bool(visible)

    return {"modified": name, "changes": changed}


def handle_delete_object(params: Dict) -> Any:
    name = params.get("name", "")
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object '{name}' not found.")
    bpy.data.objects.remove(obj, do_unlink=True)
    return {"deleted": name}


def handle_set_material(params: Dict) -> Any:
    obj_name = params.get("object_name", "")
    mat_name = params.get("material_name", "")
    color = params.get("color")

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        raise ValueError(f"Object '{obj_name}' not found.")
    if obj.type not in ("MESH", "CURVE", "SURFACE", "FONT", "META"):
        raise ValueError(
            f"Object '{obj_name}' is of type '{obj.type}' which does not support materials."
        )

    # Create or reuse material
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes

    # Find or create Principled BSDF
    bsdf = nodes.get("Principled BSDF") or next(
        (n for n in nodes if n.type == "BSDF_PRINCIPLED"), None
    )
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")

    if color and len(color) >= 3:
        rgba = list(color) + [1.0] if len(color) == 3 else list(color[:4])
        bsdf.inputs["Base Color"].default_value = rgba

    # Assign to object
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    return {
        "material_assigned": mat_name,
        "object": obj_name,
        "color": color,
    }


def handle_render_image(params: Dict) -> Any:
    file_path = params.get("file_path", "/tmp/blender_render.png")
    # Ensure directory exists
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        raise ValueError(
            f"Directory '{directory}' does not exist. Please create it first."
        )

    scene = bpy.context.scene
    scene.render.filepath = file_path
    bpy.ops.render.render(write_still=True)
    return {"rendered_to": file_path, "engine": scene.render.engine}


def handle_execute_blender_code(params: Dict) -> Any:
    code = params.get("code", "")
    if not code.strip():
        raise ValueError("No code provided.")

    # Capture print output
    import io
    import sys as _sys
    stdout_capture = io.StringIO()
    old_stdout = _sys.stdout
    try:
        _sys.stdout = stdout_capture
        local_ns: Dict[str, Any] = {"bpy": bpy}
        exec(compile(code, "<blender_mcp>", "exec"), local_ns)
        output = stdout_capture.getvalue()
    except Exception:
        output = traceback.format_exc()
    finally:
        _sys.stdout = old_stdout

    return {"output": output or "(no output)", "code_length": len(code)}


def handle_get_polyhaven_categories(params: Dict) -> Any:
    asset_type = params.get("asset_type", "textures")
    url = f"https://api.polyhaven.com/categories/{asset_type}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def handle_search_polyhaven_assets(params: Dict) -> Any:
    asset_type = params.get("asset_type", "textures")
    categories = params.get("categories")
    url = f"https://api.polyhaven.com/assets?type={asset_type}"
    if categories:
        cats = categories if isinstance(categories, str) else ",".join(categories)
        url += f"&categories={cats}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return {"total": len(data), "assets": list(data.keys())[:50]}


def handle_download_polyhaven_asset(params: Dict) -> Any:
    asset_id = params.get("asset_id", "")
    asset_type = params.get("asset_type", "textures")
    resolution = params.get("resolution", "1k")
    file_format = params.get("file_format", "jpg")
    files_data = params.get("files_data")

    if not files_data:
        url = f"https://api.polyhaven.com/files/{asset_id}"
        with urllib.request.urlopen(url, timeout=20) as resp:
            files_data = json.loads(resp.read().decode("utf-8"))

    # Navigate to the correct download entry
    try:
        if asset_type == "hdris":
            download_info = files_data["hdri"][resolution][file_format]
        elif asset_type == "textures":
            download_info = files_data.get("Diffuse", files_data.get("Color", {}))
            download_info = download_info.get(resolution, {}).get(file_format, {})
        else:
            download_info = files_data.get("blend", files_data.get("gltf", {}))
            download_info = download_info.get(resolution, {})
            download_info = next(iter(download_info.values()), {}) if download_info else {}
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"Could not resolve download URL for '{asset_id}' ({resolution} {file_format}): {exc}"
        )

    download_url = download_info.get("url") if isinstance(download_info, dict) else None
    if not download_url:
        raise ValueError(
            f"No download URL found for asset '{asset_id}' at {resolution}/{file_format}. "
            "Try a different resolution or format."
        )

    # Download to temp directory
    tmp_dir = bpy.app.tempdir
    ext = file_format
    dest = os.path.join(tmp_dir, f"{asset_id}_{resolution}.{ext}")
    urllib.request.urlretrieve(download_url, dest)

    # Import based on type
    if asset_type == "hdris":
        world = bpy.context.scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            bpy.context.scene.world = world
        world.use_nodes = True
        env_tex_node = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
        env_tex_node.image = bpy.data.images.load(dest)
        bg_node = (
            world.node_tree.nodes.get("Background")
            or world.node_tree.nodes.new("ShaderNodeBackground")
        )
        world.node_tree.links.new(
            env_tex_node.outputs["Color"], bg_node.inputs["Color"]
        )
        return {"hdri_applied": asset_id, "file": dest, "world": world.name}
    else:
        return {
            "downloaded": asset_id,
            "file": dest,
            "note": "Use blender_set_texture to apply this texture.",
        }


def handle_set_texture(params: Dict) -> Any:
    obj_name = params.get("object_name", "")
    texture_id = params.get("texture_id", "")

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        raise ValueError(f"Object '{obj_name}' not found.")

    # Find the downloaded texture file
    tmp_dir = bpy.app.tempdir
    candidates = [f for f in os.listdir(tmp_dir) if f.startswith(texture_id)]
    if not candidates:
        raise ValueError(
            f"No downloaded texture found for '{texture_id}'. "
            "Run blender_download_polyhaven_asset first."
        )

    tex_file = os.path.join(tmp_dir, candidates[0])
    mat_name = f"mat_{texture_id}"
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.image = bpy.data.images.load(tex_file)

    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    return {"texture_applied": texture_id, "material": mat_name, "object": obj_name}


def handle_set_llm_provider(params: Dict) -> Any:
    # Stored on the server side; this handler is a passthrough acknowledgement
    return {
        "provider": params.get("provider", ""),
        "model": params.get("model", ""),
        "base_url": params.get("base_url", ""),
    }


def handle_get_llm_provider(_params: Dict) -> Any:
    # The server handles provider state; this is informational
    return {"note": "Use blender_get_llm_provider on the MCP server side."}


def handle_get_ollama_models(_params: Dict) -> Any:
    # The server handles Ollama queries; this is informational
    return {"note": "Use blender_get_ollama_models on the MCP server side."}


# ---------------------------------------------------------------------------
# Command router
# ---------------------------------------------------------------------------
HANDLERS = {
    "get_scene_info":           handle_get_scene_info,
    "get_object_info":          handle_get_object_info,
    "create_object":            handle_create_object,
    "modify_object":            handle_modify_object,
    "delete_object":            handle_delete_object,
    "set_material":             handle_set_material,
    "render_image":             handle_render_image,
    "execute_blender_code":     handle_execute_blender_code,
    "get_polyhaven_categories": handle_get_polyhaven_categories,
    "search_polyhaven_assets":  handle_search_polyhaven_assets,
    "download_polyhaven_asset": handle_download_polyhaven_asset,
    "set_texture":              handle_set_texture,
    "set_llm_provider":         handle_set_llm_provider,
    "get_llm_provider":         handle_get_llm_provider,
    "get_ollama_models":        handle_get_ollama_models,
}


def _dispatch(command_type: str, params: Dict) -> bytes:
    """Run a command handler and return its encoded response."""
    handler = HANDLERS.get(command_type)
    if handler is None:
        return _err(
            f"Unknown command '{command_type}'. Available: {list(HANDLERS)}"
        )
    try:
        result = handler(params)
        return _ok(result)
    except Exception as exc:
        tb = traceback.format_exc()
        return _err(f"{type(exc).__name__}: {exc}\n{tb}")


def _close_client(conn: socket.socket) -> None:
    _server_clients.pop(conn, None)
    try:
        conn.close()
    except OSError:
        pass


def _stop_server() -> None:
    """Close the listener and clients; this function runs on Blender's main thread."""
    global _server_socket, _server_running
    _server_running = False
    for conn in list(_server_clients):
        _close_client(conn)
    if _server_socket is not None:
        try:
            _server_socket.close()
        except OSError:
            pass
        _server_socket = None
    if bpy.app.timers.is_registered(_process_server_requests):
        bpy.app.timers.unregister(_process_server_requests)


def _start_server(host: str, port: int) -> None:
    """Bind a nonblocking listener and poll it from Blender's application timer."""
    global _server_socket, _server_running
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(MAX_CLIENTS)
        listener.setblocking(False)
        if not bpy.app.timers.is_registered(_process_server_requests):
            bpy.app.timers.register(
                _process_server_requests,
                first_interval=SERVER_POLL_INTERVAL,
                persistent=True,
            )
    except Exception:
        listener.close()
        raise
    _server_clients.clear()
    _server_socket = listener
    _server_running = True
    print(f"[Blender MCP] TCP server listening on {host}:{port}")


def _process_server_requests():
    """Advance nonblocking TCP connections and run commands in Blender's timer context."""
    if not _server_running or _server_socket is None:
        return None

    now = time.monotonic()
    accepted = 0
    while len(_server_clients) < MAX_CLIENTS and accepted < MAX_CLIENTS:
        try:
            conn, _addr = _server_socket.accept()
        except BlockingIOError:
            break
        except OSError as exc:
            print(f"[Blender MCP] Accept failed: {exc}")
            _stop_server()
            return None
        try:
            conn.setblocking(False)
        except OSError:
            conn.close()
            continue
        _server_clients[conn] = {
            "buffer": bytearray(),
            "response": None,
            "sent": 0,
            "deadline": now + SOCKET_TIMEOUT,
        }
        accepted += 1

    for conn, state in list(_server_clients.items()):
        if now >= state["deadline"]:
            _close_client(conn)
            continue
        try:
            if state["response"] is None:
                try:
                    chunk = conn.recv(RECV_BUFFER)
                except BlockingIOError:
                    chunk = None
                if chunk == b"":
                    _close_client(conn)
                    continue
                if chunk:
                    state["buffer"].extend(chunk)
                    newline = state["buffer"].find(b"\n")
                    request_size = (
                        newline if newline >= 0 else len(state["buffer"])
                    )
                    if request_size > MAX_REQUEST_BYTES:
                        state["response"] = _err("Request exceeds maximum size.")
                    elif newline >= 0:
                        line = bytes(state["buffer"][:newline])
                        try:
                            message = json.loads(line.decode("utf-8").strip())
                            state["response"] = _dispatch(
                                message.get("type", ""), message.get("params", {})
                            )
                        except json.JSONDecodeError as exc:
                            state["response"] = _err(f"Invalid JSON: {exc}")
                        except Exception as exc:
                            state["response"] = _err(f"Server error: {exc}")

            response = state["response"]
            if response is not None:
                try:
                    sent = conn.send(response[state["sent"] :])
                except BlockingIOError:
                    sent = 0
                if sent == 0 and state["sent"] < len(response):
                    continue
                state["sent"] += sent
                if state["sent"] >= len(response):
                    _close_client(conn)
        except OSError:
            _close_client(conn)

    return SERVER_POLL_INTERVAL if _server_running else None


# ---------------------------------------------------------------------------
# Blender operators
# ---------------------------------------------------------------------------

class BLENDER_MCP_OT_StartServer(bpy.types.Operator):
    """Start the Blender MCP TCP server"""
    bl_idname = "blender_mcp.start_server"
    bl_label = "Start MCP Server"
    bl_description = (
        "Start the TCP server that accepts MCP commands from blender-open-mcp"
    )

    def execute(self, context: bpy.types.Context):
        if _server_running:
            self.report({"WARNING"}, "MCP server is already running.")
            return {"CANCELLED"}

        prefs = context.scene.blender_mcp_props
        try:
            _start_server(prefs.server_host, prefs.server_port)
        except OSError as exc:
            self.report({"ERROR"}, f"Could not start MCP server: {exc}")
            return {"CANCELLED"}
        self.report(
            {"INFO"}, f"MCP server started on {prefs.server_host}:{prefs.server_port}"
        )
        return {"FINISHED"}


class BLENDER_MCP_OT_StopServer(bpy.types.Operator):
    """Stop the Blender MCP TCP server"""
    bl_idname = "blender_mcp.stop_server"
    bl_label = "Stop MCP Server"
    bl_description = "Shut down the MCP TCP server"

    def execute(self, context: bpy.types.Context):
        if not _server_running:
            self.report({"WARNING"}, "MCP server is not running.")
            return {"CANCELLED"}

        _stop_server()
        self.report({"INFO"}, "MCP server stopped.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Scene properties
# ---------------------------------------------------------------------------

class BlenderMCPProperties(bpy.types.PropertyGroup):
    server_host: bpy.props.StringProperty(
        name="Host",
        default=DEFAULT_HOST,
        description="TCP host for the Blender MCP socket server",
    )
    server_port: bpy.props.IntProperty(
        name="Port",
        default=DEFAULT_PORT,
        min=1024,
        max=65535,
        description="TCP port for the Blender MCP socket server",
    )


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class BLENDER_MCP_PT_Panel(bpy.types.Panel):
    """Blender MCP sidebar panel"""
    bl_label = "Blender MCP"
    bl_idname = "BLENDER_MCP_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blender MCP"

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        props = context.scene.blender_mcp_props

        layout.label(text="MCP Server Settings", icon="NETWORK_DRIVE")
        col = layout.column(align=True)
        col.prop(props, "server_host")
        col.prop(props, "server_port")

        layout.separator()

        if _server_running:
            layout.label(text="● Server Running", icon="CHECKMARK")
            layout.operator("blender_mcp.stop_server", icon="CANCEL")
        else:
            layout.label(text="○ Server Stopped", icon="X")
            layout.operator("blender_mcp.start_server", icon="PLAY")

        layout.separator()
        layout.label(text="Quick Reference:", icon="INFO")
        box = layout.box()
        box.scale_y = 0.7
        box.label(text="MCP Server: port 8000")
        box.label(text="Blender Add-on: port 9876")
        box.label(text="Ollama: port 11434")
        box.label(text="LM Studio: port 1234")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

CLASSES = [
    BlenderMCPProperties,
    BLENDER_MCP_OT_StartServer,
    BLENDER_MCP_OT_StopServer,
    BLENDER_MCP_PT_Panel,
]


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blender_mcp_props = bpy.props.PointerProperty(
        type=BlenderMCPProperties
    )
    print(
        "[Blender MCP] Add-on registered. Open the N-sidebar in 3D View → Blender MCP."
    )


def unregister():
    _stop_server()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.blender_mcp_props
    print("[Blender MCP] Add-on unregistered.")


if __name__ == "__main__":
    register()
