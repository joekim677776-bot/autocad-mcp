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
    {"name": "AR-DOOR",       "color": 7, "linetype": "Continuous"},  # door openings (ACI 7: B/W for PDF)
    {"name": "AR-WIND",       "color": 7, "linetype": "Continuous"},  # window openings (ACI 7: B/W for PDF)
    {"name": "AR-VENT",       "color": 3, "linetype": "Continuous"},  # vent openings/ducts
    {"name": "AR-VESTIBULE",  "color": 6, "linetype": "Continuous"},  # airlock (Arctic series)
    {"name": "DIM",           "color": 2, "linetype": "Continuous"},  # dimension lines
    {"name": "TEXT",          "color": 7, "linetype": "Continuous"},  # text, specifications
    {"name": "AXIS",          "color": 4, "linetype": "Center"},      # centre/axis lines
    {"name": "TITLE-BLOCK",   "color": 7, "linetype": "Continuous"},  # sheet frame + stamp
    {"name": "HATCH-FLOOR",   "color": 9, "linetype": "Continuous"},  # floor hatch
    {"name": "FURN",          "color": 7, "linetype": "Continuous"},  # furniture / equipment symbols (ACI 7: B/W for PDF)
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
AR_WALL_INSUL_LAYER = "AR-WALL-INSUL"
AR_DOOR_LAYER = "AR-DOOR"
AR_WIND_LAYER = "AR-WIND"
FURN_LAYER = "FURN"
TEXT_LAYER = "TEXT"

# Opening tag ("ВХОД"/"ОКНО") text height on the model. Kept close to the
# dimension text so stroke weights match: POLISNAB-DIM renders ~100 mm text
# (DIMTXT 2.5 x DIMSCALE 40), so 150 mm labels sit ~1.5x that — legible but not
# overbearing when several tags share a sheet.
LABEL_HEIGHT = 150.0

DEFAULT_MODULE: dict = {
    "origin": (0.0, 0.0),
    "length": 6000.0,
    "width": 2400.0,
    "wall_thickness": 75.0,  # Standard series; matches draw_module_outline default
}

# Door leaf plan thickness (mm) — the leaf is drawn as an outline rectangle (no
# fill), so it reads with real thickness like the reference drawings.
DOOR_LEAF_THICKNESS = 45.0

# Dashed linetype for door swing arcs (the open-trajectory line is dashed on the
# reference). Defined model-scaled via entmake in the LISP backend (see
# polisnab-ensure-dash-ltype) — NOT via -LINETYPE _LOAD, which sidesteps the
# FILEDIA dialog trap entirely, same as the CENTER linetype.
SWING_LINETYPE = "POLISNAB-DASH"

# Exterior-wall band thickness by insulation series (mm) — section 4 of the
# brief: AR-WALL-INSUL is the 150 mm Arctic / 75 mm Standard insulated envelope.
# The wall is drawn as two parallel faces this far apart, filled solid grey.
INSULATION_THICKNESS: dict[str, float] = {
    "standard": 75.0,
    "arctic": 150.0,
}
DEFAULT_SERIES = "standard"


def _resolve_wall_thickness(series, wall_thickness_mm) -> float:
    """Resolve the wall-band thickness: an explicit ``wall_thickness_mm`` wins,
    otherwise the insulation thickness for ``series`` (default Standard 75 mm)."""
    if wall_thickness_mm is not None:
        return float(wall_thickness_mm)
    key = str(series or DEFAULT_SERIES).strip().lower()
    if key not in INSULATION_THICKNESS:
        raise ValueError(
            f"Unknown series {series!r} (use {'/'.join(INSULATION_THICKNESS)})")
    return INSULATION_THICKNESS[key]

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


def _side_offset_geom(side, ox, oy, L, W, t):
    """Per-side geometry in *offset coordinates* along the wall run.

    Returns (start_outer_point, along_unit, inward_normal, side_length,
    (fill_lo, fill_hi)). Offset 0 is the outer corner where the run starts;
    depth 0 is the outer face, depth ``t`` the inner face. The outer face spans
    offset [0, side_length]; the inner face spans [t, side_length - t] (it stops
    short at the corners). The fill band spans the full length on S/N but only
    [t, side_length - t] on W/E, so the four corners are covered exactly once
    (S/N own them) — same rule as the un-cut outline.
    """
    (sx, sy), (ux, uy), (nx, ny), Ls = _wall_geometry(side, (ox, oy), L, W)
    key = _SIDE_ALIASES.get(str(side).strip().lower())
    fill = (0.0, Ls) if key in ("S", "N") else (t, Ls - t)
    return (sx, sy), (ux, uy), (nx, ny), Ls, fill


