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

    async def test_single_bed_width_is_900(self, backend):
        r = await ps.insert_bed(backend, 5000.0, 5000.0, 0.0, "single")
        assert r.ok
        assert _furn_x_extent(backend) == pytest.approx(900.0)
