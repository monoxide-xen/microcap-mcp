"""Generate a Micro-Cap ``.CIR`` schematic from a component list.

A drawn schematic is what a ``.CIR`` gives over a ``.CKT``. This builds one
from an explicit geometric model — every pin location is known, not guessed —
so the generated circuit is electrically what was asked for, not whatever
Micro-Cap happens to extract from misplaced wires.

The pin geometry below was read from Micro-Cap's own component library
(``Standard.cmp``), where each ``[compdef]`` lists its pins in grid units:

    Pin="Plus",6,0     Pin="Minus",0,0     -> 6 grid = 48 px apart, horizontal

That library is Spectrum's; only the handful of coordinates this generator
needs are reproduced here, as derived constants. Facts that make it work,
each established by experiment:

* Shape/component definitions are built-in — no ``[shapedef]`` need be embedded.
* A node is named by a ``[Grid Text]`` label at its wire coordinate.
* A plot expression needs ``Plt``/``AliasID``/``Enable`` or it is ignored.
* A ``Voltage Source`` (``Definition=VSpice``) takes a ``VALUE`` attribute in
  the form ``DC=0 AC=1``; its pins are horizontal (Minus left, Plus right),
  not vertical.
"""

from __future__ import annotations

from dataclasses import dataclass

GRID = 8  # Micro-Cap's snap grid: pin coordinates are in these units.


@dataclass(frozen=True)
class PartDef:
    shape: str          # Micro-Cap component name
    value_attr: str     # attribute carrying the value
    # pins as (grid_x, grid_y); every supported two-terminal part is identical
    minus: tuple[int, int] = (0, 0)
    plus: tuple[int, int] = (6, 0)


# Derived from Standard.cmp — pins verified against a generated RC low-pass
# reproducing 1/sqrt(2) at the cutoff.
PARTS: dict[str, PartDef] = {
    "R": PartDef("Resistor", "RESISTANCE"),
    "C": PartDef("Capacitor", "CAPACITANCE"),
    "L": PartDef("Inductor", "INDUCTANCE"),
}
SOURCE = PartDef("Voltage Source", "VALUE")


class SchematicError(ValueError):
    pass


def _comp(shape: str, x: int, y: int, attrs: list[tuple[str, str]], rot: int = 0) -> str:
    out = [f"[Comp]\nName={shape}\nPx={x},{y}\nRot={rot}\n"]
    for i, (name, value) in enumerate(attrs):
        out.append(f"[Attr]\nON=12,{-20 - 14 * i},{name}\nV={value}\n")
    return "\n".join(out)


def _wire(x1: int, y1: int, x2: int, y2: int) -> str:
    return f"[Wire]\nPxs={x1},{y1},{x2},{y2}\n"


def _label(name: str, x: int, y: int) -> str:
    return f'[Grid Text]\nText="{name}"\nPx={x},{y}\nGridSnap=True\nJustifyH=Left\nJustifyV=Bottom\n'