def _subtract_gaps(lo, hi, gaps):
    """Cover [lo, hi] minus each (a, b) gap → list of (s, e) sub-intervals."""
    intervals = [(lo, hi)]
    for ga, gb in gaps:
        a, b = (ga, gb) if ga <= gb else (gb, ga)
        out = []
        for s, e in intervals:
            if b <= s or a >= e:
                out.append((s, e))
                continue
            if a > s:
                out.append((s, a))
            if b < e:
                out.append((b, e))
        intervals = out
    return [(s, e) for s, e in intervals if e - s > 1e-6]


async def _draw_wall_side(d, side, ox, oy, L, W, t, gaps):
    """Draw one thick-wall side (outer + inner faces on AR-WALL, SOLID fill on
    AR-WALL-INSUL) with ``gaps`` (list of (a, b) offset spans) cut out, plus a
    jamb line across the thickness at each gap edge. Shared by the outline
    generator and the door/opening cutters so the corner logic lives in one place."""
    (sx, sy), (ux, uy), (nx, ny), Ls, (fill_lo, fill_hi) = _side_offset_geom(
        side, ox, oy, L, W, t)

    def P(off, depth):
        return (sx + ux * off + nx * depth, sy + uy * off + ny * depth)

    for s, e in _subtract_gaps(0.0, Ls, gaps):            # outer face
        a, b = P(s, 0.0), P(e, 0.0)
        await d.line(a[0], a[1], b[0], b[1], AR_WALL_LAYER)
    for s, e in _subtract_gaps(t, Ls - t, gaps):          # inner face
        a, b = P(s, t), P(e, t)
        await d.line(a[0], a[1], b[0], b[1], AR_WALL_LAYER)
    for s, e in _subtract_gaps(fill_lo, fill_hi, gaps):   # grey body
        await d.solid_band([P(s, 0.0), P(e, 0.0), P(e, t), P(s, t)], AR_WALL_INSUL_LAYER)
    for ga, gb in gaps:                                   # jambs
        for off in (ga, gb):
            if -1e-6 <= off <= Ls + 1e-6:
                a, b = P(off, 0.0), P(off, t)
                await d.line(a[0], a[1], b[0], b[1], AR_WALL_LAYER)


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

    async def poly(self, pts, closed=True, layer=None, linetype=None):
        self._track(await self.b.create_polyline(pts, closed, layer or self.layer, linetype))

    async def solid_band(self, pts, layer=None):
        """Fill a 4-corner quad (CCW order) with a solid grey 2D SOLID — the wall
        body. Uses an entmake'd SOLID (deterministic, no hatch dialog/boundary
        detection); the crisp faces are drawn separately as lines/polylines."""
        self._track(await self.b.create_solid(pts, layer or self.layer))

    async def circle(self, cx, cy, r, layer=None):
        self._track(await self.b.create_circle(cx, cy, r, layer or self.layer))

    async def erase_window(self, x1, y1, x2, y2):
        """Erase wall entities overlapping the rectangle (for cutting openings).
        The backend selection is view-independent (whole-database bbox test), so
        this reliably removes the wall band under an opening regardless of the
        current zoom."""
        self._track(await self.b.erase_window(x1, y1, x2, y2))

    async def mtext(self, x, y, text, height=LABEL_HEIGHT, layer=None):
        # width=0 -> auto (no wrap), so short tags stay on one line.
        self._track(await self.b.create_mtext(x, y, 0.0, text, height, layer or self.layer))

    def result(self, **extra) -> CommandResult:
        payload = {"count": len(self.handles), "handles": self.handles}
        payload.update(extra)
        return CommandResult(ok=self.error is None, payload=payload, error=self.error)


