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
            i += 1
            while i < len(lines) and not lines[i].startswith("["):
                s = lines[i].strip()
                if s.startswith("ON="):
                    # ON=x,y,ATTRNAME
                    parts = s[3:].split(",")
                    if len(parts) >= 3:
                        name = parts[2]
                elif s.startswith("V=") and name:
                    cur.attrs[name] = s[2:]
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

def _rot(dx: int, dy: int, rot: int) -> tuple[int, int]:
    """Micro-Cap Rot in 90° steps. Each step is (x,y) -> (-y,x), which is SVG's
    clockwise rotate(90) in a y-down frame."""
    for _ in range(rot % 4):
        dx, dy = -dy, dx
    return dx, dy


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
# SVG symbols (drawn in the unrotated local frame, then g-transformed)
# --------------------------------------------------------------------------

def _sym_resistor() -> str:
    z = "M0,0 L8,0 L11,-6 L17,6 L23,-6 L29,6 L35,-6 L40,0 L48,0"
    return f'<path d="{z}" fill="none"/>'


def _sym_capacitor() -> str:
    return ('<path d="M0,0 L21,0 M27,0 L48,0" fill="none"/>'
            '<path d="M21,-8 L21,8 M27,-8 L27,8" fill="none"/>')


def _sym_inductor() -> str:
    arcs = "".join(f"a6,6 0 0 1 12,0 " for _ in range(3))
    return (f'<path d="M0,0 L6,0 " fill="none"/>'
            f'<path d="M6,0 {arcs}" fill="none"/>'
            f'<path d="M42,0 L48,0" fill="none"/>')


def _sym_ground() -> str:
    return ('<path d="M0,0 L0,7" fill="none"/>'
            '<path d="M-9,7 L9,7 M-5,11 L5,11 M-2,15 L2,15" fill="none"/>')


def _sym_source() -> str:
    # circle with + and - to show polarity (Minus at 0,0 side, Plus at 48)
    return ('<path d="M0,0 L12,0 M36,0 L48,0" fill="none"/>'
            '<circle cx="24" cy="0" r="12" fill="none"/>'
            '<path d="M31,-3 L31,3 M28.5,0 L33.5,0" fill="none" stroke-width="1.4"/>'
            '<path d="M15,0 L20,0" fill="none" stroke-width="1.4"/>')


def _sym_battery() -> str:
    return ('<path d="M0,0 L18,0 M30,0 L48,0" fill="none"/>'
            '<path d="M18,-10 L18,10" fill="none" stroke-width="2.2"/>'
            '<path d="M24,-5 L24,5" fill="none"/>'
            '<path d="M30,-10 L30,10" fill="none" stroke-width="2.2"/>')


def _sym_npn(pnp: bool = False) -> str:
    # base at (0,0); collector pin (24,-24), emitter pin (24,24)
    bar = '<path d="M12,-12 L12,12" fill="none" stroke-width="2"/>'
    lead = '<path d="M0,0 L12,0" fill="none"/>'
    coll = '<path d="M12,-6 L24,-24" fill="none"/>'
    emit = '<path d="M12,6 L24,24" fill="none"/>'
    # emitter arrow (out for NPN, in for PNP)
    arrow = ('<path d="M24,24 L18,18 M24,24 L23,16" fill="none"/>' if not pnp
             else '<path d="M12,6 L18,10 M12,6 L13,13" fill="none"/>')
    return lead + bar + coll + emit + arrow


def _sym_nmos() -> str:
    # gate at (0,0); drain (24,-24), source (24,24), body (24,0)
    return ('<path d="M0,0 L10,0" fill="none"/>'
            '<path d="M10,-12 L10,12" fill="none" stroke-width="2"/>'
            '<path d="M15,-12 L15,-4 M15,-2 L15,2 M15,4 L15,12" fill="none" stroke-width="2"/>'
            '<path d="M15,-8 L24,-8 L24,-24 M15,8 L24,8 L24,24 M15,0 L24,0" fill="none"/>')


def _sym_opamp() -> str:
    # +in (0,0), -in (0,48), out (72,24); triangle apex at output
    return ('<path d="M0,-10 L0,58 L72,24 Z" fill="none"/>'
            '<text x="10" y="4" font-size="11">+</text>'
            '<text x="10" y="52" font-size="11">−</text>')


_SYMBOLS = {
    "Resistor": _sym_resistor,
    "Capacitor": _sym_capacitor,
    "Inductor": _sym_inductor,
    "Voltage Source": _sym_source,
    "Battery": _sym_battery,
    "Ground": _sym_ground,
    "NPN": _sym_npn,
    "PNP": lambda: _sym_npn(pnp=True),
    "NMOS": _sym_nmos,
    "Opamp": _sym_opamp,
}


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


def render_svg(cir_text: str) -> str:
    """Render a ``.CIR`` as a standalone SVG string."""
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
        comps=[Comp(c.name, round(fx(c.x)), round(fy(c.y)), c.rot, c.attrs) for c in sch.comps],
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
        f'width="{w}" height="{h}" font-family="sans-serif">',
        f'<rect x="{minx}" y="{miny}" width="{w}" height="{h}" fill="white"/>',
        # a white halo behind every glyph keeps captions legible over wires
        '<style>text{paint-order:stroke;stroke:white;stroke-width:3px;'
        'stroke-linejoin:round}</style>',
        '<g stroke="#1a1a1a" stroke-width="1.6" stroke-linecap="round" '
        'stroke-linejoin="round" fill="#1a1a1a">',
    ]

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
            out.append(f'<circle cx="{px}" cy="{py}" r="3" fill="#1a1a1a"/>')

    # components
    for c in sch.comps:
        maker = _SYMBOLS.get(c.name)
        rotate = f' rotate({90 * c.rot})' if c.rot else ""
        if maker:
            out.append(f'<g transform="translate({c.x},{c.y}){rotate}">{maker()}</g>')
        else:
            # unknown part: a labelled box between its first two pins, so it is
            # visible rather than dropped
            out.append(f'<g transform="translate({c.x},{c.y}){rotate}">'
                       f'<rect x="0" y="-10" width="48" height="20" fill="none"/></g>')
        cap = _label_for(c) or (c.name if not maker else "")
        if cap:
            out.append(f'<text x="{c.x + 6}" y="{c.y - 14}" font-size="11" '
                       f'stroke="none">{_esc(cap)}</text>')

    # node labels (bold, offset so they clear the wire)
    for text, x, y in sch.labels:
        out.append(f'<text x="{x + 4}" y="{y - 4}" font-size="12" font-weight="bold" '
                   f'fill="#0645ad" stroke="none">{_esc(text)}</text>')

    out.append("</g></svg>")
    return "\n".join(out)
