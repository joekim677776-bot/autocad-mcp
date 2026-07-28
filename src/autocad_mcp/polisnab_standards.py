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

# Thickness (mm) of a PARTY WALL — the single wall shared by two neighbouring
# rooms of one row (Phase 4b step 3), as opposed to the insulated envelope.
#
# Why NOT the series thickness (arctic 150 / standard 75):
#   * both sides are HEATED rooms, so the thermal job the envelope exists for
#     (150 mm of mineral wool against a -50 C outdoor design temperature) simply
#     does not apply here. Building it at 150 is paying for insulation between
#     +20 C and +20 C, and on the drawing it reads as an exterior wall;
#   * but it is not the 75 mm partition of insert_interior_wall either. That one
#     closes a санузел off INSIDE one dwelling — one occupant, nothing to
#     separate acoustically. This wall stands between two SLEEPING rooms of
#     DIFFERENT residents, which is an acoustic + fire-compartment boundary;
#   * 100 mm is the standard inter-room sandwich/frame panel of modular
#     buildings and the next size up in the 50/80/100/150/200 panel range.
#
# HONESTY NOTE, same status as CORRIDOR_WIDTH: this is an engineering DEFAULT,
# not a verified norm. СП 51.13330 asks for Rw >= 50 dB between rooms of
# different residential cells and a 100 mm panel has NOT been checked against
# that here. Verify (or specify a certified panel) before any real submission —
# the number is a parameter precisely so it can be raised without a code change.
PARTY_WALL_THICKNESS = 100.0

# --- Sanitary block: WC cubicle sizing -------------------------------------
# Derived, not copied from a catalogue. The chain, so a reviewer can attack any
# single link rather than the number:
#
#   clear width 850  СП 44.13330 (bytovye buildings) gives a cubicle of
#                    1200 x 800 mm. 800 is the MINIMUM; 850 spends 50 mm to buy
#                    240 mm either side of our 370 mm toilet instead of 215 —
#                    shoulder and knee room, the difference between "passes"
#                    and "comfortable".
#   partition    50  a WC cubicle divider is an HPL/laminate panel with fixings,
#                    not the 75 mm room partition insert_interior_wall draws by
#                    default. Using 75 here would silently claim a stud wall
#                    between two toilets.
#   PITCH       900  850 + 50. This is what the requester estimated, and the
#                    derivation lands on it from below rather than being fitted
#                    to it.
#   clear depth 1200 the same СП figure. Our toilet is 650 deep, so 550 mm is
#                    left to stand up and turn in.
#   door width   700  850 clear less the jambs.
#   AISLE      1300  700 + 600. A cubicle door opens OUTWARD (so a collapsed
#                    occupant cannot block it) and therefore sweeps its full
#                    700 mm into the aisle; 600 mm is a person standing at a
#                    basin opposite. The aisle is not a round number someone
#                    liked - it is exactly "a cubicle door can open fully
#                    without touching someone washing their hands".
#
# HONESTY NOTE, same status as CORRIDOR_WIDTH and PARTY_WALL_THICKNESS: these
# are engineering defaults. СП 44.13330 was reasoned from, not verified against
# a current copy, and nothing here has been checked against the fire/evacuation
# clauses that govern sanitary rooms. Parameters, not constants, so they can be
# corrected without touching the layout code. See PROJECT-BRIEF §8a.
SANITARY_STALL_CLEAR_WIDTH = 850.0
SANITARY_STALL_CLEAR_DEPTH = 1200.0
SANITARY_PARTITION_THICKNESS = 50.0
SANITARY_STALL_DOOR_WIDTH = 700.0
SANITARY_BASIN_STAND = 600.0
SANITARY_SINK_PITCH = 700.0
SANITARY_SINK_DEPTH = 400.0        # must track insert_sink (500 x 400)


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


# The two walls that meet a given side at the start / end of its run. S and N run
# W->E, so they land on the W then the E wall; W and E run S->N, so they land on
# the S then the N wall. Used to inset each side correctly at its corners when
# the four walls are NOT all the same thickness.
_SIDE_CORNERS = {"S": ("W", "E"), "N": ("W", "E"), "W": ("S", "N"), "E": ("S", "N")}


def _corner_thickness(side_key, t, side_thickness):
    """Thicknesses of the two walls meeting ``side_key``'s run at its start and
    end corners. Falls back to ``t`` for anything ``side_thickness`` does not
    name — i.e. the uniform-envelope behaviour."""
    a, b = _SIDE_CORNERS[side_key]
    st = side_thickness or {}
    return (float(st.get(a, t)), float(st.get(b, t)))


def _side_offset_geom(side, ox, oy, L, W, t, side_thickness=None):
    """Per-side geometry in *offset coordinates* along the wall run.

    Returns (start_outer_point, along_unit, inward_normal, side_length,
    (fill_lo, fill_hi), (t_start, t_end)). Offset 0 is the outer corner where the
    run starts; depth 0 is the outer face, depth ``t`` the inner face. The outer
    face spans offset [0, side_length]; the inner face stops short at the corners
    by the thickness of the wall it meets there — [t_start, side_length - t_end].
    The fill band spans the full length on S/N but only [t_start, side_length -
    t_end] on W/E, so the four corners are covered exactly once (S/N own them) —
    same rule as the un-cut outline.

    ``side_thickness`` optionally maps "S"/"N"/"W"/"E" -> that wall's thickness,
    for a module whose walls are NOT all the same: a PARTY wall shared with the
    neighbouring room is thinner than the insulated envelope, and a wall the
    neighbour owns outright is not drawn at all (thickness 0, so this side runs
    clean through to its outer corner there). Omit it and every corner inset is
    ``t`` — byte-identical to the uniform-wall behaviour.
    """
    (sx, sy), (ux, uy), (nx, ny), Ls = _wall_geometry(side, (ox, oy), L, W)
    key = _SIDE_ALIASES.get(str(side).strip().lower())
    t0, t1 = _corner_thickness(key, t, side_thickness)
    fill = (0.0, Ls) if key in ("S", "N") else (t0, Ls - t1)
    return (sx, sy), (ux, uy), (nx, ny), Ls, fill, (t0, t1)


def _free_runs(lo, hi, band_lo, band_hi, obstacles, axis):
    """Stretches of [lo, hi] along ``axis`` that nothing already occupies, for a
    strip whose perpendicular extent is [band_lo, band_hi].

    ``axis`` is 0 for X, 1 for Y. ``obstacles`` is a list of (name, AABB) — the
    same boxes and swing sectors the collision families are fed, which is the
    point: furniture is PLACED against the geometry the checks will judge it by,
    instead of being placed by a formula and judged afterwards. Before this, a
    bed row tiled from the far wall on the assumption that its frontage was free
    and only found out from the swing check that the entrance was standing in
    it.

    Touching counts as free (a bed may sit flush against a locker bank); only a
    real overlap of the perpendicular band takes frontage out."""
    taken = []
    for _, b in obstacles:
        p0, p1 = (b[0], b[2]) if axis == 0 else (b[1], b[3])
        q0, q1 = (b[1], b[3]) if axis == 0 else (b[0], b[2])
        if q1 > band_lo + 1e-6 and q0 < band_hi - 1e-6:
            taken.append((p0, p1))
    return _subtract_gaps(lo, hi, taken)


def _resolve_bed_axis(flag, ix0, iy0, ix1, iy1):
    """Resolve ``bed_axis`` to "x" or "y" — the world axis beds are laid
    head-to-toe along, and therefore the axis the two bed rows run parallel to.

    "auto" picks the room's LONG axis, because that is what the arrangement has
    always assumed: beds lie along the long walls with the aisle between them.
    The derivation is deliberately reported back in the payload rather than left
    implicit — a caller that swaps length_mm and width_mm expecting the layout to
    follow deserves to see whether it did."""
    key = str(flag).strip().lower()
    if key in ("x", "y"):
        return key
    if key in ("auto", "none", ""):
        return "x" if (ix1 - ix0) >= (iy1 - iy0) else "y"
    raise ValueError(f"bed_axis={flag!r}: use 'auto', 'x' or 'y'")


def _wall_band_aabb(side, ox, oy, L, W, t):
    """World AABB of one wall band of a module envelope, at its FULL outer extent
    (corners included — the neighbouring sides overlap it there, which is what a
    corner is). Published per drawn side so a room that declines to draw a
    boundary can be checked against the band the neighbour really drew."""
    key = _SIDE_ALIASES.get(str(side).strip().lower())
    if key == "S":
        return (ox, oy, ox + L, oy + t)
    if key == "N":
        return (ox, oy + W - t, ox + L, oy + W)
    if key == "W":
        return (ox, oy, ox + t, oy + W)
    if key == "E":
        return (ox + L - t, oy, ox + L, oy + W)
    raise ValueError(f"Unknown wall_side: {side!r} (use N/S/E/W)")


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


async def _draw_wall_side(d, side, ox, oy, L, W, t, gaps, side_thickness=None):
    """Draw one thick-wall side (outer + inner faces on AR-WALL, SOLID fill on
    AR-WALL-INSUL) with ``gaps`` (list of (a, b) offset spans) cut out, plus a
    jamb line across the thickness at each gap edge. Shared by the outline
    generator and the door/opening cutters so the corner logic lives in one place.

    ``t`` is THIS side's own thickness; ``side_thickness`` (see _side_offset_geom)
    supplies the neighbouring sides' thicknesses so the corner insets are right on
    a module with mixed walls. The opening cutters must pass the SAME mapping the
    outline was drawn with — they erase and redraw the whole side, so a mismatch
    would rebuild that wall with the wrong corners."""
    (sx, sy), (ux, uy), (nx, ny), Ls, (fill_lo, fill_hi), (t0, t1) = _side_offset_geom(
        side, ox, oy, L, W, t, side_thickness)

    def P(off, depth):
        return (sx + ux * off + nx * depth, sy + uy * off + ny * depth)

    for s, e in _subtract_gaps(0.0, Ls, gaps):            # outer face
        a, b = P(s, 0.0), P(e, 0.0)
        await d.line(a[0], a[1], b[0], b[1], AR_WALL_LAYER)
    for s, e in _subtract_gaps(t0, Ls - t1, gaps):        # inner face
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


def _pts_aabb(pts):
    """World axis-aligned bounding box (minx, miny, maxx, maxy) of a point list."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _rot_aabb(cx, cy, w, h, deg):
    """AABB of a w(local X) x h(local Y) rectangle centred at (cx, cy) rotated by
    ``deg``. Correct for any angle (min/max over the rotated corners), so it also
    handles the ±90/180 placements used by the room generators."""
    return _pts_aabb(_place(_rect_local(0.0, 0.0, w, h), cx, cy, deg))


def _opening_aabb(P, a, b, t):
    """AABB of a wall opening (door/window): the hole spans [a, b] along the wall
    and the full thickness [0, t]. ``P(off, depth)`` is the wall's local->world map
    (as built inside the opening cutters)."""
    return _pts_aabb([P(a, 0.0), P(b, 0.0), P(a, t), P(b, t)])


def _wall_row_aabb(wall_side, ox, oy, L, W, t, off_lo, off_hi, dep_lo, dep_hi):
    """AABB of a wall-hugging footprint expressed in a wall's (offset, depth)
    frame — used to predict a locker row's footprint the same way insert_locker_row
    places it (depth measured from the OUTER face; a row sits at depth [t, t+d])."""
    (sx, sy), (ux, uy), (nx, ny), _Ls, _, _ = _side_offset_geom(wall_side, ox, oy, L, W, t)
    def P(off, dep):
        return (sx + ux * off + nx * dep, sy + uy * off + ny * dep)
    return _pts_aabb([P(off_lo, dep_lo), P(off_hi, dep_lo),
                      P(off_hi, dep_hi), P(off_lo, dep_hi)])


# Estimated glyph advance as a fraction of the text height, used to predict an
# MTEXT label's footprint without asking AutoCAD to measure it. The dispatcher
# draws labels with `_J _MC` (middle-centre attachment, see mcp-cmd-create-mtext),
# so the insertion point is the CENTRE of the text, not a corner.
#
# 0.7 is a deliberate over-estimate for the Arial-ish POLISNAB-TB style (real
# average advance is nearer 0.55-0.6 for Cyrillic caps). Over-estimating is the
# safe direction: a label box that is slightly too wide reports a near-miss as a
# clash, which a human then dismisses; too narrow silently hides a real overlap.
LABEL_CHAR_WIDTH_FRAC = 0.7


def _text_aabb(x: float, y: float, text: str, height: float):
    """Approximate world AABB of an MC-anchored MTEXT label.

    Width is estimated from the character count (no font metrics are available
    across the file_ipc boundary); height is the nominal text height. Single-line
    only — every label this module draws is a short tag with width=0 (no wrap)."""
    w = len(str(text)) * LABEL_CHAR_WIDTH_FRAC * float(height)
    h = float(height)
    return (x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0)


def _sector_aabb(cx: float, cy: float, r: float, start_deg: float, end_deg: float):
    """AABB of the circular SECTOR swept by a door leaf: the hinge point plus the
    arc it traces. Not the arc's AABB alone — the hinge corner is part of the
    swept region and is what makes the box meet the wall.

    Sampled through the same _arc_points used to draw the swing, so the predicted
    zone and the drawn arc can never disagree about where the door goes."""
    return _pts_aabb([(cx, cy)] + _arc_points(cx, cy, r, start_deg, end_deg))


def _boxes_overlap(a, b, tol: float = 1e-6):
    """Overlap extent (dx, dy) of two AABBs, or None if they only touch/miss.
    One definition of "overlapping" shared by every collision test below."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = min(ax1, bx1) - max(ax0, bx0)
    dy = min(ay1, by1) - max(ay0, by0)
    if dx > tol and dy > tol:
        return (dx, dy)
    return None


