"""Tests for the numeric-output parser.

Every case here is a bug that actually shipped and had to be found the hard
way, pinned to output Micro-Cap really produced. The parser is pure text
handling, so none of this needs Micro-Cap installed.
"""

from __future__ import annotations

import math

import pytest

from microcap_mcp.parser import (
    DataTable,
    _is_number,
    _is_strict_number,
    parse,
    to_float,
)


def header(analysis: str = "Transient", circuit: str = "ASTABLE") -> str:
    return (
        "*" * 80 + "\n"
        "***                       Micro-Cap 12.2.0.3 (64 bit)                        ***\n"
        f"***                      {analysis} Analysis of {circuit}                       ***\n"
        + "*" * 80 + "\n"
    )


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token, expected",
    [
        ("70.000000", 70.0),          # plain
        ("5.000E+00", 5.0),           # scientific
        ("-1.608E+00", -1.608),
        ("37.383410m", 37.383410e-3),  # SI suffix — milli
        ("1.5K", 1500.0),
        ("994.975MEG", 994.975e6),     # MEG must beat M
        ("4.7u", 4.7e-6),
        ("840p", 840e-12),
        (".5", 0.5),
    ],
)
def test_number_spellings(token, expected):
    """Micro-Cap emits plain, scientific and SI-suffixed numbers in one file."""
    assert to_float(token) == pytest.approx(expected, rel=1e-9)


def test_na_is_not_a_number_but_is_a_value():
    """`NA` marks an undefined value — phase at the first AC point, say.

    Rejecting the token truncates the table at row 1 and loses the other 200
    rows, which cost 22 points of corpus coverage before it was found.
    """
    assert math.isnan(to_float("NA"))


@pytest.mark.parametrize("state", ["X", "Z", "R", "F", "U"])
def test_digital_states_are_values(state):
    """Digital columns carry logic states, not measurements.

    They sit in the same table as ordinary analog columns, so refusing them
    throws away the analog data beside them.
    """
    assert math.isnan(to_float(state))


def test_rubbish_is_rejected():
    with pytest.raises(ValueError):
        to_float("banana")


def test_column_names_need_a_stricter_test_than_cells():
    """`F` is both the frequency column's name and the "falling" logic state.

    Judging a header with the lenient test makes an AC table's head look like
    data, and the parser stops recognising AC tables at all.
    """
    assert _is_number("F")            # as a cell: a logic state
    assert not _is_strict_number("F")  # as a column name: just a name
    assert _is_strict_number("70.0")


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------


def test_scientific_table():
    text = header() + (
        "Temperature=27\n"
        "\n"
        "Interpolated Waveform Values\n"
        "============================\n"
        "           T       V(1)       V(2)\n"
        "      (Secs)        (V)        (V)\n"
        "   0.000E+00  2.304E-01  5.000E+00\n"
        "   1.000E-07  2.309E-01  5.000E+00\n"
    )
    out = parse(text)
    assert out.analysis == "Transient"
    assert out.circuit == "ASTABLE"
    assert out.table.columns == ["T", "V(1)", "V(2)"]
    assert out.table.units == ["Secs", "V", "V"]
    assert out.table.column("V(2)") == [5.0, 5.0]


def test_units_are_positional_not_one_per_column():
    """Dimensionless columns have no unit at all.

    The units row cannot be split and zipped: here five columns share a single
    `(Hz)`. Columns are right-aligned, so units match by end offset.
    """
    text = header("AC", "BPFILT") + (
        "Interpolated Waveform Values\n"
        "============================\n"
        "            F Mag(v(S3)/v(In)) mag(v(S3)/v(S2))\n"
        "         (Hz)\n"
        "    70.000000       37.383410m      673.569953m\n"
    )
    table = parse(text).table
    assert table.columns == ["F", "Mag(v(S3)/v(In))", "mag(v(S3)/v(S2))"]
    assert table.units == ["Hz", "", ""]
    assert table.column("Mag(v(S3)/v(In))")[0] == pytest.approx(0.03738341)


