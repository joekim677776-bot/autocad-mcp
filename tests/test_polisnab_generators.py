"""Offline (ezdxf) regression tests for the Polisnab node generators.

These assert the actual *drawn geometry* of the parametric generators — the
coverage that was missing when insert_bed silently drew a 1400 mm double bed
against the 1200x2000 spec (PROJECT-BRIEF section 7, and the "Кровать 1200x2000"
pixel-calibration anchor in polisnab_standards.py). No AutoCAD needed.
"""

import pytest

from autocad_mcp import polisnab_standards as ps
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend


@pytest.fixture
async def backend():
    b = EzdxfBackend()
    r = await b.initialize()
    assert r.ok
    return b


def _furn_x_extent(backend):
    """Width of the bounding box of every LWPOLYLINE on the FURN layer.

    A bed drawn at rotation 0 has its outer frame span x_mm ± width/2, and the
    outer frame is the widest polyline (blanket + pillows are inset), so this
    bbox width equals the bed's real drawn width.
    """
    xs = []
    for e in backend._msp.query("LWPOLYLINE"):
        if e.dxf.layer == ps.FURN_LAYER:
            xs.extend(p[0] for p in e.get_points())
    assert xs, "no FURN polylines were drawn"
    return max(xs) - min(xs)


class TestBedSize:
    async def test_double_bed_width_is_1200(self, backend):
        # Spec: double = 1200x2000. Guards against the 1400 regression.
        r = await ps.insert_bed(backend, 5000.0, 5000.0, 0.0, "double")
        assert r.ok
        assert _furn_x_extent(backend) == pytest.approx(1200.0)

    async def test_single_bed_width_is_825(self, backend):
        # Spec corrected 2026-07-24: single = 825x2000 (width 900->825, len 2000).
        r = await ps.insert_bed(backend, 5000.0, 5000.0, 0.0, "single")
        assert r.ok
        assert _furn_x_extent(backend) == pytest.approx(825.0)


class TestDormitoryLayout:
    async def test_beds_flush_to_walls_and_no_collisions(self, backend):
        # Task 1+2+3 (2026-07-24): beds flush to the long walls (0 mm gap), lockers
        # in the two west corners, and the auto collision check passes -> verified.
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic", 2)
        assert r.ok
        assert r.payload["verified"] is True, r.payload["warnings"]
        assert r.payload["warnings"] == []
        assert r.payload["bed_pairs_placed"] == 2

        # South bed row: outer (south) edge must sit on the inner south face (y=t).
        t = r.payload["wall_thickness"]
        ys = []
        for e in backend._msp.query("LWPOLYLINE"):
            if e.dxf.layer == ps.FURN_LAYER:
                ys.extend(p[1] for p in e.get_points())
        assert min(ys) == pytest.approx(t)          # flush to south inner face
        assert max(ys) == pytest.approx(2400.0 - t)  # flush to north inner face

    async def test_collision_check_flags_overlap(self, backend):
        # The guard must actually fire: two overlapping boxes -> a reported pair.
        c = ps._Compose()
        c.boxes = [("A", (0.0, 0.0, 100.0, 100.0)),
                   ("B", (50.0, 50.0, 150.0, 150.0)),   # overlaps A by 50x50
                   ("C", (100.0, 0.0, 200.0, 50.0))]    # only touches A/B at edges
        hits = c.intersections()
        assert len(hits) == 1
        assert "A" in hits[0] and "B" in hits[0]
        assert "50x50" in hits[0]


def _all_points(backend, layers=None):
    """Every vertex of every LWPOLYLINE/LINE/SOLID/CIRCLE-bbox drawn so far,
    optionally restricted to ``layers``. Used to prove a whole room translated
    rigidly, rather than trusting the generator's own reported bbox."""
    pts = []
    for e in backend._msp:
        if layers is not None and e.dxf.layer not in layers:
            continue
        t = e.dxftype()
        if t == "LWPOLYLINE":
            pts.extend((p[0], p[1]) for p in e.get_points())
        elif t == "LINE":
            pts.append((e.dxf.start.x, e.dxf.start.y))
            pts.append((e.dxf.end.x, e.dxf.end.y))
        elif t == "SOLID":
            pts.extend((e.dxf.get(f"vtx{i}").x, e.dxf.get(f"vtx{i}").y) for i in range(4))
        elif t == "CIRCLE":
            pts.append((e.dxf.center.x, e.dxf.center.y))
        elif t in ("MTEXT", "TEXT"):
            p = e.dxf.insert
            pts.append((p.x, p.y))
    return pts


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


class TestOrigin:
    """origin_x/origin_y (2026-07-24) — the blocker for multi-room complexes.

    The generators used to draw from a hardcoded (0,0). These assert that the
    parameter translates the ENTIRE room, tags included, and that the default
    still reproduces the old output exactly.
    """

    @pytest.mark.parametrize("gen,kwargs", [
        (ps.generate_dormitory_room, {"bed_pairs": 1}),
        (ps.generate_studio_module, {}),
    ])
    async def test_whole_room_translates_rigidly(self, gen, kwargs):
        from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
        dx, dy = 3000.0, 1500.0

        b0 = EzdxfBackend()
        assert (await b0.initialize()).ok
        r0 = await gen(b0, 6000.0, 2400.0, "arctic", **kwargs)
        assert r0.ok, r0.error

        b1 = EzdxfBackend()
        assert (await b1.initialize()).ok
        r1 = await gen(b1, 6000.0, 2400.0, "arctic", origin_x=dx, origin_y=dy, **kwargs)
        assert r1.ok, r1.error

        # Same entity count, and EVERY vertex moved by exactly (dx, dy) — this is
        # what catches a sub-generator still coordinating off a hardcoded 0,0
        # (or, for the studio openings, a DOUBLE shift).
        p0, p1 = _all_points(b0), _all_points(b1)
        assert len(p0) == len(p1)
        for (x0, y0), (x1, y1) in zip(p0, p1):
            assert x1 == pytest.approx(x0 + dx), f"x drift: {x0} -> {x1}"
            assert y1 == pytest.approx(y0 + dy), f"y drift: {y0} -> {y1}"

        # Tags (ВХОД / ОКНО / ЛОКЕРЫ) travel with the geometry, not left behind
        # in absolute coordinates.
        texts0 = _all_points(b0, layers={ps.TEXT_LAYER})
        texts1 = _all_points(b1, layers={ps.TEXT_LAYER})
        assert texts0, "expected opening/locker tags on the TEXT layer"
        for (x0, y0), (x1, y1) in zip(texts0, texts1):
            assert (x1, y1) == pytest.approx((x0 + dx, y0 + dy))

        # The shifted room actually sits where it was asked to. Measured on the
        # WALL layers only: the outward-swinging entrance arc (radius 950) and the
        # opening tags deliberately stick out past the envelope, so an all-entity
        # bbox is NOT the module outline.
        assert r1.payload["origin"] == [dx, dy]
        wall = _bbox(_all_points(b1, layers={ps.AR_WALL_LAYER, ps.AR_WALL_INSUL_LAYER}))
        assert wall == pytest.approx((dx, dy, dx + 6000.0, dy + 2400.0))

    async def test_shifted_dormitory_still_collision_free(self, backend):
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=1, origin_x=3000.0,
                                             origin_y=1500.0)
        assert r.ok, r.error
        assert r.payload["verified"] is True, r.payload["warnings"]
        assert r.payload["warnings"] == []
        assert r.payload["outer"] == [3000.0, 1500.0, 9000.0, 3900.0]

    async def test_two_rooms_side_by_side_do_not_overlap(self, backend):
        # The point of the whole exercise: two rooms on ONE drawing. 300 mm gap
        # between the modules' outer faces.
        gap = 300.0
        a = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=1, origin_x=0.0, origin_y=0.0)
        b = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=1, origin_x=6000.0 + gap,
                                             origin_y=0.0)
        assert a.ok and b.ok
        assert a.payload["verified"] and b.payload["verified"]

        # Room-to-room overlap test, same AABB rule _Compose uses internally.
        (ax0, ay0, ax1, ay1) = a.payload["bbox"]
        (bx0, by0, bx1, by1) = b.payload["bbox"]
        ox = min(ax1, bx1) - max(ax0, bx0)
        oy = min(ay1, by1) - max(ay0, by0)
        assert not (ox > 1e-6 and oy > 1e-6), f"rooms overlap by {ox}x{oy} mm"

        # And the second room really is the first one, translated — not a
        # coincidentally-similar layout.
        assert b.payload["outer"] == [6300.0, 0.0, 12300.0, 2400.0]


