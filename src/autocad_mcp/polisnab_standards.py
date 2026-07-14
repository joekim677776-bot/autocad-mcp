"""Polisnab drafting standards: layer set and dimension style (Phase 1).

Single source of truth for the Polisnab module-drawing standard defined in
PROJECT-BRIEF-autocad-mcp-polisnab.md (sections 4 and 5). Exposes two
orchestrators — setup_layers() and setup_dimstyle() — that push the standard
into the active drawing through whatever backend is in use (live AutoCAD via
file_ipc, or headless via ezdxf).

The tunable numbers live here in Python; the backends apply them. The
dimension-style numbers were calibrated for module drawings at 1000-6000 mm
scale (DIMSCALE=40 -> ~100 mm text/arrows on the model), after the earlier
diagnostic showed the stock Standard style renders illegibly at that scale.
"""

from __future__ import annotations

import math

from autocad_mcp.backends.base import AutoCADBackend, CommandResult

# ---------------------------------------------------------------------------
# Layer standard — section 4 of the brief.
# Each entry: name, ACI color code, linetype.
# ---------------------------------------------------------------------------
POLISNAB_LAYERS: list[dict] = [
    {"name": "AR-WALL",       "color": 7, "linetype": "Continuous"},  # wall outline
    {"name": "AR-WALL-INSUL", "color": 8, "linetype": "Continuous"},  # insulation hatch
    {"name": "AR-DOOR",       "color": 1, "linetype": "Continuous"},  # door openings
    {"name": "AR-WIND",       "color": 5, "linetype": "Continuous"},  # window openings
    {"name": "AR-VENT",       "color": 3, "linetype": "Continuous"},  # vent openings/ducts
    {"name": "AR-VESTIBULE",  "color": 6, "linetype": "Continuous"},  # airlock (Arctic series)
    {"name": "DIM",           "color": 2, "linetype": "Continuous"},  # dimension lines
    {"name": "TEXT",          "color": 7, "linetype": "Continuous"},  # text, specifications
    {"name": "AXIS",          "color": 4, "linetype": "Center"},      # centre/axis lines
    {"name": "TITLE-BLOCK",   "color": 7, "linetype": "Continuous"},  # sheet frame + stamp
    {"name": "HATCH-FLOOR",   "color": 9, "linetype": "Continuous"},  # floor hatch
    {"name": "FURN",          "color": 4, "linetype": "Continuous"},  # furniture / equipment symbols
]

# ---------------------------------------------------------------------------
# Dimension style — section 5 of the brief.
# Tunable numeric parameters; categorical settings (mm units, integer
# precision, text-above-line, closed-filled arrows) are fixed in the backends.
# ---------------------------------------------------------------------------
POLISNAB_DIMSTYLE: dict = {
    "name": "POLISNAB-DIM",
    "dimscale": 40.0,   # global scale for 1000-6000 mm module drawings
    "dimtxt": 2.5,      # text height (paper) -> ~100 mm on model at DIMSCALE 40
    "dimasz": 2.5,      # arrowhead size (paper) -> ~100 mm on model
    "dimexe": 1.25,     # extension-line overshoot past the dimension line
    "dimexo": 1.5,      # extension-line gap from the measured feature
}

# ---------------------------------------------------------------------------
# Title block — section 6 of the brief (GOST 2.104 form 1).
# The 185x55 mm frame + editable ATTDEF attributes are built by the backend
# (see the LISP handler / ezdxf backend). Only the fixed company name and the
# default geometric insert scale live here.
#
# block_scale is a geometric multiplier applied on insert: the stamp is drawn
# 1:1 in mm, so at block_scale=30 it is 5550x1650 mm on the model — a sensible
# proportion beside a 6000 mm module. Paper-space work would use block_scale=1.
# ---------------------------------------------------------------------------
POLISNAB_TITLE_BLOCK: dict = {
    "name": "TITLE-BLOCK",
    "width": 185.0,
    "height": 55.0,
    "default_block_scale": 30.0,
    "company_name": "ООО «ПОЛИСНАБ»",
}


