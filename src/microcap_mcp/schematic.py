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

import math
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


# --------------------------------------------------------------------------
# transistor stages (bipolar primitives)
# --------------------------------------------------------------------------
#
# The NPN is a *primitive*, not a macro: it just references a model by name.
# Its pins are from Standard.cmp, at Rot=0 — no rotation is needed (the shipped
# COLPITTS.cir confirms: an NPN at Rot=0 with its collector wire landing on
# Base+(24,-24)). The trap that made this look like a rotation problem was the
# grid: a [Grid Text] node label binds only on a multiple of the 8 px grid, so
# every device pin must land on it or the label is silently dropped and the run
# aborts with "Can't find label". Keep the whole stage on the grid and it works.
#
# One more rule: a part and a node must not share a name (Micro-Cap warns and
# muddies the netlist), so the supply part and its net label are kept distinct.

NPN_PINS = {"collector": (3, -3), "base": (0, 0), "emitter": (3, 3)}
NPN_MODEL = ".MODEL QN NPN (BF=150 IS=1E-14 VAF=100)"
# MOSFET: Standard.cmp gives Drain (3,-3), Gate (0,0), Source (3,3), Body (3,0).
NMOS_PINS = {"drain": (3, -3), "gate": (0, 0), "source": (3, 3), "body": (3, 0)}
NMOS_MODEL = ".MODEL MN NMOS (LEVEL=1 VTO=1.5 KP=2M)"

# Shared layout, all on the 8 px grid so every device pin lands on it. Two
# columns: the control node (base/gate) at _CTRL_X, the top device pin
# (collector/drain) at _TOP_X. A [Grid Text] label binds only on the grid.
_DEV_X, _DEV_Y = 400, 304
_CTRL_X, _TOP_X = _DEV_X, _DEV_X + 24
_VCC_Y, _GND_Y = 104, 520
_VBE = 0.7


def _require_on_grid(*points: tuple[int, int]) -> None:
    """A node label binds only on the 8 px grid; refuse to emit off-grid pins
    rather than ship a schematic whose output silently reads 0."""
    for x, y in points:
        if x % GRID or y % GRID:
            raise SchematicError(
                f"pin ({x},{y}) is off the {GRID}px grid; its node label would "
                f"not bind. Place the device on grid-aligned coordinates."
            )


def _dev_pin(pins: dict, name: str) -> tuple[int, int]:
    gx, gy = pins[name]
    return _DEV_X + gx * GRID, _DEV_Y + gy * GRID


def _supply_rail(vcc: str) -> list[str]:
    """DC supply at the left, its ground, and the rail run out to both taps."""
    return [
        _comp(SOURCE.shape, 96, _VCC_Y, [("PART", "VS"), (SOURCE.value_attr, f"DC={vcc}")]),
        _wire(96, _VCC_Y, 96, _VCC_Y + 24),
        _comp("Ground", 96, _VCC_Y + 24, []),
        _wire(144, _VCC_Y, _CTRL_X, _VCC_Y),     # rail to the divider tap
        _wire(_CTRL_X, _VCC_Y, _TOP_X, _VCC_Y),  # and on to the collector/drain tap
    ]


def _bias_divider(ctrl_y: int, r1: str, r2: str) -> list[str]:
    """R1 (rail->control), R2 (control->ground) on the control-node column."""
    return [
        _comp("Resistor", _CTRL_X, 160, [("PART", "R1"), ("RESISTANCE", r1)], rot=1),
        _wire(_CTRL_X, _VCC_Y, _CTRL_X, 160),
        _wire(_CTRL_X, 208, _CTRL_X, ctrl_y),
        _comp("Resistor", _CTRL_X, 344, [("PART", "R2"), ("RESISTANCE", r2)], rot=1),
        _wire(_CTRL_X, ctrl_y, _CTRL_X, 344),
        _wire(_CTRL_X, 392, _CTRL_X, _GND_Y),
        _comp("Ground", _CTRL_X, _GND_Y, []),
    ]