class TestSwingAndLabelCollisions:
    """The two gaps the first two-room test exposed (2026-07-24): the door-swing
    arc and the MTEXT tags were invisible to the AABB guard, so a leaf sweeping
    into the neighbouring module and a pair of overprinted labels both passed as
    `verified=True`. Both are now checked."""

    async def test_single_room_has_no_false_positives(self, backend):
        # Guard against over-eager checks. The dormitory entrance swings OUTWARD
        # into empty space, and _label_opening parks the ВХОД tag right in that
        # swept sector — if labels were treated as obstructions, every room ever
        # drawn would report a phantom swing clash.
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=2)
        assert r.ok
        assert r.payload["warnings"] == [], r.payload["warnings"]
        assert r.payload["verified"] is True
        assert r.payload["swings"], "the entrance must publish a swing zone"

    async def test_swing_zone_is_the_swept_sector(self, backend):
        # West wall, 950-wide leaf opening outward (-X). The zone must be the
        # quarter-disc: a full 950 deep in -X and 950 tall along the wall, with
        # the hinge corner included (that is what makes it reach the wall).
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=1)
        (name, x0, y0, x1, y1) = r.payload["swings"][0]
        assert name == "insert_exterior_door"
        assert x0 == pytest.approx(-950.0)
        assert x1 == pytest.approx(0.0, abs=1e-6)      # hinge sits on the wall face
        assert (y1 - y0) == pytest.approx(950.0)

    async def test_door_swinging_onto_furniture_in_its_own_room(self, backend):
        # Answers the scope question directly: the check is NOT limited to
        # conflicts between modules. A leaf fouling on a bed inside one room is
        # the same physical failure and must be reported the same way.
        c = ps._Compose()
        c.boxes = [("insert_bed[1S]", (-500.0, 800.0, 500.0, 1200.0))]
        c.swings = [("insert_exterior_door", (-950.0, 725.0, 0.0, 1675.0))]
        hits = c.swing_collisions()
        assert len(hits) == 1
        assert hits[0].startswith("door_swing_collision:")
        assert "insert_bed[1S]" in hits[0]

    async def test_labels_are_not_obstructions_for_a_swing(self, backend):
        # A tag sitting in the swept sector is not a physical obstruction.
        c = ps._Compose()
        c.labels = [("insert_exterior_door:label[ВХОД]", (-460.0, 1125.0, -40.0, 1275.0))]
        c.swings = [("insert_exterior_door", (-950.0, 725.0, 0.0, 1675.0))]
        assert c.swing_collisions() == []

    async def test_label_box_is_centred_on_the_insertion_point(self, backend):
        # The dispatcher draws labels with `_J _MC`, so (x, y) is the CENTRE.
        x0, y0, x1, y1 = ps._text_aabb(1000.0, 500.0, "ВХОД", 150.0)
        assert (x0 + x1) / 2.0 == pytest.approx(1000.0)
        assert (y0 + y1) / 2.0 == pytest.approx(500.0)
        assert (y1 - y0) == pytest.approx(150.0)
        assert (x1 - x0) == pytest.approx(4 * ps.LABEL_CHAR_WIDTH_FRAC * 150.0)

    async def test_two_rooms_300mm_gap_now_caught(self, backend):
        # The regression this whole change exists for. Same geometry as the
        # earlier two-room test, which passed as verified=True while the door
        # swung into the neighbour and the tags overprinted.
        a = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=1, origin_x=0.0, origin_y=0.0)
        assert a.payload["verified"] is True      # alone, room A is fine

        b = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=1, origin_x=6300.0,
                                             origin_y=0.0, scene=[a.payload])
        assert b.ok
        assert b.payload["verified"] is False, "the 300 mm gap must NOT pass"

        warnings = b.payload["warnings"]
        swing = [w for w in warnings if w.startswith("door_swing_collision:")]
        labels = [w for w in warnings if "label[" in w]

        # (a) the leaf reaches into room A - and specifically onto its beds.
        assert swing, warnings
        assert any("insert_bed" in w for w in swing), swing
        # (b) the ВХОД / ОКНО tags overprint.
        assert any("ВХОД" in w and "ОКНО" in w for w in labels), labels

        # The plain geometry AABB alone still sees nothing: the modules really do
        # not overlap. That is exactly why these two extra checks were needed.
        assert not _boxes_overlap_simple(a.payload["outer"], b.payload["outer"])

    async def test_wide_gap_clears_both_problems(self, backend):
        # ...and the fix works: give the door its 950 mm and the tags room, and
        # the pair passes. Proves the check is discriminating, not always-on.
        a = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=1, origin_x=0.0, origin_y=0.0)
        b = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic",
                                             bed_pairs=1, origin_x=8000.0,
                                             origin_y=0.0, scene=[a.payload])
        assert b.payload["warnings"] == [], b.payload["warnings"]
        assert b.payload["verified"] is True


CORRIDOR_KW = dict(door_wall="S", door_swing="in", window_wall="N")