def series_circuit(
    parts: list[tuple[str, str]],
    source: str = "DC=0 AC=1",
    analysis: str = "AC",
    limits: str | None = None,
    output_node: str = "OUT",
    shunt: list[tuple[str, str]] | None = None,
) -> str:
    """Build a ``.CIR``: a voltage source driving parts in series to ground,
    with optional parallel branches from the output node to ground.

    Args:
        parts: ordered ``(kind, value)`` pairs in series, kind in R/C/L, e.g.
            ``[("R", "1K"), ("C", "159.155N")]`` for an RC low-pass. The node
            after the first part is labelled ``output_node``.
        source: the source's VALUE, Micro-Cap syntax, e.g. ``"DC=0 AC=1"`` for
            an AC probe or ``"PULSE 0 5 0 1N 1N 1U 2U"`` for transient.
        analysis: which analysis block to write (AC / Transient / DC).
        limits: analysis limit line; a sensible default per analysis if None.
        output_node: label for the junction after the first part.
        shunt: extra ``(kind, value)`` pairs hung in parallel from the output
            node to ground — e.g. ``shunt=[("L", "1M"), ("C", "1U")]`` on a
            series R makes a resonant tank. Each is its own branch to ground.

    Returns the ``.CIR`` text, ready for ``simulate_schematic``.
    """
    if not parts:
        raise SchematicError("need at least one component")
    for kind, _ in (*parts, *(shunt or [])):
        if kind.upper() not in PARTS:
            raise SchematicError(f"unsupported part {kind!r}; use one of {sorted(PARTS)}")

    y = 128                       # signal rail
    span = (SOURCE.plus[0] - SOURCE.minus[0]) * GRID   # 48 px, all parts equal
    gap = 24
    body: list[str] = []
    counts: dict[str, int] = {}

    def place(kind: str, value: str, x: int, yy: int, rot: int = 0) -> None:
        part = PARTS[kind.upper()]
        counts[kind.upper()] = counts.get(kind.upper(), 0) + 1
        ref = f"{kind.upper()}{counts[kind.upper()]}"
        body.append(_comp(part.shape, x, yy, [("PART", ref), (part.value_attr, value)], rot=rot))

    # Source: Minus (left) returns to ground, Plus (right) drives the rail.
    x = 96
    body.append(_comp(SOURCE.shape, x, y, [("PART", "V1"), (SOURCE.value_attr, source)]))
    minus_x, node_x = x, x + span
    body.append(_wire(minus_x, y, minus_x, y + 24))
    body.append(_comp("Ground", minus_x, y + 24, []))

    out_x = node_x
    for i, (kind, value) in enumerate(parts):
        left = node_x + gap
        body.append(_wire(node_x, y, left, y))          # gap wire to this part
        place(kind, value, left, y)
        node_x = left + span
        # With shunts the whole series chain feeds one output node; without,
        # the output is the classic junction after the first element.
        if (shunt and i == len(parts) - 1) or (not shunt and i == 0):
            out_x = node_x
            body.append(_label(output_node, out_x, y))

    if shunt:
        # Each shunt is a vertical branch from the output node to its own
        # ground. The output node is NOT grounded directly — the shunts are its
        # path to ground; grounding it here would short the output.
        bx = out_x
        for kind, value in shunt:
            if bx != out_x:  # bridge parallel branches along the output rail
                body.append(_wire(out_x, y, bx, y))
            body.append(_wire(bx, y, bx, y + 40))
            place(kind, value, bx, y + 40, rot=1)        # vertical
            body.append(_comp("Ground", bx, y + 40 + span, []))
            bx += span + gap
    else:
        # last node of the series chain down to ground
        body.append(_wire(node_x, y, node_x, y + 24))
        body.append(_comp("Ground", node_x, y + 24, []))

    xexp = "F" if analysis == "AC" else "T"
    header = (
        "[Main]\nFileType=CIR\nVersion=12.00\nProgram=Micro-Cap\n"
        "Component Version=10.00\nShape Version=11.00\n\n"
        "[Circuit]\nShow Grid Text=True\n\n"
    )
    lims = f"[Limits]\nAnalysis={analysis}\n{limits or _default_limits(analysis)}\n\n"
    # Plt/AliasID/Enable are required or the expression is ignored; OUTPUT
    # makes it export numbers.
    wave = (
        f"[WaveForm]\nAnalysis={analysis}\nPlt=1\nAliasID=1\n"
        f"XExp={xexp}\nYExp=V({output_node})\nOptions=OUTPUT,LINEARY\nEnable=Enable\n\n"
    )
    return header + lims + wave + "\n".join(body) + "\n"


def _default_limits(analysis: str) -> str:
    if analysis == "AC":
        return "FRange=100K,10\nNPts=100\nTemp=27"
    if analysis == "Transient":
        return "TRange=1m\nNPts=100\nTemp=27"
    return "DCRange=5,0,.1\nNPts=100\nTemp=27"


# --------------------------------------------------------------------------
# op-amp amplifiers
# --------------------------------------------------------------------------
#
# Op-amps are macro (subcircuit) components, and instantiating one needs more
# than the passive schematic structure. Cracked by bisecting a working shipped
# circuit down to its minimum; the three keys, none in the manual:
#
#   * a [Page] section — minimally `[Page]\nName=Page 1`;
#   * the model in a [Text Area] tagged with the page: `Section=0\nPage=1`;
#   * section order — Main, Circuit, drawing, Page, Text Area, Limits, WaveForm
#     (passives tolerate any order; the op-amp does not).
#
# The op-amp pin geometry is from Standard.cmp, grid units. A near-ideal
# LEVEL=1 model needs no external supply rails.

OPAMP_PINS = {"plus": (0, 0), "minus": (0, 6), "output": (9, 3)}
OPAMP_MODEL = ".MODEL O1 OPA (LEVEL=1 A=1e6 ROUTAC=50 ROUTDC=75)"


def _assemble(drawing: list[str], analysis: str, limits: str | None, output_node: str,
              model: str | None) -> str:
    """Wrap a drawing in the canonical section order an op-amp circuit needs."""
    xexp = "F" if analysis == "AC" else "T"
    header = (
        "[Main]\nFileType=CIR\nVersion=12.00\nProgram=Micro-Cap\n"
        "Component Version=10.00\nShape Version=11.00\n\n"
        "[Circuit]\nShow Grid Text=True\n\n"
    )
    page = "[Page]\nName=Page 1\n\n"
    text = f"[Text Area]\nSection=0\nPage=1\nText={model}\n\n" if model else ""
    lims = f"[Limits]\nAnalysis={analysis}\n{limits or _default_limits(analysis)}\n\n"
    wave = (
        f"[WaveForm]\nAnalysis={analysis}\nPlt=1\nAliasID=1\n"
        f"XExp={xexp}\nYExp=V({output_node})\nOptions=OUTPUT,LINEARY\nEnable=Enable\n\n"
    )
    return header + "\n".join(drawing) + "\n\n" + page + text + lims + wave


