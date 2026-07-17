"""Parser for Micro-Cap numeric output files (.TNO / .ANO / .DNO).

Format, as emitted by MC 12.2.0.3 batch runs::

    ****************************************
    ***   Micro-Cap 12.2.0.3 (64 bit)    ***
    ***   Transient Analysis of ASTABLE  ***
    ****************************************
    Limits
    ======
    Maximum Run Time           10US
    ...
    Temperature=27

    Interpolated Waveform Values
    ============================
                F Mag(v(S3)/v(In)) mag(v(S3)/v(S2))
             (Hz)
        70.000000       37.383410m      673.569953m

Three things about that table cost real debugging and are handled here:

* **The units row is positional, not one-per-column.** Dimensionless columns
  (ratios, gains) simply have no unit, so the row cannot be split and zipped.
  Every column is right-aligned, so units are matched by end position.
* **Numbers come in three spellings**: plain (``70.000000``), scientific
  (``5.000E+00``) and SI-suffixed (``37.383410m``, ``1.5K``). Which one you
  get depends on the circuit's own number-format settings.
* A file holds one block per temperature; each block holds one data table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TITLE = re.compile(r"\*\*\*\s+(\w[\w\s]*?)\s+Analysis of\s+(\S+)\s+\*\*\*")
_TEMP = re.compile(r"^Temperature=(.+)$")
_RULE = re.compile(r"^=+$")
_UNIT = re.compile(r"\(([^)]*)\)")
# e.g. "Low Range Error: Unknown identifier 'TMIN'." — printed where a table
# would go. Anchored on a colon so ordinary prose containing the word is not
# mistaken for a diagnostic.
_INLINE_ERROR = re.compile(r"\b(Error|Warning)\b[^:]*:", re.IGNORECASE)
# Only these tables are waveforms. The same file also carries "DC Operating
# Point Voltages" and "Model parameters for devices of type ..." tables, which
# are structurally identical and appear first.
_WAVEFORM_TITLE = re.compile(r"Waveform Values", re.IGNORECASE)

# Micro-Cap / SPICE engineering suffixes. MEG must be tested before M.
_SI: dict[str, float] = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
    "t": 1e12,
}

_SUFFIXED = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+))(meg|[fpnumkgt])$", re.IGNORECASE)

# Micro-Cap prints NA where a value is undefined — phase at a point with no
# defined argument, for instance. It is a legitimate cell, not a broken row,
# and refusing it silently truncates otherwise good tables to nothing.
MISSING = "NA"

# Logic states, in the digital columns of a mixed analog/digital circuit:
# unknown, high-impedance, rising and falling. (0 and 1 already parse as
# numbers.) They are not measurements, so they arrive as NaN — but refusing
# the token outright throws away the whole row, and with it the perfectly
# ordinary analog columns sitting beside it.
DIGITAL_STATES = frozenset({"X", "Z", "R", "F", "U"})


def to_float(token: str) -> float:
    """Parse one Micro-Cap numeric token, SI suffixes included.

    ``NA`` and digital state letters become NaN. Raises ValueError for
    anything else non-numeric.
    """
    if token.upper() == MISSING or token.upper() in DIGITAL_STATES:
        return float("nan")
    try:
        return float(token)  # plain and scientific
    except ValueError:
        pass
    if m := _SUFFIXED.match(token):
        return float(m.group(1)) * _SI[m.group(2).lower()]
    raise ValueError(f"not a Micro-Cap number: {token!r}")


# AC / S-parameter tables print complex values, in two forms that can appear
# side by side in one row:
#   rectangular   4.72-10.43i          one whitespace token
#   polar         38.34 170.00°        TWO tokens: magnitude, then angle°
# so a complex row has more whitespace tokens than columns. An angle token
# (ending °) belongs to the magnitude immediately before it. A number here may
# still carry an SI suffix (5.66m 81.59°), so the pieces go through to_float.
_UNSIGNED = r"(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?:meg|[fpnumkgt])?"
_NUM = rf"[-+]?{_UNSIGNED}"
# rectangular: real part, then an explicitly-signed imaginary part, then i.
_RECT = re.compile(rf"^({_NUM})([-+]{_UNSIGNED})i$", re.IGNORECASE)
_ANGLE = re.compile(rf"^({_NUM})°$", re.IGNORECASE)


def to_complex(token: str) -> complex:
    """Parse a rectangular complex token like ``4.72-10.43i``."""
    m = _RECT.match(token)
    if not m:
        raise ValueError(f"not a rectangular complex number: {token!r}")
    return complex(to_float(m.group(1)), to_float(m.group(2)))


def _merge_row(tokens: list[str]) -> list[object]:
    """Group a data row's whitespace tokens into one entry per column.

    A polar value is two tokens (``mag`` then ``angle°``); everything else is
    one. Returns a list whose length is the true column count: strings for
    single-token cells, ``(mag, angle)`` tuples for polar ones.
    """
    cells: list[object] = []
    for tok in tokens:
        if tok.endswith("°") and cells and isinstance(cells[-1], str):
            cells[-1] = (cells[-1], tok)
        else:
            cells.append(tok)
    return cells


def _cell_value(cell: object) -> float | complex:
    """Turn one merged cell into a number: real, rectangular or polar."""
    if isinstance(cell, tuple):
        import cmath
        import math

        mag, angle = cell
        deg = to_float(_ANGLE.match(angle).group(1))
        return cmath.rect(to_float(mag), math.radians(deg))
    if cell.endswith("i") and _RECT.match(cell):
        return to_complex(cell)
    return to_float(cell)


def parse_row(tokens: list[str]) -> list[float | complex]:
    """Parse a full data row, complex forms included."""
    return [_cell_value(c) for c in _merge_row(tokens)]


def _is_cell(token: str) -> bool:
    """A token that can be part of a data row: number, complex, or angle."""
    if _ANGLE.match(token) or _RECT.match(token):
        return True
    return _is_number(token)


def _cell_ok(cell: object) -> bool:
    """Is one merged cell readable? (str number/complex, or a polar tuple.)"""
    if isinstance(cell, tuple):
        mag, angle = cell
        return _is_number(mag) and bool(_ANGLE.match(angle))
    return _is_cell(cell)


def _is_number(token: str) -> bool:
    """Can this cell hold a value? Lenient: NA and logic states count."""
    try:
        to_float(token)
    except ValueError:
        return False
    return True


def _is_strict_number(token: str) -> bool:
    """Is this token literally a number? Strict: no NA, no logic states.

    Column *names* must be tested with this, not with :func:`_is_number`.
    An AC table's first column is named ``F`` — which is also the logic state
    for "falling", so the lenient test declares the header to be data and the
    whole table stops being recognised.
    """
    try:
        float(token)
        return True
    except ValueError:
        return bool(_SUFFIXED.match(token))


@dataclass
class DataTable:
    """One numeric table: column names, their units, and the rows."""

    columns: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    rows: list[list[float | complex]] = field(default_factory=list)

    def column(self, name: str) -> list[float | complex]:
        """Return one column by name, e.g. ``V(1)``. Case-insensitive."""
        try:
            i = [c.lower() for c in self.columns].index(name.lower())
        except ValueError:
            raise KeyError(f"no column {name!r}; have {self.columns}") from None
        return [r[i] for r in self.rows]

    def as_dict(self) -> dict[str, list[float]]:
        return {c: self.column(c) for c in self.columns}


@dataclass
class Run:
    """One temperature block."""

    temperature: str
    table: DataTable


@dataclass
class NumericOutput:
    analysis: str = ""
    circuit: str = ""
    limits: dict[str, str] = field(default_factory=dict)
    runs: list[Run] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    """Complaints Micro-Cap wrote into the file itself, e.g.
    ``Low Range Error: Unknown identifier 'TMIN'.`` It prints these *in place
    of* the table, so they are the real reason for an empty result."""

    @property
    def table(self) -> DataTable:
        """The first (usually only) table — the common case."""
        if not self.runs:
            if self.messages:
                raise ValueError(
                    "Micro-Cap wrote no data table and reported: "
                    + "; ".join(self.messages)
                )
            raise ValueError(
                "no data tables: the circuit has numeric output disabled. "
                "SPICE netlists need a .PRINT line; .CIR schematics need "
                "OUTPUT in the Options= of the relevant [WaveForm] blocks."
            )
        return self.runs[0].table


def _align_units(header_line: str, units_line: str, columns: list[str]) -> list[str]:
    """Match a positional units row to its columns.

    The table is right-aligned, so a unit belongs to the column whose header
    ends at the same offset. Columns with no unit get an empty string.
    """
    ends = {m.end(): i for i, m in enumerate(re.finditer(r"\S+", header_line))}
    units = [""] * len(columns)
    for m in _UNIT.finditer(units_line):
        i = ends.get(m.end())
        if i is None:  # tolerate a one-char drift in MC's padding
            i = ends.get(m.end() + 1, ends.get(m.end() - 1))
        if i is not None and i < len(units):
            units[i] = m.group(1)
    return units


def parse(text: str) -> NumericOutput:
    """Parse the text of a .TNO/.ANO/.DNO file."""
    out = NumericOutput()

    if m := _TITLE.search(text):
        out.analysis = m.group(1).strip()
        out.circuit = m.group(2).strip()

    lines = text.splitlines()
    i = 0
    temperature = ""

    # Micro-Cap prints its complaints inline, where the table should be.
    for line in lines:
        s = line.strip()
        if _INLINE_ERROR.search(s) and s not in out.messages:
            out.messages.append(s)

    while i < len(lines):
        stripped = lines[i].strip()

        if stripped == "Limits" and i + 1 < len(lines) and _RULE.match(lines[i + 1].strip()):
            i += 2
            while i < len(lines) and lines[i].strip():
                parts = re.split(r"\s{2,}", lines[i].strip())
                if len(parts) >= 2:
                    out.limits[parts[0]] = " ".join(parts[1:])
                i += 1
            continue

        if m := _TEMP.match(stripped):
            temperature = m.group(1).strip()
            i += 1
            continue

        # A data table is a title line, a ==== rule, a header row, a units row,
        # then numeric rows.
        #
        # Two traps here, each of which cost a bug:
        #
        # * The units row is not a reliable marker. When every column is
        #   dimensionless — a ratio, or a user .DEFINEd expression — Micro-Cap
        #   leaves it blank, so gating on "contains a unit" discards valid
        #   tables. The head is confirmed by the first data row instead.
        # * The file also contains operating-point and model-parameter tables,
        #   which look identical in shape and come *before* the waveforms. A
        #   shape-only test silently returns the DC operating point where the
        #   caller asked for a curve. Hence the title check.
        if _RULE.match(stripped) and i + 3 < len(lines):
            title = lines[i - 1].strip() if i else ""
            header_line, units_line = lines[i + 1], lines[i + 2]
            columns = header_line.split()
            # Merge polar pairs before counting: a complex row has more
            # whitespace tokens than columns (mag and angle° are two tokens).
            first_cells = _merge_row(lines[i + 3].split())
            head_ok = (
                _WAVEFORM_TITLE.search(title)
                and columns
                and not _is_strict_number(columns[0])
                and len(first_cells) == len(columns)
                and all(_cell_ok(c) for c in first_cells)
            )
            if head_ok:
                table = DataTable(
                    columns=columns,
                    units=_align_units(header_line, units_line, columns),
                )
                i += 3
                while i < len(lines):
                    parts = lines[i].split()
                    if not parts:
                        i += 1
                        if table.rows:
                            break
                        continue
                    cells = _merge_row(parts)
                    if len(cells) != len(columns) or not all(_cell_ok(c) for c in cells):
                        # Normally this is simply the end of the table. But a
                        # line that still looks like data — right shape, some
                        # readable cells — means we failed to read a row and are
                        # about to hand back a truncated table as if it were
                        # whole. Silent truncation is worse than an error.
                        if len(cells) == len(columns) and any(_cell_ok(c) for c in cells):
                            bad = next(c for c in cells if not _cell_ok(c))
                            out.messages.append(
                                f"table truncated at row {len(table.rows) + 1}: "
                                f"cannot read value {bad!r}"
                            )
                        break
                    table.rows.append([_cell_value(c) for c in cells])
                    i += 1
                if table.rows:
                    out.runs.append(Run(temperature=temperature, table=table))
                continue

        i += 1

    return out


def parse_file(path) -> NumericOutput:
    """Parse a numeric output file from disk."""
    with open(path, encoding="cp1252", errors="replace") as fh:
        return parse(fh.read())