class TestCorridorScheme:
    """Phase 4b step 1 (2026-07-24): a room served by a shared corridor.

    Encodes two architectural rules as assertions rather than prose:
      * a door onto a SHARED corridor opens INWARD (outward blocks the passage);
      * in a row of rooms, E/W are party walls, so the window moves to N.
    """

    async def _corridor(self, backend, length=12000.0):
        return await ps.insert_corridor(backend, 0.0, 0.0, length, ps.CORRIDOR_WIDTH,
                                        series="arctic")

    async def test_corridor_publishes_a_clearance_zone(self, backend):
        r = await self._corridor(backend)
        assert r.ok
        assert r.payload["clearances"] == [
            ["insert_corridor:passage", 0.0, 0.0, 12000.0, 2500.0]]

    async def test_corridor_walls_are_real_bands_outside_the_passage(self, backend):
        # Reworked 2026-07-24: walls are thick bands (faces + grey fill), not
        # zero-thickness lines. width_mm stays the CLEAR width, so the band is
        # drawn OUTSIDE it and the clearance zone is untouched by adding walls.
        r = await self._corridor(backend)
        assert r.payload["wall_thickness"] == 150.0     # envelope, not the 75 partition
        boxes = {n: b for n, *b in r.payload["boxes"]}
        assert boxes["insert_corridor:wall[S]"] == [0.0, -150.0, 12000.0, 0.0]
        # South band sits below the passage and only touches it.
        assert not _boxes_overlap_simple(boxes["insert_corridor:wall[S]"],
                                         (0.0, 0.0, 12000.0, 2500.0))
        # Real fill + faces on the right layers.
        layers = {e.dxf.layer for e in backend._msp}
        assert ps.AR_WALL_INSUL_LAYER in layers and ps.AR_WALL_LAYER in layers

    async def test_near_side_wall_is_left_to_the_rooms(self, backend):
        # The rooms' own south walls ARE the corridor's north wall; drawing one
        # here too would stack two bands face to face. Off by default, available.
        default = await self._corridor(backend)
        assert not any("wall[N]" in n for n, *_ in default.payload["boxes"])

        both = await ps.insert_corridor(backend, 0.0, 0.0, 12000.0, 2500.0,
                                        series="arctic", wall_north=True)
        boxes = {n: b for n, *b in both.payload["boxes"]}
        assert boxes["insert_corridor:wall[N]"] == [0.0, 2500.0, 12000.0, 2650.0]
        assert not _boxes_overlap_simple(boxes["insert_corridor:wall[N]"],
                                         (0.0, 0.0, 12000.0, 2500.0))

    async def test_corridor_wall_does_not_clash_with_the_room_it_serves(self, backend):
        # The whole point of leaving the near side off: room A's own south wall
        # occupies y 2500..2650, exactly where a corridor north wall would go.
        cor = await self._corridor(backend)
        a = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=0.0, origin_y=2500.0, scene=[cor.payload], **CORRIDOR_KW)
        assert a.payload["verified"] is True, a.payload["warnings"]
        room_shell = {n: b for n, *b in a.payload["boxes"]}["draw_module_outline"]
        corridor_wall = {n: b for n, *b in cor.payload["boxes"]}["insert_corridor:wall[S]"]
        assert not _boxes_overlap_simple(room_shell, corridor_wall)

    async def test_room_with_inward_door_onto_corridor(self, backend):
        # TEST 1: one room + one corridor segment, everything clean.
        cor = await self._corridor(backend)
        a = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=0.0, origin_y=2500.0, scene=[cor.payload], **CORRIDOR_KW)
        assert a.ok
        assert a.payload["warnings"] == [], a.payload["warnings"]
        assert a.payload["verified"] is True

    async def test_inward_swing_clears_this_rooms_own_furniture(self, backend):
        # The swing check applied to a NEW case: an inward leaf inside a furnished
        # room. Previously swing="in" had only ever been exercised on an isolated
        # interior door. The sector must land in the gap between the SW locker
        # bank and the south bed row - asserted, not assumed.
        cor = await self._corridor(backend)
        a = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=0.0, origin_y=2500.0, scene=[cor.payload], **CORRIDOR_KW)
        (_, sx0, sy0, sx1, sy1) = a.payload["swings"][0]
        # Sweeps INTO the room (north of the S wall), not into the corridor.
        assert sy0 >= 2500.0 and sy1 <= 4900.0
        assert not [w for w in a.payload["warnings"]
                    if w.startswith("door_swing_collision:")]

        boxes = {n: b for n, *b in a.payload["boxes"]}
        for name in ("insert_locker_row[SW]", "insert_bed[1S]", "insert_bed[1N]"):
            assert not _boxes_overlap_simple((sx0, sy0, sx1, sy1), boxes[name]), name

    async def test_outward_swing_onto_shared_corridor_is_rejected(self, backend):
        # The rule has teeth: the same room with swing="out" blocks the passage.
        cor = await self._corridor(backend)
        bad = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=0.0, origin_y=2500.0, scene=[cor.payload],
            door_wall="S", door_swing="out", window_wall="N")
        assert bad.payload["verified"] is False
        blocked = [w for w in bad.payload["warnings"]
                   if w.startswith("clearance_blocked:")]
        assert blocked, bad.payload["warnings"]
        assert "insert_corridor:passage" in blocked[0]

    async def test_two_rooms_along_one_side_of_the_corridor(self, backend):
        # TEST 2: two rooms in a row sharing a party wall at x=6000.
        cor = await self._corridor(backend)
        a = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=0.0, origin_y=2500.0, scene=[cor.payload], **CORRIDOR_KW)
        b = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=6000.0, origin_y=2500.0, scene=[cor.payload, a.payload],
            **CORRIDOR_KW)
        assert a.payload["verified"] is True, a.payload["warnings"]
        assert b.payload["verified"] is True, b.payload["warnings"]

        # Inward doors cannot reach each other through the corridor: both sectors
        # sit inside their own room. This is the whole point of swing="in".
        sa = a.payload["swings"][0][1:5]
        sb = b.payload["swings"][0][1:5]
        assert not _boxes_overlap_simple(sa, sb)

        # And the passage is clear along its full length: nothing physical from
        # either room intrudes into the corridor zone.
        passage = (0.0, 0.0, 12000.0, 2500.0)
        for room in (a, b):
            for name, *box in room.payload["boxes"]:
                assert not _boxes_overlap_simple(passage, box), f"{name} in corridor"
            assert not _boxes_overlap_simple(passage, room.payload["swings"][0][1:5])

    async def test_window_left_on_party_wall_is_rejected(self, backend):
        # Why window_wall must move to N in a row: on E, room A's ОКНО tag is
        # drawn 250 mm past the wall, i.e. physically inside room B.
        cor = await self._corridor(backend)
        a = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=0.0, origin_y=2500.0, scene=[cor.payload],
            door_wall="S", door_swing="in", window_wall="E")
        b = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=6000.0, origin_y=2500.0, scene=[cor.payload, a.payload],
            door_wall="S", door_swing="in", window_wall="E")
        assert b.payload["verified"] is False
        assert any("ОКНО" in w for w in b.payload["warnings"]), b.payload["warnings"]

    async def test_neighbours_label_landing_in_this_room_is_caught(self, backend):
        # Regression for the ordering blind spot found while building this step:
        # the shell exclusion (needed so furniture does not collide with its own
        # envelope) also hid foreign elements landing INSIDE this room. The
        # neighbour could not catch it either - this room did not exist yet.
        c = ps._Compose()
        c.boxes = [("draw_module_outline", (6000.0, 2500.0, 12000.0, 4900.0))]
        c.ext_labels = [("[room@0,2500] insert_window:label[ОКНО]",
                         (6040.0, 3625.0, 6460.0, 3775.0))]
        hits = c.intersections(exclude={"draw_module_outline"})
        assert len(hits) == 1, hits
        assert "420x150" in hits[0]

    async def test_room_overlapping_the_corridor_is_caught(self, backend):
        # The shell IS checked against the passage (exclude is not applied there):
        # a room built on top of the circulation route is exactly this check's job.
        c = ps._Compose()
        c.boxes = [("draw_module_outline", (0.0, 1000.0, 6000.0, 3400.0))]
        c.ext_clearances = [("insert_corridor:passage", (0.0, 0.0, 12000.0, 2500.0))]
        hits = c.clearance_violations(exclude={"draw_module_outline"})
        assert len(hits) == 1, hits
        assert hits[0].startswith("clearance_blocked:")


SOUTH_ROW_KW = dict(door_wall="N", door_swing="in", window_wall="S")