def _ac_input(ctrl_y: int, source: str, cin: str) -> list[str]:
    """AC source -> coupling cap -> control node; labels the input node IN."""
    return [
        _comp(SOURCE.shape, 96, ctrl_y, [("PART", "Vin"), (SOURCE.value_attr, source)]),
        _wire(96, ctrl_y, 96, ctrl_y + 24),
        _comp("Ground", 96, ctrl_y + 24, []),
        _label("IN", 144, ctrl_y),
        _wire(144, ctrl_y, 200, ctrl_y),
        _comp("Capacitor", 200, ctrl_y, [("PART", "Cin"), ("CAPACITANCE", cin)]),
        _wire(248, ctrl_y, _CTRL_X, ctrl_y),
    ]


def _model_name(model: str, default: str) -> str:
    """The device MODEL attribute must equal the .MODEL name, or it references
    nothing; take it from the line so the two cannot drift apart."""
    parts = model.split()
    return parts[1] if len(parts) > 1 else default


def _top_resistor(part: str, value: str) -> list[str]:
    """A resistor from the rail down to the top device pin (collector/drain),
    with that pin labelled the output node's coordinate landing on it."""
    top_y = 200
    return [
        _comp("Resistor", _TOP_X, top_y, [("PART", part), ("RESISTANCE", value)], rot=1),
        _wire(_TOP_X, _VCC_Y, _TOP_X, top_y),
        _wire(_TOP_X, top_y + 48, _TOP_X, _DEV_Y - 24),
    ]


def _bottom_resistor(part: str, value: str, bottom_x: int) -> list[str]:
    """A resistor from the bottom device pin (emitter/source) down to ground."""
    bot_y = 376
    return [
        _wire(bottom_x, _DEV_Y + 24, bottom_x, bot_y),
        _comp("Resistor", bottom_x, bot_y, [("PART", part), ("RESISTANCE", value)], rot=1),
        _wire(bottom_x, bot_y + 48, bottom_x, _GND_Y),
        _comp("Ground", bottom_x, _GND_Y, []),
    ]


def common_emitter_amplifier(
    rc: str = "4.7K",
    re: str = "1K",
    r1: str | None = None,
    r2: str | None = None,
    vcc: str = "12",
    cin: str = "10U",
    source: str = "AC=1",
    analysis: str = "AC",
    limits: str | None = None,
    output_node: str = "OUT",
    model: str = NPN_MODEL,
) -> str:
    """A common-emitter BJT gain stage: divider bias, unbypassed emitter
    degeneration, AC-coupled input. Midband gain magnitude is ``Rc/(Re+re')``,
    i.e. roughly ``Rc/Re`` for ``Re`` well above the intrinsic ``re'``.

    The DC operating point is not left to the caller by default: ``R1``/``R2``
    are computed to bias the collector at mid-supply, so the stage is always in
    the active region and the gain it reports is real. (A fixed divider silently
    saturates as soon as ``Rc`` grows — the collector pins low and the gain
    collapses to ~0. That is exactly the kind of silently-wrong output this
    refuses to emit.) Pass ``r1``/``r2`` to override the computed bias.

    Args:
        rc, re: collector and (unbypassed) emitter resistors; their ratio is
            the midband gain. ``Rc`` must exceed ``Re`` (gain > 1).
        r1, r2: base bias divider (``vcc`` to base, base to ground). ``None``
            (default) computes them for a mid-supply collector; give both to
            override.
        vcc: supply voltage (a DC ``Voltage Source`` value).
        cin: input coupling capacitor.
        source: input source VALUE, Micro-Cap syntax (``"AC=1"`` for AC gain).
        analysis: AC, Transient, or DC.
        limits, output_node: as for ``series_circuit``.
        model: the ``.MODEL`` line for the NPN; its name must match the device's
            MODEL attribute.

    Returns the ``.CIR`` text, ready for ``simulate_schematic``.
    """
    from .parser import to_float

    rc_v, re_v, vcc_v = to_float(rc), to_float(re), to_float(vcc)
    if rc_v <= re_v:
        raise SchematicError(
            f"Rc ({rc}) must exceed Re ({re}) for gain > 1; a common-emitter "
            f"stage with Rc <= Re attenuates."
        )
    r1, r2 = _bjt_divider(r1, r2, vcc_v, ic=vcc_v / (2 * rc_v), re_v=re_v, model=model)

    col = _dev_pin(NPN_PINS, "collector")
    base = _dev_pin(NPN_PINS, "base")
    emit = _dev_pin(NPN_PINS, "emitter")
    _require_on_grid(col, base, emit)

    d = [_comp("NPN", _DEV_X, _DEV_Y, [("PART", "Q1"), ("MODEL", _model_name(model, "QN"))])]
    d += _supply_rail(vcc)
    d += _top_resistor("Rc", rc)
    d.append(_label(output_node, *col))
    d += _bottom_resistor("Re", re, emit[0])
    d += _bias_divider(base[1], r1, r2)
    d += _ac_input(base[1], source, cin)
    return _assemble(d, analysis, limits, output_node, model)


