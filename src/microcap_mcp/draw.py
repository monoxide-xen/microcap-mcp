"""Render a Micro-Cap ``.CIR`` schematic to an SVG picture.

Micro-Cap's own schematic-image command (``/IC``) does not produce a file in
batch mode (see docs/micro-cap-notes), so a drawn circuit could be simulated
and plotted but never *seen*. This draws it directly from the ``.CIR`` — the
same component placements, wires and node labels the generator emits — using
the pin geometry in :mod:`microcap_mcp.schematic`, so the picture matches the
netlist that runs.

Only the parts the generators produce are given symbols (R, C, L, ground,
sources, NPN/NMOS, op-amp); anything else falls back to a labelled box, so an
unknown part is visible rather than silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

GRID = 8  # matches schematic.GRID; pixel = grid * 8


@dataclass
class Comp:
    name: str
    x: int
    y: int
    rot: int = 0
    attrs: dict[str, str] = field(default_factory=dict)
    attr_pos: dict[str, tuple[int, int]] = field(default_factory=dict)


@dataclass
class Schematic:
    comps: list[Comp] = field(default_factory=list)
    wires: list[tuple[int, int, int, int]] = field(default_factory=list)
    labels: list[tuple[str, int, int]] = field(default_factory=list)


def parse(cir_text: str) -> Schematic:
    """Pull the drawable elements out of a ``.CIR``: components with their
    attributes, wire segments and grid-text node labels."""
    sch = Schematic()
    cur: Comp | None = None
    lines = cir_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line == "[Comp]":
            cur = Comp(name="", x=0, y=0)
            i += 1
            while i < len(lines) and not lines[i].startswith("["):
                s = lines[i].strip()
                if s.startswith("Name="):
                    cur.name = s[5:]
                elif s.startswith("Px="):
                    xy = s[3:].split(",")
                    cur.x, cur.y = int(xy[0]), int(xy[1])
                elif s.startswith("Rot="):
                    cur.rot = int(s[4:]) % 4
                i += 1
            sch.comps.append(cur)
            continue
        if line == "[Attr]" and cur is not None:
            name = None
            pos = (0, 0)
            i += 1
            while i < len(lines) and not lines[i].startswith("["):
                s = lines[i].strip()
                if s.startswith("ON="):
                    # ON=x,y,ATTRNAME
                    parts = s[3:].split(",")
                    if len(parts) >= 3:
                        name = parts[2]
                        try:
                            pos = (int(parts[0]), int(parts[1]))
                        except ValueError:
                            pos = (0, 0)
                elif s.startswith("V=") and name:
                    cur.attrs[name] = s[2:]
                    cur.attr_pos[name] = pos
                i += 1
            continue
        if line == "[Wire]":
            i += 1
            while i < len(lines) and not lines[i].startswith("["):
                s = lines[i].strip()
                if s.startswith("Pxs="):
                    n = [int(v) for v in s[4:].split(",")]
                    if len(n) == 4:
                        sch.wires.append((n[0], n[1], n[2], n[3]))
                i += 1
            continue
        if line == "[Grid Text]":
            text = None
            px = None
            i += 1
            while i < len(lines) and not lines[i].startswith("["):
                s = lines[i].strip()
                if s.startswith("Text="):
                    m = re.match(r'Text="?([^"]*)"?', s)
                    text = m.group(1) if m else s[5:]
                elif s.startswith("Px="):
                    xy = s[3:].split(",")
                    px = (int(xy[0]), int(xy[1]))
                i += 1
            if text and px:
                sch.labels.append((text, px[0], px[1]))
            continue
        # any other section: it has no drawable content, but a following [Attr]
        # would no longer belong to a component
        if line.startswith("[") and line != "[Attr]":
            cur = None
        i += 1
    return sch


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

# Micro-Cap's 8 orientations as 2x2 matrices (a,b,c,d) applied as
# (a*x + c*y, b*x + d*y) — SVG's matrix(a,b,c,d,0,0). Rot 0-3 are rotations
# (det +1), 4-7 reflections (det -1). Established empirically from shipped
# circuits: for each Rot, a part's pins land on their wires under this matrix.
_MAT = {
    0: (1, 0, 0, 1),
    1: (0, 1, -1, 0),
    2: (-1, 0, 0, -1),
    3: (0, -1, 1, 0),
    4: (1, 0, 0, -1),
    5: (0, -1, -1, 0),
    6: (-1, 0, 0, 1),
    7: (0, 1, 1, 0),
}


def _rot(dx: int, dy: int, rot: int) -> tuple[int, int]:
    """Transform a point by Micro-Cap's orientation matrix (rotations 0-3,
    reflections 4-7)."""
    a, b, c, d = _MAT[rot % 8]
    return a * dx + c * dy, b * dx + d * dy


# component pins in the unrotated frame, grid units (matches schematic.py)
_PINS = {
    "Resistor": [(0, 0), (6, 0)],
    "Capacitor": [(0, 0), (6, 0)],
    "Inductor": [(0, 0), (6, 0)],
    "Voltage Source": [(0, 0), (6, 0)],
    "Battery": [(0, 0), (6, 0)],
    "Ground": [(0, 0)],
    "NPN": [(3, -3), (0, 0), (3, 3)],
    "PNP": [(3, 3), (0, 0), (3, -3)],
    "NMOS": [(3, -3), (0, 0), (3, 3), (3, 0)],
    "Opamp": [(0, 0), (0, 6), (9, 3)],
}


def _abs_pins(c: Comp) -> list[tuple[int, int]]:
    out = []
    for gx, gy in _PINS.get(c.name, []):
        dx, dy = _rot(gx * GRID, gy * GRID, c.rot)
        out.append((c.x + dx, c.y + dy))
    return out


# --------------------------------------------------------------------------
# Component symbols — Micro-Cap's own shape geometry
# --------------------------------------------------------------------------
#
# Micro-Cap draws each part from a [shapedef] in its shape library
# (standard.shp): a list of primitives — Line, PolyOpen/PolyClosed, Arc,
# Ellipse, Rectangle — and Root references to composite sub-shapes, all in the
# same pixel frame as the pins (origin at the primary pin). Reproducing those
# primitives here draws the symbols exactly as Micro-Cap does, rather than an
# approximation. Only the handful of parts the generators use are transcribed;
# the geometry is Spectrum's, kept verbatim as derived constants.

_SHAPES: dict[str, str] = {
    "Resistor": """