class TestDoubleLoadedCorridor:
    """Phase 4b step 2 (2026-07-24): a mirrored row on the far side of the
    corridor, so doors face each other across the passage."""

    async def _scene(self, backend, per_side, length):
        ny, sy = ps.corridor_row_origins(0.0, ps.CORRIDOR_WIDTH, 2400.0)
        cor = await ps.insert_corridor(backend, 0.0, 0.0, length, ps.CORRIDOR_WIDTH,
                                       series="arctic", label="КОРИДОР",
                                       wall_south=False, wall_north=False)
        scene, rooms = [cor.payload], []
        for i in range(per_side):
            for oy, kw in ((ny, CORRIDOR_KW), (sy, SOUTH_ROW_KW)):
                r = await ps.generate_dormitory_room(
                    backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
                    origin_x=6000.0 * i, origin_y=oy, scene=list(scene), **kw)
                scene.append(r.payload)
                rooms.append(r)
        return cor, rooms

    async def test_row_origins(self, backend):
        # north row adds the CORRIDOR width; south row subtracts the ROOM width.
        assert ps.corridor_row_origins(0.0, 2500.0, 2400.0) == (2500.0, -2400.0)

    async def test_south_row_is_an_exact_mirror_of_the_north_row(self):
        # The strongest statement of "no hidden S=corridor-side assumption":
        # every vertex maps under y -> 2500-y. Walls, swing arc, glazing, labels,
        # lockers and beds all mirror — checked against the drawing, not docstrings.
        from autocad_mcp.backends.ezdxf_backend import EzdxfBackend

        def verts(b):
            out = []
            for e in b._msp:
                t = e.dxftype()
                if t == "LWPOLYLINE":
                    out += [(round(p[0], 3), round(p[1], 3)) for p in e.get_points()]
                elif t == "LINE":
                    out += [(round(e.dxf.start.x, 3), round(e.dxf.start.y, 3)),
                            (round(e.dxf.end.x, 3), round(e.dxf.end.y, 3))]
                elif t == "SOLID":
                    out += [(round(e.dxf.get(f"vtx{i}").x, 3),
                             round(e.dxf.get(f"vtx{i}").y, 3)) for i in range(4)]
                elif t in ("MTEXT", "TEXT"):
                    out.append((round(e.dxf.insert.x, 3), round(e.dxf.insert.y, 3)))
                elif t == "CIRCLE":
                    out.append((round(e.dxf.center.x, 3), round(e.dxf.center.y, 3)))
            return out

        bn = EzdxfBackend(); await bn.initialize()
        await ps.generate_dormitory_room(bn, 6000.0, 2400.0, "arctic", bed_pairs=1,
                                         origin_x=0.0, origin_y=2500.0, **CORRIDOR_KW)
        bs = EzdxfBackend(); await bs.initialize()
        await ps.generate_dormitory_room(bs, 6000.0, 2400.0, "arctic", bed_pairs=1,
                                         origin_x=0.0, origin_y=-2400.0, **SOUTH_ROW_KW)
        mirrored = sorted((x, round(2500.0 - y, 3)) for x, y in verts(bn))
        assert mirrored == sorted(verts(bs))

    async def test_double_loaded_corridor_draws_no_walls_of_its_own(self, backend):
        # Both boundaries belong to rooms, so the corridor owns no wall bands.
        cor, _ = await self._scene(backend, 1, 6000.0)
        assert cor.payload["boxes"] == []
        assert cor.payload["clearances"] == [
            ["insert_corridor:passage", 0.0, 0.0, 6000.0, 2500.0]]

    async def test_one_pair_face_to_face(self, backend):
        # TEST 1.
        cor, rooms = await self._scene(backend, 1, 6000.0)
        for r in rooms:
            assert r.payload["verified"] is True, r.payload["warnings"]

        # Both leaves sweep INTO their own room, so neither enters the passage
        # and they cannot reach each other. This is what swing="in" buys.
        passage = (0.0, 0.0, 6000.0, 2500.0)
        swings = [r.payload["swings"][0][1:5] for r in rooms]
        for s in swings:
            assert not _boxes_overlap_simple(passage, s)
        assert not _boxes_overlap_simple(swings[0], swings[1])

    async def test_two_per_side(self, backend):
        # TEST 2: four rooms, two doors on each side of the passage.
        cor, rooms = await self._scene(backend, 2, 12000.0)
        assert len(rooms) == 4
        for r in rooms:
            assert r.payload["verified"] is True, r.payload["warnings"]

        passage = (0.0, 0.0, 12000.0, 2500.0)
        for r in rooms:
            for name, *box in r.payload["boxes"]:
                assert not _boxes_overlap_simple(passage, box), name
            assert not _boxes_overlap_simple(passage, r.payload["swings"][0][1:5])

    async def test_outward_swing_blocks_the_passage_from_either_side(self, backend):
        # The rule has teeth on both rows, not just the north one.
        ny, sy = ps.corridor_row_origins(0.0, ps.CORRIDOR_WIDTH, 2400.0)
        cor = await ps.insert_corridor(backend, 0.0, 0.0, 6000.0, ps.CORRIDOR_WIDTH,
                                       series="arctic",
                                       wall_south=False, wall_north=False)
        for oy, dw, ww in ((ny, "S", "N"), (sy, "N", "S")):
            bad = await ps.generate_dormitory_room(
                backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
                origin_x=0.0, origin_y=oy, scene=[cor.payload],
                door_wall=dw, door_swing="out", window_wall=ww)
            assert bad.payload["verified"] is False
            assert any(w.startswith("clearance_blocked:")
                       for w in bad.payload["warnings"]), bad.payload["warnings"]

    async def test_geometry_check_does_NOT_validate_corridor_width(self, backend):
        # Deliberate negative result, recorded so nobody later mistakes a green
        # test for evidence that 2500 mm is right. With swing="in" the leaves
        # never enter the passage, so its width constrains nothing measurable
        # here — a 1000 mm corridor passes every check identically. 2500 mm is a
        # human-circulation figure (two people passing) and needs СП/ГОСТ, not AABB.
        ny, sy = ps.corridor_row_origins(0.0, 1000.0, 2400.0)
        cor = await ps.insert_corridor(backend, 0.0, 0.0, 6000.0, 1000.0,
                                       series="arctic",
                                       wall_south=False, wall_north=False)
        scene = [cor.payload]
        for oy, kw in ((ny, CORRIDOR_KW), (sy, SOUTH_ROW_KW)):
            r = await ps.generate_dormitory_room(
                backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
                origin_x=0.0, origin_y=oy, scene=list(scene), **kw)
            assert r.payload["verified"] is True, r.payload["warnings"]
            scene.append(r.payload)