def opamp_amplifier(
    gain: float,
    kind: str = "inverting",
    rin: str = "1K",
    source: str = "DC=0 AC=1",
    analysis: str = "AC",
    limits: str | None = None,
    output_node: str = "OUT",
) -> str:
    """An op-amp amplifier with a near-ideal LEVEL=1 op-amp.

    ``inverting``: gain ``-Rf/Rin``; ``Rf = gain * Rin``.
    ``non-inverting``: gain ``1 + Rf/Rg``; ``Rf = (gain-1) * Rg``, Rg = ``rin``.

    Args:
        gain: desired magnitude of the closed-loop gain (>0).
        kind: ``inverting`` or ``non-inverting``.
        rin: the input/ground resistor value (sets Rf from the gain).
        source, analysis, limits, output_node: as for ``series_circuit``.
    """
    if kind not in ("inverting", "non-inverting"):
        raise SchematicError("kind must be 'inverting' or 'non-inverting'")
    if gain <= 0:
        raise SchematicError("gain must be positive")

    def ohms(text: str) -> float:
        from .parser import to_float
        return to_float(text)

    rin_val = ohms(rin)
    if kind == "inverting":
        rf = _fmt_ohms(gain * rin_val)
    else:
        rf = _fmt_ohms((gain - 1) * rin_val) if gain > 1 else "0"

    ox, oy = 320, 224
    def pin(n: str) -> tuple[int, int]:
        gx, gy = OPAMP_PINS[n]
        return ox + gx * GRID, oy + gy * GRID

    plus, minus, out = pin("plus"), pin("minus"), pin("output")
    d = [_comp("Opamp", ox, oy, [("PART", "O1"), ("MODEL", "O1")])]
    sx, sy = 96, minus[1]
    out_x = out[0] + 40

    # source and its ground
    d.append(_comp(SOURCE.shape, sx, sy, [("PART", "V1"), (SOURCE.value_attr, source)]))
    d.append(_wire(sx, sy, sx, sy + 24))
    d.append(_comp("Ground", sx, sy + 24, []))

    if kind == "inverting":
        # +in to ground; source -> Rin -> summing node (-in); Rf feedback
        d.append(_wire(plus[0], plus[1], plus[0] - 32, plus[1]))
        d.append(_comp("Ground", plus[0] - 32, plus[1], []))
        rinx = sx + 72
        d.append(_wire(sx + 48, sy, rinx, sy))
        d.append(_comp("Resistor", rinx, sy, [("PART", "Rin"), ("RESISTANCE", rin)]))
        d.append(_wire(rinx + 48, sy, minus[0], minus[1]))
    else:
        # +in from source; Rg from -in to ground; Rf feedback
        d.append(_wire(sx + 48, sy, plus[0], plus[1]))         # source -> +in (sy == plus? use plus row)
        d.append(_wire(sx + 48, sy, sx + 48, plus[1]))
        d.append(_wire(sx + 48, plus[1], plus[0], plus[1]))
        rgx = minus[0] - 72
        d.append(_wire(minus[0], minus[1], rgx + 48, minus[1]))
        d.append(_comp("Resistor", rgx, minus[1], [("PART", "Rg"), ("RESISTANCE", rin)]))
        d.append(_wire(rgx, minus[1], rgx, minus[1] + 24))
        d.append(_comp("Ground", rgx, minus[1] + 24, []))

    # output node + feedback Rf from output back to summing node
    d.append(_wire(*out, out_x, out[1]))
    d.append(_label(output_node, out_x, out[1]))
    fy = oy - 48
    rfx = minus[0] + 8
    d.append(_wire(out_x, out[1], out_x, fy))
    d.append(_wire(out_x, fy, rfx + 48, fy))
    d.append(_comp("Resistor", rfx, fy, [("PART", "Rf"), ("RESISTANCE", rf)]))
    d.append(_wire(rfx, fy, minus[0], fy))
    d.append(_wire(minus[0], fy, minus[0], minus[1]))

    return _assemble(d, analysis, limits, output_node, OPAMP_MODEL)


def _fmt_ohms(value: float) -> str:
    """Format a resistance in Micro-Cap's engineering style (e.g. 10K)."""
    if value == 0:
        return "0"
    for suffix, scale in (("MEG", 1e6), ("K", 1e3), ("", 1.0)):
        if abs(value) >= scale:
            v = value / scale
            return (f"{v:g}{suffix}")
    return f"{value:g}"
