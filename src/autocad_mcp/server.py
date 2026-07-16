"""AutoCAD MCP Server v3.1 — 8 consolidated tools with operation dispatch.

Tools: drawing, entity, layer, block, annotation, pid, view, system
"""

from __future__ import annotations

import structlog
from mcp.server.fastmcp import FastMCP

from autocad_mcp.client import (
    _error,
    _json,
    _safe,
    add_screenshot_if_available,
    get_backend,
)

# FastMCP validates return types via Pydantic. Tools that may return
# ImageContent (screenshot) alongside TextContent need a union return type.
ToolResult = str | list

log = structlog.get_logger()

mcp = FastMCP("autocad-mcp")


# ==========================================================================
# 1. drawing — File/drawing management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Drawing Operations", "readOnlyHint": False})
@_safe("drawing")
async def drawing(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Drawing file management.

    Operations:
      create     — Create a new empty drawing. data: {name?}
      open       — Open an existing drawing. data: {path}
      info       — Get drawing extents, entity count, layers, blocks.
      save       — Save current drawing. data: {path?} (saves to path if given, else QSAVE)
      save_as_dxf — Export as DXF. data: {path}
      plot_pdf   — Plot to PDF. data: {path}
      purge      — Purge unused objects.
      get_variables — Get system variables. data: {names: [...]}
      undo       — Undo last operation.
      redo       — Redo last undone operation.
    """
    data = data or {}
    backend = await get_backend()

    if operation == "create":
        result = await backend.drawing_create(data.get("name"))
    elif operation == "info":
        result = await backend.drawing_info()
    elif operation == "save":
        result = await backend.drawing_save(data.get("path"))
    elif operation == "save_as_dxf":
        result = await backend.drawing_save_as_dxf(data["path"])
    elif operation == "plot_pdf":
        result = await backend.drawing_plot_pdf(data["path"])
    elif operation == "purge":
        result = await backend.drawing_purge()
    elif operation == "get_variables":
        result = await backend.drawing_get_variables(data.get("names"))
    elif operation == "open":
        result = await backend.drawing_open(data["path"])
    elif operation == "undo":
        result = await backend.undo()
    elif operation == "redo":
        result = await backend.redo()
    else:
        return _json({"error": f"Unknown drawing operation: {operation}"})

    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 2. entity — Entity CRUD + modification
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Entity Operations", "readOnlyHint": False})
@_safe("entity")
async def entity(
    operation: str,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    points: list[list[float]] | None = None,
    layer: str | None = None,
    entity_id: str | None = None,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Entity creation, querying, and modification.

    Create operations:
      create_line       — x1, y1, x2, y2, layer?
      create_circle     — data: {cx, cy, radius}, layer?
      create_polyline   — points: [[x,y],...], data: {closed?}, layer?
      create_rectangle  — x1, y1, x2, y2, layer?
      create_arc        — data: {cx, cy, radius, start_angle, end_angle}, layer?
      create_ellipse    — data: {cx, cy, major_x, major_y, ratio}, layer?
      create_mtext      — data: {x, y, width, text, height?}, layer?
      create_hatch      — entity_id, data: {pattern?}

    Read operations:
      list              — layer? → list entities
      count             — layer? → count entities
      get               — entity_id → entity details

    Modify operations:
      copy    — entity_id, data: {dx, dy}
      move    — entity_id, data: {dx, dy}
      rotate  — entity_id, data: {cx, cy, angle}
      scale   — entity_id, data: {cx, cy, factor}
      mirror  — entity_id, x1, y1, x2, y2
      offset  — entity_id, data: {distance}
      array   — entity_id, data: {rows, cols, row_dist, col_dist}
      fillet  — data: {id1, id2, radius}
      chamfer — data: {id1, id2, dist1, dist2}
      erase   — entity_id
    """
    data = data or {}
    backend = await get_backend()

    # --- Create ---
    if operation == "create_line":
        result = await backend.create_line(x1, y1, x2, y2, layer)
    elif operation == "create_circle":
        result = await backend.create_circle(data["cx"], data["cy"], data["radius"], layer)
    elif operation == "create_polyline":
        result = await backend.create_polyline(points or [], data.get("closed", False), layer)
    elif operation == "create_rectangle":
        result = await backend.create_rectangle(x1, y1, x2, y2, layer)
    elif operation == "create_arc":
        result = await backend.create_arc(data["cx"], data["cy"], data["radius"], data["start_angle"], data["end_angle"], layer)
    elif operation == "create_ellipse":
        result = await backend.create_ellipse(data["cx"], data["cy"], data["major_x"], data["major_y"], data["ratio"], layer)
    elif operation == "create_mtext":
        result = await backend.create_mtext(data["x"], data["y"], data["width"], data["text"], data.get("height", 2.5), layer)
    elif operation == "create_hatch":
        result = await backend.create_hatch(entity_id, data.get("pattern", "ANSI31"))
    # --- Read ---
    elif operation == "list":
        result = await backend.entity_list(layer)
    elif operation == "count":
        result = await backend.entity_count(layer)
    elif operation == "get":
        result = await backend.entity_get(entity_id)
    # --- Modify ---
    elif operation == "copy":
        result = await backend.entity_copy(entity_id, data["dx"], data["dy"])
    elif operation == "move":
        result = await backend.entity_move(entity_id, data["dx"], data["dy"])
    elif operation == "rotate":
        result = await backend.entity_rotate(entity_id, data["cx"], data["cy"], data["angle"])
    elif operation == "scale":
        result = await backend.entity_scale(entity_id, data["cx"], data["cy"], data["factor"])
    elif operation == "mirror":
        result = await backend.entity_mirror(entity_id, x1, y1, x2, y2)
    elif operation == "offset":
        result = await backend.entity_offset(entity_id, data["distance"])
    elif operation == "array":
        result = await backend.entity_array(entity_id, data["rows"], data["cols"], data["row_dist"], data["col_dist"])
    elif operation == "fillet":
        result = await backend.entity_fillet(data["id1"], data["id2"], data["radius"])
    elif operation == "chamfer":
        result = await backend.entity_chamfer(data["id1"], data["id2"], data["dist1"], data["dist2"])
    elif operation == "erase":
        result = await backend.entity_erase(entity_id)
    else:
        return _json({"error": f"Unknown entity operation: {operation}"})

    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 3. layer — Layer management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Layer Operations", "readOnlyHint": False})
@_safe("layer")
async def layer(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Layer creation and management.

    Operations:
      list            — List all layers with properties.
      create          — data: {name, color?, linetype?}
      set_current     — data: {name}
      set_properties  — data: {name, color?, linetype?, lineweight?}
      freeze          — data: {name}
      thaw            — data: {name}
      lock            — data: {name}
      unlock          — data: {name}
    """
    data = data or {}
    backend = await get_backend()

    if operation == "list":
        result = await backend.layer_list()
    elif operation == "create":
        result = await backend.layer_create(data["name"], data.get("color", "white"), data.get("linetype", "CONTINUOUS"))
    elif operation == "set_current":
        result = await backend.layer_set_current(data["name"])
    elif operation == "set_properties":
        result = await backend.layer_set_properties(data["name"], data.get("color"), data.get("linetype"), data.get("lineweight"))
    elif operation == "freeze":
        result = await backend.layer_freeze(data["name"])
    elif operation == "thaw":
        result = await backend.layer_thaw(data["name"])
    elif operation == "lock":
        result = await backend.layer_lock(data["name"])
    elif operation == "unlock":
        result = await backend.layer_unlock(data["name"])
    else:
        return _json({"error": f"Unknown layer operation: {operation}"})

    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 4. block — Block operations
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Block Operations", "readOnlyHint": False})
@_safe("block")
async def block(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Block definition, insertion, and attribute management.

    Operations:
      list                 — List all block definitions.
      insert               — data: {name, x, y, scale?, rotation?, block_id?}
      insert_with_attributes — data: {name, x, y, scale?, rotation?, attributes: {tag: value}}
      get_attributes       — data: {entity_id}
      update_attribute     — data: {entity_id, tag, value}
      define               — data: {name, entities: [{type, ...}]}
    """
    data = data or {}
    backend = await get_backend()

    if operation == "list":
        result = await backend.block_list()
    elif operation == "insert":
        result = await backend.block_insert(
            data["name"], data["x"], data["y"],
            data.get("scale", 1.0), data.get("rotation", 0.0), data.get("block_id"),
        )
    elif operation == "insert_with_attributes":
        result = await backend.block_insert_with_attributes(
            data["name"], data["x"], data["y"],
            data.get("scale", 1.0), data.get("rotation", 0.0), data.get("attributes"),
        )
    elif operation == "get_attributes":
        result = await backend.block_get_attributes(data["entity_id"])
    elif operation == "update_attribute":
        result = await backend.block_update_attribute(data["entity_id"], data["tag"], data["value"])
    elif operation == "define":
        result = await backend.block_define(data["name"], data.get("entities", []))
    else:
        return _json({"error": f"Unknown block operation: {operation}"})

    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 5. annotation — Text, dimensions, leaders
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Annotation Operations", "readOnlyHint": False})
@_safe("annotation")
async def annotation(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Annotation: text, dimensions, and leaders.

    Operations:
      create_text             — data: {x, y, text, height?, rotation?, layer?}
      create_dimension_linear — data: {x1, y1, x2, y2, dim_x, dim_y}
      create_dimension_aligned — data: {x1, y1, x2, y2, offset}
      create_dimension_angular — data: {cx, cy, x1, y1, x2, y2}
      create_dimension_radius — data: {cx, cy, radius, angle}
      create_leader           — data: {points: [[x,y],...], text}
    """
    data = data or {}
    backend = await get_backend()

    if operation == "create_text":
        result = await backend.create_text(
            data["x"], data["y"], data["text"],
            data.get("height", 2.5), data.get("rotation", 0.0), data.get("layer"),
        )
    elif operation == "create_dimension_linear":
        result = await backend.create_dimension_linear(
            data["x1"], data["y1"], data["x2"], data["y2"], data["dim_x"], data["dim_y"],
        )
    elif operation == "create_dimension_aligned":
        result = await backend.create_dimension_aligned(
            data["x1"], data["y1"], data["x2"], data["y2"], data["offset"],
        )
    elif operation == "create_dimension_angular":
        result = await backend.create_dimension_angular(
            data["cx"], data["cy"], data["x1"], data["y1"], data["x2"], data["y2"],
        )
    elif operation == "create_dimension_radius":
        result = await backend.create_dimension_radius(
            data["cx"], data["cy"], data["radius"], data["angle"],
        )
    elif operation == "create_leader":
        result = await backend.create_leader(data["points"], data["text"])
    else:
        return _json({"error": f"Unknown annotation operation: {operation}"})

    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 6. pid — P&ID operations (CTO library)
# ==========================================================================


@mcp.tool(annotations={"title": "P&ID Operations (CTO Library)", "readOnlyHint": False})
@_safe("pid")
async def pid(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """P&ID drawing with CTO symbol library.

    Operations:
      setup_layers     — Create standard P&ID layers.
      insert_symbol    — data: {category, symbol, x, y, scale?, rotation?}
      list_symbols     — data: {category}
      draw_process_line — data: {x1, y1, x2, y2}
      connect_equipment — data: {x1, y1, x2, y2}
      add_flow_arrow   — data: {x, y, rotation?}
      add_equipment_tag — data: {x, y, tag, description?}
      add_line_number  — data: {x, y, line_num, spec}
      insert_valve     — data: {x, y, valve_type, rotation?, attributes?}
      insert_instrument — data: {x, y, instrument_type, rotation?, tag_id?, range_value?}
      insert_pump      — data: {x, y, pump_type, rotation?, attributes?}
      insert_tank      — data: {x, y, tank_type, scale?, attributes?}
    """
    data = data or {}
    backend = await get_backend()

    if operation == "setup_layers":
        result = await backend.pid_setup_layers()
    elif operation == "insert_symbol":
        result = await backend.pid_insert_symbol(
            data["category"], data["symbol"], data["x"], data["y"],
            data.get("scale", 1.0), data.get("rotation", 0.0),
        )
    elif operation == "list_symbols":
        result = await backend.pid_list_symbols(data["category"])
    elif operation == "draw_process_line":
        result = await backend.pid_draw_process_line(data["x1"], data["y1"], data["x2"], data["y2"])
    elif operation == "connect_equipment":
        result = await backend.pid_connect_equipment(data["x1"], data["y1"], data["x2"], data["y2"])
    elif operation == "add_flow_arrow":
        result = await backend.pid_add_flow_arrow(data["x"], data["y"], data.get("rotation", 0.0))
    elif operation == "add_equipment_tag":
        result = await backend.pid_add_equipment_tag(data["x"], data["y"], data["tag"], data.get("description", ""))
    elif operation == "add_line_number":
        result = await backend.pid_add_line_number(data["x"], data["y"], data["line_num"], data["spec"])
    elif operation == "insert_valve":
        result = await backend.pid_insert_valve(
            data["x"], data["y"], data["valve_type"],
            data.get("rotation", 0.0), data.get("attributes"),
        )
    elif operation == "insert_instrument":
        result = await backend.pid_insert_instrument(
            data["x"], data["y"], data["instrument_type"],
            data.get("rotation", 0.0), data.get("tag_id", ""), data.get("range_value", ""),
        )
    elif operation == "insert_pump":
        result = await backend.pid_insert_pump(
            data["x"], data["y"], data["pump_type"],
            data.get("rotation", 0.0), data.get("attributes"),
        )
    elif operation == "insert_tank":
        result = await backend.pid_insert_tank(
            data["x"], data["y"], data["tank_type"],
            data.get("scale", 1.0), data.get("attributes"),
        )
    else:
        return _json({"error": f"Unknown pid operation: {operation}"})

    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 7. view — Viewport and screenshot
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD View Operations", "readOnlyHint": True})
@_safe("view")
async def view(
    operation: str,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
) -> ToolResult:
    """Viewport control and screenshot capture.

    Operations:
      zoom_extents   — Zoom to show all entities.
      zoom_window    — Zoom to window: x1, y1, x2, y2
      get_screenshot — Capture current view as PNG image.
    """
    backend = await get_backend()

    if operation == "zoom_extents":
        result = await backend.zoom_extents()
        return _json(result.to_dict())
    elif operation == "zoom_window":
        result = await backend.zoom_window(x1, y1, x2, y2)
        return _json(result.to_dict())
    elif operation == "get_screenshot":
        result = await backend.get_screenshot()
        if result.ok and result.payload:
            from mcp.types import ImageContent, TextContent

            return [
                TextContent(type="text", text=_json({"ok": True, "screenshot": "attached"})),
                ImageContent(type="image", data=result.payload, mimeType="image/png"),
            ]
        return _json(result.to_dict())
    else:
        return _json({"error": f"Unknown view operation: {operation}"})


# ==========================================================================
# 8. system — Server management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD MCP System", "readOnlyHint": True})
@_safe("system")
async def system(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Server status and management.

    Operations:
      status        — Backend info, capabilities, health check.
      health        — Quick health check (ping backend).
      get_backend   — Return current backend name and capabilities.
      runtime       — Return process/runtime details for spawn diagnostics.
      init          — Re-initialize the backend.
      execute_lisp  — Execute arbitrary AutoLISP code (File IPC only). data: {code}
                      DISABLED by default. Runs arbitrary code on the host (can
                      reach shell/process/registry via AutoLISP). Enable only with
                      env AUTOCAD_MCP_ALLOW_EXECUTE_LISP=true.
    """
    data = data or {}

    if operation == "status" or operation == "get_backend":
        backend = await get_backend()
        result = await backend.status()
        return await add_screenshot_if_available(result, include_screenshot)
    elif operation == "health":
        try:
            backend = await get_backend()
            result = await backend.status()
            return _json({"ok": result.ok, "backend": backend.name})
        except Exception as e:
            return _json({"ok": False, "error": str(e)})
    elif operation == "runtime":
        import os
        import sys

        return _json(
            {
                "ok": True,
                "platform": sys.platform,
                "python": sys.executable,
                "cwd": os.getcwd(),
                "backend_env": os.environ.get("AUTOCAD_MCP_BACKEND", "auto"),
                "wsl_interop": bool(os.environ.get("WSL_INTEROP")),
            }
        )
    elif operation == "init":
        # Force re-initialization
        from autocad_mcp import client
        client._backend = None
        backend = await get_backend()
        result = await backend.status()
        return _json(result.to_dict())
    elif operation == "execute_lisp":
        from autocad_mcp.config import ALLOW_EXECUTE_LISP

        if not ALLOW_EXECUTE_LISP:
            return _json({
                "error": "execute_lisp is disabled.",
                "hint": "Freehand AutoLISP execution runs arbitrary code on the host "
                        "and is off by default. Set AUTOCAD_MCP_ALLOW_EXECUTE_LISP=true "
                        "to enable it.",
            })
        backend = await get_backend()
        if not data.get("code"):
            return _json({"error": "data.code is required"})
        result = await backend.execute_lisp(data["code"])
        return await add_screenshot_if_available(result, include_screenshot)
    else:
        return _json({"error": f"Unknown system operation: {operation}"})


# ==========================================================================
# 9. polisnab — Polisnab drafting standards (layers + dimension style)
# ==========================================================================


@mcp.tool(annotations={"title": "Polisnab Drafting Standards", "readOnlyHint": False})
@_safe("polisnab")
async def polisnab(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Polisnab module-drawing standards (Phases 1-2).

    Applies the fixed drafting standard from PROJECT-BRIEF-autocad-mcp-polisnab.md
    to the ACTIVE drawing. Run setup_layers + setup_dimstyle once on any new
    drawing before detailing.

    Operations:
      setup_layers    — Create the full Polisnab layer standard (11 layers:
                        AR-WALL, AR-WALL-INSUL, AR-DOOR, AR-WIND, AR-VENT,
                        AR-VESTIBULE, DIM, TEXT, AXIS, TITLE-BLOCK, HATCH-FLOOR)
                        with the ACI colors and linetypes from section 4.
      setup_dimstyle  — Create/redefine the POLISNAB-DIM dimension style
                        (DIMSCALE=40, mm units, integer precision, text above
                        the line, closed-filled arrows; section 5) and make it
                        the current dimension style so subsequent dimensions use it.
      insert_title_block — Insert the GOST 2.104 form 1 main-inscription block
                        (185x55 mm frame with editable attributes) at the
                        bottom-right of the drawing extents and fill it.
                        Pass fields via `data`:
                          doc_number, product_name, scale (e.g. "1:50"),
                          sheet_num, sheet_total, developed_by, checked_by,
                          approved_by, litera (optional), company_name (optional,
                          defaults to "ООО «ПОЛИСНАБ»"), block_scale (optional
                          geometric size multiplier, default 30).

    Parametric node generators (section 7) — geometry only, via `data`.
      draw_module_outline — Module envelope as a REAL wall: outer + inner face
                        polylines on AR-WALL with a SOLID grey fill between them
                        on AR-WALL-INSUL (double-line + hatch, not a thin line).
                        data: length_mm (=6000), width_mm (=2400) OUTER envelope;
                        wall thickness from the insulation series
                        (series "standard" 75 mm / "arctic" 150 mm) unless
                        wall_thickness_mm overrides; origin=[x,y] (=0,0).
                        Optional openings=[{wall_side, offset_mm, width_mm}, …]
                        cut holes (both faces + fill + jambs) up front — the
                        composable way to place several doors/windows at once.
      insert_interior_wall — Interior partition between two arbitrary points in
                        the same thick-wall style (SOLID band + two faces).
                        data: start_point=[x,y], end_point=[x,y],
                        thickness_mm (=75). Band is centred on the axis.
    Openings position against a module box (defaults 6000x2400 at origin;
    override with module_origin=[x,y], module_length, module_width,
    wall_thickness). wall_side is N/S/E/W; offset_mm runs left->right (S/N)
    or bottom->top (W/E). Furniture takes an absolute centre + rotation.
    Doors/windows accept an optional label (e.g. "D1"/"W1") placed as MTEXT
    on the TEXT layer just outside the opening.
      insert_exterior_door — data: wall_side, offset_mm, width_mm (=950),
                        swing ("in"/"out", default "out"), label? Cuts the thick
                        wall (both faces + fill + jambs) + leaf + dashed swing arc.
      insert_interior_door — data: wall_side, offset_mm, width_mm (=840),
                        swing_direction ("in"/"out", default "in"), label? Same symbol.
      insert_window   — data: wall_side, offset_mm, width_mm, label? Cuts the
                        thick wall (both faces + fill + jambs) + double glazing
                        line across the opening on AR-WIND (no swing/leaf).
      insert_bed      — data: x_mm, y_mm, rotation_deg, bed_type
                        ("single" 900x2000 / "double" 1400x2000) on FURN.
      insert_toilet   — data: x_mm, y_mm, rotation_deg. Cistern + bowl on FURN.
      insert_sink     — data: x_mm, y_mm, rotation_deg. Basin + tap on FURN.
      insert_table    — data: x_mm, y_mm, rotation_deg, width_mm (=1200),
                        depth_mm (=500). Plain outline rectangle on FURN;
                        wall-facing edge at local -Y.
      insert_wardrobe — data: x_mm, y_mm, rotation_deg, width_mm (=900),
                        depth_mm (=420). Cabinet body + the same two-leaf
                        "домик" door glyph as insert_locker_row, on FURN;
                        back at local -Y, doors opening toward local +Y.
      insert_chair    — data: x_mm, y_mm, rotation_deg. Seat + armrests +
                        curved backrest band on FURN, ~550x500. Faces local +Y.
      insert_locker_row — data: wall_side, offset_mm, cell_width_mm (=450),
                        depth_mm (=600), count (=5), label? Row of adjacent
                        locker cells + door-swing fan arcs along a wall on FURN.
    """
    from autocad_mcp import polisnab_standards as ps

    backend = await get_backend()
    d = data or {}

    if operation == "setup_layers":
        result = await ps.setup_layers(backend)
    elif operation == "setup_dimstyle":
        result = await ps.setup_dimstyle(backend)
    elif operation == "insert_title_block":
        result = await ps.insert_title_block(
            backend,
            doc_number=d.get("doc_number", ""),
            product_name=d.get("product_name", ""),
            scale=d.get("scale", ""),
            sheet_num=d.get("sheet_num", 1),
            sheet_total=d.get("sheet_total", 1),
            developed_by=d.get("developed_by", ""),
            checked_by=d.get("checked_by", ""),
            approved_by=d.get("approved_by", ""),
            litera=d.get("litera"),
            company_name=d.get("company_name"),
            block_scale=d.get("block_scale"),
        )
    elif operation == "draw_module_outline":
        result = await ps.draw_module_outline(
            backend,
            length_mm=d.get("length_mm"), width_mm=d.get("width_mm"),
            wall_thickness_mm=d.get("wall_thickness_mm"),
            series=d.get("series"), origin=d.get("origin"),
            openings=d.get("openings"),
        )
    elif operation == "insert_interior_wall":
        result = await ps.insert_interior_wall(
            backend, d.get("start_point"), d.get("end_point"),
            d.get("thickness_mm", 75.0),
        )
    elif operation == "insert_exterior_door":
        result = await ps.insert_exterior_door(
            backend, d.get("wall_side", "S"), d.get("offset_mm", 0),
            d.get("width_mm", 950.0), d.get("swing", "out"), label=d.get("label"),
            module_origin=d.get("module_origin"), module_length=d.get("module_length"),
            module_width=d.get("module_width"), wall_thickness=d.get("wall_thickness"),
        )
    elif operation == "insert_interior_door":
        result = await ps.insert_interior_door(
            backend, d.get("wall_side", "S"), d.get("offset_mm", 0),
            d.get("width_mm", 840.0), d.get("swing_direction", "in"), label=d.get("label"),
            module_origin=d.get("module_origin"), module_length=d.get("module_length"),
            module_width=d.get("module_width"), wall_thickness=d.get("wall_thickness"),
        )
    elif operation == "insert_window":
        result = await ps.insert_window(
            backend, d.get("wall_side", "S"), d.get("offset_mm", 0),
            d.get("width_mm", 1000.0), label=d.get("label"),
            module_origin=d.get("module_origin"), module_length=d.get("module_length"),
            module_width=d.get("module_width"), wall_thickness=d.get("wall_thickness"),
        )
    elif operation == "insert_bed":
        result = await ps.insert_bed(
            backend, d.get("x_mm", 0), d.get("y_mm", 0),
            d.get("rotation_deg", 0.0), d.get("bed_type", "single"),
        )
    elif operation == "insert_toilet":
        result = await ps.insert_toilet(
            backend, d.get("x_mm", 0), d.get("y_mm", 0), d.get("rotation_deg", 0.0),
        )
    elif operation == "insert_sink":
        result = await ps.insert_sink(
            backend, d.get("x_mm", 0), d.get("y_mm", 0), d.get("rotation_deg", 0.0),
        )
    elif operation == "insert_table":
        result = await ps.insert_table(
            backend, d.get("x_mm", 0), d.get("y_mm", 0), d.get("rotation_deg", 0.0),
            width_mm=d.get("width_mm", 1200.0), depth_mm=d.get("depth_mm", 500.0),
        )
    elif operation == "insert_wardrobe":
        result = await ps.insert_wardrobe(
            backend, d.get("x_mm", 0), d.get("y_mm", 0), d.get("rotation_deg", 0.0),
            width_mm=d.get("width_mm", 900.0), depth_mm=d.get("depth_mm", 420.0),
        )
    elif operation == "insert_chair":
        result = await ps.insert_chair(
            backend, d.get("x_mm", 0), d.get("y_mm", 0), d.get("rotation_deg", 0.0),
        )
    elif operation == "insert_locker_row":
        result = await ps.insert_locker_row(
            backend, d.get("wall_side", "W"), d.get("offset_mm", 0),
            d.get("cell_width_mm", 450.0), d.get("depth_mm", 600.0),
            d.get("count", 5), label=d.get("label"),
            module_origin=d.get("module_origin"), module_length=d.get("module_length"),
            module_width=d.get("module_width"), wall_thickness=d.get("wall_thickness"),
        )
    else:
        return _json({"error": f"Unknown polisnab operation: {operation}"})

    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# Main entry point
# ==========================================================================


def main():
    """Run the MCP server on stdio transport."""
    import logging
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )

    # Warm heavy backend imports (ezdxf → numpy/fontTools C-extensions) BEFORE
    # the stdio event loop starts. Importing them lazily from inside a request
    # handler deadlocks under the anyio stdio server on some environments
    # (observed: Windows + Python 3.14 + mcp 1.28). Pre-importing here makes the
    # later `from ... import EzdxfBackend` in get_backend() a no-op lookup.
    try:
        import autocad_mcp.backends.ezdxf_backend  # noqa: F401
        import autocad_mcp.backends.file_ipc  # noqa: F401
    except Exception as e:  # pragma: no cover - warming is best-effort
        log.warning("backend_prewarm_failed", error=str(e))

    log.info("autocad_mcp_starting", version="3.1.0")
    mcp.run(transport="stdio")