class TestSharedPartyWall:
    """Phase 4b step 3 (2026-07-27): neighbours in a row share ONE wall.

    Before this, two rooms tiled at pitch 6000 each drew their own end wall and
    the boundary came out 300 mm thick — two 150 mm insulated envelopes face to
    face, between two heated rooms. The rule (same one insert_corridor already
    states for its own sides) is that a boundary is drawn ONCE, by whoever owns
    it, and here that owner is the room to the west.
    """

    async def _room(self, backend, i, n, **kw):
        return await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=6000.0 * i, origin_y=0.0, **CORRIDOR_KW,
            **{**ps.room_row_walls(i, n), **kw})

    def _wall_lines(self, backend, y_lo, y_hi):
        """Vertical AR-WALL *face* lines inside a y band, as {x: [(y0, y1), ...]}.

        Jambs (the short lines across a door/window opening, one wall thickness
        long) are dropped — they are vertical too on an E/W run, and counting
        them would drown the face positions this is looking for."""
        out = {}
        for e in backend._msp.query("LINE"):
            if e.dxf.layer != ps.AR_WALL_LAYER:
                continue
            s, t = e.dxf.start, e.dxf.end
            if abs(s.x - t.x) > 1e-6 or min(s.y, t.y) < y_lo or max(s.y, t.y) > y_hi:
                continue
            if abs(s.y - t.y) < 500.0:              # a jamb, not a wall face
                continue
            out.setdefault(round(s.x, 3), []).append(
                (round(min(s.y, t.y), 3), round(max(s.y, t.y), 3)))
        return out

    def test_row_walls_rule(self):
        # Only room 0 draws a west wall; only the last draws a full east wall.
        assert ps.room_row_walls(0, 1) == {"wall_west": True, "wall_east": True}
        assert [ps.room_row_walls(i, 3) for i in range(3)] == [
            {"wall_west": True, "wall_east": "party"},
            {"wall_west": False, "wall_east": "party"},
            {"wall_west": False, "wall_east": True},
        ]
        with pytest.raises(ValueError):
            ps.room_row_walls(2, 2)

    async def test_boundary_between_neighbours_is_ONE_wall(self, backend):
        # The whole point. Between the two interiors there must be exactly one
        # band: faces at x=5900 and x=6000, nothing at 6150 (which is where the
        # second room's own west face used to land).
        a = await self._room(backend, 0, 2)
        b = await self._room(backend, 1, 2, scene=[a.payload])
        assert a.ok and b.ok

        faces = self._wall_lines(backend, 0.0, 2400.0)
        near = {x: v for x, v in faces.items() if 5000.0 < x < 7000.0}
        assert sorted(near) == [5900.0, 6000.0], near
        # ...and they are the two faces of one full-height band, not stubs.
        assert near[5900.0] == [(150.0, 2250.0)]     # inner face of room A
        assert near[6000.0] == [(0.0, 2400.0)]       # outer face = room B's west

        # Room A's interior ends at 5900, room B's starts at 6000: 100 mm apart.
        assert a.payload["inner"][2] == pytest.approx(5900.0)
        assert b.payload["inner"][0] == pytest.approx(6000.0)
        assert a.payload["side_thickness"]["E"] == ps.PARTY_WALL_THICKNESS
        assert b.payload["side_thickness"]["W"] == 0.0

    async def test_party_wall_is_thinner_than_the_envelope(self, backend):
        # Not a style choice: both sides are heated, so the insulated envelope
        # thickness has no job here. Guards against a silent revert to 150.
        assert ps.PARTY_WALL_THICKNESS < ps.INSULATION_THICKNESS["arctic"]
        a = await self._room(backend, 0, 2)
        bands = {n.rsplit("[", 1)[-1].rstrip("]"): b
                 for n, *b in a.payload["wall_bands"]}
        assert bands["E"] == [5900.0, 0.0, 6000.0, 2400.0]     # 100 mm
        assert bands["W"] == [0.0, 0.0, 150.0, 2400.0]         # 150 mm envelope
        # An explicit override reaches the drawing.
        c = await self._room(backend, 0, 2, party_wall_thickness_mm=80.0)
        assert c.payload["side_thickness"]["E"] == 80.0
        assert c.payload["inner"][2] == pytest.approx(5920.0)

    async def test_pitch_is_unchanged_so_the_row_still_tiles(self, backend):
        # Rooms still sit outer-edge to outer-edge at pitch = length_mm; only the
        # band inside the envelope changed. A four-room row is exactly 24 m.
        rooms = []
        scene = []
        for i in range(4):
            r = await self._room(backend, i, 4, scene=list(scene))
            rooms.append(r)
            scene.append(r.payload)
        assert rooms[0].payload["outer"][0] == 0.0
        assert rooms[-1].payload["outer"][2] == 24000.0
        for i, r in enumerate(rooms):
            assert r.payload["verified"] is True, (i, r.payload["warnings"])

    async def test_end_walls_of_the_row_stay_full_envelope(self, backend):
        # West end of the first room and east end of the last are OUTSIDE walls;
        # they must not be thinned along with the shared ones.
        first = await self._room(backend, 0, 3)
        last = await self._room(backend, 2, 3)
        assert first.payload["side_thickness"]["W"] == 150.0
        assert last.payload["side_thickness"]["E"] == 150.0

    async def test_furniture_follows_the_moved_inner_faces(self, backend):
        # Room 1 has no west wall of its own and a 100 mm east wall, so both end
        # faces moved. Everything positional is written against ix0/ix1, and this
        # asserts it really is: lockers start ON the west inner face, bed heads
        # stay 50 mm off the east inner face.
        b = await self._room(backend, 1, 3)
        boxes = {n: box for n, *box in b.payload["boxes"]}
        ix0, _, ix1, _ = b.payload["inner"]
        assert boxes["insert_locker_row[SW]"][0] == pytest.approx(ix0)
        assert boxes["insert_locker_row[NW]"][0] == pytest.approx(ix0)
        assert boxes["insert_bed[1S]"][2] == pytest.approx(ix1 - 50.0)
        # No lingering 150 mm assumption: with no west wall the row starts at the
        # room's own outer edge, not 150 mm into it.
        assert ix0 == pytest.approx(b.payload["outer"][0])

    async def test_a_row_of_four_has_no_double_wall_anywhere(self, backend):
        # Sweep the whole row: at each of the three internal boundaries there are
        # exactly two vertical faces (one band), and at the two ends exactly two
        # more, 150 mm apart. Catches a regression at any position, not just the
        # first junction.
        scene = []
        for i in range(4):
            r = await self._room(backend, i, 4, scene=list(scene))
            scene.append(r.payload)
        faces = self._wall_lines(backend, 0.0, 2400.0)
        assert sorted(faces) == [
            0.0, 150.0,                                  # west end (envelope)
            5900.0, 6000.0,                              # party walls...
            11900.0, 12000.0,
            17900.0, 18000.0,
            23850.0, 24000.0,                            # east end (envelope)
        ], sorted(faces)

    async def test_open_side_with_no_neighbour_is_caught(self, backend):
        # The check that had to arrive with the feature: declining to draw a wall
        # is only correct if somebody else drew it. Alone, this room is open to
        # the weather and every other check passes happily.
        r = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=6000.0, origin_y=0.0, wall_west=False, **CORRIDOR_KW)
        assert r.ok
        assert r.payload["verified"] is False
        assert any(w.startswith("open_side:") for w in r.payload["warnings"]), \
            r.payload["warnings"]

    async def test_open_side_is_not_satisfied_by_a_neighbours_shell_alone(self, backend):
        # The subtle version: BOTH neighbours skip the boundary, each expecting
        # the other. Their shell bboxes still overlap the strip, so a check
        # written against `boxes` would pass — it is matched against the wall
        # bands actually drawn, so it fails as it should.
        a = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=0.0, origin_y=0.0, wall_east=False, **CORRIDOR_KW)
        b = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            origin_x=6000.0, origin_y=0.0, scene=[a.payload],
            wall_west=False, **CORRIDOR_KW)
        assert b.payload["verified"] is False
        assert any(w.startswith("open_side:") for w in b.payload["warnings"]), \
            b.payload["warnings"]

    async def test_opening_on_an_undrawn_wall_is_rejected(self, backend):
        # Hard error, not a warning: there is no wall to cut, so the "opening"
        # would be a pair of jamb lines floating in air.
        r = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            wall_west=False, door_wall="W")
        assert r.ok is False
        assert "not drawn" in r.error

    async def test_opening_on_a_party_wall_is_flagged(self, backend):
        # Buildable but wrong: a window on a party wall looks into the room next
        # door. Warned rather than rejected — the distinction is deliberate.
        r = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
            wall_east="party", window_wall="E", door_wall="S", door_swing="in")
        assert r.ok
        assert r.payload["verified"] is False
        assert any("PARTY wall" in w for w in r.payload["warnings"]), \
            r.payload["warnings"]

    async def test_door_redraw_keeps_the_mixed_corners(self, backend):
        # The trap this feature could have shipped with: an opening ERASES and
        # REDRAWS its whole wall side, so if the cutter does not get the same
        # per-side thicknesses the outline used, the S wall comes back inset by
        # 150 mm at a corner where its neighbour is 100 mm (or absent).
        b = EzdxfBackend()
        assert (await b.initialize()).ok
        await ps.generate_dormitory_room(
            b, 6000.0, 2400.0, "arctic", bed_pairs=1, origin_x=6000.0, origin_y=0.0,
            wall_west=False, wall_east="party", **CORRIDOR_KW)
        # The S inner face (y=150) runs from the room's own outer edge (no west
        # wall to inset from) to the party wall's inner face, minus the door gap.
        segs = sorted((round(min(e.dxf.start.x, e.dxf.end.x), 3),
                       round(max(e.dxf.start.x, e.dxf.end.x), 3))
                      for e in b._msp.query("LINE")
                      if e.dxf.layer == ps.AR_WALL_LAYER
                      and abs(e.dxf.start.y - 150.0) < 1e-6
                      and abs(e.dxf.end.y - 150.0) < 1e-6)
        assert segs[0][0] == pytest.approx(6000.0)     # not 6150
        assert segs[-1][1] == pytest.approx(11900.0)   # not 11850

    async def test_uniform_walls_are_byte_identical_to_before(self, backend):
        # The defaults must not have moved: a free-standing module still draws
        # four 150 mm envelope walls with the same inner rectangle.
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic", 1)
        assert r.payload["side_thickness"] == {"S": 150.0, "N": 150.0,
                                               "W": 150.0, "E": 150.0}
        assert r.payload["inner"] == [150.0, 150.0, 5850.0, 2250.0]
        assert r.payload["verified"] is True, r.payload["warnings"]


class TestDoubleLoadedCorridorSharedWalls:
    """Step 3's live scene: 2+2 rooms around a double-loaded corridor, each row
    sharing one wall between its neighbours."""

    async def _scene(self, backend, per_side, length):
        ny, sy = ps.corridor_row_origins(0.0, ps.CORRIDOR_WIDTH, 2400.0)
        cor = await ps.insert_corridor(backend, 0.0, 0.0, length, ps.CORRIDOR_WIDTH,
                                       series="arctic", label="КОРИДОР",
                                       wall_south=False, wall_north=False)
        scene, rooms = [cor.payload], []
        for i in range(per_side):
            for oy, kw in ((ny, CORRIDOR_KW), (sy, SOUTH_ROW_KW)):
                r = await ps.generate_dormitory_room(
                    backend, 6000.0, 2400.0, "arctic", bed_pairs=1,
                    origin_x=6000.0 * i, origin_y=oy, scene=list(scene),
                    **kw, **ps.room_row_walls(i, per_side))
                scene.append(r.payload)
                rooms.append(r)
        return cor, rooms

    async def test_four_rooms_two_rows_still_verify(self, backend):
        cor, rooms = await self._scene(backend, 2, 12000.0)
        assert len(rooms) == 4
        for r in rooms:
            assert r.payload["verified"] is True, r.payload["warnings"]
            assert r.payload["warnings"] == []

        # The passage stays clear of everything, as in step 2.
        passage = (0.0, 0.0, 12000.0, 2500.0)
        for r in rooms:
            for name, *box in r.payload["boxes"]:
                assert not _boxes_overlap_simple(passage, box), name
            assert not _boxes_overlap_simple(passage, r.payload["swings"][0][1:5])

    async def test_both_rows_share_a_wall_not_two(self, backend):
        # Mirrored rows, so the party wall has to be right on BOTH sides of the
        # corridor — the south row runs its door/window on the opposite walls and
        # is the row where a hidden "S is the corridor side" assumption would show.
        await self._scene(backend, 2, 12000.0)
        for y_lo, y_hi in ((2500.0, 4900.0), (-2400.0, 0.0)):
            faces = TestSharedPartyWall()._wall_lines(backend, y_lo, y_hi)
            assert sorted(faces) == [0.0, 150.0, 5900.0, 6000.0, 11850.0, 12000.0], \
                (y_lo, sorted(faces))