async def draw_module_outline(
    backend: AutoCADBackend,
    length_mm=None, width_mm=None, wall_thickness_mm=None,
    *, series=None, origin=None, openings=None,
) -> CommandResult:
    """Module envelope drawn as a REAL wall: outer + inner faces on AR-WALL with
    a SOLID grey fill between them on AR-WALL-INSUL — the double-line + hatch
    style of the reference drawings, not a single thin contour line.

    ``length_mm`` x ``width_mm`` is the OUTER envelope (default 6000x2400); the
    inner face is inset inward by the band thickness. Thickness comes from the
    insulation series (Standard 75 mm / Arctic 150 mm) unless ``wall_thickness_mm``
    overrides it.

    ``openings`` (optional) is a list of ``{wall_side, offset_mm, width_mm}``
    dicts; each cuts a hole through the full wall thickness (both faces + fill)
    with jambs at its edges. This is the composable way to place several doors/
    windows at once. A door/window inserted later onto an already-drawn wall
    cuts its own hole via erase-and-redraw instead (see insert_exterior_door).
    """
    m = DEFAULT_MODULE
    ox, oy = origin if origin is not None else m["origin"]
    ox, oy = float(ox), float(oy)
    L = float(length_mm if length_mm is not None else m["length"])
    W = float(width_mm if width_mm is not None else m["width"])
    t = _resolve_wall_thickness(series, wall_thickness_mm)

    by_side: dict[str, list] = {"S": [], "N": [], "W": [], "E": []}
    for op in (openings or []):
        key = _SIDE_ALIASES.get(str(op["wall_side"]).strip().lower())
        if key is None:
            raise ValueError(f"Unknown wall_side in openings: {op.get('wall_side')!r}")
        a = float(op["offset_mm"])
        by_side[key].append((a, a + float(op["width_mm"])))

    d = _Draw(backend, AR_WALL_LAYER)
    for side in ("S", "N", "W", "E"):
        await _draw_wall_side(d, side, ox, oy, L, W, t, by_side[side])

    outer = [(ox, oy), (ox + L, oy), (ox + L, oy + W), (ox, oy + W)]
    inner = [(ox + t, oy + t), (ox + L - t, oy + t),
             (ox + L - t, oy + W - t), (ox + t, oy + W - t)]
    return d.result(
        outer=[list(p) for p in outer], inner=[list(p) for p in inner],
        wall_thickness=t, series=str(series or DEFAULT_SERIES).strip().lower(),
    )


async def insert_interior_wall(
    backend: AutoCADBackend, start_point, end_point, thickness_mm: float = 75.0,
) -> CommandResult:
    """Interior partition between two arbitrary points, in the same thick-wall
    style: a SOLID grey band on AR-WALL-INSUL centred on the start->end axis with
    the two long faces as lines on AR-WALL.

    ``thickness_mm`` is the full wall width; the band straddles the axis by
    +/- thickness/2. The ends butt flush to the given endpoints (place them on
    the inner faces of the enclosing walls so the partition merges cleanly).
    """
    x1, y1 = float(start_point[0]), float(start_point[1])
    x2, y2 = float(end_point[0]), float(end_point[1])
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length == 0.0:
        return CommandResult(ok=False, error="insert_interior_wall: start and end coincide")
    # Unit normal to the wall axis; offset each face by half the thickness.
    px, py = -dy / length, dx / length
    h = float(thickness_mm) / 2.0
    s1 = (x1 + px * h, y1 + py * h)
    e1 = (x2 + px * h, y2 + py * h)
    e2 = (x2 - px * h, y2 - py * h)
    s2 = (x1 - px * h, y1 - py * h)

    d = _Draw(backend, AR_WALL_LAYER)
    await d.solid_band([s1, e1, e2, s2], AR_WALL_INSUL_LAYER)
    await d.line(s1[0], s1[1], e1[0], e1[1], AR_WALL_LAYER)
    await d.line(s2[0], s2[1], e2[0], e2[1], AR_WALL_LAYER)
    return d.result(
        start=[x1, y1], end=[x2, y2], thickness=float(thickness_mm),
        faces=[[list(s1), list(e1)], [list(s2), list(e2)]],
    )


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
    """Door in a thick (two-face + fill) wall. Cuts the opening through BOTH
    faces and the grey fill by erasing the affected wall side over the opening
    and redrawing it split (with jambs), then draws the leaf as a thin filled
    rectangle and the 90 deg swing arc on AR-DOOR.

    NOTE: the cut redraws the whole affected wall side, so it composes across
    different sides but NOT with a second opening independently inserted on the
    SAME side — for several openings on one side, pass them together via
    draw_module_outline(openings=[...])."""
    ox, oy = module_origin
    L, W, t = module_length, module_width, wall_thickness
    (sx, sy), (ux, uy), (nx, ny), Ls, _ = _side_offset_geom(wall_side, ox, oy, L, W, t)
    a = float(offset_mm)
    w = float(width_mm)
    b = a + w

    def P(off, depth):
        return (sx + ux * off + nx * depth, sy + uy * off + ny * depth)

    p1, p2 = P(a, 0.0), P(b, 0.0)             # opening edges on the outer face

    d = _Draw(backend, layer)
    # 1) Cut the wall: erase this side's band over the opening, then redraw it
    #    split around the opening (redraw also lays the jambs). The erase box
    #    reaches from just outside the outer face to exactly the inner face — it
    #    does NOT dip into the room, so it won't grab an interior partition that
    #    only abuts the inner face elsewhere on the same wall.
    pad = 1.0
    c1, c2 = P(a - pad, -pad), P(b + pad, t)
    await d.erase_window(min(c1[0], c2[0]), min(c1[1], c2[1]),
                         max(c1[0], c2[0]), max(c1[1], c2[1]))
    await _draw_wall_side(d, wall_side, ox, oy, L, W, t, [(a, b)])

    # 2) Door leaf + swing arc on AR-DOOR. Inward swing hinges on the inner face
    #    and opens into the room; outward hinges on the outer face.
    inward = str(swing).strip().lower() in ("in", "inside", "internal", "i")
    sdir = (nx, ny) if inward else (-nx, -ny)
    hinge = P(a, t) if inward else P(a, 0.0)
    lt = DOOR_LEAF_THICKNESS
    # Thin rectangle: thickness lt along the wall (+u, toward the far jamb),
    # length w along the swing direction.
    A = hinge
    B = (A[0] + ux * lt, A[1] + uy * lt)
    C = (B[0] + sdir[0] * w, B[1] + sdir[1] * w)
    D = (A[0] + sdir[0] * w, A[1] + sdir[1] * w)
    await d.poly([A, B, C, D], closed=True, layer=AR_DOOR_LAYER)
    # 90 deg swing arc: centre at hinge, radius = leaf width, from the open tip
    # (along sdir) round to the closed position (along the wall, +u).
    a_u = math.degrees(math.atan2(uy, ux)) % 360.0
    a_l = math.degrees(math.atan2(sdir[1], sdir[0])) % 360.0
    if abs(((a_u - a_l) % 360.0) - 90.0) < 1.0:
        sa, ea = a_l, a_u
    else:
        sa, ea = a_u, a_l
    await d.poly(_arc_points(hinge[0], hinge[1], w, sa, ea), closed=False,
                 layer=AR_DOOR_LAYER, linetype=SWING_LINETYPE)

    await _label_opening(d, label, p1, p2, nx, ny)
    return d.result(wall_side=str(wall_side), opening=[list(p1), list(p2)])


