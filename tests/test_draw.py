"""Tests for the .CIR -> SVG renderer (no Micro-Cap).

The picture must match the netlist that runs, so these pin the parse (does it
find every component, wire and label the generator emits?) and the render (is a
symbol placed for each, at its pins, so wires meet the drawing?).
"""

from __future__ import annotations

from microcap_mcp import draw, schematic as sch


def test_parse_finds_components_wires_and_labels():
    cir = sch.common_emitter_amplifier(rc="4.7K", re="1K")
    s = draw.parse(cir)
    names = [c.name for c in s.comps]
    assert names.count("NPN") == 1
    assert names.count("Resistor") == 4          # R1, R2, Rc, Re
    assert "Capacitor" in names and "Voltage Source" in names
    assert names.count("Ground") >= 3
    assert s.wires, "wires must be parsed"
    labels = {t for t, _, _ in s.labels}
    assert {"IN", "OUT"} <= labels


def test_attrs_carry_the_part_and_value():
    cir = sch.common_emitter_amplifier(rc="4.7K", re="1K")
    s = draw.parse(cir)
    rc = next(c for c in s.comps if c.attrs.get("PART") == "Rc")
    assert rc.attrs["RESISTANCE"] == "4.7K"
    assert rc.name == "Resistor"


def test_pins_land_where_wires_connect():
    """Every rendered symbol is placed at its component origin; its pins (from
    the shared geometry) must coincide with wire endpoints, or the drawing would
    show parts floating off their wires. Check each device pin meets a wire.
    """
    cir = sch.differential_pair(rc="10K")
    s = draw.parse(cir)
    endpoints = set()
    for x1, y1, x2, y2 in s.wires:
        endpoints.add((x1, y1))
        endpoints.add((x2, y2))
    for c in s.comps:
        if c.name not in ("NPN", "NMOS", "Opamp"):
            continue
        for px, py in draw._abs_pins(c):
            assert (px, py) in endpoints, f"{c.name} pin {(px, py)} meets no wire"


def test_rotation_transforms_pins():
    """A rot=1 resistor is vertical: its Plus pin drops below the origin rather
    than sitting to the right, matching how the generator wires verticals.
    """
    r0 = draw.Comp("Resistor", 100, 100, rot=0)
    r1 = draw.Comp("Resistor", 100, 100, rot=1)
    assert draw._abs_pins(r0) == [(100, 100), (148, 100)]      # horizontal
    assert draw._abs_pins(r1) == [(100, 100), (100, 148)]      # vertical


def test_render_is_wellformed_svg_with_symbols_and_labels():
    cir = sch.cascode_amplifier(rc="10K", re="1K")
    svg = draw.render_svg(cir)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert 'viewBox=' in svg
    # a symbol group per component, and the node label text present
    assert svg.count("<g transform=") >= len(draw.parse(cir).comps)
    assert ">OUT<" in svg


def test_unknown_part_is_drawn_not_dropped():
    """A part with no symbol still appears (as a box), so nothing silently
    vanishes from the picture."""
    cir = (
        "[Main]\nFileType=CIR\n\n[Circuit]\n\n"
        "[Comp]\nName=Mystery\nPx=100,100\nRot=0\n\n"
        "[Attr]\nON=0,0,PART\nV=X1\n\n"
        "[Wire]\nPxs=100,100,148,100\n"
    )
    svg = draw.render_svg(cir)
    assert "<rect" in svg          # the fallback box
    assert ">X1<" in svg           # its label


def test_empty_circuit_renders_without_crashing():
    svg = draw.render_svg("[Main]\nFileType=CIR\n")
    assert svg.startswith("<svg")