class TestRoomNumber:
    """Phase 4b (2026-07-27): rooms can be numbered, but never by themselves."""

    async def test_number_is_annotation_not_geometry(self, backend):
        # No bbox in the payload -> _Compose files it under labels, not boxes.
        # A room number must not obstruct a door or count as furniture.
        r = await ps.insert_room_number(backend, 3000.0, 1200.0, "101")
        assert r.ok
        assert r.payload["bbox"] is None
        assert r.payload["label_bboxes"] == [["101", [2842.5, 1125.0, 3157.5, 1275.0]]]

    async def test_absent_by_default(self, backend):
        # Nothing is numbered unless asked: the generator cannot know which room
        # of the building this is.
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic", 1)
        assert not any("insert_room_number" in n for n, *_ in r.payload["labels"])

    async def test_dormitory_number_sits_in_the_clear_centre(self, backend):
        r = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1, room_number="101",
            **CORRIDOR_KW)
        assert r.payload["verified"] is True, r.payload["warnings"]
        labels = {n: b for n, *b in r.payload["labels"]}
        box = labels["insert_room_number:label[101]"]
        ix0, iy0, ix1, iy1 = r.payload["inner"]
        assert (box[0] + box[2]) / 2.0 == pytest.approx((ix0 + ix1) / 2.0)
        assert (box[1] + box[3]) / 2.0 == pytest.approx((iy0 + iy1) / 2.0)
        # ...and it really is clear of the furniture, not merely centred. The
        # shell is skipped for the same reason the audit skips it: its bbox is
        # the whole module, so everything inside "overlaps" it.
        for name, *fbox in r.payload["boxes"]:
            if name == "draw_module_outline":
                continue
            assert not _boxes_overlap_simple(box, fbox), name

    async def test_studio_number_is_clear_too(self, backend):
        # The studio is the packed layout; if the centre were going to land on
        # something, it would be here.
        s = await ps.generate_studio_module(backend, room_number="201")
        labels = {n: b for n, *b in s.payload["labels"]}
        box = labels["insert_room_number:label[201]"]
        for name, *fbox in s.payload["boxes"]:
            if name == "draw_module_outline":
                continue
            assert not _boxes_overlap_simple(box, fbox), name

    async def test_a_number_landing_on_furniture_is_reported(self, backend):
        # The check has teeth: the position is not asserted to be safe for an
        # arbitrary layout, it is CHECKED. Simulated by planting a label on a bed.
        c = ps._Compose()
        c.boxes = [("insert_bed[1S]", (3850.0, 2650.0, 5850.0, 3475.0))]
        c.labels = [("insert_room_number:label[101]",
                     (4842.5, 3025.0, 5157.5, 3175.0))]
        hits = c.intersections()
        assert len(hits) == 1 and "insert_room_number" in hits[0], hits


class TestCorridorEnds:
    """Phase 4b (2026-07-27): corridor ends become statable, NOT closed.

    The step-1 reasoning stands - a blank wall across an escape route
    misinforms while nobody knows what is really at the end - so the default is
    still 'not drawn'. What changes is that a caller who DOES know can say so.
    """

    async def _corridor(self, backend, **kw):
        return await ps.insert_corridor(backend, 0.0, 0.0, 12000.0, 2500.0,
                                        series="arctic", wall_south=False,
                                        wall_north=False, **kw)

    async def test_default_is_still_open(self, backend):
        r = await self._corridor(backend)
        assert r.payload["ends"] == {"W": None, "E": None}
        assert r.payload["boxes"] == []
        assert r.payload["swings"] == []

    async def test_solid_end_is_a_band_outside_the_passage(self, backend):
        r = await self._corridor(backend, end_west="wall")
        boxes = {n: b for n, *b in r.payload["boxes"]}
        assert boxes["insert_corridor:end[W]"] == [-150.0, 0.0, 0.0, 2500.0]
        # Outside the clear length, so the passage rect is untouched by it.
        passage = r.payload["clearances"][0][1:5]
        assert not _boxes_overlap_simple(boxes["insert_corridor:end[W]"], passage)

    async def test_door_end_leaves_a_hole_and_swings_outward(self, backend):
        r = await self._corridor(backend, end_east="door")
        boxes = {n: b for n, *b in r.payload["boxes"]}
        # Two band segments with a 950 gap between them, centred on the passage.
        assert boxes["insert_corridor:end[E1]"] == [12000.0, 0.0, 12150.0, 775.0]
        assert boxes["insert_corridor:end[E2]"] == [12000.0, 1725.0, 12150.0, 2500.0]
        opening = {n: b for n, *b in r.payload["openings"]}
        assert opening["insert_corridor:end_opening[E]"] == \
            [12000.0, 775.0, 12150.0, 1725.0]
        # The leaf sweeps AWAY from the corridor - egress direction - so the
        # sector is entirely east of the passage.
        swing = {n: b for n, *b in r.payload["swings"]}["insert_corridor:end_door[E]"]
        assert swing[0] >= 12150.0
        assert not _boxes_overlap_simple(swing, r.payload["clearances"][0][1:5])

    async def test_end_geometry_is_visible_to_the_checks(self, backend):
        # Not just drawn: an absorbed corridor's end wall and end door take part
        # in the collision families like anything else.
        r = await self._corridor(backend, end_west="wall", end_east="door")
        c = ps._Compose()
        c.absorb([r.payload])
        assert c.clearance_violations() == []          # nothing intrudes
        names = [n for n, _ in c.ext_boxes]
        assert any("end[W]" in n for n in names), names
        assert any("end_door[E]" in n for n, _ in c.ext_swings)
        # The end band is published as a wall band, so a room butted against the
        # corridor end can prove the wall it declined to draw is really there.
        assert any(b == (-150.0, 0.0, 0.0, 2500.0) for _, b in c.ext_wall_bands)

    async def test_inward_leaf_would_block_the_passage(self, backend):
        # Why the leaf faces out, asserted rather than asserted-in-prose: the
        # same door turned round is a clearance violation.
        r = await self._corridor(backend)
        c = ps._Compose()
        c.absorb([r.payload])
        c.swings = [("end_door_turned_inward", (11050.0, 775.0, 12000.0, 1725.0))]
        hits = c.clearance_violations()
        assert len(hits) == 1 and hits[0].startswith("clearance_blocked:"), hits

    async def test_sealing_both_ends_is_reported(self, backend):
        r = await self._corridor(backend, end_west="wall", end_east="wall")
        assert r.ok
        assert any("nobody can leave" in w for w in r.payload["warnings"]), \
            r.payload["warnings"]
        # One door is enough to make it a route again.
        ok = await self._corridor(backend, end_west="wall", end_east="door")
        assert ok.payload["warnings"] == []

    async def test_unknown_end_value_is_rejected(self, backend):
        with pytest.raises(ValueError):
            await self._corridor(backend, end_west="hatch")