async def insert_exterior_door(
    backend: AutoCADBackend, wall_side: str, offset_mm: float,
    width_mm: float = 950.0, swing: str = "out", *, label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
) -> CommandResult:
    """Exterior (entrance) door: cuts the thick-wall opening (both faces + fill +
    jambs) and draws the leaf + 90 deg swing arc (radius = width) on AR-DOOR.

    Real leaf size is 950 x 2070 mm; only the width matters in a 2D plan — the
    2070 mm height is for a future section/elevation. Default swing is "out"
    (entrances open outward, per the reference). Optional ``label`` (e.g. "D1")
    is placed as MTEXT on the TEXT layer."""
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
    width_mm: float = 840.0, swing_direction: str = "in", *, label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
) -> CommandResult:
    """Interior (room) door: same cut + leaf + swing arc symbol on AR-DOOR,
    narrower default.

    Real leaf size is 840 x 2035 mm; only the width matters in a 2D plan — the
    2035 mm height is for a future section/elevation. Default swing is "in"
    (room doors open into the room). Optional ``label`` (e.g. "D2") is placed as
    MTEXT on the TEXT layer."""
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
    """Window in a thick (two-face + fill) wall. Same cut as a door — the opening
    is cut through BOTH faces and the grey fill (erase + redraw the side split,
    with jambs) — but instead of a leaf/swing it draws the glazing as a double
    line running the length of the opening, centred in the wall thickness, on
    AR-WIND. Pass ``label`` (e.g. "ОКНО") to tag it on the TEXT layer.

    Standard window sizes (width x height mm): 925 x 1200 and 1120 x 1100. As
    with doors, only the width is used in the 2D plan — the height is for a
    future section/elevation. ``width_mm`` is passed explicitly (no default).

    Windows have no opening trajectory, so no swing arc / dashed linetype here.

    Cut limitation is the same as the door: it redraws the whole affected wall
    side, so it composes across DIFFERENT sides but not with a second opening
    independently inserted on the SAME side (use draw_module_outline(openings=…)
    for several on one side)."""
    m = DEFAULT_MODULE
    ox, oy = module_origin or m["origin"]
    L = module_length or m["length"]
    W = module_width or m["width"]
    t = wall_thickness or m["wall_thickness"]
    (sx, sy), (ux, uy), (nx, ny), Ls, _ = _side_offset_geom(wall_side, ox, oy, L, W, t)
    a = float(offset_mm)
    w = float(width_mm)
    b = a + w

    def P(off, depth):
        return (sx + ux * off + nx * depth, sy + uy * off + ny * depth)

    p1, p2 = P(a, 0.0), P(b, 0.0)             # opening edges on the outer face

    d = _Draw(backend, AR_WIND_LAYER)
    # 1) Cut the wall (both faces + fill + jambs) — identical to the door. The
    #    erase box stops at the inner face (no room intrusion).
    pad = 1.0
    c1, c2 = P(a - pad, -pad), P(b + pad, t)
    await d.erase_window(min(c1[0], c2[0]), min(c1[1], c2[1]),
                         max(c1[0], c2[0]), max(c1[1], c2[1]))
    await _draw_wall_side(d, wall_side, ox, oy, L, W, t, [(a, b)])

    # 2) Glazing: a double line across the opening, centred in the thickness.
    g = t / 6.0
    for depth in (t / 2.0 - g, t / 2.0 + g):
        s, e = P(a, depth), P(b, depth)
        await d.line(s[0], s[1], e[0], e[1], AR_WIND_LAYER)

    await _label_opening(d, label, p1, p2, nx, ny)
    return d.result(wall_side=str(wall_side), opening=[list(p1), list(p2)])


