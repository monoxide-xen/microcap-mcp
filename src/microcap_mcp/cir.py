"""Reading and preparing Micro-Cap ``.CIR`` schematic files.

A ``.CIR`` is INI-like text. Beyond the schematic itself (``[Comp]`` positions,
``[Wire]`` segments, ``[shapedef]`` geometry) it carries the analysis setup:

    [Limits]                     <- analysis settings
    Analysis=AC
    FRange=1E6,1
    NPts=2000

    [WaveForm]                   <- one per plotted expression
    Analysis=AC
    XExp=F
    YExp=V(OUT)
    Options=LINEARX,LINEARY      <- add OUTPUT here to get numeric data

The ``OUTPUT`` flag is what the Analysis Limits dialog ticks per expression.
Nearly every circuit Micro-Cap ships has it switched off — they were built to
be *looked at*, not exported — so a stock circuit runs fine and yields no
numbers at all. Enabling it is what makes the shipped library usable as data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Our analysis names -> the Analysis= value used inside a .CIR.
# These spellings are not guessable; they were surveyed across all 475 shipped
# circuits. Note "HmDistortion" and "DynamicAC" — no spaces, abbreviated.
CIR_ANALYSIS: dict[str, str] = {
    "transient": "Transient",
    "ac": "AC",
    "dc": "DC",
    "harmonic_distortion": "HmDistortion",
    "intermodulation_distortion": "ImDistortion",
    "dynamic_ac": "DynamicAC",
    "dynamic_dc": "DynamicDC",
    "stability": "Stability",
}

# Dynamic analyses annotate the schematic in place; they define no [WaveForm]
# traces at all, so there is nothing for numeric export to switch on.
NO_TRACE_ANALYSES = frozenset({"dynamic_ac", "dynamic_dc"})

_BLOCK = re.compile(r"(?m)^(?=\[)")
_OPTIONS = re.compile(r"(?m)^Options=(.*)$")


@dataclass(frozen=True)
class Expression:
    """One plotted trace in a .CIR."""

    analysis: str
    x: str
    y: str
    exported: bool


def _field(block: str, key: str) -> str | None:
    m = re.search(rf"(?m)^{re.escape(key)}=(.*)$", block)
    return m.group(1).strip() if m else None


def expressions(text: str) -> list[Expression]:
    """List every trace the circuit plots, and whether it is exported."""
    out: list[Expression] = []
    for block in _BLOCK.split(text):
        if not block.startswith("[WaveForm]"):
            continue
        y = _field(block, "YExp")
        if not y:
            continue
        opts = _field(block, "Options") or ""
        out.append(
            Expression(
                analysis=_field(block, "Analysis") or "",
                x=_field(block, "XExp") or "",
                y=y,
                exported="OUTPUT" in opts.upper(),
            )
        )
    return out


def analyses(text: str) -> list[str]:
    """Which analyses this circuit is set up for, in .CIR spelling."""
    seen: list[str] = []
    for block in _BLOCK.split(text):
        if block.startswith("[Limits]"):
            a = _field(block, "Analysis")
            if a and a not in seen:
                seen.append(a)
    return seen


# The analysis-window section that carries the numeric-output settings, e.g.
# [Transient], [AC]. Named for the analysis, unlike the [Limits] blocks.
_WINDOW_SECTION = {
    "transient": "[Transient]",
    "ac": "[AC]",
    "dc": "[DC]",
    "harmonic_distortion": "[HmDistortion]",
    "intermodulation_distortion": "[ImDistortion]",
    "stability": "[Stability]",
}

_NUM_OUT_RANGE = re.compile(r'(?m)^Num Out (Low|High)=.*$')


def resolve_numeric_range(text: str, analysis: str) -> tuple[str, bool]:
    """Replace symbolic numeric-output bounds with the analysis's own limits.

    Circuits express the export range symbolically::

        Num Out Low="TMIN"
        Num Out High="TMAX"

    Those symbols resolve interactively but not in batch, where Micro-Cap
    fails with ``Low Range Error: Unknown identifier 'TMIN'`` and writes no
    table. Substituting the concrete bounds from the ``[Limits]`` block fixes
    it. Returns the patched text and whether anything was replaced.
    """
    section = _WINDOW_SECTION.get(analysis)
    if not section:
        return text, False

    want = CIR_ANALYSIS[analysis]
    low = high = None
    for block in _BLOCK.split(text):
        if block.startswith("[Limits]") and (_field(block, "Analysis") or "") == want:
            if analysis == "transient":
                low, high = _field(block, "TStart") or "0", _field(block, "TMax") or _field(block, "TRange")
            elif analysis == "ac":
                rng = (_field(block, "FRange") or "").split(",")
                if len(rng) == 2:
                    high, low = rng[0].strip(), rng[1].strip()
            elif analysis == "dc":
                rng = (_field(block, "DCRange") or _field(block, "Range1") or "").split(",")
                if len(rng) == 2:
                    high, low = rng[0].strip(), rng[1].strip()
            break

    if not low or not high:
        return text, False

    blocks = _BLOCK.split(text)
    changed = False
    for i, block in enumerate(blocks):
        if not block.startswith(section):
            continue
        new = re.sub(r'(?m)^Num Out Low=.*$', f'Num Out Low="{low}"', block)
        new = re.sub(r'(?m)^Num Out High=.*$', f'Num Out High="{high}"', new)
        if new != block:
            blocks[i] = new
            changed = True
    return "".join(blocks), changed


def dc_swept_source(text: str) -> str | None:
    """The source a DC analysis sweeps, or None if the circuit names none.

    A DC sweep needs an input to sweep, named in the ``[Limits]`` block as
    ``I1=V1``. Micro-Cap creates a ``[Limits]`` block for *every* analysis type
    whether or not the author configured it, so a circuit can look DC-capable —
    default ``I1Range=10,0,.5``, plotted traces and all — while never naming a
    source. Micro-Cap then refuses it with "Error Source not found".

    That is a circuit that was never set up for DC, not a driver failure, and
    it accounts for most DC failures across the shipped library.
    """
    for block in _BLOCK.split(text):
        if not block.startswith("[Limits]"):
            continue
        if (_field(block, "Analysis") or "") != "DC":
            continue
        src = _field(block, "I1")
        if not src or src.upper() == "NONE":
            return None
        return src
    return None


def set_points(text: str, analysis: str, points: int) -> str:
    """Override how many interpolated points one analysis exports.

    ``NPts`` in the ``[Limits]`` block is the circuit's own display setting,
    and the numeric export honours it. Circuits drawn to be looked at rather
    than exported often carry ``NPts=0``, which yields a single row — true to
    the file, useless as data. 22 of the shipped circuits are like that.
    """
    want = CIR_ANALYSIS[analysis]
    blocks = _BLOCK.split(text)
    for i, block in enumerate(blocks):
        if not block.startswith("[Limits]"):
            continue
        if (_field(block, "Analysis") or "") != want:
            continue
        new, n = re.subn(r"(?m)^NPts=.*$", f"NPts={points}", block, count=1)
        if n == 0:
            new = re.sub(r"(?m)^(Analysis=.*)$", rf"\1\nNPts={points}", block, count=1)
        blocks[i] = new
    return "".join(blocks)


def enable_numeric_output(text: str, analysis: str) -> tuple[str, int]:
    """Turn on numeric export for every trace of one analysis.

    Adds ``OUTPUT`` to the ``Options=`` of matching ``[WaveForm]`` blocks.
    Returns the patched text and the number of traces that *end up* exported —
    including any that were already switched on. Counting only the ones we
    changed would report zero for an already-enabled circuit and make callers
    conclude there is nothing to export.

    Idempotent: patching twice is the same as patching once.

    Raises KeyError for an unknown analysis name.
    """
    want = CIR_ANALYSIS[analysis]
    blocks = _BLOCK.split(text)
    exported = 0

    for i, block in enumerate(blocks):
        if not block.startswith("[WaveForm]"):
            continue
        if (_field(block, "Analysis") or "") != want:
            continue
        if not _field(block, "YExp"):
            continue  # a blank row in the Analysis Limits grid

        def add(m: re.Match[str]) -> str:
            opts = m.group(1)
            if "OUTPUT" in opts.upper():
                return m.group(0)
            return f"Options=OUTPUT,{opts}" if opts else "Options=OUTPUT"

        new, n = _OPTIONS.subn(add, block)
        if n == 0:
            # No Options line at all: add one after the YExp line.
            new = re.sub(r"(?m)^(YExp=.*)$", r"\1\nOptions=OUTPUT", block, count=1)
        blocks[i] = new
        exported += 1

    return "".join(blocks), exported