def test_fully_dimensionless_table_has_a_blank_units_row():
    """A user `.DEFINE`d expression has no unit, so the whole row is blank.

    Gating on "the units row contains a unit" silently discarded these tables.
    """
    text = header() + (
        "Interpolated Waveform Values\n"
        "============================\n"
        "          T       Drop\n"
        "                      \n"
        "      0.000    12.853u\n"
        "     80.808m    -1.627u\n"
    )
    table = parse(text).table
    assert table.columns == ["T", "Drop"]
    assert table.units == ["", ""]
    assert len(table.rows) == 2


def test_na_does_not_truncate_the_table():
    text = header("AC", "GILBERT") + (
        "Interpolated Waveform Values\n"
        "============================\n"
        "            f V(VO2,VO1) PH(V(VO2,VO1))\n"
        "         (Hz)        (V)      (Degrees)\n"
        "      10.000K     12.872             NA\n"
        "     5.035MEG     12.815         -7.879\n"
        "    10.060MEG     12.700        -15.000\n"
    )
    table = parse(text).table
    assert len(table.rows) == 3, "NA in row 1 must not stop the table"
    phase = table.column("PH(V(VO2,VO1))")
    assert math.isnan(phase[0])
    assert phase[1] == pytest.approx(-7.879)


def test_digital_states_do_not_discard_analog_columns():
    text = header() + (
        "Interpolated Waveform Values\n"
        "============================\n"
        "         T    V(In)   V(Out) D(Convert) D(B0)\n"
        "    (Secs)      (V)      (V)                 \n"
        "      0.00     7.00     8.00          1     X\n"
        "     4.08n     7.61     8.00          1     X\n"
    )
    table = parse(text).table
    assert table.column("V(In)") == [7.0, 7.61], "analog data must survive"
    assert all(math.isnan(v) for v in table.column("D(B0)"))


def test_operating_point_table_is_not_a_waveform():
    """The file also holds operating-point tables, structurally identical to
    waveforms and placed *before* them. A shape-only test returns the DC
    operating point to a caller who asked for a curve — silently, and wrong.
    """
    text = header("AC", "BPFILT") + (
        "DC Operating Point Voltages\n"
        "===========================\n"
        "      Node    Voltage      Node\n"
        "       (#)        (V)       (#)\n"
        "     1.000      5.000     2.000\n"
        "\n"
        "Interpolated Waveform Values\n"
        "============================\n"
        "            F   Mag(v(OUT))\n"
        "         (Hz)           (V)\n"
        "    70.000000    37.383410m\n"
        "   140.000000    50.000000m\n"
    )
    out = parse(text)
    assert out.table.columns == ["F", "Mag(v(OUT))"], "must skip the operating point"
    assert len(out.table.rows) == 2


def test_inline_error_is_reported_instead_of_a_bogus_diagnosis():
    """Micro-Cap prints complaints where the table would go. Without reading
    them the caller is told "numeric output is disabled", which is a confident
    wrong answer that sends them hunting the wrong bug.
    """
    text = header() + (
        "Interpolated Waveform Values\n"
        "============================\n"
        "Low Range Error: Unknown identifier 'TMIN'.\n"
    )
    out = parse(text)
    assert not out.runs
    assert any("TMIN" in m for m in out.messages)
    with pytest.raises(ValueError, match="TMIN"):
        _ = out.table


def test_empty_output_explains_how_to_enable_export():
    out = parse(header())
    with pytest.raises(ValueError, match="numeric output"):
        _ = out.table


def test_limits_are_captured():
    text = (
        header()
        + "Limits\n"
        "======\n"
        "Maximum Run Time           10US\n"
        "Number of Points           101\n"
        "\n"
    )
    limits = parse(text).limits
    assert limits["Maximum Run Time"] == "10US"
    assert limits["Number of Points"] == "101"


def test_truncation_is_announced_not_silent():
    """A row that still looks like data but will not parse means the table is
    being cut short. Handing back the short version as if it were whole is
    worse than failing.
    """
    text = header() + (
        "Interpolated Waveform Values\n"
        "============================\n"
        "         T     V(1)\n"
        "    (Secs)      (V)\n"
        "      0.00     7.00\n"
        "      1.00   banana\n"
    )
    out = parse(text)
    assert len(out.table.rows) == 1
    assert any("truncated" in m for m in out.messages)