def _layers_to_str(layers: list[dict]) -> str:
    """Serialize the layer set to the ``name,color,linetype;...`` wire format
    consumed by the file_ipc LISP handler (same delimiter style as
    create-polyline's points_str)."""
    return ";".join(f"{l['name']},{l['color']},{l['linetype']}" for l in layers)


async def setup_layers(backend: AutoCADBackend) -> CommandResult:
    """Create the full Polisnab layer standard in the active drawing."""
    return await backend.polisnab_setup_layers(_layers_to_str(POLISNAB_LAYERS))


async def setup_dimstyle(backend: AutoCADBackend) -> CommandResult:
    """Create (or redefine) the POLISNAB-DIM dimension style and make it current."""
    d = POLISNAB_DIMSTYLE
    return await backend.polisnab_setup_dimstyle(
        d["name"], d["dimscale"], d["dimtxt"], d["dimasz"], d["dimexe"], d["dimexo"],
    )


async def insert_title_block(
    backend: AutoCADBackend,
    *,
    doc_number: str,
    product_name: str,
    scale: str,
    sheet_num: int = 1,
    sheet_total: int = 1,
    developed_by: str = "",
    checked_by: str = "",
    approved_by: str = "",
    litera: str | None = None,
    company_name: str | None = None,
    block_scale: float | None = None,
) -> CommandResult:
    """Insert the GOST 2.104 form 1 title block and fill its attributes.

    The block is placed at the bottom-right of the current drawing extents.
    ``scale`` is the drawing-scale text shown in the stamp (e.g. "1:50");
    ``block_scale`` is the geometric size multiplier of the stamp itself.
    """
    company = company_name if company_name is not None else POLISNAB_TITLE_BLOCK["company_name"]
    bscale = block_scale if block_scale is not None else POLISNAB_TITLE_BLOCK["default_block_scale"]
    return await backend.polisnab_insert_title_block(
        doc_number=doc_number,
        product_name=product_name,
        scale=scale,
        sheet_num=sheet_num,
        sheet_total=sheet_total,
        developed_by=developed_by,
        checked_by=checked_by,
        approved_by=approved_by,
        litera=litera,
        company_name=company,
        block_scale=bscale,
    )


# ===========================================================================
# Parametric node library — section 7 of the brief (Phase 2 continued).
#
# These are Python generators: they emit geometry through the existing
# low-level primitives (create_line / create_polyline / create_circle) so they
# work unchanged on BOTH backends (live AutoCAD via file_ipc, headless ezdxf).
# Curved parts (door swings, basins) are drawn as sampled polylines so they are
# portable and rotate correctly without depending on ARC/ELLIPSE quirks.
#
# Openings (doors, windows) are positioned against a module outline via
# (wall_side, offset_mm). The module box defaults to the standard test module
# (6000x2400 mm at the origin); override via the module_* keyword arguments.
# Furniture symbols take an absolute centre (x_mm, y_mm) and a rotation.
# ===========================================================================

AR_WALL_LAYER = "AR-WALL"
AR_DOOR_LAYER = "AR-DOOR"
AR_WIND_LAYER = "AR-WIND"
FURN_LAYER = "FURN"
TEXT_LAYER = "TEXT"

# Opening tag ("D1"/"W1") text height on the model. Small relative to a
# 6000 mm module but legible at zoom-extents; matches the ~100-200 mm range
# the POLISNAB-DIM style produces (DIMTXT 2.5 x DIMSCALE 40 = 100 mm).
LABEL_HEIGHT = 200.0

DEFAULT_MODULE: dict = {
    "origin": (0.0, 0.0),
    "length": 6000.0,
    "width": 2400.0,
    "wall_thickness": 100.0,
}

_SIDE_ALIASES = {
    "s": "S", "south": "S", "bottom": "S", "b": "S",
    "n": "N", "north": "N", "top": "N", "t": "N",
    "w": "W", "west": "W", "left": "W", "l": "W",
    "e": "E", "east": "E", "right": "E", "r": "E",
}


