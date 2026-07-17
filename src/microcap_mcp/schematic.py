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
) -> str:
    """Build a ``.CIR``: a voltage source driving parts in series to ground.

    Args:
        parts: ordered ``(kind, value)`` pairs, kind in R/C/L, e.g.
            ``[("R", "1K"), ("C", "159.155N")]`` for an RC low-pass. The node
            after the first part is labelled ``output_node``.
        source: the source's VALUE, Micro-Cap syntax, e.g. ``"DC=0 AC=1"`` for
            an AC probe or ``"PULSE 0 5 0 1N 1N 1U 2U"`` for transient.
        analysis: which analysis block to write (AC / Transient / DC).
        limits: analysis limit line; a sensible default per analysis if None.
        output_node: label for the junction after the first part.

    Returns the ``.CIR`` text, ready for ``simulate_schematic``.
    """
    if not parts:
        raise SchematicError("need at least one component")
    for kind, _ in parts:
        if kind.upper() not in PARTS:
            raise SchematicError(f"unsupported part {kind!r}; use one of {sorted(PARTS)}")

    y = 128                       # signal rail
    span = (SOURCE.plus[0] - SOURCE.minus[0]) * GRID   # 48 px, all parts equal
    gap = 24
    body: list[str] = []

    # Source: Minus (left) returns to ground, Plus (right) drives the rail.
    x = 96
    body.append(_comp(SOURCE.shape, x, y, [("PART", "V1"), (SOURCE.value_attr, source)]))
    minus_x, node_x = x, x + span
    # source return to a ground below its Minus pin
    body.append(_wire(minus_x, y, minus_x, y + 24))
    body.append(_comp("Ground", minus_x, y + 24, []))

    counts: dict[str, int] = {}
    for i, (kind, value) in enumerate(parts):
        part = PARTS[kind.upper()]
        left = node_x + gap
        body.append(_wire(node_x, y, left, y))          # gap wire to this part
        counts[kind.upper()] = counts.get(kind.upper(), 0) + 1
        ref = f"{kind.upper()}{counts[kind.upper()]}"
        body.append(_comp(part.shape, left, y, [("PART", ref), (part.value_attr, value)]))
        node_x = left + span
        if i == 0:
            body.append(_label(output_node, node_x, y))

    # last node down to ground
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