PolyOpen=10
PolyLine=0,0
PolyLine=12,0
PolyLine=14,-4
PolyLine=18,4
PolyLine=22,-4
PolyLine=26,4
PolyLine=30,-4
PolyLine=34,4
PolyLine=36,0
PolyLine=48,0
""",
    "Capacitor": """
Line=0,0,22,0
Line=22,-8,22,8
Line=26,-8,26,8
Line=26,0,48,0
""",
    "Inductor": """
Line=42,0,48,0
Line=0,0,5,0
Arc=5,-7,17,7,11,-7,5,0
Arc=13,-7,25,7,19,-7,13,0
Arc=29,-7,41,7,35,-7,29,0
Arc=21,-7,33,7,27,-7,21,0
Arc=6,-7,18,7,18,0,12,-7
Arc=14,-7,26,7,26,0,20,-7
Arc=22,-7,34,7,34,0,28,-7
Arc=30,-7,42,7,42,0,36,-7
""",
    "Ground": """
Line=0,0,12,0
Line=12,-8,12,8
Line=18,-4,18,4
""",
    "Battery": """
Line=0,0,22,0
Line=22,-3,22,3
Line=26,-7,26,7
Line=26,0,48,0
""",
    "Vsource.root": """
Line=0,0,12,0
Line=36,0,48,0
Ellipse=12,-12,36,12
""",
    "SPICE_V": """
Root="Vsource.root",0,0,0
""",
    "Bjt.root": """
Line=0,0,13,0
Rectangle=13,-12,15,12
PolyOpen=3
PolyLine=24,-24
PolyLine=24,-12
PolyLine=14,-2
PolyOpen=3
PolyLine=24,24
PolyLine=24,12
PolyLine=14,2
""",
    "NPN": """
Root="Bjt.root",0,0,0
PolyClosed=3
PolyLine=20,12
PolyLine=24,12
PolyLine=24,8
""",
    "Mos1.root": """