def _wall_geometry(wall_side: str, origin, length: float, width: float):
    """Resolve a wall to (start_point, along_unit, inward_normal, wall_length).

    offset_mm is measured from ``start_point`` along ``along_unit``. Walls run
    left→right (S, N) or bottom→top (W, E); the inward normal points into the room.
    """
    ox, oy = origin
    side = _SIDE_ALIASES.get(str(wall_side).strip().lower())
    if side == "S":
        return (ox, oy), (1.0, 0.0), (0.0, 1.0), length
    if side == "N":
        return (ox, oy + width), (1.0, 0.0), (0.0, -1.0), length
    if side == "W":
        return (ox, oy), (0.0, 1.0), (1.0, 0.0), width
    if side == "E":
        return (ox + length, oy), (0.0, 1.0), (-1.0, 0.0), width
    raise ValueError(f"Unknown wall_side: {wall_side!r} (use N/S/E/W)")


def _place(pts, cx: float, cy: float, deg: float):
    """Map centre-local points to world coordinates: translate to (cx,cy) after
    rotating by ``deg`` about the local origin."""
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + px * ca - py * sa, cy + px * sa + py * ca) for px, py in pts]


def _rect_local(cx: float, cy: float, w: float, h: float):
    """Axis-aligned rectangle corners centred on (cx, cy) in local space."""
    return [
        (cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
        (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2),
    ]


def _oval_local(cx: float, cy: float, rx: float, ry: float, n: int = 32):
    """Closed ellipse sampled into n points (local space)."""
    return [
        (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _arc_points(cx: float, cy: float, r: float, start_deg: float, end_deg: float, n: int = 18):
    """Sample a CCW arc from start_deg to end_deg (world space, open polyline)."""
    sweep = (end_deg - start_deg) % 360.0
    if sweep == 0.0:
        sweep = 360.0
    out = []
    for i in range(n + 1):
        ang = math.radians(start_deg + sweep * i / n)
        out.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return out


class _Draw:
    """Collects primitive calls for one generator, tracking handles + first error."""

    def __init__(self, backend: AutoCADBackend, default_layer: str):
        self.b = backend
        self.layer = default_layer
        self.handles: list[str] = []
        self.error: str | None = None

    def _track(self, r: CommandResult):
        if not r.ok:
            if self.error is None:
                self.error = r.error
        elif isinstance(r.payload, dict) and r.payload.get("handle"):
            self.handles.append(r.payload["handle"])

    async def line(self, x1, y1, x2, y2, layer=None):
        self._track(await self.b.create_line(x1, y1, x2, y2, layer or self.layer))

    async def poly(self, pts, closed=True, layer=None):
        self._track(await self.b.create_polyline(pts, closed, layer or self.layer))

    async def circle(self, cx, cy, r, layer=None):
        self._track(await self.b.create_circle(cx, cy, r, layer or self.layer))

    async def mtext(self, x, y, text, height=LABEL_HEIGHT, layer=None):
        # width=0 -> auto (no wrap), so short tags stay on one line.
        self._track(await self.b.create_mtext(x, y, 0.0, text, height, layer or self.layer))

    def result(self, **extra) -> CommandResult:
        payload = {"count": len(self.handles), "handles": self.handles}
        payload.update(extra)
        return CommandResult(ok=self.error is None, payload=payload, error=self.error)


async def _label_opening(d, label, p1, p2, nx, ny, *, height=LABEL_HEIGHT):
    """Place an MTEXT tag (e.g. "D1"/"W1") on the TEXT layer just outside the
    opening. n is the inward normal, so -n (outward) keeps the tag clear of the
    wall and of any door swing arc. No-op for a blank/None label."""
    if not label:
        return
    mx = (p1[0] + p2[0]) / 2.0
    my = (p1[1] + p2[1]) / 2.0
    off = height + 100.0                 # sit clear of the wall face
    await d.mtext(mx - nx * off, my - ny * off, str(label),
                  height=height, layer=TEXT_LAYER)


async def _draw_door(
    backend, *, wall_side, offset_mm, width_mm, swing, layer, label=None,
    module_origin, module_length, module_width, wall_thickness,
):
    (sx, sy), (ux, uy), (nx, ny), _ = _wall_geometry(
        wall_side, module_origin, module_length, module_width)
    p1 = (sx + ux * offset_mm, sy + uy * offset_mm)
    p2 = (sx + ux * (offset_mm + width_mm), sy + uy * (offset_mm + width_mm))
    inward = str(swing).strip().lower() in ("in", "inside", "internal", "i")
    s = 1.0 if inward else -1.0
    ld = (nx * s, ny * s)                      # leaf swing direction
    tip = (p1[0] + ld[0] * width_mm, p1[1] + ld[1] * width_mm)

    d = _Draw(backend, layer)
    # Jamb reveals across the wall thickness at each side of the opening.
    t = wall_thickness
    for p in (p1, p2):
        await d.line(p[0] - nx * t / 2, p[1] - ny * t / 2,
                     p[0] + nx * t / 2, p[1] + ny * t / 2)
    # Door leaf (hinged at p1).
    await d.line(p1[0], p1[1], tip[0], tip[1])
    # 90-degree swing arc from the leaf tip to the far jamb (p2).
    a_u = math.degrees(math.atan2(uy, ux)) % 360.0
    a_l = math.degrees(math.atan2(ld[1], ld[0])) % 360.0
    if abs(((a_u - a_l) % 360.0) - 90.0) < 1.0:
        sa, ea = a_l, a_u
    else:
        sa, ea = a_u, a_l
    await d.poly(_arc_points(p1[0], p1[1], width_mm, sa, ea), closed=False)
    await _label_opening(d, label, p1, p2, nx, ny)
    return d.result(wall_side=str(wall_side), opening=[list(p1), list(p2)])


async def insert_exterior_door(
    backend: AutoCADBackend, wall_side: str, offset_mm: float,
    width_mm: float = 1000.0, swing: str = "in", *, label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
) -> CommandResult:
    """Exterior door: wall opening + leaf + swing arc (radius = width) on AR-DOOR.
    Optional ``label`` (e.g. "D1") is placed as MTEXT on the TEXT layer."""
    m = DEFAULT_MODULE
    return await _draw_door(
        backend, wall_side=wall_side, offset_mm=offset_mm, width_mm=width_mm,
        swing=swing, layer=AR_DOOR_LAYER, label=label,
        module_origin=module_origin or m["origin"],
        module_length=module_length or m["length"],
        module_width=module_width or m["width"],
        wall_thickness=wall_thickness or m["wall_thickness"],
    )


async def insert_interior_door(
    backend: AutoCADBackend, wall_side: str, offset_mm: float,
    width_mm: float = 800.0, swing_direction: str = "in", *, label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
) -> CommandResult:
    """Interior door: same door symbol (narrower default) on AR-DOOR.
    Optional ``label`` (e.g. "D2") is placed as MTEXT on the TEXT layer."""
    m = DEFAULT_MODULE
    return await _draw_door(
        backend, wall_side=wall_side, offset_mm=offset_mm, width_mm=width_mm,
        swing=swing_direction, layer=AR_DOOR_LAYER, label=label,
        module_origin=module_origin or m["origin"],
        module_length=module_length or m["length"],
        module_width=module_width or m["width"],
        wall_thickness=wall_thickness or m["wall_thickness"],
    )


async def insert_window(
    backend: AutoCADBackend, wall_side: str, offset_mm: float, width_mm: float, *,
    label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
) -> CommandResult:
    """Window: wall opening (jambs) + double glazing line on AR-WIND.
    Optional ``label`` (e.g. "W1") is placed as MTEXT on the TEXT layer."""
    m = DEFAULT_MODULE
    origin = module_origin or m["origin"]
    length = module_length or m["length"]
    width = module_width or m["width"]
    t = wall_thickness or m["wall_thickness"]
    (sx, sy), (ux, uy), (nx, ny), _ = _wall_geometry(wall_side, origin, length, width)
    p1 = (sx + ux * offset_mm, sy + uy * offset_mm)
    p2 = (sx + ux * (offset_mm + width_mm), sy + uy * (offset_mm + width_mm))

    d = _Draw(backend, AR_WIND_LAYER)
    # Jambs across the wall thickness.
    for p in (p1, p2):
        await d.line(p[0] - nx * t / 2, p[1] - ny * t / 2,
                     p[0] + nx * t / 2, p[1] + ny * t / 2)
    # Double glazing line: two lines parallel to the wall, offset +/- t/4.
    g = t / 4.0
    for sgn in (1.0, -1.0):
        await d.line(p1[0] + nx * g * sgn, p1[1] + ny * g * sgn,
                     p2[0] + nx * g * sgn, p2[1] + ny * g * sgn)
    await _label_opening(d, label, p1, p2, nx, ny)
    return d.result(wall_side=str(wall_side), opening=[list(p1), list(p2)])


async def insert_bed(
    backend: AutoCADBackend, x_mm: float, y_mm: float,
    rotation_deg: float = 0.0, bed_type: str = "single",
) -> CommandResult:
    """Bed plan symbol on FURN. 900x2000 (single) or 1400x2000 (double), centred
    on (x_mm, y_mm); the headboard strip sits at the local +Y (head) end."""
    double = str(bed_type).strip().lower() in ("double", "dbl", "2", "d")
    w = 1400.0 if double else 900.0
    length = 2000.0
    hb = 140.0  # headboard depth

    def place(pts):
        return _place(pts, x_mm, y_mm, rotation_deg)

    d = _Draw(backend, FURN_LAYER)
    await d.poly(place(_rect_local(0.0, 0.0, w, length)))          # mattress
    await d.poly(place(_rect_local(0.0, length / 2 - hb / 2, w, hb)))  # headboard strip
    # Pillow(s), just below the headboard.
    pcy = length / 2 - hb - 30 - 175
    if double:
        for cx in (-w / 4, w / 4):
            await d.poly(place(_rect_local(cx, pcy, w / 2 - 120, 350)))
    else:
        await d.poly(place(_rect_local(0.0, pcy, w - 240, 350)))
    return d.result(bed_type="double" if double else "single")


async def insert_toilet(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
) -> CommandResult:
    """Toilet plan symbol on FURN: cistern (tank) + bowl, footprint ~370x650 mm,
    centred on (x_mm, y_mm); the cistern is at the local +Y (wall) end."""
    def place(pts):
        return _place(pts, x_mm, y_mm, rotation_deg)

    d = _Draw(backend, FURN_LAYER)
    await d.poly(place(_rect_local(0.0, 250.0, 370.0, 150.0)))     # cistern (y 175..325)
    await d.poly(place(_oval_local(0.0, -75.0, 155.0, 250.0, 32)))  # bowl (y -325..175)
    await d.poly(place(_oval_local(0.0, -75.0, 110.0, 190.0, 32)))  # seat opening
    return d.result(footprint=[370.0, 650.0])


async def insert_sink(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
) -> CommandResult:
    """Sink plan symbol on FURN: basin outline + bowl + drain + tap, ~500x400 mm,
    centred on (x_mm, y_mm); the tap sits at the local +Y (wall) end."""
    def place(pts):
        return _place(pts, x_mm, y_mm, rotation_deg)

    def pt(px, py):
        return _place([(px, py)], x_mm, y_mm, rotation_deg)[0]

    d = _Draw(backend, FURN_LAYER)
    await d.poly(place(_rect_local(0.0, 0.0, 500.0, 400.0)))        # basin outline
    await d.poly(place(_oval_local(0.0, -20.0, 190.0, 120.0, 32)))  # bowl
    drain = pt(0.0, -20.0)
    await d.circle(drain[0], drain[1], 18.0)                        # drain
    tap = pt(0.0, 150.0)
    await d.circle(tap[0], tap[1], 22.0)                            # tap
    return d.result(footprint=[500.0, 400.0])