async def insert_bed(
    backend: AutoCADBackend, x_mm: float, y_mm: float,
    rotation_deg: float = 0.0, bed_type: str = "single",
) -> CommandResult:
    """Bed plan symbol on FURN, matching the reference pictogram: outer frame +
    an inset inner rectangle (the turned-down blanket) + pillow(s) at the head
    with diagonally-folded (chamfered) inner corners. 900x2000 (single) or
    1400x2000 (double), centred on (x_mm, y_mm); the head sits at local +Y."""
    double = str(bed_type).strip().lower() in ("double", "dbl", "2", "d")
    w = 1400.0 if double else 900.0
    length = 2000.0
    half_len = length / 2.0

    def place(pts):
        return _place(pts, x_mm, y_mm, rotation_deg)

    # Pillow band at the head (+Y); blanket fills from the foot up to just below it.
    m = 70.0                       # side / foot inset of the blanket
    pillow_gap = 40.0              # gap from head edge to pillow
    pillow_depth = 340.0
    chamf = 90.0                   # folded-corner chamfer on the pillow
    pillow_top = half_len - pillow_gap
    pillow_bot = pillow_top - pillow_depth
    blanket_top = pillow_bot - 60.0
    blanket_bot = -half_len + m

    d = _Draw(backend, FURN_LAYER)
    await d.poly(place(_rect_local(0.0, 0.0, w, length)))          # outer frame
    # Blanket: inner rectangle, inset from the frame on the two sides + foot.
    bcy = (blanket_top + blanket_bot) / 2.0
    await d.poly(place(_rect_local(0.0, bcy, w - 2 * m, blanket_top - blanket_bot)))
    # Pillow(s) with chamfered foot-facing corners (the folded pillow look).
    centers = (-w / 4.0, w / 4.0) if double else (0.0,)
    pw = (w / 2.0 - 90.0) if double else (w - 2 * m)
    for cx in centers:
        left, right = cx - pw / 2.0, cx + pw / 2.0
        await d.poly(place([
            (left, pillow_top), (right, pillow_top),
            (right, pillow_bot + chamf), (right - chamf, pillow_bot),
            (left + chamf, pillow_bot), (left, pillow_bot + chamf),
        ]))
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