Line=0,0,10,0
Line=10,-8,10,8
Rectangle=12,-12,14,12
PolyOpen=3
PolyLine=24,-24
PolyLine=24,-8
PolyLine=13,-8
PolyOpen=3
PolyLine=24,24
PolyLine=24,8
PolyLine=13,8
""",
    "NMOS": """
Root="Mos1.root",0,0,0
Line=14,0,24,0
PolyClosed=3
PolyLine=16,0
PolyLine=20,4
PolyLine=20,-4
""",
    "Plus.root": """
Line=-2,0,2,0
Line=0,-2,0,2
""",
    "Minus.root": """
Line=-2,0,2,0
""",
    "Opamp.root": """
Line=0,48,6,48
Line=0,0,5,0
Line=6,-4,6,52
Line=6,-4,48,24
Line=6,52,48,24
Line=48,24,72,24
Line=10,12,14,12
Line=12,10,12,14
Line=10,36,14,36
""",
    "Opamp5": """
Root="Opamp.root",0,0,0
Root="Plus.root",25,-4,0
Root="Minus.root",25,52,0
""",
}

# component Name (in the .CIR) -> shapedef name (from Micro-Cap's [compdef])
_COMP_SHAPE = {
    "Resistor": "Resistor",
    "Capacitor": "Capacitor",
    "Inductor": "Inductor",
    "Battery": "Battery",
    "Voltage Source": "SPICE_V",
    "Ground": "Ground",
    "NPN": "NPN",
    "NMOS": "NMOS",
    "Opamp": "Opamp5",
}


def _nums(text: str) -> list[float]:
    return [float(n) for n in re.findall(r"-?\d+\.?\d*", text)]


def _arc_path(v: list[float]) -> str:
    """Micro-Cap Arc = ellipse bounding box (x1,y1,x2,y2) then start (x3,y3) and
    end (x4,y4) points, drawn counter-clockwise."""
    x1, y1, x2, y2, sx, sy, ex, ey = v[:8]
    rx, ry = abs(x2 - x1) / 2, abs(y2 - y1) / 2
    if rx == 0 or ry == 0:
        return f"M{sx},{sy} L{ex},{ey}"
    return f"M{sx},{sy} A{rx},{ry} 0 0 0 {ex},{ey}"


def _shape_svg(name: str, depth: int = 0) -> str:
    """Render a shapedef's primitives to SVG, resolving Root sub-shapes."""
    if depth > 6 or name not in _SHAPES:
        return ""
    out: list[str] = []
    lines = [ln.strip() for ln in _SHAPES[name].strip().splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        key, _, val = lines[i].partition("=")
        key = key.strip()
        if key == "Line":
            a, b, c, d = _nums(val)
            out.append(f'<line x1="{a}" y1="{b}" x2="{c}" y2="{d}" fill="none"/>')
        elif key == "Rectangle":
            a, b, c, d = _nums(val)
            out.append(f'<rect x="{min(a, c)}" y="{min(b, d)}" width="{abs(c - a)}" '
                       f'height="{abs(d - b)}" stroke="none" fill="#1a1a1a"/>')
        elif key == "Ellipse":
            a, b, c, d = _nums(val)
            out.append(f'<ellipse cx="{(a + c) / 2}" cy="{(b + d) / 2}" rx="{abs(c - a) / 2}" '
                       f'ry="{abs(d - b) / 2}" fill="none"/>')
        elif key == "Arc":
            out.append(f'<path d="{_arc_path(_nums(val))}" fill="none"/>')
        elif key in ("PolyOpen", "PolyClosed"):
            n = int(_nums(val)[0])
            pts = []
            for _ in range(n):
                i += 1
                pts.append(",".join(str(int(x)) for x in _nums(lines[i].partition("=")[2])))
            tag = "polyline" if key == "PolyOpen" else "polygon"
            fill = "none" if key == "PolyOpen" else "#1a1a1a"
            out.append(f'<{tag} points="{" ".join(pts)}" fill="{fill}"/>')
        elif key == "Root":
            m = re.match(r'"([^"]+)",\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)', val)
            if m:
                nm, tx, ty, rot = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
                tr = f"translate({tx},{ty})" + (f" rotate({90 * rot})" if rot else "")
                out.append(f'<g transform="{tr}">{_shape_svg(nm, depth + 1)}</g>')
        i += 1
    return "".join(out)


def _label_for(c: Comp) -> str:
    """A short caption: the part reference and its value if any."""
    ref = c.attrs.get("PART", "")
    val = ""
    for k in ("RESISTANCE", "CAPACITANCE", "INDUCTANCE", "VALUE"):
        if k in c.attrs and c.attrs[k]:
            val = c.attrs[k]
            break
    return " ".join(x for x in (ref, val) if x)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for lo, hi in sorted(intervals):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(lo, hi) for lo, hi in out]