def common_collector_amplifier(
    re: str = "1K",
    r1: str | None = None,
    r2: str | None = None,
    vcc: str = "12",
    cin: str = "10U",
    source: str = "AC=1",
    analysis: str = "AC",
    limits: str | None = None,
    output_node: str = "OUT",
    model: str = NPN_MODEL,
) -> str:
    """An emitter follower (common-collector): collector straight to the supply,
    output taken at the emitter. Voltage gain is just below 1 (``Re/(Re+re')``);
    the point is current gain and a low output impedance — a buffer.

    The divider biases the emitter at mid-supply so the output can swing both
    ways. Pass ``r1``/``r2`` to override.

    Args:
        re: emitter resistor (sets the bias current, ``Vcc/2 / Re``).
        r1, r2: base bias divider; ``None`` auto-biases for a mid-supply emitter.
        vcc, cin, source, analysis, limits, output_node, model: as for
            ``common_emitter_amplifier``.

    Returns the ``.CIR`` text.
    """
    from .parser import to_float

    re_v, vcc_v = to_float(re), to_float(vcc)
    ve = vcc_v / 2                       # mid-supply emitter
    r1, r2 = _bjt_divider(r1, r2, vcc_v, ic=ve / re_v, re_v=re_v, model=model)

    col = _dev_pin(NPN_PINS, "collector")
    base = _dev_pin(NPN_PINS, "base")
    emit = _dev_pin(NPN_PINS, "emitter")
    _require_on_grid(col, base, emit)

    d = [_comp("NPN", _DEV_X, _DEV_Y, [("PART", "Q1"), ("MODEL", _model_name(model, "QN"))])]
    d += _supply_rail(vcc)
    d.append(_wire(_TOP_X, _VCC_Y, _TOP_X, col[1]))     # collector straight to the rail
    d += _bottom_resistor("Re", re, emit[0])
    d.append(_label(output_node, *emit))                # output at the emitter
    d += _bias_divider(base[1], r1, r2)
    d += _ac_input(base[1], source, cin)
    return _assemble(d, analysis, limits, output_node, model)