def test_multiple_temperature_blocks():
    text = header() + (
        "Temperature=27\n"
        "Interpolated Waveform Values\n"
        "============================\n"
        "         T     V(1)\n"
        "    (Secs)      (V)\n"
        "      0.00     7.00\n"
        "\n"
        "Temperature=85\n"
        "Interpolated Waveform Values\n"
        "============================\n"
        "         T     V(1)\n"
        "    (Secs)      (V)\n"
        "      0.00     6.50\n"
    )
    out = parse(text)
    assert [r.temperature for r in out.runs] == ["27", "85"]
    assert out.table.column("V(1)") == [7.0], "`.table` is the first block"


# --------------------------------------------------------------------------
# DataTable
# --------------------------------------------------------------------------


def test_column_lookup_is_case_insensitive():
    t = DataTable(columns=["T", "V(OUT)"], units=["Secs", "V"], rows=[[0.0, 1.0]])
    assert t.column("v(out)") == [1.0]


def test_unknown_column_names_the_ones_it_has():
    t = DataTable(columns=["T", "V(OUT)"], units=["Secs", "V"], rows=[[0.0, 1.0]])
    with pytest.raises(KeyError, match="V\\(OUT\\)"):
        t.column("V(NOPE)")


# --------------------------------------------------------------------------
# complex output (AC / S-parameters / Smith)
# --------------------------------------------------------------------------

import cmath  # noqa: E402
import math   # noqa: E402

from microcap_mcp.parser import parse_row, to_complex  # noqa: E402


def test_rectangular_complex():
    z = to_complex("4.72-10.43i")
    assert z.real == pytest.approx(4.72)
    assert z.imag == pytest.approx(-10.43)
    assert to_complex("2.45-1.44i") == pytest.approx(complex(2.45, -1.44))


def test_rectangular_with_si_suffix():
    z = to_complex("5.66m-1.2ki")
    assert z.real == pytest.approx(5.66e-3)
    assert z.imag == pytest.approx(-1200.0)


def test_polar_pair_is_one_column():
    """`38.34 170.00°` is magnitude and angle — two tokens, one value."""
    row = parse_row(["10.00MEG", "38.34", "170.00°"])
    assert len(row) == 2, "the polar pair must collapse to a single cell"
    freq, s = row
    assert freq == pytest.approx(10e6)
    assert abs(s) == pytest.approx(38.34)
    assert math.degrees(cmath.phase(s)) == pytest.approx(170.0)


def test_real_and_rect_and_polar_in_one_row():
    """The exact shape smith emits: 5 columns, 7 whitespace tokens."""
    tokens = ["10.00MEG", "4.72-10.43i", "2.45-1.44i", "38.34", "170.00°", "5.66m", "81.59°"]
    row = parse_row(tokens)
    assert len(row) == 5
    assert row[0] == pytest.approx(10e6)                 # F, real
    assert row[1] == pytest.approx(complex(4.72, -10.43))  # S22, rectangular
    assert abs(row[3]) == pytest.approx(38.34)            # S21, polar magnitude
    assert abs(row[4]) == pytest.approx(5.66e-3)          # S12, polar mag with suffix


def test_smith_table_parses_end_to_end():
    text = (
        header("AC", "smith")
        + "Interpolated Waveform Values\n"
        "============================\n"
        "           F               S22               S11               S21              S12\n"
        "        (Hz)                                                                       \n"
        "    10.00MEG       4.72-10.43i        2.45-1.44i     38.34 170.00°     5.66m 81.59°\n"
        "    14.00MEG        3.05-7.87i        1.97-1.58i     37.84 166.12°     7.82m 78.65°\n"
    )
    table = parse(text).table
    assert table.columns == ["F", "S22", "S11", "S21", "S12"]
    assert len(table.rows) == 2
    assert table.column("S22")[0] == pytest.approx(complex(4.72, -10.43))
    assert abs(table.column("S21")[0]) == pytest.approx(38.34)


def test_a_plain_real_table_is_unaffected():
    """The common case must not regress: no ° or i means no merging."""
    text = header("AC", "RC") + (
        "Interpolated Waveform Values\n"
        "============================\n"
        "            F   V(OUT)\n"
        "         (Hz)      (V)\n"
        "    1000.0000  0.70710\n"
        "   10000.0000  0.09950\n"
    )
    table = parse(text).table
    assert table.column("V(OUT)") == [pytest.approx(0.70710), pytest.approx(0.09950)]
    assert all(isinstance(v, float) for row in table.rows for v in row)