class TestStudioPartyWall:
    """Phase 4b (2026-07-27): the dormitory's party-wall idiom, ported."""

    async def _row(self, backend, i, n, **kw):
        return await ps.generate_studio_module(
            backend, 6000.0, 2400.0, "arctic", origin_x=6000.0 * i, origin_y=0.0,
            door_swing="in", **{**ps.room_row_walls(i, n), **kw})

    async def test_same_flags_same_thickness_as_dormitory(self, backend):
        a = await self._row(backend, 0, 2)
        b = await self._row(backend, 1, 2, scene=[a.payload])
        assert a.payload["side_thickness"]["E"] == ps.PARTY_WALL_THICKNESS
        assert b.payload["side_thickness"]["W"] == 0.0
        # One band between the two interiors, 100 mm.
        assert a.payload["inner"][2] == pytest.approx(5900.0)
        assert b.payload["inner"][0] == pytest.approx(6000.0)

    async def test_boundary_is_one_wall(self, backend):
        a = await self._row(backend, 0, 2)
        await self._row(backend, 1, 2, scene=[a.payload])
        faces = sorted({round(e.dxf.start.x, 3) for e in backend._msp.query("LINE")
                        if e.dxf.layer == ps.AR_WALL_LAYER
                        and abs(e.dxf.start.x - e.dxf.end.x) < 1e-6
                        and abs(e.dxf.start.y - e.dxf.end.y) > 500.0})
        # west envelope | sanuzel A | PARTY | sanuzel B | east envelope
        assert faces == [0.0, 150.0, 4750.0, 4850.0, 5900.0, 6000.0,
                         10700.0, 10800.0, 11850.0, 12000.0], faces

    async def test_the_asymmetry_the_dormitory_hid(self, backend):
        # The dormitory is mirror-symmetric E-W, so moving both end faces went
        # unnoticed. The studio is not: the WEST group (bed) rides on ix0 and the
        # EAST group (sanuzel) on ix1, and they move by different amounts.
        first = await self._row(backend, 0, 3)     # W=150 envelope, E=100 party
        mid = await self._row(backend, 1, 3)       # W=0   neighbour, E=100 party
        assert mid.payload["inner"][0] - first.payload["inner"][0] == \
            pytest.approx(6000.0 - 150.0)          # west face moved out by 150
        assert mid.payload["inner"][2] - first.payload["inner"][2] == \
            pytest.approx(6000.0)                  # east face moved by the pitch
        # The sanuzel keeps its 1100 mm regardless, because san_x tracks ix1.
        for r in (first, mid):
            partition = [b for n, *b in r.payload["boxes"] if "san_N" in n][0]
            assert r.payload["inner"][2] - partition[2] == pytest.approx(1050.0)

    async def test_open_side_applies_to_the_studio_too(self, backend):
        lone = await ps.generate_studio_module(
            backend, origin_x=6000.0, origin_y=0.0, wall_west=False)
        assert any(w.startswith("open_side:") for w in lone.payload["warnings"]), \
            lone.payload["warnings"]

    async def test_row_has_no_real_collisions(self, backend):
        # verified stays False by design for the studio, so assert on the
        # collision families instead of on the flag.
        scene = []
        for i in range(2):
            r = await self._row(backend, i, 2, scene=list(scene),
                                room_number=f"{201 + i}")
            real = [w for w in r.payload["warnings"]
                    if w.startswith(("collision:", "door_swing_collision:",
                                     "clearance_blocked:", "open_side:"))]
            assert real == [], (i, real)
            scene.append(r.payload)

    async def test_door_swing_is_parameterised_but_walls_are_not(self, backend):
        # What was added, and what deliberately was not: "in" for a corridor,
        # while door_wall/window_wall stay fixed because this layout would need
        # mirroring rather than a flag.
        import inspect
        sig = inspect.signature(ps.generate_studio_module)
        assert "door_swing" in sig.parameters
        assert "door_wall" not in sig.parameters
        assert "window_wall" not in sig.parameters
        out = await ps.generate_studio_module(backend, door_swing="out")
        inn = await ps.generate_studio_module(backend, door_swing="in")
        s_out = {n: b for n, *b in out.payload["swings"]}["insert_exterior_door"]
        s_in = {n: b for n, *b in inn.payload["swings"]}["insert_exterior_door"]
        assert s_out[1] < 0.0            # sweeps south, outside the module
        assert s_in[1] >= 150.0          # sweeps north, into the room


class TestCompleteness:
    """Phase 4b (2026-07-28): a room that came out smaller than ordered.

    Every other family asks whether something is in the way. This one asks
    whether what was ordered got built - and it exists because the answer used
    to be flattering: a dormitory that dropped both bed pairs for lack of floor
    reported verified=True, because there were no beds left to collide.
    """

    async def test_shortfall_forces_verified_false(self, backend):
        # The exact configuration from the diagnostic: swapped proportions with
        # the furniture frame still on X, so no bed fits at all. It used to come
        # back verified=True with zero beds.
        r = await ps.generate_dormitory_room(
            backend, 2400.0, 6000.0, "arctic", bed_pairs=2, bed_axis="x")
        assert r.ok
        assert r.payload["beds_placed"] == 0
        assert r.payload["verified"] is False
        inc = [w for w in r.payload["warnings"] if w.startswith("incomplete:")]
        assert len(inc) == 1, r.payload["warnings"]
        assert "0 of 4 placed" in inc[0]

    async def test_reason_travels_with_the_count(self, backend):
        # "3 of 4" says something is wrong; the reason says what to do about it.
        r = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=2, **CORRIDOR_KW)
        inc = [w for w in r.payload["warnings"] if w.startswith("incomplete:")]
        assert inc and "S row 1/2" in inc[0] and "N row 2/2" in inc[0], inc

    async def test_a_complete_room_says_nothing(self, backend):
        # Discriminating, not always-on.
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic", 2)
        assert r.payload["beds_placed"] == r.payload["beds_requested"] == 4
        assert not [w for w in r.payload["warnings"] if w.startswith("incomplete:")]

    async def test_studio_roster_is_checked(self, backend):
        # The studio has no count to fall short of, but it does have a fixed
        # roster; a fixture that was never drawn cannot collide with anything.
        c = ps._Compose()
        hits = c.completeness_violations(
            [("studio fixtures", 14, 13, "missing: insert_toilet")])
        assert len(hits) == 1 and "insert_toilet" in hits[0]
        s = await ps.generate_studio_module(backend)
        assert not [w for w in s.payload["warnings"] if w.startswith("incomplete:")]


class TestFourBedsOnACorridor:
    """Phase 4b (2026-07-28): the bug the rotation diagnostic turned up.

    6000x2400 + bed_pairs=2 + a door onto the corridor drew a bed straight
    through the entrance's swing - 875x825 mm of overlap. Never caught, because
    bed_pairs=2 was only ever tested with the default west door and the corridor
    scheme only ever with bed_pairs=1.
    """

    async def test_no_bed_in_the_door_swing(self, backend):
        r = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=2, **CORRIDOR_KW)
        assert not [w for w in r.payload["warnings"]
                    if w.startswith("door_swing_collision:")], r.payload["warnings"]
        swing = {n: b for n, *b in r.payload["swings"]}["insert_exterior_door"]
        for name, *box in r.payload["boxes"]:
            if name.startswith("insert_bed"):
                assert not _boxes_overlap_simple(swing, box), name

    async def test_the_door_row_loses_a_bed_and_says_so(self, backend):
        # Fixing the collision does not conjure floor space: with the entrance
        # mid-wall and the lockers at the other end, the south row holds one bed.
        # The honest outcome is 3 of 4 reported, not 4 drawn through the door.
        r = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=2, **CORRIDOR_KW)
        assert r.payload["beds_placed"] == 3
        assert r.payload["verified"] is False
        assert any(w.startswith("incomplete:") for w in r.payload["warnings"])

    async def test_the_default_west_door_is_untouched(self, backend):
        # The historical layout must not have moved by a millimetre.
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic", 2)
        boxes = {n: b for n, *b in r.payload["boxes"]}
        assert boxes["insert_bed[1S]"] == pytest.approx([3800.0, 150.0, 5800.0, 975.0])
        assert boxes["insert_bed[2S]"] == pytest.approx([1400.0, 150.0, 3400.0, 975.0])
        assert boxes["insert_bed[1N]"] == pytest.approx([3800.0, 1425.0, 5800.0, 2250.0])
        assert boxes["insert_bed[2N]"] == pytest.approx([1400.0, 1425.0, 3400.0, 2250.0])
        assert r.payload["verified"] is True