def _build_axis(coords: list[int], protected: list[tuple[int, int]], cap: int = 88):
    """A monotonic coordinate remap that squeezes big empty runs.

    Micro-Cap places parts on a sparse grid, leaving long empty wire runs that
    make the drawing look stretched. This keeps unit slope inside every interval
    a component spans — so its pins stay put and wires still meet them — and
    caps only the *empty* gaps between them, pulling the picture together.
    """
    import bisect

    xs = sorted(set(coords))
    if len(xs) < 2:
        return lambda v: float(v)
    prot = _merge(protected)
    out = {xs[0]: float(xs[0])}
    for a, b in zip(xs, xs[1:]):
        gap = b - a
        inside = any(lo <= a and b <= hi for lo, hi in prot)
        if not inside and gap > cap:
            gap = cap
        out[b] = out[a] + gap

    def f(v: float) -> float:
        if v <= xs[0]:
            return out[xs[0]] + (v - xs[0])
        if v >= xs[-1]:
            return out[xs[-1]] + (v - xs[-1])
        i = bisect.bisect_right(xs, v) - 1
        a, b = xs[i], xs[i + 1]
        t = (v - a) / (b - a) if b > a else 0.0
        return out[a] + t * (out[b] - out[a])

    return f


def render_svg(cir_text: str, annotations: dict[str, str] | None = None) -> str:
    """Render a ``.CIR`` as a standalone SVG string.

    ``annotations`` maps a node label to a string (e.g. an operating-point
    voltage) drawn beside that node, turning the drawing into a marked-up
    schematic.
    """
    sch = parse(cir_text)

    # Collect every coordinate that appears, and the axis span each component
    # occupies (its pins), so the compaction never squeezes a part's own body.
    xcoords: list[int] = []
    ycoords: list[int] = []
    xprot: list[tuple[int, int]] = []
    yprot: list[tuple[int, int]] = []
    for x1, y1, x2, y2 in sch.wires:
        xcoords += [x1, x2]
        ycoords += [y1, y2]
    for c in sch.comps:
        pins = _abs_pins(c) or [(c.x, c.y)]
        pxs = [p[0] for p in pins] + [c.x]
        pys = [p[1] for p in pins] + [c.y]
        xcoords += pxs
        ycoords += pys
        xprot.append((min(pxs), max(pxs)))
        yprot.append((min(pys), max(pys)))
    for _, x, y in sch.labels:
        xcoords.append(x)
        ycoords.append(y)
    if not xcoords:
        xcoords, ycoords = [0, 100], [0, 100]

    fx = _build_axis(xcoords, xprot)
    fy = _build_axis(ycoords, yprot)

    # rewrite the schematic through the remap; symbols keep their local size,
    # which stays aligned because each part's span is unit-slope
    sch = Schematic(
        comps=[Comp(c.name, round(fx(c.x)), round(fy(c.y)), c.rot, c.attrs, c.attr_pos)
               for c in sch.comps],
        wires=[(round(fx(a)), round(fy(b)), round(fx(cc)), round(fy(d)))
               for a, b, cc, d in sch.wires],
        labels=[(t, round(fx(x)), round(fy(y))) for t, x, y in sch.labels],
    )

    xs: list[int] = []
    ys: list[int] = []
    for x1, y1, x2, y2 in sch.wires:
        xs += [x1, x2]
        ys += [y1, y2]
    for c in sch.comps:
        for px, py in _abs_pins(c):
            xs.append(px)
            ys.append(py)
        xs.append(c.x)
        ys.append(c.y)
    for _, x, y in sch.labels:
        xs.append(x)
        ys.append(y)
    if not xs:
        xs, ys = [0, 100], [0, 100]

    # Captions and value labels extend to the right of their anchor, so reserve
    # width for the longest one rather than let it clip at the frame edge.
    longest = max((len(_label_for(c)) for c in sch.comps), default=0)
    longest = max(longest, max((len(t) for t, _, _ in sch.labels), default=0))
    pad = 40
    minx, maxx = min(xs) - pad, max(xs) + pad + longest * 7
    miny, maxy = min(ys) - pad, max(ys) + pad
    w, h = maxx - minx, maxy - miny

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx} {miny} {w} {h}" '
        f'width="{w}" height="{h}" font-family="Verdana,Geneva,sans-serif" font-size="11">',
        f'<rect x="{minx}" y="{miny}" width="{w}" height="{h}" fill="white"/>',
        # a white halo behind every glyph keeps captions legible over wires
        '<style>text{paint-order:stroke;stroke:white;stroke-width:2.5px;'
        'stroke-linejoin:round}</style>',
    ]

    # Micro-Cap's editor shows a grid of dots; draw them faintly behind
    # everything, snapped so they sit on the schematic's own coordinates.
    step = 24
    gx0 = minx - (minx % step) + step
    gy0 = miny - (miny % step) + step
    dots = []
    yy = gy0
    while yy < maxy:
        xx = gx0
        while xx < maxx:
            dots.append(f'<circle cx="{xx}" cy="{yy}" r="0.9"/>')
            xx += step
        yy += step
    if dots:
        out.append(f'<g fill="#c4ccd4" stroke="none">{"".join(dots)}</g>')

    # everything else in Micro-Cap's dark-navy ink
    ink = "#141d7a"
    out.append(f'<g stroke="{ink}" stroke-width="1.5" stroke-linecap="round" '
               f'stroke-linejoin="round" fill="{ink}">')

    # wires first, so symbols sit on top
    for x1, y1, x2, y2 in sch.wires:
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')

    # connection dots where 3+ wire endpoints meet
    from collections import Counter
    endpoints = Counter()
    for x1, y1, x2, y2 in sch.wires:
        endpoints[(x1, y1)] += 1
        endpoints[(x2, y2)] += 1
    for (px, py), n in endpoints.items():
        if n >= 3:
            out.append(f'<circle cx="{px}" cy="{py}" r="2.6" fill="{ink}"/>')

    # components — Micro-Cap's own shape geometry, placed by its orientation
    # matrix (rotations 0-3, reflections 4-7)
    for c in sch.comps:
        shape = _COMP_SHAPE.get(c.name)
        # MC's Ground shape points right unrotated and it never places one that
        # way (shipped circuits use Rot 1/7); a Rot=0 ground is a generator that
        # didn't rotate it, so draw it pointing down.
        rot = 1 if (c.name == "Ground" and c.rot == 0) else c.rot
        a, b, cc, d = _MAT[rot % 8]
        tr = f"translate({c.x},{c.y}) matrix({a},{b},{cc},{d},0,0)"
        inner = _shape_svg(shape) if shape else '<rect x="0" y="-10" width="48" height="20" fill="none"/>'
        out.append(f'<g transform="{tr}">{inner}</g>')
        # part name over its value, stacked beside the part (as Micro-Cap shows
        # them). The [Attr] ON gives the anchor; the text stays upright.
        part = c.attrs.get("PART", "")
        val = next((c.attrs[k] for k in
                    ("RESISTANCE", "CAPACITANCE", "INDUCTANCE", "VALUE") if c.attrs.get(k)), "")
        ox, oy = c.attr_pos.get("PART", (12, -18))
        tx, ty = c.x + ox, c.y + oy
        if part:
            out.append(f'<text x="{tx}" y="{ty}" stroke="none" fill="#1a1a1a">{_esc(part)}</text>')
            ty += 13
        if val:
            out.append(f'<text x="{tx}" y="{ty}" stroke="none" fill="#1a1a1a">{_esc(val)}</text>')

    # node labels, and an optional operating-point annotation beneath
    for text, x, y in sch.labels:
        out.append(f'<text x="{x + 4}" y="{y - 4}" font-weight="bold" '
                   f'fill="{ink}" stroke="none">{_esc(text)}</text>')
        if annotations and text in annotations:
            out.append(f'<text x="{x + 4}" y="{y + 12}" '
                       f'fill="#c1121f" stroke="none">{_esc(annotations[text])}</text>')

    out.append("</g></svg>")
    return "\n".join(out)