async def insert_locker_row(
    backend: AutoCADBackend, wall_side: str, offset_mm: float,
    cell_width_mm: float = 600.0, depth_mm: float = 420.0, count: int = 5,
    *, label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
) -> CommandResult:
    """Row of ``count`` adjacent locker cells along a wall, on FURN. Each cell is
    ``cell_width_mm`` wide (along the wall) x ``depth_mm`` deep (into the room),
    marked with a locker-door glyph: two leaves hinged at the two ends of the open
    (room-facing) edge, swung into the room at different angles.

    Default cell size 600 (along wall) x 420 (deep) mm; the lower leaf opens 25 deg
    off the front edge (length 0.60x the cell width), the upper leaf opens 15 deg and
    is shortened so its tip sits at the SAME along-wall level as the lower tip — two
    separate lines (no closed "V"/triangle, no crossing).
    Optional group ``label`` (e.g. "ЛОКЕРЫ") is drawn once, MC-centred in front of
    the whole row, same height/style as opening tags.

    All coordinates cross the file_ipc boundary through create_line/create_polyline,
    which already apply _fmt_coord — no sci-notation/FP-noise.
    """
    m = DEFAULT_MODULE
    ox, oy = module_origin or m["origin"]
    L = module_length or m["length"]
    W = module_width or m["width"]
    t = wall_thickness or m["wall_thickness"]
    (sx, sy), (ux, uy), (nx, ny), _Ls, _ = _side_offset_geom(wall_side, ox, oy, L, W, t)
    cw = float(cell_width_mm)
    dp = float(depth_mm)
    n = int(count)

    def P(off, depth):
        return (sx + ux * off + nx * depth, sy + uy * off + ny * depth)

    back = t             # inner wall face (cell back)
    front = t + dp       # cell front, into the room
    panel_d = 40.0       # double-line: thin back-panel strip inset from the wall
    lbl_clear = cw * 0.4          # label stand-off in front of the row

    d = _Draw(backend, FURN_LAYER)
    for i in range(n):
        a = offset_mm + i * cw
        b = a + cw
        await d.poly([P(a, back), P(b, back), P(b, front), P(a, front)],
                     closed=True, layer=FURN_LAYER)          # cell outline (no fill)
        # Second thin line by the wall face -> the double line (end plate).
        pa, pb = P(a, back + panel_d), P(b, back + panel_d)
        await d.line(pa[0], pa[1], pb[0], pb[1], FURN_LAYER)
        # Locker-door glyph: two leaves hinged at the two ends of the open (room-
        # facing) edge, swung into the room at different angles. The lower leaf opens
        # LEAF_BOT_DEG (longer); the upper leaf opens LEAF_TOP_DEG and is shortened so
        # its tip sits at the SAME along-wall level as the lower tip -> two separate
        # lines, no closed "V"/triangle and no crossing.
        #
        # Built in the cell's OWN local frame, then mapped to world through the SAME
        # P() offset/normal transform that places the rectangle corners
        # (P(a, back) == cell(lx=0, ly=0); P(b, front) == cell(lx=dp, ly=cw)):
        #   local_x: 0 = wall-adjacent face, dp = open room-facing edge -> depth = t + lx
        #   local_y: 0..cw along the wall                               -> off   = a  + ly
        LEAF_BOT_DEG, LEAF_TOP_DEG, LEAF_BOT_FRAC = 25.0, 15.0, 0.60
        def cell(lx, ly):
            return P(a + ly, t + lx)
        b_ang, t_ang = math.radians(LEAF_BOT_DEG), math.radians(LEAF_TOP_DEG)
        l1 = LEAF_BOT_FRAC * cw                 # lower leaf length
        tip_ly = l1 * math.cos(b_ang)           # along-wall level shared by both tips
        l2 = (cw - tip_ly) / math.cos(t_ang)    # upper leaf, shortened to that level
        h_bot   = cell(dp, 0.0)                              # lower hinge (bottom of open edge)
        h_top   = cell(dp, cw)                               # upper hinge (top of open edge)
        tip_bot = cell(dp + l1 * math.sin(b_ang), tip_ly)    # lower leaf tip (deeper, 25 deg)
        tip_top = cell(dp + l2 * math.sin(t_ang), tip_ly)    # upper leaf tip (shallower, 15 deg)
        await d.line(h_bot[0], h_bot[1], tip_bot[0], tip_bot[1], FURN_LAYER)
        await d.line(h_top[0], h_top[1], tip_top[0], tip_top[1], FURN_LAYER)

    if label:
        lbl_h = LABEL_HEIGHT / 2.0                           # 75 mm for the locker tag
        row_mid = offset_mm + n * cw / 2.0
        lp = P(row_mid, front + lbl_clear + lbl_h + 150.0)   # clear of the row
        await d.mtext(lp[0], lp[1], str(label), height=lbl_h, layer=TEXT_LAYER)

    return d.result(wall_side=str(wall_side), count=n, cell_width=cw, depth=dp)