class TestLabelLegibility:
    """Phase 4b (2026-07-28): tags too close to read apart.

    Found by looking at a screenshot, which is exactly the way it should NOT
    have to be found - hence the check.
    """

    # The two ЛОКЕРЫ tags exactly as the live 2400x6000 room emitted them
    # before the fix: 15 mm apart, no AABB overlap, verified=true, and the
    # drawing read "ЛОКЕРЫОКЕРЫ".
    BROKEN_W = (877.5, 712.5, 1192.5, 787.5)
    BROKEN_E = (1207.5, 712.5, 1522.5, 787.5)

    def _compose(self, *labels):
        c = ps._Compose()
        c.labels = list(labels)
        return c

    def test_the_15mm_case_is_caught(self):
        c = self._compose(("insert_locker_row[WS]:label[ЛОКЕРЫ]", self.BROKEN_W),
                          ("insert_locker_row[ES]:label[ЛОКЕРЫ]", self.BROKEN_E))
        assert c.intersections() == []          # genuinely no overlap - 15 mm apart
        hits = c.label_legibility_violations()
        assert len(hits) == 1, hits
        assert "15 mm apart" in hits[0] and "75 mm" in hits[0]

    def test_it_forces_verified_false(self):
        c = self._compose(("insert_locker_row[WS]:label[ЛОКЕРЫ]", self.BROKEN_W),
                          ("insert_locker_row[ES]:label[ЛОКЕРЫ]", self.BROKEN_E))
        warnings, verified = c.audit()
        assert verified is False
        assert any(w.startswith("label_legibility:") for w in warnings)

    def test_the_threshold_is_one_text_height(self):
        # 74 mm fails, 76 mm passes, for 75 mm-high tags. Pins the rule itself,
        # not the sample that motivated it.
        for gap, caught in ((74.0, True), (76.0, False)):
            b = (self.BROKEN_W[2] + gap, 712.5, self.BROKEN_W[2] + gap + 315.0, 787.5)
            c = self._compose(("a", self.BROKEN_W), ("b", b))
            assert bool(c.label_legibility_violations()) is caught, gap

    def test_it_scales_with_the_tag(self):
        # A 150 mm room number is held to a 150 mm gap, a 75 mm furniture tag
        # to 75. The taller of the pair sets the bar.
        small = (0.0, 0.0, 315.0, 75.0)
        tall = (415.0, 0.0, 730.0, 150.0)        # 100 mm away
        assert self._compose(("a", small), ("b", tall)).label_legibility_violations()

    def test_overlap_is_not_reported_twice(self):
        # Overlapping tags are a `collision`; saying it again here would make one
        # defect look like two.
        over = (1000.0, 712.5, 1315.0, 787.5)
        c = self._compose(("a", self.BROKEN_W), ("b", over))
        assert c.intersections() != []
        assert c.label_legibility_violations() == []

    def test_diagonal_neighbours_read_apart(self):
        # Offset both across and along: the larger axis gap is what counts.
        far = (self.BROKEN_W[2] + 20.0, 1500.0, self.BROKEN_W[2] + 335.0, 1575.0)
        assert self._compose(("a", self.BROKEN_W), ("b", far)) \
            .label_legibility_violations() == []

    async def test_the_stand_off_measures_the_right_dimension(self, backend):
        # The bug itself, at the level it lived: on a N/S wall the stand-off
        # crosses the tag's HEIGHT, on a W/E wall its WIDTH. Measuring the
        # height in both cases is what pushed the W/E tag to the room's centre.
        kw = dict(module_origin=(0, 0), module_length=2400.0,
                  module_width=6000.0, wall_thickness=150.0)
        w = await ps.insert_locker_row(backend, "W", 150.0, 600.0, 420.0, 2,
                                       label="ЛОКЕРЫ", **kw)
        e = await ps.insert_locker_row(backend, "E", 150.0, 600.0, 420.0, 2,
                                       label="ЛОКЕРЫ", **kw)
        wb = w.payload["label_bboxes"][0][1]
        eb = e.payload["label_bboxes"][0][1]
        assert eb[0] - wb[2] == pytest.approx(150.0)   # was 15 mm
        assert wb[2] < 1200.0 < eb[0]                  # each stays on its side

    async def test_the_wide_room_keeps_its_tags_apart_too(self, backend):
        r = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic", 2)
        assert r.payload["verified"] is True, r.payload["warnings"]
        lab = {n: b for n, *b in r.payload["labels"]}
        s = lab["insert_locker_row[SW]:label[ЛОКЕРЫ]"]
        n = lab["insert_locker_row[NW]:label[ЛОКЕРЫ]"]
        assert n[1] - s[3] == pytest.approx(630.0)     # was 255 mm


class TestBedAxis:
    """Phase 4b (2026-07-28): the 90-degree turn for a 2400-facade room."""

    async def test_auto_follows_the_long_side(self, backend):
        wide = await ps.generate_dormitory_room(backend, 6000.0, 2400.0, "arctic", 1)
        deep = await ps.generate_dormitory_room(backend, 2400.0, 6000.0, "arctic", 1)
        assert wide.payload["bed_axis"] == "x"
        assert deep.payload["bed_axis"] == "y"

    async def test_rotated_room_gets_all_four_beds(self, backend):
        # The point of the exercise: 4 places, a corridor door on the 2400
        # facade, and BOTH check families clean - not merely "does not crash".
        r = await ps.generate_dormitory_room(
            backend, 2400.0, 6000.0, "arctic", bed_pairs=2, **CORRIDOR_KW)
        assert r.ok
        assert r.payload["bed_axis"] == "y"
        assert r.payload["beds_placed"] == 4
        assert r.payload["verified"] is True, r.payload["warnings"]
        assert r.payload["warnings"] == []

    async def test_rotated_geometry_matches_the_arithmetic(self, backend):
        # 2400 facade = 825 + 450 aisle + 825; 6000 depth = 1200 lockers + 50 +
        # 2000 + 400 + 2000 + 50. Asserted so the numbers in the docs stay true.
        r = await ps.generate_dormitory_room(
            backend, 2400.0, 6000.0, "arctic", bed_pairs=2, **CORRIDOR_KW)
        b = {n: box for n, *box in r.payload["boxes"]}
        west, east = b["insert_bed[1W]"], b["insert_bed[1E]"]
        assert west[0] == 150.0 and west[2] == 975.0        # 825 wide, flush
        assert east[0] == 1425.0 and east[2] == 2250.0      # 825 wide, flush
        assert east[0] - west[2] == pytest.approx(450.0)    # the aisle
        lockers = b["insert_locker_row[WS]"]
        assert lockers[0] == 150.0 and lockers[1] == 150.0
        assert lockers[3] == pytest.approx(1350.0)          # 2 cells x 600
        assert b["insert_bed[2W]"][1] - b["insert_locker_row[WS]"][3] \
            == pytest.approx(50.0)                          # bed clears lockers
        assert b["insert_bed[1W]"][1] - b["insert_bed[2W]"][3] \
            == pytest.approx(400.0)                         # gap between beds

    async def test_lockers_move_with_the_frame(self, backend):
        # Lockers belong on the walls the beds back onto, at the end away from
        # the bed heads - not on a hardcoded S/N.
        deep = await ps.generate_dormitory_room(
            backend, 2400.0, 6000.0, "arctic", bed_pairs=2, **CORRIDOR_KW)
        names = {n for n, *_ in deep.payload["boxes"]}
        assert "insert_locker_row[WS]" in names and "insert_locker_row[ES]" in names
        assert not any("[SW]" in n or "[NW]" in n for n in names)

    async def test_explicit_axis_overrides_the_derivation(self, backend):
        # The derivation is a default, not a law: a caller may state the frame.
        r = await ps.generate_dormitory_room(
            backend, 6000.0, 2400.0, "arctic", bed_pairs=1, bed_axis="y")
        assert r.payload["bed_axis"] == "y"

    async def test_unknown_axis_is_rejected(self, backend):
        with pytest.raises(ValueError):
            await ps.generate_dormitory_room(
                backend, 6000.0, 2400.0, "arctic", 1, bed_axis="diagonal")


def _boxes_overlap_simple(a, b):
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx > 1e-6 and dy > 1e-6