def _oval_local(cx: float, cy: float, rx: float, ry: float, n: int = 32):
    """Closed ellipse sampled into n points (local space)."""
    return [
        (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _rrect_local(cx: float, cy: float, w: float, h: float, r: float, n: int = 6):
    """Rounded-rectangle outline centred on (cx, cy) in local space, sampled into
    a closed polyline. ``r`` is clamped to half the shorter side."""
    r = min(float(r), w / 2.0, h / 2.0)
    ax, ay = w / 2.0 - r, h / 2.0 - r          # corner-arc centres, from the centre
    out = []
    # Corners CCW from bottom-right; each arc sweeps 90 deg.
    for ox, oy, start in ((ax, -ay, 270.0), (ax, ay, 0.0), (-ax, ay, 90.0), (-ax, -ay, 180.0)):
        for i in range(n + 1):
            a = math.radians(start + 90.0 * i / n)
            out.append((cx + ox + r * math.cos(a), cy + oy + r * math.sin(a)))
    return out


# Cabinet-front door glyph geometry — the "домик" (roof) icon solved for the
# locker row and reused verbatim by the wardrobe: two leaves hinged at the two
# ends of the open (room-facing) edge, swung into the room at different angles,
# their tips meeting at the SAME along-wall level -> two separate lines, no
# closed "V"/triangle and no crossing. No swing arc (deliberate: at cabinet
# scale an arc reads as clutter, and the reference drawings omit it).
LEAF_BOT_DEG, LEAF_TOP_DEG, LEAF_BOT_FRAC = 25.0, 15.0, 0.60
CABINET_PANEL_DEPTH = 40.0     # back-panel strip inset from the wall -> double line


async def _cabinet_cell(d, cell, cw: float, dp: float, layer: str = FURN_LAYER):
    """Draw one cabinet cell + its door glyph through the caller's ``cell(lx, ly)``
    mapping, which takes cell-local coordinates to world:

      lx: 0 = wall-adjacent back face .. dp = open, room-facing front edge
      ly: 0 .. cw along the wall

    Shared by insert_locker_row (wall-anchored, lx/ly mapped through the wall's
    offset/normal frame) and insert_wardrobe (free-placed, mapped through
    _place). Keeping ONE implementation is the point: the leaf angles below were
    tuned against the reference and must not drift apart between the two callers.
    """
    await d.poly([cell(0.0, 0.0), cell(0.0, cw), cell(dp, cw), cell(dp, 0.0)],
                 closed=True, layer=layer)                  # cell outline (no fill)
    # Second thin line by the wall face -> the double line (end plate).
    pa, pb = cell(CABINET_PANEL_DEPTH, 0.0), cell(CABINET_PANEL_DEPTH, cw)
    await d.line(pa[0], pa[1], pb[0], pb[1], layer)

    b_ang, t_ang = math.radians(LEAF_BOT_DEG), math.radians(LEAF_TOP_DEG)
    l1 = LEAF_BOT_FRAC * cw                 # lower leaf length
    tip_ly = l1 * math.cos(b_ang)           # along-wall level shared by both tips
    l2 = (cw - tip_ly) / math.cos(t_ang)    # upper leaf, shortened to that level
    h_bot   = cell(dp, 0.0)                              # lower hinge (bottom of open edge)
    h_top   = cell(dp, cw)                               # upper hinge (top of open edge)
    tip_bot = cell(dp + l1 * math.sin(b_ang), tip_ly)    # lower leaf tip (deeper, 25 deg)
    tip_top = cell(dp + l2 * math.sin(t_ang), tip_ly)    # upper leaf tip (shallower, 15 deg)
    await d.line(h_bot[0], h_bot[1], tip_bot[0], tip_bot[1], layer)
    await d.line(h_top[0], h_top[1], tip_top[0], tip_top[1], layer)


# ---------------------------------------------------------------------------
# Sanitary-ware silhouettes, measured pixel-by-pixel off the "Санузел" zone of
# reference/reference-studio-module-layout.png rather than eyeballed. Each table
# is (depth_fraction, half_width_fraction), where depth_fraction runs 0..1 from
# the wall end of the bowl to its front tip, and half_width_fraction is relative
# to the FULL fixture width. Straight from the reference rows:
#   cistern spans x 1019..1059 (41 px) -> the full width;
#   bowl    spans y  262..307  (45 px), widths 32 -> 30 -> 36 -> 0;
#   seat    spans y  269..305  (36 px), widths  0 -> 29 -> 0.
# The bowl deliberately starts at 0.390 (not 0): it meets the cistern along 78%
# of the cistern's width, which is what stops it reading as two loose shapes.
TOILET_BOWL_PROFILE = [
    (0.000, 0.390), (0.089, 0.366), (0.200, 0.366), (0.289, 0.415),
    (0.400, 0.439), (0.511, 0.439), (0.622, 0.427), (0.733, 0.390),
    (0.844, 0.329), (0.911, 0.280), (0.956, 0.232),
    # The reference's last rows (w 19 -> 17 -> closed within 2 px) are quantisation,
    # not a cone: round the tip off rather than interpolating them straight to zero.
    (0.980, 0.185), (0.993, 0.115), (1.000, 0.000),
]
TOILET_SEAT_PROFILE = [
    (0.156, 0.000), (0.167, 0.150), (0.178, 0.220), (0.289, 0.305),
    (0.400, 0.341), (0.511, 0.354), (0.622, 0.341), (0.733, 0.305),
    (0.844, 0.220), (0.911, 0.134), (0.945, 0.075), (0.967, 0.000),
]


def _profile_at(profile, d: float) -> float:
    """Linear-interpolate a (depth_fraction, half_width_fraction) table at ``d``."""
    if d <= profile[0][0]:
        return profile[0][1]
    if d >= profile[-1][0]:
        return profile[-1][1]
    for (d0, w0), (d1, w1) in zip(profile, profile[1:]):
        if d0 <= d <= d1:
            t = 0.0 if d1 == d0 else (d - d0) / (d1 - d0)
            return w0 + (w1 - w0) * t
    return 0.0


def _egg_local(cx: float, cy_top: float, width: float, depth: float, profile,
               n: int = 48):
    """Closed egg outline in local space, built from a measured ``profile``.

    Grows downward (-Y) from ``cy_top`` for ``depth``; ``width`` is the reference
    width the profile's half-widths are fractions of. Sampled down the right side
    then back up the left, so a zero half-width at either end closes to a tip.
    """
    d_lo, d_hi = profile[0][0], profile[-1][0]
    right, left = [], []
    for i in range(n + 1):
        d = d_lo + (d_hi - d_lo) * i / n
        hw = _profile_at(profile, d) * width
        y = cy_top - d * depth
        right.append((cx + hw, y))
        left.append((cx - hw, y))
    pts = right + list(reversed(left))
    # Drop consecutive duplicates (the tips, where both sides meet at hw == 0).
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    return out


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
        # Axis-aligned bounding box [minx, miny, maxx, maxy] over the drawn
        # PHYSICAL geometry. Labels (mtext) and selection windows (erase_window)
        # are excluded — they are annotations / operations, not footprint. Used
        # by _Compose to auto-detect element collisions after assembly.
        self.bbox: list[float] | None = None
        # Label footprints, tracked SEPARATELY from self.bbox on purpose. Folding
        # a tag into its element's bbox would inflate that element (the ЛОКЕРЫ tag
        # stands well clear of the row it names) and produce phantom furniture
        # collisions. They are their own boxes, checked in their own right.
        self.label_boxes: list[tuple[str, tuple]] = []

    def _grow(self, pts):
        for x, y in pts:
            if self.bbox is None:
                self.bbox = [x, y, x, y]
            else:
                if x < self.bbox[0]: self.bbox[0] = x
                if y < self.bbox[1]: self.bbox[1] = y
                if x > self.bbox[2]: self.bbox[2] = x
                if y > self.bbox[3]: self.bbox[3] = y

    def _track(self, r: CommandResult):
        if not r.ok:
            if self.error is None:
                self.error = r.error
        elif isinstance(r.payload, dict) and r.payload.get("handle"):
            self.handles.append(r.payload["handle"])

    async def line(self, x1, y1, x2, y2, layer=None):
        self._grow([(x1, y1), (x2, y2)])
        self._track(await self.b.create_line(x1, y1, x2, y2, layer or self.layer))

    async def poly(self, pts, closed=True, layer=None, linetype=None):
        self._grow(pts)
        self._track(await self.b.create_polyline(pts, closed, layer or self.layer, linetype))

    async def solid_band(self, pts, layer=None):
        """Fill a 4-corner quad (CCW order) with a solid grey 2D SOLID — the wall
        body. Uses an entmake'd SOLID (deterministic, no hatch dialog/boundary
        detection); the crisp faces are drawn separately as lines/polylines."""
        self._grow(pts)
        self._track(await self.b.create_solid(pts, layer or self.layer))

    async def circle(self, cx, cy, r, layer=None):
        self._grow([(cx - r, cy - r), (cx + r, cy + r)])
        self._track(await self.b.create_circle(cx, cy, r, layer or self.layer))

    async def erase_window(self, x1, y1, x2, y2):
        """Erase wall entities overlapping the rectangle (for cutting openings).
        The backend selection is view-independent (whole-database bbox test), so
        this reliably removes the wall band under an opening regardless of the
        current zoom."""
        self._track(await self.b.erase_window(x1, y1, x2, y2))

    async def mtext(self, x, y, text, height=LABEL_HEIGHT, layer=None):
        # width=0 -> auto (no wrap), so short tags stay on one line.
        self.label_boxes.append((str(text), _text_aabb(x, y, text, height)))
        self._track(await self.b.create_mtext(x, y, 0.0, text, height, layer or self.layer))

    def result(self, **extra) -> CommandResult:
        bbox = tuple(self.bbox) if self.bbox is not None else None
        payload = {"count": len(self.handles), "handles": self.handles, "bbox": bbox,
                   "label_bboxes": [[t, list(b)] for t, b in self.label_boxes]}
        payload.update(extra)   # callers may override "bbox" (e.g. openings, whose
                                # drawn geometry is the whole wall side, not the hole)
        return CommandResult(ok=self.error is None, payload=payload, error=self.error)


async def draw_module_outline(
    backend: AutoCADBackend,
    length_mm=None, width_mm=None, wall_thickness_mm=None,
    *, series=None, origin=None, openings=None, side_thickness=None,
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

    ``side_thickness`` (optional) overrides the thickness PER SIDE, as a mapping
    "S"/"N"/"W"/"E" -> mm. Two values are special:
      * a thinner one — a PARTY wall shared with the room next door, which is an
        internal wall between two heated rooms and must not be built as the
        insulated envelope (see PARTY_WALL_THICKNESS);
      * ``0`` — that side is NOT DRAWN at all, because the neighbouring room
        already owns the wall there. Same rule insert_corridor states for its own
        sides: a boundary that belongs to somebody else is drawn once, by them.
        The other three sides then run clean through to that outer corner, so no
        stub of a missing wall is left behind.
    The envelope rectangle ``length_mm x width_mm`` is unchanged by any of this —
    only the band drawn inside it — so a row of rooms still tiles at pitch
    ``length_mm`` and the INNER face moves instead (see ``inner`` in the payload).

    The payload carries ``wall_bands``: ``[side, x0, y0, x1, y1]`` per side that
    was actually drawn. That is what lets a later room prove the neighbour really
    does own the wall it declined to draw (open-side check in _Compose).
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

    st = {k: float(v) for k, v in (side_thickness or {}).items()}
    ts = {side: st.get(side, t) for side in ("S", "N", "W", "E")}

    d = _Draw(backend, AR_WALL_LAYER)
    bands = []
    for side in ("S", "N", "W", "E"):
        if ts[side] <= 0.0:          # the neighbour owns this boundary
            continue
        await _draw_wall_side(d, side, ox, oy, L, W, ts[side], by_side[side], ts)
        bands.append([side, *_wall_band_aabb(side, ox, oy, L, W, ts[side])])

    outer = [(ox, oy), (ox + L, oy), (ox + L, oy + W), (ox, oy + W)]
    inner = [(ox + ts["W"], oy + ts["S"]), (ox + L - ts["E"], oy + ts["S"]),
             (ox + L - ts["E"], oy + W - ts["N"]), (ox + ts["W"], oy + W - ts["N"])]
    return d.result(
        outer=[list(p) for p in outer], inner=[list(p) for p in inner],
        wall_thickness=t, wall_bands=bands,
        side_thickness={k: ts[k] for k in ("S", "N", "W", "E")},
        series=str(series or DEFAULT_SERIES).strip().lower(),
    )


# Corridor clear width (mm). This is a WORKING DEFAULT, not a verified norm:
# it is what fits two people passing plus door leaves, and it makes the arithmetic
# of a двусторонняя scheme come out even. It has NOT been checked against СП/ГОСТ
# evacuation-width requirements — do that before any real submission.
CORRIDOR_WIDTH = 2500.0


def corridor_row_origins(corridor_origin_y: float, corridor_width_mm: float,
                         room_width_mm: float):
    """``origin_y`` for a room row on each side of a corridor, as
    (north_row_origin_y, south_row_origin_y).

    The rule both rows obey: a row's wall ON THE CORRIDOR SIDE must BE the
    corridor's boundary, not sit next to one. So the room's OUTER face lands
    exactly on the passage edge and nothing is drawn twice:

      * north row — its outer SOUTH face on the passage's north edge, so the
        SW corner is at ``corridor_origin_y + corridor_width``;
      * south row — its outer NORTH face on the passage's south edge, so the
        SW corner is ``room_width`` BELOW the passage: ``corridor_origin_y -
        room_width``.

    Note the asymmetry in the arithmetic: origin_y is the SW corner in both
    cases, so the north row adds the CORRIDOR width and the south row subtracts
    the ROOM width. Getting this wrong by a wall thickness is the easy mistake
    (the rooms would overlap the passage, or leave a sliver of dead space), and
    it is exactly what the clearance check would catch.
    """
    cy = float(corridor_origin_y)
    return (cy + float(corridor_width_mm), cy - float(room_width_mm))


def _end_wall_thickness(argname, flag, t, party_t) -> float:
    """Resolve a ``wall_west`` / ``wall_east`` flag to a thickness in mm:
    True -> the envelope ``t``, "party" -> ``party_t``, False -> 0 (not drawn)."""
    if flag is True:
        return float(t)
    if flag is False or flag is None:
        return 0.0
    key = str(flag).strip().lower()
    if key in ("party", "shared"):
        return float(party_t)
    if key in ("true", "envelope", "exterior", "outer"):
        return float(t)
    if key in ("false", "none", "no", "neighbour", "neighbor"):
        return 0.0
    raise ValueError(
        f"{argname}={flag!r}: use True (envelope wall), 'party' (shared with the "
        f"room next door) or False (the neighbour draws it)")


def room_row_walls(index: int, count: int) -> dict:
    """End-wall kwargs for room ``index`` of a row of ``count`` rooms tiled
    west→east, as ``{"wall_west": ..., "wall_east": ...}`` for
    generate_dormitory_room.

    The rule in one line: **a boundary between two rooms is drawn once, by the
    room to its west.** So every room draws its east wall — as the insulated
    envelope if it ends the row, as a party wall if a neighbour follows — and
    only the first room of the row draws a west wall, because only there is west
    the outside of the building.

    This exists so the off-by-one lives in ONE place. Written out at the call
    site it is three cases that all look plausible, and getting it wrong is
    silent in the drawing: an extra west wall reads as the double band this whole
    step removes, a missing one leaves the end room open to the weather (which is
    why open_side_violations exists to catch it).
    """
    n = int(count)
    i = int(index)
    if not (0 <= i < n):
        raise ValueError(f"room_row_walls: index {i} out of range for {n} room(s)")
    return {"wall_west": i == 0, "wall_east": True if i == n - 1 else "party"}


async def _draw_wall_band(d, x0, y0, x1, y1):
    """One straight thick-wall band given its two long edges: grey SOLID fill on
    AR-WALL-INSUL plus both faces as lines on AR-WALL. Same construction as
    draw_module_outline / insert_interior_wall, expressed here in absolute edge
    coordinates because a corridor wall is placed by its faces, not by an axis."""
    await d.solid_band([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], AR_WALL_INSUL_LAYER)
    await d.line(x0, y0, x1, y0, AR_WALL_LAYER)
    await d.line(x0, y1, x1, y1, AR_WALL_LAYER)


def _resolve_corridor_end(argname, flag):
    """Resolve an ``end_west`` / ``end_east`` flag to one of None / "wall" /
    "door"."""
    if flag is None or flag is False:
        return None
    key = str(flag).strip().lower()
    if key in ("wall", "solid", "blank", "true"):
        return "wall"
    if key in ("door", "exit", "opening"):
        return "door"
    if key in ("none", "false", "open"):
        return None
    raise ValueError(
        f"{argname}={flag!r}: use None/False (left open), 'wall' (solid) or "
        f"'door' (an opening with a leaf)")


async def _draw_corridor_end(d, mode, x0, x1, y_lo, y_hi, passage_lo, passage_hi,
                             door_w, outward):
    """Draw one end of a corridor across its width and report what it means to
    the checks.

    Returns (bands, swing, opening) where ``bands`` is the list of drawn wall
    AABBs, ``swing`` the leaf's swept AABB (or None) and ``opening`` the hole
    rect (or None).

    The leaf hinges on the OUTER face and opens AWAY from the corridor, because
    a door at the end of a circulation route is an exit and a door on an escape
    route opens in the direction of travel. It also keeps the swept sector out
    of the passage, which the clearance check would otherwise report — turn the
    leaf around and it fires, which is the check having teeth rather than the
    geometry being lucky."""
    if mode == "wall":
        await _draw_wall_band(d, x0, y_lo, x1, y_hi)
        return [(x0, y_lo, x1, y_hi)], None, None

    # "door": the opening is centred on the CLEAR passage, not on the band, so
    # it stays centred whether or not the side walls are drawn.
    mid = (passage_lo + passage_hi) / 2.0
    dw = min(float(door_w), passage_hi - passage_lo)
    d_lo, d_hi = mid - dw / 2.0, mid + dw / 2.0

    bands = []
    for lo, hi in ((y_lo, d_lo), (d_hi, y_hi)):
        if hi - lo > 1e-6:
            await _draw_wall_band(d, x0, lo, x1, hi)
            bands.append((x0, lo, x1, hi))
    for y in (d_lo, d_hi):                       # jambs across the thickness
        await d.line(x0, y, x1, y, AR_WALL_LAYER)

    hinge = (x1, d_lo) if outward[0] > 0 else (x0, d_lo)
    swing = await _draw_door_leaf(d, hinge, (0.0, 1.0), outward, dw)
    return bands, tuple(swing), (min(x0, x1), d_lo, max(x0, x1), d_hi)


async def insert_corridor(
    backend: AutoCADBackend,
    origin_x: float, origin_y: float, length_mm: float,
    width_mm: float = CORRIDOR_WIDTH, *, label: str | None = None,
    series=None, wall_thickness_mm=None,
    wall_south: bool = True, wall_north: bool = False,
    end_west=None, end_east=None, end_door_width_mm: float = 950.0,
) -> CommandResult:
    """A corridor segment running along +X, its CLEAR PASSAGE ``width_mm`` wide,
    with the SW corner of that passage at (origin_x, origin_y).

    Walls are drawn as REAL thick walls — outer + inner faces on AR-WALL with a
    grey SOLID fill between them on AR-WALL-INSUL — the same construction
    draw_module_outline and insert_interior_wall use. (Before 2026-07-24 they
    were zero-thickness lines; the corridor read as a caption in empty space.)

    WHICH SIDES GET A WALL. The rule is one line: **a side that has rooms
    against it gets no wall here**, because those rooms already carry their own
    wall and in a real building that wall IS the corridor's wall. Drawing one
    too would stack two bands face to face and read as a double wall.

      * single-loaded, rooms to the NORTH (Phase 4b step 1) — the defaults:
        ``wall_south=True`` (far side, drawn), ``wall_north=False``;
      * double-loaded, rooms BOTH sides (step 2) — ``wall_south=False,
        wall_north=False``. A double-loaded corridor owns NO walls at all: it is
        simply the space between two rows, and both boundaries belong to rooms;
      * a stretch with no rooms on a side — turn that side back on.

    ``width_mm`` is the CLEAR width (what matters for circulation), so the wall
    bands are drawn OUTSIDE it: the south band spans [origin_y - t, origin_y].
    The passage rectangle is therefore unchanged by adding walls, and so is the
    ``clearances`` zone — the collision layer built on top of it keeps working
    untouched.

    THICKNESS. Resolved like any envelope wall (``series``: arctic 150 /
    standard 75, or an explicit ``wall_thickness_mm``), NOT the 75 mm default of
    insert_interior_wall. The reasoning: for as long as this wall exists it is
    the OUTSIDE of the complex, and it needs the insulated envelope. It never
    becomes an interior partition — when the second row of rooms arrives on the
    far side, those rooms bring their own walls and this one is not drawn at all
    (wall_south=False). So it is either exterior, or it is gone. Step 2 confirmed
    this: going double-loaded removed the wall rather than thinning it.

    Use ``corridor_row_origins`` to place the rows; it encodes the arithmetic
    that keeps each row's corridor-side face exactly on the passage edge.

    ENDS DEFAULT TO OPEN, and that default has not changed. A cap across the end
    reads as a dead end, and a corridor must terminate in an exit, a stair or
    more corridor — none of which this generator can know. On a drawing whose
    subject is circulation, a blank wall across the escape route is worse than
    an unfinished edge. So the end is not something to "finish": it is something
    the caller states once the complex layout says what is really there.

    ``end_west`` / ``end_east`` say what that is, three states each:

        None / False  — not drawn (the default, and still the honest answer when
                        the end is where the corridor continues or where the
                        layout has not been decided);
        "wall"        — a solid band across the end, for a genuinely closed end;
        "door"        — the same band with a ``end_door_width_mm`` opening cut
                        into it plus a leaf, for an end that gives onto an exit.

    A door here hinges on the OUTER face and swings AWAY from the corridor: a
    door on an escape route opens in the direction of travel. It follows that
    the sector stays out of the passage; turn it around and the clearance check
    reports it.

    Sealing BOTH ends with "wall" and no door is reported in ``warnings`` — that
    is a corridor nobody can leave, and it is the one case where this generator
    will say something rather than draw what it was told in silence.

    Returns a payload in the same shape the room generators emit (boxes / labels /
    swings / clearances / origin), so it can be passed straight into their
    ``scene`` argument. The wall bands ARE registered as obstruction boxes now
    that they have real thickness.

    Runs along +X only; a N-S corridor is not supported yet.
    """
    cx, cy = float(origin_x), float(origin_y)
    L = float(length_mm)
    Wc = float(width_mm)
    t = _resolve_wall_thickness(series, wall_thickness_mm)

    ew = _resolve_corridor_end("end_west", end_west)
    ee = _resolve_corridor_end("end_east", end_east)

    d = _Draw(backend, AR_WALL_LAYER)
    boxes = []
    swings = []
    openings = []
    if wall_south:
        await _draw_wall_band(d, cx, cy - t, cx + L, cy)
        boxes.append(["insert_corridor:wall[S]", cx, cy - t, cx + L, cy])
    if wall_north:
        await _draw_wall_band(d, cx, cy + Wc, cx + L, cy + Wc + t)
        boxes.append(["insert_corridor:wall[N]", cx, cy + Wc, cx + L, cy + Wc + t])

    # Ends. The band sits OUTSIDE the clear length, exactly as the side walls sit
    # outside the clear width, so the passage rectangle (and the clearance zone
    # built on it) is the same whether the ends are drawn or not. It spans the
    # side-wall bands too where those exist, so the corner is covered once.
    y_lo = cy - (t if wall_south else 0.0)
    y_hi = cy + Wc + (t if wall_north else 0.0)
    for tag, mode, x0, x1, outward in (
            ("W", ew, cx - t, cx, (-1.0, 0.0)),
            ("E", ee, cx + L, cx + L + t, (1.0, 0.0))):
        if mode is None:
            continue
        bands, swing, opening = await _draw_corridor_end(
            d, mode, x0, x1, y_lo, y_hi, cy, cy + Wc, end_door_width_mm, outward)
        for i, b in enumerate(bands):
            suffix = f"[{tag}]" if len(bands) == 1 else f"[{tag}{i + 1}]"
            boxes.append([f"insert_corridor:end{suffix}", *b])
        if swing is not None:
            swings.append([f"insert_corridor:end_door[{tag}]", *swing])
        if opening is not None:
            openings.append([f"insert_corridor:end_opening[{tag}]", *opening])

    if label:
        await d.mtext(cx + L / 2.0, cy + Wc / 2.0, str(label),
                      height=LABEL_HEIGHT, layer=TEXT_LAYER)

    warnings = []
    if ew == "wall" and ee == "wall":
        warnings.append(
            "corridor is sealed at BOTH ends with no door - nobody can leave it. "
            "Give one end 'door', or leave it open until the layout says what is "
            "really there.")

    r = d.result(origin=[cx, cy], outer=[cx, cy, cx + L, cy + Wc],
                 length=L, width=Wc, wall_thickness=t,
                 ends={"W": ew, "E": ee}, warnings=warnings,
                 series=str(series or DEFAULT_SERIES).strip().lower())
    if r.ok:
        r.payload["boxes"] = boxes
        r.payload["labels"] = [[f"insert_corridor:label[{txt}]", *b]
                               for txt, b in d.label_boxes]
        # An end door's swept sector is published like any other swing, so a
        # leaf turned the wrong way shows up as a clearance violation instead of
        # quietly sweeping the passage.
        r.payload["swings"] = swings
        r.payload["openings"] = openings
        r.payload["clearances"] = [["insert_corridor:passage", cx, cy, cx + L, cy + Wc]]
        # Same evidence draw_module_outline publishes: a room that declines to
        # draw a boundary can check a corridor wall covers it (the corridor never
        # draws one where rooms stand, but the check must not depend on that).
        r.payload["wall_bands"] = [[n.rsplit("[", 1)[-1].rstrip("]"), *b]
                                   for n, *b in boxes]
    return r


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


async def insert_room_number(
    backend: AutoCADBackend, x_mm: float, y_mm: float, number, *,
    height: float = LABEL_HEIGHT,
) -> CommandResult:
    """Room number as its own MTEXT tag on the TEXT layer, at (x_mm, y_mm).

    ``number`` is whatever the caller says it is — "101", "1.2", "К-3". This
    generator never invents one: which room this is depends on the building, and
    only the caller assembling the building knows that.

    Deliberately NOT physical geometry: the payload carries a label_bbox and no
    ``bbox``, so the tag is checked as annotation (may not overprint anything,
    does not obstruct a door) rather than as an object. That is the same footing
    the ВХОД / ОКНО / ЛОКЕРЫ tags are on.
    """
    d = _Draw(backend, TEXT_LAYER)
    await d.mtext(float(x_mm), float(y_mm), str(number), height=float(height),
                  layer=TEXT_LAYER)
    return d.result(number=str(number), position=[float(x_mm), float(y_mm)])


def _room_number_position(ix0, iy0, ix1, iy1):
    """Where a room number goes: the centre of the room's CLEAR interior.

    The convention this follows is the ordinary architectural one — a room's
    number is set in the middle of the space it names, so which room it belongs
    to is unambiguous. Two alternatives were considered and rejected:

      * beside the entrance — ambiguous exactly where it matters. In a corridor
        scheme the doors of neighbouring rooms sit on the same wall a few metres
        apart, and a number drawn near a jamb reads as belonging to the corridor
        or to the room next door as easily as to this one;
      * in a free corner — unambiguous but arbitrary, and it moves as soon as
        the furniture changes.

    The centre is also where this layout has floor: the tag lands in the aisle
    between the bed rows (dormitory) or between the wardrobe and the desk
    (studio). It is NOT guaranteed free for an arbitrary layout, and that is the
    point of running it through the label check rather than hand-placing it.
    """
    return ((ix0 + ix1) / 2.0, (iy0 + iy1) / 2.0)


async def _draw_door_leaf(d, hinge, along_unit, swing_dir, width, open_deg=90.0):
    """Door leaf (thin filled rectangle) + swing arc on AR-DOOR.

    ``open_deg`` is how far open the LEAF IS DRAWN (default 90). It changes the
    glyph only - see the note on the return value.

    ``hinge`` is the hinge point; ``along_unit`` the unit vector along the wall
    toward the far jamb (the leaf's closed direction); ``swing_dir`` the unit
    vector the leaf opens toward. Shared by _draw_door (openings cut in the module
    envelope) and _draw_door_symbol (doors in interior partitions).

    RETURNS the AABB of the sector the leaf sweeps, so callers can publish the
    door's *clearance* requirement alongside its opening. The drawn arc and the
    returned zone come from the same two angles by construction."""
    ux, uy = along_unit
    sx, sy = swing_dir
    lt = DOOR_LEAF_THICKNESS
    w = float(width)
    a_u = math.degrees(math.atan2(uy, ux)) % 360.0
    a_s = math.degrees(math.atan2(sy, sx)) % 360.0
    # Which way round the leaf travels: +1 if the open direction is CCW from the
    # wall, -1 if CW. Everything below is expressed in terms of this so the
    # drawn angle can be anything between closed and fully open.
    turn = 1.0 if ((a_s - a_u) % 360.0) < 180.0 else -1.0
    ang = float(open_deg)
    a_leaf = a_u + turn * ang
    dx, dy = math.cos(math.radians(a_leaf)), math.sin(math.radians(a_leaf))
    # Leaf thickness runs perpendicular to the leaf, on the closed side.
    a_thick = math.radians(a_leaf - turn * 90.0)
    tx, ty = math.cos(a_thick), math.sin(a_thick)
    A = (hinge[0], hinge[1])
    B = (A[0] + tx * lt, A[1] + ty * lt)
    C = (B[0] + dx * w, B[1] + dy * w)
    D = (A[0] + dx * w, A[1] + dy * w)
    await d.poly([A, B, C, D], closed=True, layer=AR_DOOR_LAYER)
    # Swing arc: centre at hinge, radius = leaf width, from the closed position
    # (along the wall) round to the drawn open position.
    sa, ea = (a_u, a_leaf) if turn > 0 else (a_leaf, a_u)
    await d.poly(_arc_points(hinge[0], hinge[1], w, sa, ea), closed=False,
                 layer=AR_DOOR_LAYER, linetype=SWING_LINETYPE)
    # CLEARANCE IS NOT THE GLYPH. The returned sector is the FULL quarter turn,
    # whatever angle was drawn. Showing a leaf at 45 deg is a draughting
    # convention for a less cluttered plan; the door still physically travels
    # 90 deg, and the floor it needs must stay empty. Deriving the clearance
    # from open_deg instead would silently shrink every door's reserved space
    # the moment someone tidied the drawing - and the bed rows, which are laid
    # into the frontage these sectors leave free, would move in behind it.
    fa, fe = (a_u, a_u + 90.0) if turn > 0 else (a_u - 90.0, a_u)
    return _sector_aabb(hinge[0], hinge[1], w, fa % 360.0, fe % 360.0)


async def _draw_door_symbol(backend, hinge, along_unit, swing_dir, width_mm,
                            *, open_deg: float = 90.0):
    """Standalone door leaf + swing arc on AR-DOOR, hinged at ``hinge`` — for a
    door in an INTERIOR partition (which is drawn as two insert_interior_wall
    segments with the door opening as the gap between them; this only draws the
    leaf/arc symbol into that gap). ``along_unit`` points toward the far jamb,
    ``swing_dir`` is the side the leaf opens into."""
    d = _Draw(backend, AR_DOOR_LAYER)
    swing = await _draw_door_leaf(d, hinge, along_unit, swing_dir, width_mm,
                                  open_deg)
    return d.result(hinge=[float(hinge[0]), float(hinge[1])], width=float(width_mm),
                    swing_bbox=list(swing))


async def _draw_door(
    backend, *, wall_side, offset_mm, width_mm, swing, layer, label=None,
    module_origin, module_length, module_width, wall_thickness, side_thickness=None,
    open_deg=90.0,
):
    """Door in a thick (two-face + fill) wall. Cuts the opening through BOTH
    faces and the grey fill by erasing the affected wall side over the opening
    and redrawing it split (with jambs), then draws the leaf as a thin filled
    rectangle and the 90 deg swing arc on AR-DOOR.

    NOTE: the cut redraws the whole affected wall side, so it composes across
    different sides but NOT with a second opening independently inserted on the
    SAME side — for several openings on one side, pass them together via
    draw_module_outline(openings=[...]).

    ``side_thickness`` must be the SAME per-side mapping the outline was drawn
    with (see draw_module_outline): the redraw rebuilds the whole side, so
    without it a wall next to a party wall / an undrawn boundary comes back with
    the wrong corner insets."""
    ox, oy = module_origin
    L, W, t = module_length, module_width, wall_thickness
    (sx, sy), (ux, uy), (nx, ny), Ls, _, _ = _side_offset_geom(
        wall_side, ox, oy, L, W, t, side_thickness)
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
    await _draw_wall_side(d, wall_side, ox, oy, L, W, t, [(a, b)], side_thickness)

    # 2) Door leaf + swing arc on AR-DOOR. Inward swing hinges on the inner face
    #    and opens into the room; outward hinges on the outer face.
    inward = str(swing).strip().lower() in ("in", "inside", "internal", "i")
    sdir = (nx, ny) if inward else (-nx, -ny)
    hinge = P(a, t) if inward else P(a, 0.0)
    swing_box = await _draw_door_leaf(d, hinge, (ux, uy), sdir, w, open_deg)

    await _label_opening(d, label, p1, p2, nx, ny)
    # bbox stays the OPENING rect (the hole in the wall) — that is the door's
    # physical footprint. The swept sector is published separately as swing_bbox:
    # it is a CLEARANCE requirement, not geometry, so it must not be folded into
    # the footprint (that would make every door "collide" with its own room).
    return d.result(wall_side=str(wall_side), opening=[list(p1), list(p2)],
                    bbox=_opening_aabb(P, a, b, t), swing_bbox=list(swing_box))


async def insert_exterior_door(
    backend: AutoCADBackend, wall_side: str, offset_mm: float,
    width_mm: float = 950.0, swing: str = "out", *, label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
    side_thickness=None, open_deg: float = 45.0,
) -> CommandResult:
    """Exterior (entrance) door: cuts the thick-wall opening (both faces + fill +
    jambs) and draws the leaf + swing arc (radius = width) on AR-DOOR.

    ``open_deg`` is how far open the leaf is DRAWN - 45 by default, which keeps
    the entrance area readable on a plan. The clearance the door reserves is the
    full 90 deg quarter turn regardless; see _draw_door_leaf.

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
        side_thickness=side_thickness, open_deg=open_deg,
    )


async def insert_interior_door(
    backend: AutoCADBackend, wall_side: str, offset_mm: float,
    width_mm: float = 840.0, swing_direction: str = "in", *, label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
    side_thickness=None,
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
        side_thickness=side_thickness,
    )


async def insert_window(
    backend: AutoCADBackend, wall_side: str, offset_mm: float, width_mm: float, *,
    label: str | None = None,
    module_origin=None, module_length=None, module_width=None, wall_thickness=None,
    side_thickness=None,
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
    (sx, sy), (ux, uy), (nx, ny), Ls, _, _ = _side_offset_geom(
        wall_side, ox, oy, L, W, t, side_thickness)
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
    await _draw_wall_side(d, wall_side, ox, oy, L, W, t, [(a, b)], side_thickness)

    # 2) Glazing: a double line across the opening, centred in the thickness.
    g = t / 6.0
    for depth in (t / 2.0 - g, t / 2.0 + g):
        s, e = P(a, depth), P(b, depth)
        await d.line(s[0], s[1], e[0], e[1], AR_WIND_LAYER)

    await _label_opening(d, label, p1, p2, nx, ny)
    return d.result(wall_side=str(wall_side), opening=[list(p1), list(p2)],
                    bbox=_opening_aabb(P, a, b, t))


async def insert_bed(
    backend: AutoCADBackend, x_mm: float, y_mm: float,
    rotation_deg: float = 0.0, bed_type: str = "single",
) -> CommandResult:
    """Bed plan symbol on FURN, matching the reference pictogram: outer frame +
    an inset inner rectangle (the turned-down blanket) + pillow(s) at the head
    with diagonally-folded (chamfered) inner corners. 825x2000 (single) or
    1200x2000 (double), centred on (x_mm, y_mm); the head sits at local +Y.

    Single width is 825 mm (the real Polisnab single mattress). This is the
    corrected true size — NOT a leftover of the earlier 1200x825 mix-up; only the
    WIDTH changed 900->825, the length stays 2000. Double stays 1200x2000."""
    double = str(bed_type).strip().lower() in ("double", "dbl", "2", "d")
    w = 1200.0 if double else 825.0
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
    *, scale: float = 1.0,
) -> CommandResult:
    """Toilet plan symbol on FURN, matching the reference "Унитаз" pictogram: a
    rounded-rectangle cistern with a flush button, and an egg-shaped bowl drawn as
    a double outline (bowl + seat opening). 370x650 mm at scale=1.0, centred on
    (x_mm, y_mm); the cistern is at the local +Y (wall) end, so rotation_deg matches
    the wall the toilet backs onto (0 = wall to the north).

    ``scale`` shrinks/grows the WHOLE pictogram uniformly (every length ×scale) so
    the look is preserved exactly, only the size changes; footprint becomes
    370·scale × 650·scale. The back face then sits scale·325 mm off the centre, so a
    wall-mounted placement must set the centre at inner_face + 325·scale.

    The bowl silhouette comes from TOILET_BOWL_PROFILE / TOILET_SEAT_PROFILE, which
    are measured off the reference. It meets the cistern along a real width — an
    earlier version used a plain ellipse whose tip touched the cistern at a single
    point, which read as two unrelated shapes rather than a toilet."""
    s = float(scale)
    def place(pts):
        return _place(pts, x_mm, y_mm, rotation_deg)

    def pt(px, py):
        return _place([(px, py)], x_mm, y_mm, rotation_deg)[0]

    width, cist_d = 370.0 * s, 150.0 * s          # cistern spans y 175..325 (×s)
    bowl_top, bowl_d = 175.0 * s, 500.0 * s       # bowl spans y -325..175 (×s)
    cist_cy = bowl_top + cist_d / 2.0

    d = _Draw(backend, FURN_LAYER)
    await d.poly(place(_rrect_local(0.0, cist_cy, width, cist_d, 35.0 * s)))   # cistern
    btn = pt(0.0, bowl_top + 0.54 * cist_d)       # flush button, 46% down the cistern
    await d.circle(btn[0], btn[1], 18.0 * s)
    await d.poly(place(_egg_local(0.0, bowl_top, width, bowl_d, TOILET_BOWL_PROFILE)))
    await d.poly(place(_egg_local(0.0, bowl_top, width, bowl_d, TOILET_SEAT_PROFILE)))
    return d.result(footprint=[width, cist_d + bowl_d])


async def insert_sink(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
) -> CommandResult:
    """Sink plan symbol on FURN, matching the reference "Раковина" pictogram.
    500 x 400 mm, centred on (x_mm, y_mm), wall at local +Y — so rotation_deg matches
    the wall the sink hangs on.

    The symbol is a BASE with everything seated inside it, not a free-floating bowl:

        base   D-shaped carcass — flat back on the wall, straight sides, rounded
               front. This is the 500 x 400 footprint.
        bowl   oval, inset INSIDE the base: 70 mm from the wall but only 30 mm from
               the front and 20 mm from the sides. That lopsided 70 mm strip at the
               back is not slack — it is exactly what the tap occupies.
        valve  triangle in that strip, base 20 mm off the wall and 50 mm deep, so its
               apex lands precisely on the bowl's edge (20 + 50 = 70).
        spout  bar reaching from the valve in over the drain.
        drain  circle 180 mm off the wall — i.e. 40 mm off the bowl's centre toward
               the wall, not centred.

    Measured off the reference at 10 mm/px, the scale being fixed by the base itself
    (40 x 50 px = 400 x 500 mm). Two earlier versions got this wrong: the first drew a
    rectangular vanity with an inset oval; the second dropped the carcass altogether
    and left the bowl and tap floating with nothing to sit on."""
    def place(pts):
        return _place(pts, x_mm, y_mm, rotation_deg)

    def pt(px, py):
        return _place([(px, py)], x_mm, y_mm, rotation_deg)[0]

    w, dep = 500.0, 400.0
    hw, wall = w / 2.0, dep / 2.0       # half-width; wall face at local +Y
    side_to = wall - 240.0              # sides run straight this far, then the front curves
    bowl_cy, bowl_rx, bowl_ry = -20.0, 230.0, 150.0
    valve_off, valve_d, valve_hw = 20.0, 50.0, 25.0
    spout_from, spout_to = 50.0, 120.0  # spout span, measured off the wall
    drain_off, drain_r = 180.0, 25.0    # drain centre, off the wall

    # Elliptical front, sampled left->right: hw across, and deep enough to reach the
    # front face at y = -wall (NOT wall - side_to, which overshoots past the base).
    front_ry = side_to + wall
    front = []
    for i in range(33):
        a = math.pi + math.pi * i / 32
        front.append((hw * math.cos(a), side_to + front_ry * math.sin(a)))

    d = _Draw(backend, FURN_LAYER)
    await d.poly(place([(-hw, wall), (-hw, side_to)] + front + [(hw, wall)]), closed=True)
    await d.poly(place(_oval_local(0.0, bowl_cy, bowl_rx, bowl_ry, 48)))
    drain = pt(0.0, wall - drain_off)
    await d.circle(drain[0], drain[1], drain_r)
    # Valve: apex on the bowl's edge, base toward the wall — seated inside the base.
    v_base = wall - valve_off
    await d.poly(place([(0.0, v_base - valve_d), (-valve_hw, v_base), (valve_hw, v_base)]),
                 closed=True)
    s0, s1 = pt(0.0, wall - spout_from), pt(0.0, wall - spout_to)
    await d.line(s0[0], s0[1], s1[0], s1[1])
    return d.result(footprint=[w, dep], bowl=[bowl_rx * 2, bowl_ry * 2])


# ---------------------------------------------------------------------------
# Sizes below are ESTIMATES scaled off reference/reference-studio-module-layout.png
# (the "Стол"/"Шкаф"/"Ступ" objects), which carries no dimension tags for them.
# That drawing is an ILLUSTRATIVE schematic, not a scaled plan: calibrating
# against its own anchors gives inconsistent results (module 9000 mm -> 9.1 mm/px
# across, 2500 mm -> 7.8 mm/px down; the "Душ 1200x1200" tile is drawn ~30%
# under its stated size). The bed ("Кровать 1200x2000") is the most self-
# consistent anchor and sits in the same zone as this furniture, so it sets the
# scale here: 10.2 mm/px across, 8.8 mm/px down. Measured pixel extents ->
#   Стол  124 x 51 px -> ~1260 x 450 mm   (module-scale cross-check: 1130 x 400)
#   Шкаф   91 x 48 px -> ~ 920 x 420 mm   (cross-check:  830 x 370)
#   Стул   54 x 52 px -> ~ 550 x 460 mm   (cross-check:  490 x 400)
# The defaults round those to sane furniture sizes. They are a reasonable
# reading of an illustrative drawing, NOT a normative standard — override via
# the width/depth arguments where a real spec exists.
# ---------------------------------------------------------------------------

async def insert_table(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
    *, width_mm: float = 1200.0, depth_mm: float = 500.0,
) -> CommandResult:
    """Table/desk plan symbol on FURN: a plain outline rectangle, exactly as the
    reference draws it. ``width_mm`` runs along the wall, ``depth_mm`` into the
    room; centred on (x_mm, y_mm) with the wall-facing edge at local -Y, so
    rotation_deg matches the wall the desk backs onto (0 = wall to the south)."""
    d = _Draw(backend, FURN_LAYER)
    await d.poly(_place(_rect_local(0.0, 0.0, float(width_mm), float(depth_mm)),
                        x_mm, y_mm, rotation_deg))
    return d.result(footprint=[float(width_mm), float(depth_mm)])


async def insert_wardrobe(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
    *, width_mm: float = 900.0, depth_mm: float = 420.0,
) -> CommandResult:
    """Wardrobe plan symbol on FURN — a single free-placed cabinet cell drawn with
    the SAME door glyph as insert_locker_row (see _cabinet_cell): body rectangle +
    back-panel line + the two-leaf "домик" peak, no swing arc.

    ``width_mm`` runs along the wall, ``depth_mm`` into the room; centred on
    (x_mm, y_mm) with the back against local -Y and the doors opening toward
    local +Y, so rotation_deg matches the wall it stands against (0 = wall to the
    south, doors opening north)."""
    cw, dp = float(width_mm), float(depth_mm)

    # Cell frame -> centre-local -> world. lx runs from the back (-dp/2) forward;
    # ly runs along the wall from the left edge (-cw/2).
    def cell(lx, ly):
        return _place([(-cw / 2.0 + ly, -dp / 2.0 + lx)], x_mm, y_mm, rotation_deg)[0]

    d = _Draw(backend, FURN_LAYER)
    await _cabinet_cell(d, cell, cw, dp)
    return d.result(footprint=[cw, dp])


async def insert_chair(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
) -> CommandResult:
    """Chair plan symbol on FURN, matching the reference "Ступ" pictogram: a
    rounded-square seat, a small armrest pad each side, and a curved backrest drawn as
    a closed band (two concentric arcs joined at each end by a line running up to the
    seat) behind it. 550x490 mm overall, centred on (x_mm, y_mm);
    the chair FACES local +Y with the backrest at local -Y — so rotation_deg is
    the direction the sitter looks (0 = facing north, toward a desk to the north)."""
    def place(pts):
        return _place(pts, x_mm, y_mm, rotation_deg)

    seat_w, seat_d, seat_cy = 450.0, 380.0, 50.0    # seat spans y -140..+240
    arm_w, arm_d, arm_cy = 50.0, 230.0, 30.0
    back_r, back_t = 440.0, 60.0
    back_from, back_to = 242.0, 298.0               # arc sweep, symmetric about 270
    # Place the arc centre so the inner arc's endpoints land EXACTLY on the seat's rear
    # edge — otherwise the band meets the seat with a hairline gap that shows up on
    # zoom/OSNAP even though it is invisible at plot scale.
    seat_rear = seat_cy - seat_d / 2.0
    back_cy = seat_rear - back_r * math.sin(math.radians(back_from))

    d = _Draw(backend, FURN_LAYER)
    await d.poly(place(_rrect_local(0.0, seat_cy, seat_w, seat_d, 90.0)))   # seat
    for sx in (-1.0, 1.0):                                                  # armrests
        cx = sx * (seat_w / 2.0 + arm_w / 2.0)
        await d.poly(place(_rrect_local(cx, arm_cy, arm_w, arm_d, 20.0)))
    # Backrest: a CLOSED band bulging away from the seat (local -Y) — two concentric
    # arcs joined at each end by a straight line, as the reference draws it (not two
    # free-floating arcs). The band is centred behind the seat so the inner arc's
    # endpoints land on the seat's rear edge (y = seat_cy - seat_d/2), which makes the
    # two closing lines read as the backrest's uprights meeting the seat.
    inner = _arc_points(0.0, back_cy, back_r, back_from, back_to, 24)
    outer = _arc_points(0.0, back_cy, back_r + back_t, back_from, back_to, 24)
    # inner left..right, then outer right..left; closing the loop draws the two
    # end lines (inner_right->outer_right and outer_left->inner_left).
    await d.poly(place(inner + list(reversed(outer))), closed=True)
    width = seat_w + 2 * arm_w                      # armrests set the overall width
    depth = (seat_cy + seat_d / 2.0) - (back_cy - (back_r + back_t))
    return d.result(footprint=[round(width, 1), round(depth, 1)])


# ---------------------------------------------------------------------------
# Fixtures / engineering symbols read off reference-studio-module-layout.png
# ("Душ", "Тумба", "Конвектор", "Сплит-система", "Щит").
#
# SIZE PROVENANCE — read this before trusting the defaults.
# NONE of these defaults come from this reference, INCLUDING the shower. The
# shower used to be the exception — it carries an explicit "Душ 1200x1200" tag,
# which was taken as a stated size. It is not one: it is a competitor's drawing,
# and the real Polisnab trays are 800 or 900 square. That tag was the single
# most authoritative-looking number on the reference and it was still wrong, so
# treat a tag here as the competitor's claim about their own product, never as a
# spec for ours. insert_shower now accepts only 800/900 and rejects 1200.
# The other four defaults are typical-product sizes, NOT measurements of this
# drawing — the reference is an illustrative schematic and disagrees with them.
# Measured against the bed anchor ("Кровать 1200x2000" -> 9.95 mm/px across,
# 8.0 mm/px down; same anchor and same caveats as the Стол/Шкаф/Стул block
# above), the drawn pixel extents are:
#   Душ         107 x  90 px -> ~1065 x  720 mm   (tagged 1200x1200 — tag is the
#                                                  competitor's, real = 800/900)
#   Тумба        53 x  46 px -> ~ 527 x  368 mm   (default 450 x 430)
#   Конвектор    91 x   8 px -> ~ 906 x   64 mm   (default 665 x  95, length parametric)
#   Сплит         68 x   6 px -> ~ 677 x   48 mm   (default 475 x 142, length parametric)
#   Щит          57 x  28 px -> ~ 567 x  224 mm   (default 285 x 285 — square, ref is landscape)
# So the defaults are a reasonable product-catalogue reading, NOT a measurement
# of this reference and NOT a normative standard. Override via the size
# arguments wherever a real spec exists.
# ---------------------------------------------------------------------------

_SHOWER_SIZES_MM = (800.0, 900.0)


async def insert_shower(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
    *, size_mm: float = 800.0,
) -> CommandResult:
    """Shower plan symbol on FURN — the reference's "Душ" pictogram:

        tray   rounded outer square — the size_mm x size_mm footprint.
        rim    second rounded square inset 70 mm: the tray's upstand. This is
               what separates a shower from a plain tiled square at plan scale.
        drain  two concentric circles (body + grate) in the wall corner, not a
               single circle — a lone circle reads as a column or a pipe.
        valve  circle on the -X wall with a short stem back to it — the mixer /
               riser (вентиль). Without it the square says "there is a tray
               here" but not "this is plumbed", which is the ambiguity it fixes.

    NO DOOR is drawn — deliberate, not an omission. An earlier version drew a
    leaf + swing arc; it was dropped. The trade-off that buys: the symbol no
    longer says which side you enter from, nor reserves the floor the door
    needs. If a layout has to answer either question, the door belongs back (it
    is in git history, hinged at the +X end of the +Y side, swinging inward).

    ORIENTATION still matters, but now only for the plumbing: the cabin sits in
    a CORNER, with building walls at local -X and -Y. rotation_deg = 0 puts them
    to the west and south. The drain sits in the -X/-Y corner (against the
    walls, where the pipework is) and the mixer on the -X wall — point them at
    the real soil stack when placing.

    size_mm: 800 или 900, реальные стандартные размеры, НЕ 1200 (это была
    ошибочная оценка с референс-чертежа конкурента). Квадратный поддон, поэтому
    size_mm задаёт обе стороны. Любое другое значение отвергается — это не
    придирка к вводу, а единственное место, где зафиксировано, что 1200 больше
    не вариант. Centred on (x_mm, y_mm)."""
    if float(size_mm) not in _SHOWER_SIZES_MM:
        return CommandResult(
            ok=False,
            error=f"insert_shower: size_mm must be 800 or 900, got {size_mm}",
        )
    s = float(size_mm)
    h = s / 2.0
    rim = 70.0                          # upstand width, tray edge -> inner basin
    inset = 170.0                       # drain centre, off the two corner edges
    drain_r, grate_r = 45.0, 20.0
    valve_r, valve_off = 55.0, 110.0    # mixer circle, and its centre off the wall

    def pt(px, py):
        return _place([(px, py)], x_mm, y_mm, rotation_deg)[0]

    d = _Draw(backend, FURN_LAYER)
    # Tray + upstand.
    await d.poly(_place(_rrect_local(0.0, 0.0, s, s, 60.0), x_mm, y_mm, rotation_deg))
    await d.poly(_place(_rrect_local(0.0, 0.0, s - 2 * rim, s - 2 * rim, 40.0),
                        x_mm, y_mm, rotation_deg))
    # Drain in the wall corner.
    dr = pt(-h + inset, -h + inset)
    await d.circle(dr[0], dr[1], drain_r)
    await d.circle(dr[0], dr[1], grate_r)
    # Mixer on the -X wall, mid-side, with a stem tying it back to the wall.
    vc = pt(-h + valve_off, 0.0)
    await d.circle(vc[0], vc[1], valve_r)
    s0, s1 = _place([(-h, 0.0), (-h + valve_off - valve_r, 0.0)],
                    x_mm, y_mm, rotation_deg)
    await d.line(s0[0], s0[1], s1[0], s1[1])
    return d.result(footprint=[s, s])


async def insert_nightstand(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
    *, width_mm: float = 450.0, depth_mm: float = 430.0,
) -> CommandResult:
    """Nightstand plan symbol on FURN — the reference's "Тумба" pictogram with the
    drawer read in, so it is not just an anonymous box:

        body    outer rectangle, the 450 x 430 footprint.
        front   line 40 mm in from the room-facing edge — the drawer/door face.
                This is what tells the reader which way the тумба opens.
        handle  short bar centred on that face.

    ``width_mm`` runs along the wall, ``depth_mm`` into the room; centred on
    (x_mm, y_mm) with the back against local -Y and the drawer opening toward
    local +Y, so rotation_deg matches the wall it stands against (0 = wall to
    the south, drawer opening north).

    Size is an ESTIMATE (typical product), not a measured or tagged value — the
    reference is a schematic and draws this box at ~527 x 368 mm. See the
    provenance block above."""
    w, dp = float(width_mm), float(depth_mm)
    face_off = 40.0                     # drawer face, in from the front edge
    handle_w, handle_off = 120.0, 20.0  # handle bar, and its offset from the front

    front = dp / 2.0
    d = _Draw(backend, FURN_LAYER)
    await d.poly(_place(_rect_local(0.0, 0.0, w, dp), x_mm, y_mm, rotation_deg))
    f0, f1 = _place([(-w / 2.0, front - face_off), (w / 2.0, front - face_off)],
                    x_mm, y_mm, rotation_deg)
    await d.line(f0[0], f0[1], f1[0], f1[1])
    h0, h1 = _place([(-handle_w / 2.0, front - handle_off),
                     (handle_w / 2.0, front - handle_off)], x_mm, y_mm, rotation_deg)
    await d.line(h0[0], h0[1], h1[0], h1[1])
    return d.result(footprint=[w, dp])


async def insert_convector(
    backend: AutoCADBackend, x_mm: float, y_mm: float,
    length_mm: float = 665.0, rotation_deg: float = 0.0,
    *, depth_mm: float = 95.0,
) -> CommandResult:
    """Convector plan symbol on FURN — the reference's "Конвектор" pictogram with
    the grille drawn in, which is what actually identifies a convector on a plan:

        case  outer rectangle, length x depth.
        grille  inner rectangle inset 15 mm.
        fins  short bars across the grille at ~80 mm centres. The fin pitch is
              deliberately coarse: at 1:50 a realistic ~10 mm pitch collapses
              into a black smear, so this is a legible stand-in, not a count of
              real fins.

    Without the fins this symbol is a thin rectangle — indistinguishable from a
    shelf, a sill or a duct.

    ``length_mm`` is the panel length along the wall — parametric, since real
    convectors are sized to the window/wall they sit under (the reference draws
    two of different lengths). ``depth_mm`` is the panel's fixed 95 mm section.
    Centred on (x_mm, y_mm), length along local X, wall at local -Y.

    Both figures are ESTIMATES (typical product), not measured or tagged — the
    reference draws its panel at ~906 x 64 mm. See the provenance block above."""
    L, dp = float(length_mm), float(depth_mm)
    inset, pitch = 15.0, 80.0

    d = _Draw(backend, FURN_LAYER)
    await d.poly(_place(_rect_local(0.0, 0.0, L, dp), x_mm, y_mm, rotation_deg))
    gl, gd = L - 2 * inset, dp - 2 * inset
    await d.poly(_place(_rect_local(0.0, 0.0, gl, gd), x_mm, y_mm, rotation_deg))
    # Fins spanning the grille, evenly spaced across its length.
    n = max(int(gl // pitch), 1)
    for i in range(1, n):
        fx = -gl / 2.0 + gl * i / n
        p0, p1 = _place([(fx, -gd / 2.0), (fx, gd / 2.0)], x_mm, y_mm, rotation_deg)
        await d.line(p0[0], p0[1], p1[0], p1[1])
    return d.result(footprint=[L, dp], fins=n - 1)


async def insert_split_system(
    backend: AutoCADBackend, x_mm: float, y_mm: float,
    length_mm: float = 475.0, rotation_deg: float = 0.0,
    *, depth_mm: float = 142.0,
) -> CommandResult:
    """Split-system indoor unit plan symbol on FURN — the reference's
    "Сплит-система" pictogram, detailed to read as a real indoor unit:

        body    rounded rectangle whose corner radius is half the depth, so the
                short ends come out as full semicircles. That pill silhouette is
                what tells it apart at a glance from the square-cornered
                convector — the two are otherwise both thin wall-hugging bars.
        louver  line along the room-facing face, inset 40 mm and spanning the
                middle 80% of the length: the air outlet. It also disambiguates
                the orientation, which the bare pill cannot (it is symmetric).

    ``length_mm`` runs along the wall, ``depth_mm`` is the unit's section into
    the room. Centred on (x_mm, y_mm), length along local X, wall at local -Y —
    so place the centre depth_mm/2 off the wall's inner face to sit flush.

    Both figures are ESTIMATES (typical product), not measured or tagged — the
    reference draws its unit at ~677 x 48 mm. See the provenance block above."""
    L, dp = float(length_mm), float(depth_mm)
    louver_off, louver_frac = 40.0, 0.8

    d = _Draw(backend, FURN_LAYER)
    await d.poly(_place(_rrect_local(0.0, 0.0, L, dp, dp / 2.0, n=8),
                        x_mm, y_mm, rotation_deg))
    lx = L * louver_frac / 2.0
    ly = dp / 2.0 - louver_off
    p0, p1 = _place([(-lx, ly), (lx, ly)], x_mm, y_mm, rotation_deg)
    await d.line(p0[0], p0[1], p1[0], p1[1])
    return d.result(footprint=[L, dp])


async def insert_electrical_panel(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
    *, size_mm: float = 285.0,
) -> CommandResult:
    """Electrical-panel plan symbol on FURN — the reference's "Щит" pictogram:

        case  outer square, the 285 x 285 footprint.
        door  inner square inset 25 mm — the hinged front, which is what makes
              this read as an enclosure rather than a solid block.
        bolt  lightning (молния) struck diagonally through it, sized to the door.

    The bolt — not a circle or a dot pattern — is what the reference actually
    draws, and it is the conventional electrical marker, so it is worth keeping:
    a bare box would read as just another nightstand at plan scale. It is a
    single 6-point open polyline running lower-left to upper-right.

    285 x 285 mm square, centred on (x_mm, y_mm); the panel hangs on the wall at
    local -Y, so rotation_deg matches that wall (0 = wall to the south).

    Size is an ESTIMATE (typical product), not measured or tagged. NOTE the
    reference draws this box LANDSCAPE (~567 x 224 mm), not square — the default
    here is the product shape, not the drawn one. See the provenance block above."""
    s = float(size_mm)
    door = 25.0

    d = _Draw(backend, FURN_LAYER)
    await d.poly(_place(_rect_local(0.0, 0.0, s, s), x_mm, y_mm, rotation_deg))
    await d.poly(_place(_rect_local(0.0, 0.0, s - 2 * door, s - 2 * door),
                        x_mm, y_mm, rotation_deg))

    # Lightning bolt as a fraction of the box, so it scales with size_mm.
    # Local frame: x right, y up; the stroke runs from the lower-left corner
    # region up to the upper-right, with the two mid-notches that make it read
    # as a bolt rather than a plain zigzag. Kept inside the door rectangle.
    u = s / 100.0
    bolt = [(-24 * u, -24 * u), (-4 * u, 13 * u), (5 * u, 2 * u),
            (10 * u, 10 * u), (15 * u, -2 * u), (24 * u, 24 * u)]
    await d.poly(_place(bolt, x_mm, y_mm, rotation_deg), closed=False)
    return d.result(footprint=[s, s])


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
    (sx, sy), (ux, uy), (nx, ny), _Ls, _, _ = _side_offset_geom(wall_side, ox, oy, L, W, t)
    cw = float(cell_width_mm)
    dp = float(depth_mm)
    n = int(count)

    def P(off, depth):
        return (sx + ux * off + nx * depth, sy + uy * off + ny * depth)

    front = t + dp       # cell front, into the room
    lbl_clear = cw * 0.4          # label stand-off in front of the row

    d = _Draw(backend, FURN_LAYER)
    for i in range(n):
        a = offset_mm + i * cw
        # Map the cell's own frame to world through the SAME P() offset/normal
        # transform that places the wall geometry:
        #   lx: 0 = wall-adjacent face, dp = open room-facing edge -> depth = t + lx
        #   ly: 0..cw along the wall                               -> off   = a  + ly
        def cell(lx, ly, a=a):
            return P(a + ly, t + lx)
        await _cabinet_cell(d, cell, cw, dp)

    if label:
        lbl_h = LABEL_HEIGHT / 2.0                           # 75 mm for the locker tag
        row_mid = offset_mm + n * cw / 2.0
        # Stand-off is a clearance from the row front to the tag's NEAR EDGE, so
        # it has to be measured against the text's extent IN THE STAND-OFF
        # DIRECTION. The tag is always horizontal: on a N/S wall the stand-off
        # runs across its height (75 mm), on a W/E wall across its WIDTH (315 mm
        # for "ЛОКЕРЫ") - four times as much. The old code added the height in
        # both cases and pushed the tag centre out by a further fixed 150 mm,
        # which put it a hair short of the room's centre line. Two banks facing
        # each other across a 2400 mm room then left their tags 15 mm apart:
        # no AABB overlap, so every check passed, and the drawing read
        # "ЛОКЕРЫОКЕРЫ". Measuring the right dimension keeps each tag in front
        # of its OWN bank instead of meeting in the middle.
        tw, th = _text_aabb(0.0, 0.0, label, lbl_h)[2] * 2.0, lbl_h
        half = (tw if abs(nx) > abs(ny) else th) / 2.0
        lp = P(row_mid, front + lbl_clear + half)
        await d.mtext(lp[0], lp[1], str(label), height=lbl_h, layer=TEXT_LAYER)

    return d.result(wall_side=str(wall_side), count=n, cell_width=cw, depth=dp)


# ==========================================================================
# Phase 4 — room templates. TWO SEPARATE composite generators, each reproducing
# one reference layout end-to-end (standards + shell + openings + furniture) in
# a single call. Deliberately NOT one shared function: the references are
# different room types (dormitory vs studio) with different element sets and,
# crucially, different confidence levels — folding them together would hide
# which combinations have actually been seen live.
#
# CONFIDENCE — read before trusting the geometry these emit:
#   generate_dormitory_room  is modelled on reference-4bed-room-layout.png. Only
#     bed_pairs=1 is a confirmed, screenshot-verified arrangement. bed_pairs>1
#     tiles further pairs westward BY ANALOGY and is returned verified=False +
#     a warning — that multi-pair spacing has NOT been eyeballed live.
#   generate_studio_module   packs every studio element (INCLUDING a double bed)
#     into one 6000x2400 room. This exact combination has NOT been verified live
#     — the double bed had only ever been drawn on its own, never alongside the
#     санузел cluster + engineering symbols — so it is returned verified=False
#     until a screenshot confirms it. Treat the first run as a first-time test.
#
# Both compose the existing node generators; they invent no new primitives. The
# only fresh decisions are placement coordinates, kept as named locals below.
# ==========================================================================


class _Compose:
    """Aggregate several sub-generator CommandResults into one summary result.

    Mirrors _Draw's ok/error discipline at the orchestration level: the first
    failing step's error wins and flips the whole call to ok=False, but every
    step is still attempted and recorded so the caller can see how far it got.
    """

    def __init__(self):
        self.steps: list[dict] = []
        self.error: str | None = None
        self.entities = 0
        self.boxes: list[tuple] = []     # (step_name, aabb) physical footprints
        # Label footprints and door-swing clearance zones are kept in their OWN
        # lists rather than mixed into self.boxes, because the three kinds obey
        # different collision rules:
        #   boxes  — physical geometry; may not overlap other physical geometry.
        #   labels — annotation; may not overlap anything (it becomes unreadable),
        #            but does NOT obstruct a door.
        #   swings — a clearance requirement, not an object; may not be blocked by
        #            physical geometry, but freely passes over labels and is not
        #            itself an obstruction to anything else.
        #   clearances — a named volume of space that must STAY EMPTY (the corridor
        #            passage). Violated by physical geometry and by door swings;
        #            like swings, indifferent to labels (a tag floating in a
        #            corridor is normal draughting, not an obstruction).
        self.labels: list[tuple] = []
        self.swings: list[tuple] = []
        self.clearances: list[tuple] = []
        # Wall bands actually DRAWN by this composite, per side. Not a collision
        # family — the opposite: it is the evidence a room next door needs to
        # prove that a boundary it deliberately did not draw is really walled
        # (see open_side_violations). Kept out of self.boxes, whose entries are
        # whole-element footprints and are excluded/aggregated by step name.
        self.wall_bands: list[tuple] = []
        # Elements drawn by PREVIOUS generate_* calls on the same sheet, merged in
        # via absorb(). Held apart from the three lists above so this room's own
        # bbox / boxes / labels / swings payload stays strictly its own — otherwise
        # room B would report room A's footprint as part of itself, and chaining
        # rooms would compound. They take part in the checks, nothing else.
        self.ext_boxes: list[tuple] = []
        self.ext_labels: list[tuple] = []
        self.ext_swings: list[tuple] = []
        self.ext_clearances: list[tuple] = []
        self.ext_wall_bands: list[tuple] = []

    def add(self, name: str, r: CommandResult) -> CommandResult:
        cnt = None
        bbox = None
        if isinstance(r.payload, dict):
            cnt = r.payload.get("count")
            bbox = r.payload.get("bbox")
        self.steps.append({"step": name, "ok": r.ok, "error": r.error, "count": cnt})
        if r.ok and isinstance(cnt, int):
            self.entities += cnt
        if r.ok and bbox is not None:
            self.boxes.append((name, tuple(bbox)))
        if r.ok and isinstance(r.payload, dict):
            for text, lb in (r.payload.get("label_bboxes") or []):
                self.labels.append((f"{name}:label[{text}]", tuple(lb)))
            sb = r.payload.get("swing_bbox")
            if sb is not None:
                self.swings.append((name, tuple(sb)))
            # A step may publish clearance zones of its own (insert_corridor does),
            # so a composite that draws a corridor inline is checked the same way
            # as one that receives it through `scene`.
            for entry in (r.payload.get("clearances") or []):
                self.clearances.append((f"{name}:{entry[0]}", tuple(entry[1:5])))
            for entry in (r.payload.get("wall_bands") or []):
                self.wall_bands.append((f"{name}:wall[{entry[0]}]", tuple(entry[1:5])))
        if not r.ok and self.error is None:
            self.error = f"{name}: {r.error}"
        return r

    def absorb(self, scene):
        """Merge boxes/labels/swings from ALREADY-DRAWN elements (previous
        generate_* calls on the same drawing) so this room can be checked against
        them. ``scene`` is a list of prior result payloads.

        Names are prefixed with the source room's origin, so a warning says which
        room the obstruction belongs to. Nothing is drawn — this only widens what
        the checks below can see."""
        for prev in (scene or []):
            if not isinstance(prev, dict):
                continue
            o = prev.get("origin") or [0.0, 0.0]
            tag = f"room@{float(o[0]):.0f},{float(o[1]):.0f}"
            for key, dest in (("boxes", self.ext_boxes), ("labels", self.ext_labels),
                              ("swings", self.ext_swings),
                              ("clearances", self.ext_clearances),
                              ("wall_bands", self.ext_wall_bands)):
                for entry in (prev.get(key) or []):
                    dest.append((f"[{tag}] {entry[0]}", tuple(entry[1:5])))

    def _pairs(self, own, own_vs_ext, ext, kind):
        """Every own-own pair plus every own-ext pair. ext-ext pairs are skipped:
        those are two previously-drawn rooms, already audited by their own calls —
        re-reporting them here would duplicate a warning the caller has seen.

        ``own`` is the excluded-filtered list used for own-own pairing;
        ``own_vs_ext`` is the UNFILTERED list used against foreign elements. The
        two differ by the module shell, and the difference matters: excluding the
        shell is right within a room (its bbox is the whole module rectangle, so
        every bed would trivially "collide" with it) but wrong across rooms —
        with the shell dropped, a neighbour's label landing inside THIS room goes
        unreported, and the neighbour could not have caught it either because
        this room did not exist when it was drawn. Blind in both directions."""
        out = []
        seen = set()
        for i in range(len(own)):
            ni, bi = own[i]
            for nj, bj in own[i + 1:]:
                hit = _boxes_overlap(bi, bj)
                if hit:
                    seen.add((ni, nj))
                    out.append(f"{kind}: '{ni}' <-> '{nj}' "
                               f"overlap {hit[0]:.0f}x{hit[1]:.0f} mm")
        for ni, bi in own_vs_ext:
            for nj, bj in ext:
                hit = _boxes_overlap(bi, bj)
                if hit and (ni, nj) not in seen:
                    out.append(f"{kind}: '{ni}' <-> '{nj}' "
                               f"overlap {hit[0]:.0f}x{hit[1]:.0f} mm")
        return out

    def intersections(self, exclude=()):
        """Pairwise AABB-overlap check over physical geometry AND labels (the
        module shell etc. can be named in ``exclude``). Returns a list of
        human-readable warnings — one per overlapping pair, with the overlap
        extent in mm. Touching edges (0 overlap) do NOT count. An empty list
        means the scene is collision-free by this test.

        Labels participate on equal footing: an unreadable overprinted tag
        (the "ВХОДКНО" case) is a drawing defect just like clashing furniture."""
        ex = set(exclude)
        own_all = self.boxes + self.labels
        own = [(n, b) for (n, b) in own_all if n not in ex]
        ext = self.ext_boxes + self.ext_labels
        return self._pairs(own, own_all, ext, "collision")

    def swing_collisions(self, exclude=()):
        """Door-swing clearance check: does the sector each leaf sweeps run into
        anything physical? Reported as its own category ("door_swing_collision")
        because the failure is different in kind from two objects occupying the
        same space — here the geometry is fine on paper, but nobody can open the
        door.

        Scope is deliberately EVERY physical box in the scene, whether it belongs
        to this room or to a neighbouring module merged in via ``absorb``: a leaf
        fouling on our own bed and a leaf fouling on the module next door are the
        same physical problem, and the leaf does not care who drew the obstacle.

        Excluded from consideration:
          * ``exclude`` (the room's own shell — its bbox is the whole module
            rectangle, so every inward-swinging door would trivially "hit" it);
          * the door's own opening (the hinge sits on it by definition);
          * labels — text is not an obstruction. Without this, the ВХОД tag that
            _label_opening deliberately parks outside the wall would fire a false
            clash on every outward-swinging entrance ever drawn."""
        ex = set(exclude)
        out = []
        # Our doors against everything physical; and previously-drawn neighbouring
        # doors against OUR geometry (their swing may reach into this new room —
        # that direction is invisible to the neighbour's own already-finished call).
        for swings, obstacles in ((self.swings, self.boxes + self.ext_boxes),
                                  (self.ext_swings, self.boxes)):
            for dname, sbox in swings:
                for name, box in obstacles:
                    if name in ex or name == dname:
                        continue
                    hit = _boxes_overlap(sbox, box)
                    if hit:
                        out.append(
                            f"door_swing_collision: '{dname}' swing is blocked by "
                            f"'{name}' - overlap {hit[0]:.0f}x{hit[1]:.0f} mm")
        return out

    def union_bbox(self):
        """AABB covering every element this composite drew (shell included), as
        (minx, miny, maxx, maxy) — or None if nothing was drawn. This is the
        room's footprint in WORLD coordinates, so a caller assembling several
        rooms on one drawing can run the same overlap test BETWEEN rooms that
        ``intersections`` runs between elements of one room."""
        if not self.boxes:
            return None
        xs0 = [b[0] for _, b in self.boxes]
        ys0 = [b[1] for _, b in self.boxes]
        xs1 = [b[2] for _, b in self.boxes]
        ys1 = [b[3] for _, b in self.boxes]
        return (min(xs0), min(ys0), max(xs1), max(ys1))

    def clearance_violations(self, exclude=()):
        """Zones that must stay empty (currently: the corridor passage) versus
        anything that intrudes into them — physical geometry or a door swing.

        Reported as its own category ("clearance_blocked") because the failure is
        again different in kind: nothing collides with anything, but a route
        stops being usable. This is what turns "the corridor is walkable end to
        end" into a property the generator can assert.

        Labels are NOT checked: a door tag or room number drawn in circulation
        space is ordinary draughting, not an obstruction. Same rule as swings."""
        ex = set(exclude)
        out = []
        all_clear = [(n, b, False) for n, b in self.clearances] + \
                    [(n, b, True) for n, b in self.ext_clearances]
        for cname, cbox, c_is_ext in all_clear:
            obstacles = ([(n, b, False) for n, b in self.boxes] +
                         [(n, b, False) for n, b in self.swings] +
                         [(n, b, True) for n, b in self.ext_boxes] +
                         [(n, b, True) for n, b in self.ext_swings])
            for name, box, o_is_ext in obstacles:
                # Skip ext-vs-ext: both were already present when the earlier call
                # ran its own audit, so re-reporting duplicates a known warning.
                # NOTE `exclude` is deliberately NOT applied here. The room shell
                # is excluded elsewhere so furniture does not collide with its own
                # envelope — but a shell overlapping the corridor passage is a room
                # built on top of the circulation route, which is precisely the
                # error this check exists to find.
                if (c_is_ext and o_is_ext) or name == cname:
                    continue
                hit = _boxes_overlap(cbox, box)
                if hit:
                    out.append(
                        f"clearance_blocked: '{cname}' is obstructed by '{name}' "
                        f"- intrusion {hit[0]:.0f}x{hit[1]:.0f} mm")
        return out

    def open_side_violations(self, open_sides):
        """The check that had to come WITH shared walls, not after them.

        Every other family here asks "is something in the way?". This one asks
        the opposite question, and it only became askable once a room could
        legitimately skip a wall: **if this room did not draw a boundary because
        the neighbour owns it, is that neighbour's wall actually there?**

        Without it, ``wall_west=False`` on the FIRST room of a row (an off-by-one
        away) leaves the module open to the outdoors and every existing check
        still passes with flying colours — nothing overlaps, nothing is blocked,
        there is simply no wall. Silence would be the wrong answer.

        ``open_sides`` is a list of (name, strip) where ``strip`` is the AABB the
        missing wall would have occupied. It is matched against the wall bands
        the NEIGHBOURS published (ext_wall_bands), not against their shell
        bboxes: a shell bbox is the whole room rectangle, so it would "cover"
        the strip even if that neighbour skipped the very same wall — both rooms
        pointing at each other and neither drawing anything."""
        out = []
        for name, strip in open_sides:
            hit = [n for n, b in self.ext_wall_bands if _boxes_overlap(strip, b)]
            if not hit:
                out.append(
                    f"open_side: '{name}' is not drawn (the neighbour is meant to "
                    f"own it) but no neighbouring wall band covers "
                    f"[{strip[0]:.0f},{strip[1]:.0f}]..[{strip[2]:.0f},{strip[3]:.0f}] "
                    f"- the room is open there")
        return out

    def label_legibility_violations(self, exclude=()):
        """Two tags that do not overlap but sit too close to be read apart.

        `intersections` answers "do these occupy the same space?", and for two
        labels 15 mm apart the honest answer is no - which is how a bank of
        lockers came out tagged "ЛОКЕРЫОКЕРЫ" with verified=true. Non-overlap is
        not legibility, so it gets its own question and its own category.

        THE NUMBER: the minimum clear gap is ONE TEXT HEIGHT - the taller of the
        two tags. That is the typographic leading argument, not a round number
        picked to make this case fail: lines set closer together than about one
        em stop reading as separate lines, which is exactly the failure here.
        It also scales with the annotation instead of being an absolute, so a
        150 mm room tag is held to a wider gap than a 75 mm furniture tag, as it
        should be. The existing scenes clear it with room to spare (the wide
        dormitory's two locker tags are 630 mm apart), so it is a real floor and
        not a threshold tuned to the sample.

        Diagonal neighbours are measured on the LARGER axis gap: two tags offset
        both across and along read apart even when one separation is small.
        Overlapping pairs are skipped - `collision` already reported those, and
        saying it twice would just make the same defect look like two."""
        ex = set(exclude)
        own = [(n, b) for (n, b) in self.labels if n not in ex]
        out = []
        for i in range(len(own)):
            ni, bi = own[i]
            for nj, bj in own[i + 1:] + self.ext_labels:
                if _boxes_overlap(bi, bj):
                    continue
                dx = max(bj[0] - bi[2], bi[0] - bj[2], 0.0)
                dy = max(bj[1] - bi[3], bi[1] - bj[3], 0.0)
                gap = max(dx, dy)
                need = max(bi[3] - bi[1], bj[3] - bj[1])
                if gap < need:
                    out.append(
                        f"label_legibility: '{ni}' <-> '{nj}' are {gap:.0f} mm "
                        f"apart, under the {need:.0f} mm (one text height) needed "
                        f"to read them as separate tags")
        return out

    def completeness_violations(self, shortfalls):
        """Did the room actually get what was ORDERED?

        The other families all ask some version of "is something in the way?".
        This one asks whether the thing that was asked for is there at all, and
        it exists because a room that quietly came out smaller than ordered
        passes every collision check with flying colours — there is simply less
        geometry to collide. A dormitory that dropped both bed pairs for lack of
        floor reported verified=True with zero beds in it.

        ``shortfalls`` is a list of (what, requested, placed, reason). Only a
        genuine shortfall is reported; placing MORE than asked is not a thing
        any generator here can do.

        The reason travels with the count on purpose. "3 of 4 beds" tells you
        something is wrong; "no floor left between the door swing and the
        lockers" tells you whether to move the door, drop a bed or turn the room
        — and that is the decision the caller actually has to make.
        """
        out = []
        for what, requested, placed, reason in shortfalls:
            if placed >= requested:
                continue
            out.append(f"incomplete: {what} - {placed} of {requested} placed "
                       f"({reason})")
        return out

    def audit(self, exclude=(), open_sides=(), shortfalls=()):
        """Run every check family and return (warnings, verified).
        ``verified`` is False if ANY family reported anything."""
        w = (self.intersections(exclude=exclude)
             + self.swing_collisions(exclude=exclude)
             + self.clearance_violations(exclude=exclude)
             + self.label_legibility_violations(exclude=exclude)
             + self.open_side_violations(open_sides)
             + self.completeness_violations(shortfalls))
        return w, (len(w) == 0)

    def result(self, **extra) -> CommandResult:
        payload = {"steps": self.steps, "entities": self.entities,
                   "bbox": self.union_bbox(),
                   # Emitted so the caller can feed this room back in as `scene`
                   # when drawing the next one on the same sheet.
                   "boxes": [[n, *b] for n, b in self.boxes],
                   "labels": [[n, *b] for n, b in self.labels],
                   "swings": [[n, *b] for n, b in self.swings],
                   "clearances": [[n, *b] for n, b in self.clearances],
                   "wall_bands": [[n, *b] for n, b in self.wall_bands]}
        payload.update(extra)
        return CommandResult(ok=self.error is None, payload=payload, error=self.error)


async def generate_dormitory_room(
    backend: AutoCADBackend,
    length_mm: float = 6000.0, width_mm: float = 2400.0,
    series: str = "arctic", bed_pairs: int = 1,
    origin_x: float = 0.0, origin_y: float = 0.0, scene=None,
    door_wall: str = "W", door_swing: str = "out", door_offset_mm=None,
    window_wall: str = "E", window_offset_mm=None,
    wall_west=True, wall_east=True, party_wall_thickness_mm=None,
    room_number=None, bed_axis: str = "auto", door_open_deg: float = 45.0,
) -> CommandResult:
    """4-bed dormitory (общежитие) module in one call. Wall layout (reworked
    2026-07-24): standards + a real thick-wall shell, an entrance door on the
    WEST wall, a window on the EAST wall, a row of lockers on the SOUTH wall, a
    BLANK north wall, and ``bed_pairs`` pair(s) of single beds by the EAST wall.
    (Earlier revisions had the door on SOUTH and lockers on WEST — no longer.)

    ``origin_x`` / ``origin_y`` place the module's SW outer corner in world
    coordinates (default 0,0 — the historical behaviour, byte-identical output).
    Everything the generator emits — shell, openings, furniture, the ВХОД/ОКНО/
    ЛОКЕРЫ tags — is derived from the inner-face locals below, which are
    themselves anchored on the origin, so the whole room translates rigidly.
    (setup_dimstyle draws no geometry; it only sets DIMSTYLE variables, so there
    is nothing there to translate.) The returned payload carries the room's world
    ``bbox``, so a caller tiling several rooms can overlap-test them against each
    other the same way _Compose overlap-tests elements within one room.

    Geometry orientation (in the module's own frame, inner faces inset from the
    origin by the wall thickness ``t``):
      * beds run head-to-EAST (toward the window wall), rotation_deg=-90 so the
        pillow end (local +Y) points +X; a pair is a south + north bed sharing an
        X range, kept apart by a clear GAP (``bed_gap_y``), NOT a partition — an
        interior wall between beds reads as structural, and beds against a wall
        only need air between them (feedback from the first live test);
      * successive pairs (one behind another along the wall) are gap-separated
        too (``pair_gap_x``), never touching;
      * the locker row starts at the SOUTH wall's inner face; its cell width is
        shrunk to the clear inner run (L - 2t) only if 4x600 would overflow — on
        6000 it does not, so cells stay 600 (offset 0 is the OUTER corner — a
        full-width run would sink 1/4 of the end cells into the walls).
      NOTE: the south beds front the south wall, so a locker row centred on the
      south wall can collide with them — see the caller/warnings.

    ``series`` picks the wall thickness (arctic 150 / standard 75). Door/window
    are cut with insert_exterior_door / insert_window; they must sit on DIFFERENT
    walls so the two cuts compose cleanly (the same-side caveat in _draw_door
    bites if you put both on one wall).

    CORRIDOR SCHEME (Phase 4b). Defaults reproduce the free-standing module: door
    on W opening OUT (a door to the outdoors), window on E. For a room served by
    a shared corridor, pass:

        door_wall="S", door_swing="in", window_wall="N"

    and the two architectural rules that go with it:
      * a door onto a SHARED corridor must open INWARD. Outward, it blocks the
        passage and fouls the opposite/neighbouring doors. This is the opposite
        of the free-standing default, where "out" is right because the leaf
        swings into open air;
      * the window moves off the E wall, because in a row of rooms along a
        corridor E/W are PARTY walls between neighbours and only N is exterior.
        Leaving the window on E does not merely look wrong: its ОКНО tag is
        drawn 250 mm beyond the wall, i.e. physically inside the next room, and
        the label check reports it.
    With those, a centred S door clears this layout's own furniture: the swept
    sector lands between the SW locker bank and the south bed row. That is not
    luck-by-design — it is asserted by the swing check, not assumed.

    SHARED WALLS IN A ROW (Phase 4b step 3). ``wall_west`` / ``wall_east`` say
    what to draw on the two SHORT end walls, in the same spirit as
    insert_corridor's wall_south/wall_north — the boundary is drawn ONCE, by
    whoever owns it, instead of two rooms each drawing their own and stacking two
    bands face to face. Each takes:

        True     — a full insulated envelope wall (``series`` thickness). The
                   real outer end of the building.
        "party"  — a PARTY wall shared with the room next door, drawn at
                   ``party_wall_thickness_mm`` (default PARTY_WALL_THICKNESS =
                   100 mm; see that constant for why it is neither 150 nor 75).
        False    — not drawn at all: the neighbour on that side owns the wall.

    The row rule, for ``n`` rooms tiled west→east at pitch ``length_mm``:

        room 0        wall_west=True,  wall_east="party"   (its west IS the end)
        room 1..n-2   wall_west=False, wall_east="party"
        room n-1      wall_west=False, wall_east=True      (its east IS the end)
        n == 1        both True — a free-standing module, the default.

    ``room_row_walls(i, n)`` returns exactly that as kwargs, so a caller never
    hand-writes the off-by-one. The PITCH IS UNCHANGED (``length_mm``, rooms
    still tile outer-edge to outer-edge): the envelope rectangle stays L x W and
    only the band inside it changes, so room i+1's interior begins at its own
    outer edge and the wall to its west is room i's party band, ending 100 mm
    earlier. One wall between the two rooms, and the total building length is the
    same as it was with two.

    Consequence to keep in mind: the INNER faces are now per-side, so rooms in
    the middle of a row are slightly roomier (their end walls are 100/0 mm rather
    than 150). Everything positional here is written against ix0/ix1, so the
    furniture follows automatically — the locker rows still start at the real
    inner west face and the beds' heads still sit 50 mm off the real inner east
    face, wherever those have moved to.

    A wall that is not drawn cannot hold an opening: ``door_wall`` / ``window_wall``
    pointing at a False side is rejected outright (ok=False) rather than cutting
    a hole in a wall that is not there. Pointing one at a "party" side is allowed
    but flagged — a window into the neighbour's room is a drawing error, and an
    entrance door through a party wall is not what insert_exterior_door draws.

    ``room_number`` (optional) tags the room at the centre of its clear interior
    (see _room_number_position for why there). It is passed in, never derived:
    which room this is depends on the building, and the caller assembling the
    building is the only one that knows. The tag is checked as annotation like
    every other label, so a number landing on furniture is reported.

    VERIFIED: only bed_pairs=1 (its confirmed, screenshot-checked form). For
    bed_pairs>1 the extra pairs are tiled westward by analogy and the result is
    flagged verified=False with a warning — that spacing has never been seen
    live, so do not treat it as final without a screenshot.
    """
    L = float(length_mm)
    W = float(width_mm)
    ox, oy = float(origin_x), float(origin_y)
    t = _resolve_wall_thickness(series, None)
    tp = (float(party_wall_thickness_mm) if party_wall_thickness_mm is not None
          else PARTY_WALL_THICKNESS)
    # Per-side thicknesses. Only the two SHORT end walls are variable: the long
    # N/S walls are always the real envelope (one faces the corridor, the other
    # the outdoors), it is the row direction that produces shared boundaries.
    side_t = {"S": t, "N": t,
              "W": _end_wall_thickness("wall_west", wall_west, t, tp),
              "E": _end_wall_thickness("wall_east", wall_east, t, tp)}
    modkw = dict(module_origin=(ox, oy), module_length=L, module_width=W,
                 wall_thickness=t, side_thickness=side_t)
    # Inner faces in WORLD coordinates. Every absolute placement below is written
    # against these four, never against a bare t / L-t, so the origin propagates
    # by construction instead of by remembering to add it at each call site.
    # They are PER SIDE since step 3: an end wall may be a 100 mm party wall or
    # (thickness 0) not this room's at all, and the furniture must follow the
    # face that is really there.
    ix0, iy0 = ox + side_t["W"], oy + t
    ix1, iy1 = ox + L - side_t["E"], oy + W - t

    # An opening needs a wall to be cut into. Rejected, not warned: cutting a
    # 950 mm hole out of a wall that was never drawn produces a jamb line
    # floating in mid-air and an ok:true result.
    for what, side in (("door_wall", door_wall), ("window_wall", window_wall)):
        key = _SIDE_ALIASES.get(str(side).strip().lower())
        if key is None:
            return CommandResult(
                ok=False, error=f"generate_dormitory_room: unknown {what}={side!r}")
        if side_t[key] <= 0.0:
            return CommandResult(ok=False, error=(
                f"generate_dormitory_room: {what}={side!r} but that wall is not "
                f"drawn by this room (the neighbour owns it) - move the opening "
                f"to a wall this room actually builds"))

    c = _Compose()
    c.absorb(scene)

    # 1) Standards + thick-wall shell.
    c.add("setup_layers", await setup_layers(backend))
    c.add("setup_dimstyle", await setup_dimstyle(backend))
    c.add("draw_module_outline",
          await draw_module_outline(backend, length_mm=L, width_mm=W, series=series,
                                    origin=(ox, oy), side_thickness=side_t))

    # 2) Entrance (W) + window (E) — different walls, so the cuts compose. The
    #    entrance was moved off the SOUTH wall to the WEST wall (S is now a blank
    #    wall); the door is centred on the west wall (offset measured from the SW
    #    outer corner along +Y, wall length = W), so it sits clear of both corners.
    #    NOTE offset_mm is measured ALONG THE WALL from that wall's start corner,
    #    not in world X/Y — the opening generators add module_origin themselves.
    #    Do NOT add origin_x/origin_y here; that would double-shift the openings.
    #    door_wall / window_wall pick the wall; the run length along a wall is L on
    #    N/S and W on E/W, so centring has to ask which one it is.
    def wall_run(side):
        return W if _SIDE_ALIASES.get(str(side).strip().lower()) in ("W", "E") else L

    door_w = 950.0
    door_off = (float(door_offset_mm) if door_offset_mm is not None
                else (wall_run(door_wall) - door_w) / 2.0)      # centred by default
    c.add("insert_exterior_door",
          await insert_exterior_door(backend, door_wall, door_off, door_w, door_swing,
                                     label="ВХОД", open_deg=door_open_deg, **modkw))
    win_w = min(1120.0, wall_run(window_wall) - 2 * t - 200.0)
    win_off = (float(window_offset_mm) if window_offset_mm is not None
               else (wall_run(window_wall) - win_w) / 2.0)
    c.add("insert_window",
          await insert_window(backend, window_wall, win_off, win_w,
                              label="ОКНО", **modkw))

    # 3) Lockers: TWO 2-cell banks tucked into the two WEST corners (beside the
    #    door wall), well clear of the bed rows that front the east part of both
    #    long walls. A single 4-cell run on one long wall would sit under that
    #    wall's beds (its frontage is taken), so the lockers are split into the
    #    two free corners instead:
    #      * SW corner — south wall, west end (offset = t, flush to the inner
    #        west-wall face);
    #      * NW corner — north wall, west end (offset = t).
    #    offset = t starts the first cell exactly at the inner face so no cell is
    #    buried in the west wall. The post-assembly collision check below is the
    #    real guard that these clear the door and the beds.
    #    NOTE the offset is the WEST wall's own thickness, not the envelope t —
    #    on a room whose west boundary is the neighbour's wall (side_t["W"] == 0)
    #    the inner face IS the outer corner, and an offset of t would leave a
    #    150 mm dead strip beside the lockers.
    #    Which two walls those are is not fixed any more: they are the walls the
    #    beds back onto, i.e. the ones parallel to `bed_axis`. On the historical
    #    wide room that is S and N at their west end — byte-identical to before.
    bed_axis = _resolve_bed_axis(bed_axis, ix0, iy0, ix1, iy1)
    if bed_axis == "x":
        row_walls, lock_end, lock_off = ("S", "N"), "W", side_t["W"]
        bed_rot, axis = -90.0, 0
        a_lo, a_hi, c_lo, c_hi = ix0, ix1, iy0, iy1
    else:
        row_walls, lock_end, lock_off = ("W", "E"), "S", side_t["S"]
        bed_rot, axis = 0.0, 1
        a_lo, a_hi, c_lo, c_hi = iy0, iy1, ix0, ix1

    lock_cw, lock_depth, lock_n = 600.0, 420.0, 2
    lockkw = {k: v for k, v in modkw.items() if k != "side_thickness"}
    # UNTAGGED in the turned layout. There the two banks flank the entrance on a
    # 2400 mm facade, and both tags land in the one strip of floor left between
    # them - legible now that they are spaced, but crowding the door and its
    # swing arc for no gain: a pair of locker banks either side of the entrance
    # reads as lockers without being told. The wide room keeps its tags, where
    # the banks sit on opposite walls and there is floor to put them on.
    lock_label = "ЛОКЕРЫ" if bed_axis == "x" else None
    for wall in row_walls:
        c.add(f"insert_locker_row[{wall}{lock_end}]",
              await insert_locker_row(backend, wall, lock_off, lock_cw, lock_depth,
                                      lock_n, label=lock_label, **lockkw))

    # 4) Bed pair(s) by the east wall — head-to-east (rot -90). A pair is a
    #    south + north bed sharing an X range, kept apart by a clear GAP, not a
    #    partition: an interior wall between beds reads as structural, and beds
    #    against a wall only need air between them. Successive pairs (one behind
    #    another along the wall) are likewise gap-separated, never touching.
    bed_len, bed_w = 2000.0, 825.0   # must track insert_bed single (825x2000)
    head_clear = 50.0            # bed head off the wall it faces
    pair_gap_x = 400.0          # clear gap between successive beds in a row
    # Each row sits FLUSH against its long wall: the bed's outer (wall-side) edge
    # is on the wall's inner face, 0 mm gap. The earlier code CENTRED the two-bed
    # stack (leaving a y_margin gap on both walls) — that centring, not the bed
    # width, was the source of the visible wall gap. With bed_w=825 the leftover
    # central aisle is inner_h - 2*bed_w = 450 mm (auto, was the old bed_gap_y).
    # Each row is tiled into the frontage that is MEASURED to be free, from the
    # far end back. The old code computed positions from ix1 by formula and
    # assumed the wall was clear; with the entrance on a long wall (the corridor
    # scheme) it is not, and a bed was drawn straight through the door swing —
    # 875x825 mm of it. Reserving the door's frontage is the same rule the
    # lockers already followed ("its frontage is taken"), applied to the beds.
    n_req = max(1, int(bed_pairs))
    warnings: list[str] = []
    rows = ((c_lo + bed_w / 2.0, row_walls[0]), (c_hi - bed_w / 2.0, row_walls[1]))
    per_row = []
    for centre, tag in rows:
        band_lo, band_hi = centre - bed_w / 2.0, centre + bed_w / 2.0
        obstacles = ([(n, b) for n, b in c.boxes if n != "draw_module_outline"]
                     + list(c.swings))
        n_here = 0
        for run_lo, run_hi in reversed(_free_runs(a_lo, a_hi, band_lo, band_hi,
                                                  obstacles, axis)):
            head = run_hi - head_clear
            while n_here < n_req and head - bed_len >= run_lo - 1e-6:
                foot = head - bed_len
                mid = (head + foot) / 2.0
                bx, by = (mid, centre) if axis == 0 else (centre, mid)
                c.add(f"insert_bed[{n_here + 1}{tag}]",
                      await insert_bed(backend, bx, by, bed_rot, "single"))
                n_here += 1
                head = foot - pair_gap_x
            if n_here >= n_req:
                break
        per_row.append((tag, n_here))
    placed = min(n for _, n in per_row)

    # Completeness: the room was ordered N pairs and may have got fewer. This is
    # its own family, not a collision — see _Compose.completeness_violations.
    beds_req, beds_got = 2 * n_req, sum(n for _, n in per_row)
    shortfalls = []
    if beds_got < beds_req:
        detail = ", ".join(f"{tag} row {n}/{n_req}" for tag, n in per_row)
        shortfalls.append((
            "beds", beds_req, beds_got,
            f"{detail}; a {bed_len:.0f} mm bed needs that much clear frontage "
            f"and the door swing, the lockers and the end walls take the rest"))

    # Automatic collision guard (replaces the old "bed_pairs>1 unverified" gate).
    # Two families, both fatal to `verified`:
    #   collision            — physical geometry or labels sharing space;
    #   door_swing_collision — a leaf that cannot complete its travel.
    # Covers this room's own elements plus anything merged in via `scene`.
    if room_number is not None:
        rx, ry = _room_number_position(ix0, iy0, ix1, iy1)
        c.add("insert_room_number",
              await insert_room_number(backend, rx, ry, room_number))

    #   open_side           — a boundary this room left to the neighbour, with no
    #                         neighbouring wall actually there to take it.
    # The strip to look in is OUTSIDE the envelope rectangle, not inside it: with
    # this side's thickness at 0 the inner face IS the outer edge, so the wall
    # that has to be there belongs to the neighbour and stands beyond it. (Probing
    # inside the rectangle instead finds nothing and cries wolf on every room of
    # a correctly-built row — the first way this was written.)
    # The strip spans the room's CLEAR height (iy0..iy1), not the full W: the
    # neighbour's own N and S bands run the whole length of their module and so
    # poke into the corners of this strip. Include the corners and any neighbour
    # at all "covers" the boundary — including one that skipped the very same
    # wall, which is the case this check exists for.
    open_sides = []
    if side_t["W"] <= 0.0:
        open_sides.append(("draw_module_outline:wall[W]", (ox - tp, iy0, ox, iy1)))
    if side_t["E"] <= 0.0:
        open_sides.append(("draw_module_outline:wall[E]",
                           (ox + L, iy0, ox + L + tp, iy1)))
    audit_warnings, verified = c.audit(exclude={"draw_module_outline"},
                                       open_sides=open_sides,
                                       shortfalls=shortfalls)
    warnings.extend(audit_warnings)
    # An opening on a party wall is geometrically fine and architecturally wrong:
    # a window would look into the neighbour's room, and insert_exterior_door
    # draws an ENTRANCE, not a door between two rooms. Flagged, not rejected —
    # unlike an opening on an undrawn wall, this one is at least buildable.
    for what, side in (("door_wall", door_wall), ("window_wall", window_wall)):
        key = _SIDE_ALIASES.get(str(side).strip().lower())
        if side_t[key] == tp and tp != t:
            warnings.append(f"{what}={side!r} is a PARTY wall shared with the room "
                            f"next door - an exterior opening does not belong there.")
            verified = False

    return c.result(series=str(series).strip().lower(), module=[L, W],
                    origin=[ox, oy], outer=[ox, oy, ox + L, oy + W],
                    wall_thickness=t, side_thickness=side_t,
                    inner=[ix0, iy0, ix1, iy1], bed_pairs_placed=placed,
                    beds_placed=beds_got, beds_requested=beds_req,
                    bed_axis=bed_axis,
                    verified=verified, warnings=warnings)


async def insert_sanitary_block(
    backend: AutoCADBackend, x_mm: float, y_mm: float, rotation_deg: float = 0.0,
    stall_count: int = 6, *, series=None, sink_count=None, scene=None,
    label: str | None = "САНУЗЕЛ",
) -> CommandResult:
    """Shared WC block: a row of ``stall_count`` cubicles, a basin run and an
    entrance door, inside the usual thick-wall shell. Composite - it calls
    insert_toilet / insert_sink / insert_interior_wall / insert_interior_door
    rather than replacing any of them.

    ``x_mm, y_mm`` is the block's CENTRE (like the furniture primitives, not
    like the room generators' origin_x/origin_y). The block is drawn
    axis-aligned, so ``rotation_deg`` only accepts 0 for now.

    LAYOUT, in the block's own frame: cubicles line the BACK wall facing the
    entrance, basins line the ENTRANCE wall, and the aisle runs between them.
    The aisle depth is derived (cubicle door + person at a basin), not chosen -
    see the constants above.

    BASIN COUNT is deliberately not one per cubicle. Norms for public WCs run
    around one basin per four appliances, which would give 2 here; this uses
    ``ceil(stalls/2)``, i.e. 3 for six cubicles, because a shift camp is the
    worst case for a ratio derived from averaged demand - everybody arrives at
    the same minute when the shift ends, so the queue forms at the basins, not
    over the day. Override with ``sink_count`` if the real occupancy says
    otherwise.

    NO CLEARANCE ZONE is published for the aisle, on purpose. Unlike a corridor,
    a WC aisle is *meant* to have doors swinging into it - that is the safe
    arrangement - so a clearance zone would fire on correct geometry every time.
    What is published instead is each cubicle door's swept sector, and the aisle
    is sized from it.
    """
    n = max(1, int(stall_count))
    t = _resolve_wall_thickness(series, None)
    cw = SANITARY_STALL_CLEAR_WIDTH
    tp = SANITARY_PARTITION_THICKNESS
    sd = SANITARY_STALL_CLEAR_DEPTH
    aisle = SANITARY_STALL_DOOR_WIDTH + SANITARY_BASIN_STAND
    n_sink = int(sink_count) if sink_count is not None else max(2, -(-n // 2))

    # Local envelope: the cubicle row sets the width, the three bands set the depth.
    inner_w = n * cw + (n - 1) * tp
    inner_d = sd + aisle + SANITARY_SINK_DEPTH
    L, W = inner_w + 2 * t, inner_d + 2 * t
    if int(round(float(rotation_deg))) % 360:
        raise ValueError(
            f"rotation_deg={rotation_deg!r}: the block is drawn axis-aligned; "
            f"rotation is not supported yet")
    ol, ow, rot = L, W, 0.0
    ox, oy = x_mm - ol / 2.0, y_mm - ow / 2.0
    place = lambda lx, ly: (ox + lx, oy + ly)

    ix0, iy0, ix1, iy1 = t, t, L - t, W - t      # inner faces, LOCAL
    modkw = dict(module_origin=(ox, oy), module_length=ol, module_width=ow,
                 wall_thickness=t)

    c = _Compose()
    c.absorb(scene)
    c.add("setup_layers", await setup_layers(backend))
    c.add("draw_module_outline",
          await draw_module_outline(backend, length_mm=ol, width_mm=ow,
                                    series=series, origin=(ox, oy)))

    # 1) Entrance, on the basin wall at the end away from the basins.
    door_w = 840.0
    door_lo = ix1 - 300.0 - door_w
    ws, woff = "S", door_lo
    facade, depth = ol, ow
    c.add("insert_interior_door",
          await insert_interior_door(backend, ws, woff, door_w, "in",
                                     label="ВХОД", **modkw))

    # 2) Cubicles: toilet, the partition on its far side, and the door leaf.
    for i in range(n):
        sx0 = ix0 + i * (cw + tp)
        cx_l = sx0 + cw / 2.0
        wx, wy = place(cx_l, iy1 - 325.0)         # toilet 650 deep, back to the wall
        c.add(f"insert_toilet[{i + 1}]",
              await insert_toilet(backend, wx, wy, rot))
        if i < n - 1:                              # n cubicles need n-1 dividers
            px = sx0 + cw + tp / 2.0
            c.add(f"insert_interior_wall[stall{i + 1}/{i + 2}]",
                  await insert_interior_wall(backend, place(px, iy1),
                                             place(px, iy1 - sd), tp))
        # Door leaf, hinged at the near jamb and opening OUT into the aisle.
        # Drawn at 45 deg for a readable plan; the published sector is the full
        # quarter turn, exactly as the entrance doors do it.
        hinge = place(sx0 + 75.0, iy1 - sd)
        along = place(sx0 + 76.0, iy1 - sd)
        out = place(sx0 + 75.0, iy1 - sd - 1.0)
        au = (along[0] - hinge[0], along[1] - hinge[1])
        so = (out[0] - hinge[0], out[1] - hinge[1])
        c.add(f"insert_stall_door[{i + 1}]",
              await _draw_door_symbol(backend, hinge, au, so,
                                      SANITARY_STALL_DOOR_WIDTH, open_deg=45.0))

    # 3) Basins along the entrance wall, from the far end so they stay clear of
    #    the door and its swing.
    for j in range(n_sink):
        sx_l = ix0 + 100.0 + SANITARY_SINK_PITCH * j + 250.0
        wx, wy = place(sx_l, iy0 + SANITARY_SINK_DEPTH / 2.0)
        c.add(f"insert_sink[{j + 1}]",
              await insert_sink(backend, wx, wy, rot + 180.0))

    if label:
        lx, ly = place((ix0 + ix1) / 2.0, iy0 + SANITARY_SINK_DEPTH + aisle / 2.0)
        d = _Draw(backend, TEXT_LAYER)
        await d.mtext(lx, ly, str(label), height=LABEL_HEIGHT, layer=TEXT_LAYER)
        c.add("insert_label", d.result())

    warnings, verified = c.audit(exclude={"draw_module_outline"})
    r = c.result(origin=[ox, oy], outer=[ox, oy, ox + ol, oy + ow],
                 module=[ol, ow], facade=facade, depth=depth,
                 stall_count=n, sink_count=n_sink,
                 stall_pitch=cw + tp, aisle_depth=aisle,
                 wall_thickness=t, rotation=rot,
                 verified=verified, warnings=warnings)
    if r.ok:
        r.payload["boxes"] = [[n_, *b] for n_, b in c.boxes]
        r.payload["labels"] = [[n_, *b] for n_, b in c.labels]
        r.payload["swings"] = [[n_, *b] for n_, b in c.swings]
    return r


async def generate_studio_module(
    backend: AutoCADBackend,
    length_mm: float = 6000.0, width_mm: float = 2400.0, series: str = "arctic",
    origin_x: float = 0.0, origin_y: float = 0.0, scene=None,
    door_swing: str = "out",
    wall_west=True, wall_east=True, party_wall_thickness_mm=None,
    room_number=None,
) -> CommandResult:
    """Studio module (студия-модуль) in one call — the element set of
    reference-studio-module-layout.png condensed into one 6000x2400 room:
    standards + shell + entrance door + window, a DOUBLE bed with a nightstand,
    a work zone (table + chair + wardrobe), an ENCLOSED санузел (shower + toilet
    + sink behind a real door), and the engineering symbols (convector under the
    window, split-system, electrical panel by the entrance).

    ``origin_x`` / ``origin_y`` place the module's SW outer corner in world
    coordinates (default 0,0 — the historical behaviour, byte-identical output).
    Shell, openings, санузел partition + its door, every fixture and every tag
    are derived from the inner-face locals, which are anchored on the origin, so
    the room translates rigidly. Two X values here (``door_center``,
    ``win_center``) are deliberately kept ABSOLUTE, because the convector and the
    electrical panel are positioned off them; the wall-relative offsets the
    opening generators want are derived from them by subtracting ``ox`` at the
    call site. The returned payload carries the room's world ``bbox`` for
    room-to-room overlap testing.

    The reference is a 9000x2500 drawing with a separate тамбур/гардероб; that
    does not fit 6000x2400, so this reproduces the STYLE and the fixture set, not
    the exact partitioning. Layout (module frame, inner faces inset by ``t``) —
    reworked after the first live studio review:
      * санузел: a COMPACT east room (1100 mm) shut off by a FULL-height N-S
        partition with a real interior DOOR — a proper wet room. The door is a
        NARROW 400 mm leaf that swings WEST, into the living-room corridor (never
        into the tiny wet room), placed near the south end so its swing clears the
        corner desk. Shower in the NE corner (rot 180 -> drain in the +X/+Y
        corner); sink on the east wall directly BELOW the shower (rot -90, backs
        east) — pulled up against it; toilet on the SOUTH wall (rot 180, backs
        south), east half under the shower and off the east wall / out of the
        doorway;
      * bed: double, head-to-NORTH (rot 0), NW corner;
      * nightstand: beside the HEAD (north end, east side, back to the north
        wall) so it is within arm's reach of the pillow — NOT at the feet;
      * window: north wall over the living zone; convector directly under it
        (no split-system — removed; its corner now holds the desk);
      * desk: VERTICAL (rot 90), tucked into the NE corner of the living area
        against the санузел partition and the north wall; chair pulled up on its
        west side (rot -90, facing the desk);
      * wardrobe: south wall, clear of the bed foot;
      * entrance door: south wall, swinging out;
      * electrical panel: south wall right beside the entrance door — by the
        вход, never inside the санузел.

    SHARED WALLS IN A ROW. ``wall_west`` / ``wall_east`` /
    ``party_wall_thickness_mm`` behave exactly as in generate_dormitory_room —
    True (envelope) / "party" (shared, 100 mm) / False (the neighbour owns it) —
    and ``room_row_walls(i, n)`` gives the row rule for both generators.

    What differs from the dormitory is what the moving faces drag with them. This
    layout is ASYMMETRIC: the entrance, window, bed, wardrobe and panel hang off
    the WEST inner face, while the санузел block (partition, shower, sink,
    toilet) and the desk/chair hang off the EAST one via ``san_x``. Thinning or
    dropping an end wall therefore moves those two groups by different amounts —
    the санузел keeps its 1100 mm because san_x and ix1 move together, and the
    living zone absorbs the difference. The dormitory got away without noticing
    because its layout is symmetric about the E-W axis; here it is visible, and
    it is only safe because these flags never make a wall THICKER than the
    envelope, so the clear interior can grow but not shrink. Pass a
    ``party_wall_thickness_mm`` above the series thickness and the collision
    check is what will tell you.

    ``door_swing`` is parameterised for the corridor scheme ("in"), for the same
    reason as the dormitory: a door onto a shared corridor that opens outward
    blocks the passage. NOT parameterised, on purpose: ``door_wall`` and
    ``window_wall``. In the dormitory those could be swapped N<->S freely because
    the furniture is mirror-symmetric; here the whole layout is built around
    entrance-on-S and window-on-N (convector under the window, panel beside the
    door, bed head to the north). Swapping them needs the layout mirrored, not a
    parameter, so a studio can serve a corridor to its SOUTH and not one to its
    north. That is a separate job, not a flag.

    ``room_number`` (optional) tags the room at the centre of its clear interior,
    as in the dormitory.

    NOT VERIFIED LIVE by default — this packed combination is returned
    verified=False; confirm by screenshot before treating it as final.

    All wall-mounted devices are placed depth/2 off the inner face with the back
    turned to their wall, per the пристенные-элементы rule in CLAUDE.md.
    """
    L = float(length_mm)
    W = float(width_mm)
    ox, oy = float(origin_x), float(origin_y)
    t = _resolve_wall_thickness(series, None)
    tp = (float(party_wall_thickness_mm) if party_wall_thickness_mm is not None
          else PARTY_WALL_THICKNESS)
    # Only the two SHORT end walls vary; N and S stay envelope (one faces the
    # corridor, the other the outdoors) — same as the dormitory.
    side_t = {"S": t, "N": t,
              "W": _end_wall_thickness("wall_west", wall_west, t, tp),
              "E": _end_wall_thickness("wall_east", wall_east, t, tp)}
    modkw = dict(module_origin=(ox, oy), module_length=L, module_width=W,
                 wall_thickness=t, side_thickness=side_t)
    # Inner faces in WORLD coordinates — see the dormitory generator: everything
    # absolute is written against these, so the origin propagates by construction.
    # Per-side since the party wall landed: the WEST group (door/window/bed/
    # wardrobe/panel) rides on ix0 and the EAST group (санузел, desk, chair) on
    # ix1, and those two now move independently.
    ix0, iy0 = ox + side_t["W"], oy + t
    ix1, iy1 = ox + L - side_t["E"], oy + W - t
    c = _Compose()
    c.absorb(scene)

    # 1) Standards + thick-wall shell.
    c.add("setup_layers", await setup_layers(backend))
    c.add("setup_dimstyle", await setup_dimstyle(backend))
    c.add("draw_module_outline",
          await draw_module_outline(backend, length_mm=L, width_mm=W, series=series,
                                    origin=(ox, oy), side_thickness=side_t))

    # Zone X-bands: living/bedroom (west, larger) | санузел (east, compact 1100).
    san_x = ix1 - 1100.0                      # санузел partition line

    # 2) Entrance (S) + window (N, over the living zone). Different walls -> the
    #    envelope cuts compose.
    #    door_center / win_center are WORLD X (the convector and the panel hang off
    #    them). The S and N walls both start at the SW/NW corner and run +X, so the
    #    wall-relative offset the opening generators want is (world X - ox); the
    #    generator re-adds module_origin internally. Passing the world X straight
    #    through would double-shift both openings — the exact bug this parameter
    #    was added to avoid.
    door_w = 950.0
    door_center = ix0 + 3100.0
    c.add("insert_exterior_door",
          await insert_exterior_door(backend, "S", door_center - door_w / 2.0 - ox,
                                     door_w, door_swing, label="ВХОД", **modkw))
    win_w, win_center = 1120.0, ix0 + 3050.0
    c.add("insert_window",
          await insert_window(backend, "N", win_center - win_w / 2.0 - ox, win_w,
                              label="ОКНО", **modkw))

    # 3) Double bed, head-to-NORTH (rot 0), NW corner. 1200 x 2000 (the
    #    generator's double): X = cx +/- bed_hw, Y = cy +/- 1000, head at +Y.
    #    bed_hw is derived from the real double width, NOT hardcoded — furniture
    #    placed east of the bed hangs off this, so it must track insert_bed.
    bed_w, bed_l = 1200.0, 2000.0
    bed_hw = bed_w / 2.0
    bed_cx, bed_cy = ix0 + 850.0, iy1 - 1000.0
    c.add("insert_bed", await insert_bed(backend, bed_cx, bed_cy, 0.0, "double"))

    # Nightstand beside the HEAD (north end), east of the bed, back to the north
    # wall — within reach of the pillow (not at the feet). 30 mm gap off the
    # bed's east edge.
    ns_w, ns_d = 450.0, 430.0
    c.add("insert_nightstand",
          await insert_nightstand(backend, bed_cx + bed_hw + 30.0 + ns_w / 2.0,
                                  iy1 - ns_d / 2.0, 180.0, width_mm=ns_w, depth_mm=ns_d))

    # Convector under the window. NO split-system (removed at user request); its
    # NE corner now holds the work desk — see below.
    conv_d = 95.0
    c.add("insert_convector",
          await insert_convector(backend, win_center, iy1 - conv_d / 2.0,
                                 1000.0, 180.0, depth_mm=conv_d))

    # 4) Work zone: a VERTICAL desk tucked into the NE corner of the living area
    #    (north wall + санузел partition) — the corner the split-system vacated.
    #    rot 90 turns the desk so width_mm runs N-S along the partition; its back
    #    sits on the partition's west face and its top edge on the north wall, so
    #    it "fits the corner" exactly. Chair pulled up on its west side.
    part_t = 100.0                            # санузел partition thickness (below)
    dsk_w, dsk_d = 1000.0, 550.0              # width runs N-S, depth into the room
    dsk_cx = (san_x - part_t / 2.0) - dsk_d / 2.0   # back on the partition west face
    dsk_cy = iy1 - dsk_w / 2.0                       # top edge on the north wall
    c.add("insert_table",
          await insert_table(backend, dsk_cx, dsk_cy, 90.0,
                             width_mm=dsk_w, depth_mm=dsk_d))
    # Chair faces EAST (rot -90) into the desk; front edge (local +240) just meets
    # the desk's west face.
    c.add("insert_chair",
          await insert_chair(backend, dsk_cx - dsk_d / 2.0 - 240.0, dsk_cy, -90.0))

    # Wardrobe on the south wall, east of the bed foot (which blocks x < bed_cx+bed_hw).
    wr_w, wr_d = 900.0, 420.0
    c.add("insert_wardrobe",
          await insert_wardrobe(backend, bed_cx + bed_hw + 100.0 + wr_w / 2.0,
                                iy0 + wr_d / 2.0, 0.0, width_mm=wr_w, depth_mm=wr_d))

    # Electrical panel on the south wall right beside the entrance (east jamb).
    ep = 285.0
    c.add("insert_electrical_panel",
          await insert_electrical_panel(backend, door_center + door_w / 2.0 + 40.0 + ep / 2.0,
                                        iy0 + ep / 2.0, 0.0, size_mm=ep))

    # 5) Санузел: FULL-height partition drawn as two segments with a door gap,
    #    plus the door leaf/arc. The opening is a NARROW 400 mm (half the earlier
    #    800) and the leaf swings WEST — into the living-room corridor, not into
    #    the tiny wet room. Placed near the south end so its swing clears the
    #    corner desk (which sits at y >= dsk top, well north of here).
    door_w_san = 400.0
    door_lo = iy0 + 250.0
    door_hi = door_lo + door_w_san                   # 400 mm opening
    c.add("insert_interior_wall[san_S]",
          await insert_interior_wall(backend, (san_x, iy0), (san_x, door_lo), part_t))
    c.add("insert_interior_wall[san_N]",
          await insert_interior_wall(backend, (san_x, door_hi), (san_x, iy1), part_t))
    c.add("санузел_door",
          await _draw_door_symbol(backend, (san_x, door_lo), (0.0, 1.0), (-1.0, 0.0),
                                  door_w_san))

    sh = 900.0
    c.add("insert_shower",
          await insert_shower(backend, ix1 - sh / 2.0, iy1 - sh / 2.0, 180.0, size_mm=sh))
    # Sink (500 x 400) on the east wall below the shower (rot -90, backs east),
    # with a CLEAR GAP to the shower tray (sink_gap) — the two must not touch.
    sink_d, sink_len = 400.0, 500.0
    sink_gap = 150.0                          # gap between sink top and shower tray
    c.add("insert_sink",
          await insert_sink(backend, ix1 - sink_d / 2.0,
                            (iy1 - sh) - sink_gap - sink_len / 2.0, -90.0))
    # Toilet on the SOUTH wall (rot 180, backs south), a touch smaller than the
    # 370x650 default. Nudged WEST of the sink's x-band so the sink can drop down
    # for its gap without clashing — still under the shower and off the east wall /
    # clear of the door. toi_d follows the scale so the back stays on the wall.
    toi_scale = 0.85
    toi_d = 650.0 * toi_scale
    c.add("insert_toilet",
          await insert_toilet(backend, san_x + 450.0, iy0 + toi_d / 2.0, 180.0,
                              scale=toi_scale))

    # Same automatic collision guard as the dormitory (general principle): geometry
    # + label overlap, and door-swing clearance, over every placed element
    # (excluding the shell). The studio stays verified=False by design (packed
    # novel combo needs a human screenshot), but any detected clash is still
    # listed explicitly so real problems surface.
    if room_number is not None:
        rx, ry = _room_number_position(ix0, iy0, ix1, iy1)
        c.add("insert_room_number",
              await insert_room_number(backend, rx, ry, room_number))

    open_sides = []
    if side_t["W"] <= 0.0:
        open_sides.append(("draw_module_outline:wall[W]", (ox - tp, iy0, ox, iy1)))
    if side_t["E"] <= 0.0:
        open_sides.append(("draw_module_outline:wall[E]",
                           (ox + L, iy0, ox + L + tp, iy1)))
    # Completeness. The studio has no count to fall short of the way bed_pairs
    # does, but its roster IS fixed, so the failure mode is a sub-generator that
    # returns ok:true having drawn nothing. Checking the roster against what
    # actually reached the box list catches that; without it a studio missing
    # its toilet passes every collision check, because a fixture that was never
    # drawn cannot collide with anything.
    roster = ("insert_exterior_door", "insert_window", "insert_bed",
              "insert_nightstand", "insert_convector", "insert_table",
              "insert_chair", "insert_wardrobe", "insert_electrical_panel",
              "insert_interior_wall[san_S]", "insert_interior_wall[san_N]",
              "insert_shower", "insert_sink", "insert_toilet")
    drawn = {n for n, _ in c.boxes}
    missing = [n for n in roster if n not in drawn]
    shortfalls = []
    if missing:
        shortfalls.append(("studio fixtures", len(roster), len(roster) - len(missing),
                           "missing: " + ", ".join(missing)))
    audit_warnings, _ = c.audit(exclude={"draw_module_outline"},
                                open_sides=open_sides, shortfalls=shortfalls)
    warnings = list(audit_warnings)
    warnings.append("Studio layout is a packed, verified=False-by-design "
                    "combination (double bed + enclosed санузел + engineering "
                    "symbols in one 6000x2400 scene). Confirm by screenshot "
                    "before treating it as final.")
    return c.result(series=str(series).strip().lower(), module=[L, W],
                    origin=[ox, oy], outer=[ox, oy, ox + L, oy + W],
                    wall_thickness=t, side_thickness=side_t,
                    inner=[ix0, iy0, ix1, iy1],
                    verified=False, warnings=warnings)