def common_source_amplifier(
    rd: str = "4.7K",
    rs: str = "1K",
    r1: str | None = None,
    r2: str | None = None,
    vdd: str = "12",
    cin: str = "10U",
    source: str = "AC=1",
    analysis: str = "AC",
    limits: str | None = None,
    output_node: str = "OUT",
    model: str = NMOS_MODEL,
) -> str:
    """A common-source MOSFET gain stage: gate divider bias, source degeneration,
    AC-coupled input, body tied to source. Midband gain is ``-gm*Rd/(1+gm*Rs)``,
    i.e. roughly ``-Rd/Rs`` when ``gm*Rs`` is large.

    The gate divider is computed from the model's ``VTO``/``KP`` to bias the
    drain at mid-supply, so the device sits in saturation and actually amplifies
    (a mis-biased MOSFET drops out of saturation and the gain collapses). Pass
    ``r1``/``r2`` to override.

    Args:
        rd, rs: drain and (degeneration) source resistors.
        r1, r2: gate bias divider; ``None`` auto-biases for a mid-supply drain.
        vdd: supply voltage.
        cin, source, analysis, limits, output_node: as for the BJT stages.
        model: the NMOS ``.MODEL`` line; needs ``VTO`` and ``KP`` for auto-bias.

    Returns the ``.CIR`` text.
    """
    import re as _re
    from .parser import to_float

    rd_v, rs_v, vdd_v = to_float(rd), to_float(rs), to_float(vdd)
    if (r1 is None) != (r2 is None):
        raise SchematicError("override the bias with both r1 and r2, or neither")

    if r1 is None:
        vto_m = _re.search(r"\bVTO\s*=\s*([0-9.eE+-]+)", model)
        kp_m = _re.search(r"\bKP\s*=\s*([0-9.eE+-]+[a-zA-Z]?)", model)
        if not (vto_m and kp_m):
            raise SchematicError(
                "auto-bias needs VTO and KP in the NMOS model; pass r1 and r2 "
                "explicitly for a model without them."
            )
        vto, kp = float(vto_m.group(1)), to_float(kp_m.group(1))
        idd = vdd_v / (2 * rd_v)                # mid-rail drain
        vov = math.sqrt(2 * idd / kp)          # LEVEL=1, W/L defaults to 1
        vg = idd * rs_v + vto + vov            # Vs + Vgs
        if vg >= vdd_v:
            raise SchematicError(
                "these Rd/Rs put the gate above the supply; lower Rs or Rd."
            )
        idiv = 10e-6                            # gate draws no DC current
        r1 = _fmt_ohms((vdd_v - vg) / idiv)
        r2 = _fmt_ohms(vg / idiv)

    drain = _dev_pin(NMOS_PINS, "drain")
    gate = _dev_pin(NMOS_PINS, "gate")
    src = _dev_pin(NMOS_PINS, "source")
    body = _dev_pin(NMOS_PINS, "body")
    _require_on_grid(drain, gate, src, body)

    d = [_comp("NMOS", _DEV_X, _DEV_Y, [("PART", "M1"), ("MODEL", _model_name(model, "MN"))])]
    d += _supply_rail(vdd)
    d += _top_resistor("Rd", rd)
    d.append(_label(output_node, *drain))
    d += _bottom_resistor("Rs", rs, src[0])
    d.append(_wire(*body, *src))                        # body tied to source
    d += _bias_divider(gate[1], r1, r2)
    d += _ac_input(gate[1], source, cin)
    return _assemble(d, analysis, limits, output_node, model)


def _bjt_divider(r1, r2, vcc_v, ic, re_v, model):
    """Resolve the base divider: given (or auto-sized for the operating point).

    Auto-sizing puts the requested current through the collector/emitter and a
    stiff divider (10x the base current) at the base, so the bias holds against
    the beta spread.
    """
    if (r1 is None) != (r2 is None):
        raise SchematicError("override the bias with both r1 and r2, or neither")
    if r1 is not None:
        return r1, r2
    import re as _re
    m = _re.search(r"\bBF\s*=\s*([0-9.eE+-]+)", model)
    beta = float(m.group(1)) if m else 150.0
    vb = ic * re_v + _VBE
    idiv = 10 * (ic / beta)
    return _fmt_ohms((vcc_v - vb) / idiv), _fmt_ohms(vb / idiv)
